import traceback
from pathlib import Path

import pytest


def _exception_evidence(error: BaseException) -> str:
    evidence: list[str] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        evidence.extend(traceback.format_exception(current))
        current_traceback = current.__traceback__
        while current_traceback is not None:
            frame = current_traceback.tb_frame
            filename = frame.f_code.co_filename.replace("\\", "/")
            if "/src/evalforge/" in filename:
                evidence.append(repr(frame.f_locals))
            current_traceback = current_traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(evidence)


def test_worker_executes_one_job_and_publishes_tenant_scoped_report(tmp_path: Path) -> None:
    from evalforge.contracts import EvaluationSuite
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService
    from evalforge.worker import EvaluationWorker

    registry = SQLiteRunRegistry(tmp_path / "worker.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "worker-smoke",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    queued = service.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"policy": "Allowed"},
        idempotency_key="worker-execution-test-0001",
    )
    worker = EvaluationWorker(
        registry,
        worker_id="worker-a",
        lease_seconds=30,
    )

    completed = worker.run_once()

    assert completed is not None
    assert completed.job_id == queued.job_id
    assert completed.status == "succeeded"
    assert completed.run_id is not None
    stored_run = service.get_run(tenant_id="tenant-alpha", run_id=completed.run_id)
    assert stored_run is not None
    assert stored_run.report["run_id"] == completed.run_id
    assert stored_run.report["suite_name"] == "worker-smoke"
    assert stored_run.report["release_ready"] is True
    assert stored_run.report["passed_cases"] == 1
    assert service.get_run(tenant_id="tenant-beta", run_id=completed.run_id) is None
    assert worker.run_once() is None


