from __future__ import annotations

import pytest
from pydantic import ValidationError


def _evaluation_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "cases": [
            {
                "case_id": "customer-profile",
                "schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "age": {"type": "integer", "minimum": 18},
                    },
                    "required": ["name", "age"],
                    "additionalProperties": False,
                },
                "candidate": {"name": "Ada", "age": 37},
            },
            {
                "case_id": "support-labels",
                "schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "array",
                    "items": {"type": "string", "maxLength": 12},
                    "minItems": 1,
                    "maxItems": 3,
                    "uniqueItems": True,
                },
                "candidate": ["this-label-is-too-long"],
            },
        ],
    }


def _policy_payload(approved_schema_catalog_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "support-structured-output",
        "approved_schema_catalog_sha256": approved_schema_catalog_sha256,
        "required_case_ids": ["customer-profile", "support-labels"],
        "minimum_pass_rate_numerator": 1,
        "minimum_pass_rate_denominator": 2,
    }


def test_structured_output_evaluation_derives_redacted_schema_evidence() -> None:
    from evalforge.structured_output import (
        StructuredOutputEvaluation,
        StructuredOutputPolicy,
        evaluate_structured_output,
        schema_catalog_sha256,
    )

    evaluation = StructuredOutputEvaluation.model_validate(_evaluation_payload())
    policy = StructuredOutputPolicy.model_validate(
        _policy_payload(schema_catalog_sha256(evaluation))
    )

    report = evaluate_structured_output(evaluation, policy)

    assert report.release_ready is True
    assert report.total_cases == 2
    assert report.valid_cases == 1
    assert report.pass_rate == 0.5
    assert [result.valid for result in report.results] == [True, False]
    assert report.results[0].violation_count == 0
    assert report.results[0].validators == ()
    assert report.results[1].violation_count == 1
    assert report.results[1].validators == ("maxLength",)
    assert all(len(result.schema_sha256) == 64 for result in report.results)
    assert all(len(result.candidate_sha256) == 64 for result in report.results)
    serialized = report.model_dump_json()
    assert "Ada" not in serialized
    assert "this-label-is-too-long" not in serialized
    assert '"candidate"' not in serialized
    assert '"schema"' not in serialized


def test_structured_output_rejects_unapproved_schemas_and_inexact_case_accounting() -> None:
    from evalforge.structured_output import (
        StructuredOutputEvaluation,
        StructuredOutputPolicy,
        evaluate_structured_output,
        schema_catalog_sha256,
    )

    evaluation = StructuredOutputEvaluation.model_validate(_evaluation_payload())
    approved = schema_catalog_sha256(evaluation)
    policy_payload = _policy_payload(approved)

    with pytest.raises(ValueError, match="schema catalog is not approved"):
        evaluate_structured_output(
            evaluation,
            StructuredOutputPolicy.model_validate(
                {**policy_payload, "approved_schema_catalog_sha256": "f" * 64}
            ),
        )

    for required_ids in (
        ["customer-profile"],
        ["support-labels", "customer-profile"],
        ["customer-profile", "support-labels", "unknown"],
    ):
        with pytest.raises(ValueError, match="case IDs must exactly match"):
            evaluate_structured_output(
                evaluation,
                StructuredOutputPolicy.model_validate(
                    {**policy_payload, "required_case_ids": required_ids}
                ),
            )


def test_structured_output_contracts_reject_unsafe_schemas_and_coercion() -> None:
    from evalforge.structured_output import StructuredOutputEvaluation, StructuredOutputPolicy

    base = _evaluation_payload()
    cases = base["cases"]
    assert isinstance(cases, list)
    invalid_evaluations = (
        {**base, "schema_version": True},
        {**base, "cases": []},
        {**base, "cases": [cases[0], cases[0]]},
        {
            **base,
            "cases": [
                cases[0],
                {**cases[1], "candidate": ["label"] * 1001},
            ],
        },
        {
            **base,
            "cases": [
                {**cases[0], "schema": {}},
                cases[1],
            ],
        },
        {
            **base,
            "cases": [
                {**cases[0], "schema": {"$ref": "https://example.com/private-schema"}},
                cases[1],
            ],
        },
        {
            **base,
            "cases": [
                {**cases[0], "schema": {"type": "string", "pattern": "(a+)+$"}},
                cases[1],
            ],
        },
        {
            **base,
            "cases": [
                {
                    **cases[0],
                    "schema": {
                        "type": "integer",
                        "minimum": 9_007_199_254_740_993,
                        "maximum": 9_007_199_254_740_992,
                    },
                },
                cases[1],
            ],
        },
    )
    for payload in invalid_evaluations:
        with pytest.raises(ValidationError):
            StructuredOutputEvaluation.model_validate(payload)

    with pytest.raises(ValidationError):
        StructuredOutputPolicy.model_validate(
            {
                **_policy_payload("a" * 64),
                "minimum_pass_rate_numerator": "1",
            }
        )


def test_structured_output_revalidates_mutated_models_without_remote_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    from evalforge.structured_output import (
        StructuredOutputEvaluation,
        StructuredOutputPolicy,
        evaluate_structured_output,
        schema_catalog_sha256,
    )

    evaluation = StructuredOutputEvaluation.model_validate(_evaluation_payload())
    policy = StructuredOutputPolicy.model_validate(
        _policy_payload(schema_catalog_sha256(evaluation))
    )
    evaluation.cases[0].output_schema.clear()
    evaluation.cases[0].output_schema["$ref"] = "https://attacker.invalid/schema.json"
    requested_urls: list[str] = []

    def record_remote_request(url: object, *args: object, **kwargs: object) -> object:
        requested_urls.append(str(url))
        raise AssertionError("remote schema retrieval must not run")

    monkeypatch.setattr(urllib.request, "urlopen", record_remote_request)

    with pytest.raises(ValidationError):
        evaluate_structured_output(evaluation, policy)
    assert requested_urls == []


