from __future__ import annotations

import pytest
from pydantic import ValidationError


def _evaluation_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "retriever": {
            "schema_version": 1,
            "producer": "pytest-retriever",
            "revision": "retriever-2026.10.1",
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


def _policy_payload(approved_retriever_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "support-retrieval",
        "approved_retriever_sha256": approved_retriever_sha256,
        "required_case_ids": ["refund-policy", "shipping-policy"],
        "cutoff_k": 3,
        "minimum_precision_at_k": {"numerator": 1, "denominator": 3},
        "minimum_recall_at_k": {"numerator": 1, "denominator": 1},
        "minimum_mrr_at_k": {"numerator": 3, "denominator": 4},
    }


def test_retrieval_evaluation_derives_exact_ranked_metrics_without_document_ids() -> None:
    from evalforge.contracts import RetrievalEvaluation, RetrievalPolicy
    from evalforge.retrieval import evaluate_retrieval, retriever_provenance_sha256

    evaluation = RetrievalEvaluation.model_validate(_evaluation_payload())
    policy = RetrievalPolicy.model_validate(
        _policy_payload(retriever_provenance_sha256(evaluation.retriever))
    )

    report = evaluate_retrieval(evaluation, policy)

    assert report.release_ready is True
    assert report.total_cases == 2
    assert [result.model_dump() for result in report.results] == [
        {
            "case_id": "refund-policy",
            "relevant_document_count": 1,
            "retrieved_document_count": 3,
            "relevant_at_k": 1,
            "first_relevant_rank": 2,
            "precision_at_k": 1 / 3,
            "recall_at_k": 1.0,
            "reciprocal_rank_at_k": 0.5,
        },
        {
            "case_id": "shipping-policy",
            "relevant_document_count": 1,
            "retrieved_document_count": 3,
            "relevant_at_k": 1,
            "first_relevant_rank": 1,
            "precision_at_k": 1 / 3,
            "recall_at_k": 1.0,
            "reciprocal_rank_at_k": 1.0,
        },
    ]
    assert [metric.model_dump() for metric in report.metrics] == [
        {
            "metric": "precision_at_k",
            "observed_numerator": "1",
            "observed_denominator": "3",
            "observed_value": 1 / 3,
            "required_numerator": 1,
            "required_denominator": 3,
            "passed": True,
        },
        {
            "metric": "recall_at_k",
            "observed_numerator": "1",
            "observed_denominator": "1",
            "observed_value": 1.0,
            "required_numerator": 1,
            "required_denominator": 1,
            "passed": True,
        },
        {
            "metric": "mrr_at_k",
            "observed_numerator": "3",
            "observed_denominator": "4",
            "observed_value": 0.75,
            "required_numerator": 3,
            "required_denominator": 4,
            "passed": True,
        },
    ]
    assert report.gate_failures == ()
    serialized = report.model_dump_json()
    assert "refund-v2" not in serialized
    assert "shipping-v3" not in serialized
    assert "retrieved_document_ids" not in serialized


def test_retrieval_report_rejects_impossible_hit_counts() -> None:
    from evalforge.contracts import RetrievalEvaluation, RetrievalPolicy, RetrievalReport
    from evalforge.retrieval import evaluate_retrieval, retriever_provenance_sha256

    evaluation = RetrievalEvaluation.model_validate(_evaluation_payload())
    policy = RetrievalPolicy.model_validate(
        _policy_payload(retriever_provenance_sha256(evaluation.retriever))
    )
    report = evaluate_retrieval(evaluation, policy)
    payload = report.model_dump(mode="json")
    forged_result = {**payload["results"][0], "retrieved_document_count": 0}
    payload["results"] = [forged_result, payload["results"][1]]

    with pytest.raises(ValidationError, match="retrieved result count"):
        RetrievalReport.model_validate(payload)

    forged_rank_payload = report.model_dump(mode="json")
    forged_rank_payload["results"][0] = {
        **forged_rank_payload["results"][0],
        "relevant_document_count": 2,
        "relevant_at_k": 2,
        "first_relevant_rank": 3,
        "precision_at_k": 2 / 3,
        "recall_at_k": 1.0,
        "reciprocal_rank_at_k": 1 / 3,
    }
    with pytest.raises(ValidationError, match="too few ranked positions"):
        RetrievalReport.model_validate(forged_rank_payload)


def test_retrieval_gates_exact_fractions_and_rejects_unapproved_or_incomplete_evidence() -> None:
    from evalforge.contracts import RetrievalEvaluation, RetrievalPolicy
    from evalforge.retrieval import evaluate_retrieval, retriever_provenance_sha256

    evaluation = RetrievalEvaluation.model_validate(_evaluation_payload())
    policy_payload = _policy_payload(retriever_provenance_sha256(evaluation.retriever))
    policy_payload["minimum_precision_at_k"] = {"numerator": 167, "denominator": 500}
    policy = RetrievalPolicy.model_validate(policy_payload)

    report = evaluate_retrieval(evaluation, policy)

    assert report.release_ready is False
    assert [failure.model_dump() for failure in report.gate_failures] == [
        {
            "metric": "precision_at_k",
            "observed_numerator": "1",
            "observed_denominator": "3",
            "required_numerator": 167,
            "required_denominator": 500,
        }
    ]

    with pytest.raises(ValueError, match="provenance is not approved"):
        evaluate_retrieval(
            evaluation,
            RetrievalPolicy.model_validate(
                {**policy_payload, "approved_retriever_sha256": "f" * 64}
            ),
        )
    for required_ids in (
        ["refund-policy"],
        ["refund-policy", "shipping-policy", "unknown"],
        ["shipping-policy", "refund-policy"],
    ):
        with pytest.raises(ValueError, match="case IDs must exactly match"):
            evaluate_retrieval(
                evaluation,
                RetrievalPolicy.model_validate(
                    {**policy_payload, "required_case_ids": required_ids}
                ),
            )


def test_retrieval_contracts_reject_ambiguous_or_coercive_evidence() -> None:
    from evalforge.contracts import RetrievalEvaluation, RetrievalPolicy
    from evalforge.retrieval import retriever_provenance_sha256

    base = _evaluation_payload()
    cases = base["cases"]
    assert isinstance(cases, list)
    invalid_evaluations = (
        {**base, "schema_version": True},
        {**base, "cases": []},
        {**base, "cases": [cases[0], cases[0]]},
        {**base, "query": "sensitive query"},
        {
            **base,
            "cases": [
                {**cases[0], "relevant_document_ids": ["refund-v2", "refund-v2"]},
                cases[1],
            ],
        },
        {
            **base,
            "cases": [
                {**cases[0], "retrieved_document_ids": ["faq", "faq"]},
                cases[1],
            ],
        },
    )
    for payload in invalid_evaluations:
        with pytest.raises(ValidationError):
            RetrievalEvaluation.model_validate(payload)

    evaluation = RetrievalEvaluation.model_validate(base)
    policy = _policy_payload(retriever_provenance_sha256(evaluation.retriever))
    for invalid_policy in (
        {**policy, "schema_version": True},
        {**policy, "cutoff_k": True},
        {**policy, "cutoff_k": "3"},
        {
            **policy,
            "minimum_precision_at_k": {"numerator": 2, "denominator": 6},
        },
        {**policy, "required_case_ids": ["refund-policy", "refund-policy"]},
    ):
        with pytest.raises(ValidationError):
            RetrievalPolicy.model_validate(invalid_policy)


def test_retrieval_artifact_rejects_forged_report_and_content_identity() -> None:
    from evalforge.contracts import RetrievalEvaluation, RetrievalPolicy, RetrievalReport
    from evalforge.provenance import canonical_json_sha256
    from evalforge.retrieval import (
        RetrievalEvaluationArtifact,
        build_retrieval_evaluation_artifact,
        evaluate_retrieval,
        retriever_provenance_sha256,
    )

    evaluation_payload = _evaluation_payload()
    evaluation = RetrievalEvaluation.model_validate(evaluation_payload)
    policy_payload = _policy_payload(retriever_provenance_sha256(evaluation.retriever))
    policy = RetrievalPolicy.model_validate(policy_payload)
    report = evaluate_retrieval(evaluation, policy)
    artifact = build_retrieval_evaluation_artifact(
        report,
        evaluation_sha256=canonical_json_sha256(evaluation_payload),
        policy_sha256=canonical_json_sha256(policy_payload),
    )

    report_payload = report.model_dump(mode="json")
    for forged in (
        {**report_payload, "release_ready": False},
        {**report_payload, "retriever_provenance_sha256": "f" * 64},
        {**report_payload, "cutoff_k": 2},
    ):
        with pytest.raises(ValidationError):
            RetrievalReport.model_validate(forged)

    artifact_payload = artifact.model_dump(mode="json")
    with pytest.raises(ValidationError, match="retrieval_evaluation_id"):
        RetrievalEvaluationArtifact.model_validate(
            {**artifact_payload, "evaluation_sha256": "e" * 64}
        )
    with pytest.raises(ValidationError, match="report_sha256"):
        RetrievalEvaluationArtifact.model_validate({**artifact_payload, "task_id": "forged"})
