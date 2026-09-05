from __future__ import annotations

import math

import pytest
from pydantic import ValidationError


def test_case_quality_dimensions_have_strict_defaults_and_weights() -> None:
    from evalforge.contracts import EvaluationCase

    case = EvaluationCase(case_id="default", prompt="Default", expected_output="yes")

    assert case.weight == 1.0
    assert case.severity == "medium"
    assert case.category == "uncategorized"
    assert case.tags == ()

    configured = EvaluationCase(
        case_id="configured",
        prompt="Configured",
        expected_output="yes",
        weight=2,
        severity="critical",
        category="safety",
        tags=("policy", "refunds"),
    )
    assert configured.weight == 2.0
    assert configured.tags == ("policy", "refunds")

    for invalid_weight in (
        0,
        -1,
        True,
        "2",
        1_000_000.01,
        math.nan,
        math.inf,
        -math.inf,
    ):
        with pytest.raises(ValidationError):
            EvaluationCase(
                case_id="invalid-weight",
                prompt="Invalid",
                expected_output="yes",
                weight=invalid_weight,  # type: ignore[arg-type]
            )

    with pytest.raises(ValidationError):
        EvaluationCase(
            case_id="invalid-severity",
            prompt="Invalid",
            expected_output="yes",
            severity="urgent",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        EvaluationCase(
            case_id="duplicate-tags",
            prompt="Invalid",
            expected_output="yes",
            tags=("policy", "policy"),
        )


def test_weighted_aggregation_and_critical_failures_control_release() -> None:
    from evalforge.contracts import EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(
            case_id="critical-failure",
            prompt="Critical",
            expected_output="safe",
            weight=1,
            severity="critical",
            category="safety",
            tags=("policy",),
        ),
        EvaluationCase(
            case_id="heavy-pass",
            prompt="Heavy",
            expected_output="yes",
            weight=9,
            severity="high",
            category="quality",
            tags=("core",),
        ),
    ]

    report = evaluate_suite(
        cases,
        {"critical-failure": "unsafe", "heavy-pass": "yes"},
        policy=ReleasePolicy(minimum_pass_rate=0.5),
    )

    assert report.pass_rate == 0.5
    assert report.schema_version == 4
    assert report.total_weight == 10.0
    assert report.passed_weight == 9.0
    assert report.weighted_pass_rate == 0.9
    assert report.release_ready is False
    assert [failure.code for failure in report.gate_failures] == ["release_critical_case_failed"]
    assert report.results[0].weight == 1.0
    assert report.results[0].severity == "critical"
    assert report.results[0].category == "safety"
    assert report.results[0].tags == ("policy",)

    weighted_failure = evaluate_suite(
        [
            EvaluationCase(case_id="light-pass", prompt="Light", expected_output="yes", weight=1),
            EvaluationCase(case_id="heavy-fail", prompt="Heavy", expected_output="yes", weight=3),
        ],
        {"light-pass": "yes", "heavy-fail": "no"},
        policy=ReleasePolicy(minimum_pass_rate=0.5),
    )
    assert weighted_failure.pass_rate == 0.5
    assert weighted_failure.weighted_pass_rate == 0.25
    assert [failure.model_dump() for failure in weighted_failure.gate_failures] == [
        {
            "code": "minimum_pass_rate_not_met",
            "observed": 0.25,
            "required": 0.5,
        }
    ]


def test_category_and_tag_slices_are_weighted_and_deterministically_sorted() -> None:
    from evalforge.contracts import EvaluationCase
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(
            case_id="quality-pass",
            prompt="Quality",
            expected_output="yes",
            weight=2,
            category="quality",
            tags=("shared", "core"),
        ),
        EvaluationCase(
            case_id="safety-fail",
            prompt="Safety",
            expected_output="yes",
            weight=1,
            category="safety",
            tags=("policy", "shared"),
        ),
        EvaluationCase(
            case_id="quality-policy-pass",
            prompt="Quality policy",
            expected_output="yes",
            weight=3,
            category="quality",
            tags=("policy",),
        ),
    ]

    report = evaluate_suite(
        cases,
        {
            "quality-pass": "yes",
            "safety-fail": "no",
            "quality-policy-pass": "yes",
        },
        minimum_pass_rate=0.0,
    )

    assert [summary.name for summary in report.category_slices] == ["quality", "safety"]
    assert [summary.model_dump() for summary in report.category_slices] == [
        {
            "name": "quality",
            "total_cases": 2,
            "passed_cases": 2,
            "total_weight": 5.0,
            "passed_weight": 5.0,
            "weighted_pass_rate": 1.0,
        },
        {
            "name": "safety",
            "total_cases": 1,
            "passed_cases": 0,
            "total_weight": 1.0,
            "passed_weight": 0.0,
            "weighted_pass_rate": 0.0,
        },
    ]
    assert [summary.name for summary in report.tag_slices] == ["core", "policy", "shared"]
    policy = report.tag_slices[1]
    assert policy.total_cases == 2
    assert policy.total_weight == 4.0
    assert policy.passed_weight == 3.0
    assert policy.weighted_pass_rate == 0.75


def test_cli_serializes_versioned_weighted_slice_report(tmp_path: object) -> None:
    import json
    from pathlib import Path

    from typer.testing import CliRunner

    from evalforge.cli import app

    directory = Path(str(tmp_path))
    suite_path = directory / "suite.json"
    outputs_path = directory / "outputs.json"
    report_path = directory / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "weighted-cli",
                "release_policy": {"minimum_pass_rate": 0.5},
                "cases": [
                    {
                        "case_id": "important",
                        "prompt": "Important",
                        "expected_output": "yes",
                        "weight": 3,
                        "severity": "high",
                        "category": "quality",
                        "tags": ["core"],
                    },
                    {
                        "case_id": "minor",
                        "prompt": "Minor",
                        "expected_output": "yes",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text('{"important":"yes","minor":"no"}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["evaluate", str(suite_path), str(outputs_path), "--report-path", str(report_path)],
    )

    assert result.exit_code == 0, result.output
    assert "Weighted pass rate: 75.00%" in result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 4
    assert report["weighted_pass_rate"] == 0.75
    assert [item["name"] for item in report["category_slices"]] == [
        "quality",
        "uncategorized",
    ]
    assert report["results"][0]["severity"] == "high"
