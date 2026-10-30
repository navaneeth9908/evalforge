from __future__ import annotations

from pathlib import Path

import pytest


def _evaluation_payloads() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    from evalforge.provenance import canonical_json_sha256, deterministic_run_id

    suite: dict[str, object] = {
        "schema_version": 1,
        "name": "registry-smoke",
        "minimum_pass_rate": 1.0,
        "cases": [
            {
                "case_id": "policy",
                "prompt": "State the synthetic policy.",
                "expected_output": "Allowed",
            }
        ],
    }
    candidate: dict[str, object] = {"policy": "Allowed"}
    suite_sha256 = canonical_json_sha256(suite)
    candidate_sha256 = canonical_json_sha256(candidate)
    run_id = deterministic_run_id(
        suite_sha256=suite_sha256,
        candidate_sha256=candidate_sha256,
        dataset_manifest_sha256=None,
    )
    report: dict[str, object] = {
        "schema_version": 5,
        "run_id": run_id,
        "suite_name": "registry-smoke",
        "suite_sha256": suite_sha256,
        "candidate_sha256": candidate_sha256,
        "dataset_manifest_sha256": None,
        "release_ready": True,
        "passed_cases": 1,
        "total_cases": 1,
    }
    return suite, candidate, report


def test_create_get_and_list_run_history_transactionally(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    created = registry.create_run(
        tenant_id="__local__",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )
    loaded = registry.get_run(tenant_id="__local__", run_id=created.run_id)
    listed = registry.list_runs(tenant_id="__local__")

    assert loaded == created
    assert loaded is not None
    assert loaded.suite == suite
    assert loaded.candidate == candidate
    assert loaded.report == report
    assert listed == (created.summary(),)


@pytest.mark.parametrize(
    "statement",
    ("UPDATE runs SET created_at = 'tampered'", "DELETE FROM runs"),
)
def test_run_evidence_is_database_immutable(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")
    registry.create_run(
        tenant_id="__local__",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(statement)


@pytest.mark.parametrize(
    "statement",
    (
        """
        INSERT OR REPLACE INTO candidates(id, digest, payload_json, created_at)
        SELECT id, digest, '{}', 'tampered' FROM candidates LIMIT 1
        """,
        """
        INSERT OR REPLACE INTO runs(
            run_id, suite_id, candidate_id, dataset_manifest_digest, created_at
        )
        SELECT run_id, suite_id, candidate_id, dataset_manifest_digest, 'tampered'
        FROM runs LIMIT 1
        """,
        """
        INSERT OR REPLACE INTO reports(
            id, run_id, digest, payload_json, release_ready, created_at
        )
        SELECT id, run_id, digest, '{}', 0, 'tampered' FROM reports LIMIT 1
        """,
    ),
)
def test_run_evidence_rejects_insert_or_replace_tampering(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")
    registry.create_run(
        tenant_id="__local__",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(statement)


def test_registry_applies_migrations_and_safe_connection_defaults(tmp_path: Path) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    registry = SQLiteRunRegistry(tmp_path / "history.db")

    diagnostics = registry.diagnostics()
    assert diagnostics.schema_version == 5
    assert diagnostics.foreign_keys is True
    assert diagnostics.journal_mode == "wal"
    assert diagnostics.busy_timeout_ms == 5_000
    assert diagnostics.synchronous == 1

    with sqlite3.connect(registry.path) as connection:
        run_foreign_keys = connection.execute("PRAGMA foreign_key_list(runs)").fetchall()
        report_foreign_keys = connection.execute("PRAGMA foreign_key_list(reports)").fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
            )
        }
    assert {row[2] for row in run_foreign_keys} == {"suites", "candidates"}
    assert {row[2] for row in report_foreign_keys} == {"runs"}
    assert {
        "schema_migrations",
        "suites",
        "tenant_suites",
        "tenant_runs",
        "candidates",
        "runs",
        "reports",
        "evaluation_jobs",
    } <= tables


def test_evaluation_job_submission_is_idempotent_and_tenant_scoped(tmp_path: Path) -> None:
    from evalforge.registry import EvaluationJobConflictError, SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "jobs.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )

    created = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-test-0001",
    )
    replayed = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-test-0001",
    )

    assert replayed == created
    assert len(created.job_id) == 64
    assert created.status == "queued"
    assert created.attempts == 0
    assert created.max_attempts == 3
    assert created.run_id is None
    assert created.failure_code is None
    assert registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=created.job_id) == created
    assert registry.get_evaluation_job(tenant_id="tenant-beta", job_id=created.job_id) is None
    assert registry.count_runs(tenant_id="tenant-alpha") == 0

    import sqlite3

    with sqlite3.connect(registry.path) as connection:
        idempotency_digest, request_digest = connection.execute(
            "SELECT idempotency_key_digest, request_digest FROM evaluation_jobs"
        ).fetchone()
    assert len(idempotency_digest) == len(request_digest) == 64
    assert "evaluation-job-test-0001" not in {idempotency_digest, request_digest}

    with pytest.raises(EvaluationJobConflictError, match="idempotency key"):
        registry.create_evaluation_job(
            tenant_id="tenant-alpha",
            suite_id=stored_suite.suite_id,
            candidate={"policy": "Denied"},
            idempotency_key="evaluation-job-test-0001",
        )


