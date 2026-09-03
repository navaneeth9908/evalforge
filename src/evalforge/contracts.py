"""Versioned evaluation contracts shared by the engine and future adapters."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
DatasetIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    ),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CaseIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
    ),
]
PromptText = Annotated[str, StringConstraints(min_length=1, max_length=8192)]
OutputText = Annotated[str, StringConstraints(max_length=65536)]


class DatasetLineage(BaseModel):
    """Human-auditable origin and transformation metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: NonEmptyText
    source_revision: NonEmptyText
    created_by: NonEmptyText
    license: NonEmptyText
    transformations: tuple[NonEmptyText, ...] = Field(min_length=1, max_length=100)


class DatasetManifest(BaseModel):
    """Strict versioned manifest binding lineage to suite content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    dataset_id: DatasetIdentifier
    dataset_version: DatasetIdentifier
    suite_sha256: Sha256Digest
    lineage: DatasetLineage

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


class EvaluationCase(BaseModel):
    """One deterministic expected-output evaluation case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseIdentifier
    prompt: PromptText
    expected_output: OutputText


class EvaluationSuite(BaseModel):
    """Versioned collection of cases and its release threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    name: str = Field(min_length=1, max_length=512)
    minimum_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("minimum_pass_rate", mode="before")
    @classmethod
    def require_numeric_minimum_pass_rate(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("minimum_pass_rate must be a JSON number")
        return value

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("evaluation case IDs must be unique")
        return self


class CaseResult(BaseModel):
    """Auditable outcome for one evaluated case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    expected_output: str
    actual_output: str


class EvaluationReport(BaseModel):
    """Aggregate release decision plus ordered case-level evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    total_cases: int = Field(ge=1)
    passed_cases: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    minimum_pass_rate: float = Field(ge=0.0, le=1.0)
    release_ready: bool
    results: tuple[CaseResult, ...]
