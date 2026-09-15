from __future__ import annotations

import json
import re
from pathlib import Path
from xml.etree import ElementTree

from typer.testing import CliRunner

from evalforge.contracts import (
    EvaluationSuite,
    SensitiveDataFinding,
    SensitiveDataReport,
)
from evalforge.engine import evaluate_suite


def _evaluation_report():
    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "ci-report-smoke",
            "release_policy": {
                "minimum_pass_rate": 1.0,
                "blocking_severities": ["critical"],
            },
            "cases": [
                {
                    "case_id": "alpha",
                    "prompt": "Synthetic passing case",
                    "expected_output": "ok",
                    "metric": "exact",
                },
                {
                    "case_id": "beta",
                    "prompt": "Synthetic failing case",
                    "expected_output": "SENSITIVE_EXPECTED",
                    "metric": "contains",
                    "threshold": 1.0,
                    "severity": "critical",
                    "category": "safety",
                },
            ],
        }
    )
    return evaluate_suite(
        suite.cases,
        {"alpha": "OK", "beta": "SENSITIVE_ACTUAL"},
        policy=suite.resolved_policy,
    )


def test_junit_export_has_stable_case_and_metric_names_with_redacted_failure_detail() -> None:
    from evalforge.ci_reporting import evaluation_report_to_junit

    rendered = evaluation_report_to_junit(_evaluation_report(), suite_name="ci-report-smoke")

    assert rendered == (Path(__file__).parent / "golden" / "evaluation.junit.xml").read_text(
        encoding="utf-8"
    )
    root = ElementTree.fromstring(rendered)
    assert root.attrib == {"name": "evalforge", "tests": "3", "failures": "2"}
    cases = root.findall("./testsuite/testcase")
    assert [(case.attrib["classname"], case.attrib["name"]) for case in cases] == [
        ("evalforge.metric.exact", "case:alpha"),
        ("evalforge.metric.contains", "case:beta"),
        ("evalforge.gate", "metric:weighted_pass_rate"),
    ]
    assert "SENSITIVE_EXPECTED" not in rendered
    assert "SENSITIVE_ACTUAL" not in rendered
    assert "score=0" in rendered
    assert "threshold=1" in rendered


def test_sarif_export_matches_golden_schema_and_never_contains_raw_evidence() -> None:
    from evalforge.ci_reporting import sensitive_data_report_to_sarif

    report = SensitiveDataReport(
        total_findings=2,
        findings=(
            SensitiveDataFinding(
                output_index=0,
                category="email_address",
                severity="medium",
            ),
            SensitiveDataFinding(
                output_index=1,
                category="api_key",
                severity="critical",
            ),
        ),
        release_ready=False,
    )

    rendered = sensitive_data_report_to_sarif(report)

    assert rendered == (Path(__file__).parent / "golden" / "safety.sarif.json").read_text(
        encoding="utf-8"
    )
    payload = json.loads(rendered)
    assert payload["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert payload["version"] == "2.1.0"
    run = payload["runs"][0]
    assert run["tool"]["driver"]["name"] == "EvalForge"
    assert [result["ruleId"] for result in run["results"]] == [
        "evalforge.sensitive-data.email-address",
        "evalforge.sensitive-data.api-key",
    ]
    assert [result["level"] for result in run["results"]] == ["warning", "error"]
    assert all(result["properties"]["evidence"] == "redacted" for result in run["results"])
    assert "synthetic@example.invalid" not in rendered
    assert "rawEvidence" not in rendered


def test_cli_writes_ci_artifacts_before_returning_failed_gate_status(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    policy_path = tmp_path / "leakage-policy.json"
    evaluation_path = tmp_path / "evaluation.json"
    leakage_path = tmp_path / "leakage.json"
    junit_path = tmp_path / "evaluation.junit.xml"
    sarif_path = tmp_path / "safety.sarif.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "ci-cli-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "synthetic-case",
                        "prompt": "Return safe synthetic text",
                        "expected_output": "allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(
        json.dumps({"synthetic-case": "synthetic@example.invalid"}), encoding="utf-8"
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

    evaluation = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--report-path",
            str(evaluation_path),
            "--junit-path",
            str(junit_path),
        ],
    )
    leakage = CliRunner().invoke(
        app,
        [
            "scan-leakage",
            str(outputs_path),
            "--policy",
            str(policy_path),
            "--report-path",
            str(leakage_path),
            "--sarif-path",
            str(sarif_path),
        ],
    )

    assert evaluation.exit_code == 1, evaluation.output
    assert leakage.exit_code == 1, leakage.output
    assert "JUnit report:" in evaluation.output
    assert "SARIF report:" in leakage.output
    assert ElementTree.parse(junit_path).getroot().attrib["failures"] == "2"
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["runs"][0]["results"][0]["properties"]["evidence"] == "redacted"
    assert "synthetic@example.invalid" not in sarif_path.read_text(encoding="utf-8")


def test_reusable_reporting_workflow_is_pinned_least_privilege_and_dogfooded() -> None:
    root = Path(__file__).parents[1]
    reusable = (root / ".github" / "workflows" / "evalforge-reports.yml").read_text(
        encoding="utf-8"
    )
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "workflow_call:" in reusable
    assert "permissions: {}" in reusable
    assert "contents: read" in reusable
    assert "security-events: write" in reusable
    assert reusable.count("persist-credentials: false") == 1
    assert ci.count("persist-credentials: false") == 1
    assert "evalforge evaluate" in reusable and "--junit-path" in reusable
    assert "evalforge scan-leakage" in reusable and "--sarif-path" in reusable
    assert "github/codeql-action/upload-sarif@b96794f015dfd88f77b49b1c93e0fa7110f94c63" in reusable
    action_refs = re.findall(r"uses:\s+[^\s@]+@([^\s#]+)", reusable)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert "uses: ./.github/workflows/evalforge-reports.yml" in ci