def test_evaluation_job_binds_digest_to_one_candidate_snapshot(tmp_path: Path) -> None:
    import hashlib
    import sqlite3

    from evalforge.provenance import canonical_json_sha256
    from evalforge.registry import SQLiteRunRegistry

    class StatefulCandidate(dict[str, str]):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def items(self):  # type: ignore[no-untyped-def]
            self.reads += 1
            value = "Allowed" if self.reads == 1 else "Denied"
            return {"policy": value}.items()

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "snapshot.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )

    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate=StatefulCandidate(),
        idempotency_key="evaluation-job-test-0002",
    )

    with sqlite3.connect(registry.path) as connection:
        candidate_digest, candidate_json, request_digest = connection.execute(
            """
            SELECT candidates.digest, candidates.payload_json, evaluation_jobs.request_digest
            FROM evaluation_jobs
            JOIN candidates ON candidates.id = evaluation_jobs.candidate_id
            """
        ).fetchone()
    assert candidate_digest == hashlib.sha256(candidate_json.encode("utf-8")).hexdigest()
    assert request_digest == canonical_json_sha256(
        {"suite_id": stored_suite.suite_id, "candidate_sha256": candidate_digest}
    )


def test_evaluation_job_claim_is_atomic_and_keeps_lease_token_out_of_storage(
    tmp_path: Path,
) -> None:
    import hashlib
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    path = tmp_path / "leases.db"
    submitter = SQLiteRunRegistry(path)
    stored_suite = submitter.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = submitter.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-lease-0001",
    )
    competing_worker = SQLiteRunRegistry(path)

    claimed = submitter.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    unavailable = competing_worker.claim_evaluation_job(worker_id="worker-b", lease_seconds=30)

    assert claimed is not None
    assert claimed.job_id == queued.job_id
    assert claimed.tenant_id == "tenant-alpha"
    assert claimed.suite_id == stored_suite.suite_id
    assert claimed.suite == suite
    assert claimed.candidate == {"policy": "Allowed"}
    assert claimed.attempts == 1
    assert claimed.max_attempts == 3
    assert unavailable is None
    assert (
        submitter.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id).status
        == "running"
    )

    with sqlite3.connect(path) as connection:
        worker_id, token_digest, lease_expires_at = connection.execute(
            """
            SELECT worker_id, lease_token_digest, lease_expires_at
            FROM evaluation_jobs WHERE job_id = ?
            """,
            (queued.job_id,),
        ).fetchone()
    assert worker_id == "worker-a"
    assert token_digest == hashlib.sha256(claimed.lease_token.encode("ascii")).hexdigest()
    assert claimed.lease_token != token_digest
    assert len(token_digest) == 64
    assert lease_expires_at == claimed.lease_expires_at


def test_active_worker_can_atomically_renew_its_evaluation_job_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from evalforge import registry as registry_module
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "renew-lease.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-renew-0001",
    )
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000000Z",
            "2026-01-01T00:00:10.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None

    renewed = registry.renew_evaluation_job_lease(
        job_id=claimed.job_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        lease_seconds=60,
    )

    assert renewed.job_id == claimed.job_id
    assert renewed.attempts == claimed.attempts == 1
    assert renewed.lease_expires_at == "2026-01-01T00:01:10.000000Z"
    with sqlite3.connect(registry.path) as connection:
        attempts, worker_id, token_digest, lease_expires_at, updated_at = connection.execute(
            """
            SELECT attempts, worker_id, lease_token_digest, lease_expires_at, updated_at
            FROM evaluation_jobs WHERE job_id = ?
            """,
            (claimed.job_id,),
        ).fetchone()
    assert attempts == 1
    assert worker_id == "worker-a"
    assert token_digest != claimed.lease_token
    assert lease_expires_at == renewed.lease_expires_at
    assert updated_at == "2026-01-01T00:00:10.000000Z"


