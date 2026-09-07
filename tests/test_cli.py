from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


def test_evaluate_command_writes_auditable_release_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "support-policy-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "refund-policy",
                        "prompt": "How long is the refund window?",
                        "expected_output": "30 days",
                    },
                    {
                        "case_id": "account-sharing",
                        "prompt": "Can customers share accounts?",
                        "expected_output": "No",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(
        json.dumps({"refund-policy": "30 DAYS", "account-sharing": " no "}),
        encoding="utf-8",
    )

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

    assert result.exit_code == 0, result.output
    assert "Release gate: PASS" in result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["suite_name"] == "support-policy-smoke"
    assert report["release_ready"] is True
    assert report["passed_cases"] == 2
    assert report["total_cases"] == 2
    assert [case["case_id"] for case in report["results"]] == [
        "refund-policy",
        "account-sharing",
    ]


def test_evaluate_command_reports_invalid_json_without_a_traceback(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "invalid-output-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text("{not-json", encoding="utf-8")

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

    assert result.exit_code == 2
    assert "outputs file is not valid JSON" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


def test_evaluate_command_rejects_non_string_output_mapping(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "typed-output-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(json.dumps({"policy": 1}), encoding="utf-8")

    result = CliRunner().invoke(app, ["evaluate", str(suite_path), str(outputs_path)])

    assert result.exit_code == 2
    assert "outputs must be a JSON object mapping case IDs to strings" in result.output


def test_evaluate_command_writes_evidence_and_exits_one_when_gate_fails(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "release-gate-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(json.dumps({"policy": "Denied"}), encoding="utf-8")

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

    assert result.exit_code == 1
    assert "Release gate: FAIL" in result.output
    assert json.loads(report_path.read_text(encoding="utf-8"))["release_ready"] is False


def test_evaluate_command_rejects_duplicate_candidate_output_keys(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "duplicate-output-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(
        '{"policy": "Denied", "policy": "Allowed"}',
        encoding="utf-8",
    )

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

    assert result.exit_code == 2
    assert "outputs file is not valid JSON or is ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


@pytest.mark.parametrize(
    "suite_content",
    [
        "{not-json",
        json.dumps(
            {
                "schema_version": 1,
                "name": "extra-field-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
                "unexpected": True,
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "name": "duplicate-case-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    },
                    {
                        "case_id": "policy",
                        "prompt": "Repeat the policy",
                        "expected_output": "Allowed",
                    },
                ],
            }
        ),
    ],
)
def test_evaluate_command_reports_invalid_suite_without_a_traceback(
    tmp_path: Path,
    suite_content: str,
) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(suite_content, encoding="utf-8")
    outputs_path.write_text(json.dumps({"policy": "Allowed"}), encoding="utf-8")

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

    assert result.exit_code == 2
    assert "suite file is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


def test_evaluate_command_reports_mismatched_case_ids_without_a_traceback(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "missing-output-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text("{}", encoding="utf-8")

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

    assert result.exit_code == 2
    assert "evaluation input is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


@pytest.mark.parametrize(
    ("invalid_file", "expected_message"),
    [
        ("suite", "suite file is invalid or ambiguous"),
        ("outputs", "outputs file is not valid JSON or is ambiguous"),
    ],
)
def test_evaluate_command_rejects_invalid_utf8_without_a_traceback(
    tmp_path: Path,
    invalid_file: str,
    expected_message: str,
) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "encoding-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(json.dumps({"policy": "Allowed"}), encoding="utf-8")
    invalid_path = suite_path if invalid_file == "suite" else outputs_path
    invalid_path.write_bytes(b"\xff\xfe{not-utf8")

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

    assert result.exit_code == 2
    assert expected_message in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


def test_trajectory_command_writes_deterministic_release_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    policy_path = tmp_path / "trajectory-policy.json"
    trajectory_path = tmp_path / "trajectory.json"
    report_path = tmp_path / "trajectory-report.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "initial_state": "planning",
                "terminal_states": ["completed"],
                "required_transitions": [
                    {"from_state": "planning", "to_state": "researching"},
                    {"from_state": "researching", "to_state": "completed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "steps": [
                    {
                        "step_id": "step-1",
                        "state_before": "planning",
                        "action": "search",
                        "state_after": "researching",
                    },
                    {
                        "step_id": "step-2",
                        "state_before": "researching",
                        "action": "answer",
                        "state_after": "completed",
                        "terminated": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "trajectory",
            str(policy_path),
            str(trajectory_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Trajectory gate: PASS" in result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["release_ready"] is True
    assert report["metrics"]["sequence_score"] == 1.0
    assert report["metrics"]["termination_score"] == 1.0
    assert len(report["trajectory_id"]) == 64
