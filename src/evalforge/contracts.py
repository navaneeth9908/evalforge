"""Versioned evaluation contracts shared by the engine and future adapters."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from evalforge.provenance import JsonValue, canonical_json_bytes

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
CaseWeight = Annotated[float, Field(gt=0.0, le=1_000_000.0, allow_inf_nan=False)]
Severity = Literal["low", "medium", "high", "critical"]
SliceLabel = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    ),
]
ToolCallIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
    ),
]
ToolName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z][a-zA-Z0-9._-]*$",
    ),
]

MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
NonNegativeSafeInteger = Annotated[int, Field(ge=0, le=MAX_SAFE_JSON_INTEGER, strict=True)]
Percentile = Annotated[int, Field(ge=1, le=100, strict=True)]


def _require_json_number(value: object, *, field_name: str) -> object:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a JSON number")
    return value


def _require_canonical_json(value: object) -> object:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 100_000 or depth > 64:
            raise ValueError("tool evidence exceeds structural limits")
        if item is None or type(item) in (bool, int, float):
            continue
        if isinstance(item, str):
            if len(item) > 65_536:
                raise ValueError("tool evidence string exceeds length limit")
            continue
        if isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
            continue
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
            continue
        raise ValueError("tool evidence must contain only JSON values")

    canonical_json_bytes(cast(JsonValue, value))
    return value


class ToolCallRequest(BaseModel):
    """Strict request evidence for one AI-agent tool invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: ToolCallIdentifier
    tool: ToolName
    arguments: dict[str, object]

    _validate_arguments = field_validator("arguments", mode="before")(_require_canonical_json)


class ToolCallResult(BaseModel):
    """Strict result evidence paired to a tool request by call ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: ToolCallIdentifier
    result: object

    _validate_result = field_validator("result", mode="before")(_require_canonical_json)


class ToolCall(BaseModel):
    """One complete request/result exchange."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: ToolCallRequest
    result: ToolCallResult

    @model_validator(mode="after")
    def require_matching_call_ids(self) -> Self:
        if self.request.call_id != self.result.call_id:
            raise ValueError("tool request and result call IDs must match")
        return self


class AgentToolTrace(BaseModel):
    """Versioned ordered tool exchanges emitted by one agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    calls: tuple[ToolCall, ...] = Field(max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("calls")
    @classmethod
    def require_unique_call_ids(cls, value: tuple[ToolCall, ...]) -> tuple[ToolCall, ...]:
        call_ids = [call.request.call_id for call in value]
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("tool call IDs must be unique")
        return value


class ExpectedToolCall(BaseModel):
    """Expected tool name, arguments, and result without a runtime call ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: ToolName
    arguments: dict[str, object]
    result: object

    _validate_arguments = field_validator("arguments", mode="before")(_require_canonical_json)
    _validate_result = field_validator("result", mode="before")(_require_canonical_json)


class ToolTraceExpectation(BaseModel):
    """Strict expected-call contract and tool allowlist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    allowed_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=1000)
    expected_calls: tuple[ExpectedToolCall, ...] = Field(min_length=1, max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def require_unique_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allowed tools must be unique")
        return value

    @model_validator(mode="after")
    def require_expected_tools_to_be_allowed(self) -> Self:
        if any(call.tool not in self.allowed_tools for call in self.expected_calls):
            raise ValueError("every expected tool must be allowed")
        return self


class ToolTraceCallEvidence(BaseModel):
    """Redaction-safe evidence for one observed exchange."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_index: int = Field(ge=0, strict=True)
    call_id: ToolCallIdentifier
    tool: ToolName
    expected_index: int | None = Field(default=None, ge=0, strict=True)
    argument_sha256: Sha256Digest
    result_sha256: Sha256Digest
    tool_matched: bool
    arguments_matched: bool
    result_matched: bool


class ToolTraceMetrics(BaseModel):
    """Deterministic matching counts and rates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_call_count: int = Field(ge=1, strict=True)
    actual_call_count: int = Field(ge=0, strict=True)
    tool_match_count: int = Field(ge=0, strict=True)
    argument_match_count: int = Field(ge=0, strict=True)
    result_match_count: int = Field(ge=0, strict=True)
    complete_match_count: int = Field(ge=0, strict=True)
    precision: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    recall: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


ToolTraceFindingCode = Literal[
    "missing_call",
    "extra_call",
    "repeated_call",
    "disallowed_call",
    "argument_mismatch",
    "result_mismatch",
]


class ToolTraceFinding(BaseModel):
    """Redaction-safe reason a tool-trace gate failed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ToolTraceFindingCode
    tool: ToolName
    expected_index: int | None = Field(default=None, ge=0, strict=True)
    actual_index: int | None = Field(default=None, ge=0, strict=True)
    expected_sha256: Sha256Digest | None = None
    actual_sha256: Sha256Digest | None = None


