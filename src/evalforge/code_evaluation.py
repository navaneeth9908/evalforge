"""Deterministic evaluation of trusted, precomputed code-harness evidence."""

from __future__ import annotations

from typing import Literal, Self, cast

from pydantic import ConfigDict, model_validator

from evalforge.contracts import (
    CodeCaseResult,
    CodeEvaluationPolicy,
    CodeEvaluationReport,
    CodeGateFailure,
    CodeHarnessEvidence,
    CodeHarnessProvenance,
    Sha256Digest,
)
from evalforge.provenance import CANONICALIZATION_VERSION, JsonValue, canonical_json_sha256

CODE_EVALUATION_SEMANTICS_VERSION = "precomputed-code-harness-v1"


class CodeEvaluationArtifact(CodeEvaluationReport):
    """Persisted code report bound to validated input documents and evaluator semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    evidence_sha256: Sha256Digest
    policy_sha256: Sha256Digest
    report_sha256: Sha256Digest
    canonicalization_version: Literal["evalforge-json-v1"]
    code_evaluation_semantics_version: Literal["precomputed-code-harness-v1"]
    code_evaluation_id: Sha256Digest

    @model_validator(mode="after")
    def require_content_addressed_identity(self) -> Self:
        artifact_payload = self.model_dump(mode="json")
        report_payload = {
            field_name: artifact_payload[field_name]
            for field_name in CodeEvaluationReport.model_fields
        }
        expected_report_sha256 = canonical_json_sha256(cast(JsonValue, report_payload))
        if self.report_sha256 != expected_report_sha256:
            raise ValueError("report_sha256 must match the serialized code report")
        expected_id = canonical_json_sha256(
            {
                "canonicalization_version": self.canonicalization_version,
                "code_evaluation_id_schema_version": 1,
                "code_evaluation_semantics_version": self.code_evaluation_semantics_version,
                "evidence_sha256": self.evidence_sha256,
                "policy_sha256": self.policy_sha256,
                "report_sha256": self.report_sha256,
            }
        )
        if self.code_evaluation_id != expected_id:
            raise ValueError("code_evaluation_id must match its versioned input preimage")
        return self


def harness_provenance_sha256(harness: CodeHarnessProvenance) -> str:
    """Digest the complete normalized harness and runtime identity."""
    return canonical_json_sha256(cast(JsonValue, harness.model_dump(mode="json")))


def evaluate_code_harness(
    evidence: CodeHarnessEvidence, policy: CodeEvaluationPolicy
) -> CodeEvaluationReport:
    """Evaluate terminal outcomes without loading or executing candidate code."""
    provenance_sha256 = harness_provenance_sha256(evidence.harness)
    if provenance_sha256 != policy.approved_harness_sha256:
        raise ValueError("code harness provenance is not approved by policy")
    observed_case_ids = tuple(case.case_id for case in evidence.cases)
    if observed_case_ids != policy.required_case_ids:
        raise ValueError("code harness case IDs must exactly match policy order")

    results = tuple(
        CodeCaseResult(
            case_id=case.case_id,
            outcome=case.outcome,
            passed=case.outcome == "passed",
        )
        for case in evidence.cases
    )
    passed_cases = sum(result.passed for result in results)
    blocking_ids = tuple(
        result.case_id for result in results if result.outcome in {"error", "timeout", "skipped"}
    )
    pass_rate = passed_cases / len(results)
    failures: list[CodeGateFailure] = []
    if (
        passed_cases * policy.minimum_pass_rate_denominator
        < policy.minimum_pass_rate_numerator * len(results)
    ):
        failures.append(
            CodeGateFailure(
                code="minimum_pass_rate_not_met",
                observed_passed_cases=passed_cases,
                observed_total_cases=len(results),
                required_numerator=policy.minimum_pass_rate_numerator,
                required_denominator=policy.minimum_pass_rate_denominator,
            )
        )
    if blocking_ids:
        failures.append(CodeGateFailure(code="blocking_outcomes_present", case_ids=blocking_ids))

    return CodeEvaluationReport(
        task_id=policy.task_id,
        candidate_artifact_sha256=evidence.candidate_artifact_sha256,
        harness=evidence.harness,
        harness_provenance_sha256=provenance_sha256,
        minimum_pass_rate_numerator=policy.minimum_pass_rate_numerator,
        minimum_pass_rate_denominator=policy.minimum_pass_rate_denominator,
        total_cases=len(results),
        passed_cases=passed_cases,
        blocking_cases=len(blocking_ids),
        pass_rate=pass_rate,
        results=results,
        gate_failures=tuple(failures),
        release_ready=not failures,
    )


def build_code_evaluation_artifact(
    report: CodeEvaluationReport, *, evidence_sha256: str, policy_sha256: str
) -> CodeEvaluationArtifact:
    """Bind a validated report to content-addressed raw inputs and fixed semantics."""
    report_sha256 = canonical_json_sha256(cast(JsonValue, report.model_dump(mode="json")))
    identity_preimage: JsonValue = {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "code_evaluation_id_schema_version": 1,
        "code_evaluation_semantics_version": CODE_EVALUATION_SEMANTICS_VERSION,
        "evidence_sha256": evidence_sha256,
        "policy_sha256": policy_sha256,
        "report_sha256": report_sha256,
    }
    return CodeEvaluationArtifact.model_validate(
        {
            **report.model_dump(mode="json"),
            "evidence_sha256": evidence_sha256,
            "policy_sha256": policy_sha256,
            "report_sha256": report_sha256,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "code_evaluation_semantics_version": CODE_EVALUATION_SEMANTICS_VERSION,
            "code_evaluation_id": canonical_json_sha256(identity_preimage),
        }
    )
