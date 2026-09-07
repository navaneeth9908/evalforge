from __future__ import annotations


def test_fully_supported_citations_produce_complete_grounding_metrics() -> None:
    from evalforge.contracts import GroundingEvaluation
    from evalforge.grounding import evaluate_grounding

    evaluation = GroundingEvaluation.model_validate(
        {
            "schema_version": 1,
            "answer": "The Atlas battery lasts 18 hours and charges in 40 minutes.",
            "claims": [
                {
                    "claim_id": "battery-life",
                    "text": "The Atlas battery lasts 18 hours",
                    "answer_start": 0,
                    "answer_end": 32,
                },
                {
                    "claim_id": "charge-time",
                    "text": "and charges in 40 minutes.",
                    "answer_start": 33,
                    "answer_end": 59,
                },
            ],
            "retrieved_documents": [
                {
                    "document_id": "atlas-spec",
                    "content": ("The Atlas battery lasts 18 hours and charges in 40 minutes."),
                },
                {
                    "document_id": "warranty",
                    "content": "Atlas includes a two year warranty.",
                },
            ],
            "citations": [
                {"claim_id": "battery-life", "document_id": "atlas-spec"},
                {"claim_id": "charge-time", "document_id": "atlas-spec"},
            ],
        }
    )

    report = evaluate_grounding(evaluation)

    assert report.release_ready is True
    assert report.metrics.model_dump() == {
        "claim_count": 2,
        "retrieved_document_count": 2,
        "citation_count": 2,
        "valid_citation_count": 2,
        "supported_citation_count": 2,
        "supported_claim_count": 2,
        "utilized_document_count": 1,
        "citation_validity": 1.0,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "context_utilization": 0.5,
        "lexical_grounding": 1.0,
    }
    assert [item.model_dump() for item in report.citations] == [
        {
            "claim_id": "battery-life",
            "document_id": "atlas-spec",
            "valid": True,
            "lexical_support": 1.0,
            "supported": True,
        },
        {
            "claim_id": "charge-time",
            "document_id": "atlas-spec",
            "valid": True,
            "lexical_support": 1.0,
            "supported": True,
        },
    ]
    assert report.findings == ()


def test_invalid_unsupported_and_uncited_evidence_fails_closed() -> None:
    from evalforge.contracts import GroundingEvaluation
    from evalforge.grounding import evaluate_grounding

    evaluation = GroundingEvaluation.model_validate(
        {
            "schema_version": 1,
            "answer": "Atlas lasts 18 hours and has solar charging and is waterproof",
            "claims": [
                {
                    "claim_id": "battery-life",
                    "text": "Atlas lasts 18 hours",
                    "answer_start": 0,
                    "answer_end": 20,
                },
                {
                    "claim_id": "solar",
                    "text": "and has solar charging",
                    "answer_start": 21,
                    "answer_end": 43,
                },
                {
                    "claim_id": "waterproof",
                    "text": "and is waterproof",
                    "answer_start": 44,
                    "answer_end": 61,
                },
            ],
            "retrieved_documents": [
                {"document_id": "spec", "content": "Atlas lasts 18 hours"},
                {"document_id": "charging", "content": "Atlas uses cable charging"},
            ],
            "citations": [
                {"claim_id": "battery-life", "document_id": "spec"},
                {"claim_id": "solar", "document_id": "charging"},
                {"claim_id": "solar", "document_id": "missing-doc"},
                {"claim_id": "missing-claim", "document_id": "spec"},
            ],
        }
    )

    report = evaluate_grounding(evaluation)

    assert report.release_ready is False
    assert report.metrics.citation_validity == 0.5
    assert report.metrics.citation_precision == 0.25
    assert report.metrics.citation_recall == 1 / 3
    assert report.metrics.context_utilization == 1.0
    assert report.metrics.lexical_grounding == 0.5
    assert [finding.model_dump(exclude_none=True) for finding in report.findings] == [
        {
            "code": "unsupported_citation",
            "claim_id": "solar",
            "document_id": "charging",
        },
        {
            "code": "invalid_document_reference",
            "claim_id": "solar",
            "document_id": "missing-doc",
        },
        {
            "code": "invalid_claim_reference",
            "claim_id": "missing-claim",
            "document_id": "spec",
        },
        {"code": "uncited_claim", "claim_id": "waterproof"},
    ]


def test_grounding_contract_rejects_ambiguous_versions_and_duplicate_ids() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import GroundingEvaluation

    base = {
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
        "retrieved_documents": [{"document_id": "spec", "content": "Atlas lasts 18 hours"}],
        "citations": [{"claim_id": "life", "document_id": "spec"}],
    }
    invalid_payloads = (
        {**base, "schema_version": True},
        {**base, "unknown": "rejected"},
        {
            **base,
            "claims": [{**base["claims"][0], "answer_start": True}],
        },
        {**base, "claims": [*base["claims"], *base["claims"]]},
        {
            **base,
            "retrieved_documents": [
                *base["retrieved_documents"],
                *base["retrieved_documents"],
            ],
        },
        {**base, "citations": [*base["citations"], *base["citations"]]},
    )

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            GroundingEvaluation.model_validate(payload)


