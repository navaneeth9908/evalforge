"""Governed cross-dataset scorecards and deterministic long-horizon trends."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from typing import Annotated, Literal, Self, cast

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from evalforge.contracts import DatasetIdentifier, Sha256Digest
from evalforge.provenance import JsonValue, canonical_json_sha256

TREND_CANONICALIZATION_VERSION: Literal["evalforge-json-v1"] = "evalforge-json-v1"
TREND_SEMANTICS_VERSION: Literal["cross-dataset-scorecard-v1"] = "cross-dataset-scorecard-v1"
_SCORE_DECIMAL_PLACES = 6
_SCORE_SCALE = 1_000_000
_WEIGHT_DECIMAL_PLACES = 3
_WEIGHT_SCALE = 1_000


def _require_score_granularity(value: float) -> float:
    exponent = Decimal(str(value)).as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -_SCORE_DECIMAL_PLACES:
        raise ValueError("score must use at most six decimal places")
    return value


def _require_weight_granularity(value: float) -> float:
    exponent = Decimal(str(value)).as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -_WEIGHT_DECIMAL_PLACES:
        raise ValueError("weight must be a multiple of 0.001")
    return value


Score = Annotated[
    float,
    Field(ge=0.0, le=1.0, allow_inf_nan=False, strict=True),
    AfterValidator(_require_score_granularity),
]
ComputedScore = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False, strict=True)]
StrictBoolean = Annotated[bool, Field(strict=True)]
Weight = Annotated[
    float,
    Field(ge=0.001, le=1000.0, allow_inf_nan=False, strict=True),
    AfterValidator(_require_weight_granularity),
]


class TrendProducer(BaseModel):
    """Minimal immutable identity for the trusted observation producer."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    producer_id: DatasetIdentifier
    revision: DatasetIdentifier
    artifact_sha256: Sha256Digest


class DatasetObservation(BaseModel):
    """One content-free dataset score produced at a checkpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    dataset_id: DatasetIdentifier
    dataset_version: DatasetIdentifier
    suite_sha256: Sha256Digest
    evaluator_semantics_version: DatasetIdentifier
    score: Score
    release_ready: StrictBoolean


class TrendCheckpoint(BaseModel):
    """An ordered UTC checkpoint covering every governed dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    observed_at: str = Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
        max_length=20,
    )
    datasets: tuple[DatasetObservation, ...] = Field(min_length=1, max_length=1000)

    @field_validator("observed_at")
    @classmethod
    def require_canonical_utc_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("observed_at must be canonical UTC at whole-second precision") from exc
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise ValueError("observed_at must be canonical UTC at whole-second precision")
        return value


class CrossDatasetObservations(BaseModel):
    """Versioned precomputed score history from one governed producer."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1]
    producer: TrendProducer
    checkpoints: tuple[TrendCheckpoint, ...] = Field(min_length=1, max_length=10000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_strictly_increasing_timestamps(self) -> Self:
        timestamps = tuple(checkpoint.observed_at for checkpoint in self.checkpoints)
        if any(current >= following for current, following in pairwise(timestamps)):
            raise ValueError("checkpoint timestamps must be strictly increasing")
        return self


class DatasetTrendPolicy(BaseModel):
    """Stable dataset identity, contribution weight, and drawdown budget."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    dataset_id: DatasetIdentifier
    dataset_version: DatasetIdentifier
    suite_sha256: Sha256Digest
    evaluator_semantics_version: DatasetIdentifier
    weight: Weight
    max_drawdown: Score


class CrossDatasetPolicy(BaseModel):
    """Exact ordered dataset catalog and cross-dataset gate policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1]
    approved_producer_sha256: Sha256Digest
    datasets: tuple[DatasetTrendPolicy, ...] = Field(min_length=1, max_length=1000)
    max_overall_drawdown: Score

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("datasets")
    @classmethod
    def require_unique_dataset_ids(
        cls, value: tuple[DatasetTrendPolicy, ...]
    ) -> tuple[DatasetTrendPolicy, ...]:
        if len({dataset.dataset_id for dataset in value}) != len(value):
            raise ValueError("policy dataset IDs must be unique")
        return value


class CheckpointScore(BaseModel):
    """One timestamped weighted cross-dataset score."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    observed_at: str = Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
        max_length=20,
    )
    overall_score: ComputedScore
    dataset_scores: tuple[Score, ...] = Field(min_length=1, max_length=1000)
    dataset_release_ready: tuple[StrictBoolean, ...] = Field(min_length=1, max_length=1000)

    @field_validator("observed_at")
    @classmethod
    def require_canonical_utc_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("observed_at must be canonical UTC at whole-second precision") from exc
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise ValueError("observed_at must be canonical UTC at whole-second precision")
        return value


