from __future__ import annotations


def test_governed_embeddings_report_cosine_and_l2_metrics_without_raw_vectors() -> None:
    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    evaluation = EmbeddingEvaluation.model_validate(
        {
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
                },
                {
                    "case_id": "unrelated",
                    "reference_embedding": [1.0, 0.0, 0.0],
                    "candidate_embedding": [0.0, 1.0, 0.0],
                },
            ],
        }
    )
    policy = EmbeddingPolicy(
        schema_version=1,
        approved_model_sha256=model_provenance_sha256(evaluation.model),
        minimum_cosine_similarity=0.75,
        minimum_pass_rate=0.5,
    )

    report = evaluate_embedding_similarity(evaluation, policy)

    assert report.release_ready is True
    assert report.passed_cases == 1
    assert report.pass_rate == 0.5
    assert [result.cosine_similarity for result in report.results] == [0.8, 0.0]
    assert [result.euclidean_distance for result in report.results] == [
        0.6324555320336759,
        1.4142135623730951,
    ]
    assert [result.passed for result in report.results] == [True, False]
    assert report.model == evaluation.model
    serialized = report.model_dump_json()
    result_payload = report.model_dump(mode="json")["results"][0]
    assert "reference_embedding" not in result_payload
    assert "candidate_embedding" not in result_payload
    assert "[1.0,0.0,0.0]" not in serialized
    assert len(report.results[0].reference_embedding_sha256) == 64
    assert len(report.results[0].candidate_embedding_sha256) == 64


def test_embedding_gate_reports_failed_pass_rate_and_rejects_unapproved_model() -> None:
    import pytest

    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "semantic-v1",
                "revision": "revision-1",
                "dimensions": 2,
                "artifact_sha256": "b" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "opposite",
                    "reference_embedding": [1.0, 0.0],
                    "candidate_embedding": [-1.0, 0.0],
                }
            ],
        }
    )
    approved_digest = model_provenance_sha256(evaluation.model)
    report = evaluate_embedding_similarity(
        evaluation,
        EmbeddingPolicy(
            schema_version=1,
            approved_model_sha256=approved_digest,
            minimum_cosine_similarity=0.0,
            minimum_pass_rate=1.0,
        ),
    )

    assert report.release_ready is False
    assert [failure.model_dump() for failure in report.gate_failures] == [
        {
            "code": "minimum_pass_rate_not_met",
            "observed": 0.0,
            "required": 1.0,
        }
    ]
    with pytest.raises(ValueError, match="not approved"):
        evaluate_embedding_similarity(
            evaluation,
            EmbeddingPolicy(
                schema_version=1,
                approved_model_sha256="c" * 64,
                minimum_cosine_similarity=0.0,
                minimum_pass_rate=1.0,
            ),
        )


def test_embedding_contract_rejects_ambiguous_or_unsafe_vectors() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import EmbeddingEvaluation

    base = {
        "schema_version": 1,
        "model": {
            "schema_version": 1,
            "provider": "local",
            "model_id": "semantic-v1",
            "revision": "revision-1",
            "dimensions": 2,
            "artifact_sha256": "d" * 64,
            "license": "MIT",
        },
        "cases": [
            {
                "case_id": "case-1",
                "reference_embedding": [1.0, 0.0],
                "candidate_embedding": [0.0, 1.0],
            }
        ],
    }
    invalid_payloads = (
        {**base, "schema_version": True},
        {**base, "model": {**base["model"], "dimensions": True}},
        {
            **base,
            "cases": [{**base["cases"][0], "reference_embedding": [True, 0.0]}],
        },
        {
            **base,
            "cases": [{**base["cases"][0], "reference_embedding": [0.0, 0.0]}],
        },
        {
            **base,
            "cases": [{**base["cases"][0], "candidate_embedding": [1.0]}],
        },
        {**base, "cases": [*base["cases"], *base["cases"]]},
        {**base, "unknown": "rejected"},
    )

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            EmbeddingEvaluation.model_validate(payload)


