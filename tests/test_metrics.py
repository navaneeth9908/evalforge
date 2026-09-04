from __future__ import annotations


def test_contains_metric_reports_normalized_substring_evidence() -> None:
    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite

    case = EvaluationCase(
        case_id="refund-policy",
        prompt="Summarize the refund policy",
        expected_output="Refund: 30 days!",
        metric="contains",
    )

    report = evaluate_suite(
        [case],
        {"refund-policy": "  Your REFUND: 30 DAYS! applies.  "},
        minimum_pass_rate=1.0,
    )

    result = report.results[0]
    assert result.metric == "contains"
    assert result.score == 1.0
    assert result.passed is True
    assert result.threshold == 1.0
    assert result.evidence.normalization == "strip_casefold_v1"
    assert result.evidence.normalized_expected == "refund: 30 days!"
    assert result.evidence.normalized_actual == "your refund: 30 days! applies."


def test_regex_metric_accepts_safe_patterns_and_rejects_unbounded_ones() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite

    case = EvaluationCase(
        case_id="ticket",
        prompt="Return a ticket ID",
        expected_output=r"^ticket-[0-9][0-9][0-9]$",
        metric="regex",
    )
    report = evaluate_suite([case], {"ticket": " TICKET-042 "}, minimum_pass_rate=1.0)

    assert report.results[0].passed is True
    assert report.results[0].metric == "regex"
    with pytest.raises(ValidationError, match="bounded regex"):
        EvaluationCase(
            case_id="unsafe",
            prompt="Unsafe pattern",
            expected_output=r"^(a+)+$",
            metric="regex",
        )


def test_regex_metric_preserves_uppercase_escape_semantics() -> None:
    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite

    examples = (
        (r"^CODE-\D$", " code-X "),
        (r"^DONE\Z", " done "),
    )
    for index, (pattern, actual) in enumerate(examples):
        case = EvaluationCase(
            case_id=f"escape-{index}",
            prompt="Match a bounded pattern",
            expected_output=pattern,
            metric="regex",
        )

        result = evaluate_suite(
            [case],
            {case.case_id: actual},
            minimum_pass_rate=1.0,
        ).results[0]

        assert result.passed is True
        assert result.evidence.normalization == "strip_regex_ignorecase_v1"
        assert result.evidence.normalized_expected == pattern
        assert result.evidence.normalized_actual == actual.strip()


def test_contains_metric_rejects_empty_normalized_expectations() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import EvaluationCase

    for expected_output in ("", " \t\n "):
        with pytest.raises(ValidationError, match="non-empty expected output"):
            EvaluationCase(
                case_id="empty-contains",
                prompt="Find a meaningful substring",
                expected_output=expected_output,
                metric="contains",
            )


def test_metric_registry_is_explicit_and_exact_remains_the_default() -> None:
    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite, registered_metrics

    case = EvaluationCase(case_id="legacy", prompt="Legacy", expected_output="YES")
    report = evaluate_suite([case], {"legacy": " yes "}, minimum_pass_rate=1.0)

    assert registered_metrics() == ("exact", "contains", "regex")
    assert case.metric == "exact"
    assert report.results[0].passed is True
    assert report.results[0].threshold_source == "legacy"
