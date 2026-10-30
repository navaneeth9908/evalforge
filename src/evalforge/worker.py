"""Bounded worker execution for durable evaluation jobs."""

from __future__ import annotations

from dataclasses import dataclass

from evalforge.registry import SQLiteRunRegistry, StoredEvaluationJob
from evalforge.service import _evaluation_report_payload


@dataclass(frozen=True)
class EvaluationWorker:
    """Claim and execute at most one deterministic evaluation job per invocation."""

    registry: SQLiteRunRegistry
    worker_id: str
    lease_seconds: int = 60

    def run_once(self) -> StoredEvaluationJob | None:
        """Complete one available job, or return ``None`` when the queue is empty."""

        claimed = self.registry.claim_evaluation_job(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            return None
        try:
            _suite_name, report = _evaluation_report_payload(
                suite_id=claimed.suite_id,
                suite_payload=claimed.suite,
                candidate_payload=claimed.candidate,
            )
        except ValueError:
            return self.registry.fail_evaluation_job(
                job_id=claimed.job_id,
                worker_id=self.worker_id,
                lease_token=claimed.lease_token,
                failure_code="invalid_evaluation",
            )
        return self.registry.complete_evaluation_job(
            job_id=claimed.job_id,
            worker_id=self.worker_id,
            lease_token=claimed.lease_token,
            report=report,
        )
