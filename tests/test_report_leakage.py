from __future__ import annotations


def test_nested_report_values_are_scanned_without_retaining_matches() -> None:
    from evalforge.contracts import SensitiveDataPolicy
    from evalforge.leakage import scan_sensitive_json

    synthetic_email = "reviewer@example.invalid"
    synthetic_token = "SYNTHETIC_REPORT_TOKEN_123456"
    report_input = {
        "schema_version": 5,
        "results": [{"actual_output": f"Contact {synthetic_email}"}],
        "diagnostics": {"message": f"api_key={synthetic_token}"},
    }
    policy = SensitiveDataPolicy(
        schema_version=1,
        enabled_categories=("email_address", "api_key"),
        blocking_severities=("medium", "high", "critical"),
    )

    report = scan_sensitive_json(report_input, policy)

    assert [finding.category for finding in report.findings] == [
        "email_address",
        "api_key",
    ]
    assert report.release_ready is False
    serialized = report.model_dump_json()
    assert synthetic_email not in serialized
    assert synthetic_token not in serialized


def test_report_leakage_cli_writes_redacted_failed_gate(tmp_path) -> None:
    import json

    from typer.testing import CliRunner

    from evalforge.cli import app

    synthetic_email = "reviewer@example.invalid"
    source_path = tmp_path / "evaluation-report.json"
    policy_path = tmp_path / "policy.json"
    result_path = tmp_path / "leakage-report.json"
    source_path.write_text(
        json.dumps({"results": [{"actual_output": f"Contact {synthetic_email}"}]}),
        encoding="utf-8",
    )
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled_categories": ["email_address"],
                "blocking_severities": ["medium"],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "scan-report-leakage",
            str(source_path),
            "--policy",
            str(policy_path),
            "--report-path",
            str(result_path),
        ],
    )

    assert result.exit_code == 1
    assert "Sensitive-data gate: FAIL" in result.output
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["total_findings"] == 1
    assert len(payload["report_sha256"]) == 64
    assert synthetic_email not in result_path.read_text(encoding="utf-8")
