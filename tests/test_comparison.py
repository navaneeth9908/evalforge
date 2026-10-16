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

    assert report.schema_version == 3
    assert report.baseline.schema_version == 5
    assert report.candidate.schema_version == 5
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
    assert report.weighted_pass_rate_relative_delta is None
    assert report.budget_failures == ()
    assert report.release_ready is True


def test_weighted_regressions_drive_overall_metric_and_matrix_evidence() -> None:
    from evalforge.comparison import compare_evaluations
    from evalforge.contracts import ComparisonPolicy, EvaluationCase, ReleasePolicy

    cases = [
        EvaluationCase(
            case_id="release-critical",
            prompt="Critical",
            expected_output="yes",
            weight=100,
            severity="high",
            metric="exact",
        ),
        EvaluationCase(
            case_id="minor",
            prompt="Minor",
            expected_output="yes",
            weight=1,
            metric="exact",
        ),
    ]

    report = compare_evaluations(
        cases,
        baseline_outputs={"release-critical": "yes", "minor": "no"},
        candidate_outputs={"release-critical": "no", "minor": "yes"},
        evaluation_policy=ReleasePolicy(minimum_pass_rate=0.0),
        comparison_policy=ComparisonPolicy(
            schema_version=1,
            max_absolute_regression=0.0,
            max_relative_regression=0.0,
        ),
    )

    assert report.baseline.pass_rate == report.candidate.pass_rate == 0.5
    assert report.baseline.weighted_pass_rate == 100 / 101
    assert report.candidate.weighted_pass_rate == 1 / 101
    assert report.weighted_pass_rate_absolute_delta == -99 / 101
    assert report.weighted_pass_rate_relative_delta == -0.99
    assert [failure.scope for failure in report.budget_failures] == ["overall", "metric"]
    assert report.metric_deltas[0].baseline_score == 100 / 101
    assert report.metric_deltas[0].candidate_score == 1 / 101
    assert [row.weighted_pass_rate for row in report.model_matrix] == [100 / 101, 1 / 101]
    assert report.release_ready is False


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


def test_persisted_report_comparison_projects_redacted_score_and_slice_deltas() -> None:
    from evalforge.comparison import compare_report_scores
    from evalforge.contracts import EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(
            case_id="critical-policy",
            prompt="State the private policy.",
            expected_output="Allowed",
            weight=3,
            category="policy",
            tags=("critical-path",),
        ),
        EvaluationCase(
            case_id="supporting-context",
            prompt="Include the private context.",
            expected_output="context",
            metric="contains",
            category="quality",
            tags=("context",),
        ),
    ]
    policy = ReleasePolicy(minimum_pass_rate=0.0)
    baseline = evaluate_suite(
        cases,
        {"critical-policy": "Allowed", "supporting-context": "missing"},
        policy=policy,
    )
    candidate = evaluate_suite(
        cases,
        {"critical-policy": "Denied", "supporting-context": "found context"},
        policy=policy,
    )

    comparison = compare_report_scores(baseline, candidate)

    assert comparison.weighted_pass_rate_absolute_delta == -0.5
    assert comparison.weighted_pass_rate_relative_delta == -2 / 3
    assert [delta.status for delta in comparison.case_deltas] == [
        "regression",
        "improvement",
    ]
    assert [delta.metric for delta in comparison.metric_deltas] == ["exact", "contains"]
    assert [(delta.name, delta.absolute_delta) for delta in comparison.category_deltas] == [
        ("policy", -1.0),
        ("quality", 1.0),
    ]
    assert [(delta.name, delta.absolute_delta) for delta in comparison.tag_deltas] == [
        ("context", 1.0),
        ("critical-path", -1.0),
    ]
    assert comparison.ablation_summary.model_dump() == {
        "improved_cases": 1,
        "regressed_cases": 1,
        "unchanged_cases": 0,
    }
    assert "private" not in comparison.model_dump_json()


def test_persisted_report_comparison_rejects_misaligned_case_evidence() -> None:
    import pytest

    from evalforge.comparison import compare_report_scores
    from evalforge.contracts import EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    baseline = evaluate_suite(
        [EvaluationCase(case_id="first", prompt="Private", expected_output="yes")],
        {"first": "yes"},
        policy=ReleasePolicy(minimum_pass_rate=0.0),
    )
    candidate = evaluate_suite(
        [EvaluationCase(case_id="second", prompt="Private", expected_output="yes")],
        {"second": "yes"},
        policy=ReleasePolicy(minimum_pass_rate=0.0),
    )

    with pytest.raises(ValueError, match="aligned"):
        compare_report_scores(baseline, candidate)


def test_persisted_report_validation_rejects_forged_derived_evidence(
    tmp_path: object,
) -> None:
    from dataclasses import replace
    from pathlib import Path
    from typing import cast

    import pytest

    from evalforge.contracts import EvaluationSuite
    from evalforge.provenance import JsonValue, canonical_json_sha256
    from evalforge.registry import SQLiteRunRegistry
    from evalforge.service import EvalForgeService, _validated_persisted_report

    registry = SQLiteRunRegistry(Path(cast(Path, tmp_path)) / "forged-report.db")
    service = EvalForgeService(registry)
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "stored-report-integrity",
            "minimum_pass_rate": 0.0,
            "cases": [
                {
                    "case_id": "case",
                    "prompt": "Private prompt",
                    "expected_output": "yes",
                }
            ],
        }
    )
    stored_suite = service.create_suite(suite, tenant_id="tenant-alpha")
    stored = service.evaluate(
        tenant_id="tenant-alpha",
        suite_id=stored_suite.suite_id,
        candidate_outputs={"case": "yes"},
    )
    assert isinstance(stored.report, dict)

    forged_reports = (
        {**stored.report, "weighted_pass_rate": 0.0},
        {
            **stored.report,
            "evaluation_semantics_version": "forged-semantics",
            "canonicalization_version": "forged-canonicalization",
        },
    )
    for forged_report in forged_reports:
        forged_payload = cast(JsonValue, forged_report)
        forged = replace(
            stored,
            report=forged_payload,
            report_sha256=canonical_json_sha256(forged_payload),
        )
        with pytest.raises(RuntimeError, match="derived evidence validation"):
            _validated_persisted_report(forged)
