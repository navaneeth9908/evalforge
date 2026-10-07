from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def _write_trend_inputs(tmp_path: Path, *, latest_ready: bool = True) -> tuple[Path, Path]:
    from evalforge.cross_dataset_trends import producer_sha256

    producer = {
        "producer_id": "offline-evaluation-exporter",
        "revision": "exporter-v3",
        "artifact_sha256": "a" * 64,
    }
    policy = {
        "schema_version": 1,
        "approved_producer_sha256": producer_sha256(producer),
        "datasets": [
            {
                "dataset_id": "support",
                "dataset_version": "2026-09",
                "suite_sha256": "b" * 64,
                "evaluator_semantics_version": "text-metrics-v3",
                "weight": 3.0,
                "max_drawdown": 0.2,
            },
            {
                "dataset_id": "safety",
                "dataset_version": "2026-09",
                "suite_sha256": "c" * 64,
                "evaluator_semantics_version": "policy-v2",
                "weight": 1.0,
                "max_drawdown": 0.2,
            },
        ],
        "max_overall_drawdown": 0.2,
    }
    datasets = [
        {
            "dataset_id": "support",
            "dataset_version": "2026-09",
            "suite_sha256": "b" * 64,
            "evaluator_semantics_version": "text-metrics-v3",
            "score": 0.8,
            "release_ready": latest_ready,
        },
        {
            "dataset_id": "safety",
            "dataset_version": "2026-09",
            "suite_sha256": "c" * 64,
            "evaluator_semantics_version": "policy-v2",
            "score": 0.9,
            "release_ready": True,
        },
    ]
    observations = {
        "schema_version": 1,
        "producer": producer,
        "checkpoints": [
            {"observed_at": "2026-01-01T00:00:00Z", "datasets": datasets},
            {"observed_at": "2026-02-01T00:00:00Z", "datasets": datasets},
        ],
    }
    observations_path = tmp_path / "cross-dataset-observations.json"
    policy_path = tmp_path / "cross-dataset-policy.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return observations_path, policy_path


def test_cross_dataset_trends_cli_writes_repeatable_atomic_artifact(tmp_path: Path) -> None:
    from evalforge.cli import app

    observations_path, policy_path = _write_trend_inputs(tmp_path)
    artifact_paths = (tmp_path / "trend-a.json", tmp_path / "trend-b.json")
    results = [
        CliRunner().invoke(
            app,
            [
                "cross-dataset-trends",
                str(observations_path),
                str(policy_path),
                "--artifact-path",
                str(artifact_path),
            ],
        )
        for artifact_path in artifact_paths
    ]

    assert [result.exit_code for result in results] == [0, 0]
    assert "Cross-dataset score: 82.50%" in results[0].output
    assert "Trend gate: PASS" in results[0].output
    assert artifact_paths[0].read_bytes() == artifact_paths[1].read_bytes()
    artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
    assert artifact["report"]["checkpoint_count"] == 2
    assert artifact["report"]["release_ready"] is True


def test_cross_dataset_trends_cli_uses_exit_one_for_gate_failure(tmp_path: Path) -> None:
    from evalforge.cli import app

    observations_path, policy_path = _write_trend_inputs(tmp_path, latest_ready=False)
    artifact_path = tmp_path / "failed-trend.json"

    result = CliRunner().invoke(
        app,
        [
            "cross-dataset-trends",
            str(observations_path),
            str(policy_path),
            "--artifact-path",
            str(artifact_path),
        ],
    )

    assert result.exit_code == 1
    assert "Trend gate: FAIL" in result.output
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["report"]["gate_failures"] == [
        {
            "scope": "dataset",
            "dataset_id": "support",
            "reason": "latest_underlying_gate",
            "observed": 0.0,
            "required": 1.0,
        }
    ]


def test_cross_dataset_trends_cli_rejects_duplicate_keys_without_replacing_artifact(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    observations_path, policy_path = _write_trend_inputs(tmp_path)
    policy_path.write_text(
        '{"schema_version":1,"schema_version":1,"approved_producer_sha256":"'
        + ("a" * 64)
        + '","datasets":[],"max_overall_drawdown":0.1}',
        encoding="utf-8",
    )
    artifact_path = tmp_path / "trend.json"
    artifact_path.write_text("preserve existing artifact", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "cross-dataset-trends",
            str(observations_path),
            str(policy_path),
            "--artifact-path",
            str(artifact_path),
        ],
    )

    assert result.exit_code == 2
    assert "cross-dataset trend input is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert artifact_path.read_text(encoding="utf-8") == "preserve existing artifact"
