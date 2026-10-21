"""Application service layer shared by HTTP adapters and deterministic core logic."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from evalforge.comparison import compare_report_scores
from evalforge.contracts import (
    CandidateOutput,
    EvaluationReport,
    EvaluationSuite,
    PersistedEvaluationReport,
    ReportScoreComparison,
)
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


class RunComparisonNotFoundError(LookupError):
    """One or both requested runs are not visible to the tenant."""


class IncompatibleRunComparisonError(ValueError):
    """Two visible runs do not share one evaluation contract."""


class RunTrendNotFoundError(LookupError):
    """The trend anchor is not visible to the tenant."""


@dataclass(frozen=True)
class StoredRunComparison:
    """Privacy-safe descriptive analytics over two immutable stored runs."""

    suite_name: str
    baseline: RunSummary
    candidate: RunSummary
    baseline_weighted_pass_rate: float
    candidate_weighted_pass_rate: float
    scores: ReportScoreComparison


@dataclass(frozen=True)
class StoredRunTrendPoint:
    """One validated, redacted point in recorded unique-run history."""

    run: RunSummary
    weighted_pass_rate: float


@dataclass(frozen=True)
class StoredRunTrend:
    """Bounded chronological history for one immutable evaluation contract."""

    suite_name: str
    anchor_run_id: str
    points: tuple[StoredRunTrendPoint, ...]


def _validated_persisted_report(stored: StoredRun) -> PersistedEvaluationReport:
    if (
        canonical_json_sha256(stored.report) != stored.report_sha256
        or canonical_json_sha256(stored.suite) != stored.suite_sha256
        or canonical_json_sha256(stored.candidate) != stored.candidate_sha256
    ):
        raise RuntimeError("stored evaluation report failed integrity validation")
    try:
        report = PersistedEvaluationReport.model_validate(stored.report)
        suite = EvaluationSuite.model_validate(stored.suite)
        if not isinstance(stored.candidate, dict):
            raise ValueError("stored candidate evidence must be an object")
        candidate_outputs = {
            case_id: (output if isinstance(output, str) else CandidateOutput.model_validate(output))
            for case_id, output in stored.candidate.items()
        }
        expected_report = evaluate_suite(
            suite.cases,
            candidate_outputs,
            policy=suite.resolved_policy,
        )
    except ValueError as exc:
        raise RuntimeError("stored evaluation report failed contract validation") from exc
    if (
        report.run_id != stored.run_id
        or report.suite_name != stored.suite_name
        or report.suite_name != suite.name
        or report.suite_sha256 != stored.suite_sha256
        or report.candidate_sha256 != stored.candidate_sha256
        or report.dataset_manifest_sha256 != stored.dataset_manifest_sha256
        or report.release_ready != stored.release_ready
    ):
        raise RuntimeError("stored evaluation report failed identity validation")
    expected_run_id = deterministic_run_id(
        suite_sha256=stored.suite_sha256,
        candidate_sha256=stored.candidate_sha256,
        dataset_manifest_sha256=stored.dataset_manifest_sha256,
    )
    persisted_evaluation = EvaluationReport.model_validate(
        report.model_dump(mode="python", include=set(EvaluationReport.model_fields))
    )
    if (
        report.run_id != expected_run_id
        or report.evaluation_semantics_version != EVALUATION_SEMANTICS_VERSION
        or report.canonicalization_version != CANONICALIZATION_VERSION
        or persisted_evaluation != expected_report
    ):
        raise RuntimeError("stored evaluation report failed derived evidence validation")
    return report


class EvalForgeService:
    """Coordinate typed contracts, core evaluation, and local persistence."""

    def __init__(self, registry: SQLiteRunRegistry) -> None:
        self.registry = registry

    def create_suite(self, suite: EvaluationSuite, *, tenant_id: str) -> StoredSuite:
        """Persist one already-validated evaluation suite."""

        payload = cast(JsonValue, suite.model_dump(mode="json", exclude_none=True))
        return self.registry.create_suite(tenant_id=tenant_id, name=suite.name, suite=payload)

    def list_suites(self, *, tenant_id: str, limit: int, offset: int) -> tuple[StoredSuite, ...]:
        """Page through stored suites."""

        return self.registry.list_suites(tenant_id=tenant_id, limit=limit, offset=offset)

    def count_suites(self, *, tenant_id: str) -> int:
        """Count suites visible to one tenant."""

        return self.registry.count_suites(tenant_id=tenant_id)

    def evaluate(
        self,
        *,
        tenant_id: str,
        suite_id: str,
        candidate_outputs: Mapping[str, str | CandidateOutput],
    ) -> StoredRun:
        """Evaluate with the deterministic core and atomically persist its evidence."""

        stored_suite = self.registry.get_suite(tenant_id=tenant_id, suite_id=suite_id)
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
            tenant_id=tenant_id,
            suite_name=suite.name,
            suite=stored_suite.suite,
            candidate=candidate_payload,
            report=report_payload,
        )

    def list_runs(
        self,
        *,
        tenant_id: str,
        limit: int,
        offset: int,
    ) -> tuple[RunSummary, ...]:
        """Page through compact run history."""

        return self.registry.list_runs(tenant_id=tenant_id, limit=limit, offset=offset)

    def get_run(self, *, tenant_id: str, run_id: str) -> StoredRun | None:
        """Load one run and its complete immutable evidence."""

        return self.registry.get_run(tenant_id=tenant_id, run_id=run_id)

    def compare_runs(
        self,
        *,
        tenant_id: str,
        baseline_run_id: str,
        candidate_run_id: str,
    ) -> StoredRunComparison:
        """Derive redacted analytics for two tenant-visible compatible runs."""

        baseline = self.registry.get_run(tenant_id=tenant_id, run_id=baseline_run_id)
        candidate = self.registry.get_run(tenant_id=tenant_id, run_id=candidate_run_id)
        if baseline is None or candidate is None:
            raise RunComparisonNotFoundError("run comparison was not found")
        baseline_report = _validated_persisted_report(baseline)
        candidate_report = _validated_persisted_report(candidate)
        if (
            baseline.suite_sha256 != candidate.suite_sha256
            or baseline.dataset_manifest_sha256 != candidate.dataset_manifest_sha256
            or baseline_report.evaluation_semantics_version
            != candidate_report.evaluation_semantics_version
            or baseline_report.canonicalization_version != candidate_report.canonicalization_version
        ):
            raise IncompatibleRunComparisonError("evaluation runs are not comparable")
        try:
            scores = compare_report_scores(baseline_report, candidate_report)
        except ValueError as exc:
            raise RuntimeError("stored evaluation reports failed alignment validation") from exc
        return StoredRunComparison(
            suite_name=baseline.suite_name,
            baseline=baseline.summary(),
            candidate=candidate.summary(),
            baseline_weighted_pass_rate=baseline_report.weighted_pass_rate,
            candidate_weighted_pass_rate=candidate_report.weighted_pass_rate,
            scores=scores,
        )

    def get_run_trend(
        self,
        *,
        tenant_id: str,
        anchor_run_id: str,
        limit: int = 20,
    ) -> StoredRunTrend:
        """Derive bounded validated history from a tenant-visible anchor run."""

        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("trend limit must be an integer from 1 through 20")
        anchor = self.registry.get_run(tenant_id=tenant_id, run_id=anchor_run_id)
        if anchor is None:
            raise RunTrendNotFoundError("run trend was not found")
        anchor_report = _validated_persisted_report(anchor)
        stored_runs = self.registry.list_compatible_runs(
            tenant_id=tenant_id,
            suite_sha256=anchor.suite_sha256,
            dataset_manifest_sha256=anchor.dataset_manifest_sha256,
            limit=limit,
        )
        points: list[StoredRunTrendPoint] = []
        for stored in reversed(stored_runs):
            report = _validated_persisted_report(stored)
            if (
                report.evaluation_semantics_version != anchor_report.evaluation_semantics_version
                or report.canonicalization_version != anchor_report.canonicalization_version
            ):
                raise RuntimeError("stored trend history failed compatibility validation")
            points.append(
                StoredRunTrendPoint(
                    run=stored.summary(),
                    weighted_pass_rate=report.weighted_pass_rate,
                )
            )
        return StoredRunTrend(
            suite_name=anchor.suite_name,
            anchor_run_id=anchor.run_id,
            points=tuple(points),
        )
