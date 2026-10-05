"""Governed, deterministic validation of structured JSON outputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Any, Literal, Self, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from referencing import Registry

from evalforge.contracts import (
    MAX_SAFE_JSON_INTEGER,
    CaseIdentifier,
    DatasetIdentifier,
    ScoreThreshold,
    Sha256Digest,
)
from evalforge.provenance import (
    CANONICALIZATION_VERSION,
    JsonValue,
    canonical_json_bytes,
    canonical_json_sha256,
)

STRUCTURED_OUTPUT_SEMANTICS_VERSION = "bounded-json-schema-2020-12-v1"
MAX_SCHEMA_ERRORS_PER_CASE = 100
_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
_SCHEMA_TYPES = frozenset(("array", "boolean", "integer", "null", "number", "object", "string"))
_ALLOWED_SCHEMA_KEYWORDS = frozenset(
    (
        "$schema",
        "additionalProperties",
        "const",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "properties",
        "required",
        "type",
        "uniqueItems",
    )
)
StructuredOutputValidator = Literal[
    "additionalProperties",
    "const",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "required",
    "type",
    "uniqueItems",
]
_VALIDATOR_NAMES = frozenset(
    (
        "additionalProperties",
        "const",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "required",
        "type",
        "uniqueItems",
    )
)


_OFFLINE_SCHEMA_REGISTRY: Registry[bool | Mapping[str, Any]] = Registry()


def _require_json_value(value: object) -> object:
    """Reject Python-only values and enforce the canonical JSON safety boundary."""
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 100_000 or depth > 64:
            raise ValueError("structured JSON exceeds safety limits")
        if item is None or type(item) in (bool, int):
            continue
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("structured JSON numbers must be finite")
            continue
        if isinstance(item, str):
            if len(item) > 65_536:
                raise ValueError("structured JSON string exceeds length limit")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("structured JSON contains invalid Unicode") from exc
            continue
        if isinstance(item, list):
            if len(item) > 1000:
                raise ValueError("structured JSON array exceeds item limit")
            stack.extend((child, depth + 1) for child in item)
            continue
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            if len(item) > 1000:
                raise ValueError("structured JSON object exceeds property limit")
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
            continue
        raise ValueError("structured output evidence must contain only JSON values")
    canonical_json_bytes(cast(JsonValue, value))
    return value


def _strict_non_negative_integer(value: object, keyword: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_JSON_INTEGER:
        raise ValueError(f"{keyword} must be a non-negative safe integer")
    return value


def _strict_number(value: object, keyword: str) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(cast(float, value)):
        raise ValueError(f"{keyword} must be a finite JSON number")
    return cast(int | float, value)


def _validate_schema_node(schema: object, *, root: bool = False) -> None:
    if not isinstance(schema, dict) or not all(isinstance(key, str) for key in schema):
        raise ValueError("structured-output schemas must be JSON objects")
    unknown = set(schema) - _ALLOWED_SCHEMA_KEYWORDS
    if unknown:
        raise ValueError("structured-output schema contains an unsupported keyword")
    if not {"type", "const", "enum"} & set(schema):
        raise ValueError("structured-output schema must contain a validation assertion")
    if not root and "$schema" in schema:
        raise ValueError("$schema is permitted only at the schema root")
    if "$schema" in schema and schema["$schema"] != _SCHEMA_URI:
        raise ValueError("structured-output schema must use Draft 2020-12")
    if "type" in schema and schema["type"] not in _SCHEMA_TYPES:
        raise ValueError("structured-output schema type must use the bounded profile")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict) or not all(isinstance(key, str) for key in properties):
            raise ValueError("schema properties must map strings to schemas")
        if len(properties) > 1000 or any(len(key) > 256 for key in properties):
            raise ValueError("schema properties exceed bounded profile limits")
        for child in properties.values():
            _validate_schema_node(child)

    required = schema.get("required")
    if required is not None:
        if (
            not isinstance(required, list)
            or not required
            or len(required) > 1000
            or not all(isinstance(item, str) for item in required)
            or len(set(required)) != len(required)
        ):
            raise ValueError("required must contain unique property names")
        if not isinstance(properties, dict) or not set(required) <= set(properties):
            raise ValueError("required names must be declared properties")

    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        raise ValueError("additionalProperties must be a Boolean in the bounded profile")
    if "items" in schema:
        _validate_schema_node(schema["items"])
    if "uniqueItems" in schema and type(schema["uniqueItems"]) is not bool:
        raise ValueError("uniqueItems must be a Boolean")

    integer_pairs = (
        ("minItems", "maxItems"),
        ("minLength", "maxLength"),
        ("minProperties", "maxProperties"),
    )
    for minimum_name, maximum_name in integer_pairs:
        minimum = (
            _strict_non_negative_integer(schema[minimum_name], minimum_name)
            if minimum_name in schema
            else None
        )
        maximum = (
            _strict_non_negative_integer(schema[maximum_name], maximum_name)
            if maximum_name in schema
            else None
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{minimum_name} must not exceed {maximum_name}")

    for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if keyword in schema:
            _strict_number(schema[keyword], keyword)
    if (
        "minimum" in schema
        and "maximum" in schema
        and _strict_number(schema["minimum"], "minimum")
        > _strict_number(schema["maximum"], "maximum")
    ):
        raise ValueError("minimum must not exceed maximum")

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not 1 <= len(enum) <= 1000:
            raise ValueError("enum must contain between 1 and 1000 JSON values")
        fingerprints = [canonical_json_bytes(cast(JsonValue, item)) for item in enum]
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("enum values must be unique")


def _validate_schema_profile(value: object) -> object:
    _require_json_value(value)
    _validate_schema_node(value, root=True)
    try:
        Draft202012Validator.check_schema(cast(dict[str, object], value))
    except SchemaError as exc:
        raise ValueError("structured-output schema is invalid") from exc
    return value


class StructuredOutputCase(BaseModel):
    """One schema and candidate JSON value evaluated without retaining raw content."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        validate_by_alias=True,
        validate_by_name=True,
    )

    case_id: CaseIdentifier
    output_schema: dict[str, object] = Field(alias="schema")
    candidate: object

    _validate_schema = field_validator("output_schema", mode="before")(_validate_schema_profile)
    _validate_candidate = field_validator("candidate", mode="before")(_require_json_value)


