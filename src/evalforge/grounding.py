"""Deterministic lexical grounding and citation evaluation for RAG outputs."""

from __future__ import annotations

import re
import unicodedata

from evalforge.contracts import (
    CitationEvidence,
    GroundingDocumentEvidence,
    GroundingEvaluation,
    GroundingFinding,
    GroundingMetrics,
    GroundingReport,
)

GROUNDING_SEMANTICS_VERSION = f"lexical-grounding-v1-unicode-{unicodedata.unidata_version}"
_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(text)}


def _coverage(subject: set[str], context: set[str]) -> float:
    if not subject:
        return 0.0
    return len(subject & context) / len(subject)


def evaluate_grounding(evaluation: GroundingEvaluation) -> GroundingReport:
    """Evaluate citations and lexical overlap without emitting source text."""
    claims = {claim.claim_id: claim for claim in evaluation.claims}
    claim_tokens = {claim.claim_id: _tokens(claim.text) for claim in evaluation.claims}
    documents = {document.document_id: document for document in evaluation.retrieved_documents}
    document_tokens = {
        document.document_id: _tokens(document.content)
        for document in evaluation.retrieved_documents
    }
    citation_evidence = []
    findings = []
    cited_claims: set[str] = set()
    supported_claims: set[str] = set()
    utilized_documents: set[str] = set()

    for citation in evaluation.citations:
        claim = claims.get(citation.claim_id)
        document = documents.get(citation.document_id)
        valid = claim is not None and document is not None
        if claim is None or document is None:
            lexical_support = 0.0
        else:
            lexical_support = _coverage(
                claim_tokens[citation.claim_id], document_tokens[citation.document_id]
            )
        supported = valid and lexical_support == 1.0
        if valid:
            cited_claims.add(citation.claim_id)
            utilized_documents.add(citation.document_id)
        if supported:
            supported_claims.add(citation.claim_id)
        if claim is None:
            findings.append(
                GroundingFinding(
                    code="invalid_claim_reference",
                    claim_id=citation.claim_id,
                    document_id=citation.document_id,
                )
            )
        if document is None:
            findings.append(
                GroundingFinding(
                    code="invalid_document_reference",
                    claim_id=citation.claim_id,
                    document_id=citation.document_id,
                )
            )
        if valid and not supported:
            findings.append(
                GroundingFinding(
                    code="unsupported_citation",
                    claim_id=citation.claim_id,
                    document_id=citation.document_id,
                )
            )
        citation_evidence.append(
            CitationEvidence(
                claim_id=citation.claim_id,
                document_id=citation.document_id,
                valid=valid,
                lexical_support=lexical_support,
                supported=supported,
            )
        )

    for claim in evaluation.claims:
        if claim.claim_id not in cited_claims:
            findings.append(GroundingFinding(code="uncited_claim", claim_id=claim.claim_id))

    citation_count = len(evaluation.citations)
    cited_document_ids = {citation.document_id for citation in evaluation.citations}
    document_evidence = tuple(
        GroundingDocumentEvidence(
            document_id=document.document_id,
            cited=document.document_id in cited_document_ids,
            utilized=document.document_id in utilized_documents,
        )
        for document in evaluation.retrieved_documents
    )
    valid_citation_count = sum(item.valid for item in citation_evidence)
    supported_citation_count = sum(item.supported for item in citation_evidence)
    context_tokens: set[str] = set()
    for tokens in document_tokens.values():
        context_tokens.update(tokens)
    lexical_grounding = _coverage(_tokens(evaluation.answer), context_tokens)
    citation_validity = valid_citation_count / citation_count if citation_count else 0.0
    citation_precision = supported_citation_count / citation_count if citation_count else 0.0
    citation_recall = len(supported_claims) / len(evaluation.claims)

    metrics = GroundingMetrics(
        claim_count=len(evaluation.claims),
        retrieved_document_count=len(evaluation.retrieved_documents),
        citation_count=citation_count,
        valid_citation_count=valid_citation_count,
        supported_citation_count=supported_citation_count,
        supported_claim_count=len(supported_claims),
        utilized_document_count=len(utilized_documents),
        citation_validity=citation_validity,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        context_utilization=len(utilized_documents) / len(evaluation.retrieved_documents),
        lexical_grounding=lexical_grounding,
    )
    return GroundingReport(
        metrics=metrics,
        documents=document_evidence,
        citations=tuple(citation_evidence),
        findings=tuple(findings),
        release_ready=(
            citation_validity == 1.0
            and citation_precision == 1.0
            and citation_recall == 1.0
            and lexical_grounding == 1.0
        ),
    )
