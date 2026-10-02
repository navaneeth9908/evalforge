from __future__ import annotations

import pytest
from pydantic import ValidationError


def _evidence_payload() -> dict[str, object]:
    return {
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
            {"case_id": "hidden-empty", "outcome": "failed"},
        ],
    }


def _policy_payload(approved_harness_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "python-addition",
        "approved_harness_sha256": approved_harness_sha256,
        "required_case_ids": ["public-add", "hidden-empty"],
        "minimum_pass_rate_numerator": 1,
        "minimum_pass_rate_denominator": 2,
    }


def test_code_harness_evaluation_derives_redacted_release_evidence() -> None:
    from evalforge.code_evaluation import (
        evaluate_code_harness,
        harness_provenance_sha256,
    )
    from evalforge.contracts import CodeEvaluationPolicy, CodeHarnessEvidence

    evidence = CodeHarnessEvidence.model_validate(_evidence_payload())
    policy = CodeEvaluationPolicy.model_validate(
        _policy_payload(harness_provenance_sha256(evidence.harness))
    )

    report = evaluate_code_harness(evidence, policy)

    assert report.release_ready is True
    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.pass_rate == 0.5
    assert report.blocking_cases == 0
    assert [result.model_dump() for result in report.results] == [
        {"case_id": "public-add", "outcome": "passed", "passed": True},
        {"case_id": "hidden-empty", "outcome": "failed", "passed": False},
    ]
    serialized = report.model_dump_json()
    assert "source_code" not in serialized
    assert "stdout" not in serialized
    assert report.candidate_artifact_sha256 == "c" * 64
    assert report.gate_failures == ()


def test_code_harness_blocks_infrastructure_outcomes_and_rejects_untrusted_evidence() -> None:
    from evalforge.code_evaluation import (
        evaluate_code_harness,
        harness_provenance_sha256,
    )
    from evalforge.contracts import CodeEvaluationPolicy, CodeHarnessEvidence

    payload = _evidence_payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    cases[1] = {"case_id": "hidden-empty", "outcome": "timeout"}
    evidence = CodeHarnessEvidence.model_validate(payload)
    approved = harness_provenance_sha256(evidence.harness)
    policy_payload = _policy_payload(approved)
    policy_payload["minimum_pass_rate_numerator"] = 0
    policy_payload["minimum_pass_rate_denominator"] = 1
    policy = CodeEvaluationPolicy.model_validate(policy_payload)

    report = evaluate_code_harness(evidence, policy)

    assert report.release_ready is False
    assert report.blocking_cases == 1
    assert [failure.model_dump(mode="json") for failure in report.gate_failures] == [
        {
            "code": "blocking_outcomes_present",
            "case_ids": ["hidden-empty"],
            "observed_passed_cases": None,
            "observed_total_cases": None,
            "required_numerator": None,
            "required_denominator": None,
        }
    ]

    with pytest.raises(ValueError, match="provenance is not approved"):
        evaluate_code_harness(
            evidence,
            CodeEvaluationPolicy.model_validate(
                {**policy_payload, "approved_harness_sha256": "f" * 64}
            ),
        )
    for required_ids in (
        ["public-add"],
        ["public-add", "hidden-empty", "unknown"],
        ["hidden-empty", "public-add"],
    ):
        with pytest.raises(ValueError, match="case IDs must exactly match"):
            evaluate_code_harness(
                evidence,
                CodeEvaluationPolicy.model_validate(
                    {**policy_payload, "required_case_ids": required_ids}
                ),
            )


@pytest.mark.parametrize("outcome", ["error", "timeout", "skipped"])
def test_code_harness_treats_every_infrastructure_outcome_as_blocking(outcome: str) -> None:
    from evalforge.code_evaluation import evaluate_code_harness, harness_provenance_sha256
    from evalforge.contracts import CodeEvaluationPolicy, CodeHarnessEvidence

    payload = _evidence_payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    cases[1] = {"case_id": "hidden-empty", "outcome": outcome}
    evidence = CodeHarnessEvidence.model_validate(payload)
    policy_payload = _policy_payload(harness_provenance_sha256(evidence.harness))
    policy_payload["minimum_pass_rate_numerator"] = 0
    policy = CodeEvaluationPolicy.model_validate(policy_payload)

    report = evaluate_code_harness(evidence, policy)

    assert report.release_ready is False
    assert report.blocking_cases == 1
    assert report.gate_failures[0].case_ids == ("hidden-empty",)


