from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

from typer.testing import CliRunner


def test_synthetic_demo_crosses_provider_metrics_persistence_api_dashboard_and_ci(
    tmp_path: Path,
) -> None:
    from evalforge.demo import run_synthetic_demo

    evidence = run_synthetic_demo(tmp_path)

    assert evidence["schema_version"] == 1
    assert evidence["provider"] == "evalforge-synthetic-fake"
    assert evidence["api_version"] == "v1"
    assert evidence["release_ready"] is True
    assert evidence["persisted_runs"] == 1
    assert len(evidence["run_id"]) == 64
    assert evidence["artifacts"] == [
        "dashboard.html",
        "evaluation.json",
        "evaluation.junit.xml",
        "safety.sarif.json",
    ]

    report = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    assert report["run_id"] == evidence["run_id"]
    assert report["weighted_pass_rate"] == 1.0
    assert report["performance"] == {
        "case_count": 2,
        "total_latency_ms": 24,
        "average_latency_ms": 12,
        "total_cost_micro_usd": 0,
        "average_cost_micro_usd": 0,
        "latency_percentile": None,
        "cost_percentile": None,
    }
    assert (tmp_path / "evalforge.db").is_file()

    dashboard = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert evidence["run_id"] in dashboard
    assert "PASS" in dashboard
    assert "synthetic private candidate" not in dashboard

    junit = ElementTree.parse(tmp_path / "evaluation.junit.xml").getroot()
    assert junit.attrib == {"name": "evalforge", "tests": "7", "failures": "0"}
    sarif = json.loads((tmp_path / "safety.sarif.json").read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []


def test_demo_command_emits_machine_readable_acceptance_evidence(tmp_path: Path) -> None:
    from evalforge.cli import app

    result = CliRunner().invoke(app, ["demo", "--output-directory", str(tmp_path)])

    assert result.exit_code == 0, result.output
    evidence = json.loads(result.output)
    assert evidence["release_ready"] is True
    assert evidence["persisted_runs"] == 1
    assert (tmp_path / "evaluation.json").is_file()
