from __future__ import annotations


def test_threshold_precedence_is_case_then_metric_then_suite() -> None:
    from evalforge.contracts import EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    cases = [
        EvaluationCase(
            case_id="case-override",
            prompt="Case override",
            expected_output="expected",
            metric="exact",
            threshold=0.0,
        ),
        EvaluationCase(
            case_id="metric-override",
            prompt="Metric override",
            expected_output="needle",
            metric="contains",
        ),
        EvaluationCase(
            case_id="suite-default",
            prompt="Suite default",
            expected_output="^allowed$",
            metric="regex",
        ),
    ]
    policy = ReleasePolicy(
        minimum_pass_rate=2 / 3,
        default_case_threshold=1.0,
        metric_thresholds={"exact": 1.0, "contains": 0.0},
    )

    report = evaluate_suite(
        cases,
        {
            "case-override": "different",
            "metric-override": "absent",
            "suite-default": "denied",
        },
        policy=policy,
    )

    assert [
        (result.passed, result.threshold, result.threshold_source) for result in report.results
    ] == [
        (True, 0.0, "case"),
        (True, 0.0, "metric"),
        (False, 1.0, "suite"),
    ]
    assert report.pass_rate == 2 / 3
    assert report.release_ready is True
    assert report.gate_failures == ()


def test_failed_aggregate_gate_reports_a_machine_readable_reason() -> None:
    from evalforge.contracts import EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    report = evaluate_suite(
        [
            EvaluationCase(case_id="passing", prompt="Pass", expected_output="yes"),
            EvaluationCase(case_id="failing", prompt="Fail", expected_output="yes"),
        ],
        {"passing": "yes", "failing": "no"},
        policy=ReleasePolicy(minimum_pass_rate=0.75),
    )

    assert report.release_ready is False
    assert [failure.model_dump() for failure in report.gate_failures] == [
        {
            "code": "minimum_pass_rate_not_met",
            "observed": 0.5,
            "required": 0.75,
        }
    ]


def test_suite_accepts_declarative_policy_and_legacy_threshold_contracts() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import EvaluationSuite

    case = {"case_id": "policy", "prompt": "State it", "expected_output": "allowed"}
    declarative = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "declarative",
            "release_policy": {
                "minimum_pass_rate": 0.8,
                "default_case_threshold": 0.5,
                "metric_thresholds": {"contains": 0.75},
            },
            "cases": [case],
        }
    )
    legacy = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "legacy",
            "minimum_pass_rate": 1.0,
            "cases": [case],
        }
    )

    assert declarative.resolved_policy.minimum_pass_rate == 0.8
    assert declarative.resolved_policy.metric_thresholds == {"contains": 0.75}
    assert legacy.resolved_policy.model_dump() == {
        "minimum_pass_rate": 1.0,
        "default_case_threshold": 1.0,
        "metric_thresholds": {},
        "blocking_severities": ("critical",),
        "resource_budgets": None,
    }
    for ambiguous in (
        {"schema_version": 1, "name": "missing", "cases": [case]},
        {
            "schema_version": 1,
            "name": "duplicate",
            "minimum_pass_rate": 1.0,
            "release_policy": {"minimum_pass_rate": 1.0},
            "cases": [case],
        },
    ):
        with pytest.raises(ValidationError, match="exactly one"):
            EvaluationSuite.model_validate(ambiguous)


def test_suite_rejects_explicit_null_policy_fields() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import EvaluationSuite

    case = {"case_id": "policy", "prompt": "State it", "expected_output": "allowed"}
    invalid_policy_fields: tuple[dict[str, object], ...] = (
        {
            "minimum_pass_rate": None,
            "release_policy": {"minimum_pass_rate": 1.0},
        },
        {"minimum_pass_rate": 1.0, "release_policy": None},
        {"minimum_pass_rate": None},
        {"release_policy": None},
    )
    for policy_fields in invalid_policy_fields:
        with pytest.raises(ValidationError, match="exactly one"):
            EvaluationSuite.model_validate(
                {
                    "schema_version": 1,
                    "name": "explicit-null",
                    **policy_fields,
                    "cases": [case],
                }
            )


def test_cli_applies_declarative_policy_and_serializes_gate_failures(tmp_path: object) -> None:
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
                "name": "policy-cli",
                "release_policy": {
                    "minimum_pass_rate": 1.0,
                    "default_case_threshold": 0.0,
                    "metric_thresholds": {"exact": 0.5},
                },
                "cases": [
                    {
                        "case_id": "strict-case",
                        "prompt": "State it",
                        "expected_output": "allowed",
                        "threshold": 1.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text('{"strict-case":"denied"}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 1, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 5
    assert report["results"][0]["threshold_source"] == "case"
    assert report["gate_failures"] == [
        {"code": "minimum_pass_rate_not_met", "observed": 0.0, "required": 1.0}
    ]


def test_threshold_contracts_are_strict_finite_and_closed_range() -> None:
    import math

    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import EvaluationCase, ReleasePolicy
    from evalforge.engine import evaluate_suite

    assert ReleasePolicy(
        minimum_pass_rate=0,
        default_case_threshold=1,
        metric_thresholds={"exact": 0, "contains": 1},
    ).model_dump() == {
        "minimum_pass_rate": 0.0,
        "default_case_threshold": 1.0,
        "metric_thresholds": {"exact": 0.0, "contains": 1.0},
        "blocking_severities": ("critical",),
        "resource_budgets": None,
    }
    invalid_thresholds: tuple[object, ...] = (
        True,
        "0.5",
        -0.01,
        1.01,
        math.nan,
        math.inf,
        -math.inf,
    )
    for invalid in invalid_thresholds:
        with pytest.raises(ValidationError):
            ReleasePolicy(minimum_pass_rate=invalid)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            ReleasePolicy(
                minimum_pass_rate=1.0,
                default_case_threshold=invalid,  # type: ignore[arg-type]
            )
        with pytest.raises(ValidationError):
            ReleasePolicy(
                minimum_pass_rate=1.0,
                metric_thresholds={"exact": invalid},  # type: ignore[dict-item]
            )
        with pytest.raises(ValidationError):
            EvaluationCase(
                case_id="strict",
                prompt="Strict threshold",
                expected_output="yes",
                threshold=invalid,  # type: ignore[arg-type]
            )

    case = EvaluationCase(case_id="runtime", prompt="Runtime", expected_output="yes")
    with pytest.raises(ValueError, match="minimum_pass_rate"):
        evaluate_suite([case], {"runtime": "yes"}, minimum_pass_rate=True)