class ToolTraceReport(BaseModel):
    """Versioned deterministic tool-trace verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    metrics: ToolTraceMetrics
    calls: tuple[ToolTraceCallEvidence, ...]
    findings: tuple[ToolTraceFinding, ...] = ()
    release_ready: bool


StateName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z][a-zA-Z0-9._-]*$",
    ),
]


class StateTransition(BaseModel):
    """One directed state transition used by a trajectory policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_state: StateName
    to_state: StateName


class TrajectoryPolicy(BaseModel):
    """Versioned ordered transition requirements for one agent trajectory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    initial_state: StateName
    terminal_states: tuple[StateName, ...] = Field(min_length=1, max_length=1000)
    required_transitions: tuple[StateTransition, ...] = Field(min_length=1, max_length=10000)
    forbidden_transitions: tuple[StateTransition, ...] = Field(default=(), max_length=10000)
    allow_loops: bool = Field(default=False, strict=True)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_unambiguous_state_rules(self) -> Self:
        if len(set(self.terminal_states)) != len(self.terminal_states):
            raise ValueError("terminal states must be unique")
        required = [
            (transition.from_state, transition.to_state) for transition in self.required_transitions
        ]
        forbidden = [
            (transition.from_state, transition.to_state)
            for transition in self.forbidden_transitions
        ]
        if len(set(required)) != len(required):
            raise ValueError("required transitions must be unique")
        if len(set(forbidden)) != len(forbidden):
            raise ValueError("forbidden transitions must be unique")
        if set(required) & set(forbidden):
            raise ValueError("required and forbidden transitions must not overlap")
        return self


class TrajectoryStep(BaseModel):
    """One ordered state transition observed during an agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: ToolCallIdentifier
    state_before: StateName
    action: ToolName
    state_after: StateName
    terminated: bool = Field(default=False, strict=True)


class AgentTrajectory(BaseModel):
    """Versioned sequence of state transitions emitted by one agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    steps: tuple[TrajectoryStep, ...] = Field(min_length=1, max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("steps")
    @classmethod
    def require_unique_step_ids(
        cls, value: tuple[TrajectoryStep, ...]
    ) -> tuple[TrajectoryStep, ...]:
        if len({step.step_id for step in value}) != len(value):
            raise ValueError("trajectory step IDs must be unique")
        return value


class TrajectoryMetrics(BaseModel):
    """Deterministic ordering, termination, and cycle measurements."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_count: int = Field(ge=1, strict=True)
    ordered_step_count: int = Field(ge=0, strict=True)
    required_transition_count: int = Field(ge=1, strict=True)
    observed_required_transition_count: int = Field(ge=0, strict=True)
    forbidden_transition_count: int = Field(ge=0, strict=True)
    termination_violation_count: int = Field(ge=0, strict=True)
    loop_count: int = Field(ge=0, strict=True)
    sequence_score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    termination_score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


TrajectoryFindingCode = Literal[
    "initial_state_mismatch",
    "sequence_mismatch",
    "missing_transition",
    "unexpected_transition",
    "state_discontinuity",
    "forbidden_transition",
    "premature_termination",
    "unterminated",
    "loop_detected",
]


class TrajectoryFinding(BaseModel):
    """Explainable state and index evidence for a trajectory-gate failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: TrajectoryFindingCode
    expected_index: int | None = Field(default=None, ge=0, strict=True)
    actual_index: int | None = Field(default=None, ge=0, strict=True)
    expected_from_state: StateName | None = None
    expected_to_state: StateName | None = None
    actual_from_state: StateName | None = None
    actual_to_state: StateName | None = None


class TrajectoryReport(BaseModel):
    """Versioned deterministic trajectory-policy verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    metrics: TrajectoryMetrics
    findings: tuple[TrajectoryFinding, ...] = ()
    release_ready: bool


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
    weight: CaseWeight = 1.0
    severity: Severity = "medium"
    category: SliceLabel = "uncategorized"
    tags: tuple[SliceLabel, ...] = Field(default=(), max_length=20)

    @field_validator("threshold", mode="before")
    @classmethod
    def require_numeric_threshold(cls, value: object) -> object:
        if value is None:
            return value
        return _require_json_number(value, field_name="threshold")

    @field_validator("weight", mode="before")
    @classmethod
    def require_numeric_weight(cls, value: object) -> object:
        return _require_json_number(value, field_name="weight")

    @field_validator("tags")
    @classmethod
    def require_unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evaluation case tags must be unique")
        return value

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