class StructuredOutputEvaluation(BaseModel):
    """Versioned, bounded structured-output cases."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1]
    cases: tuple[StructuredOutputCase, ...] = Field(min_length=1, max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("cases")
    @classmethod
    def require_unique_case_ids(
        cls, value: tuple[StructuredOutputCase, ...]
    ) -> tuple[StructuredOutputCase, ...]:
        if len({case.case_id for case in value}) != len(value):
            raise ValueError("structured-output case IDs must be unique")
        return value


class StructuredOutputPolicy(BaseModel):
    """Approved schema catalog, exact case accounting, and exact release threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1]
    task_id: DatasetIdentifier
    approved_schema_catalog_sha256: Sha256Digest
    required_case_ids: tuple[CaseIdentifier, ...] = Field(min_length=1, max_length=10000)
    minimum_pass_rate_numerator: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER, strict=True)
    minimum_pass_rate_denominator: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER, strict=True)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_canonical_threshold_and_unique_cases(self) -> Self:
        threshold = Fraction(self.minimum_pass_rate_numerator, self.minimum_pass_rate_denominator)
        if not 0 <= threshold <= 1:
            raise ValueError("minimum structured-output pass rate must be in the unit interval")
        if (threshold.numerator, threshold.denominator) != (
            self.minimum_pass_rate_numerator,
            self.minimum_pass_rate_denominator,
        ):
            raise ValueError("minimum structured-output pass rate must be in lowest terms")
        if len(set(self.required_case_ids)) != len(self.required_case_ids):
            raise ValueError("required structured-output case IDs must be unique")
        return self


