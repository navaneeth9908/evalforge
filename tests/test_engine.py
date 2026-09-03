from __future__ import annotations

import pytest


def test_exact_match_suite_returns_case_evidence_and_release_decision() -> None:
    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(
            case_id="refund-policy",
            prompt="How long is the refund window?",
            expected_output="30 days",
        ),
        EvaluationCase(
            case_id="identity-policy",
            prompt="Can I share an account?",
            expected_output="No",
        ),
    ]
    candidate_outputs = {
        "refund-policy": " 30 DAYS ",
        "identity-policy": "Yes",
    }

    report = evaluate_suite(cases, candidate_outputs, minimum_pass_rate=0.75)

    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.pass_rate == 0.5
    assert report.release_ready is False
    assert [result.case_id for result in report.results] == [
        "refund-policy",
        "identity-policy",
    ]
    assert report.results[0].passed is True
    assert report.results[0].score == 1.0
    assert report.results[1].passed is False
    assert report.results[1].score == 0.0


def test_suite_rejects_ambiguous_or_incomplete_evidence() -> None:
    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite

    case = EvaluationCase(case_id="policy", prompt="State the policy", expected_output="Allowed")

    with pytest.raises(ValueError, match="at least one"):
        evaluate_suite([], {}, minimum_pass_rate=1.0)
    with pytest.raises(ValueError, match="unique"):
        evaluate_suite([case, case], {"policy": "Allowed"}, minimum_pass_rate=1.0)
    with pytest.raises(ValueError, match="exactly match"):
        evaluate_suite([case], {}, minimum_pass_rate=1.0)
    with pytest.raises(ValueError, match="exactly match"):
        evaluate_suite(
            [case],
            {"policy": "Allowed", "unregistered": "Ignored?"},
            minimum_pass_rate=1.0,
        )


@pytest.mark.parametrize("minimum_pass_rate", [-0.01, 1.01, float("nan"), float("inf")])
def test_suite_rejects_invalid_release_threshold(minimum_pass_rate: float) -> None:
    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite

    case = EvaluationCase(case_id="policy", prompt="State the policy", expected_output="Allowed")

    with pytest.raises(ValueError, match="minimum_pass_rate"):
        evaluate_suite([case], {"policy": "Allowed"}, minimum_pass_rate=minimum_pass_rate)