def test_embedding_metrics_preserve_direction_at_minimum_subnormal() -> None:
    import math

    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    minimum_subnormal = float.fromhex("0x0.0000000000001p-1022")
    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "tiny-vectors",
                "revision": "revision-1",
                "dimensions": 2,
                "artifact_sha256": "e" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "angled",
                    "reference_embedding": [minimum_subnormal, minimum_subnormal],
                    "candidate_embedding": [minimum_subnormal, 0.0],
                }
            ],
        }
    )
    policy = EmbeddingPolicy(
        schema_version=1,
        approved_model_sha256=model_provenance_sha256(evaluation.model),
        minimum_cosine_similarity=0.9,
        minimum_pass_rate=1.0,
    )

    report = evaluate_embedding_similarity(evaluation, policy)
    result = report.results[0]

    assert math.isclose(result.cosine_similarity, math.sqrt(0.5), rel_tol=1e-15)
    assert result.euclidean_distance == minimum_subnormal
    assert result.passed is False
    assert report.release_ready is False


def test_embedding_cosine_preserves_exact_collinear_endpoint_decisions() -> None:
    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    reference = [1_000_000.0] * 8192
    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "high-dimensional-endpoints",
                "revision": "revision-1",
                "dimensions": 8192,
                "artifact_sha256": "f" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "identical",
                    "reference_embedding": reference,
                    "candidate_embedding": reference,
                },
                {
                    "case_id": "opposite",
                    "reference_embedding": reference,
                    "candidate_embedding": [-coordinate for coordinate in reference],
                },
            ],
        }
    )
    report = evaluate_embedding_similarity(
        evaluation,
        EmbeddingPolicy(
            schema_version=1,
            approved_model_sha256=model_provenance_sha256(evaluation.model),
            minimum_cosine_similarity=-0.9999999999999999,
            minimum_pass_rate=1.0,
        ),
    )

    assert [result.cosine_similarity for result in report.results] == [1.0, -1.0]
    assert [result.passed for result in report.results] == [True, False]
    assert report.release_ready is False


def test_embedding_gate_does_not_promote_rounded_overshoot_to_perfect_match() -> None:
    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "near-endpoint-rounding",
                "revision": "revision-1",
                "dimensions": 2,
                "artifact_sha256": "1" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "non-collinear",
                    "reference_embedding": [301868.9460797075, -855127.4266649145],
                    "candidate_embedding": [301868.94607970753, -855127.4266649145],
                }
            ],
        }
    )
    report = evaluate_embedding_similarity(
        evaluation,
        EmbeddingPolicy(
            schema_version=1,
            approved_model_sha256=model_provenance_sha256(evaluation.model),
            minimum_cosine_similarity=1.0,
            minimum_pass_rate=1.0,
        ),
    )

    assert report.results[0].cosine_similarity < 1.0
    assert report.results[0].passed is False
    assert report.release_ready is False


def test_embedding_gate_rejects_nonproportional_unit_vector_collision() -> None:
    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "normalized-collision",
                "revision": "revision-1",
                "dimensions": 2,
                "artifact_sha256": "2" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "distinct-directions",
                    "reference_embedding": [653574.5591789216, -927348.4821612446],
                    "candidate_embedding": [653574.5591789217, -927348.4821612446],
                }
            ],
        }
    )
    report = evaluate_embedding_similarity(
        evaluation,
        EmbeddingPolicy(
            schema_version=1,
            approved_model_sha256=model_provenance_sha256(evaluation.model),
            minimum_cosine_similarity=1.0,
            minimum_pass_rate=1.0,
        ),
    )

    assert report.results[0].cosine_similarity < 1.0
    assert report.results[0].passed is False
    assert report.release_ready is False


def test_embedding_gate_exactly_rejects_similarity_below_positive_endpoint_threshold() -> None:
    import math

    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "positive-endpoint-threshold",
                "revision": "revision-1",
                "dimensions": 2,
                "artifact_sha256": "3" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "below-threshold",
                    "reference_embedding": [8864.80477566144, -546731.4076748808],
                    "candidate_embedding": [8864.817926149213, -546731.4074616564],
                }
            ],
        }
    )
    threshold = math.nextafter(1.0, 0.0)
    report = evaluate_embedding_similarity(
        evaluation,
        EmbeddingPolicy(
            schema_version=1,
            approved_model_sha256=model_provenance_sha256(evaluation.model),
            minimum_cosine_similarity=threshold,
            minimum_pass_rate=1.0,
        ),
    )

    assert report.results[0].cosine_similarity == math.nextafter(threshold, -math.inf)
    assert report.results[0].passed is False
    assert report.release_ready is False