def test_code_harness_contracts_reject_ambiguous_and_forged_evidence() -> None:
    from evalforge.code_evaluation import harness_provenance_sha256
    from evalforge.contracts import (
        CodeCaseEvidence,
        CodeEvaluationPolicy,
        CodeHarnessEvidence,
    )

    base = _evidence_payload()
    invalid_payloads = (
        {**base, "schema_version": True},
        {**base, "candidate_artifact_sha256": "not-a-digest"},
        {**base, "cases": []},
        {**base, "cases": [base["cases"][0], base["cases"][0]]},
        {**base, "source_code": "secret"},
        {
            **base,
            "harness": {**base["harness"], "command": "python candidate.py"},
        },
        {
            **base,
            "cases": [{**base["cases"][0], "stdout": "secret"}],
        },
    )
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            CodeHarnessEvidence.model_validate(payload)

    evidence = CodeHarnessEvidence.model_validate(base)
    approved = harness_provenance_sha256(evidence.harness)
    with pytest.raises(ValidationError):
        CodeEvaluationPolicy.model_validate(
            {**_policy_payload(approved), "minimum_pass_rate_numerator": "1"}
        )

    forged_case = CodeCaseEvidence.model_construct(case_id="forged", outcome="unknown")
    with pytest.raises(ValidationError):
        CodeHarnessEvidence.model_validate({**base, "cases": [forged_case]})


def test_code_report_rejects_forged_decisions_and_content_identity() -> None:
    from evalforge.code_evaluation import (
        CodeEvaluationArtifact,
        build_code_evaluation_artifact,
        evaluate_code_harness,
        harness_provenance_sha256,
    )
    from evalforge.contracts import (
        CodeEvaluationPolicy,
        CodeEvaluationReport,
        CodeHarnessEvidence,
    )
    from evalforge.provenance import canonical_json_sha256

    evidence_payload = _evidence_payload()
    evidence = CodeHarnessEvidence.model_validate(evidence_payload)
    policy_payload = _policy_payload(harness_provenance_sha256(evidence.harness))
    policy = CodeEvaluationPolicy.model_validate(policy_payload)
    report = evaluate_code_harness(evidence, policy)
    artifact = build_code_evaluation_artifact(
        report,
        evidence_sha256=canonical_json_sha256(evidence_payload),
        policy_sha256=canonical_json_sha256(policy_payload),
    )

    report_payload = report.model_dump(mode="json")
    forged_reports = (
        {**report_payload, "release_ready": False},
        {**report_payload, "passed_cases": 2},
        {**report_payload, "pass_rate": 1.0},
        {**report_payload, "blocking_cases": 1},
        {**report_payload, "harness_provenance_sha256": "f" * 64},
        {
            **report_payload,
            "results": [
                {**report_payload["results"][0], "passed": False},
                report_payload["results"][1],
            ],
        },
    )
    for forged in forged_reports:
        with pytest.raises(ValidationError):
            CodeEvaluationReport.model_validate(forged)

    artifact_payload = artifact.model_dump(mode="json")
    assert artifact_payload["report_sha256"] == canonical_json_sha256(
        report.model_dump(mode="json")
    )
    with pytest.raises(ValidationError, match="code_evaluation_id"):
        CodeEvaluationArtifact.model_validate({**artifact_payload, "evidence_sha256": "e" * 64})
    with pytest.raises(ValidationError, match="report_sha256"):
        CodeEvaluationArtifact.model_validate({**artifact_payload, "task_id": "forged-task"})
    forged_report_payload = {**artifact_payload, "task_id": "forged-task"}
    forged_report_payload["report_sha256"] = canonical_json_sha256(
        {
            field_name: forged_report_payload[field_name]
            for field_name in CodeEvaluationReport.model_fields
        }
    )
    with pytest.raises(ValidationError, match="code_evaluation_id"):
        CodeEvaluationArtifact.model_validate(forged_report_payload)


def test_code_pass_rate_gate_uses_exact_rational_thresholds() -> None:
    from evalforge.code_evaluation import evaluate_code_harness, harness_provenance_sha256
    from evalforge.contracts import CodeEvaluationPolicy, CodeHarnessEvidence

    payload = _evidence_payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    cases.append({"case_id": "hidden-negative", "outcome": "passed"})
    evidence = CodeHarnessEvidence.model_validate(payload)
    base = {
        "schema_version": 1,
        "task_id": "python-addition",
        "approved_harness_sha256": harness_provenance_sha256(evidence.harness),
        "required_case_ids": ["public-add", "hidden-empty", "hidden-negative"],
    }

    exact_two_thirds = evaluate_code_harness(
        evidence,
        CodeEvaluationPolicy.model_validate(
            {
                **base,
                "minimum_pass_rate_numerator": 2,
                "minimum_pass_rate_denominator": 3,
            }
        ),
    )
    just_above_two_thirds = evaluate_code_harness(
        evidence,
        CodeEvaluationPolicy.model_validate(
            {
                **base,
                "minimum_pass_rate_numerator": 2_000_000_000_000_001,
                "minimum_pass_rate_denominator": 3_000_000_000_000_001,
            }
        ),
    )

    assert exact_two_thirds.release_ready is True
    assert just_above_two_thirds.release_ready is False
    assert just_above_two_thirds.gate_failures[0].model_dump() == {
        "code": "minimum_pass_rate_not_met",
        "case_ids": (),
        "observed_passed_cases": 2,
        "observed_total_cases": 3,
        "required_numerator": 2_000_000_000_000_001,
        "required_denominator": 3_000_000_000_000_001,
    }
