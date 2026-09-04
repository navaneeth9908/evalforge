"""Versioned evaluation contracts shared by the engine and future adapters."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
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
MetricName = Literal["exact", "contains", "regex"]
ThresholdSource = Literal["case", "metric", "suite", "legacy"]
ScoreThreshold = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


def _require_json_number(value: object, *, field_name: str) -> object:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a JSON number")
    return value


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
    metric: MetricName = "exact"
    threshold: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)

    @field_validator("threshold", mode="before")
    @classmethod
    def require_numeric_threshold(cls, value: object) -> object:
        if value is None:
            return value
        return _require_json_number(value, field_name="threshold")

    @model_validator(mode="after")
    def require_valid_metric_expectation(self) -> Self:
        if self.metric == "contains":
            if not self.expected_output.strip():
                raise ValueError("contains metric requires a non-empty expected output")
            return self
        if self.metric != "regex":
            return self
        pattern = self.expected_output
        if len(pattern) > 256 or any(operator in pattern for operator in "*+{}()|?"):
            raise ValueError("regex metric requires a bounded regex pattern")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("regex metric requires a valid bounded regex pattern") from exc
        return self


class ReleasePolicy(BaseModel):
    """Declarative aggregate and score thresholds for a release decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    default_case_threshold: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    metric_thresholds: dict[MetricName, ScoreThreshold] = Field(default_factory=dict, max_length=3)

    @field_validator("minimum_pass_rate", "default_case_threshold", mode="before")
    @classmethod
    def require_numeric_thresholds(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name
        assert field_name is not None
        return _require_json_number(value, field_name=field_name)

    @field_validator("metric_thresholds", mode="before")
    @classmethod
    def require_numeric_metric_thresholds(cls, value: object) -> object:
        if isinstance(value, dict):
            for threshold in value.values():
                _require_json_number(threshold, field_name="metric threshold")
        return value


class EvaluationSuite(BaseModel):
    """Versioned collection of cases and its release threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    name: str = Field(min_length=1, max_length=512)
    minimum_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    release_policy: ReleasePolicy | None = None
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
        if value is None:
            return value
        return _require_json_number(value, field_name="minimum_pass_rate")

    @model_validator(mode="after")
    def require_unambiguous_policy_and_unique_case_ids(self) -> Self:
        policy_fields = self.model_fields_set & {"minimum_pass_rate", "release_policy"}
        if len(policy_fields) != 1 or (
            self.minimum_pass_rate is None and self.release_policy is None
        ):
            raise ValueError(
                "evaluation suite must provide exactly one of minimum_pass_rate or release_policy"
            )
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("evaluation case IDs must be unique")
        return self

    @property
    def resolved_policy(self) -> ReleasePolicy:
        """Resolve a legacy aggregate threshold to the declarative policy shape."""
        if self.release_policy is not None:
            return self.release_policy
        assert self.minimum_pass_rate is not None
        return ReleasePolicy(minimum_pass_rate=self.minimum_pass_rate)


class MetricEvidence(BaseModel):
    """Normalized values used by a deterministic text metric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalization: Literal["strip_casefold_v1", "strip_regex_ignorecase_v1"] = "strip_casefold_v1"
    normalized_expected: str
    normalized_actual: str


class CaseResult(BaseModel):
    """Auditable outcome for one evaluated case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    metric: MetricName
    threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    threshold_source: ThresholdSource
    evidence: MetricEvidence
    expected_output: str
    actual_output: str


class GateFailure(BaseModel):
    """Machine-readable reason an aggregate release gate failed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["minimum_pass_rate_not_met"]
    observed: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    required: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class EvaluationReport(BaseModel):
    """Aggregate release decision plus ordered case-level evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    total_cases: int = Field(ge=1)
    passed_cases: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    minimum_pass_rate: float = Field(ge=0.0, le=1.0)
    release_ready: bool
    gate_failures: tuple[GateFailure, ...] = ()
    results: tuple[CaseResult, ...]


ComparisonStatus = Literal["improvement", "unchanged", "regression"]
MatrixRole = Literal["baseline", "candidate"]
BudgetScope = Literal["overall", "metric"]
BudgetReason = Literal["absolute", "relative"]


class ComparisonPolicy(BaseModel):
    """Versioned limits for candidate score regressions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    max_absolute_regression: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    max_relative_regression: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("max_absolute_regression", "max_relative_regression", mode="before")
    @classmethod
    def require_numeric_budgets(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name
        assert field_name is not None
        return _require_json_number(value, field_name=field_name)


class CaseComparison(BaseModel):
    """Candidate-minus-baseline evidence for one case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    metric: MetricName
    baseline_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    absolute_delta: float = Field(ge=-1.0, le=1.0)
    relative_delta: float | None
    status: ComparisonStatus


class MetricComparison(BaseModel):
    """Mean candidate-minus-baseline score evidence for one metric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: MetricName
    case_count: int = Field(ge=1)
    baseline_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    absolute_delta: float = Field(ge=-1.0, le=1.0)
    relative_delta: float | None
    status: ComparisonStatus


class MatrixMetric(BaseModel):
    """One deterministic metric cell in a model-matrix row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: MetricName
    score: float = Field(ge=0.0, le=1.0)


class ModelMatrixRow(BaseModel):
    """Stable summary row for one evaluated variant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MatrixRole
    label: NonEmptyText
    pass_rate: float = Field(ge=0.0, le=1.0)
    metrics: tuple[MatrixMetric, ...]


class AblationSummary(BaseModel):
    """Deterministic counts describing candidate changes from baseline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    improved_cases: int = Field(ge=0)
    regressed_cases: int = Field(ge=0)
    unchanged_cases: int = Field(ge=0)


class RegressionBudgetFailure(BaseModel):
    """Evidence showing where a configured regression budget was exceeded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: BudgetScope
    metric: MetricName | None = None
    baseline_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    absolute_regression: float = Field(ge=0.0, le=1.0)
    relative_regression: float = Field(ge=0.0, le=1.0)
    max_absolute_regression: float = Field(ge=0.0, le=1.0)
    max_relative_regression: float = Field(ge=0.0, le=1.0)
    reasons: tuple[BudgetReason, ...] = Field(min_length=1, max_length=2)


class ComparisonReport(BaseModel):
    """Versioned, evidence-rich baseline-versus-candidate decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    baseline_label: NonEmptyText
    candidate_label: NonEmptyText
    baseline: EvaluationReport
    candidate: EvaluationReport
    pass_rate_absolute_delta: float = Field(ge=-1.0, le=1.0)
    pass_rate_relative_delta: float | None
    case_deltas: tuple[CaseComparison, ...]
    metric_deltas: tuple[MetricComparison, ...]
    model_matrix: tuple[ModelMatrixRow, ModelMatrixRow]
    ablation_summary: AblationSummary
    budget_failures: tuple[RegressionBudgetFailure, ...] = ()
    release_ready: bool