class DatasetTrendSummary(BaseModel):
    """Long-horizon summary for one governed dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    dataset_id: DatasetIdentifier
    dataset_version: DatasetIdentifier
    suite_sha256: Sha256Digest
    evaluator_semantics_version: DatasetIdentifier
    weight: Weight
    first_score: Score
    latest_score: Score
    peak_score: Score
    max_drawdown: Score
    max_drawdown_budget: Score
    latest_release_ready: StrictBoolean


class OverallTrendSummary(BaseModel):
    """Long-horizon summary of weighted checkpoint scores."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    first_score: ComputedScore
    latest_score: ComputedScore
    peak_score: ComputedScore
    max_drawdown: ComputedScore
    max_drawdown_budget: Score


class TrendGateFailure(BaseModel):
    """Machine-readable latest-gate or drawdown-budget failure."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    scope: Literal["dataset", "overall"]
    dataset_id: DatasetIdentifier | None = None
    reason: Literal["latest_underlying_gate", "max_drawdown"]
    observed: ComputedScore
    required: Score


class CrossDatasetTrendReport(BaseModel):
    """Content-redacted ordered scorecard and full-history trend evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1] = 1
    checkpoint_count: int = Field(ge=1, le=10000, strict=True)
    checkpoints: tuple[CheckpointScore, ...] = Field(min_length=1, max_length=10000)
    datasets: tuple[DatasetTrendSummary, ...] = Field(min_length=1, max_length=1000)
    overall: OverallTrendSummary
    gate_failures: tuple[TrendGateFailure, ...] = Field(max_length=2001)
    release_ready: bool = Field(strict=True)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_consistent_derived_evidence(self) -> Self:
        if self.checkpoint_count != len(self.checkpoints):
            raise ValueError("trend report checkpoint count must match checkpoints")
        timestamps = tuple(checkpoint.observed_at for checkpoint in self.checkpoints)
        if any(current >= following for current, following in pairwise(timestamps)):
            raise ValueError("trend report checkpoint timestamps must be strictly increasing")
        if len({dataset.dataset_id for dataset in self.datasets}) != len(self.datasets):
            raise ValueError("trend report dataset IDs must be unique")
        weights = tuple(dataset.weight for dataset in self.datasets)
        for checkpoint in self.checkpoints:
            if len(checkpoint.dataset_scores) != len(self.datasets) or len(
                checkpoint.dataset_release_ready
            ) != len(self.datasets):
                raise ValueError("trend report checkpoint dataset evidence must be complete")
            if checkpoint.overall_score != _weighted_score(checkpoint.dataset_scores, weights):
                raise ValueError("trend report checkpoint weighted score must match dataset scores")

        overall_scores = tuple(checkpoint.overall_score for checkpoint in self.checkpoints)
        expected_overall = OverallTrendSummary(
            first_score=overall_scores[0],
            latest_score=overall_scores[-1],
            peak_score=max(overall_scores),
            max_drawdown=_max_drawdown(overall_scores),
            max_drawdown_budget=self.overall.max_drawdown_budget,
        )
        if self.overall != expected_overall:
            raise ValueError("trend report overall summary must match checkpoints")

        expected_failures: list[TrendGateFailure] = []
        for index, dataset in enumerate(self.datasets):
            scores = tuple(checkpoint.dataset_scores[index] for checkpoint in self.checkpoints)
            latest_ready = self.checkpoints[-1].dataset_release_ready[index]
            expected_dataset = DatasetTrendSummary(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.dataset_version,
                suite_sha256=dataset.suite_sha256,
                evaluator_semantics_version=dataset.evaluator_semantics_version,
                weight=dataset.weight,
                first_score=scores[0],
                latest_score=scores[-1],
                peak_score=max(scores),
                max_drawdown=_max_drawdown(scores),
                max_drawdown_budget=dataset.max_drawdown_budget,
                latest_release_ready=latest_ready,
            )
            if dataset != expected_dataset:
                raise ValueError("trend report dataset summary must match checkpoints")
            if not latest_ready:
                expected_failures.append(
                    TrendGateFailure(
                        scope="dataset",
                        dataset_id=dataset.dataset_id,
                        reason="latest_underlying_gate",
                        observed=0.0,
                        required=1.0,
                    )
                )
            if dataset.max_drawdown > dataset.max_drawdown_budget:
                expected_failures.append(
                    TrendGateFailure(
                        scope="dataset",
                        dataset_id=dataset.dataset_id,
                        reason="max_drawdown",
                        observed=dataset.max_drawdown,
                        required=dataset.max_drawdown_budget,
                    )
                )
        if self.overall.max_drawdown > self.overall.max_drawdown_budget:
            expected_failures.append(
                TrendGateFailure(
                    scope="overall",
                    reason="max_drawdown",
                    observed=self.overall.max_drawdown,
                    required=self.overall.max_drawdown_budget,
                )
            )
        if self.gate_failures != tuple(expected_failures):
            raise ValueError("trend report gate failures must match derived evidence")
        if self.release_ready != (not expected_failures):
            raise ValueError("trend report release verdict must match gate failures")
        return self


