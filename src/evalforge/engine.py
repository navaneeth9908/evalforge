"""Deterministic evaluation engine."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from typing import Literal

from evalforge.contracts import (
    CaseResult,
    EvaluationCase,
    EvaluationReport,
    GateFailure,
    MetricEvidence,
    MetricName,
    ReleasePolicy,
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


def evaluate_suite(
    cases: Sequence[EvaluationCase],
    candidate_outputs: Mapping[str, str],
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

    results_list: list[CaseResult] = []
    for case in cases:
        actual_output = candidate_outputs[case.case_id]
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
                evidence=MetricEvidence(
                    normalization=normalization,
                    normalized_expected=normalized_expected,
                    normalized_actual=normalized_actual,
                ),
                expected_output=case.expected_output,
                actual_output=actual_output,
            )
        )
    results = tuple(results_list)
    passed_cases = sum(result.passed for result in results)
    pass_rate = passed_cases / len(results)
    release_ready = pass_rate >= effective_minimum_pass_rate
    gate_failures = (
        ()
        if release_ready
        else (
            GateFailure(
                code="minimum_pass_rate_not_met",
                observed=pass_rate,
                required=effective_minimum_pass_rate,
            ),
        )
    )
    return EvaluationReport(
        total_cases=len(results),
        passed_cases=passed_cases,
        pass_rate=pass_rate,
        minimum_pass_rate=effective_minimum_pass_rate,
        release_ready=release_ready,
        gate_failures=gate_failures,
        results=results,
    )