def test_worker_schedules_transient_failure_with_bounded_backoff(
    tmp_path: Path, monkeypatch: object
) -> None:
    from evalforge import registry as registry_module
    from evalforge import worker as worker_module
    from evalforge.contracts import EvaluationSuite
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService
    from evalforge.worker import EvaluationWorker

    registry = SQLiteRunRegistry(tmp_path / "retry-worker.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "worker-transient-retry",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    queued = service.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"policy": "Allowed"},
        idempotency_key="worker-transient-retry-0001",
    )
    timestamps = iter(
        (
            "2027-01-01T00:00:00.000000Z",
            "2027-01-01T00:00:01.000000Z",
            "2027-01-01T00:00:05.000000Z",
            "2027-01-01T00:00:11.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))  # type: ignore[attr-defined]

    def raise_transient_failure(**_kwargs: object) -> object:
        raise RuntimeError("database password=do-not-persist")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        worker_module,
        "_evaluation_report_payload",
        raise_transient_failure,
    )
    worker = EvaluationWorker(
        registry,
        worker_id="worker-a",
        lease_seconds=30,
        retry_backoff_seconds=10,
    )

    retrying = worker.run_once()

    assert retrying is not None
    assert retrying.job_id == queued.job_id
    assert retrying.status == "queued"
    assert retrying.attempts == 1
    assert retrying.failure_code is None
    assert retrying.available_at == "2027-01-01T00:00:11.000000Z"
    assert registry.count_runs(tenant_id="tenant-alpha") == 0
    assert worker.run_once() is None
    reclaimed = registry.claim_evaluation_job(worker_id="worker-b", lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed.job_id == queued.job_id
    assert reclaimed.attempts == 2


def test_worker_transient_retries_double_then_fail_at_attempt_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge import registry as registry_module
    from evalforge import worker as worker_module
    from evalforge.contracts import EvaluationSuite
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService
    from evalforge.worker import EvaluationWorker

    registry = SQLiteRunRegistry(tmp_path / "exponential-worker.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "worker-exponential-retry",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    service.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"policy": "Allowed"},
        idempotency_key="worker-exponential-retry-0001",
    )
    timestamps = iter(
        (
            "2099-01-01T00:00:00.000000Z",
            "2099-01-01T00:00:01.000000Z",
            "2099-01-01T00:00:11.000000Z",
            "2099-01-01T00:00:12.000000Z",
            "2099-01-01T00:00:32.000000Z",
            "2099-01-01T00:00:33.000000Z",
            "2099-01-01T00:00:34.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))

    def raise_transient_failure(**_kwargs: object) -> object:
        raise RuntimeError("transient evaluation failure")

    monkeypatch.setattr(worker_module, "_evaluation_report_payload", raise_transient_failure)
    worker = EvaluationWorker(
        registry,
        worker_id="worker-a",
        lease_seconds=30,
        retry_backoff_seconds=10,
    )

    first = worker.run_once()
    assert first is not None and first.available_at == "2099-01-01T00:00:11.000000Z"
    second = worker.run_once()
    assert second is not None and second.available_at == "2099-01-01T00:00:32.000000Z"
    terminal = worker.run_once()
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.attempts == terminal.max_attempts == 3
    assert terminal.failure_code == "attempt_limit_exhausted"
    assert terminal.available_at is None
    assert worker.run_once() is None


def test_worker_transient_retry_backoff_caps_at_one_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge import registry as registry_module
    from evalforge import worker as worker_module
    from evalforge.contracts import EvaluationSuite
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService
    from evalforge.worker import EvaluationWorker

    registry = SQLiteRunRegistry(tmp_path / "capped-worker.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "worker-capped-retry",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    service.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"policy": "Allowed"},
        idempotency_key="worker-capped-retry-0001",
    )
    timestamps = iter(
        (
            "2099-01-01T00:00:00.000000Z",
            "2099-01-01T00:00:01.000000Z",
            "2099-01-01T00:33:21.000000Z",
            "2099-01-01T00:33:22.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))

    def raise_transient_failure(**_kwargs: object) -> object:
        raise RuntimeError("transient evaluation failure")

    monkeypatch.setattr(worker_module, "_evaluation_report_payload", raise_transient_failure)
    worker = EvaluationWorker(
        registry,
        worker_id="worker-a",
        lease_seconds=30,
        retry_backoff_seconds=2_000,
    )

    first = worker.run_once()
    assert first is not None and first.available_at == "2099-01-01T00:33:21.000000Z"
    second = worker.run_once()
    assert second is not None and second.available_at == "2099-01-01T01:33:22.000000Z"


def test_retry_persistence_failure_drops_original_exception_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge import registry as registry_module
    from evalforge import worker as worker_module
    from evalforge.contracts import EvaluationSuite
    from evalforge.registry import EvaluationJobLeaseNotFoundError, SQLiteRunRegistry
    from evalforge.service import EvalForgeService
    from evalforge.worker import EvaluationWorker

    registry = SQLiteRunRegistry(tmp_path / "redacted-retry-failure.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "worker-redacted-retry-failure",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    queued = service.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"policy": "Allowed"},
        idempotency_key="worker-redacted-retry-failure-0001",
    )
    timestamps = iter(
        (
            "2099-01-01T00:00:00.000000Z",
            "2099-01-01T00:00:31.000000Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))
    sensitive_value = "database password=retry-probe-secret"

    def raise_sensitive_failure(**_kwargs: object) -> object:
        raise RuntimeError(sensitive_value)

    monkeypatch.setattr(worker_module, "_evaluation_report_payload", raise_sensitive_failure)
    worker = EvaluationWorker(registry, worker_id="worker-a", lease_seconds=30)

    with pytest.raises(EvaluationJobLeaseNotFoundError) as rejected:
        worker.run_once()

    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None
    assert sensitive_value not in _exception_evidence(rejected.value)
    current = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)
    assert current is not None and current.status == "running"


def test_worker_rejects_invalid_retry_backoff_before_claiming_work(tmp_path: Path) -> None:
    import pytest

    from evalforge.contracts import EvaluationSuite
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService
    from evalforge.worker import EvaluationWorker

    registry = SQLiteRunRegistry(tmp_path / "invalid-retry-worker.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "invalid-worker-backoff",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    queued = service.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"policy": "Allowed"},
        idempotency_key="worker-invalid-backoff-0001",
    )

    with pytest.raises(
        ValueError,
        match="retry backoff must be an integer from 1 through 3600 seconds",
    ):
        EvaluationWorker(
            registry,
            worker_id="worker-a",
            lease_seconds=30,
            retry_backoff_seconds=0,
        )

    current = registry.get_evaluation_job(tenant_id="tenant-alpha", job_id=queued.job_id)
    assert current is not None
    assert current.status == "queued"
    assert current.attempts == 0


def test_worker_terminally_fails_invalid_candidate_without_retry_or_run_publication(
    tmp_path: Path,
) -> None:
    from evalforge.contracts import EvaluationSuite
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService
    from evalforge.worker import EvaluationWorker

    registry = SQLiteRunRegistry(tmp_path / "invalid-worker.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "worker-invalid-input",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    queued = service.create_evaluation_job(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"unexpected": "output"},
        idempotency_key="worker-invalid-input-0001",
    )
    worker = EvaluationWorker(registry, worker_id="worker-a", lease_seconds=30)

    failed = worker.run_once()

    assert failed is not None
    assert failed.job_id == queued.job_id
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.run_id is None
    assert failed.failure_code == "invalid_evaluation"
    assert registry.count_runs(tenant_id="tenant-alpha") == 0
    assert worker.run_once() is None
