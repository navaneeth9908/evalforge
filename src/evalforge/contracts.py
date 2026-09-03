"""Versioned evaluation contracts shared by the engine and future adapters."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationCase(BaseModel):
    """One deterministic expected-output evaluation case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    expected_output: str


class EvaluationSuite(BaseModel):
    """Versioned collection of cases and its release threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    name: str = Field(min_length=1)
    minimum_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

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
