from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def test_stability_cli_serializes_deterministically_and_enforces_gates(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite = {
        "schema_version": 1,
        "name": "stability-smoke",
        "release_policy": {"minimum_pass_rate": 0.0},
        "cases": [
            {"case_id": "stable", "prompt": "Stable", "expected_output": "yes"},
            {"case_id": "flaky", "prompt": "Flaky", "expected_output": "yes"},
        ],
    }
    observations = {
        "schema_version": 1,
        "runs": [
            {"run_id": "run-1", "outputs": {"stable": "yes", "flaky": "yes"}},
            {"run_id": "run-2", "outputs": {"stable": "yes", "flaky": "no"}},
        ],
    }
    policy = {
        "schema_version": 1,
        "max_weighted_pass_rate_variance": 0.05,
        "max_flaky_case_rate": 0.25,
    }
    paths: dict[str, Path] = {}
    for name, payload in (("suite", suite), ("observations", observations), ("policy", policy)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path

    reports = (tmp_path / "report-a.json", tmp_path / "report-b.json")
    results = [
        CliRunner().invoke(
            app,
            [
                "stability",
                str(paths["suite"]),
                str(paths["observations"]),
                "--stability-policy",
                str(paths["policy"]),
                "--report-path",
                str(report_path),
            ],
        )
        for report_path in reports
    ]

    assert [result.exit_code for result in results] == [1, 1]
    assert "Stability gate: FAIL" in results[0].output
    assert reports[0].read_bytes() == reports[1].read_bytes()
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["suite_name"] == "stability-smoke"
    assert report["weighted_pass_rate"] == {
        "mean": 0.75,
        "minimum": 0.5,
        "maximum": 1.0,
        "population_variance": 0.0625,
    }
    assert report["cases"][1] == {
        "case_id": "flaky",
        "pass_count": 1,
        "fail_count": 1,
        "flaky": True,
    }
    assert len(report["suite_sha256"]) == 64
    assert len(report["observations_sha256"]) == 64
    assert len(report["stability_policy_sha256"]) == 64
    assert len(report["stability_id"]) == 64


def test_stability_cli_rejects_invalid_samples_without_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    observations_path = tmp_path / "observations.json"
    policy_path = tmp_path / "policy.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "invalid-stability",
                "minimum_pass_rate": 1.0,
                "cases": [{"case_id": "only", "prompt": "Only", "expected_output": "yes"}],
            }
        ),
        encoding="utf-8",
    )
    observations_path.write_text(json.dumps({"schema_version": 1, "runs": []}), encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "max_weighted_pass_rate_variance": 0.0,
                "max_flaky_case_rate": 0.0,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "stability",
            str(suite_path),
            str(observations_path),
            "--stability-policy",
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "stability input is invalid or ambiguous" in result.output
    assert not report_path.exists()
