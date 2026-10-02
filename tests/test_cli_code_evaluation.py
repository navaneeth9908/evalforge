from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

import pytest
from typer.testing import CliRunner


def _evalforge_traceback_locals(exception: BaseException) -> str:
    frames: list[str] = []
    traceback = exception.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        filename = frame.f_code.co_filename.replace("\\", "/")
        if "/src/evalforge/" in filename:
            frames.append(repr(frame.f_locals))
        traceback = traceback.tb_next
    return "\n".join(frames)


def _payloads() -> tuple[dict[str, object], dict[str, object]]:
    from evalforge.code_evaluation import harness_provenance_sha256
    from evalforge.contracts import CodeHarnessEvidence

    evidence: dict[str, object] = {
        "schema_version": 1,
        "candidate_artifact_sha256": "c" * 64,
        "harness": {
            "schema_version": 1,
            "producer": "pytest-sandbox",
            "revision": "runner-2026.10.1",
            "harness_artifact_sha256": "a" * 64,
            "runtime_artifact_sha256": "b" * 64,
        },
        "cases": [
            {"case_id": "public-add", "outcome": "passed"},
            {"case_id": "hidden-empty", "outcome": "passed"},
        ],
    }
    parsed = CodeHarnessEvidence.model_validate(evidence)
    policy: dict[str, object] = {
        "schema_version": 1,
        "task_id": "python-addition",
        "approved_harness_sha256": harness_provenance_sha256(parsed.harness),
        "required_case_ids": ["public-add", "hidden-empty"],
        "minimum_pass_rate_numerator": 1,
        "minimum_pass_rate_denominator": 1,
    }
    return evidence, policy


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    evidence, policy = _payloads()
    evidence_path = tmp_path / "code-evidence.json"
    policy_path = tmp_path / "code-policy.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return evidence_path, policy_path


def test_code_harness_cli_writes_deterministic_content_addressed_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    evidence_path, policy_path = _write_inputs(tmp_path)
    report_paths = (tmp_path / "report-a.json", tmp_path / "report-b.json")

    results = [
        CliRunner().invoke(
            app,
            [
                "code-harness",
                str(evidence_path),
                str(policy_path),
                "--report-path",
                str(report_path),
            ],
        )
        for report_path in report_paths
    ]

    assert [result.exit_code for result in results] == [0, 0]
    assert "Code harness gate: PASS" in results[0].output
    assert "Code pass rate: 100.00%" in results[0].output
    assert "Blocking outcomes: 0" in results[0].output
    assert report_paths[0].read_bytes() == report_paths[1].read_bytes()
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert payload["code_evaluation_semantics_version"] == "precomputed-code-harness-v1"
    assert len(payload["evidence_sha256"]) == 64
    assert len(payload["policy_sha256"]) == 64
    assert len(payload["report_sha256"]) == 64
    assert len(payload["code_evaluation_id"]) == 64
    assert payload["candidate_artifact_sha256"] == "c" * 64
    assert payload["release_ready"] is True
    assert payload["results"][0] == {
        "case_id": "public-add",
        "outcome": "passed",
        "passed": True,
    }
    serialized = report_paths[0].read_text(encoding="utf-8")
    assert "source_code" not in serialized
    assert "stdout" not in serialized
    assert "stderr" not in serialized


def test_code_harness_cli_writes_failed_evidence_and_exits_one(tmp_path: Path) -> None:
    from evalforge.cli import app

    evidence, policy = _payloads()
    cases = evidence["cases"]
    assert isinstance(cases, list)
    cases[1] = {"case_id": "hidden-empty", "outcome": "timeout"}
    policy["minimum_pass_rate_numerator"] = 0
    evidence_path = tmp_path / "code-evidence.json"
    policy_path = tmp_path / "code-policy.json"
    report_path = tmp_path / "report.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "code-harness",
            str(evidence_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 1
    assert "Code harness gate: FAIL" in result.output
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["release_ready"] is False
    assert payload["blocking_cases"] == 1
    assert payload["gate_failures"][0]["case_ids"] == ["hidden-empty"]


def test_code_harness_cli_rejects_invalid_evidence_without_replacing_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    evidence_path, policy_path = _write_inputs(tmp_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["cases"] = [{"case_id": "public-add", "outcome": "passed"}]
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text("preserve existing report", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "code-harness",
            str(evidence_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "code harness input is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert report_path.read_text(encoding="utf-8") == "preserve existing report"


def test_code_harness_cli_sanitizes_rejected_content_and_report_write_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import typer

    from evalforge.cli import code_harness_command

    evidence, policy = _payloads()
    sensitive_value = "candidate-sensitive-marker"
    evidence["source_code"] = sensitive_value
    evidence_path = tmp_path / "invalid-evidence.json"
    policy_path = tmp_path / "code-policy.json"
    report_path = tmp_path / "report.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(typer.BadParameter) as rejected:
        code_harness_command(evidence_path, policy_path, report_path)

    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None
    assert sensitive_value not in str(rejected.value)
    assert sensitive_value not in _evalforge_traceback_locals(rejected.value)
    assert not report_path.exists()

    valid_evidence_path, valid_policy_path = _write_inputs(tmp_path)
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("preserve blocker", encoding="utf-8")
    blocked_report = blocked_parent / "report.json"
    with pytest.raises(typer.BadParameter, match="could not be written") as write_failure:
        code_harness_command(valid_evidence_path, valid_policy_path, blocked_report)

    assert write_failure.value.__cause__ is None
    assert write_failure.value.__context__ is None
    assert blocked_parent.read_text(encoding="utf-8") == "preserve blocker"

    import evalforge.cli as cli_module

    original_named_temporary_file = cli_module.tempfile.NamedTemporaryFile

    class _FailingTemporaryFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._context = original_named_temporary_file(*args, **kwargs)
            self._destination: TextIO | None = None

        def __enter__(self) -> object:
            self._destination = self._context.__enter__()
            return self

        @property
        def name(self) -> str:
            assert self._destination is not None
            return str(self._destination.name)

        def write(self, value: str) -> None:
            assert self._destination is not None
            self._destination.write(value[:10])
            self._destination.flush()
            raise OSError("simulated partial write")

        def __exit__(self, *args: object) -> object:
            return self._context.__exit__(*args)

    monkeypatch.setattr(cli_module.tempfile, "NamedTemporaryFile", _FailingTemporaryFile)
    partial_report = tmp_path / "partial-report.json"
    with pytest.raises(typer.BadParameter, match="could not be written"):
        code_harness_command(valid_evidence_path, valid_policy_path, partial_report)
    assert not partial_report.exists()
    assert list(tmp_path.glob(".partial-report.json.*.tmp")) == []