class CasePerformance(BaseModel):
    """Integer-safe latency and cost evidence for one candidate output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    latency_ms: NonNegativeSafeInteger
    cost_micro_usd: NonNegativeSafeInteger


class CandidateOutput(CasePerformance):
    """Candidate text paired with required performance evidence."""

    output: OutputText


RunIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
    ),
]


class ObservationRun(BaseModel):
    """One named observation of outputs for every suite case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: RunIdentifier
    outputs: dict[CaseIdentifier, str | CandidateOutput] = Field(min_length=1, max_length=10000)


class RepeatedObservations(BaseModel):
    """Versioned collection of repeated candidate observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    runs: tuple[ObservationRun, ...] = Field(min_length=1, max_length=1000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("runs")
    @classmethod
    def require_unique_run_ids(
        cls, value: tuple[ObservationRun, ...]
    ) -> tuple[ObservationRun, ...]:
        if len({run.run_id for run in value}) != len(value):
            raise ValueError("observation run IDs must be unique")
        return value


class StabilityPolicy(BaseModel):
    """Versioned limits for repeated-run score variance and flaky outcomes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    max_weighted_pass_rate_variance: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    max_flaky_case_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("max_weighted_pass_rate_variance", "max_flaky_case_rate", mode="before")
    @classmethod
    def require_numeric_limits(cls, value: object, info: ValidationInfo) -> object:
        field_name = info.field_name
        assert field_name is not None
        return _require_json_number(value, field_name=field_name)


class LatencyPercentileBudget(BaseModel):
    """Inclusive nearest-rank latency percentile limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    percentile: Percentile
    max_latency_ms: NonNegativeSafeInteger


class CostPercentileBudget(BaseModel):
    """Inclusive nearest-rank cost percentile limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    percentile: Percentile
    max_cost_micro_usd: NonNegativeSafeInteger


class ResourceBudgets(BaseModel):
    """Inclusive suite limits for deterministic latency and cost aggregates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_total_latency_ms: NonNegativeSafeInteger | None = None
    max_average_latency_ms: NonNegativeSafeInteger | None = None
    latency_percentile: LatencyPercentileBudget | None = None
    max_total_cost_micro_usd: NonNegativeSafeInteger | None = None
    max_average_cost_micro_usd: NonNegativeSafeInteger | None = None
    cost_percentile: CostPercentileBudget | None = None

    @model_validator(mode="after")
    def require_at_least_one_limit(self) -> Self:
        if not any(value is not None for value in self.__dict__.values()):
            raise ValueError("resource budgets must configure at least one limit")
        return self


class ReleasePolicy(BaseModel):
    """Declarative aggregate and score thresholds for a release decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    default_case_threshold: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    metric_thresholds: dict[MetricName, ScoreThreshold] = Field(default_factory=dict, max_length=3)
    blocking_severities: tuple[Severity, ...] = ("critical",)
    resource_budgets: ResourceBudgets | None = None

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

    @field_validator("blocking_severities")
    @classmethod
    def require_unique_blocking_severities(
        cls, value: tuple[Severity, ...]
    ) -> tuple[Severity, ...]:
        if len(set(value)) != len(value):
            raise ValueError("blocking severities must be unique")
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
    weight: CaseWeight
    severity: Severity
    category: SliceLabel
    tags: tuple[SliceLabel, ...]
    evidence: MetricEvidence
    expected_output: str
    actual_output: str
    performance: CasePerformance | None = None