def test_lease_renewal_rotates_the_capability_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import sqlite3

    from evalforge import registry as registry_module
    from evalforge.registry import EvaluationJobLeaseNotFoundError, SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "rotate-renewal-token.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-rotate-renewal-token",
    )
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000000Z",
            "2026-01-01T00:00:10.000000Z",
            "2026-01-01T00:00:20.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None

    renewed = registry.renew_evaluation_job_lease(
        job_id=claimed.job_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        lease_seconds=60,
    )

    assert renewed.lease_token != claimed.lease_token
    with sqlite3.connect(registry.path) as connection:
        token_digest = connection.execute(
            "SELECT lease_token_digest FROM evaluation_jobs WHERE job_id = ?",
            (claimed.job_id,),
        ).fetchone()[0]
    assert token_digest == hashlib.sha256(renewed.lease_token.encode("ascii")).hexdigest()
    with pytest.raises(EvaluationJobLeaseNotFoundError, match="evaluation job lease was not found"):
        registry.renew_evaluation_job_lease(
            job_id=claimed.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token,
            lease_seconds=120,
        )


@pytest.mark.parametrize("invalid_capability", ("job", "worker", "token"))
def test_lease_renewal_rejects_unknown_capabilities_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_capability: str,
) -> None:
    import sqlite3

    from evalforge import registry as registry_module
    from evalforge.registry import EvaluationJobLeaseNotFoundError, SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / f"reject-renewal-{invalid_capability}.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key=f"evaluation-job-reject-renewal-{invalid_capability}",
    )
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000000Z",
            "2026-01-01T00:00:10.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    arguments = {
        "job_id": claimed.job_id,
        "worker_id": "worker-a",
        "lease_token": claimed.lease_token,
        "lease_seconds": 60,
    }
    if invalid_capability == "job":
        arguments["job_id"] = "f" * 64
    elif invalid_capability == "worker":
        arguments["worker_id"] = "worker-b"
    else:
        arguments["lease_token"] = "a" * len(claimed.lease_token)

    with pytest.raises(EvaluationJobLeaseNotFoundError, match="evaluation job lease was not found"):
        registry.renew_evaluation_job_lease(**arguments)  # type: ignore[arg-type]

    with sqlite3.connect(registry.path) as connection:
        attempts, worker_id, lease_expires_at, updated_at = connection.execute(
            """
            SELECT attempts, worker_id, lease_expires_at, updated_at
            FROM evaluation_jobs WHERE job_id = ?
            """,
            (claimed.job_id,),
        ).fetchone()
    assert attempts == 1
    assert worker_id == "worker-a"
    assert lease_expires_at == claimed.lease_expires_at
    assert updated_at == "2026-01-01T00:00:00.000000Z"


def test_evaluation_job_lease_starts_after_write_lock_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from evalforge import registry as registry_module
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    path = tmp_path / "contended-lease.db"
    registry = SQLiteRunRegistry(path)
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-contended-lease-0001",
    )
    lock_held = threading.Event()
    monkeypatch.setattr(
        registry_module,
        "_utc_now",
        lambda: (
            "2026-01-01T00:00:00.000000Z" if lock_held.is_set() else "2026-01-01T00:00:02.000000Z"
        ),
    )

    with sqlite3.connect(path) as blocker, ThreadPoolExecutor(max_workers=1) as executor:
        blocker.execute("BEGIN IMMEDIATE")
        lock_held.set()
        future = executor.submit(
            registry.claim_evaluation_job,
            worker_id="worker-a",
            lease_seconds=1,
        )
        time.sleep(0.1)
        blocker.rollback()
        lock_held.clear()
        claimed = future.result(timeout=2)

    assert claimed is not None
    assert claimed.lease_expires_at == "2026-01-01T00:00:03.000000Z"
    assert registry.claim_evaluation_job(worker_id="worker-b", lease_seconds=1) is None


