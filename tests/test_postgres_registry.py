from __future__ import annotations

import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

_REQUIRED_TABLES = frozenset(
    {
        "schema_migrations",
        "suites",
        "tenant_suites",
        "candidates",
        "runs",
        "reports",
        "tenant_runs",
        "evaluation_jobs",
    }
)


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self._rows: list[tuple[object, ...]] = []

    def execute(self, statement: str, parameters: object = None) -> None:
        normalized = " ".join(statement.split())
        self.connection.events.append((normalized, parameters))
        upper = normalized.upper()
        if upper.startswith("SELECT VERSION FROM SCHEMA_MIGRATIONS"):
            self._rows = [(version,) for version in self.connection.history]
        elif "FROM INFORMATION_SCHEMA.TABLES" in upper:
            visible = self.connection.tables - self.connection.hidden_tables
            self._rows = [(table,) for table in sorted(visible)]
        elif upper.startswith("INSERT INTO SCHEMA_MIGRATIONS"):
            assert isinstance(parameters, tuple)
            self.connection.history.append(int(parameters[0]))
        else:
            match = re.match(r"CREATE TABLE(?: IF NOT EXISTS)? ([a-z_]+)", normalized, re.I)
            if match is not None:
                self.connection.tables.add(match.group(1))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def close(self) -> None:
        self.connection.cursor_closed = True


class _FakeConnection:
    def __init__(
        self,
        *,
        history: list[int] | None = None,
        tables: set[str] | None = None,
        hidden_tables: set[str] | None = None,
    ) -> None:
        self.history = [] if history is None else history
        self.tables = set() if tables is None else tables
        self.hidden_tables = set() if hidden_tables is None else hidden_tables
        self.events: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.cursor_closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_schema_initialization_locks_before_mutation_and_returns_frozen_state() -> None:
    from evalforge.postgres_registry import initialize_postgres_registry_schema

    connection = _FakeConnection()

    state = initialize_postgres_registry_schema(connection)

    statements = [statement for statement, _parameters in connection.events]
    lock_index = next(
        index
        for index, statement in enumerate(statements)
        if "PG_ADVISORY_XACT_LOCK" in statement.upper()
    )
    first_mutation_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.upper().startswith("CREATE TABLE")
    )
    assert statements[0] == "BEGIN"
    assert lock_index < first_mutation_index
    assert state.schema_version == 1
    assert state.tables == _REQUIRED_TABLES
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.cursor_closed is True
    with pytest.raises(FrozenInstanceError):
        state.schema_version = 2  # type: ignore[misc]


def test_schema_initialization_applies_migration_once() -> None:
    from evalforge.postgres_registry import initialize_postgres_registry_schema

    connection = _FakeConnection()

    first = initialize_postgres_registry_schema(connection)
    second = initialize_postgres_registry_schema(connection)

    migration_inserts = [
        statement
        for statement, _parameters in connection.events
        if statement.upper().startswith("INSERT INTO SCHEMA_MIGRATIONS")
    ]
    assert first == second
    assert connection.history == [1]
    assert len(migration_inserts) == 1
    assert connection.commits == 2


def test_schema_preserves_domain_limits_and_rejects_ambiguous_failed_jobs() -> None:
    from evalforge.postgres_registry import initialize_postgres_registry_schema

    connection = _FakeConnection()

    initialize_postgres_registry_schema(connection)

    schema_sql = "\n".join(statement for statement, _parameters in connection.events)
    assert "length(name) BETWEEN 1 AND 512" in schema_sql
    assert "AND failure_code IS NOT NULL AND failure_code IN" in schema_sql


@pytest.mark.parametrize("history", ([2], [1, 3]))
def test_schema_initialization_rejects_unsupported_history(history: list[int]) -> None:
    from evalforge.postgres_registry import initialize_postgres_registry_schema

    connection = _FakeConnection(history=history, tables=set(_REQUIRED_TABLES))

    with pytest.raises(RuntimeError, match="PostgreSQL registry migration history is unsupported"):
        initialize_postgres_registry_schema(connection)

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_schema_initialization_rejects_missing_required_table() -> None:
    from evalforge.postgres_registry import initialize_postgres_registry_schema

    connection = _FakeConnection(hidden_tables={"reports"})

    with pytest.raises(RuntimeError, match="PostgreSQL registry schema is incomplete: reports"):
        initialize_postgres_registry_schema(connection)

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_postgres_schema_initialization_is_concurrent_and_strict() -> None:
    dsn = os.environ.get("EVALFORGE_TEST_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("EVALFORGE_TEST_POSTGRES_DSN is not configured")
    psycopg = pytest.importorskip("psycopg")
    sql = pytest.importorskip("psycopg.sql")
    from evalforge.postgres_registry import initialize_postgres_registry_schema

    schema = f"evalforge_test_{uuid.uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    barrier = threading.Barrier(2)

    def initialize() -> object:
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            connection.commit()
            barrier.wait(timeout=10)
            return initialize_postgres_registry_schema(connection)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            states = tuple(executor.map(lambda _index: initialize(), range(2)))

        assert states[0] == states[1]
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            migration_count = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
            table_rows = connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
                """
            ).fetchall()
            assert migration_count == 1
            assert {str(row[0]) for row in table_rows} == _REQUIRED_TABLES

            digest = "a" * 64
            suite_id = connection.execute(
                """
                INSERT INTO suites(digest, name, payload_json)
                VALUES (%s, %s, %s::jsonb)
                RETURNING id
                """,
                (digest, " ", '{"name":"integration"}'),
            ).fetchone()[0]
            candidate_id = connection.execute(
                """
                INSERT INTO candidates(digest, payload_json)
                VALUES (%s, %s::jsonb)
                RETURNING id
                """,
                ("e" * 64, '{"policy":"Allowed"}'),
            ).fetchone()[0]
            connection.commit()

            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO suites(digest, name, payload_json)
                    VALUES (%s, %s, %s::jsonb)
                    """,
                    ("f" * 64, "s" * 512 + " ", '{"name":"too-long"}'),
                )
            connection.rollback()

            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO evaluation_jobs(
                        job_id, tenant_id, idempotency_key_digest, request_digest,
                        suite_id, candidate_id, status, attempts, max_attempts,
                        failure_code, submitted_at, updated_at
                    )
                    VALUES (
                        %s, 'tenant-alpha', %s, %s, %s, %s,
                        'failed', 1, 3, NULL, now(), now()
                    )
                    """,
                    ("9" * 64, "8" * 64, "7" * 64, suite_id, candidate_id),
                )
            connection.rollback()

            with pytest.raises(psycopg.IntegrityError, match="immutable"):
                connection.execute("UPDATE suites SET name = 'tampered' WHERE id = %s", (suite_id,))
            connection.rollback()

            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO evaluation_jobs(
                        job_id, tenant_id, idempotency_key_digest, request_digest,
                        suite_id, candidate_id, status, attempts, max_attempts,
                        submitted_at, updated_at, available_at
                    )
                    VALUES (
                        %s, 'tenant-alpha', %s, %s, %s, %s,
                        'queued', 3, 2, now(), now(), now()
                    )
                    """,
                    ("b" * 64, "c" * 64, "d" * 64, suite_id, candidate_id),
                )
            connection.rollback()
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
