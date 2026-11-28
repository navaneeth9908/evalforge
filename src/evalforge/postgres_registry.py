"""Advisory-locked PostgreSQL registry schema bootstrap.

This module initializes schema only. It is not a PostgreSQL ``RunRegistry`` adapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

_ADVISORY_LOCK_ID = 1_763_249_117
_SCHEMA_VERSION = 1
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


class _Cursor(Protocol):
    def execute(self, statement: str, parameters: object = None) -> object: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...

    def close(self) -> None: ...


class PostgresConnection(Protocol):
    """Small DB-API-compatible surface required by schema initialization."""

    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True)
class PostgresRegistrySchemaState:
    """Validated state of the initialized PostgreSQL registry schema."""

    schema_version: int
    tables: frozenset[str]


_MIGRATION_V1: tuple[str, ...] = (
    """
    CREATE TABLE suites (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        digest text NOT NULL UNIQUE
            CHECK (digest ~ '^[0-9a-f]{64}$'),
        name text NOT NULL
            CHECK (length(name) BETWEEN 1 AND 512),
        payload_json jsonb NOT NULL
            CHECK (jsonb_typeof(payload_json) = 'object'),
        created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
    )
    """,
    """
    CREATE TABLE tenant_suites (
        tenant_id text NOT NULL
            CHECK (
                tenant_id = '__local__'
                OR tenant_id ~ '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'
            ),
        suite_id bigint NOT NULL REFERENCES suites(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
        PRIMARY KEY (tenant_id, suite_id)
    )
    """,
    """
    CREATE TABLE candidates (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        digest text NOT NULL UNIQUE
            CHECK (digest ~ '^[0-9a-f]{64}$'),
        payload_json jsonb NOT NULL
            CHECK (jsonb_typeof(payload_json) = 'object'),
        created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
    )
    """,
    """
    CREATE TABLE runs (
        run_id text PRIMARY KEY
            CHECK (run_id ~ '^[0-9a-f]{64}$'),
        suite_id bigint NOT NULL REFERENCES suites(id) ON DELETE RESTRICT,
        candidate_id bigint NOT NULL REFERENCES candidates(id) ON DELETE RESTRICT,
        dataset_manifest_digest text
            CHECK (
                dataset_manifest_digest IS NULL
                OR dataset_manifest_digest ~ '^[0-9a-f]{64}$'
            ),
        created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
    )
    """,
    """
    CREATE TABLE reports (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id text NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE RESTRICT,
        digest text NOT NULL UNIQUE
            CHECK (digest ~ '^[0-9a-f]{64}$'),
        payload_json jsonb NOT NULL
            CHECK (jsonb_typeof(payload_json) = 'object'),
        release_ready boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
    )
    """,
    """
    CREATE TABLE tenant_runs (
        tenant_id text NOT NULL
            CHECK (
                tenant_id = '__local__'
                OR tenant_id ~ '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'
            ),
        run_id text NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
        PRIMARY KEY (tenant_id, run_id)
    )
    """,
    """
    CREATE TABLE evaluation_jobs (
        job_id text PRIMARY KEY
            CHECK (job_id ~ '^[0-9a-f]{64}$'),
        tenant_id text NOT NULL
            CHECK (
                tenant_id = '__local__'
                OR tenant_id ~ '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'
            ),
        idempotency_key_digest text NOT NULL
            CHECK (idempotency_key_digest ~ '^[0-9a-f]{64}$'),
        request_digest text NOT NULL
            CHECK (request_digest ~ '^[0-9a-f]{64}$'),
        suite_id bigint NOT NULL REFERENCES suites(id) ON DELETE RESTRICT,
        candidate_id bigint NOT NULL REFERENCES candidates(id) ON DELETE RESTRICT,
        status text NOT NULL
            CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
        attempts integer NOT NULL,
        max_attempts integer NOT NULL,
        run_id text REFERENCES runs(run_id) ON DELETE RESTRICT,
        failure_code text,
        worker_id text,
        lease_token_digest text,
        lease_expires_at timestamptz,
        submitted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
        available_at timestamptz,
        UNIQUE (tenant_id, idempotency_key_digest),
        CHECK (max_attempts BETWEEN 1 AND 10),
        CHECK (attempts BETWEEN 0 AND max_attempts),
        CHECK (updated_at >= submitted_at),
        CHECK (
            worker_id IS NULL
            OR worker_id ~ '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'
        ),
        CHECK (
            lease_token_digest IS NULL
            OR lease_token_digest ~ '^[0-9a-f]{64}$'
        ),
        CHECK (
            (status = 'running'
             AND attempts BETWEEN 1 AND max_attempts
             AND worker_id IS NOT NULL
             AND lease_token_digest IS NOT NULL
             AND lease_expires_at IS NOT NULL)
            OR
            (status <> 'running'
             AND worker_id IS NULL
             AND lease_token_digest IS NULL
             AND lease_expires_at IS NULL)
        ),
        CHECK (
            (status = 'queued'
             AND available_at IS NOT NULL
             AND available_at >= submitted_at
             AND available_at >= updated_at)
            OR
            (status <> 'queued' AND available_at IS NULL)
        ),
        CHECK (
            (status = 'succeeded' AND run_id IS NOT NULL AND failure_code IS NULL)
            OR
            (status = 'failed'
             AND run_id IS NULL
             AND failure_code IS NOT NULL
             AND failure_code IN (
                 'attempt_limit_exhausted',
                 'invalid_evaluation',
                 'legacy_running_state'
             ))
            OR
            (status IN ('queued', 'running') AND run_id IS NULL AND failure_code IS NULL)
        )
    )
    """,
    "CREATE INDEX runs_created_at_idx ON runs(created_at DESC, run_id)",
    """
    CREATE INDEX tenant_suites_page_idx
    ON tenant_suites(tenant_id, created_at DESC, suite_id)
    """,
    """
    CREATE INDEX tenant_runs_page_idx
    ON tenant_runs(tenant_id, created_at DESC, run_id)
    """,
    """
    CREATE INDEX evaluation_jobs_tenant_page_idx
    ON evaluation_jobs(tenant_id, submitted_at DESC, job_id)
    """,
    """
    CREATE INDEX evaluation_jobs_available_idx
    ON evaluation_jobs(status, available_at, submitted_at, job_id)
    """,
    """
    CREATE FUNCTION evalforge_reject_immutable_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION '% is immutable', TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
    END;
    $$
    """,
    """
    CREATE TRIGGER suites_immutable
    BEFORE UPDATE OR DELETE ON suites
    FOR EACH ROW EXECUTE FUNCTION evalforge_reject_immutable_mutation()
    """,
    """
    CREATE TRIGGER tenant_suites_immutable
    BEFORE UPDATE OR DELETE ON tenant_suites
    FOR EACH ROW EXECUTE FUNCTION evalforge_reject_immutable_mutation()
    """,
    """
    CREATE TRIGGER candidates_immutable
    BEFORE UPDATE OR DELETE ON candidates
    FOR EACH ROW EXECUTE FUNCTION evalforge_reject_immutable_mutation()
    """,
    """
    CREATE TRIGGER runs_immutable
    BEFORE UPDATE OR DELETE ON runs
    FOR EACH ROW EXECUTE FUNCTION evalforge_reject_immutable_mutation()
    """,
    """
    CREATE TRIGGER reports_immutable
    BEFORE UPDATE OR DELETE ON reports
    FOR EACH ROW EXECUTE FUNCTION evalforge_reject_immutable_mutation()
    """,
    """
    CREATE TRIGGER tenant_runs_immutable
    BEFORE UPDATE OR DELETE ON tenant_runs
    FOR EACH ROW EXECUTE FUNCTION evalforge_reject_immutable_mutation()
    """,
)


def initialize_postgres_registry_schema(
    connection: PostgresConnection,
) -> PostgresRegistrySchemaState:
    """Initialize and validate the PostgreSQL registry schema in one transaction.

    The caller owns the connection and must provide it while it is idle. No DSN is
    accepted or retained. A fixed transaction-scoped advisory lock serializes all
    migration inspection and mutation in the connection's current schema.
    """

    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN")
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_ID,))
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version integer PRIMARY KEY CHECK (version > 0),
                applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
            )
            """
        )
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
        applied = [int(str(row[0])) for row in cursor.fetchall()]
        if applied not in ([], list(range(1, _SCHEMA_VERSION + 1))):
            raise RuntimeError("PostgreSQL registry migration history is unsupported")

        if not applied:
            for statement in _MIGRATION_V1:
                cursor.execute(statement)
            cursor.execute(
                "INSERT INTO schema_migrations(version) VALUES (%s)",
                (_SCHEMA_VERSION,),
            )

        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
            """
        )
        tables = frozenset(str(row[0]) for row in cursor.fetchall())
        missing = sorted(_REQUIRED_TABLES - tables)
        if missing:
            raise RuntimeError(f"PostgreSQL registry schema is incomplete: {', '.join(missing)}")
        connection.commit()
        return PostgresRegistrySchemaState(
            schema_version=_SCHEMA_VERSION,
            tables=_REQUIRED_TABLES,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