def test_exhausted_queued_job_does_not_poison_later_work(tmp_path: Path) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "exhausted-queued.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    exhausted = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "first"},
        idempotency_key="evaluation-job-exhausted-queued-0001",
    )
    available = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "second"},
        idempotency_key="evaluation-job-exhausted-queued-0002",
    )
    with sqlite3.connect(registry.path) as connection:
        connection.execute(
            "UPDATE evaluation_jobs SET attempts = max_attempts WHERE job_id = ?",
            (exhausted.job_id,),
        )

    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)

    assert claimed is not None
    assert claimed.job_id == available.job_id
    terminal = registry.get_evaluation_job(
        tenant_id="tenant-alpha",
        job_id=exhausted.job_id,
    )
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.failure_code == "attempt_limit_exhausted"


def test_expired_evaluation_job_leases_retry_only_to_the_attempt_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge import registry as registry_module
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "retry-leases.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-lease-0002",
    )
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000000Z",
            "2026-01-01T00:00:02.000000Z",
            "2026-01-01T00:00:04.000000Z",
            "2026-01-01T00:00:06.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))

    first = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=1)
    second = registry.claim_evaluation_job(worker_id="worker-b", lease_seconds=1)
    third = registry.claim_evaluation_job(worker_id="worker-c", lease_seconds=1)
    exhausted = registry.claim_evaluation_job(worker_id="worker-d", lease_seconds=1)

    assert first is not None and first.attempts == 1
    assert second is not None and second.attempts == 2
    assert third is not None and third.attempts == 3
    assert len({first.lease_token, second.lease_token, third.lease_token}) == 3
    assert exhausted is None
    terminal = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.attempts == terminal.max_attempts == 3
    assert terminal.failure_code == "attempt_limit_exhausted"


def test_invalid_evaluation_failure_is_atomic_and_clears_active_lease(tmp_path: Path) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "terminal-failure.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"unexpected": "output"},
        idempotency_key="evaluation-job-terminal-failure-0001",
    )
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None

    failed = registry.fail_evaluation_job(
        job_id=claimed.job_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        failure_code="invalid_evaluation",
    )

    assert failed.job_id == queued.job_id
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.run_id is None
    assert failed.failure_code == "invalid_evaluation"
    assert registry.count_runs(tenant_id="tenant-alpha") == 0
    assert registry.claim_evaluation_job(worker_id="worker-b", lease_seconds=30) is None
    with sqlite3.connect(registry.path) as connection:
        lease_state = connection.execute(
            """
            SELECT worker_id, lease_token_digest, lease_expires_at
            FROM evaluation_jobs WHERE job_id = ?
            """,
            (queued.job_id,),
        ).fetchone()
    assert lease_state == (None, None, None)


@pytest.mark.parametrize("failure_code", ("temporary_error", 1))
def test_evaluation_job_failure_rejects_unclassified_codes_without_mutation(
    tmp_path: Path, failure_code: object
) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "unclassified-failure.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"unexpected": "output"},
        idempotency_key="evaluation-job-unclassified-failure-0001",
    )
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None

    with pytest.raises(ValueError, match="evaluation job failure code is invalid"):
        registry.fail_evaluation_job(
            job_id=claimed.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token,
            failure_code=failure_code,  # type: ignore[arg-type]
        )

    current = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=claimed.job_id)
    assert current is not None
    assert current.status == "running"
    assert current.attempts == 1
    assert current.failure_code is None


