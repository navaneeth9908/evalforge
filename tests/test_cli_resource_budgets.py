from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def test_evaluate_cli_serializes_resource_evidence_and_budget_failures(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "resource-gate",
                "release_policy": {
                    "minimum_pass_rate": 1.0,
                    "resource_budgets": {
                        "max_total_latency_ms": 9,
                        "max_total_cost_micro_usd": 20,
                    },
                },
                "cases": [
                    {
                        "case_id": "answer",
                        "prompt": "Answer",
                        "expected_output": "yes",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(
        json.dumps(
            {
                "answer": {
                    "output": "yes",
                    "latency_ms": 10,
                    "cost_micro_usd": 20,
                }
            }
        ),
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

    assert result.exit_code == 1, result.output
    assert "Release gate: FAIL" in result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 5
    assert report["performance"] == {
        "case_count": 1,
        "total_latency_ms": 10,
        "average_latency_ms": 10,
        "latency_percentile": None,
        "total_cost_micro_usd": 20,
        "average_cost_micro_usd": 20,
        "cost_percentile": None,
    }
    assert report["results"][0]["performance"] == {
        "latency_ms": 10,
        "cost_micro_usd": 20,
    }
    assert report["resource_gate_failures"] == [
        {
            "metric": "total_latency_ms",
            "observed": 10,
            "required": 9,
            "percentile": None,
        }
    ]


def test_compare_cli_accepts_structured_resource_evidence(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite = {
        "schema_version": 1,
        "name": "resource-comparison",
        "release_policy": {
            "minimum_pass_rate": 1.0,
            "resource_budgets": {
                "max_total_latency_ms": 30,
                "max_total_cost_micro_usd": 30,
            },
        },
        "cases": [{"case_id": "answer", "prompt": "Answer", "expected_output": "yes"}],
    }
    baseline = {"answer": {"output": "yes", "latency_ms": 20, "cost_micro_usd": 20}}
    candidate = {"answer": {"output": "yes", "latency_ms": 10, "cost_micro_usd": 10}}
    comparison_policy = {
        "schema_version": 1,
        "max_absolute_regression": 0.0,
        "max_relative_regression": 0.0,
    }
    paths: dict[str, Path] = {}
    for name, payload in (
        ("suite", suite),
        ("baseline", baseline),
        ("candidate", candidate),
        ("policy", comparison_policy),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path

    report_path = tmp_path / "comparison.json"
    result = CliRunner().invoke(
        app,
        [
            "compare",
            str(paths["suite"]),
            str(paths["baseline"]),
            str(paths["candidate"]),
            "--comparison-policy",
            str(paths["policy"]),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 3
    assert report["baseline"]["schema_version"] == 5
    assert report["candidate"]["schema_version"] == 5
    assert report["baseline"]["performance"]["total_latency_ms"] == 20
    assert report["candidate"]["performance"]["total_cost_micro_usd"] == 10
    assert report["release_ready"] is True


def test_evaluate_cli_rejects_aggregate_overflow_without_writing_report(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    maximum = 9_007_199_254_740_991
    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "aggregate-overflow",
                "release_policy": {"minimum_pass_rate": 1.0},
                "cases": [
                    {"case_id": "a", "prompt": "A", "expected_output": "yes"},
                    {"case_id": "b", "prompt": "B", "expected_output": "yes"},
                ],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(
        json.dumps(
            {
                "a": {
                    "output": "yes",
                    "latency_ms": maximum,
                    "cost_micro_usd": maximum,
                },
                "b": {
                    "output": "yes",
                    "latency_ms": maximum,
                    "cost_micro_usd": maximum,
                },
            }
        ),
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
    assert "evaluation input is invalid or ambiguous" in result.output
    assert not report_path.exists()


def test_evaluate_cli_rejects_missing_budget_evidence_without_writing_report(
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
                "name": "missing-resource-evidence",
                "release_policy": {
                    "minimum_pass_rate": 1.0,
                    "resource_budgets": {"max_total_latency_ms": 10},
                },
                "cases": [{"case_id": "answer", "prompt": "A", "expected_output": "yes"}],
            }
        ),
        encoding="utf-8",
    )
    outputs_path.write_text(json.dumps({"answer": "yes"}), encoding="utf-8")

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
    assert not report_path.exists()
