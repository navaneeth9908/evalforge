from __future__ import annotations


def test_comparison_reports_case_metric_matrix_and_ablation_deltas() -> None:
    from evalforge.comparison import compare_evaluations
    from evalforge.contracts import ComparisonPolicy, EvaluationCase, ReleasePolicy

    cases = [
        EvaluationCase(case_id="exact-stable", prompt="Stable", expected_output="yes"),
        EvaluationCase(case_id="exact-regressed", prompt="Regressed", expected_output="yes"),
        EvaluationCase(
            case_id="contains-improved",
            prompt="Improved",
            expected_output="needle",
            metric="contains",
        ),
    ]

    report = compare_evaluations(
        cases,
        baseline_outputs={
            "exact-stable": "yes",
            "exact-regressed": "yes",
            "contains-improved": "missing",
        },
        candidate_outputs={
            "exact-stable": "yes",
            "exact-regressed": "no",
            "contains-improved": "found needle",
        },
        evaluation_policy=ReleasePolicy(minimum_pass_rate=0.0),
        comparison_policy=ComparisonPolicy(
            schema_version=1,
            max_absolute_regression=0.25,
            max_relative_regression=0.40,
        ),
        baseline_label="baseline-v1",
        candidate_label="candidate-v2",
    )

    assert report.schema_version == 1
    assert [delta.status for delta in report.case_deltas] == [
        "unchanged",
        "regression",
        "improvement",
    ]
    assert [delta.absolute_delta for delta in report.case_deltas] == [0.0, -1.0, 1.0]
    assert [delta.metric for delta in report.metric_deltas] == ["exact", "contains"]
    exact_delta = report.metric_deltas[0]
    assert exact_delta.baseline_score == 1.0
    assert exact_delta.candidate_score == 0.5
    assert exact_delta.absolute_delta == -0.5
    assert exact_delta.relative_delta == -0.5
    assert [failure.scope for failure in report.budget_failures] == ["metric"]
    assert report.budget_failures[0].metric == "exact"
    assert report.release_ready is False
    assert report.ablation_summary.model_dump() == {
        "improved_cases": 1,
        "regressed_cases": 1,
        "unchanged_cases": 1,
    }
    assert [(row.role, row.label) for row in report.model_matrix] == [
        ("baseline", "baseline-v1"),
        ("candidate", "candidate-v2"),
    ]
    assert [metric.metric for metric in report.model_matrix[0].metrics] == ["exact", "contains"]


def test_zero_baseline_has_explicit_relative_delta_and_cannot_regress() -> None:
    from evalforge.comparison import compare_evaluations
    from evalforge.contracts import ComparisonPolicy, EvaluationCase, ReleasePolicy

    case = EvaluationCase(case_id="new-capability", prompt="Answer", expected_output="yes")
    report = compare_evaluations(
        [case],
        baseline_outputs={"new-capability": "no"},
        candidate_outputs={"new-capability": "yes"},
        evaluation_policy=ReleasePolicy(minimum_pass_rate=0.0),
        comparison_policy=ComparisonPolicy(
            schema_version=1,
            max_absolute_regression=0.0,
            max_relative_regression=0.0,
        ),
    )

    assert report.case_deltas[0].baseline_score == 0.0
    assert report.case_deltas[0].absolute_delta == 1.0
    assert report.case_deltas[0].relative_delta is None
    assert report.metric_deltas[0].relative_delta is None
    assert report.pass_rate_relative_delta is None
    assert report.budget_failures == ()
    assert report.release_ready is True


def test_absolute_and_relative_budgets_are_independent_and_inclusive() -> None:
    from evalforge.comparison import compare_evaluations
    from evalforge.contracts import ComparisonPolicy, EvaluationCase, ReleasePolicy

    cases = [
        EvaluationCase(case_id=str(index), prompt="Answer", expected_output="yes")
        for index in range(4)
    ]
    baseline = {str(index): "yes" for index in range(4)}
    candidate = {"0": "yes", "1": "yes", "2": "yes", "3": "no"}
    evaluation_policy = ReleasePolicy(minimum_pass_rate=0.0)

    accepted = compare_evaluations(
        cases,
        baseline,
        candidate,
        evaluation_policy=evaluation_policy,
        comparison_policy=ComparisonPolicy(
            schema_version=1,
            max_absolute_regression=0.25,
            max_relative_regression=0.25,
        ),
    )
    blocked = compare_evaluations(
        cases,
        baseline,
        candidate,
        evaluation_policy=evaluation_policy,
        comparison_policy=ComparisonPolicy(
            schema_version=1,
            max_absolute_regression=0.20,
            max_relative_regression=0.20,
        ),
    )

    assert accepted.release_ready is True
    assert accepted.budget_failures == ()
    assert blocked.release_ready is False
    assert [(failure.scope, failure.reasons) for failure in blocked.budget_failures] == [
        ("overall", ("absolute", "relative")),
        ("metric", ("absolute", "relative")),
    ]
