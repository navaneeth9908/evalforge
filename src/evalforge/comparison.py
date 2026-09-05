"""Deterministic baseline-versus-candidate comparison."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from evalforge.contracts import (
    AblationSummary,
    BudgetReason,
    BudgetScope,
    CaseComparison,
    ComparisonPolicy,
    ComparisonReport,
    ComparisonStatus,
    EvaluationCase,
    EvaluationReport,
    MatrixMetric,
    MatrixRole,
    MetricComparison,
    MetricName,
    ModelMatrixRow,
    RegressionBudgetFailure,
    ReleasePolicy,
)
from evalforge.engine import evaluate_suite, registered_metrics


def _relative_delta(baseline: float, candidate: float) -> float | None:
    if baseline == 0.0:
        return None
    return (candidate - baseline) / baseline


def _status(baseline: float, candidate: float) -> ComparisonStatus:
    if candidate > baseline:
        return "improvement"
    if candidate < baseline:
        return "regression"
    return "unchanged"


def _metric_means(report: EvaluationReport) -> dict[MetricName, float]:
    means: dict[MetricName, float] = {}
    for metric in registered_metrics():
        results = [result for result in report.results if result.metric == metric]
        if results:
            total_weight = sum(result.weight for result in results)
            means[metric] = sum(result.score * result.weight for result in results) / total_weight
    return means


def _budget_failure(
    *,
    scope: BudgetScope,
    metric: MetricName | None,
    baseline: float,
    candidate: float,
    policy: ComparisonPolicy,
) -> RegressionBudgetFailure | None:
    absolute_regression = max(baseline - candidate, 0.0)
    relative_regression = absolute_regression / baseline if baseline > 0.0 else 0.0
    reasons: list[BudgetReason] = []
    if absolute_regression > policy.max_absolute_regression:
        reasons.append("absolute")
    if relative_regression > policy.max_relative_regression:
        reasons.append("relative")
    if not reasons:
        return None
    return RegressionBudgetFailure(
        scope=scope,
        metric=metric,
        baseline_score=baseline,
        candidate_score=candidate,
        absolute_regression=absolute_regression,
        relative_regression=relative_regression,
        max_absolute_regression=policy.max_absolute_regression,
        max_relative_regression=policy.max_relative_regression,
        reasons=tuple(reasons),
    )


def compare_evaluations(
    cases: Sequence[EvaluationCase],
    baseline_outputs: Mapping[str, str],
    candidate_outputs: Mapping[str, str],
    *,
    evaluation_policy: ReleasePolicy,
    comparison_policy: ComparisonPolicy,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
) -> ComparisonReport:
    """Evaluate two variants and compare their score evidence under regression budgets."""
    baseline = evaluate_suite(cases, baseline_outputs, policy=evaluation_policy)
    candidate = evaluate_suite(cases, candidate_outputs, policy=evaluation_policy)

    case_deltas = tuple(
        CaseComparison(
            case_id=baseline_result.case_id,
            metric=baseline_result.metric,
            baseline_score=baseline_result.score,
            candidate_score=candidate_result.score,
            absolute_delta=candidate_result.score - baseline_result.score,
            relative_delta=_relative_delta(baseline_result.score, candidate_result.score),
            status=_status(baseline_result.score, candidate_result.score),
        )
        for baseline_result, candidate_result in zip(
            baseline.results, candidate.results, strict=True
        )
    )

    baseline_metrics = _metric_means(baseline)
    candidate_metrics = _metric_means(candidate)
    metric_deltas = tuple(
        MetricComparison(
            metric=metric,
            case_count=sum(result.metric == metric for result in baseline.results),
            baseline_score=baseline_metrics[metric],
            candidate_score=candidate_metrics[metric],
            absolute_delta=candidate_metrics[metric] - baseline_metrics[metric],
            relative_delta=_relative_delta(baseline_metrics[metric], candidate_metrics[metric]),
            status=_status(baseline_metrics[metric], candidate_metrics[metric]),
        )
        for metric in registered_metrics()
        if metric in baseline_metrics
    )

    failures: list[RegressionBudgetFailure] = []
    overall_failure = _budget_failure(
        scope="overall",
        metric=None,
        baseline=baseline.weighted_pass_rate,
        candidate=candidate.weighted_pass_rate,
        policy=comparison_policy,
    )
    if overall_failure is not None:
        failures.append(overall_failure)
    for delta in metric_deltas:
        failure = _budget_failure(
            scope="metric",
            metric=delta.metric,
            baseline=delta.baseline_score,
            candidate=delta.candidate_score,
            policy=comparison_policy,
        )
        if failure is not None:
            failures.append(failure)

    def matrix_row(role: MatrixRole, label: str, evaluation: EvaluationReport) -> ModelMatrixRow:
        metric_means = _metric_means(evaluation)
        return ModelMatrixRow(
            role=role,
            label=label,
            weighted_pass_rate=evaluation.weighted_pass_rate,
            metrics=tuple(
                MatrixMetric(metric=metric, score=metric_means[metric])
                for metric in registered_metrics()
                if metric in metric_means
            ),
        )

    return ComparisonReport(
        baseline_label=baseline_label,
        candidate_label=candidate_label,
        baseline=baseline,
        candidate=candidate,
        weighted_pass_rate_absolute_delta=(
            candidate.weighted_pass_rate - baseline.weighted_pass_rate
        ),
        weighted_pass_rate_relative_delta=_relative_delta(
            baseline.weighted_pass_rate, candidate.weighted_pass_rate
        ),
        case_deltas=case_deltas,
        metric_deltas=metric_deltas,
        model_matrix=(
            matrix_row("baseline", baseline_label, baseline),
            matrix_row("candidate", candidate_label, candidate),
        ),
        ablation_summary=AblationSummary(
            improved_cases=sum(delta.status == "improvement" for delta in case_deltas),
            regressed_cases=sum(delta.status == "regression" for delta in case_deltas),
            unchanged_cases=sum(delta.status == "unchanged" for delta in case_deltas),
        ),
        budget_failures=tuple(failures),
        release_ready=candidate.release_ready and not failures,
    )