def test_stale_worker_cannot_fail_job_after_another_worker_reclaims_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from evalforge import registry as registry_module
    from evalforge.registry import EvaluationJobLeaseNotFoundError, SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "stale-failure.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"unexpected": "output"},
        idempotency_key="evaluation-job-stale-failure-0001",
    )
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000000Z",
            "2026-01-01T00:00:02.000000Z",
            "2026-01-01T00:00:02.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))
    stale = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=1)
    active = registry.claim_evaluation_job(worker_id="worker-b", lease_seconds=30)
    assert stale is not None and active is not None

    with pytest.raises(EvaluationJobLeaseNotFoundError, match="evaluation job lease was not found"):
        registry.fail_evaluation_job(
            job_id=stale.job_id,
            worker_id="worker-a",
            lease_token=stale.lease_token,
            failure_code="invalid_evaluation",
        )

    current = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=active.job_id)
    assert current is not None
    assert current.status == "running"
    assert current.attempts == 2
    with sqlite3.connect(registry.path) as connection:
        worker_id, lease_expires_at = connection.execute(
            "SELECT worker_id, lease_expires_at FROM evaluation_jobs WHERE job_id = ?",
            (active.job_id,),
        ).fetchone()
    assert worker_id == "worker-b"
    assert lease_expires_at == active.lease_expires_at


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE evaluation_jobs SET status = 'running'",
        """
        UPDATE evaluation_jobs
        SET status = 'running', attempts = 1, worker_id = 'Worker-A',
            lease_token_digest = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            lease_expires_at = '2026-01-01T00:00:01.000000Z'
        """,
        """
        UPDATE evaluation_jobs
        SET status = 'running', attempts = 1, worker_id = 'worker-a',
            lease_token_digest = 'gggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg',
            lease_expires_at = '2026-01-01T00:00:01.000000Z'
        """,
        """
        UPDATE evaluation_jobs
        SET status = 'running', attempts = 0, worker_id = 'worker-a',
            lease_token_digest = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            lease_expires_at = 'not-a-canonical-timestamp'
        """,
        """
        UPDATE evaluation_jobs
        SET status = 'running', attempts = 1, worker_id = NULL,
            lease_token_digest = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            lease_expires_at = '2026-01-01T00:00:01.000000Z'
        """,
        """
        UPDATE evaluation_jobs
        SET status = 'running', attempts = 1, worker_id = 'worker-a',
            lease_token_digest = NULL,
            lease_expires_at = '2026-01-01T00:00:01.000000Z'
        """,
        """
        UPDATE evaluation_jobs
        SET status = 'running', attempts = 1, worker_id = 'worker-a',
            lease_token_digest = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            lease_expires_at = '2026-02-30T00:00:01.000000Z'
        """,
    ),
)
def test_evaluation_job_lease_state_rejects_invalid_database_mutation(
    tmp_path: Path, statement: str
) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "lease-state.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-lease-0003",
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="lease state is invalid"),
    ):
        connection.execute(statement)


def test_v4_running_evaluation_job_migration_fails_closed(tmp_path: Path) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    path = tmp_path / "legacy-running-job.db"
    registry = SQLiteRunRegistry(path)
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate={"policy": "Allowed"},
        idempotency_key="evaluation-job-lease-0004",
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER evaluation_jobs_lease_state_insert")
        connection.execute("DROP TRIGGER evaluation_jobs_lease_state_update")
        connection.execute("ALTER TABLE evaluation_jobs DROP COLUMN worker_id")
        connection.execute("ALTER TABLE evaluation_jobs DROP COLUMN lease_token_digest")
        connection.execute("ALTER TABLE evaluation_jobs DROP COLUMN lease_expires_at")
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute(
            """
            UPDATE evaluation_jobs
            SET status = 'running', attempts = 1, updated_at = '2026-01-01T00:00:00.000000Z'
            WHERE job_id = ?
            """,
            (queued.job_id,),
        )

    migrated = SQLiteRunRegistry(path)
    terminal = migrated.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)

    assert migrated.diagnostics().schema_version == 5
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.attempts == 1
    assert terminal.failure_code == "legacy_running_state"
    assert migrated.claim_evaluation_job(worker_id="worker-a", lease_seconds=30) is None


