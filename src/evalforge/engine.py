"""Deterministic evaluation engine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite

from evalforge.contracts import CaseResult, EvaluationCase, EvaluationReport


def _normalized(value: str) -> str:
    return value.strip().casefold()


def evaluate_suite(
    cases: Sequence[EvaluationCase],
    candidate_outputs: Mapping[str, str],
    *,
    minimum_pass_rate: float,
) -> EvaluationReport:
    """Evaluate ordered cases with normalized exact matching."""
    if not cases:
        raise ValueError("evaluation suite must contain at least one case")
    if not isfinite(minimum_pass_rate) or not 0.0 <= minimum_pass_rate <= 1.0:
        raise ValueError("minimum_pass_rate must be finite and between 0 and 1")

    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("evaluation case IDs must be unique")
    if set(candidate_outputs) != set(case_ids):
        raise ValueError("candidate output IDs must exactly match evaluation case IDs")

    results_list: list[CaseResult] = []
    for case in cases:
        actual_output = candidate_outputs[case.case_id]
        passed = _normalized(actual_output) == _normalized(case.expected_output)
        results_list.append(
            CaseResult(
                case_id=case.case_id,
                passed=passed,
                score=float(passed),
                expected_output=case.expected_output,
                actual_output=actual_output,
            )
        )
    results = tuple(results_list)
    passed_cases = sum(result.passed for result in results)
    pass_rate = passed_cases / len(results)
    return EvaluationReport(
        total_cases=len(results),
        passed_cases=passed_cases,
        pass_rate=pass_rate,
        minimum_pass_rate=minimum_pass_rate,
        release_ready=pass_rate >= minimum_pass_rate,
        results=results,
    )
