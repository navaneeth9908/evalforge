"""Deterministic repeated-run stability analysis."""

from __future__ import annotations

from collections.abc import Sequence
from math import fsum

from evalforge.contracts import (
    CaseStability,
    EvaluationCase,
    ReleasePolicy,
    RepeatedObservations,
    RunStability,
    StabilityGateFailure,
    StabilityGateMetric,
    StabilityPolicy,
    StabilityReport,
    StabilityScoreSummary,
)
from evalforge.engine import evaluate_suite


def analyze_stability(
    cases: Sequence[EvaluationCase],
    observations: RepeatedObservations,
    *,
    evaluation_policy: ReleasePolicy,
    stability_policy: StabilityPolicy,
) -> StabilityReport:
    """Evaluate repeated observations and enforce deterministic stability limits."""
    evaluations = tuple(
        evaluate_suite(cases, run.outputs, policy=evaluation_policy) for run in observations.runs
    )
    samples = tuple(evaluation.weighted_pass_rate for evaluation in evaluations)
    mean = fsum(samples) / len(samples)
    variance = fsum((sample - mean) ** 2 for sample in samples) / len(samples)

    case_summaries = tuple(
        CaseStability(
            case_id=case.case_id,
            pass_count=sum(evaluation.results[index].passed for evaluation in evaluations),
            fail_count=sum(not evaluation.results[index].passed for evaluation in evaluations),
            flaky=len({evaluation.results[index].passed for evaluation in evaluations}) > 1,
        )
        for index, case in enumerate(cases)
    )
    flaky_case_count = sum(case.flaky for case in case_summaries)
    flaky_case_rate = flaky_case_count / len(case_summaries)
    checks: tuple[tuple[StabilityGateMetric, float, float], ...] = (
        (
            "weighted_pass_rate_population_variance",
            variance,
            stability_policy.max_weighted_pass_rate_variance,
        ),
        ("flaky_case_rate", flaky_case_rate, stability_policy.max_flaky_case_rate),
    )
    failures = tuple(
        StabilityGateFailure(metric=metric, observed=observed, required=required)
        for metric, observed, required in checks
        if observed > required
    )
    run_summaries = tuple(
        RunStability(
            run_id=observation.run_id,
            weighted_pass_rate=evaluation.weighted_pass_rate,
            release_ready=evaluation.release_ready,
        )
        for observation, evaluation in zip(observations.runs, evaluations, strict=True)
    )
    failed_run_count = sum(not run.release_ready for run in run_summaries)
    return StabilityReport(
        run_count=len(evaluations),
        failed_run_count=failed_run_count,
        runs=run_summaries,
        weighted_pass_rate=StabilityScoreSummary(
            mean=mean,
            minimum=min(samples),
            maximum=max(samples),
            population_variance=variance,
        ),
        flaky_case_count=flaky_case_count,
        flaky_case_rate=flaky_case_rate,
        cases=case_summaries,
        gate_failures=failures,
        release_ready=failed_run_count == 0 and not failures,
    )