def test_v1_registry_migration_assigns_legacy_suites_only_to_trusted_local(
    tmp_path: Path,
) -> None:
    import sqlite3

    from evalforge.auth import TRUSTED_LOCAL_TENANT_ID
    from evalforge.provenance import canonical_json_bytes, canonical_json_sha256
    from evalforge.registry import SQLiteRunRegistry

    path = tmp_path / "legacy.db"
    SQLiteRunRegistry(path)
    suite, _candidate, _report = _evaluation_payloads()
    suite_id = canonical_json_sha256(suite)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("DROP INDEX evaluation_jobs_tenant_page_idx")
        connection.execute("DROP TABLE evaluation_jobs")
        connection.execute("DELETE FROM schema_migrations WHERE version = 4")
        connection.execute("DROP INDEX tenant_runs_page_idx")
        connection.execute("DROP TABLE tenant_runs")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.execute("DROP TRIGGER suites_immutable_insert")
        connection.execute("DROP TRIGGER candidates_immutable_insert")
        connection.execute("DROP TRIGGER runs_immutable_insert")
        connection.execute("DROP TRIGGER reports_immutable_insert")
        connection.execute("DROP TRIGGER tenant_suites_immutable_update")
        connection.execute("DROP TRIGGER tenant_suites_immutable_delete")
        connection.execute("DROP INDEX tenant_suites_page_idx")
        connection.execute("DROP TABLE tenant_suites")
        connection.execute("DELETE FROM schema_migrations WHERE version = 2")
        connection.execute(
            """
            INSERT INTO suites(digest, name, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                suite_id,
                "registry-smoke",
                canonical_json_bytes(suite).decode("utf-8"),
                "2026-01-01T00:00:00.000000Z",
            ),
        )

    migrated = SQLiteRunRegistry(path)

    assert migrated.diagnostics().schema_version == 5
    assert migrated.get_suite(tenant_id=TRUSTED_LOCAL_TENANT_ID, suite_id=suite_id) is not None
    assert migrated.get_suite(tenant_id="tenant-alpha", suite_id=suite_id) is None
    assert migrated.count_suites(tenant_id=TRUSTED_LOCAL_TENANT_ID) == 1
    assert SQLiteRunRegistry(path).diagnostics().schema_version == 5


def test_v2_registry_migration_assigns_legacy_runs_only_to_trusted_local(
    tmp_path: Path,
) -> None:
    import sqlite3

    from evalforge.auth import TRUSTED_LOCAL_TENANT_ID
    from evalforge.registry import SQLiteRunRegistry

    path = tmp_path / "legacy-runs.db"
    registry = SQLiteRunRegistry(path)
    suite, candidate, report = _evaluation_payloads()
    created = registry.create_run(
        tenant_id=TRUSTED_LOCAL_TENANT_ID,
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 5")
        connection.execute("DROP INDEX evaluation_jobs_tenant_page_idx")
        connection.execute("DROP TABLE evaluation_jobs")
        connection.execute("DELETE FROM schema_migrations WHERE version = 4")
        connection.execute("DROP INDEX tenant_runs_page_idx")
        connection.execute("DROP TABLE tenant_runs")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")

    migrated = SQLiteRunRegistry(path)

    assert migrated.diagnostics().schema_version == 5
    assert (
        migrated.get_run(
            tenant_id=TRUSTED_LOCAL_TENANT_ID,
            run_id=created.run_id,
        )
        is not None
    )
    assert migrated.get_run(tenant_id="tenant-alpha", run_id=created.run_id) is None
    assert migrated.count_runs(tenant_id=TRUSTED_LOCAL_TENANT_ID) == 1


def test_identical_suite_content_has_independent_tenant_memberships(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge import registry as registry_module
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "memberships.db")
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000001Z",
            "2026-01-01T00:00:00.000002Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))

    alpha = registry.create_suite(tenant_id="tenant-alpha", name="registry-smoke", suite=suite)
    beta = registry.create_suite(tenant_id="tenant-beta", name="registry-smoke", suite=suite)

    assert alpha.suite_id == beta.suite_id
    assert alpha.created_at == "2026-01-01T00:00:00.000001Z"
    assert beta.created_at == "2026-01-01T00:00:00.000002Z"
    assert registry.list_suites(tenant_id="tenant-alpha") == (alpha,)
    assert registry.list_suites(tenant_id="tenant-beta") == (beta,)


@pytest.mark.parametrize(
    "tenant_id",
    ("", "Tenant-Alpha", "tenant alpha", "-tenant-alpha", "tenant-alpha-", "a" * 65),
)
def test_registry_rejects_invalid_tenant_id_before_persistence(
    tmp_path: Path, tenant_id: str
) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "invalid-tenant.db")

    with pytest.raises(ValueError, match="tenant ID is invalid"):
        registry.create_suite(tenant_id=tenant_id, name="registry-smoke", suite=suite)

    assert registry.count_suites(tenant_id="tenant-alpha") == 0


def test_registry_rejects_tenant_id_subclasses_before_sql(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    class TenantId(str):
        pass

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "invalid-tenant-subclass.db")

    with pytest.raises(ValueError, match="tenant ID is invalid"):
        registry.create_suite(
            tenant_id=TenantId("tenant-alpha"),
            name="registry-smoke",
            suite=suite,
        )

    assert registry.count_suites(tenant_id="tenant-alpha") == 0


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE tenant_suites SET created_at = 'tampered'",
        "DELETE FROM tenant_suites",
        """
        INSERT OR REPLACE INTO tenant_suites(tenant_id, suite_id, created_at)
        SELECT tenant_id, suite_id, 'tampered' FROM tenant_suites LIMIT 1
        """,
    ),
)
def test_tenant_suite_membership_is_database_immutable(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "memberships.db")
    registry.create_suite(tenant_id="tenant-alpha", name="registry-smoke", suite=suite)

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(statement)


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE tenant_runs SET created_at = 'tampered'",
        "DELETE FROM tenant_runs",
        """
        INSERT OR REPLACE INTO tenant_runs(tenant_id, run_id, created_at)
        SELECT tenant_id, run_id, 'tampered' FROM tenant_runs LIMIT 1
        """,
    ),
)
def test_tenant_run_membership_is_database_immutable(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "run-memberships.db")
    registry.create_run(
        tenant_id="tenant-alpha",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(statement)


def test_rejected_cross_tenant_run_collision_does_not_grant_membership(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "run-collision.db")
    created = registry.create_run(
        tenant_id="tenant-alpha",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )
    altered_report = {**report, "release_ready": False}

    with pytest.raises(ValueError, match="different immutable evidence"):
        registry.create_run(
            tenant_id="tenant-beta",
            suite_name="registry-smoke",
            suite=suite,
            candidate=candidate,
            report=altered_report,
        )

    assert registry.count_suites(tenant_id="tenant-beta") == 0
    assert registry.count_runs(tenant_id="tenant-beta") == 0
    assert registry.get_run(tenant_id="tenant-beta", run_id=created.run_id) is None


def test_suite_evidence_cannot_be_replaced_and_retarget_tenant_membership(
    tmp_path: Path,
) -> None:
    import sqlite3

    from evalforge.provenance import canonical_json_bytes, canonical_json_sha256
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    replacement = {**suite, "name": "replacement-suite"}
    registry = SQLiteRunRegistry(tmp_path / "memberships.db")
    created = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        suite_row_id = connection.execute(
            "SELECT id FROM suites WHERE digest = ?", (created.suite_id,)
        ).fetchone()[0]
        connection.execute(
            """
            INSERT OR REPLACE INTO suites(id, digest, name, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                suite_row_id,
                canonical_json_sha256(replacement),
                "replacement-suite",
                canonical_json_bytes(replacement).decode("utf-8"),
                "2026-01-02T00:00:00.000000Z",
            ),
        )


