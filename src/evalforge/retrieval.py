"""Deterministic ranked-retrieval evaluation over governed evidence."""

from __future__ import annotations

from fractions import Fraction
from typing import Literal, Self, cast

from pydantic import ConfigDict, model_validator

from evalforge.contracts import (
    RationalThreshold,
    RetrievalCaseResult,
    RetrievalEvaluation,
    RetrievalGateFailure,
    RetrievalMetricEvidence,
    RetrievalMetricName,
    RetrievalPolicy,
    RetrievalReport,
    RetrieverProvenance,
    Sha256Digest,
)
from evalforge.provenance import CANONICALIZATION_VERSION, JsonValue, canonical_json_sha256

RETRIEVAL_SEMANTICS_VERSION = "ranked-retrieval-exact-fractions-v1"


class RetrievalEvaluationArtifact(RetrievalReport):
    """Persisted retrieval report bound to validated inputs and fixed semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    evaluation_sha256: Sha256Digest
    policy_sha256: Sha256Digest
    report_sha256: Sha256Digest
    canonicalization_version: Literal["evalforge-json-v1"]
    retrieval_semantics_version: Literal["ranked-retrieval-exact-fractions-v1"]
    retrieval_evaluation_id: Sha256Digest

    @model_validator(mode="after")
    def require_content_addressed_identity(self) -> Self:
        artifact_payload = self.model_dump(mode="json")
        report_payload = {
            field_name: artifact_payload[field_name] for field_name in RetrievalReport.model_fields
        }
        expected_report_sha256 = canonical_json_sha256(cast(JsonValue, report_payload))
        if self.report_sha256 != expected_report_sha256:
            raise ValueError("report_sha256 must match the serialized retrieval report")
        expected_id = canonical_json_sha256(
            {
                "canonicalization_version": self.canonicalization_version,
                "evaluation_sha256": self.evaluation_sha256,
                "policy_sha256": self.policy_sha256,
                "report_sha256": self.report_sha256,
                "retrieval_evaluation_id_schema_version": 1,
                "retrieval_semantics_version": self.retrieval_semantics_version,
            }
        )
        if self.retrieval_evaluation_id != expected_id:
            raise ValueError("retrieval_evaluation_id must match its versioned input preimage")
        return self


def retriever_provenance_sha256(retriever: RetrieverProvenance) -> str:
    """Digest the complete normalized retriever, corpus, and index identity."""
    return canonical_json_sha256(cast(JsonValue, retriever.model_dump(mode="json")))


def _mean(values: list[Fraction]) -> Fraction:
    return sum(values, Fraction()) / len(values)


def _metric_evidence(
    metric: RetrievalMetricName,
    observed: Fraction,
    required: RationalThreshold,
) -> RetrievalMetricEvidence:
    required_fraction = Fraction(required.numerator, required.denominator)
    return RetrievalMetricEvidence(
        metric=metric,
        observed_numerator=str(observed.numerator),
        observed_denominator=str(observed.denominator),
        observed_value=float(observed),
        required_numerator=required.numerator,
        required_denominator=required.denominator,
        passed=observed >= required_fraction,
    )


def evaluate_retrieval(evaluation: RetrievalEvaluation, policy: RetrievalPolicy) -> RetrievalReport:
    """Evaluate ranked retrieval quality without copying document IDs into reports."""
    provenance_sha256 = retriever_provenance_sha256(evaluation.retriever)
    if provenance_sha256 != policy.approved_retriever_sha256:
        raise ValueError("retriever provenance is not approved by policy")
    observed_case_ids = tuple(case.case_id for case in evaluation.cases)
    if observed_case_ids != policy.required_case_ids:
        raise ValueError("retrieval case IDs must exactly match policy order")

    results: list[RetrievalCaseResult] = []
    precision_values: list[Fraction] = []
    recall_values: list[Fraction] = []
    reciprocal_rank_values: list[Fraction] = []
    for case in evaluation.cases:
        relevant_ids = set(case.relevant_document_ids)
        top_k = case.retrieved_document_ids[: policy.cutoff_k]
        hit_ranks = tuple(
            rank for rank, document_id in enumerate(top_k, start=1) if document_id in relevant_ids
        )
        relevant_at_k = len(hit_ranks)
        first_relevant_rank = hit_ranks[0] if hit_ranks else None
        precision = Fraction(relevant_at_k, policy.cutoff_k)
        recall = Fraction(relevant_at_k, len(relevant_ids))
        reciprocal_rank = (
            Fraction(1, first_relevant_rank) if first_relevant_rank is not None else Fraction()
        )
        results.append(
            RetrievalCaseResult(
                case_id=case.case_id,
                relevant_document_count=len(relevant_ids),
                retrieved_document_count=len(case.retrieved_document_ids),
                relevant_at_k=relevant_at_k,
                first_relevant_rank=first_relevant_rank,
                precision_at_k=float(precision),
                recall_at_k=float(recall),
                reciprocal_rank_at_k=float(reciprocal_rank),
            )
        )
        precision_values.append(precision)
        recall_values.append(recall)
        reciprocal_rank_values.append(reciprocal_rank)

    metrics = (
        _metric_evidence("precision_at_k", _mean(precision_values), policy.minimum_precision_at_k),
        _metric_evidence("recall_at_k", _mean(recall_values), policy.minimum_recall_at_k),
        _metric_evidence("mrr_at_k", _mean(reciprocal_rank_values), policy.minimum_mrr_at_k),
    )
    failures = tuple(
        RetrievalGateFailure(
            metric=metric.metric,
            observed_numerator=metric.observed_numerator,
            observed_denominator=metric.observed_denominator,
            required_numerator=metric.required_numerator,
            required_denominator=metric.required_denominator,
        )
        for metric in metrics
        if not metric.passed
    )
    return RetrievalReport(
        task_id=policy.task_id,
        retriever=evaluation.retriever,
        retriever_provenance_sha256=provenance_sha256,
        required_case_ids=policy.required_case_ids,
        cutoff_k=policy.cutoff_k,
        total_cases=len(results),
        results=tuple(results),
        metrics=metrics,
        gate_failures=failures,
        release_ready=not failures,
    )


def build_retrieval_evaluation_artifact(
    report: RetrievalReport, *, evaluation_sha256: str, policy_sha256: str
) -> RetrievalEvaluationArtifact:
    """Bind a validated report to content-addressed inputs and fixed semantics."""
    report_sha256 = canonical_json_sha256(cast(JsonValue, report.model_dump(mode="json")))
    identity_preimage: JsonValue = {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "evaluation_sha256": evaluation_sha256,
        "policy_sha256": policy_sha256,
        "report_sha256": report_sha256,
        "retrieval_evaluation_id_schema_version": 1,
        "retrieval_semantics_version": RETRIEVAL_SEMANTICS_VERSION,
    }
    return RetrievalEvaluationArtifact.model_validate(
        {
            **report.model_dump(mode="json"),
            "evaluation_sha256": evaluation_sha256,
            "policy_sha256": policy_sha256,
            "report_sha256": report_sha256,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "retrieval_semantics_version": RETRIEVAL_SEMANTICS_VERSION,
            "retrieval_evaluation_id": canonical_json_sha256(identity_preimage),
        }
    )