class GateFailure(BaseModel):
    """Machine-readable reason an aggregate release gate failed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["minimum_pass_rate_not_met", "release_critical_case_failed"]
    observed: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    required: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class SliceSummary(BaseModel):
    """Deterministic weighted quality summary for one category or tag."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: SliceLabel
    total_cases: int = Field(ge=1)
    passed_cases: int = Field(ge=0)
    total_weight: float = Field(gt=0.0, allow_inf_nan=False)
    passed_weight: float = Field(ge=0.0, allow_inf_nan=False)
    weighted_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class PercentileLatencyEvidence(BaseModel):
    """Nearest-rank latency percentile evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    percentile: Percentile
    observed_latency_ms: NonNegativeSafeInteger


class PercentileCostEvidence(BaseModel):
    """Nearest-rank cost percentile evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    percentile: Percentile
    observed_cost_micro_usd: NonNegativeSafeInteger


class PerformanceSummary(BaseModel):
    """Integer-only aggregate runtime and cost evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_count: int = Field(ge=1, strict=True)
    total_latency_ms: NonNegativeSafeInteger
    average_latency_ms: NonNegativeSafeInteger
    latency_percentile: PercentileLatencyEvidence | None = None
    total_cost_micro_usd: NonNegativeSafeInteger
    average_cost_micro_usd: NonNegativeSafeInteger
    cost_percentile: PercentileCostEvidence | None = None


ResourceGateMetric = Literal[
    "total_latency_ms",
    "average_latency_ms",
    "percentile_latency_ms",
    "total_cost_micro_usd",
    "average_cost_micro_usd",
    "percentile_cost_micro_usd",
]


class ResourceGateFailure(BaseModel):
    """Machine-readable evidence for one exceeded latency or cost budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: ResourceGateMetric
    observed: NonNegativeSafeInteger
    required: NonNegativeSafeInteger
    percentile: Percentile | None = None


class EvaluationReport(BaseModel):
    """Aggregate release decision plus ordered case-level evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[5] = 5
    total_cases: int = Field(ge=1)
    passed_cases: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    total_weight: float = Field(gt=0.0, allow_inf_nan=False)
    passed_weight: float = Field(ge=0.0, allow_inf_nan=False)
    weighted_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    minimum_pass_rate: float = Field(ge=0.0, le=1.0)
    release_ready: bool
    gate_failures: tuple[GateFailure, ...] = ()
    performance: PerformanceSummary | None = None
    resource_gate_failures: tuple[ResourceGateFailure, ...] = ()
    category_slices: tuple[SliceSummary, ...]
    tag_slices: tuple[SliceSummary, ...]
    results: tuple[CaseResult, ...]


class StabilityScoreSummary(BaseModel):
    """Deterministic aggregate statistics across repeated weighted pass rates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mean: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    minimum: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    maximum: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    population_variance: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class CaseStability(BaseModel):
    """Repeated pass/fail evidence for one suite case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseIdentifier
    pass_count: int = Field(ge=0, strict=True)
    fail_count: int = Field(ge=0, strict=True)
    flaky: bool


class RunStability(BaseModel):
    """Suite-gate outcome for one repeated observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: RunIdentifier
    weighted_pass_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    release_ready: bool


StabilityGateMetric = Literal[
    "weighted_pass_rate_population_variance",
    "flaky_case_rate",
]


class StabilityGateFailure(BaseModel):
    """Evidence for one exceeded repeated-run stability limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: StabilityGateMetric
    observed: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    required: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class StabilityReport(BaseModel):
    """Versioned repeated-run stability evidence and release decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_count: int = Field(ge=1, strict=True)
    failed_run_count: int = Field(ge=0, strict=True)
    runs: tuple[RunStability, ...]
    weighted_pass_rate: StabilityScoreSummary
    flaky_case_count: int = Field(ge=0, strict=True)
    flaky_case_rate: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    cases: tuple[CaseStability, ...]
    gate_failures: tuple[StabilityGateFailure, ...] = ()
    release_ready: bool


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
    """Weighted-mean candidate-minus-baseline evidence for one metric."""

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
    weighted_pass_rate: float = Field(ge=0.0, le=1.0)
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

    schema_version: Literal[3] = 3
    baseline_label: NonEmptyText
    candidate_label: NonEmptyText
    baseline: EvaluationReport
    candidate: EvaluationReport
    weighted_pass_rate_absolute_delta: float = Field(ge=-1.0, le=1.0)
    weighted_pass_rate_relative_delta: float | None
    case_deltas: tuple[CaseComparison, ...]
    metric_deltas: tuple[MetricComparison, ...]
    model_matrix: tuple[ModelMatrixRow, ModelMatrixRow]
    ablation_summary: AblationSummary
    budget_failures: tuple[RegressionBudgetFailure, ...] = ()
    release_ready: bool
