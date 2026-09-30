from __future__ import annotations

import json
from pathlib import Path

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


def _evaluation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "model": {
            "schema_version": 1,
            "provider": "sentence-transformers",
            "model_id": "all-MiniLM-L6-v2",
            "revision": "8b3219a92973c328a8e22fadcfa821b5dc75636a",
            "dimensions": 3,
            "artifact_sha256": "a" * 64,
            "license": "Apache-2.0",
        },
        "cases": [
            {
                "case_id": "paraphrase",
                "reference_embedding": [1.0, 0.0, 0.0],
                "candidate_embedding": [0.8, 0.6, 0.0],
            }
        ],
    }


def test_embedding_similarity_cli_writes_deterministic_redacted_provenance(tmp_path: Path) -> None:
    from evalforge.cli import app
    from evalforge.contracts import EmbeddingEvaluation
    from evalforge.embedding_similarity import model_provenance_sha256

    evaluation = _evaluation()
    model = EmbeddingEvaluation.model_validate(evaluation).model
    policy = {
        "schema_version": 1,
        "approved_model_sha256": model_provenance_sha256(model),
        "minimum_cosine_similarity": 0.75,
        "minimum_pass_rate": 1.0,
    }
    evaluation_path = tmp_path / "embedding-evaluation.json"
    policy_path = tmp_path / "embedding-policy.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    report_paths = (tmp_path / "report-a.json", tmp_path / "report-b.json")

    results = [
        CliRunner().invoke(
            app,
            [
                "embedding-similarity",
                str(evaluation_path),
                str(policy_path),
                "--report-path",
                str(report_path),
            ],
        )
        for report_path in report_paths
    ]

    assert [result.exit_code for result in results] == [0, 0]
    assert "Embedding similarity gate: PASS" in results[0].output
    assert report_paths[0].read_bytes() == report_paths[1].read_bytes()
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert (
        payload["embedding_semantics_version"]
        == "embedding-cosine-l2-v1-scaled-hypot-fsum-exact-decision-aligned"
    )
    assert len(payload["evaluation_sha256"]) == 64
    assert len(payload["policy_sha256"]) == 64
    assert len(payload["embedding_evaluation_id"]) == 64
    assert payload["model"]["revision"] == "8b3219a92973c328a8e22fadcfa821b5dc75636a"
    assert payload["results"][0]["cosine_similarity"] == 0.8
    assert "reference_embedding" not in payload["results"][0]
    assert "candidate_embedding" not in payload["results"][0]


