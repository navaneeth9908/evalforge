"""Governed deterministic dataset mutation and adversarial case generation."""

from __future__ import annotations

from typing import Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from evalforge.contracts import (
    CaseIdentifier,
    DatasetIdentifier,
    EvaluationCase,
    EvaluationSuite,
    Sha256Digest,
)
from evalforge.provenance import JsonValue, canonical_json_sha256

MUTATION_CANONICALIZATION_VERSION: Literal["evalforge-json-v1"] = "evalforge-json-v1"
MUTATION_SEMANTICS_VERSION: Literal["deterministic-prompt-mutations-v1"] = (
    "deterministic-prompt-mutations-v1"
)
MutationOperator = Literal[
    "prompt_prefix",
    "prompt_injection_suffix",
    "prompt_character_deletion",
]


class MutationSpec(BaseModel):
    """One explicit deterministic transformation of a source evaluation case."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    mutation_id: DatasetIdentifier
    source_case_id: CaseIdentifier
    operator: MutationOperator
    text: str | None = Field(default=None, min_length=1, max_length=1024)
    character_index: int | None = Field(default=None, ge=0, le=8191, strict=True)

    @model_validator(mode="after")
    def require_operator_parameters(self) -> Self:
        if self.operator == "prompt_character_deletion":
            if self.character_index is None or self.text is not None:
                raise ValueError("character deletion requires only character_index")
        elif self.text is None or self.character_index is not None:
            raise ValueError("text mutation requires only non-empty text")
        return self


class MutationPlan(BaseModel):
    """Versioned campaign bound to one normalized source suite."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1]
    campaign_id: DatasetIdentifier
    source_suite_sha256: Sha256Digest
    mutations: tuple[MutationSpec, ...] = Field(min_length=1, max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("mutations")
    @classmethod
    def require_unique_mutation_ids(
        cls, value: tuple[MutationSpec, ...]
    ) -> tuple[MutationSpec, ...]:
        if len({mutation.mutation_id for mutation in value}) != len(value):
            raise ValueError("mutation IDs must be unique")
        return value


class MutationRecord(BaseModel):
    """Content-redacted provenance for one generated case."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    mutation_id: DatasetIdentifier
    source_case_id: CaseIdentifier
    generated_case_id: CaseIdentifier
    operator: MutationOperator
    source_prompt_sha256: Sha256Digest
    generated_prompt_sha256: Sha256Digest


class MutationArtifact(BaseModel):
    """Generated suite and content-addressed mutation-campaign evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1] = 1
    campaign_id: DatasetIdentifier
    source_suite_sha256: Sha256Digest
    plan_sha256: Sha256Digest
    generated_suite_sha256: Sha256Digest
    canonicalization_version: Literal["evalforge-json-v1"]
    mutation_semantics_version: Literal["deterministic-prompt-mutations-v1"]
    mutation_campaign_id: Sha256Digest
    source_suite: EvaluationSuite
    plan: MutationPlan
    generated_suite: EvaluationSuite
    mutations: tuple[MutationRecord, ...] = Field(min_length=1, max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_serializer("source_suite", "generated_suite")
    def serialize_suite(self, suite: EvaluationSuite) -> dict[str, object]:
        """Preserve the suite contract's exactly-one policy representation."""
        return suite.model_dump(mode="json", exclude_none=True)

    @model_validator(mode="after")
    def require_consistent_content_addressed_evidence(self) -> Self:
        if self.campaign_id != self.plan.campaign_id:
            raise ValueError("campaign_id must match the mutation plan")
        source_digest = suite_sha256(self.source_suite)
        if self.source_suite_sha256 != source_digest:
            raise ValueError("source_suite_sha256 must match the source suite")
        if self.plan.source_suite_sha256 != source_digest:
            raise ValueError("mutation plan must be bound to the source suite")
        expected_plan_sha256 = canonical_json_sha256(
            cast(JsonValue, self.plan.model_dump(mode="json"))
        )
        if self.plan_sha256 != expected_plan_sha256:
            raise ValueError("plan_sha256 must match the mutation plan")
        if self.generated_suite_sha256 != suite_sha256(self.generated_suite):
            raise ValueError("generated_suite_sha256 must match the generated suite")
        generated_ids = tuple(case.case_id for case in self.generated_suite.cases)
        record_ids = tuple(record.generated_case_id for record in self.mutations)
        if record_ids != generated_ids:
            raise ValueError("mutation records must match generated suite case order")
        if len(self.mutations) != len(self.plan.mutations):
            raise ValueError("mutation records must match the mutation plan")
        for mutation, record in zip(self.plan.mutations, self.mutations, strict=True):
            if (
                record.mutation_id != mutation.mutation_id
                or record.source_case_id != mutation.source_case_id
                or record.operator != mutation.operator
            ):
                raise ValueError("mutation records must match the mutation plan")
        expected_suite, expected_records = _generate_suite_and_records(self.source_suite, self.plan)
        if self.generated_suite != expected_suite:
            raise ValueError("generated suite must match the mutation plan")
        if self.mutations != expected_records:
            raise ValueError("mutation records must match generated prompt evidence")
        if len({record.mutation_id for record in self.mutations}) != len(self.mutations):
            raise ValueError("mutation record IDs must be unique")
        expected_id = canonical_json_sha256(
            {
                "canonicalization_version": self.canonicalization_version,
                "generated_suite_sha256": self.generated_suite_sha256,
                "mutation_campaign_id_schema_version": 1,
                "mutation_semantics_version": self.mutation_semantics_version,
                "plan_sha256": self.plan_sha256,
                "source_suite_sha256": self.source_suite_sha256,
            }
        )
        if self.mutation_campaign_id != expected_id:
            raise ValueError("mutation_campaign_id must match its versioned preimage")
        return self


def _validated_suite_snapshot(suite: EvaluationSuite) -> EvaluationSuite:
    if type(suite) is not EvaluationSuite:
        raise TypeError("suite must be an exact EvaluationSuite instance")
    if any(type(case) is not EvaluationCase for case in suite.cases):
        raise TypeError("suite cases must be exact EvaluationCase instances")
    return EvaluationSuite.model_validate(
        EvaluationSuite.__pydantic_serializer__.to_python(suite, mode="python", exclude_none=True)
    )


def _validated_plan_snapshot(plan: MutationPlan) -> MutationPlan:
    if type(plan) is not MutationPlan:
        raise TypeError("plan must be an exact MutationPlan instance")
    if any(type(mutation) is not MutationSpec for mutation in plan.mutations):
        raise TypeError("mutations must be exact MutationSpec instances")
    return MutationPlan.model_validate(
        MutationPlan.__pydantic_serializer__.to_python(plan, mode="python")
    )


def suite_sha256(suite: EvaluationSuite) -> str:
    """Digest the normalized, fully revalidated source suite contract."""
    snapshot = _validated_suite_snapshot(suite)
    return canonical_json_sha256(cast(JsonValue, snapshot.model_dump(mode="json")))


def _mutated_prompt(source_prompt: str, mutation: MutationSpec) -> str:
    if mutation.operator == "prompt_prefix":
        assert mutation.text is not None
        return f"{mutation.text}{source_prompt}"
    if mutation.operator == "prompt_injection_suffix":
        assert mutation.text is not None
        return f"{source_prompt}{mutation.text}"
    assert mutation.character_index is not None
    if mutation.character_index >= len(source_prompt):
        raise ValueError("character_index must identify a source prompt character")
    result = (
        source_prompt[: mutation.character_index] + source_prompt[mutation.character_index + 1 :]
    )
    if not result:
        raise ValueError("character deletion must not create an empty prompt")
    return result


def _generate_suite_and_records(
    source_suite: EvaluationSuite, plan: MutationPlan
) -> tuple[EvaluationSuite, tuple[MutationRecord, ...]]:
    source_cases = {case.case_id: case for case in source_suite.cases}
    generated_cases: list[EvaluationCase] = []
    records: list[MutationRecord] = []
    for mutation in plan.mutations:
        source_case = source_cases.get(mutation.source_case_id)
        if source_case is None:
            raise ValueError("mutation references an unknown source case")
        prompt = _mutated_prompt(source_case.prompt, mutation)
        generated_case_id = f"{source_case.case_id}--{mutation.mutation_id}"
        case_payload = EvaluationCase.__pydantic_serializer__.to_python(source_case, mode="python")
        generated_case = EvaluationCase.model_validate(
            {**case_payload, "case_id": generated_case_id, "prompt": prompt}
        )
        generated_cases.append(generated_case)
        records.append(
            MutationRecord(
                mutation_id=mutation.mutation_id,
                source_case_id=source_case.case_id,
                generated_case_id=generated_case.case_id,
                operator=mutation.operator,
                source_prompt_sha256=canonical_json_sha256(source_case.prompt),
                generated_prompt_sha256=canonical_json_sha256(prompt),
            )
        )

    suite_payload = EvaluationSuite.__pydantic_serializer__.to_python(
        source_suite, mode="python", exclude_none=True
    )
    generated_suite = EvaluationSuite.model_validate(
        {**suite_payload, "cases": tuple(generated_cases)}
    )
    return generated_suite, tuple(records)


def generate_mutation_artifact(
    source_suite: EvaluationSuite, plan: MutationPlan
) -> MutationArtifact:
    """Generate a deterministic suite from an approved explicit mutation plan."""
    source_suite = _validated_suite_snapshot(source_suite)
    plan = _validated_plan_snapshot(plan)
    source_digest = suite_sha256(source_suite)
    if source_digest != plan.source_suite_sha256:
        raise ValueError("mutation plan is not approved for the source suite")

    generated_suite, records = _generate_suite_and_records(source_suite, plan)
    plan_sha256 = canonical_json_sha256(cast(JsonValue, plan.model_dump(mode="json")))
    generated_suite_digest = suite_sha256(generated_suite)
    campaign_preimage: JsonValue = {
        "canonicalization_version": MUTATION_CANONICALIZATION_VERSION,
        "generated_suite_sha256": generated_suite_digest,
        "mutation_campaign_id_schema_version": 1,
        "mutation_semantics_version": MUTATION_SEMANTICS_VERSION,
        "plan_sha256": plan_sha256,
        "source_suite_sha256": source_digest,
    }
    return MutationArtifact(
        campaign_id=plan.campaign_id,
        source_suite_sha256=source_digest,
        plan_sha256=plan_sha256,
        generated_suite_sha256=generated_suite_digest,
        canonicalization_version=MUTATION_CANONICALIZATION_VERSION,
        mutation_semantics_version=MUTATION_SEMANTICS_VERSION,
        mutation_campaign_id=canonical_json_sha256(campaign_preimage),
        source_suite=source_suite,
        plan=plan,
        generated_suite=generated_suite,
        mutations=tuple(records),
    )