class StructuredOutputCaseResult(BaseModel):
    """Value-redacted schema-validation evidence for one case."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    case_id: CaseIdentifier
    schema_sha256: Sha256Digest
    candidate_sha256: Sha256Digest
    valid: bool = Field(strict=True)
    violation_count: int = Field(ge=0, le=MAX_SCHEMA_ERRORS_PER_CASE, strict=True)
    violations_truncated: bool = Field(strict=True)
    validators: tuple[StructuredOutputValidator, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def require_consistent_validation_evidence(self) -> Self:
        if tuple(sorted(set(self.validators))) != self.validators:
            raise ValueError("structured-output validator names must be sorted and unique")
        if self.valid != (self.violation_count == 0 and not self.violations_truncated):
            raise ValueError("structured-output validity must match violation evidence")
        if self.valid and self.validators:
            raise ValueError("valid structured outputs must not contain validators")
        if not self.valid and not self.validators:
            raise ValueError("invalid structured outputs require validator evidence")
        if self.violations_truncated and self.violation_count != MAX_SCHEMA_ERRORS_PER_CASE:
            raise ValueError("truncated violations must reach the diagnostic cap")
        return self


class StructuredOutputGateFailure(BaseModel):
    """Exact evidence for a failed structured-output pass-rate gate."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    code: Literal["minimum_pass_rate_not_met"]
    observed_valid_cases: int = Field(ge=0, le=10000, strict=True)
    observed_total_cases: int = Field(ge=1, le=10000, strict=True)
    required_numerator: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER, strict=True)
    required_denominator: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER, strict=True)


