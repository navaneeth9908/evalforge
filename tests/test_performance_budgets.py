from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_resource_budgets_reject_vacuous_explicit_null_limits() -> None:
    from evalforge.contracts import ResourceBudgets

    for payload in ({}, {"max_total_latency_ms": None}):
        with pytest.raises(ValidationError, match="at least one"):
            ResourceBudgets.model_validate(payload)


def test_measurements_and_percentiles_are_strict_safe_integers() -> None:
    import math

    from evalforge.contracts import CandidateOutput, LatencyPercentileBudget

    for invalid_measurement in (
        -1,
        True,
        "1",
        1.5,
        math.nan,
        math.inf,
        9_007_199_254_740_992,
    ):
        with pytest.raises(ValidationError):
            CandidateOutput(
                output="yes",
                latency_ms=invalid_measurement,  # type: ignore[arg-type]
                cost_micro_usd=0,
            )
    for invalid_percentile in (0, 101, True, 1.5):
        with pytest.raises(ValidationError):
            LatencyPercentileBudget(
                percentile=invalid_percentile,  # type: ignore[arg-type]
                max_latency_ms=1,
            )


def test_partial_per_case_measurements_fail_closed() -> None:
    from evalforge.contracts import CandidateOutput, EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(case_id="measured", prompt="Answer", expected_output="yes"),
        EvaluationCase(case_id="missing", prompt="Answer", expected_output="yes"),
    ]

    with pytest.raises(ValueError, match="every case or no cases"):
        evaluate_suite(
            cases,
            {
                "measured": CandidateOutput(output="yes", latency_ms=1, cost_micro_usd=1),
                "missing": "yes",
            },
            policy=ReleasePolicy(minimum_pass_rate=1.0),
        )


def test_latency_and_cost_budgets_emit_deterministic_integer_evidence() -> None:
    from evalforge.contracts import (
        CandidateOutput,
        CostPercentileBudget,
        EvaluationCase,
        LatencyPercentileBudget,
        ReleasePolicy,
        ResourceBudgets,
    )
    from evalforge.engine import evaluate_suite
    from evalforge.provenance import EVALUATION_SEMANTICS_VERSION

    cases = [
        EvaluationCase(case_id="a", prompt="A", expected_output="yes"),
        EvaluationCase(case_id="b", prompt="B", expected_output="yes"),
        EvaluationCase(case_id="c", prompt="C", expected_output="yes"),
        EvaluationCase(case_id="d", prompt="D", expected_output="yes"),
    ]
    outputs = {
        "a": CandidateOutput(output="yes", latency_ms=10, cost_micro_usd=100),
        "b": CandidateOutput(output="yes", latency_ms=20, cost_micro_usd=200),
        "c": CandidateOutput(output="yes", latency_ms=30, cost_micro_usd=300),
        "d": CandidateOutput(output="yes", latency_ms=100, cost_micro_usd=900),
    }
    policy = ReleasePolicy(
        minimum_pass_rate=1.0,
        resource_budgets=ResourceBudgets(
            max_total_latency_ms=159,
            max_average_latency_ms=40,
            latency_percentile=LatencyPercentileBudget(percentile=75, max_latency_ms=29),
            max_total_cost_micro_usd=1_500,
            max_average_cost_micro_usd=374,
            cost_percentile=CostPercentileBudget(percentile=75, max_cost_micro_usd=300),
        ),
    )

    report = evaluate_suite(cases, outputs, policy=policy)

    assert report.schema_version == 5
    assert EVALUATION_SEMANTICS_VERSION.startswith("text-metrics-policy-v3-unicode-")
    assert report.performance is not None
    assert report.performance.model_dump() == {
        "case_count": 4,
        "total_latency_ms": 160,
        "average_latency_ms": 40,
        "latency_percentile": {"percentile": 75, "observed_latency_ms": 30},
        "total_cost_micro_usd": 1_500,
        "average_cost_micro_usd": 375,
        "cost_percentile": {"percentile": 75, "observed_cost_micro_usd": 300},
    }
    assert [result.performance.model_dump() for result in report.results if result.performance] == [
        {"latency_ms": 10, "cost_micro_usd": 100},
        {"latency_ms": 20, "cost_micro_usd": 200},
        {"latency_ms": 30, "cost_micro_usd": 300},
        {"latency_ms": 100, "cost_micro_usd": 900},
    ]
    assert [failure.model_dump() for failure in report.resource_gate_failures] == [
        {
            "metric": "total_latency_ms",
            "observed": 160,
            "required": 159,
            "percentile": None,
        },
        {
            "metric": "percentile_latency_ms",
            "observed": 30,
            "required": 29,
            "percentile": 75,
        },
        {
            "metric": "average_cost_micro_usd",
            "observed": 375,
            "required": 374,
            "percentile": None,
        },
    ]
    assert report.release_ready is False