def test_report_exposes_document_ids_without_answer_or_context_content() -> None:
    from evalforge.contracts import GroundingEvaluation
    from evalforge.grounding import evaluate_grounding

    report = evaluate_grounding(
        GroundingEvaluation.model_validate(
            {
                "schema_version": 1,
                "answer": "private-answer-sentinel private-claim-sentinel",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "text": "private-answer-sentinel private-claim-sentinel",
                        "answer_start": 0,
                        "answer_end": 46,
                    }
                ],
                "retrieved_documents": [
                    {
                        "document_id": "public-doc-id",
                        "content": "private-document-sentinel",
                    }
                ],
                "citations": [{"claim_id": "claim-1", "document_id": "public-doc-id"}],
            }
        )
    )

    assert [document.model_dump() for document in report.documents] == [
        {"document_id": "public-doc-id", "cited": True, "utilized": True}
    ]
    serialized = report.model_dump_json()
    assert "public-doc-id" in serialized
    assert "private-answer-sentinel" not in serialized
    assert "private-claim-sentinel" not in serialized
    assert "private-document-sentinel" not in serialized


def test_grounding_semantics_version_binds_unicode_database() -> None:
    import unicodedata

    from evalforge.grounding import GROUNDING_SEMANTICS_VERSION

    assert (
        f"lexical-grounding-v1-unicode-{unicodedata.unidata_version}"
    ) == GROUNDING_SEMANTICS_VERSION


def test_claim_spans_bind_the_complete_answer() -> None:
    from evalforge.contracts import GroundingEvaluation

    evaluation = GroundingEvaluation.model_validate(
        {
            "schema_version": 1,
            "answer": "alpha beta",
            "claims": [
                {"claim_id": "alpha", "text": "alpha", "answer_start": 0, "answer_end": 5},
                {"claim_id": "beta", "text": "beta", "answer_start": 6, "answer_end": 10},
            ],
            "retrieved_documents": [{"document_id": "doc", "content": "alpha beta"}],
            "citations": [
                {"claim_id": "alpha", "document_id": "doc"},
                {"claim_id": "beta", "document_id": "doc"},
            ],
        }
    )

    assert [(claim.answer_start, claim.answer_end) for claim in evaluation.claims] == [
        (0, 5),
        (6, 10),
    ]


def test_claim_spans_reject_incomplete_or_mismatched_answer_coverage() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import GroundingEvaluation

    base = {
        "schema_version": 1,
        "answer": "alpha beta",
        "retrieved_documents": [{"document_id": "doc", "content": "alpha beta"}],
        "citations": [{"claim_id": "alpha", "document_id": "doc"}],
    }
    invalid_claim_sets = (
        (
            [{"claim_id": "alpha", "text": "alpha", "answer_start": 0, "answer_end": 5}],
            "claims must cover the complete answer",
        ),
        (
            [{"claim_id": "alpha", "text": "beta", "answer_start": 0, "answer_end": 5}],
            "claim text must match its answer span",
        ),
        (
            [
                {"claim_id": "beta", "text": "beta", "answer_start": 6, "answer_end": 10},
                {"claim_id": "alpha", "text": "alpha", "answer_start": 0, "answer_end": 5},
            ],
            "claim spans must be ordered and non-overlapping",
        ),
    )

    for claims, message in invalid_claim_sets:
        with pytest.raises(ValidationError, match=message):
            GroundingEvaluation.model_validate({**base, "claims": claims})


def test_dual_invalid_citation_emits_both_reference_findings() -> None:
    from evalforge.contracts import GroundingEvaluation
    from evalforge.grounding import evaluate_grounding

    evaluation = GroundingEvaluation.model_validate(
        {
            "schema_version": 1,
            "answer": "alpha",
            "claims": [{"claim_id": "alpha", "text": "alpha", "answer_start": 0, "answer_end": 5}],
            "retrieved_documents": [{"document_id": "doc", "content": "alpha"}],
            "citations": [{"claim_id": "missing-claim", "document_id": "missing-doc"}],
        }
    )

    report = evaluate_grounding(evaluation)
    assert [
        finding.code
        for finding in report.findings
        if finding.claim_id == "missing-claim" and finding.document_id == "missing-doc"
    ] == ["invalid_claim_reference", "invalid_document_reference"]


def test_zero_citations_fail_closed_without_dividing_by_zero() -> None:
    from evalforge.contracts import GroundingEvaluation
    from evalforge.grounding import evaluate_grounding

    evaluation = GroundingEvaluation.model_validate(
        {
            "schema_version": 1,
            "answer": "alpha",
            "claims": [{"claim_id": "alpha", "text": "alpha", "answer_start": 0, "answer_end": 5}],
            "retrieved_documents": [{"document_id": "doc", "content": "alpha"}],
            "citations": [],
        }
    )

    report = evaluate_grounding(evaluation)
    assert report.metrics.citation_validity == 0.0
    assert report.metrics.citation_precision == 0.0
    assert report.metrics.citation_recall == 0.0
    assert report.metrics.context_utilization == 0.0
    assert report.release_ready is False


def test_tokenless_text_fails_closed() -> None:
    from evalforge.contracts import GroundingEvaluation
    from evalforge.grounding import evaluate_grounding

    evaluation = GroundingEvaluation.model_validate(
        {
            "schema_version": 1,
            "answer": "!!!",
            "claims": [{"claim_id": "marks", "text": "!!!", "answer_start": 0, "answer_end": 3}],
            "retrieved_documents": [{"document_id": "doc", "content": "..."}],
            "citations": [{"claim_id": "marks", "document_id": "doc"}],
        }
    )

    report = evaluate_grounding(evaluation)
    assert report.metrics.citation_validity == 1.0
    assert report.metrics.citation_precision == 0.0
    assert report.metrics.citation_recall == 0.0
    assert report.metrics.lexical_grounding == 0.0
    assert report.release_ready is False
