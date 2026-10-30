from pathlib import Path


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
