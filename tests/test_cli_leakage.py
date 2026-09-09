from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def test_leakage_cli_writes_deterministic_redacted_failed_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    synthetic_email = "person@example.invalid"
    outputs = {
        "synthetic-case": {
            "output": f"Contact {synthetic_email}",
            "latency_ms": 1,
            "cost_micro_usd": 0,
        }
    }
    policy = {
        "schema_version": 1,
        "enabled_categories": ["email_address"],
        "blocking_severities": ["medium", "high", "critical"],
    }
    outputs_path = tmp_path / "outputs.json"
    policy_path = tmp_path / "policy.json"
    outputs_path.write_text(json.dumps(outputs), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    report_paths = (tmp_path / "report-a.json", tmp_path / "report-b.json")

    results = [
        CliRunner().invoke(
            app,
            [
                "scan-leakage",
                str(outputs_path),
                "--policy",
                str(policy_path),
                "--report-path",
                str(report_path),
            ],
        )
        for report_path in report_paths
    ]

    assert [result.exit_code for result in results] == [1, 1]
    assert "Sensitive-data gate: FAIL" in results[0].output
    assert report_paths[0].read_bytes() == report_paths[1].read_bytes()
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["total_findings"] == 1
    assert payload["findings"] == [
        {"output_index": 0, "category": "email_address", "severity": "medium"}
    ]
    assert payload["leakage_semantics_version"].startswith("sensitive-output-v1-unicode-")
    assert len(payload["outputs_sha256"]) == 64
    assert len(payload["policy_sha256"]) == 64
    assert len(payload["scan_id"]) == 64
    assert synthetic_email not in report_paths[0].read_text(encoding="utf-8")


def test_leakage_cli_passes_clean_output_and_rejects_invalid_policy(tmp_path: Path) -> None:
    from evalforge.cli import app

    outputs_path = tmp_path / "outputs.json"
    policy_path = tmp_path / "policy.json"
    report_path = tmp_path / "report.json"
    outputs_path.write_text(
        json.dumps({"case-a": "No sensitive placeholder here."}), encoding="utf-8"
    )
    policy_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    clean = CliRunner().invoke(
        app,
        [
            "scan-leakage",
            str(outputs_path),
            "--policy",
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert clean.exit_code == 0
    assert "Sensitive-data gate: PASS" in clean.output
    policy_path.write_text(json.dumps({"schema_version": True}), encoding="utf-8")
    invalid_report = tmp_path / "invalid.json"
    invalid = CliRunner().invoke(
        app,
        [
            "scan-leakage",
            str(outputs_path),
            "--policy",
            str(policy_path),
            "--report-path",
            str(invalid_report),
        ],
    )
    assert invalid.exit_code == 2
    assert not invalid_report.exists()


def test_leakage_cli_rejects_empty_output_set_without_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    outputs_path = tmp_path / "outputs.json"
    policy_path = tmp_path / "policy.json"
    report_path = tmp_path / "report.json"
    outputs_path.write_text("{}", encoding="utf-8")
    policy_path.write_text('{"schema_version": 1}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "scan-leakage",
            str(outputs_path),
            "--policy",
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert not report_path.exists()
