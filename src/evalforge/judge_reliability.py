"""Deterministic calibration, reliability, drift, and judge-bias diagnostics."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from math import fsum, isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalforge.contracts import (
    OutputText,
    PromptText,
    Rubric,
    RubricEvaluation,
    ScoreThreshold,
    SliceLabel,
)
from evalforge.judging import JudgeAdapter, evaluate_with_rubric


def _require_score(value: object) -> object:
    if type(value) is int:
        numeric_value = float(value)
    elif type(value) is float:
        numeric_value = value
    else:
        raise ValueError("score must be a finite JSON number from zero through one")
    if not isfinite(numeric_value) or not 0 <= numeric_value <= 1:
        raise ValueError("score must be a finite JSON number from zero through one")
    return value


class CalibrationCase(BaseModel):
    """One synthetic-label example used only after a blinded judge observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    case_id: SliceLabel
    prompt: PromptText
    candidate_output: OutputText
    synthetic_label: ScoreThreshold
    verbosity_pair_id: SliceLabel | None = None
    verbosity: Literal["concise", "verbose"] | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("synthetic_label", mode="before")
    @classmethod
    def require_numeric_synthetic_label(cls, value: object) -> object:
        return _require_score(value)

    @model_validator(mode="after")
    def require_complete_verbosity_pair_metadata(self) -> CalibrationCase:
        if (self.verbosity_pair_id is None) != (self.verbosity is None):
            raise ValueError("verbosity pair metadata must be supplied together")
        return self


class CalibrationDataset(BaseModel):
    """A bounded calibration dataset with labels hidden from judge requests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    dataset_id: SliceLabel
    cases: tuple[CalibrationCase, ...] = Field(min_length=2, max_length=100)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_unique_cases_and_valid_verbosity_pairs(self) -> CalibrationDataset:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("calibration case IDs must be unique")
        pairs: dict[str, list[CalibrationCase]] = defaultdict(list)
        for case in self.cases:
            if case.verbosity_pair_id is not None:
                pairs[case.verbosity_pair_id].append(case)
        for pair in pairs.values():
            if (
                len(pair) != 2
                or {case.verbosity for case in pair} != {"concise", "verbose"}
                or len({case.prompt for case in pair}) != 1
                or len({case.synthetic_label for case in pair}) != 1
            ):
                raise ValueError(
                    "verbosity pairs require one concise and one verbose case "
                    "with matching prompts and synthetic labels"
                )
        return self


class JudgeReliabilityPolicy(BaseModel):
    """Bounded repetitions plus agreement and drift release thresholds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repetitions: int = Field(ge=2, le=10, strict=True)
    agreement_tolerance: ScoreThreshold = 0.0
    minimum_agreement: ScoreThreshold
    maximum_drift: ScoreThreshold

    @field_validator("agreement_tolerance", "minimum_agreement", "maximum_drift", mode="before")
    @classmethod
    def require_numeric_threshold(cls, value: object) -> object:
        return _require_score(value)


class JudgeObservation(BaseModel):
    """One scored observation with deterministic blind and position evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: SliceLabel
    blind_id: SliceLabel
    repetition: int = Field(ge=1, le=10)
    position: int = Field(ge=1, le=100)
    score: ScoreThreshold


class JudgeReliabilityGateFailure(BaseModel):
    """Observed judge diagnostic that violates a configured release threshold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: Literal["agreement", "drift"]
    observed: ScoreThreshold
    required: ScoreThreshold


