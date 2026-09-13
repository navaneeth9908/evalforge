"""Application service layer shared by HTTP adapters and deterministic core logic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from evalforge.contracts import CandidateOutput, EvaluationSuite
from evalforge.engine import evaluate_suite
from evalforge.provenance import (
    CANONICALIZATION_VERSION,
    EVALUATION_SEMANTICS_VERSION,
    JsonValue,
    canonical_json_sha256,
    deterministic_run_id,
)
from evalforge.registry import (
    RunSummary,
    SQLiteRunRegistry,
    StoredRun,
    StoredSuite,
)


class SuiteNotFoundError(LookupError):
    """A requested suite digest is not present in local storage."""


class EvalForgeService:
    """Coordinate typed contracts, core evaluation, and local persistence."""

    def __init__(self, registry: SQLiteRunRegistry) -> None:
        self.registry = registry

    def create_suite(self, suite: EvaluationSuite) -> StoredSuite:
        """Persist one already-validated evaluation suite."""

        payload = cast(JsonValue, suite.model_dump(mode="json", exclude_none=True))
        return self.registry.create_suite(name=suite.name, suite=payload)

    def list_suites(self, *, limit: int, offset: int) -> tuple[StoredSuite, ...]:
        """Page through stored suites."""

        return self.registry.list_suites(limit=limit, offset=offset)

    def evaluate(
        self,
        *,
        suite_id: str,
        candidate_outputs: Mapping[str, str | CandidateOutput],
    ) -> StoredRun:
        """Evaluate with the deterministic core and atomically persist its evidence."""

        stored_suite = self.registry.get_suite(suite_id)
        if stored_suite is None:
            raise SuiteNotFoundError("evaluation suite was not found")
        suite = EvaluationSuite.model_validate(stored_suite.suite)
        report = evaluate_suite(
            suite.cases,
            candidate_outputs,
            policy=suite.resolved_policy,
        )
        candidate_payload = cast(
            JsonValue,
            {
                case_id: (
                    output.model_dump(mode="json")
                    if isinstance(output, CandidateOutput)
                    else output
                )
                for case_id, output in candidate_outputs.items()
            },
        )
        candidate_sha256 = canonical_json_sha256(candidate_payload)
        run_id = deterministic_run_id(
            suite_sha256=suite_id,
            candidate_sha256=candidate_sha256,
            dataset_manifest_sha256=None,
        )
        report_payload = cast(
            JsonValue,
            {
                "suite_name": suite.name,
                **report.model_dump(mode="json"),
                "schema_version": 5,
                "suite_sha256": suite_id,
                "candidate_sha256": candidate_sha256,
                "dataset_manifest_sha256": None,
                "evaluation_semantics_version": EVALUATION_SEMANTICS_VERSION,
                "canonicalization_version": CANONICALIZATION_VERSION,
                "run_id": run_id,
            },
        )
        return self.registry.create_run(
            suite_name=suite.name,
            suite=stored_suite.suite,
            candidate=candidate_payload,
            report=report_payload,
        )

    def list_runs(self, *, limit: int, offset: int) -> tuple[RunSummary, ...]:
        """Page through compact run history."""

        return self.registry.list_runs(limit=limit, offset=offset)

    def get_run(self, run_id: str) -> StoredRun | None:
        """Load one run and its complete immutable evidence."""

        return self.registry.get_run(run_id)
