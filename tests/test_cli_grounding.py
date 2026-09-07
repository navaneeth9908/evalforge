from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def test_grounding_cli_writes_deterministic_content_redacted_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    evaluation = {
        "schema_version": 1,
        "answer": "Atlas lasts 18 hours",
        "claims": [
            {
                "claim_id": "life",
                "text": "Atlas lasts 18 hours",
                "answer_start": 0,
                "answer_end": 20,
            }
        ],
        "retrieved_documents": [
            {"document_id": "atlas-spec", "content": "Atlas lasts 18 hours"},
            {"document_id": "private-note", "content": "sensitive-document-content"},
        ],
        "citations": [{"claim_id": "life", "document_id": "atlas-spec"}],
    }
    evaluation_path = tmp_path / "grounding.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    report_paths = (tmp_path / "report-a.json", tmp_path / "report-b.json")

    results = [
        CliRunner().invoke(
            app,
            ["grounding", str(evaluation_path), "--report-path", str(report_path)],
        )
        for report_path in report_paths
    ]

    assert [result.exit_code for result in results] == [0, 0]
    assert "Grounding gate: PASS" in results[0].output
    assert report_paths[0].read_bytes() == report_paths[1].read_bytes()
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["grounding_semantics_version"].startswith("lexical-grounding-v1-unicode-")
    assert len(payload["evaluation_sha256"]) == 64
    assert len(payload["grounding_id"]) == 64
    assert [document["document_id"] for document in payload["documents"]] == [
        "atlas-spec",
        "private-note",
    ]
    serialized = report_paths[0].read_text(encoding="utf-8")
    assert "Atlas lasts 18 hours" not in serialized
    assert "sensitive-document-content" not in serialized


def test_grounding_cli_writes_failed_gate_report_and_exits_one(tmp_path: Path) -> None:
    from evalforge.cli import app

    evaluation = {
        "schema_version": 1,
        "answer": "private-answer",
        "claims": [
            {
                "claim_id": "claim",
                "text": "private-answer",
                "answer_start": 0,
                "answer_end": 14,
            }
        ],
        "retrieved_documents": [{"document_id": "doc", "content": "private-document"}],
        "citations": [{"claim_id": "claim", "document_id": "doc"}],
    }
    evaluation_path = tmp_path / "grounding.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["grounding", str(evaluation_path), "--report-path", str(report_path)],
    )

    assert result.exit_code == 1
    assert "Grounding gate: FAIL" in result.output
    assert report_path.is_file()
    serialized = report_path.read_text(encoding="utf-8")
    assert "private-answer" not in serialized
    assert "private-document" not in serialized


def test_grounding_cli_rejects_malformed_input_without_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    malformed = {
        "schema_version": 1,
        "answer": "alpha beta",
        "claims": [{"claim_id": "alpha", "text": "alpha", "answer_start": 0, "answer_end": 5}],
        "retrieved_documents": [{"document_id": "doc", "content": "alpha beta"}],
        "citations": [{"claim_id": "alpha", "document_id": "doc"}],
    }
    evaluation_path = tmp_path / "grounding.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(malformed), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["grounding", str(evaluation_path), "--report-path", str(report_path)],
    )

    assert result.exit_code == 2
    assert not report_path.exists()
