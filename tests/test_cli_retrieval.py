from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def _payloads() -> tuple[dict[str, object], dict[str, object]]:
    from evalforge.contracts import RetrievalEvaluation
    from evalforge.retrieval import retriever_provenance_sha256

    evaluation: dict[str, object] = {
        "schema_version": 1,
        "retriever": {
            "schema_version": 1,
            "producer": "evalforge-synthetic-retriever",
            "revision": "fixture-1",
            "retriever_artifact_sha256": "a" * 64,
            "corpus_artifact_sha256": "b" * 64,
            "index_artifact_sha256": "c" * 64,
        },
        "cases": [
            {
                "case_id": "refund-policy",
                "relevant_document_ids": ["refund-v2"],
                "retrieved_document_ids": ["faq", "refund-v2", "privacy"],
            },
            {
                "case_id": "shipping-policy",
                "relevant_document_ids": ["shipping-v3"],
                "retrieved_document_ids": ["shipping-v3", "contact", "returns"],
            },
        ],
    }
    parsed = RetrievalEvaluation.model_validate(evaluation)
    policy: dict[str, object] = {
        "schema_version": 1,
        "task_id": "support-retrieval",
        "approved_retriever_sha256": retriever_provenance_sha256(parsed.retriever),
        "required_case_ids": ["refund-policy", "shipping-policy"],
        "cutoff_k": 3,
        "minimum_precision_at_k": {"numerator": 1, "denominator": 3},
        "minimum_recall_at_k": {"numerator": 1, "denominator": 1},
        "minimum_mrr_at_k": {"numerator": 3, "denominator": 4},
    }
    return evaluation, policy


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    evaluation, policy = _payloads()
    evaluation_path = tmp_path / "retrieval-evaluation.json"
    policy_path = tmp_path / "retrieval-policy.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return evaluation_path, policy_path


def test_retrieval_cli_writes_deterministic_content_addressed_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    evaluation_path, policy_path = _write_inputs(tmp_path)
    report_paths = (tmp_path / "report-a.json", tmp_path / "report-b.json")

    results = [
        CliRunner().invoke(
            app,
            [
                "retrieval",
                str(evaluation_path),
                str(policy_path),
                "--report-path",
                str(report_path),
            ],
        )
        for report_path in report_paths
    ]

    assert [result.exit_code for result in results] == [0, 0]
    assert "Retrieval precision@3: 33.33%" in results[0].output
    assert "Retrieval recall@3: 100.00%" in results[0].output
    assert "Retrieval MRR@3: 75.00%" in results[0].output
    assert "Retrieval gate: PASS" in results[0].output
    assert report_paths[0].read_bytes() == report_paths[1].read_bytes()
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert payload["retrieval_semantics_version"] == "ranked-retrieval-exact-fractions-v1"
    for field in (
        "evaluation_sha256",
        "policy_sha256",
        "report_sha256",
        "retrieval_evaluation_id",
    ):
        assert len(payload[field]) == 64
    assert payload["release_ready"] is True
    serialized = report_paths[0].read_text(encoding="utf-8")
    assert "refund-v2" not in serialized
    assert "shipping-v3" not in serialized


def test_retrieval_cli_writes_failed_evidence_and_exits_one(tmp_path: Path) -> None:
    from evalforge.cli import app

    evaluation, policy = _payloads()
    policy["minimum_precision_at_k"] = {"numerator": 167, "denominator": 500}
    evaluation_path = tmp_path / "retrieval-evaluation.json"
    policy_path = tmp_path / "retrieval-policy.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "retrieval",
            str(evaluation_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 1
    assert "Retrieval gate: FAIL" in result.output
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["release_ready"] is False
    assert payload["gate_failures"][0]["metric"] == "precision_at_k"


def test_retrieval_cli_rejects_ambiguous_input_without_replacing_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    evaluation_path, policy_path = _write_inputs(tmp_path)
    evaluation_path.write_text(
        '{"schema_version":1,"schema_version":1,"retriever":{},"cases":[]}',
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    report_path.write_text("preserve existing report", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "retrieval",
            str(evaluation_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "retrieval input is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert report_path.read_text(encoding="utf-8") == "preserve existing report"
