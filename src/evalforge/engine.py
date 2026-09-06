"""Deterministic evaluation engine."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from typing import Literal

from evalforge.contracts import (
    MAX_SAFE_JSON_INTEGER,
    CandidateOutput,
    CasePerformance,
    CaseResult,
    EvaluationCase,
    EvaluationReport,
    GateFailure,
    MetricEvidence,
    MetricName,
    PercentileCostEvidence,
    PercentileLatencyEvidence,
    PerformanceSummary,
    ReleasePolicy,
    ResourceBudgets,
    ResourceGateFailure,
    ResourceGateMetric,
    SliceSummary,
    ThresholdSource,
)


def _normalized(value: str) -> str:
    return value.strip().casefold()


def _exact_score(expected: str, actual: str) -> float:
    return float(actual == expected)


def _contains_score(expected: str, actual: str) -> float:
    return float(expected in actual)


def _regex_score(expected: str, actual: str) -> float:
    return float(re.fullmatch(expected, actual, flags=re.IGNORECASE) is not None)


_METRIC_REGISTRY: dict[MetricName, Callable[[str, str], float]] = {
    "exact": _exact_score,
    "contains": _contains_score,
    "regex": _regex_score,
}


def registered_metrics() -> tuple[MetricName, ...]:
    """Return deterministic metric names in stable registration order."""
    return tuple(_METRIC_REGISTRY)


def _summarize_slices(
    results: tuple[CaseResult, ...], dimension: Literal["category", "tag"]
) -> tuple[SliceSummary, ...]:
    if dimension == "category":
        names = sorted({result.category for result in results})
    else:
        names = sorted({tag for result in results for tag in result.tags})

    summaries: list[SliceSummary] = []
    for name in names:
        members = tuple(
            result
            for result in results
            if (result.category == name if dimension == "category" else name in result.tags)
        )
        total_weight = sum(result.weight for result in members)
        passed_weight = sum(result.weight for result in members if result.passed)
        summaries.append(
            SliceSummary(
                name=name,
                total_cases=len(members),
                passed_cases=sum(result.passed for result in members),
                total_weight=total_weight,
                passed_weight=passed_weight,
                weighted_pass_rate=passed_weight / total_weight,
            )
        )
    return tuple(summaries)


def _nearest_rank(values: list[int], percentile: int) -> int:
    rank = (percentile * len(values) + 99) // 100
    return sorted(values)[rank - 1]


def _performance_summary(
    results: tuple[CaseResult, ...], budgets: ResourceBudgets | None
) -> tuple[PerformanceSummary, tuple[ResourceGateFailure, ...]]:
    performance = [result.performance for result in results]
    if any(item is None for item in performance):
        raise ValueError("resource budgets require latency and cost evidence for every case")
    measured = [item for item in performance if item is not None]
    case_count = len(measured)
    total_latency = sum(item.latency_ms for item in measured)
    total_cost = sum(item.cost_micro_usd for item in measured)
    if total_latency > MAX_SAFE_JSON_INTEGER or total_cost > MAX_SAFE_JSON_INTEGER:
        raise ValueError("aggregate performance evidence exceeds maximum JSON-safe integer")
    average_latency = (total_latency + case_count - 1) // case_count
    average_cost = (total_cost + case_count - 1) // case_count
    latency_budget = budgets.latency_percentile if budgets is not None else None
    cost_budget = budgets.cost_percentile if budgets is not None else None
    latency_percentile = (
        PercentileLatencyEvidence(
            percentile=latency_budget.percentile,
            observed_latency_ms=_nearest_rank(
                [item.latency_ms for item in measured],
                latency_budget.percentile,
            ),
        )
        if latency_budget is not None
        else None
    )
    cost_percentile = (
        PercentileCostEvidence(
            percentile=cost_budget.percentile,
            observed_cost_micro_usd=_nearest_rank(
                [item.cost_micro_usd for item in measured],
                cost_budget.percentile,
            ),
        )
        if cost_budget is not None
        else None
    )
    summary = PerformanceSummary(
        case_count=case_count,
        total_latency_ms=total_latency,
        average_latency_ms=average_latency,
        latency_percentile=latency_percentile,
        total_cost_micro_usd=total_cost,
        average_cost_micro_usd=average_cost,
        cost_percentile=cost_percentile,
    )
    if budgets is None:
        return summary, ()
    checks: tuple[tuple[ResourceGateMetric, int, int | None, int | None], ...] = (
        ("total_latency_ms", total_latency, budgets.max_total_latency_ms, None),
        ("average_latency_ms", average_latency, budgets.max_average_latency_ms, None),
        (
            "percentile_latency_ms",
            latency_percentile.observed_latency_ms if latency_percentile else 0,
            budgets.latency_percentile.max_latency_ms if budgets.latency_percentile else None,
            budgets.latency_percentile.percentile if budgets.latency_percentile else None,
        ),
        ("total_cost_micro_usd", total_cost, budgets.max_total_cost_micro_usd, None),
        ("average_cost_micro_usd", average_cost, budgets.max_average_cost_micro_usd, None),
        (
            "percentile_cost_micro_usd",
            cost_percentile.observed_cost_micro_usd if cost_percentile else 0,
            budgets.cost_percentile.max_cost_micro_usd if budgets.cost_percentile else None,
            budgets.cost_percentile.percentile if budgets.cost_percentile else None,
        ),
    )
    failures = tuple(
        ResourceGateFailure(
            metric=metric,
            observed=observed,
            required=required,
            percentile=percentile,
        )
        for metric, observed, required, percentile in checks
        if required is not None and observed > required
    )
    return summary, failures


def evaluate_suite(
    cases: Sequence[EvaluationCase],
    candidate_outputs: Mapping[str, str | CandidateOutput],
    *,
    minimum_pass_rate: float | None = None,
    policy: ReleasePolicy | None = None,
) -> EvaluationReport:
    """Evaluate ordered cases with deterministic metrics and threshold precedence."""
    if not cases:
        raise ValueError("evaluation suite must contain at least one case")
    if policy is not None and minimum_pass_rate is not None:
        raise ValueError("provide either policy or minimum_pass_rate, not both")
    effective_minimum_pass_rate = (
        policy.minimum_pass_rate if policy is not None else minimum_pass_rate
    )
    if (
        effective_minimum_pass_rate is None
        or type(effective_minimum_pass_rate) not in (int, float)
        or not isfinite(effective_minimum_pass_rate)
        or not 0.0 <= effective_minimum_pass_rate <= 1.0
    ):
        raise ValueError("minimum_pass_rate must be finite and between 0 and 1")

    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("evaluation case IDs must be unique")
    if set(candidate_outputs) != set(case_ids):
        raise ValueError("candidate output IDs must exactly match evaluation case IDs")
    measured_case_count = sum(
        isinstance(candidate_output, CandidateOutput)
        for candidate_output in candidate_outputs.values()
    )
    if measured_case_count not in (0, len(cases)):
        raise ValueError("performance evidence is required for every case or no cases")
    resource_budgets = policy.resource_budgets if policy is not None else None
    if resource_budgets is not None and measured_case_count == 0:
        raise ValueError("resource budgets require latency and cost evidence for every case")

    results_list: list[CaseResult] = []
    for case in cases:
        candidate_output = candidate_outputs[case.case_id]
        actual_output = (
            candidate_output.output
            if isinstance(candidate_output, CandidateOutput)
            else candidate_output
        )
        performance = (
            CasePerformance(
                latency_ms=candidate_output.latency_ms,
                cost_micro_usd=candidate_output.cost_micro_usd,
            )
            if isinstance(candidate_output, CandidateOutput)
            else None
        )
        normalization: Literal["strip_casefold_v1", "strip_regex_ignorecase_v1"]
        if case.metric == "regex":
            normalized_expected = case.expected_output.strip()
            normalized_actual = actual_output.strip()
            normalization = "strip_regex_ignorecase_v1"
        else:
            normalized_expected = _normalized(case.expected_output)
            normalized_actual = _normalized(actual_output)
            normalization = "strip_casefold_v1"
        score = _METRIC_REGISTRY[case.metric](normalized_expected, normalized_actual)

        threshold_source: ThresholdSource
        if case.threshold is not None:
            threshold = case.threshold
            threshold_source = "case"
        elif policy is not None and case.metric in policy.metric_thresholds:
            threshold = policy.metric_thresholds[case.metric]
            threshold_source = "metric"
        elif policy is not None:
            threshold = policy.default_case_threshold
            threshold_source = "suite"
        else:
            threshold = 1.0
            threshold_source = "legacy"

        passed = score >= threshold
        results_list.append(
            CaseResult(
                case_id=case.case_id,
                passed=passed,
                score=score,
                metric=case.metric,
                threshold=threshold,
                threshold_source=threshold_source,
                weight=case.weight,
                severity=case.severity,
                category=case.category,
                tags=case.tags,
                evidence=MetricEvidence(
                    normalization=normalization,
                    normalized_expected=normalized_expected,
                    normalized_actual=normalized_actual,
                ),
                expected_output=case.expected_output,
                actual_output=actual_output,
                performance=performance,
            )
        )
    results = tuple(results_list)
    passed_cases = sum(result.passed for result in results)
    pass_rate = passed_cases / len(results)
    total_weight = sum(result.weight for result in results)
    passed_weight = sum(result.weight for result in results if result.passed)
    weighted_pass_rate = passed_weight / total_weight
    blocking_severities = policy.blocking_severities if policy is not None else ("critical",)
    has_blocking_failure = any(
        not result.passed and result.severity in blocking_severities for result in results
    )
    gate_failures_list: list[GateFailure] = []
    if weighted_pass_rate < effective_minimum_pass_rate:
        gate_failures_list.append(
            GateFailure(
                code="minimum_pass_rate_not_met",
                observed=weighted_pass_rate,
                required=effective_minimum_pass_rate,
            )
        )
    if has_blocking_failure:
        gate_failures_list.append(
            GateFailure(
                code="release_critical_case_failed",
                observed=0.0,
                required=1.0,
            )
        )
    gate_failures = tuple(gate_failures_list)
    performance_summary = None
    resource_gate_failures: tuple[ResourceGateFailure, ...] = ()
    if measured_case_count == len(cases):
        performance_summary, resource_gate_failures = _performance_summary(
            results, resource_budgets
        )
    return EvaluationReport(
        total_cases=len(results),
        passed_cases=passed_cases,
        pass_rate=pass_rate,
        total_weight=total_weight,
        passed_weight=passed_weight,
        weighted_pass_rate=weighted_pass_rate,
        minimum_pass_rate=effective_minimum_pass_rate,
        release_ready=not gate_failures and not resource_gate_failures,
        gate_failures=gate_failures,
        performance=performance_summary,
        resource_gate_failures=resource_gate_failures,
        category_slices=_summarize_slices(results, "category"),
        tag_slices=_summarize_slices(results, "tag"),
        results=results,
    )