class CrossDatasetTrendArtifact(BaseModel):
    """Content-addressed scorecard with redacted provenance and derived report."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    schema_version: Literal[1] = 1
    producer: TrendProducer
    observations: CrossDatasetObservations
    policy: CrossDatasetPolicy
    producer_sha256: Sha256Digest
    observations_sha256: Sha256Digest
    policy_sha256: Sha256Digest
    report_sha256: Sha256Digest
    canonicalization_version: Literal["evalforge-json-v1"]
    trend_semantics_version: Literal["cross-dataset-scorecard-v1"]
    trend_analysis_id: Sha256Digest
    report: CrossDatasetTrendReport

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_consistent_content_addressed_evidence(self) -> Self:
        if self.producer != self.observations.producer:
            raise ValueError("artifact producer must match embedded observations")
        if self.producer_sha256 != producer_sha256(self.producer):
            raise ValueError("producer_sha256 must match producer provenance")
        expected_observations_sha256 = canonical_json_sha256(
            cast(JsonValue, self.observations.model_dump(mode="json"))
        )
        if self.observations_sha256 != expected_observations_sha256:
            raise ValueError("observations_sha256 must match embedded observations")
        expected_policy_sha256 = canonical_json_sha256(
            cast(JsonValue, self.policy.model_dump(mode="json"))
        )
        if self.policy_sha256 != expected_policy_sha256:
            raise ValueError("policy_sha256 must match the embedded trend policy")
        if self.policy.approved_producer_sha256 != self.producer_sha256:
            raise ValueError("embedded policy must approve the artifact producer")
        _require_governed_observations(self.observations, self.policy)
        policy_controls = tuple(
            (
                dataset.dataset_id,
                dataset.dataset_version,
                dataset.suite_sha256,
                dataset.evaluator_semantics_version,
                dataset.weight,
                dataset.max_drawdown,
            )
            for dataset in self.policy.datasets
        )
        report_controls = tuple(
            (
                dataset.dataset_id,
                dataset.dataset_version,
                dataset.suite_sha256,
                dataset.evaluator_semantics_version,
                dataset.weight,
                dataset.max_drawdown_budget,
            )
            for dataset in self.report.datasets
        )
        if policy_controls != report_controls or (
            self.policy.max_overall_drawdown != self.report.overall.max_drawdown_budget
        ):
            raise ValueError("trend report policy controls must match the embedded policy")
        if self.report != analyze_cross_dataset_trends(self.observations, self.policy):
            raise ValueError("trend report must match embedded observations and policy")
        expected_report_sha256 = canonical_json_sha256(
            cast(JsonValue, self.report.model_dump(mode="json"))
        )
        if self.report_sha256 != expected_report_sha256:
            raise ValueError("report_sha256 must match the trend report")
        expected_id = canonical_json_sha256(
            {
                "canonicalization_version": self.canonicalization_version,
                "observations_sha256": self.observations_sha256,
                "policy_sha256": self.policy_sha256,
                "producer_sha256": self.producer_sha256,
                "report_sha256": self.report_sha256,
                "trend_analysis_id_schema_version": 1,
                "trend_semantics_version": self.trend_semantics_version,
            }
        )
        if self.trend_analysis_id != expected_id:
            raise ValueError("trend_analysis_id must match its versioned preimage")
        return self


def producer_sha256(producer: TrendProducer | dict[str, object]) -> str:
    """Digest normalized minimal producer provenance."""
    snapshot = (
        producer if isinstance(producer, TrendProducer) else TrendProducer.model_validate(producer)
    )
    return canonical_json_sha256(cast(JsonValue, snapshot.model_dump(mode="json")))


def _max_drawdown(scores: tuple[float, ...]) -> float:
    peak = Fraction(Decimal(str(scores[0])))
    maximum = Fraction(0)
    for score in scores[1:]:
        exact_score = Fraction(Decimal(str(score)))
        maximum = max(maximum, peak - exact_score)
        peak = max(peak, exact_score)
    return float(maximum)


def _weighted_score(scores: tuple[float, ...], weights: tuple[float, ...]) -> float:
    score_ticks = tuple(int(Fraction(Decimal(str(score))) * _SCORE_SCALE) for score in scores)
    weight_ticks = tuple(int(Fraction(Decimal(str(weight))) * _WEIGHT_SCALE) for weight in weights)
    weighted_ticks = sum(
        score * weight for score, weight in zip(score_ticks, weight_ticks, strict=True)
    )
    # With at most 1000 weights capped at 1000.0, adjacent exact means are at
    # least 1e-15 apart, safely above binary64 spacing throughout [0, 1].
    return float(Fraction(weighted_ticks, sum(weight_ticks) * _SCORE_SCALE))


def _require_governed_observations(
    observations: CrossDatasetObservations, policy: CrossDatasetPolicy
) -> None:
    if producer_sha256(observations.producer) != policy.approved_producer_sha256:
        raise ValueError("observation producer is not approved")
    expected = tuple(
        (
            dataset.dataset_id,
            dataset.dataset_version,
            dataset.suite_sha256,
            dataset.evaluator_semantics_version,
        )
        for dataset in policy.datasets
    )
    for checkpoint in observations.checkpoints:
        actual = tuple(
            (
                dataset.dataset_id,
                dataset.dataset_version,
                dataset.suite_sha256,
                dataset.evaluator_semantics_version,
            )
            for dataset in checkpoint.datasets
        )
        if actual != expected:
            raise ValueError("checkpoint datasets must exactly match policy coverage and order")


def analyze_cross_dataset_trends(
    observations: CrossDatasetObservations, policy: CrossDatasetPolicy
) -> CrossDatasetTrendReport:
    """Compute weighted checkpoints, full-history drawdowns, and release gates."""
    observations = CrossDatasetObservations.model_validate(
        CrossDatasetObservations.__pydantic_serializer__.to_python(observations, mode="python")
    )
    policy = CrossDatasetPolicy.model_validate(
        CrossDatasetPolicy.__pydantic_serializer__.to_python(policy, mode="python")
    )
    _require_governed_observations(observations, policy)

    weights = tuple(dataset.weight for dataset in policy.datasets)
    checkpoint_scores = tuple(
        _weighted_score(tuple(observed.score for observed in checkpoint.datasets), weights)
        for checkpoint in observations.checkpoints
    )
    checkpoints = tuple(
        CheckpointScore(
            observed_at=checkpoint.observed_at,
            overall_score=score,
            dataset_scores=tuple(dataset.score for dataset in checkpoint.datasets),
            dataset_release_ready=tuple(dataset.release_ready for dataset in checkpoint.datasets),
        )
        for checkpoint, score in zip(observations.checkpoints, checkpoint_scores, strict=True)
    )

    dataset_summaries: list[DatasetTrendSummary] = []
    failures: list[TrendGateFailure] = []
    for index, configured in enumerate(policy.datasets):
        scores = tuple(checkpoint.datasets[index].score for checkpoint in observations.checkpoints)
        latest_ready = observations.checkpoints[-1].datasets[index].release_ready
        drawdown = _max_drawdown(scores)
        dataset_summaries.append(
            DatasetTrendSummary(
                dataset_id=configured.dataset_id,
                dataset_version=configured.dataset_version,
                suite_sha256=configured.suite_sha256,
                evaluator_semantics_version=configured.evaluator_semantics_version,
                weight=configured.weight,
                first_score=scores[0],
                latest_score=scores[-1],
                peak_score=max(scores),
                max_drawdown=drawdown,
                max_drawdown_budget=configured.max_drawdown,
                latest_release_ready=latest_ready,
            )
        )
        if not latest_ready:
            failures.append(
                TrendGateFailure(
                    scope="dataset",
                    dataset_id=configured.dataset_id,
                    reason="latest_underlying_gate",
                    observed=0.0,
                    required=1.0,
                )
            )
        if drawdown > configured.max_drawdown:
            failures.append(
                TrendGateFailure(
                    scope="dataset",
                    dataset_id=configured.dataset_id,
                    reason="max_drawdown",
                    observed=drawdown,
                    required=configured.max_drawdown,
                )
            )

    overall_drawdown = _max_drawdown(checkpoint_scores)
    if overall_drawdown > policy.max_overall_drawdown:
        failures.append(
            TrendGateFailure(
                scope="overall",
                reason="max_drawdown",
                observed=overall_drawdown,
                required=policy.max_overall_drawdown,
            )
        )
    return CrossDatasetTrendReport(
        checkpoint_count=len(checkpoints),
        checkpoints=checkpoints,
        datasets=tuple(dataset_summaries),
        overall=OverallTrendSummary(
            first_score=checkpoint_scores[0],
            latest_score=checkpoint_scores[-1],
            peak_score=max(checkpoint_scores),
            max_drawdown=overall_drawdown,
            max_drawdown_budget=policy.max_overall_drawdown,
        ),
        gate_failures=tuple(failures),
        release_ready=not failures,
    )


def build_cross_dataset_trend_artifact(
    observations: CrossDatasetObservations, policy: CrossDatasetPolicy
) -> CrossDatasetTrendArtifact:
    """Build a deterministic content-addressed artifact from governed observations."""
    observations = CrossDatasetObservations.model_validate(
        CrossDatasetObservations.__pydantic_serializer__.to_python(observations, mode="python")
    )
    policy = CrossDatasetPolicy.model_validate(
        CrossDatasetPolicy.__pydantic_serializer__.to_python(policy, mode="python")
    )
    report = analyze_cross_dataset_trends(observations, policy)
    producer_digest = producer_sha256(observations.producer)
    observations_digest = canonical_json_sha256(
        cast(JsonValue, observations.model_dump(mode="json"))
    )
    policy_digest = canonical_json_sha256(cast(JsonValue, policy.model_dump(mode="json")))
    report_digest = canonical_json_sha256(cast(JsonValue, report.model_dump(mode="json")))
    identifier_preimage: JsonValue = {
        "canonicalization_version": TREND_CANONICALIZATION_VERSION,
        "observations_sha256": observations_digest,
        "policy_sha256": policy_digest,
        "producer_sha256": producer_digest,
        "report_sha256": report_digest,
        "trend_analysis_id_schema_version": 1,
        "trend_semantics_version": TREND_SEMANTICS_VERSION,
    }
    return CrossDatasetTrendArtifact(
        producer=observations.producer,
        observations=observations,
        policy=policy,
        producer_sha256=producer_digest,
        observations_sha256=observations_digest,
        policy_sha256=policy_digest,
        report_sha256=report_digest,
        canonicalization_version=TREND_CANONICALIZATION_VERSION,
        trend_semantics_version=TREND_SEMANTICS_VERSION,
        trend_analysis_id=canonical_json_sha256(identifier_preimage),
        report=report,
    )