class JudgeReliabilityReport(BaseModel):
    """Deterministic judge reliability, calibration, bias, and gate evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    dataset_id: SliceLabel
    observation_count: int = Field(ge=1)
    observations: tuple[JudgeObservation, ...]
    agreement: ScoreThreshold
    drift: ScoreThreshold
    maximum_position_delta: ScoreThreshold
    verbosity_bias: ScoreThreshold | None
    calibration_mean_absolute_error: ScoreThreshold
    minimum_agreement: ScoreThreshold
    maximum_drift: ScoreThreshold
    gate_failures: tuple[JudgeReliabilityGateFailure, ...]
    release_ready: bool


def _mean(values: list[float]) -> float:
    return fsum(values) / len(values)


def measure_judge_reliability(
    rubric: Rubric,
    dataset: CalibrationDataset,
    judge: JudgeAdapter,
    policy: JudgeReliabilityPolicy,
) -> JudgeReliabilityReport:
    """Collect blinded alternating-order observations and calculate diagnostics."""

    observations: list[JudgeObservation] = []
    scores_by_case: dict[str, list[float]] = defaultdict(list)
    scores_by_repetition: dict[int, list[float]] = defaultdict(list)
    scores_by_case_and_repetition: dict[tuple[str, int], float] = {}

    for repetition_index in range(policy.repetitions):
        repetition = repetition_index + 1
        ordered_cases = (
            dataset.cases if repetition_index % 2 == 0 else tuple(reversed(dataset.cases))
        )
        for position_index, case in enumerate(ordered_cases, start=1):
            judged = evaluate_with_rubric(
                rubric,
                RubricEvaluation(
                    schema_version=1,
                    prompt=case.prompt,
                    candidate_output=case.candidate_output,
                ),
                judge,
            )
            score = judged.weighted_score
            observation = JudgeObservation(
                case_id=case.case_id,
                blind_id=f"blind-{len(observations) + 1:04d}",
                repetition=repetition,
                position=position_index,
                score=score,
            )
            observations.append(observation)
            scores_by_case[case.case_id].append(score)
            scores_by_repetition[repetition].append(score)
            scores_by_case_and_repetition[(case.case_id, repetition)] = score

    agreement_pairs = [
        abs(left - right) <= policy.agreement_tolerance
        for scores in scores_by_case.values()
        for left, right in combinations(scores, 2)
    ]
    agreement = fsum(float(item) for item in agreement_pairs) / len(agreement_pairs)
    drift = abs(_mean(scores_by_repetition[1]) - _mean(scores_by_repetition[policy.repetitions]))
    maximum_position_delta = max(
        abs(
            _mean(
                [
                    scores_by_case_and_repetition[(case.case_id, repetition)]
                    for repetition in range(1, policy.repetitions + 1, 2)
                ]
            )
            - _mean(
                [
                    scores_by_case_and_repetition[(case.case_id, repetition)]
                    for repetition in range(2, policy.repetitions + 1, 2)
                ]
            )
        )
        for case in dataset.cases
    )
    case_means = {case_id: _mean(scores) for case_id, scores in scores_by_case.items()}
    calibration_mean_absolute_error = _mean(
        [abs(case_means[case.case_id] - case.synthetic_label) for case in dataset.cases]
    )

    verbosity_groups: dict[str, dict[str, str]] = defaultdict(dict)
    for case in dataset.cases:
        if case.verbosity_pair_id is not None and case.verbosity is not None:
            verbosity_groups[case.verbosity_pair_id][case.verbosity] = case.case_id
    verbosity_deltas = [
        abs(case_means[group["concise"]] - case_means[group["verbose"]])
        for group in verbosity_groups.values()
        if set(group) == {"concise", "verbose"}
    ]
    verbosity_bias = _mean(verbosity_deltas) if verbosity_deltas else None

    failures: list[JudgeReliabilityGateFailure] = []
    if agreement < policy.minimum_agreement:
        failures.append(
            JudgeReliabilityGateFailure(
                metric="agreement",
                observed=agreement,
                required=policy.minimum_agreement,
            )
        )
    if drift > policy.maximum_drift:
        failures.append(
            JudgeReliabilityGateFailure(
                metric="drift",
                observed=drift,
                required=policy.maximum_drift,
            )
        )

    return JudgeReliabilityReport(
        dataset_id=dataset.dataset_id,
        observation_count=len(observations),
        observations=tuple(observations),
        agreement=agreement,
        drift=drift,
        maximum_position_delta=maximum_position_delta,
        verbosity_bias=verbosity_bias,
        calibration_mean_absolute_error=calibration_mean_absolute_error,
        minimum_agreement=policy.minimum_agreement,
        maximum_drift=policy.maximum_drift,
        gate_failures=tuple(failures),
        release_ready=not failures,
    )
