from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from evalforge.provenance import canonical_json_sha256


def test_compare_command_writes_deterministic_evidence_and_fails_regression_gate(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    suite = {
        "schema_version": 1,
        "name": "comparison-smoke",
        "minimum_pass_rate": 0.0,
        "cases": [
            {"case_id": "stable", "prompt": "Answer", "expected_output": "yes"},
            {"case_id": "changed", "prompt": "Answer", "expected_output": "yes"},
        ],
    }
    baseline = {"stable": "yes", "changed": "yes"}
    candidate = {"stable": "yes", "changed": "no"}
    policy = {
        "schema_version": 1,
        "max_absolute_regression": 0.0,
        "max_relative_regression": 0.0,
    }
    paths = {
        "suite": tmp_path / "suite.json",
        "baseline": tmp_path / "baseline.json",
        "candidate": tmp_path / "candidate.json",
        "policy": tmp_path / "comparison-policy.json",
    }
    for name, payload in (
        ("suite", suite),
        ("baseline", baseline),
        ("candidate", candidate),
        ("policy", policy),
    ):
        paths[name].write_text(json.dumps(payload), encoding="utf-8")

    report_path = tmp_path / "comparison.json"
    args = [
        "compare",
        str(paths["suite"]),
        str(paths["baseline"]),
        str(paths["candidate"]),
        "--comparison-policy",
        str(paths["policy"]),
        "--baseline-label",
        "production",
        "--candidate-label",
        "change-42",
        "--report-path",
        str(report_path),
    ]
    first = CliRunner().invoke(app, args)
    first_bytes = report_path.read_bytes()
    second = CliRunner().invoke(app, args)

    assert first.exit_code == 1, first.output
    assert second.exit_code == 1, second.output
    assert report_path.read_bytes() == first_bytes
    report = json.loads(first_bytes)
    assert report["schema_version"] == 2
    assert report["baseline"]["schema_version"] == 4
    assert report["candidate"]["schema_version"] == 4
    assert report["baseline_label"] == "production"
    assert report["candidate_label"] == "change-42"
    assert report["suite_sha256"] == canonical_json_sha256(suite)
    assert report["baseline_sha256"] == canonical_json_sha256(baseline)
    assert report["candidate_sha256"] == canonical_json_sha256(candidate)
    assert report["comparison_policy_sha256"] == canonical_json_sha256(policy)
    assert report["comparison_policy"] == policy
    assert len(report["comparison_id"]) == 64
    assert report["case_deltas"][1]["status"] == "regression"
    assert report["budget_failures"][0]["scope"] == "overall"
    assert report["baseline"]["results"][1]["actual_output"] == "yes"
    assert report["candidate"]["results"][1]["actual_output"] == "no"
    assert "Weighted pass-rate delta: -50.00%" in first.output
    assert "Comparison gate: FAIL" in first.output


def test_comparison_id_uses_the_serialized_normalized_labels(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite = {
        "schema_version": 1,
        "name": "normalized-labels",
        "minimum_pass_rate": 1.0,
        "cases": [{"case_id": "stable", "prompt": "Answer", "expected_output": "yes"}],
    }
    inputs = {
        "suite": suite,
        "baseline": {"stable": "yes"},
        "candidate": {"stable": "yes"},
        "policy": {
            "schema_version": 1,
            "max_absolute_regression": 0.0,
            "max_relative_regression": 0.0,
        },
    }
    paths: dict[str, Path] = {}
    for name, payload in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path

    def run_with_labels(
        baseline_label: str, candidate_label: str, report_name: str
    ) -> dict[str, object]:
        report_path = tmp_path / report_name
        result = CliRunner().invoke(
            app,
            [
                "compare",
                str(paths["suite"]),
                str(paths["baseline"]),
                str(paths["candidate"]),
                "--comparison-policy",
                str(paths["policy"]),
                "--baseline-label",
                baseline_label,
                "--candidate-label",
                candidate_label,
                "--report-path",
                str(report_path),
            ],
        )
        assert result.exit_code == 0, result.output
        return json.loads(report_path.read_text(encoding="utf-8"))

    plain = run_with_labels("production", "change-42", "plain.json")
    padded = run_with_labels("  production  ", "\tchange-42\n", "padded.json")

    assert padded["baseline_label"] == plain["baseline_label"] == "production"
    assert padded["candidate_label"] == plain["candidate_label"] == "change-42"
    assert padded["comparison_id"] == plain["comparison_id"]