def test_complete_evidence_is_aggregated_without_resource_budgets() -> None:
    from evalforge.contracts import CandidateOutput, EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(case_id="a", prompt="A", expected_output="yes"),
        EvaluationCase(case_id="b", prompt="B", expected_output="yes"),
    ]
    report = evaluate_suite(
        cases,
        {
            "a": CandidateOutput(output="yes", latency_ms=1, cost_micro_usd=2),
            "b": CandidateOutput(output="yes", latency_ms=2, cost_micro_usd=3),
        },
        policy=ReleasePolicy(minimum_pass_rate=1.0),
    )

    assert report.performance is not None
    assert report.performance.model_dump() == {
        "case_count": 2,
        "total_latency_ms": 3,
        "average_latency_ms": 2,
        "latency_percentile": None,
        "total_cost_micro_usd": 5,
        "average_cost_micro_usd": 3,
        "cost_percentile": None,
    }
    assert report.resource_gate_failures == ()
    assert report.release_ready is True


def test_aggregate_resource_evidence_must_remain_json_safe() -> None:
    from evalforge.contracts import CandidateOutput, EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    maximum = 9_007_199_254_740_991
    cases = [
        EvaluationCase(case_id="a", prompt="A", expected_output="yes"),
        EvaluationCase(case_id="b", prompt="B", expected_output="yes"),
    ]

    with pytest.raises(ValueError, match="aggregate performance evidence exceeds"):
        evaluate_suite(
            cases,
            {
                "a": CandidateOutput(output="yes", latency_ms=maximum, cost_micro_usd=maximum),
                "b": CandidateOutput(output="yes", latency_ms=maximum, cost_micro_usd=maximum),
            },
            policy=ReleasePolicy(minimum_pass_rate=1.0),
        )


def test_inclusive_percentile_boundaries_and_complete_failure_order() -> None:
    from evalforge.contracts import (
        CandidateOutput,
        CostPercentileBudget,
        EvaluationCase,
        LatencyPercentileBudget,
        ReleasePolicy,
        ResourceBudgets,
    )
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(case_id="a", prompt="A", expected_output="yes"),
        EvaluationCase(case_id="b", prompt="B", expected_output="yes"),
    ]
    outputs = {
        "a": CandidateOutput(output="yes", latency_ms=1, cost_micro_usd=1),
        "b": CandidateOutput(output="yes", latency_ms=2, cost_micro_usd=2),
    }
    exact_policy = ReleasePolicy(
        minimum_pass_rate=1.0,
        resource_budgets=ResourceBudgets(
            max_total_latency_ms=3,
            max_average_latency_ms=2,
            latency_percentile=LatencyPercentileBudget(percentile=100, max_latency_ms=2),
            max_total_cost_micro_usd=3,
            max_average_cost_micro_usd=2,
            cost_percentile=CostPercentileBudget(percentile=1, max_cost_micro_usd=1),
        ),
    )

    exact_report = evaluate_suite(cases, outputs, policy=exact_policy)

    assert exact_report.resource_gate_failures == ()
    assert exact_report.release_ready is True

    failing_policy = ReleasePolicy(
        minimum_pass_rate=1.0,
        resource_budgets=ResourceBudgets(
            max_total_latency_ms=0,
            max_average_latency_ms=0,
            latency_percentile=LatencyPercentileBudget(percentile=100, max_latency_ms=0),
            max_total_cost_micro_usd=0,
            max_average_cost_micro_usd=0,
            cost_percentile=CostPercentileBudget(percentile=1, max_cost_micro_usd=0),
        ),
    )
    failing_report = evaluate_suite(cases, outputs, policy=failing_policy)

    assert [failure.metric for failure in failing_report.resource_gate_failures] == [
        "total_latency_ms",
        "average_latency_ms",
        "percentile_latency_ms",
        "total_cost_micro_usd",
        "average_cost_micro_usd",
        "percentile_cost_micro_usd",
    ]
    assert failing_report.release_ready is False