def test_create_run_rejects_a_run_id_not_derived_from_its_evidence(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    report["run_id"] = "0" * 64
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    with pytest.raises(ValueError, match="run ID does not match"):
        registry.create_run(
            tenant_id="__local__",
            suite_name="registry-smoke",
            suite=suite,
            candidate=candidate,
            report=report,
        )
    assert registry.list_runs(tenant_id="__local__") == ()


def test_create_run_rejects_suite_name_metadata_mismatch(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    with pytest.raises(ValueError, match="suite name"):
        registry.create_run(
            tenant_id="__local__",
            suite_name="different-suite",
            suite=suite,
            candidate=candidate,
            report=report,
        )


def test_compatible_run_history_rejects_more_than_twenty_points(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService

    registry = SQLiteRunRegistry(tmp_path / "bounded-trends.db")
    service = EvalForgeService(registry)

    with pytest.raises(ValueError, match="trend limit must be an integer from 1 through 20"):
        registry.list_compatible_runs(
            tenant_id="__local__",
            suite_sha256="0" * 64,
            dataset_manifest_sha256=None,
            limit=21,
        )
    with pytest.raises(ValueError, match="trend limit must be an integer from 1 through 20"):
        service.get_run_trend(
            tenant_id="__local__",
            anchor_run_id="0" * 64,
            limit=21,
        )


def test_evaluation_job_completion_requires_active_capability_and_publishes_atomically(
    tmp_path: Path,
) -> None:
    import sqlite3

    from evalforge.registry import EvaluationJobLeaseNotFoundError, SQLiteRunRegistry
    from evalforge.service import _evaluation_report_payload

    suite, candidate, _minimal_report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "complete-job.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate=candidate,
        idempotency_key="evaluation-job-completion-0001",
    )
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    _suite_name, report = _evaluation_report_payload(
        suite_id=stored_suite.suite_id,
        suite_payload=suite,
        candidate_payload=candidate,
    )

    with pytest.raises(EvaluationJobLeaseNotFoundError, match="evaluation job lease was not found"):
        registry.complete_evaluation_job(
            job_id=claimed.job_id,
            worker_id="worker-a",
            lease_token="a" * len(claimed.lease_token),
            report=report,
        )

    running = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)
    assert running is not None and running.status == "running"
    assert registry.count_runs(tenant_id="tenant-alpha") == 0

    completed = registry.complete_evaluation_job(
        job_id=claimed.job_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        report=report,
    )

    assert completed.status == "succeeded"
    assert completed.attempts == 1
    assert completed.run_id == report["run_id"]
    assert completed.failure_code is None
    assert registry.get_run(tenant_id="tenant-alpha", run_id=completed.run_id) is not None
    assert registry.count_runs(tenant_id="tenant-alpha") == 1
    assert registry.claim_evaluation_job(worker_id="worker-b", lease_seconds=30) is None
    with sqlite3.connect(registry.path) as connection:
        lease_state = connection.execute(
            """
            SELECT worker_id, lease_token_digest, lease_expires_at
            FROM evaluation_jobs WHERE job_id = ?
            """,
            (claimed.job_id,),
        ).fetchone()
    assert lease_state == (None, None, None)


def test_evaluation_job_completion_rejects_rehashed_derived_report_tampering(
    tmp_path: Path,
) -> None:
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import _evaluation_report_payload

    suite, candidate, _minimal_report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "tampered-completion.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate=candidate,
        idempotency_key="evaluation-job-completion-tamper",
    )
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    _suite_name, report = _evaluation_report_payload(
        suite_id=stored_suite.suite_id,
        suite_payload=suite,
        candidate_payload=candidate,
    )
    assert isinstance(report, dict)
    tampered = {**report, "passed_cases": 0}

    with pytest.raises(ValueError, match="derived evidence"):
        registry.complete_evaluation_job(
            job_id=claimed.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token,
            report=tampered,
        )

    running = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)
    assert running is not None and running.status == "running"
    assert registry.count_runs(tenant_id="tenant-alpha") == 0
    completed = registry.complete_evaluation_job(
        job_id=claimed.job_id,
        worker_id="worker-a",
        lease_token=claimed.lease_token,
        report=report,
    )
    assert completed.status == "succeeded"