class StructuredOutputReport(BaseModel):
    """Versioned, content-redacted structured-output release evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1] = 1
    task_id: DatasetIdentifier
    schema_catalog_sha256: Sha256Digest
    required_case_ids: tuple[CaseIdentifier, ...]
    minimum_pass_rate_numerator: int = Field(ge=0, le=MAX_SAFE_JSON_INTEGER, strict=True)
    minimum_pass_rate_denominator: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER, strict=True)
    total_cases: int = Field(ge=1, le=10000, strict=True)
    valid_cases: int = Field(ge=0, le=10000, strict=True)
    pass_rate: ScoreThreshold
    results: tuple[StructuredOutputCaseResult, ...]
    gate_failures: tuple[StructuredOutputGateFailure, ...] = ()
    release_ready: bool = Field(strict=True)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("pass_rate", mode="before")
    @classmethod
    def require_numeric_pass_rate(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("pass_rate must be a JSON number")
        return value

    @model_validator(mode="after")
    def require_consistent_release_evidence(self) -> Self:
        if self.total_cases != len(self.results):
            raise ValueError("total_cases must equal the number of structured-output results")
        result_ids = tuple(result.case_id for result in self.results)
        if result_ids != self.required_case_ids:
            raise ValueError("structured-output result IDs must match required case order")
        expected_catalog_sha256 = canonical_json_sha256(
            [
                {"case_id": result.case_id, "schema_sha256": result.schema_sha256}
                for result in self.results
            ]
        )
        if self.schema_catalog_sha256 != expected_catalog_sha256:
            raise ValueError("schema_catalog_sha256 must match ordered result schema digests")
        valid_cases = sum(result.valid for result in self.results)
        if self.valid_cases != valid_cases:
            raise ValueError("valid_cases must equal the number of valid structured outputs")
        if self.pass_rate != valid_cases / self.total_cases:
            raise ValueError("pass_rate must equal valid_cases divided by total_cases")
        threshold = Fraction(self.minimum_pass_rate_numerator, self.minimum_pass_rate_denominator)
        if not 0 <= threshold <= 1 or (threshold.numerator, threshold.denominator) != (
            self.minimum_pass_rate_numerator,
            self.minimum_pass_rate_denominator,
        ):
            raise ValueError("structured-output threshold must be canonical")
        failed = valid_cases * self.minimum_pass_rate_denominator < (
            self.minimum_pass_rate_numerator * self.total_cases
        )
        expected_failures: tuple[StructuredOutputGateFailure, ...] = ()
        if failed:
            expected_failures = (
                StructuredOutputGateFailure(
                    code="minimum_pass_rate_not_met",
                    observed_valid_cases=valid_cases,
                    observed_total_cases=self.total_cases,
                    required_numerator=self.minimum_pass_rate_numerator,
                    required_denominator=self.minimum_pass_rate_denominator,
                ),
            )
        if self.gate_failures != expected_failures:
            raise ValueError("structured-output gate failures must match derived evidence")
        if self.release_ready != (not failed):
            raise ValueError("release_ready must match the structured-output gate")
        return self


class StructuredOutputArtifact(StructuredOutputReport):
    """Structured-output report bound to raw inputs and fixed evaluator semantics."""

    evaluation_sha256: Sha256Digest
    policy_sha256: Sha256Digest
    report_sha256: Sha256Digest
    canonicalization_version: Literal["evalforge-json-v1"]
    structured_output_semantics_version: Literal["bounded-json-schema-2020-12-v1"]
    structured_output_evaluation_id: Sha256Digest

    @model_validator(mode="after")
    def require_content_addressed_identity(self) -> Self:
        payload = self.model_dump(mode="json")
        report_payload = {
            field_name: payload[field_name] for field_name in StructuredOutputReport.model_fields
        }
        expected_report_sha256 = canonical_json_sha256(cast(JsonValue, report_payload))
        if self.report_sha256 != expected_report_sha256:
            raise ValueError("report_sha256 must match the serialized structured-output report")
        expected_id = canonical_json_sha256(
            {
                "canonicalization_version": self.canonicalization_version,
                "evaluation_sha256": self.evaluation_sha256,
                "policy_sha256": self.policy_sha256,
                "report_sha256": self.report_sha256,
                "structured_output_evaluation_id_schema_version": 1,
                "structured_output_semantics_version": self.structured_output_semantics_version,
            }
        )
        if self.structured_output_evaluation_id != expected_id:
            raise ValueError("structured_output_evaluation_id must match its versioned preimage")
        return self


def _validated_evaluation_snapshot(
    evaluation: StructuredOutputEvaluation,
) -> StructuredOutputEvaluation:
    """Revalidate and detach nested mutable values at each public evaluation boundary."""
    if type(evaluation) is not StructuredOutputEvaluation:
        raise TypeError("evaluation must be an exact StructuredOutputEvaluation instance")
    if any(type(case) is not StructuredOutputCase for case in evaluation.cases):
        raise TypeError("cases must be exact StructuredOutputCase instances")
    return StructuredOutputEvaluation.model_validate(
        StructuredOutputEvaluation.__pydantic_serializer__.to_python(
            evaluation, mode="python", by_alias=True
        )
    )


def _validated_policy_snapshot(policy: StructuredOutputPolicy) -> StructuredOutputPolicy:
    if type(policy) is not StructuredOutputPolicy:
        raise TypeError("policy must be an exact StructuredOutputPolicy instance")
    return StructuredOutputPolicy.model_validate(
        StructuredOutputPolicy.__pydantic_serializer__.to_python(policy, mode="python")
    )


def _schema_catalog_sha256(evaluation: StructuredOutputEvaluation) -> str:
    catalog: JsonValue = [
        {
            "case_id": case.case_id,
            "schema_sha256": canonical_json_sha256(cast(JsonValue, case.output_schema)),
        }
        for case in evaluation.cases
    ]
    return canonical_json_sha256(catalog)


def schema_catalog_sha256(evaluation: StructuredOutputEvaluation) -> str:
    """Digest the ordered case IDs and complete bounded schemas, excluding candidates."""
    return _schema_catalog_sha256(_validated_evaluation_snapshot(evaluation))


def _canonical_copy(value: object) -> JsonValue:
    return cast(JsonValue, json.loads(canonical_json_bytes(cast(JsonValue, value))))


def evaluate_structured_output(
    evaluation: StructuredOutputEvaluation, policy: StructuredOutputPolicy
) -> StructuredOutputReport:
    """Validate candidate JSON against an approved, bounded Draft 2020-12 profile."""
    evaluation = _validated_evaluation_snapshot(evaluation)
    policy = _validated_policy_snapshot(policy)
    catalog_sha256 = _schema_catalog_sha256(evaluation)
    if catalog_sha256 != policy.approved_schema_catalog_sha256:
        raise ValueError("structured-output schema catalog is not approved by policy")
    observed_case_ids = tuple(case.case_id for case in evaluation.cases)
    if observed_case_ids != policy.required_case_ids:
        raise ValueError("structured-output case IDs must exactly match policy order")

    results: list[StructuredOutputCaseResult] = []
    for case in evaluation.cases:
        canonical_schema = cast(dict[str, object], _canonical_copy(case.output_schema))
        canonical_candidate = _canonical_copy(case.candidate)
        validator = Draft202012Validator(canonical_schema, registry=_OFFLINE_SCHEMA_REGISTRY)
        errors = []
        for error in validator.iter_errors(canonical_candidate):
            errors.append(error)
            if len(errors) > MAX_SCHEMA_ERRORS_PER_CASE:
                break
        validators = cast(
            tuple[StructuredOutputValidator, ...],
            tuple(
                sorted(
                    {
                        cast(str, error.validator)
                        for error in errors
                        if error.validator in _VALIDATOR_NAMES
                    }
                )
            ),
        )
        if errors and not validators:
            raise ValueError("structured-output validation produced unsupported evidence")
        results.append(
            StructuredOutputCaseResult(
                case_id=case.case_id,
                schema_sha256=canonical_json_sha256(cast(JsonValue, case.output_schema)),
                candidate_sha256=canonical_json_sha256(cast(JsonValue, case.candidate)),
                valid=not errors,
                violation_count=min(len(errors), MAX_SCHEMA_ERRORS_PER_CASE),
                violations_truncated=len(errors) > MAX_SCHEMA_ERRORS_PER_CASE,
                validators=validators,
            )
        )

    valid_cases = sum(result.valid for result in results)
    failed = valid_cases * policy.minimum_pass_rate_denominator < (
        policy.minimum_pass_rate_numerator * len(results)
    )
    failures: tuple[StructuredOutputGateFailure, ...] = ()
    if failed:
        failures = (
            StructuredOutputGateFailure(
                code="minimum_pass_rate_not_met",
                observed_valid_cases=valid_cases,
                observed_total_cases=len(results),
                required_numerator=policy.minimum_pass_rate_numerator,
                required_denominator=policy.minimum_pass_rate_denominator,
            ),
        )
    return StructuredOutputReport(
        task_id=policy.task_id,
        schema_catalog_sha256=catalog_sha256,
        required_case_ids=policy.required_case_ids,
        minimum_pass_rate_numerator=policy.minimum_pass_rate_numerator,
        minimum_pass_rate_denominator=policy.minimum_pass_rate_denominator,
        total_cases=len(results),
        valid_cases=valid_cases,
        pass_rate=valid_cases / len(results),
        results=tuple(results),
        gate_failures=failures,
        release_ready=not failed,
    )


def build_structured_output_artifact(
    report: StructuredOutputReport, *, evaluation_sha256: str, policy_sha256: str
) -> StructuredOutputArtifact:
    """Bind redacted structured-output evidence to raw inputs and evaluator semantics."""
    report_sha256 = canonical_json_sha256(cast(JsonValue, report.model_dump(mode="json")))
    identity_preimage: JsonValue = {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "evaluation_sha256": evaluation_sha256,
        "policy_sha256": policy_sha256,
        "report_sha256": report_sha256,
        "structured_output_evaluation_id_schema_version": 1,
        "structured_output_semantics_version": STRUCTURED_OUTPUT_SEMANTICS_VERSION,
    }
    return StructuredOutputArtifact.model_validate(
        {
            **report.model_dump(mode="json"),
            "evaluation_sha256": evaluation_sha256,
            "policy_sha256": policy_sha256,
            "report_sha256": report_sha256,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "structured_output_semantics_version": STRUCTURED_OUTPUT_SEMANTICS_VERSION,
            "structured_output_evaluation_id": canonical_json_sha256(identity_preimage),
        }
    )
