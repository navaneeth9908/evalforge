"""Transactional local SQLite persistence for evaluation run history."""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from evalforge.auth import TRUSTED_LOCAL_TENANT_ID
from evalforge.contracts import CandidateOutput, EvaluationSuite
from evalforge.engine import evaluate_suite
from evalforge.provenance import (
    CANONICALIZATION_VERSION,
    EVALUATION_SEMANTICS_VERSION,
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
    (
        2,
        (
            """
            CREATE TABLE tenant_suites (
                tenant_id TEXT NOT NULL,
                suite_id INTEGER NOT NULL REFERENCES suites(id),
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, suite_id)
            ) WITHOUT ROWID
            """,
            """
            INSERT INTO tenant_suites(tenant_id, suite_id, created_at)
            SELECT '__local__', id, created_at FROM suites
            """,
            """
            CREATE INDEX tenant_suites_page_idx
            ON tenant_suites(tenant_id, created_at DESC, suite_id)
            """,
            """
            CREATE TRIGGER tenant_suites_immutable_update BEFORE UPDATE ON tenant_suites
            BEGIN SELECT RAISE(ABORT, 'tenant suite membership is immutable'); END
            """,
            """
            CREATE TRIGGER tenant_suites_immutable_delete BEFORE DELETE ON tenant_suites
            BEGIN SELECT RAISE(ABORT, 'tenant suite membership is immutable'); END
            """,
            """
            CREATE TRIGGER tenant_suites_immutable_insert BEFORE INSERT ON tenant_suites
            WHEN EXISTS (
                SELECT 1 FROM tenant_suites AS existing
                WHERE existing.tenant_id = NEW.tenant_id
                  AND existing.suite_id = NEW.suite_id
            )
            BEGIN SELECT RAISE(ABORT, 'tenant suite membership is immutable'); END
            """,
            """
            CREATE TRIGGER suites_immutable_insert BEFORE INSERT ON suites
            WHEN EXISTS (
                SELECT 1 FROM suites AS existing
                WHERE existing.id = NEW.id OR existing.digest = NEW.digest
            )
            BEGIN SELECT RAISE(ABORT, 'suite evidence is immutable'); END
            """,
            """
            CREATE TRIGGER candidates_immutable_insert BEFORE INSERT ON candidates
            WHEN EXISTS (
                SELECT 1 FROM candidates AS existing
                WHERE existing.id = NEW.id OR existing.digest = NEW.digest
            )
            BEGIN SELECT RAISE(ABORT, 'candidate evidence is immutable'); END
            """,
            """
            CREATE TRIGGER runs_immutable_insert BEFORE INSERT ON runs
            WHEN EXISTS (
                SELECT 1 FROM runs AS existing
                WHERE existing.run_id = NEW.run_id
            )
            BEGIN SELECT RAISE(ABORT, 'run evidence is immutable'); END
            """,
            """
            CREATE TRIGGER reports_immutable_insert BEFORE INSERT ON reports
            WHEN EXISTS (
                SELECT 1 FROM reports AS existing
                WHERE existing.id = NEW.id
                   OR existing.run_id = NEW.run_id
                   OR existing.digest = NEW.digest
            )
            BEGIN SELECT RAISE(ABORT, 'report evidence is immutable'); END
            """,
        ),
    ),
    (
        3,
        (
            """
            CREATE TABLE tenant_runs (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id)
            ) WITHOUT ROWID
            """,
            """
            INSERT INTO tenant_runs(tenant_id, run_id, created_at)
            SELECT '__local__', run_id, created_at FROM runs
            """,
            """
            CREATE INDEX tenant_runs_page_idx
            ON tenant_runs(tenant_id, created_at DESC, run_id)
            """,
            """
            CREATE TRIGGER tenant_runs_immutable_update BEFORE UPDATE ON tenant_runs
            BEGIN SELECT RAISE(ABORT, 'tenant run membership is immutable'); END
            """,
            """
            CREATE TRIGGER tenant_runs_immutable_delete BEFORE DELETE ON tenant_runs
            BEGIN SELECT RAISE(ABORT, 'tenant run membership is immutable'); END
            """,
            """
            CREATE TRIGGER tenant_runs_immutable_insert BEFORE INSERT ON tenant_runs
            WHEN EXISTS (
                SELECT 1 FROM tenant_runs AS existing
                WHERE existing.tenant_id = NEW.tenant_id
                  AND existing.run_id = NEW.run_id
            )
            BEGIN SELECT RAISE(ABORT, 'tenant run membership is immutable'); END
            """,
        ),
    ),
    (
        4,
        (
            """
            CREATE TABLE evaluation_jobs (
                job_id TEXT PRIMARY KEY CHECK(length(job_id) = 64),
                tenant_id TEXT NOT NULL,
                idempotency_key_digest TEXT NOT NULL CHECK(length(idempotency_key_digest) = 64),
                request_digest TEXT NOT NULL CHECK(length(request_digest) = 64),
                suite_id INTEGER NOT NULL REFERENCES suites(id),
                candidate_id INTEGER NOT NULL REFERENCES candidates(id),
                status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed')),
                attempts INTEGER NOT NULL CHECK(attempts >= 0 AND attempts <= max_attempts),
                max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 10),
                run_id TEXT REFERENCES runs(run_id),
                failure_code TEXT,
                submitted_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (tenant_id, idempotency_key_digest),
                CHECK (
                    (status = 'succeeded' AND run_id IS NOT NULL AND failure_code IS NULL)
                    OR (status = 'failed' AND run_id IS NULL AND failure_code IS NOT NULL)
                    OR (status IN ('queued', 'running') AND run_id IS NULL AND failure_code IS NULL)
                )
            )
            """,
            """
            CREATE INDEX evaluation_jobs_tenant_page_idx
            ON evaluation_jobs(tenant_id, submitted_at DESC, job_id)
            """,
        ),
    ),
    (
        5,
        (
            """
            UPDATE evaluation_jobs
            SET status = 'failed', failure_code = 'legacy_running_state'
            WHERE status = 'running'
            """,
            "ALTER TABLE evaluation_jobs ADD COLUMN worker_id TEXT",
            "ALTER TABLE evaluation_jobs ADD COLUMN lease_token_digest TEXT",
            "ALTER TABLE evaluation_jobs ADD COLUMN lease_expires_at TEXT",
            """
            CREATE TRIGGER evaluation_jobs_lease_state_insert
            BEFORE INSERT ON evaluation_jobs
            WHEN NOT (
                (NEW.status = 'running'
                 AND NEW.attempts BETWEEN 1 AND NEW.max_attempts
                 AND NEW.worker_id IS NOT NULL
                 AND length(NEW.worker_id) BETWEEN 1 AND 64
                 AND substr(NEW.worker_id, 1, 1) GLOB '[a-z0-9]'
                 AND substr(NEW.worker_id, -1, 1) GLOB '[a-z0-9]'
                 AND NEW.worker_id NOT GLOB '*[^a-z0-9-]*'
                 AND NEW.lease_token_digest IS NOT NULL
                 AND length(NEW.lease_token_digest) = 64
                 AND NEW.lease_token_digest NOT GLOB '*[^0-9a-f]*'
                 AND NEW.lease_expires_at IS NOT NULL
                 AND length(NEW.lease_expires_at) = 27
                 AND NEW.lease_expires_at GLOB
                     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                 AND julianday(substr(NEW.lease_expires_at, 1, 19) || 'Z') IS NOT NULL
                 AND strftime(
                     '%Y-%m-%dT%H:%M:%S',
                     julianday(substr(NEW.lease_expires_at, 1, 19) || 'Z')
                 ) = substr(NEW.lease_expires_at, 1, 19))
                OR
                (NEW.status != 'running'
                 AND NEW.worker_id IS NULL
                 AND NEW.lease_token_digest IS NULL
                 AND NEW.lease_expires_at IS NULL)
            )
            BEGIN SELECT RAISE(ABORT, 'evaluation job lease state is invalid'); END
            """,
            """
            CREATE TRIGGER evaluation_jobs_lease_state_update
            BEFORE UPDATE ON evaluation_jobs
            WHEN NOT (
                (NEW.status = 'running'
                 AND NEW.attempts BETWEEN 1 AND NEW.max_attempts
                 AND NEW.worker_id IS NOT NULL
                 AND length(NEW.worker_id) BETWEEN 1 AND 64
                 AND substr(NEW.worker_id, 1, 1) GLOB '[a-z0-9]'
                 AND substr(NEW.worker_id, -1, 1) GLOB '[a-z0-9]'
                 AND NEW.worker_id NOT GLOB '*[^a-z0-9-]*'
                 AND NEW.lease_token_digest IS NOT NULL
                 AND length(NEW.lease_token_digest) = 64
                 AND NEW.lease_token_digest NOT GLOB '*[^0-9a-f]*'
                 AND NEW.lease_expires_at IS NOT NULL
                 AND length(NEW.lease_expires_at) = 27
                 AND NEW.lease_expires_at GLOB
                     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                 AND julianday(substr(NEW.lease_expires_at, 1, 19) || 'Z') IS NOT NULL
                 AND strftime(
                     '%Y-%m-%dT%H:%M:%S',
                     julianday(substr(NEW.lease_expires_at, 1, 19) || 'Z')
                 ) = substr(NEW.lease_expires_at, 1, 19))
                OR
                (NEW.status != 'running'
                 AND NEW.worker_id IS NULL
                 AND NEW.lease_token_digest IS NULL
                 AND NEW.lease_expires_at IS NULL)
            )
            BEGIN SELECT RAISE(ABORT, 'evaluation job lease state is invalid'); END
            """,
        ),
    ),
    (
        6,
        (
            "ALTER TABLE evaluation_jobs ADD COLUMN available_at TEXT",
            """
            UPDATE evaluation_jobs
            SET available_at = submitted_at
            WHERE status = 'queued'
            """,
            """
            CREATE INDEX evaluation_jobs_available_idx
            ON evaluation_jobs(status, available_at, submitted_at, job_id)
            """,
            """
            CREATE TRIGGER evaluation_jobs_availability_state_insert
            BEFORE INSERT ON evaluation_jobs
            WHEN NOT (
                (NEW.status = 'queued'
                 AND NEW.available_at IS NOT NULL
                 AND NEW.available_at >= NEW.submitted_at
                 AND NEW.available_at >= NEW.updated_at
                 AND length(NEW.available_at) = 27
                 AND NEW.available_at GLOB
                     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                 AND julianday(substr(NEW.available_at, 1, 19) || 'Z') IS NOT NULL
                 AND strftime(
                     '%Y-%m-%dT%H:%M:%S',
                     julianday(substr(NEW.available_at, 1, 19) || 'Z')
                 ) = substr(NEW.available_at, 1, 19))
                OR
                (NEW.status != 'queued' AND NEW.available_at IS NULL)
            )
            BEGIN SELECT RAISE(ABORT, 'evaluation job availability state is invalid'); END
            """,
            """
            CREATE TRIGGER evaluation_jobs_availability_state_update
            BEFORE UPDATE ON evaluation_jobs
            WHEN NOT (
                (NEW.status = 'queued'
                 AND NEW.available_at IS NOT NULL
                 AND NEW.available_at >= NEW.submitted_at
                 AND NEW.available_at >= NEW.updated_at
                 AND length(NEW.available_at) = 27
                 AND NEW.available_at GLOB
                     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                 AND julianday(substr(NEW.available_at, 1, 19) || 'Z') IS NOT NULL
                 AND strftime(
                     '%Y-%m-%dT%H:%M:%S',
                     julianday(substr(NEW.available_at, 1, 19) || 'Z')
                 ) = substr(NEW.available_at, 1, 19))
                OR
                (NEW.status != 'queued' AND NEW.available_at IS NULL)
            )
            BEGIN SELECT RAISE(ABORT, 'evaluation job availability state is invalid'); END
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


class EvaluationJobConflictError(ValueError):
    """An idempotency key is already bound to another evaluation request."""


class EvaluationJobSuiteNotFoundError(LookupError):
    """A queued evaluation references no suite visible to the tenant."""


class EvaluationJobLeaseNotFoundError(LookupError):
    """A worker capability does not identify one active evaluation-job lease."""


@dataclass(frozen=True)
class StoredEvaluationJob:
    """Tenant-scoped operational state for one durable evaluation request."""

    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    max_attempts: int
    run_id: str | None
    failure_code: str | None
    available_at: str | None
    submitted_at: str
    updated_at: str


@dataclass(frozen=True)
class ClaimedEvaluationJob:
    """One atomically leased work item with its private execution evidence."""

    job_id: str
    tenant_id: str
    suite_id: str
    suite: JsonValue
    candidate: JsonValue
    attempts: int
    max_attempts: int
    lease_token: str
    lease_expires_at: str


@dataclass(frozen=True)
class RenewedEvaluationJobLease:
    """A capability-authorized extension of one active worker lease."""

    job_id: str
    attempts: int
    lease_token: str
    lease_expires_at: str


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
            known_versions = {version for version, _statements in _MIGRATIONS}
            if not applied <= known_versions or applied != set(
                range(1, max(applied, default=0) + 1)
            ):
                raise RuntimeError("registry migration history is unsupported")
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
        tenant_id: str,
        suite_name: str,
        suite: JsonValue,
        candidate: JsonValue,
        report: JsonValue,
    ) -> StoredRun:
        """Atomically persist one evaluation and return its immutable stored form."""

        _validate_tenant_id(tenant_id)
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
            stored_run = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if stored_run is None:
                connection.execute(
                    """
                    INSERT INTO runs(
                        run_id, suite_id, candidate_id, dataset_manifest_digest, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, suite_id, candidate_id, dataset_manifest_sha256, created_at),
                )
            stored_report = connection.execute(
                "SELECT 1 FROM reports WHERE run_id = ?", (run_id,)
            ).fetchone()
            if stored_report is None:
                connection.execute(
                    """
                    INSERT INTO reports(run_id, digest, payload_json, release_ready, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, report_sha256, report_json, int(release_ready), created_at),
                )
            stored_binding = connection.execute(
                """
                SELECT suites.digest AS suite_digest,
                       candidates.digest AS candidate_digest,
                       runs.dataset_manifest_digest,
                       reports.digest AS report_digest,
                       reports.payload_json AS report_json,
                       reports.release_ready
                FROM runs
                JOIN suites ON suites.id = runs.suite_id
                JOIN candidates ON candidates.id = runs.candidate_id
                JOIN reports ON reports.run_id = runs.run_id
                WHERE runs.run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if stored_binding is None:
                raise RuntimeError("stored run could not be validated")
            if (
                stored_binding["suite_digest"] != suite_sha256
                or stored_binding["candidate_digest"] != candidate_sha256
                or stored_binding["report_digest"] != report_sha256
                or stored_binding["dataset_manifest_digest"] != dataset_manifest_sha256
                or stored_binding["report_json"] != report_json
                or bool(stored_binding["release_ready"]) != release_ready
            ):
                raise ValueError("run ID is already bound to different immutable evidence")
            _insert_tenant_suite_membership(
                connection,
                tenant_id=tenant_id,
                suite_id=suite_id,
                created_at=created_at,
            )
            _insert_tenant_run_membership(
                connection,
                tenant_id=tenant_id,
                run_id=run_id,
                created_at=created_at,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        stored = self.get_run(tenant_id=tenant_id, run_id=run_id)
        if stored is None:
            raise RuntimeError("stored run could not be read back")
        return stored

    def create_evaluation_job(
        self,
        *,
        tenant_id: str,
        suite_id: str,
        candidate: JsonValue,
        idempotency_key: str,
    ) -> StoredEvaluationJob:
        """Durably enqueue one idempotent tenant-scoped evaluation request."""

        _validate_tenant_id(tenant_id)
        _require_digest(suite_id, label="suite digest")
        _validate_idempotency_key(idempotency_key)
        candidate_object = _require_object(candidate, label="candidate")
        candidate_bytes = canonical_json_bytes(candidate_object)
        candidate_json = candidate_bytes.decode("utf-8")
        candidate_sha256 = sha256(candidate_bytes).hexdigest()
        request_digest = canonical_json_sha256(
            {"suite_id": suite_id, "candidate_sha256": candidate_sha256}
        )
        idempotency_key_digest = sha256(idempotency_key.encode("ascii")).hexdigest()
        submitted_at = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            suite_row = connection.execute(
                """
                SELECT suites.id
                FROM tenant_suites
                JOIN suites ON suites.id = tenant_suites.suite_id
                WHERE tenant_suites.tenant_id = ? AND suites.digest = ?
                """,
                (tenant_id, suite_id),
            ).fetchone()
            if suite_row is None:
                raise EvaluationJobSuiteNotFoundError("evaluation suite was not found")
            existing = connection.execute(
                _EVALUATION_JOB_SELECT
                + " WHERE evaluation_jobs.tenant_id = ?"
                + " AND evaluation_jobs.idempotency_key_digest = ?",
                (tenant_id, idempotency_key_digest),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise EvaluationJobConflictError(
                        "idempotency key is already bound to another evaluation request"
                    )
                connection.commit()
                return _stored_evaluation_job(existing)
            candidate_id = _insert_evidence(
                connection,
                table="candidates",
                digest=candidate_sha256,
                payload_json=candidate_json,
                created_at=submitted_at,
            )
            job_id = secrets.token_hex(32)
            while (
                connection.execute(
                    "SELECT 1 FROM evaluation_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                is not None
            ):
                job_id = secrets.token_hex(32)
            connection.execute(
                """
                INSERT INTO evaluation_jobs(
                    job_id, tenant_id, idempotency_key_digest, request_digest,
                    suite_id, candidate_id, status, attempts, max_attempts,
                    run_id, failure_code, submitted_at, updated_at, available_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, 3, NULL, NULL, ?, ?, ?)
                """,
                (
                    job_id,
                    tenant_id,
                    idempotency_key_digest,
                    request_digest,
                    int(suite_row["id"]),
                    candidate_id,
                    submitted_at,
                    submitted_at,
                    submitted_at,
                ),
            )
            row = connection.execute(
                _EVALUATION_JOB_SELECT + " WHERE evaluation_jobs.job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("stored evaluation job could not be validated")
            connection.commit()
            return _stored_evaluation_job(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_evaluation_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> StoredEvaluationJob | None:
        """Read one evaluation job only through its tenant membership."""

        _validate_tenant_id(tenant_id)
        _require_digest(job_id, label="evaluation job ID")
        connection = self._connect()
        try:
            row = connection.execute(
                _EVALUATION_JOB_SELECT
                + " WHERE evaluation_jobs.tenant_id = ? AND evaluation_jobs.job_id = ?",
                (tenant_id, job_id),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _stored_evaluation_job(row)

    def claim_evaluation_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ClaimedEvaluationJob | None:
        """Atomically lease the oldest available job for one bounded attempt."""

        _validate_worker_id(worker_id)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3_600:
            raise ValueError("lease duration must be an integer from 1 through 3600 seconds")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            lease_expires_at = _lease_expiry(now, lease_seconds)
            connection.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'failed', failure_code = 'attempt_limit_exhausted',
                    worker_id = NULL, lease_token_digest = NULL, lease_expires_at = NULL,
                    available_at = NULL, updated_at = ?
                WHERE attempts >= max_attempts
                  AND (
                      status = 'queued'
                      OR (status = 'running' AND lease_expires_at <= ?)
                  )
                """,
                (now, now),
            )
            row = connection.execute(
                """
                SELECT evaluation_jobs.job_id, evaluation_jobs.tenant_id,
                       evaluation_jobs.attempts, evaluation_jobs.max_attempts,
                       suites.digest AS suite_digest, suites.payload_json AS suite_json,
                       candidates.payload_json AS candidate_json
                FROM evaluation_jobs
                JOIN suites ON suites.id = evaluation_jobs.suite_id
                JOIN candidates ON candidates.id = evaluation_jobs.candidate_id
                WHERE (
                       evaluation_jobs.status = 'queued'
                       AND evaluation_jobs.available_at <= ?
                       AND evaluation_jobs.attempts < evaluation_jobs.max_attempts
                   )
                   OR (
                       evaluation_jobs.status = 'running'
                       AND evaluation_jobs.lease_expires_at <= ?
                       AND evaluation_jobs.attempts < evaluation_jobs.max_attempts
                   )
                ORDER BY evaluation_jobs.submitted_at, evaluation_jobs.job_id
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            lease_token = secrets.token_urlsafe(32)
            lease_token_digest = sha256(lease_token.encode("ascii")).hexdigest()
            attempts = int(row["attempts"]) + 1
            updated = connection.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'running', attempts = ?, failure_code = NULL,
                    worker_id = ?, lease_token_digest = ?, lease_expires_at = ?,
                    available_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    attempts,
                    worker_id,
                    lease_token_digest,
                    lease_expires_at,
                    now,
                    str(row["job_id"]),
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("evaluation job lease could not be persisted")
            connection.commit()
            return ClaimedEvaluationJob(
                job_id=str(row["job_id"]),
                tenant_id=str(row["tenant_id"]),
                suite_id=str(row["suite_digest"]),
                suite=cast(JsonValue, json.loads(str(row["suite_json"]))),
                candidate=cast(JsonValue, json.loads(str(row["candidate_json"]))),
                attempts=attempts,
                max_attempts=int(row["max_attempts"]),
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew_evaluation_job_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> RenewedEvaluationJobLease:
        """Atomically extend one active lease held by its capability owner."""

        _require_digest(job_id, label="evaluation job ID")
        _validate_worker_id(worker_id)
        _validate_lease_token(lease_token)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3_600:
            raise ValueError("lease duration must be an integer from 1 through 3600 seconds")
        token_digest = sha256(lease_token.encode("ascii")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            lease_expires_at = _lease_expiry(now, lease_seconds)
            row = connection.execute(
                """
                SELECT status, attempts, worker_id, lease_token_digest, lease_expires_at
                FROM evaluation_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "running"
                or row["worker_id"] != worker_id
                or row["lease_token_digest"] is None
                or not secrets.compare_digest(str(row["lease_token_digest"]), token_digest)
                or row["lease_expires_at"] is None
                or str(row["lease_expires_at"]) <= now
            ):
                raise EvaluationJobLeaseNotFoundError("evaluation job lease was not found")
            if lease_expires_at <= str(row["lease_expires_at"]):
                raise ValueError("lease duration must extend the active lease")
            renewed_lease_token = secrets.token_urlsafe(32)
            renewed_token_digest = sha256(renewed_lease_token.encode("ascii")).hexdigest()
            updated = connection.execute(
                """
                UPDATE evaluation_jobs
                SET lease_token_digest = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (renewed_token_digest, lease_expires_at, now, job_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("evaluation job lease could not be renewed")
            connection.commit()
            return RenewedEvaluationJobLease(
                job_id=job_id,
                attempts=int(row["attempts"]),
                lease_token=renewed_lease_token,
                lease_expires_at=lease_expires_at,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def retry_evaluation_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        backoff_seconds: int,
    ) -> StoredEvaluationJob:
        """Requeue one transient failure after a capability-authorized delay."""

        _require_digest(job_id, label="evaluation job ID")
        _validate_worker_id(worker_id)
        _validate_lease_token(lease_token)
        if type(backoff_seconds) is not int or not 1 <= backoff_seconds <= 3_600:
            raise ValueError("retry backoff must be an integer from 1 through 3600 seconds")
        token_digest = sha256(lease_token.encode("ascii")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            row = connection.execute(
                """
                SELECT status, attempts, max_attempts, worker_id,
                       lease_token_digest, lease_expires_at
                FROM evaluation_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "running"
                or row["worker_id"] != worker_id
                or row["lease_token_digest"] is None
                or not secrets.compare_digest(str(row["lease_token_digest"]), token_digest)
                or row["lease_expires_at"] is None
                or str(row["lease_expires_at"]) <= now
            ):
                raise EvaluationJobLeaseNotFoundError("evaluation job lease was not found")
            attempts = int(row["attempts"])
            if attempts >= int(row["max_attempts"]):
                status = "failed"
                failure_code = "attempt_limit_exhausted"
                available_at = None
            else:
                status = "queued"
                failure_code = None
                available_at = _lease_expiry(now, backoff_seconds)
            updated = connection.execute(
                """
                UPDATE evaluation_jobs
                SET status = ?, run_id = NULL, failure_code = ?,
                    worker_id = NULL, lease_token_digest = NULL, lease_expires_at = NULL,
                    available_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, failure_code, available_at, now, job_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("evaluation job retry could not be persisted")
            retrying = connection.execute(
                _EVALUATION_JOB_SELECT + " WHERE evaluation_jobs.job_id = ?",
                (job_id,),
            ).fetchone()
            if retrying is None:
                raise RuntimeError("retrying evaluation job could not be validated")
            connection.commit()
            return _stored_evaluation_job(retrying)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_evaluation_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        report: JsonValue,
    ) -> StoredEvaluationJob:
        """Atomically publish run evidence and complete one capability-owned lease."""

        _require_digest(job_id, label="evaluation job ID")
        _validate_worker_id(worker_id)
        _validate_lease_token(lease_token)
        token_digest = sha256(lease_token.encode("ascii")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            row = connection.execute(
                """
                SELECT evaluation_jobs.tenant_id, evaluation_jobs.status,
                       evaluation_jobs.worker_id, evaluation_jobs.lease_token_digest,
                       evaluation_jobs.lease_expires_at,
                       evaluation_jobs.suite_id, evaluation_jobs.candidate_id,
                       suites.digest AS suite_digest, suites.name AS suite_name,
                       suites.payload_json AS suite_json,
                       candidates.digest AS candidate_digest,
                       candidates.payload_json AS candidate_json
                FROM evaluation_jobs
                JOIN suites ON suites.id = evaluation_jobs.suite_id
                JOIN candidates ON candidates.id = evaluation_jobs.candidate_id
                WHERE evaluation_jobs.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "running"
                or row["worker_id"] != worker_id
                or row["lease_token_digest"] is None
                or not secrets.compare_digest(str(row["lease_token_digest"]), token_digest)
                or row["lease_expires_at"] is None
                or str(row["lease_expires_at"]) <= now
            ):
                raise EvaluationJobLeaseNotFoundError("evaluation job lease was not found")

            report_object = _require_object(report, label="report")
            report_json = canonical_json_bytes(report_object).decode("utf-8")
            report_sha256 = sha256(report_json.encode("utf-8")).hexdigest()
            suite_sha256 = str(row["suite_digest"])
            candidate_sha256 = str(row["candidate_digest"])
            suite_name = str(row["suite_name"])
            if report_object.get("suite_name") != suite_name:
                raise ValueError("suite name must match suite and report evidence")
            run_id = _require_digest(report_object.get("run_id"), label="run ID")
            if (
                _require_digest(report_object.get("suite_sha256"), label="suite digest")
                != suite_sha256
            ):
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
            _validate_derived_evaluation_report(
                suite_json=str(row["suite_json"]),
                candidate_json=str(row["candidate_json"]),
                suite_sha256=suite_sha256,
                candidate_sha256=candidate_sha256,
                report=report_object,
            )
            completion_time = _utc_now()
            if str(row["lease_expires_at"]) <= completion_time:
                raise EvaluationJobLeaseNotFoundError("evaluation job lease was not found")
            now = completion_time

            stored_run = connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if stored_run is None:
                connection.execute(
                    """
                    INSERT INTO runs(
                        run_id, suite_id, candidate_id, dataset_manifest_digest, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        int(row["suite_id"]),
                        int(row["candidate_id"]),
                        dataset_manifest_sha256,
                        now,
                    ),
                )
            stored_report = connection.execute(
                "SELECT 1 FROM reports WHERE run_id = ?", (run_id,)
            ).fetchone()
            if stored_report is None:
                connection.execute(
                    """
                    INSERT INTO reports(run_id, digest, payload_json, release_ready, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, report_sha256, report_json, int(release_ready), now),
                )
            stored_binding = connection.execute(
                """
                SELECT suites.digest AS suite_digest,
                       candidates.digest AS candidate_digest,
                       runs.dataset_manifest_digest,
                       reports.digest AS report_digest,
                       reports.payload_json AS report_json,
                       reports.release_ready
                FROM runs
                JOIN suites ON suites.id = runs.suite_id
                JOIN candidates ON candidates.id = runs.candidate_id
                JOIN reports ON reports.run_id = runs.run_id
                WHERE runs.run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if stored_binding is None:
                raise RuntimeError("stored run could not be validated")
            if (
                stored_binding["suite_digest"] != suite_sha256
                or stored_binding["candidate_digest"] != candidate_sha256
                or stored_binding["report_digest"] != report_sha256
                or stored_binding["dataset_manifest_digest"] != dataset_manifest_sha256
                or stored_binding["report_json"] != report_json
                or bool(stored_binding["release_ready"]) != release_ready
            ):
                raise ValueError("run ID is already bound to different immutable evidence")
            _insert_tenant_run_membership(
                connection,
                tenant_id=str(row["tenant_id"]),
                run_id=run_id,
                created_at=now,
            )
            updated = connection.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'succeeded', run_id = ?, failure_code = NULL,
                    worker_id = NULL, lease_token_digest = NULL, lease_expires_at = NULL,
                    available_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (run_id, now, job_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("evaluation job completion could not be persisted")
            completed = connection.execute(
                _EVALUATION_JOB_SELECT + " WHERE evaluation_jobs.job_id = ?",
                (job_id,),
            ).fetchone()
            if completed is None:
                raise RuntimeError("completed evaluation job could not be validated")
            connection.commit()
            return _stored_evaluation_job(completed)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail_evaluation_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        failure_code: Literal["invalid_evaluation"],
    ) -> StoredEvaluationJob:
        """Atomically fail invalid work through its active lease capability."""

        _require_digest(job_id, label="evaluation job ID")
        _validate_worker_id(worker_id)
        _validate_lease_token(lease_token)
        if type(failure_code) is not str or failure_code != "invalid_evaluation":
            raise ValueError("evaluation job failure code is invalid")
        token_digest = sha256(lease_token.encode("ascii")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            row = connection.execute(
                """
                SELECT status, worker_id, lease_token_digest, lease_expires_at
                FROM evaluation_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "running"
                or row["worker_id"] != worker_id
                or row["lease_token_digest"] is None
                or not secrets.compare_digest(str(row["lease_token_digest"]), token_digest)
                or row["lease_expires_at"] is None
                or str(row["lease_expires_at"]) <= now
            ):
                raise EvaluationJobLeaseNotFoundError("evaluation job lease was not found")
            updated = connection.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'failed', run_id = NULL, failure_code = ?,
                    worker_id = NULL, lease_token_digest = NULL, lease_expires_at = NULL,
                    available_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (failure_code, now, job_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("evaluation job failure could not be persisted")
            failed = connection.execute(
                _EVALUATION_JOB_SELECT + " WHERE evaluation_jobs.job_id = ?",
                (job_id,),
            ).fetchone()
            if failed is None:
                raise RuntimeError("failed evaluation job could not be validated")
            connection.commit()
            return _stored_evaluation_job(failed)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_suite(self, *, tenant_id: str, name: str, suite: JsonValue) -> StoredSuite:
        """Persist a suite idempotently by its canonical content digest."""

        _validate_tenant_id(tenant_id)
        suite_object = _require_object(suite, label="suite")
        if not name.strip() or suite_object.get("name") != name:
            raise ValueError("suite name must match suite evidence")
        payload_json = canonical_json_bytes(suite).decode("utf-8")
        suite_id = canonical_json_sha256(suite)
        created_at = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            stored_suite_id = _insert_evidence(
                connection,
                table="suites",
                digest=suite_id,
                payload_json=payload_json,
                created_at=created_at,
                name=name,
            )
            _insert_tenant_suite_membership(
                connection,
                tenant_id=tenant_id,
                suite_id=stored_suite_id,
                created_at=created_at,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        stored = self.get_suite(tenant_id=tenant_id, suite_id=suite_id)
        if stored is None:
            raise RuntimeError("stored suite could not be read back")
        return stored

    def get_suite(self, *, tenant_id: str, suite_id: str) -> StoredSuite | None:
        """Read a suite by its canonical content digest."""

        _validate_tenant_id(tenant_id)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT suites.digest, suites.name, suites.payload_json,
                       tenant_suites.created_at
                FROM tenant_suites
                JOIN suites ON suites.id = tenant_suites.suite_id
                WHERE tenant_suites.tenant_id = ? AND suites.digest = ?
                """,
                (tenant_id, suite_id),
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

    def list_suites(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[StoredSuite, ...]:
        """List suites newest first using digest order as a stable tie break."""

        _validate_tenant_id(tenant_id)
        _validate_page(limit=limit, offset=offset)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT suites.digest, suites.name, suites.payload_json,
                       tenant_suites.created_at
                FROM tenant_suites
                JOIN suites ON suites.id = tenant_suites.suite_id
                WHERE tenant_suites.tenant_id = ?
                ORDER BY tenant_suites.created_at DESC, suites.digest
                LIMIT ? OFFSET ?
                """,
                (tenant_id, limit, offset),
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

    def count_suites(self, *, tenant_id: str) -> int:
        """Return the number of immutable suite records."""

        _validate_tenant_id(tenant_id)
        connection = self._connect()
        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM tenant_suites WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def get_run(self, *, tenant_id: str, run_id: str) -> StoredRun | None:
        """Read one run and all related evidence by deterministic ID."""

        _validate_tenant_id(tenant_id)
        connection = self._connect()
        try:
            row = connection.execute(
                _RUN_SELECT + " WHERE tenant_runs.tenant_id = ? AND runs.run_id = ?",
                (tenant_id, run_id),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _stored_run(row)

    def list_runs(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RunSummary, ...]:
        """List newest history first with a deterministic run-ID tie break."""

        _validate_tenant_id(tenant_id)
        _validate_page(limit=limit, offset=offset)
        connection = self._connect()
        try:
            rows = connection.execute(
                _RUN_SELECT
                + " WHERE tenant_runs.tenant_id = ?"
                + " ORDER BY tenant_runs.created_at DESC, runs.run_id LIMIT ? OFFSET ?",
                (tenant_id, limit, offset),
            ).fetchall()
        finally:
            connection.close()
        return tuple(_stored_run(row).summary() for row in rows)

    def count_runs(self, *, tenant_id: str) -> int:
        """Return the number of immutable evaluation runs."""

        _validate_tenant_id(tenant_id)
        connection = self._connect()
        try:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM tenant_runs WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def list_compatible_runs(
        self,
        *,
        tenant_id: str,
        suite_sha256: str,
        dataset_manifest_sha256: str | None,
        limit: int = 20,
    ) -> tuple[StoredRun, ...]:
        """List a bounded tenant history for one exact suite and dataset lineage."""

        _validate_tenant_id(tenant_id)
        _require_digest(suite_sha256, label="suite digest")
        if dataset_manifest_sha256 is not None:
            _require_digest(dataset_manifest_sha256, label="dataset manifest digest")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("trend limit must be an integer from 1 through 20")
        connection = self._connect()
        try:
            rows = connection.execute(
                _RUN_SELECT
                + " WHERE tenant_runs.tenant_id = ? AND suites.digest = ?"
                + " AND runs.dataset_manifest_digest IS ?"
                + " ORDER BY tenant_runs.created_at DESC, runs.run_id LIMIT ?",
                (tenant_id, suite_sha256, dataset_manifest_sha256, limit),
            ).fetchall()
        finally:
            connection.close()
        return tuple(_stored_run(row) for row in rows)

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
    tenant_runs.created_at,
    suites.digest AS suite_digest,
    suites.name AS suite_name,
    suites.payload_json AS suite_json,
    candidates.digest AS candidate_digest,
    candidates.payload_json AS candidate_json,
    reports.digest AS report_digest,
    reports.payload_json AS report_json,
    reports.release_ready
FROM runs
JOIN tenant_runs ON tenant_runs.run_id = runs.run_id
JOIN suites ON suites.id = runs.suite_id
JOIN candidates ON candidates.id = runs.candidate_id
JOIN reports ON reports.run_id = runs.run_id
"""

_EVALUATION_JOB_SELECT = """
SELECT
    evaluation_jobs.job_id,
    evaluation_jobs.request_digest,
    evaluation_jobs.status,
    evaluation_jobs.attempts,
    evaluation_jobs.max_attempts,
    evaluation_jobs.run_id,
    evaluation_jobs.failure_code,
    evaluation_jobs.available_at,
    evaluation_jobs.submitted_at,
    evaluation_jobs.updated_at
FROM evaluation_jobs
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
        row = connection.execute(
            "SELECT id, name, payload_json FROM suites WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO suites(digest, name, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (digest, name, payload_json, created_at),
            )
            row = connection.execute(
                "SELECT id, name, payload_json FROM suites WHERE digest = ?", (digest,)
            ).fetchone()
        if row is None or row["name"] != name or row["payload_json"] != payload_json:
            raise ValueError("suite digest is already bound to different immutable evidence")
    elif table == "candidates":
        row = connection.execute(
            "SELECT id, payload_json FROM candidates WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO candidates(digest, payload_json, created_at)
                VALUES (?, ?, ?)
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


def _insert_tenant_suite_membership(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    suite_id: int,
    created_at: str,
) -> None:
    existing = connection.execute(
        """
        SELECT 1 FROM tenant_suites
        WHERE tenant_id = ? AND suite_id = ?
        """,
        (tenant_id, suite_id),
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO tenant_suites(tenant_id, suite_id, created_at)
        VALUES (?, ?, ?)
        """,
        (tenant_id, suite_id, created_at),
    )


def _insert_tenant_run_membership(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    run_id: str,
    created_at: str,
) -> None:
    existing = connection.execute(
        """
        SELECT 1 FROM tenant_runs
        WHERE tenant_id = ? AND run_id = ?
        """,
        (tenant_id, run_id),
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO tenant_runs(tenant_id, run_id, created_at)
        VALUES (?, ?, ?)
        """,
        (tenant_id, run_id, created_at),
    )


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


def _stored_evaluation_job(row: sqlite3.Row) -> StoredEvaluationJob:
    status = str(row["status"])
    if status not in {"queued", "running", "succeeded", "failed"}:
        raise RuntimeError("stored evaluation job has an invalid status")
    return StoredEvaluationJob(
        job_id=str(row["job_id"]),
        status=cast(Literal["queued", "running", "succeeded", "failed"], status),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        run_id=None if row["run_id"] is None else str(row["run_id"]),
        failure_code=None if row["failure_code"] is None else str(row["failure_code"]),
        available_at=None if row["available_at"] is None else str(row["available_at"]),
        submitted_at=str(row["submitted_at"]),
        updated_at=str(row["updated_at"]),
    )


def _validate_derived_evaluation_report(
    *,
    suite_json: str,
    candidate_json: str,
    suite_sha256: str,
    candidate_sha256: str,
    report: dict[str, JsonValue],
) -> None:
    try:
        suite = EvaluationSuite.model_validate_json(suite_json)
        candidate = json.loads(candidate_json)
        if not isinstance(candidate, dict):
            raise ValueError("candidate evidence must be an object")
        candidate_outputs = {
            case_id: (output if isinstance(output, str) else CandidateOutput.model_validate(output))
            for case_id, output in candidate.items()
        }
        expected_evaluation = evaluate_suite(
            suite.cases,
            candidate_outputs,
            policy=suite.resolved_policy,
        )
        expected_run_id = deterministic_run_id(
            suite_sha256=suite_sha256,
            candidate_sha256=candidate_sha256,
            dataset_manifest_sha256=None,
        )
        expected_report = cast(
            JsonValue,
            {
                "suite_name": suite.name,
                **expected_evaluation.model_dump(mode="json"),
                "schema_version": 5,
                "suite_sha256": suite_sha256,
                "candidate_sha256": candidate_sha256,
                "dataset_manifest_sha256": None,
                "evaluation_semantics_version": EVALUATION_SEMANTICS_VERSION,
                "canonicalization_version": CANONICALIZATION_VERSION,
                "run_id": expected_run_id,
            },
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("report failed derived evidence validation") from exc
    if canonical_json_bytes(report) != canonical_json_bytes(expected_report):
        raise ValueError("report failed derived evidence validation")


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


def _validate_tenant_id(tenant_id: str) -> None:
    if type(tenant_id) is not str:
        raise ValueError("tenant ID is invalid")
    if tenant_id == TRUSTED_LOCAL_TENANT_ID:
        return
    if (
        not 1 <= len(tenant_id) <= 64
        or not tenant_id[0].isalnum()
        or not tenant_id[-1].isalnum()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in tenant_id)
    ):
        raise ValueError("tenant ID is invalid")


def _validate_idempotency_key(idempotency_key: str) -> None:
    if (
        type(idempotency_key) is not str
        or not 16 <= len(idempotency_key) <= 128
        or any(not "!" <= character <= "~" for character in idempotency_key)
    ):
        raise ValueError("idempotency key is invalid")


def _validate_worker_id(worker_id: str) -> None:
    if (
        type(worker_id) is not str
        or not 1 <= len(worker_id) <= 64
        or not worker_id[0].isalnum()
        or not worker_id[-1].isalnum()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in worker_id)
    ):
        raise ValueError("worker ID is invalid")


def _validate_lease_token(lease_token: str) -> None:
    if (
        type(lease_token) is not str
        or not 32 <= len(lease_token) <= 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in lease_token
        )
    ):
        raise ValueError("lease token is invalid")


def _lease_expiry(now: str, lease_seconds: int) -> str:
    parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return (
        (parsed + timedelta(seconds=lease_seconds))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