def test_embedding_gate_exactly_accepts_similarity_above_negative_endpoint_threshold() -> None:
    import math

    from evalforge.contracts import EmbeddingEvaluation, EmbeddingPolicy
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "negative-endpoint-threshold",
                "revision": "revision-1",
                "dimensions": 2,
                "artifact_sha256": "4" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "above-threshold",
                    "reference_embedding": [-388184.2664697948, 29605.121661721496],
                    "candidate_embedding": [388184.2659596217, -29605.128351144118],
                }
            ],
        }
    )
    threshold = math.nextafter(-1.0, 0.0)
    report = evaluate_embedding_similarity(
        evaluation,
        EmbeddingPolicy(
            schema_version=1,
            approved_model_sha256=model_provenance_sha256(evaluation.model),
            minimum_cosine_similarity=threshold,
            minimum_pass_rate=1.0,
        ),
    )

    assert report.results[0].cosine_similarity == threshold
    assert report.results[0].passed is True
    assert report.release_ready is True


def test_embedding_evaluation_revalidates_constructed_nested_models() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import (
        EmbeddingEvaluation,
        EmbeddingModelProvenance,
        EmbeddingSimilarityCase,
    )

    forged_model = EmbeddingModelProvenance.model_construct(
        schema_version=1,
        provider="local",
        model_id="semantic-v1",
        revision="revision-1",
        dimensions=True,
        artifact_sha256="5" * 64,
        license="MIT",
    )
    one_dimension_case = EmbeddingSimilarityCase(
        case_id="case-1",
        reference_embedding=(1.0,),
        candidate_embedding=(1.0,),
    )
    valid_model = EmbeddingModelProvenance(
        schema_version=1,
        provider="local",
        model_id="semantic-v1",
        revision="revision-1",
        dimensions=2,
        artifact_sha256="5" * 64,
        license="MIT",
    )
    forged_case = EmbeddingSimilarityCase.model_construct(
        case_id="case-2",
        reference_embedding=(True, 0.0),
        candidate_embedding=(1.0, 0.0),
    )

    invalid_nested_instances = (
        (forged_model, one_dimension_case),
        (valid_model, forged_case),
    )
    for model, case in invalid_nested_instances:
        with pytest.raises(ValidationError):
            EmbeddingEvaluation.model_validate(
                {
                    "schema_version": 1,
                    "model": model,
                    "cases": [case],
                }
            )


def test_embedding_report_rejects_contradictory_release_evidence() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import (
        EmbeddingEvaluation,
        EmbeddingPolicy,
        EmbeddingSimilarityReport,
    )
    from evalforge.embedding_similarity import (
        evaluate_embedding_similarity,
        model_provenance_sha256,
    )

    evaluation = EmbeddingEvaluation.model_validate(
        {
            "schema_version": 1,
            "model": {
                "schema_version": 1,
                "provider": "local",
                "model_id": "semantic-v1",
                "revision": "revision-1",
                "dimensions": 2,
                "artifact_sha256": "6" * 64,
                "license": "MIT",
            },
            "cases": [
                {
                    "case_id": "failed-case",
                    "reference_embedding": [1.0, 0.0],
                    "candidate_embedding": [-1.0, 0.0],
                }
            ],
        }
    )
    report = evaluate_embedding_similarity(
        evaluation,
        EmbeddingPolicy(
            schema_version=1,
            approved_model_sha256=model_provenance_sha256(evaluation.model),
            minimum_cosine_similarity=0.0,
            minimum_pass_rate=1.0,
        ),
    )
    forged_aggregate = report.model_dump(mode="python")
    forged_aggregate["release_ready"] = True
    forged_aggregate["gate_failures"] = ()

    forged_case_decision = report.model_dump(mode="python")
    forged_case_decision["results"][0]["passed"] = True
    forged_case_decision["passed_cases"] = 1
    forged_case_decision["pass_rate"] = 1.0
    forged_case_decision["gate_failures"] = ()
    forged_case_decision["release_ready"] = True

    coercive_boolean_evidence = report.model_dump(mode="python")
    coercive_boolean_evidence["results"][0]["passed"] = "true"
    coercive_boolean_evidence["passed_cases"] = 1
    coercive_boolean_evidence["pass_rate"] = 1.0
    coercive_boolean_evidence["gate_failures"] = ()
    coercive_boolean_evidence["release_ready"] = "true"

    for forged in (
        forged_aggregate,
        forged_case_decision,
        coercive_boolean_evidence,
    ):
        with pytest.raises(ValidationError):
            EmbeddingSimilarityReport.model_validate(forged)
