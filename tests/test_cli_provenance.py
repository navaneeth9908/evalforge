from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def test_evaluate_command_records_content_addressed_inputs_and_manifest(tmp_path: Path) -> None:
    from evalforge.cli import app
    from evalforge.provenance import EVALUATION_SEMANTICS_VERSION, canonical_json_sha256

    suite = {
        "schema_version": 1,
        "name": "provenance-smoke",
        "minimum_pass_rate": 1.0,
        "cases": [
            {
                "case_id": "policy",
                "prompt": "State the policy",
                "expected_output": "Allowed",
            }
        ],
    }
    outputs = {"policy": "Allowed"}
    manifest = {
        "schema_version": 1,
        "dataset_id": "provenance-smoke",
        "dataset_version": "v1",
        "suite_sha256": canonical_json_sha256(suite),
        "lineage": {
            "source": "synthetic://evalforge/provenance-smoke",
            "source_revision": "v1",
            "created_by": "EvalForge maintainers",
            "license": "CC0-1.0",
            "transformations": ["Hand-authored synthetic examples"],
        },
    }
    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    manifest_path = tmp_path / "dataset-manifest.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(json.dumps(suite, indent=2), encoding="utf-8")
    outputs_path.write_text(json.dumps(outputs), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--dataset-manifest",
            str(manifest_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 5
    assert report["suite_sha256"] == canonical_json_sha256(suite)
    assert report["candidate_sha256"] == canonical_json_sha256(outputs)
    assert report["dataset_manifest_sha256"] == canonical_json_sha256(manifest)
    assert report["dataset"] == {
        "dataset_id": "provenance-smoke",
        "dataset_version": "v1",
        "license": "CC0-1.0",
    }
    for sensitive_field in ("source", "source_revision", "created_by", "transformations"):
        assert sensitive_field not in report
        assert sensitive_field not in report["dataset"]
    assert "dataset_manifest" not in report
    assert report["evaluation_semantics_version"] == EVALUATION_SEMANTICS_VERSION
    assert report["canonicalization_version"] == "evalforge-json-v1"
    assert len(report["run_id"]) == 64

    second_report_path = tmp_path / "second-report.json"
    suite_path.write_text(json.dumps(suite, separators=(",", ":")), encoding="utf-8")
    second = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--dataset-manifest",
            str(manifest_path),
            "--report-path",
            str(second_report_path),
        ],
    )
    assert second.exit_code == 0, second.output
    second_report = json.loads(second_report_path.read_text(encoding="utf-8"))
    assert second_report["run_id"] == report["run_id"]


def test_evaluate_command_rejects_manifest_for_different_suite(tmp_path: Path) -> None:
    from evalforge.cli import app

    suite = {
        "schema_version": 1,
        "name": "digest-mismatch",
        "minimum_pass_rate": 1.0,
        "cases": [
            {
                "case_id": "policy",
                "prompt": "State the policy",
                "expected_output": "Allowed",
            }
        ],
    }
    manifest = {
        "schema_version": 1,
        "dataset_id": "digest-mismatch",
        "dataset_version": "v1",
        "suite_sha256": "0" * 64,
        "lineage": {
            "source": "synthetic://evalforge/digest-mismatch",
            "source_revision": "v1",
            "created_by": "EvalForge maintainers",
            "license": "CC0-1.0",
            "transformations": ["Hand-authored synthetic examples"],
        },
    }
    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    outputs_path.write_text(json.dumps({"policy": "Allowed"}), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--dataset-manifest",
            str(manifest_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "dataset manifest suite digest does not match suite content" in result.output
    assert not report_path.exists()


def test_evaluate_hashes_validated_raw_json_instead_of_normalized_models(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app
    from evalforge.provenance import canonical_json_sha256

    suite = {
        "schema_version": 1,
        "name": "raw-content-identity",
        "minimum_pass_rate": 1,
        "cases": [
            {
                "case_id": "policy",
                "prompt": "State the policy",
                "expected_output": "Allowed",
            }
        ],
    }
    manifest = {
        "schema_version": 1,
        "dataset_id": "raw-content-identity",
        "dataset_version": "v1",
        "suite_sha256": canonical_json_sha256(suite),
        "lineage": {
            "source": "  synthetic://evalforge/raw-content-identity  ",
            "source_revision": "v1",
            "created_by": "EvalForge maintainers",
            "license": "CC0-1.0",
            "transformations": ["Hand-authored synthetic examples"],
        },
    }
    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    outputs_path.write_text(json.dumps({"policy": "Allowed"}), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--dataset-manifest",
            str(manifest_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["suite_sha256"] == canonical_json_sha256(suite)
    assert report["dataset_manifest_sha256"] == canonical_json_sha256(manifest)


def test_evaluate_rejects_an_invalid_manifest_schema_without_a_traceback(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app
    from evalforge.provenance import canonical_json_sha256

    suite = {
        "schema_version": 1,
        "name": "invalid-manifest",
        "minimum_pass_rate": 1.0,
        "cases": [
            {
                "case_id": "policy",
                "prompt": "State the policy",
                "expected_output": "Allowed",
            }
        ],
    }
    manifest = {
        "schema_version": True,
        "dataset_id": "invalid-manifest",
        "dataset_version": "v1",
        "suite_sha256": canonical_json_sha256(suite),
        "lineage": {
            "source": "synthetic://evalforge/invalid-manifest",
            "source_revision": "v1",
            "created_by": "EvalForge maintainers",
            "license": "CC0-1.0",
            "transformations": ["Hand-authored synthetic examples"],
        },
    }
    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    outputs_path.write_text(json.dumps({"policy": "Allowed"}), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--dataset-manifest",
            str(manifest_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "dataset manifest is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()
