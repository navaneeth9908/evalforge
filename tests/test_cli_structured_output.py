from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def _payloads() -> tuple[dict[str, object], dict[str, object]]:
    from evalforge.structured_output import StructuredOutputEvaluation, schema_catalog_sha256

    evaluation: dict[str, object] = {
        "schema_version": 1,
        "cases": [
            {
                "case_id": "customer-profile",
                "schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "active": {"type": "boolean"},
                    },
                    "required": ["name", "active"],
                    "additionalProperties": False,
                },
                "candidate": {"name": "Ada", "active": True},
            }
        ],
    }
    parsed = StructuredOutputEvaluation.model_validate(evaluation)
    policy: dict[str, object] = {
        "schema_version": 1,
        "task_id": "support-structured-output",
        "approved_schema_catalog_sha256": schema_catalog_sha256(parsed),
        "required_case_ids": ["customer-profile"],
        "minimum_pass_rate_numerator": 1,
        "minimum_pass_rate_denominator": 1,
    }
    return evaluation, policy


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    evaluation, policy = _payloads()
    evaluation_path = tmp_path / "structured-evaluation.json"
    policy_path = tmp_path / "structured-policy.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return evaluation_path, policy_path


def test_structured_output_cli_writes_deterministic_content_addressed_report(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    evaluation_path, policy_path = _write_inputs(tmp_path)
    report_paths = (tmp_path / "report-a.json", tmp_path / "report-b.json")
    results = [
        CliRunner().invoke(
            app,
            [
                "structured-output",
                str(evaluation_path),
                str(policy_path),
                "--report-path",
                str(report_path),
            ],
        )
        for report_path in report_paths
    ]

    assert [result.exit_code for result in results] == [0, 0]
    assert "Structured-output pass rate: 100.00%" in results[0].output
    assert "Structured-output gate: PASS" in results[0].output
    assert report_paths[0].read_bytes() == report_paths[1].read_bytes()
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert payload["structured_output_semantics_version"] == "bounded-json-schema-2020-12-v1"
    assert payload["release_ready"] is True
    for field in (
        "evaluation_sha256",
        "policy_sha256",
        "report_sha256",
        "structured_output_evaluation_id",
    ):
        assert len(payload[field]) == 64
    serialized = report_paths[0].read_text(encoding="utf-8")
    assert "Ada" not in serialized
    assert '"candidate"' not in serialized
    assert '"properties"' not in serialized


def test_structured_output_cli_writes_failed_evidence_and_exits_one(tmp_path: Path) -> None:
    from evalforge.cli import app

    evaluation, policy = _payloads()
    cases = evaluation["cases"]
    assert isinstance(cases, list)
    cases[0] = {**cases[0], "candidate": {"name": "Ada", "active": "yes"}}
    evaluation_path = tmp_path / "structured-evaluation.json"
    policy_path = tmp_path / "structured-policy.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "structured-output",
            str(evaluation_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 1
    assert "Structured-output gate: FAIL" in result.output
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["release_ready"] is False
    assert payload["results"][0]["validators"] == ["type"]


def test_structured_output_cli_rejects_ambiguous_input_without_replacing_report(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    evaluation_path, policy_path = _write_inputs(tmp_path)
    evaluation_path.write_text(
        '{"schema_version":1,"schema_version":1,"cases":[]}', encoding="utf-8"
    )
    report_path = tmp_path / "report.json"
    report_path.write_text("preserve existing report", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "structured-output",
            str(evaluation_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "structured-output input is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert report_path.read_text(encoding="utf-8") == "preserve existing report"


def test_structured_output_cli_rejects_lossy_numeric_literals_without_writing_report(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app
    from evalforge.structured_output import StructuredOutputEvaluation, schema_catalog_sha256

    evaluation = {
        "schema_version": 1,
        "cases": [
            {
                "case_id": "numeric-boundary",
                "schema": {"type": "number", "maximum": 0.1},
                "candidate": 0.1,
            }
        ],
    }
    approved = schema_catalog_sha256(StructuredOutputEvaluation.model_validate(evaluation))
    evaluation_path = tmp_path / "structured-evaluation.json"
    policy_path = tmp_path / "structured-policy.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(
        '{"schema_version":1,"cases":[{"case_id":"numeric-boundary",'
        '"schema":{"type":"number","maximum":0.1},'
        '"candidate":0.10000000000000001}]}',
        encoding="utf-8",
    )
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "numeric-boundary",
                "approved_schema_catalog_sha256": approved,
                "required_case_ids": ["numeric-boundary"],
                "minimum_pass_rate_numerator": 1,
                "minimum_pass_rate_denominator": 1,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "structured-output",
            str(evaluation_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "structured-output input is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()