def test_structured_output_rejects_constructed_models_at_the_evaluator_boundary() -> None:
    from evalforge.structured_output import (
        StructuredOutputCase,
        StructuredOutputEvaluation,
        StructuredOutputPolicy,
        evaluate_structured_output,
        schema_catalog_sha256,
    )

    valid_evaluation = StructuredOutputEvaluation.model_validate(_evaluation_payload())
    policy = StructuredOutputPolicy.model_validate(
        _policy_payload(schema_catalog_sha256(valid_evaluation))
    )
    forged_case = StructuredOutputCase.model_construct(
        case_id="customer-profile",
        output_schema={},
        candidate="false pass",
    )
    forged_evaluation = StructuredOutputEvaluation.model_construct(
        schema_version=1,
        cases=(forged_case, valid_evaluation.cases[1]),
    )

    with pytest.raises(ValidationError):
        schema_catalog_sha256(forged_evaluation)
    with pytest.raises(ValidationError):
        evaluate_structured_output(forged_evaluation, policy)


def test_structured_output_rejects_model_subclasses_before_serialization() -> None:
    from evalforge.structured_output import (
        StructuredOutputEvaluation,
        StructuredOutputPolicy,
        evaluate_structured_output,
        schema_catalog_sha256,
    )

    class ForgedEvaluation(StructuredOutputEvaluation):
        def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
            return _evaluation_payload()

    valid_evaluation = StructuredOutputEvaluation.model_validate(_evaluation_payload())
    policy = StructuredOutputPolicy.model_validate(
        _policy_payload(schema_catalog_sha256(valid_evaluation))
    )
    forged_evaluation = ForgedEvaluation.model_construct(schema_version=1, cases=())

    with pytest.raises(TypeError, match="exact StructuredOutputEvaluation"):
        schema_catalog_sha256(forged_evaluation)
    with pytest.raises(TypeError, match="exact StructuredOutputEvaluation"):
        evaluate_structured_output(forged_evaluation, policy)


def test_structured_output_uses_trusted_serializers_for_exact_model_snapshots() -> None:
    from evalforge.structured_output import (
        StructuredOutputEvaluation,
        StructuredOutputPolicy,
        evaluate_structured_output,
        schema_catalog_sha256,
    )

    valid_evaluation = StructuredOutputEvaluation.model_validate(_evaluation_payload())
    approved = schema_catalog_sha256(valid_evaluation)
    evaluation_payload = _evaluation_payload()
    cases = evaluation_payload["cases"]
    assert isinstance(cases, list)
    first_case = cases[0]
    assert isinstance(first_case, dict)
    first_case["candidate"] = {"name": "", "age": 1}
    evaluation = StructuredOutputEvaluation.model_validate(evaluation_payload)
    evaluation.__dict__["model_dump"] = lambda *args, **kwargs: _evaluation_payload()
    policy = StructuredOutputPolicy.model_validate(_policy_payload(approved))

    report = evaluate_structured_output(evaluation, policy)

    assert report.release_ready is False
    assert report.valid_cases == 0

    strict_policy = StructuredOutputPolicy.model_validate(
        {
            **_policy_payload(approved),
            "minimum_pass_rate_numerator": 1,
            "minimum_pass_rate_denominator": 1,
        }
    )
    strict_policy.__dict__["model_dump"] = lambda *args, **kwargs: {
        **_policy_payload(approved),
        "minimum_pass_rate_numerator": 0,
        "minimum_pass_rate_denominator": 1,
    }

    report = evaluate_structured_output(valid_evaluation, strict_policy)

    assert report.release_ready is False


def test_structured_output_report_and_artifact_reject_forged_decisions() -> None:
    from evalforge.provenance import canonical_json_sha256
    from evalforge.structured_output import (
        StructuredOutputArtifact,
        StructuredOutputEvaluation,
        StructuredOutputPolicy,
        StructuredOutputReport,
        build_structured_output_artifact,
        evaluate_structured_output,
        schema_catalog_sha256,
    )

    evaluation_payload = _evaluation_payload()
    evaluation = StructuredOutputEvaluation.model_validate(evaluation_payload)
    policy_payload = _policy_payload(schema_catalog_sha256(evaluation))
    policy = StructuredOutputPolicy.model_validate(policy_payload)
    report = evaluate_structured_output(evaluation, policy)
    artifact = build_structured_output_artifact(
        report,
        evaluation_sha256=canonical_json_sha256(evaluation_payload),
        policy_sha256=canonical_json_sha256(policy_payload),
    )

    report_payload = report.model_dump(mode="json")
    for forged in (
        {**report_payload, "release_ready": False},
        {**report_payload, "valid_cases": 2},
        {**report_payload, "pass_rate": 1.0},
        {
            **report_payload,
            "results": [
                {**report_payload["results"][0], "valid": False},
                report_payload["results"][1],
            ],
        },
        {
            **report_payload,
            "results": [
                {**report_payload["results"][0], "schema_sha256": "f" * 64},
                report_payload["results"][1],
            ],
        },
    ):
        with pytest.raises(ValidationError):
            StructuredOutputReport.model_validate(forged)

    artifact_payload = artifact.model_dump(mode="json")
    with pytest.raises(ValidationError, match="structured_output_evaluation_id"):
        StructuredOutputArtifact.model_validate({**artifact_payload, "evaluation_sha256": "e" * 64})
    with pytest.raises(ValidationError, match="report_sha256"):
        StructuredOutputArtifact.model_validate({**artifact_payload, "task_id": "forged-task"})
