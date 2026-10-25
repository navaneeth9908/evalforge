"""Application service layer shared by HTTP adapters and deterministic core logic."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from evalforge.comparison import compare_report_scores
from evalforge.contracts import (
    CandidateOutput,
    EvaluationReport,
    EvaluationSuite,
    MetricName,
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
    EvaluationJobConflictError as RegistryEvaluationJobConflictError,
)
from evalforge.registry import (
    EvaluationJobSuiteNotFoundError,
    RunSummary,
    SQLiteRunRegistry,
    StoredEvaluationJob,
    StoredRun,
    StoredSuite,
)

_SLICE_FILTER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class SuiteNotFoundError(LookupError):
    """A requested suite digest is not present in local storage."""


class RunComparisonNotFoundError(LookupError):
    """One or both requested runs are not visible to the tenant."""


class IncompatibleRunComparisonError(ValueError):
    """Two visible runs do not share one evaluation contract."""


class RunTrendNotFoundError(LookupError):
    """The trend anchor is not visible to the tenant."""


class RunTrendFilterNotFoundError(LookupError):
    """A requested metric or slice is absent from the visible evaluation contract."""


class InvalidRunTrendFilterError(ValueError):
    """A trend request selected more than one mutually exclusive dimension."""


class CaseEvidenceNotFoundError(LookupError):
    """The requested run or case evidence is not visible to the tenant."""


class EvaluationJobConflictError(ValueError):
    """An idempotency key is already bound to another evaluation request."""


class EvaluationJobNotFoundError(LookupError):
    """The requested evaluation job is not visible to the tenant."""


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
    score: float


@dataclass(frozen=True)
class StoredRunTrend:
    """Bounded chronological history for one immutable evaluation contract."""

    suite_name: str
    anchor_run_id: str
    filter_kind: Literal["overall", "metric", "category", "tag"]
    filter_value: str | None
    points: tuple[StoredRunTrendPoint, ...]


@dataclass(frozen=True)
class StoredCaseEvidence:
    """Bounded privacy-safe evidence for one case in an immutable stored run."""

    suite_name: str
    run: RunSummary
    case_id: str
    passed: bool
    score: float
    metric: str
    threshold: float
    threshold_source: str
    weight: float
    severity: str
    category: str
    tags: tuple[str, ...]
    latency_ms: int | None
    cost_micro_usd: int | None


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


def _metric_weighted_mean(report: EvaluationReport, metric: MetricName) -> float:
    results = tuple(result for result in report.results if result.metric == metric)
    if not results:
        raise ValueError("trend metric is not represented by the evaluation suite")
    total_weight = sum(result.weight for result in results)
    return sum(result.score * result.weight for result in results) / total_weight


def _slice_weighted_pass_rate(
    report: EvaluationReport,
    *,
    kind: Literal["category", "tag"],
    name: str,
) -> float:
    slices = report.category_slices if kind == "category" else report.tag_slices
    match = next((item for item in slices if item.name == name), None)
    if match is None:
        raise ValueError("trend slice is not represented by the evaluation suite")
    return match.weighted_pass_rate


def _trend_score(
    report: EvaluationReport,
    *,
    metric: MetricName | None,
    category: str | None,
    tag: str | None,
) -> float:
    if metric is not None:
        return _metric_weighted_mean(report, metric)
    if category is not None:
        return _slice_weighted_pass_rate(report, kind="category", name=category)
    if tag is not None:
        return _slice_weighted_pass_rate(report, kind="tag", name=tag)
    return report.weighted_pass_rate


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

    def create_evaluation_job(
        self,
        *,
        tenant_id: str,
        suite_id: str,
        candidate_outputs: Mapping[str, str | CandidateOutput],
        idempotency_key: str,
    ) -> StoredEvaluationJob:
        """Persist an idempotent evaluation request without executing it inline."""

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
        try:
            return self.registry.create_evaluation_job(
                tenant_id=tenant_id,
                suite_id=suite_id,
                candidate=candidate_payload,
                idempotency_key=idempotency_key,
            )
        except EvaluationJobSuiteNotFoundError as exc:
            raise SuiteNotFoundError("evaluation suite was not found") from exc
        except RegistryEvaluationJobConflictError as exc:
            raise EvaluationJobConflictError(
                "idempotency key is already bound to another evaluation request"
            ) from exc

    def get_evaluation_job(self, *, tenant_id: str, job_id: str) -> StoredEvaluationJob:
        """Return one tenant-visible evaluation job or a non-disclosing failure."""

        stored = self.registry.get_evaluation_job(tenant_id=tenant_id, job_id=job_id)
        if stored is None:
            raise EvaluationJobNotFoundError("evaluation job was not found")
        return stored

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
        metric: tuple[str, ...] = (),
        category: tuple[str, ...] = (),
        tag: tuple[str, ...] = (),
        limit: int = 20,
    ) -> StoredRunTrend:
        """Derive bounded validated history from a tenant-visible anchor run."""

        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("trend limit must be an integer from 1 through 20")
        anchor = self.registry.get_run(tenant_id=tenant_id, run_id=anchor_run_id)
        if anchor is None:
            raise RunTrendNotFoundError("run trend was not found")
        anchor_report = _validated_persisted_report(anchor)

        filter_groups = (metric, category, tag)
        if any(len(values) > 1 for values in filter_groups) or sum(map(bool, filter_groups)) > 1:
            raise InvalidRunTrendFilterError("trend filter is invalid or ambiguous")
        raw_metric = metric[0] if metric else None
        raw_category = category[0] if category else None
        raw_tag = tag[0] if tag else None
        if raw_metric is not None and raw_metric not in {"exact", "contains", "regex"}:
            raise InvalidRunTrendFilterError("trend filter is invalid or ambiguous")
        if any(
            value is not None and _SLICE_FILTER_PATTERN.fullmatch(value) is None
            for value in (raw_category, raw_tag)
        ):
            raise InvalidRunTrendFilterError("trend filter is invalid or ambiguous")
        selected_metric = cast(MetricName | None, raw_metric)
        try:
            _trend_score(
                anchor_report,
                metric=selected_metric,
                category=raw_category,
                tag=raw_tag,
            )
        except ValueError as exc:
            raise RunTrendFilterNotFoundError("trend filter was not found") from exc

        stored_runs = self.registry.list_compatible_runs(
            tenant_id=tenant_id,
            suite_sha256=anchor.suite_sha256,
            dataset_manifest_sha256=anchor.dataset_manifest_sha256,
            limit=limit,
        )
        filter_kind: Literal["overall", "metric", "category", "tag"] = "overall"
        filter_value: str | None = None
        if selected_metric is not None:
            filter_kind, filter_value = "metric", selected_metric
        elif raw_category is not None:
            filter_kind, filter_value = "category", raw_category
        elif raw_tag is not None:
            filter_kind, filter_value = "tag", raw_tag
        points: list[StoredRunTrendPoint] = []
        for stored in reversed(stored_runs):
            report = _validated_persisted_report(stored)
            if (
                report.evaluation_semantics_version != anchor_report.evaluation_semantics_version
                or report.canonicalization_version != anchor_report.canonicalization_version
            ):
                raise RuntimeError("stored trend history failed compatibility validation")
            try:
                score = _trend_score(
                    report,
                    metric=selected_metric,
                    category=raw_category,
                    tag=raw_tag,
                )
            except ValueError as exc:
                raise RuntimeError("stored trend history failed dimension validation") from exc
            points.append(
                StoredRunTrendPoint(
                    run=stored.summary(),
                    score=score,
                )
            )
        return StoredRunTrend(
            suite_name=anchor.suite_name,
            anchor_run_id=anchor.run_id,
            filter_kind=filter_kind,
            filter_value=filter_value,
            points=tuple(points),
        )

    def get_case_evidence(
        self,
        *,
        tenant_id: str,
        run_id: str,
        case_id: str,
    ) -> StoredCaseEvidence:
        """Return a redacted case projection from one tenant-visible validated run."""

        stored = self.registry.get_run(tenant_id=tenant_id, run_id=run_id)
        if stored is None:
            raise CaseEvidenceNotFoundError("case evidence was not found")
        report = _validated_persisted_report(stored)
        result = next((item for item in report.results if item.case_id == case_id), None)
        if result is None:
            raise CaseEvidenceNotFoundError("case evidence was not found")
        return StoredCaseEvidence(
            suite_name=stored.suite_name,
            run=stored.summary(),
            case_id=result.case_id,
            passed=result.passed,
            score=result.score,
            metric=result.metric,
            threshold=result.threshold,
            threshold_source=result.threshold_source,
            weight=result.weight,
            severity=result.severity,
            category=result.category,
            tags=result.tags,
            latency_ms=result.performance.latency_ms if result.performance is not None else None,
            cost_micro_usd=(
                result.performance.cost_micro_usd if result.performance is not None else None
            ),
        )