@pytest.mark.parametrize("mutation", ("string-count", "boolean-count", "manifest"))
def test_evaluation_job_completion_requires_exact_authoritative_report(
    tmp_path: Path,
    mutation: str,
) -> None:
    from evalforge.provenance import deterministic_run_id
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import _evaluation_report_payload

    suite, candidate, _minimal_report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / f"authoritative-{mutation}.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate=candidate,
        idempotency_key=f"evaluation-job-authoritative-{mutation}",
    )
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    _suite_name, report = _evaluation_report_payload(
        suite_id=stored_suite.suite_id,
        suite_payload=suite,
        candidate_payload=candidate,
    )
    assert isinstance(report, dict)
    if mutation == "string-count":
        altered = {**report, "passed_cases": "1"}
    elif mutation == "boolean-count":
        altered = {**report, "passed_cases": True}
    else:
        manifest = "f" * 64
        altered = {
            **report,
            "dataset_manifest_sha256": manifest,
            "run_id": deterministic_run_id(
                suite_sha256=stored_suite.suite_id,
                candidate_sha256=str(report["candidate_sha256"]),
                dataset_manifest_sha256=manifest,
            ),
        }

    with pytest.raises(ValueError, match="derived evidence"):
        registry.complete_evaluation_job(
            job_id=claimed.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token,
            report=altered,
        )

    running = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)
    assert running is not None and running.status == "running"
    assert registry.count_runs(tenant_id="tenant-alpha") == 0


def test_evaluation_job_completion_rechecks_expiry_after_report_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evalforge import registry as registry_module
    from evalforge.registry import EvaluationJobLeaseNotFoundError, SQLiteRunRegistry
    from evalforge.service import _evaluation_report_payload

    suite, candidate, _minimal_report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "completion-expiry.db")
    stored_suite = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )
    queued = registry.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate=candidate,
        idempotency_key="evaluation-job-completion-expiry",
    )
    _suite_name, report = _evaluation_report_payload(
        suite_id=stored_suite.suite_id,
        suite_payload=suite,
        candidate_payload=candidate,
    )
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000000Z",
            "2026-01-01T00:00:00.500000Z",
            "2026-01-01T00:00:01.100000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))
    claimed = registry.claim_evaluation_job(worker_id="worker-a", lease_seconds=1)
    assert claimed is not None

    with pytest.raises(EvaluationJobLeaseNotFoundError, match="evaluation job lease was not found"):
        registry.complete_evaluation_job(
            job_id=claimed.job_id,
            worker_id="worker-a",
            lease_token=claimed.lease_token,
            report=report,
        )

    running = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)
    assert running is not None and running.status == "running"
    assert registry.count_runs(tenant_id="tenant-alpha") == 0