def test_embedding_similarity_cli_rejects_unapproved_model_without_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    evaluation_path = tmp_path / "embedding-evaluation.json"
    policy_path = tmp_path / "embedding-policy.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(_evaluation()), encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "approved_model_sha256": "f" * 64,
                "minimum_cosine_similarity": 0.75,
                "minimum_pass_rate": 1.0,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "embedding-similarity",
            str(evaluation_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "embedding input is invalid or ambiguous" in result.output
    assert not report_path.exists()


def test_embedding_similarity_cli_rejects_nonzero_thresholds_that_underflow_to_zero(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app
    from evalforge.contracts import EmbeddingEvaluation
    from evalforge.embedding_similarity import model_provenance_sha256

    evaluation = _evaluation()
    model = EmbeddingEvaluation.model_validate(evaluation).model
    base_policy = {
        "schema_version": 1,
        "approved_model_sha256": model_provenance_sha256(model),
        "minimum_cosine_similarity": 0.75,
        "minimum_pass_rate": 1.0,
    }
    evaluation_path = tmp_path / "embedding-evaluation.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")

    for underflow_literal in ("1e-9999", f"1e-{'9' * 252}"):
        for field_name, original_value in (
            ("minimum_cosine_similarity", "0.75"),
            ("minimum_pass_rate", "1.0"),
        ):
            policy_path = tmp_path / f"{field_name}-{len(underflow_literal)}.json"
            report_path = tmp_path / f"{field_name}-{len(underflow_literal)}-report.json"
            report_path.write_text("preserve existing report", encoding="utf-8")
            raw_policy = json.dumps(base_policy).replace(
                f'"{field_name}": {original_value}',
                f'"{field_name}": {underflow_literal}',
            )
            assert underflow_literal in raw_policy
            policy_path.write_text(raw_policy, encoding="utf-8")

            result = CliRunner().invoke(
                app,
                [
                    "embedding-similarity",
                    str(evaluation_path),
                    str(policy_path),
                    "--report-path",
                    str(report_path),
                ],
            )

            assert result.exit_code == 2
            assert "embedding policy is invalid or ambiguous" in result.output
            assert "Traceback" not in result.output
            assert report_path.read_text(encoding="utf-8") == "preserve existing report"


def test_embedding_similarity_validation_errors_do_not_retain_raw_vectors(
    tmp_path: Path,
) -> None:
    import pytest
    import typer

    from evalforge.cli import embedding_similarity_command

    sensitive_coordinate = 987654321.125
    evaluation = _evaluation()
    cases = evaluation["cases"]
    assert isinstance(cases, list)
    case = cases[0]
    assert isinstance(case, dict)
    case["reference_embedding"] = [sensitive_coordinate, 0.0]
    evaluation_path = tmp_path / "embedding-evaluation.json"
    policy_path = tmp_path / "embedding-policy.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "approved_model_sha256": "f" * 64,
                "minimum_cosine_similarity": 0.75,
                "minimum_pass_rate": 1.0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(typer.BadParameter) as raised:
        embedding_similarity_command(evaluation_path, policy_path, report_path)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert str(sensitive_coordinate) not in str(raised.value)
    assert str(sensitive_coordinate) not in _evalforge_traceback_locals(raised.value)
    assert not report_path.exists()


def test_embedding_json_errors_do_not_retain_the_source_document(tmp_path: Path) -> None:
    import pytest
    import typer

    from evalforge.cli import _read_json

    sensitive_coordinate = "987654321.125"
    evaluation_path = tmp_path / "malformed-embedding-evaluation.json"
    evaluation_path.write_text(
        f'{{"reference_embedding":[{sensitive_coordinate},',
        encoding="utf-8",
    )

    with pytest.raises(typer.BadParameter) as raised:
        _read_json(evaluation_path, label="embedding_evaluation")

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert sensitive_coordinate not in str(raised.value)


def test_embedding_model_approval_errors_do_not_retain_validated_inputs(tmp_path: Path) -> None:
    import pytest
    import typer

    from evalforge.cli import embedding_similarity_command

    sensitive_coordinate = 654321.125
    evaluation = _evaluation()
    cases = evaluation["cases"]
    assert isinstance(cases, list)
    case = cases[0]
    assert isinstance(case, dict)
    case["reference_embedding"] = [sensitive_coordinate, 0.0, 0.0]
    evaluation_path = tmp_path / "embedding-evaluation.json"
    policy_path = tmp_path / "embedding-policy.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "approved_model_sha256": "f" * 64,
                "minimum_cosine_similarity": 0.75,
                "minimum_pass_rate": 1.0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(typer.BadParameter) as raised:
        embedding_similarity_command(evaluation_path, policy_path, report_path)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert str(sensitive_coordinate) not in _evalforge_traceback_locals(raised.value)
    assert not report_path.exists()


def test_embedding_similarity_cli_writes_failed_gate_evidence_and_exits_one(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app
    from evalforge.contracts import EmbeddingEvaluation
    from evalforge.embedding_similarity import model_provenance_sha256

    evaluation = _evaluation()
    sensitive_coordinate = 543210.125
    cases = evaluation["cases"]
    assert isinstance(cases, list)
    case = cases[0]
    assert isinstance(case, dict)
    case["reference_embedding"] = [sensitive_coordinate, 0.0, 0.0]
    model = EmbeddingEvaluation.model_validate(evaluation).model
    evaluation_path = tmp_path / "embedding-evaluation.json"
    policy_path = tmp_path / "embedding-policy.json"
    report_path = tmp_path / "report.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "approved_model_sha256": model_provenance_sha256(model),
                "minimum_cosine_similarity": 0.9,
                "minimum_pass_rate": 1.0,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "embedding-similarity",
            str(evaluation_path),
            str(policy_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 1
    assert "Embedding similarity gate: FAIL" in result.output
    assert result.exception is not None
    assert str(sensitive_coordinate) not in _evalforge_traceback_locals(result.exception)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["release_ready"] is False
    assert payload["gate_failures"] == [
        {
            "code": "minimum_pass_rate_not_met",
            "observed": 0.0,
            "required": 1.0,
        }
    ]


def test_embedding_parser_and_report_write_errors_drop_sensitive_frame_locals(
    tmp_path: Path,
) -> None:
    import pytest
    import typer

    from evalforge.cli import embedding_similarity_command
    from evalforge.contracts import EmbeddingEvaluation
    from evalforge.embedding_similarity import model_provenance_sha256

    sensitive_coordinate = "432109.125"
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text(
        f'{{"reference_embedding":[{sensitive_coordinate},',
        encoding="utf-8",
    )
    unused_policy_path = tmp_path / "unused-policy.json"

    with pytest.raises(typer.BadParameter) as parser_error:
        embedding_similarity_command(malformed_path, unused_policy_path, tmp_path / "unused.json")

    assert sensitive_coordinate not in _evalforge_traceback_locals(parser_error.value)

    evaluation = _evaluation()
    cases = evaluation["cases"]
    assert isinstance(cases, list)
    case = cases[0]
    assert isinstance(case, dict)
    case["reference_embedding"] = [float(sensitive_coordinate), 0.0, 0.0]
    model = EmbeddingEvaluation.model_validate(evaluation).model
    evaluation_path = tmp_path / "embedding-evaluation.json"
    policy_path = tmp_path / "embedding-policy.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "approved_model_sha256": model_provenance_sha256(model),
                "minimum_cosine_similarity": 0.75,
                "minimum_pass_rate": 1.0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(typer.BadParameter) as write_error:
        embedding_similarity_command(evaluation_path, policy_path, tmp_path)

    assert sensitive_coordinate not in _evalforge_traceback_locals(write_error.value)
