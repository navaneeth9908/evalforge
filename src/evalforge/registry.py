"""Transactional local SQLite persistence for evaluation run history."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from evalforge.provenance import (
    JsonValue,
    canonical_json_bytes,
    canonical_json_sha256,
    deterministic_run_id,
)

_BUSY_TIMEOUT_MS = 5_000
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE suites (
                id INTEGER PRIMARY KEY,
                digest TEXT NOT NULL UNIQUE CHECK(length(digest) = 64),
                name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE candidates (
                id INTEGER PRIMARY KEY,
                digest TEXT NOT NULL UNIQUE CHECK(length(digest) = 64),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY CHECK(length(run_id) = 64),
                suite_id INTEGER NOT NULL REFERENCES suites(id),
                candidate_id INTEGER NOT NULL REFERENCES candidates(id),
                dataset_manifest_digest TEXT CHECK(
                    dataset_manifest_digest IS NULL OR length(dataset_manifest_digest) = 64
                ),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE reports (
                id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
                digest TEXT NOT NULL UNIQUE CHECK(length(digest) = 64),
                payload_json TEXT NOT NULL,
                release_ready INTEGER NOT NULL CHECK(release_ready IN (0, 1)),
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX runs_created_at_idx ON runs(created_at DESC, run_id)",
            """
            CREATE TRIGGER suites_immutable_update BEFORE UPDATE ON suites
            BEGIN SELECT RAISE(ABORT, 'suite evidence is immutable'); END
            """,
            """
            CREATE TRIGGER suites_immutable_delete BEFORE DELETE ON suites
            BEGIN SELECT RAISE(ABORT, 'suite evidence is immutable'); END
            """,
            """
            CREATE TRIGGER candidates_immutable_update BEFORE UPDATE ON candidates
            BEGIN SELECT RAISE(ABORT, 'candidate evidence is immutable'); END
            """,
            """
            CREATE TRIGGER candidates_immutable_delete BEFORE DELETE ON candidates
            BEGIN SELECT RAISE(ABORT, 'candidate evidence is immutable'); END
            """,
            """
            CREATE TRIGGER runs_immutable_update BEFORE UPDATE ON runs
            BEGIN SELECT RAISE(ABORT, 'run evidence is immutable'); END
            """,
            """
            CREATE TRIGGER runs_immutable_delete BEFORE DELETE ON runs
            BEGIN SELECT RAISE(ABORT, 'run evidence is immutable'); END
            """,
            """
            CREATE TRIGGER reports_immutable_update BEFORE UPDATE ON reports
            BEGIN SELECT RAISE(ABORT, 'report evidence is immutable'); END
            """,
            """
            CREATE TRIGGER reports_immutable_delete BEFORE DELETE ON reports
            BEGIN SELECT RAISE(ABORT, 'report evidence is immutable'); END
            """,
        ),
    ),
)


@dataclass(frozen=True)
class RegistryDiagnostics:
    """Applied schema and effective connection-safety settings."""

    schema_version: int
    foreign_keys: bool
    journal_mode: str
    busy_timeout_ms: int
    synchronous: int


@dataclass(frozen=True)
class StoredSuite:
    """A content-addressed evaluation suite."""

    suite_id: str
    name: str
    created_at: str
    suite: JsonValue


@dataclass(frozen=True)
class RunSummary:
    """Compact immutable history row."""

    run_id: str
    suite_sha256: str
    suite_name: str
    candidate_sha256: str
    report_sha256: str
    dataset_manifest_sha256: str | None
    release_ready: bool
    created_at: str


@dataclass(frozen=True)
class StoredRun:
    """A run and its complete content-addressed local evidence."""

    run_id: str
    suite_sha256: str
    suite_name: str
    candidate_sha256: str
    report_sha256: str
    dataset_manifest_sha256: str | None
    release_ready: bool
    created_at: str
    suite: JsonValue
    candidate: JsonValue
    report: JsonValue

    def summary(self) -> RunSummary:
        """Return the compact history representation."""

        return RunSummary(
            run_id=self.run_id,
            suite_sha256=self.suite_sha256,
            suite_name=self.suite_name,
            candidate_sha256=self.candidate_sha256,
            report_sha256=self.report_sha256,
            dataset_manifest_sha256=self.dataset_manifest_sha256,
            release_ready=self.release_ready,
            created_at=self.created_at,
        )


class SQLiteRunRegistry:
    """Open short-lived SQLite connections with safe local concurrency defaults."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            self._migrate(connection)
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, statements in _MIGRATIONS:
                if version in applied:
                    continue
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _utc_now()),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def create_run(
        self,
        *,
        suite_name: str,
        suite: JsonValue,
        candidate: JsonValue,
        report: JsonValue,
    ) -> StoredRun:
        """Atomically persist one evaluation and return its immutable stored form."""

        suite_json = canonical_json_bytes(suite).decode("utf-8")
        candidate_json = canonical_json_bytes(candidate).decode("utf-8")
        report_json = canonical_json_bytes(report).decode("utf-8")
        suite_sha256 = canonical_json_sha256(suite)
        candidate_sha256 = canonical_json_sha256(candidate)
        report_sha256 = canonical_json_sha256(report)
        suite_object = _require_object(suite, label="suite")
        report_object = _require_object(report, label="report")
        if (
            not suite_name.strip()
            or suite_object.get("name") != suite_name
            or report_object.get("suite_name") != suite_name
        ):
            raise ValueError("suite name must match suite and report evidence")
        run_id = _require_digest(report_object.get("run_id"), label="run ID")
        if _require_digest(report_object.get("suite_sha256"), label="suite digest") != suite_sha256:
            raise ValueError("report suite digest does not match suite evidence")
        if (
            _require_digest(report_object.get("candidate_sha256"), label="candidate digest")
            != candidate_sha256
        ):
            raise ValueError("report candidate digest does not match candidate evidence")
        manifest_value = report_object.get("dataset_manifest_sha256")
        dataset_manifest_sha256 = (
            None
            if manifest_value is None
            else _require_digest(manifest_value, label="dataset manifest digest")
        )
        expected_run_id = deterministic_run_id(
            suite_sha256=suite_sha256,
            candidate_sha256=candidate_sha256,
            dataset_manifest_sha256=dataset_manifest_sha256,
        )
        if run_id != expected_run_id:
            raise ValueError("report run ID does not match its versioned evidence")
        release_ready = report_object.get("release_ready")
        if type(release_ready) is not bool:
            raise ValueError("report release decision must be Boolean")

        created_at = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            suite_id = _insert_evidence(
                connection,
                table="suites",
                digest=suite_sha256,
                payload_json=suite_json,
                created_at=created_at,
                name=suite_name,
            )
            candidate_id = _insert_evidence(
                connection,
                table="candidates",
                digest=candidate_sha256,
                payload_json=candidate_json,
                created_at=created_at,
            )
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, suite_id, candidate_id, dataset_manifest_digest, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (run_id, suite_id, candidate_id, dataset_manifest_sha256, created_at),
            )
            connection.execute(
                """
                INSERT INTO reports(run_id, digest, payload_json, release_ready, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (run_id, report_sha256, report_json, int(release_ready), created_at),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        stored = self.get_run(run_id)
        if stored is None:
            raise RuntimeError("stored run could not be read back")
        if (
            stored.suite_sha256 != suite_sha256
            or stored.candidate_sha256 != candidate_sha256
            or stored.report_sha256 != report_sha256
            or stored.dataset_manifest_sha256 != dataset_manifest_sha256
        ):
            raise ValueError("run ID is already bound to different immutable evidence")
        return stored

    def create_suite(self, *, name: str, suite: JsonValue) -> StoredSuite:
        """Persist a suite idempotently by its canonical content digest."""

        suite_object = _require_object(suite, label="suite")
        if not name.strip() or suite_object.get("name") != name:
            raise ValueError("suite name must match suite evidence")
        payload_json = canonical_json_bytes(suite).decode("utf-8")
        suite_id = canonical_json_sha256(suite)
        created_at = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _insert_evidence(
                connection,
                table="suites",
                digest=suite_id,
                payload_json=payload_json,
                created_at=created_at,
                name=name,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        stored = self.get_suite(suite_id)
        if stored is None:
            raise RuntimeError("stored suite could not be read back")
        return stored

    def get_suite(self, suite_id: str) -> StoredSuite | None:
        """Read a suite by its canonical content digest."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT digest, name, payload_json, created_at FROM suites WHERE digest = ?",
                (suite_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return StoredSuite(
            suite_id=str(row["digest"]),
            name=str(row["name"]),
            created_at=str(row["created_at"]),
            suite=cast(JsonValue, json.loads(str(row["payload_json"]))),
        )

    def list_suites(self, *, limit: int = 100, offset: int = 0) -> tuple[StoredSuite, ...]:
        """List suites newest first using digest order as a stable tie break."""

        _validate_page(limit=limit, offset=offset)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT digest, name, payload_json, created_at
                FROM suites
                ORDER BY created_at DESC, digest
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            StoredSuite(
                suite_id=str(row["digest"]),
                name=str(row["name"]),
                created_at=str(row["created_at"]),
                suite=cast(JsonValue, json.loads(str(row["payload_json"]))),
            )
            for row in rows
        )

    def count_suites(self) -> int:
        """Return the number of immutable suite records."""

        connection = self._connect()
        try:
            return int(connection.execute("SELECT COUNT(*) FROM suites").fetchone()[0])
        finally:
            connection.close()

    def get_run(self, run_id: str) -> StoredRun | None:
        """Read one run and all related evidence by deterministic ID."""

        connection = self._connect()
        try:
            row = connection.execute(_RUN_SELECT + " WHERE runs.run_id = ?", (run_id,)).fetchone()
        finally:
            connection.close()
        return None if row is None else _stored_run(row)

    def list_runs(self, *, limit: int = 100, offset: int = 0) -> tuple[RunSummary, ...]:
        """List newest history first with a deterministic run-ID tie break."""

        if type(limit) is not int or not 1 <= limit <= 1_000:
            raise ValueError("history limit must be an integer from 1 through 1000")
        if type(offset) is not int or not 0 <= offset <= 10_000:
            raise ValueError("history offset must be an integer from 0 through 10000")
        connection = self._connect()
        try:
            rows = connection.execute(
                _RUN_SELECT + " ORDER BY runs.created_at DESC, runs.run_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        finally:
            connection.close()
        return tuple(_stored_run(row).summary() for row in rows)

    def count_runs(self) -> int:
        """Return the number of immutable evaluation runs."""

        connection = self._connect()
        try:
            return int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        finally:
            connection.close()

    def diagnostics(self) -> RegistryDiagnostics:
        """Read the applied migration and effective SQLite safety defaults."""

        connection = self._connect()
        try:
            schema_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout_ms = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
        finally:
            connection.close()
        return RegistryDiagnostics(
            schema_version=int(schema_version),
            foreign_keys=bool(foreign_keys),
            journal_mode=str(journal_mode),
            busy_timeout_ms=int(busy_timeout_ms),
            synchronous=int(synchronous),
        )


_RUN_SELECT = """
SELECT
    runs.run_id,
    runs.dataset_manifest_digest,
    runs.created_at,
    suites.digest AS suite_digest,
    suites.name AS suite_name,
    suites.payload_json AS suite_json,
    candidates.digest AS candidate_digest,
    candidates.payload_json AS candidate_json,
    reports.digest AS report_digest,
    reports.payload_json AS report_json,
    reports.release_ready
FROM runs
JOIN suites ON suites.id = runs.suite_id
JOIN candidates ON candidates.id = runs.candidate_id
JOIN reports ON reports.run_id = runs.run_id
"""


def _insert_evidence(
    connection: sqlite3.Connection,
    *,
    table: str,
    digest: str,
    payload_json: str,
    created_at: str,
    name: str | None = None,
) -> int:
    if table == "suites":
        connection.execute(
            """
            INSERT INTO suites(digest, name, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(digest) DO NOTHING
            """,
            (digest, name, payload_json, created_at),
        )
        row = connection.execute(
            "SELECT id, name, payload_json FROM suites WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None or row["name"] != name or row["payload_json"] != payload_json:
            raise ValueError("suite digest is already bound to different immutable evidence")
    elif table == "candidates":
        connection.execute(
            """
            INSERT INTO candidates(digest, payload_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(digest) DO NOTHING
            """,
            (digest, payload_json, created_at),
        )
        row = connection.execute(
            "SELECT id, payload_json FROM candidates WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None or row["payload_json"] != payload_json:
            raise ValueError("candidate digest is already bound to different immutable evidence")
    else:
        raise ValueError("unsupported evidence table")
    return int(row["id"])


def _stored_run(row: sqlite3.Row) -> StoredRun:
    return StoredRun(
        run_id=str(row["run_id"]),
        suite_sha256=str(row["suite_digest"]),
        suite_name=str(row["suite_name"]),
        candidate_sha256=str(row["candidate_digest"]),
        report_sha256=str(row["report_digest"]),
        dataset_manifest_sha256=(
            None if row["dataset_manifest_digest"] is None else str(row["dataset_manifest_digest"])
        ),
        release_ready=bool(row["release_ready"]),
        created_at=str(row["created_at"]),
        suite=cast(JsonValue, json.loads(str(row["suite_json"]))),
        candidate=cast(JsonValue, json.loads(str(row["candidate_json"]))),
        report=cast(JsonValue, json.loads(str(row["report_json"]))),
    )


def _require_object(value: JsonValue, *, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_page(*, limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("page limit must be an integer from 1 through 100")
    if type(offset) is not int or not 0 <= offset <= 10_000:
        raise ValueError("page offset must be an integer from 0 through 10000")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
