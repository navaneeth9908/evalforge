"""Deterministic evaluation of ordered agent state trajectories."""

from __future__ import annotations

from evalforge.contracts import (
    AgentTrajectory,
    TrajectoryFinding,
    TrajectoryMetrics,
    TrajectoryPolicy,
    TrajectoryReport,
)

TRAJECTORY_SEMANTICS_VERSION = "1"


def evaluate_trajectory(policy: TrajectoryPolicy, trajectory: AgentTrajectory) -> TrajectoryReport:
    """Evaluate a trajectory against its ordered required transitions."""
    findings = []
    first = trajectory.steps[0]
    if first.state_before != policy.initial_state:
        findings.append(
            TrajectoryFinding(
                code="initial_state_mismatch",
                actual_index=0,
                expected_from_state=policy.initial_state,
                actual_from_state=first.state_before,
            )
        )

    observed_required = 0
    for index, (step, transition) in enumerate(
        zip(trajectory.steps, policy.required_transitions, strict=False)
    ):
        if step.state_before == transition.from_state and step.state_after == transition.to_state:
            observed_required += 1
        else:
            findings.append(
                TrajectoryFinding(
                    code="sequence_mismatch",
                    expected_index=index,
                    actual_index=index,
                    expected_from_state=transition.from_state,
                    expected_to_state=transition.to_state,
                    actual_from_state=step.state_before,
                    actual_to_state=step.state_after,
                )
            )

    for expected_index in range(len(trajectory.steps), len(policy.required_transitions)):
        transition = policy.required_transitions[expected_index]
        findings.append(
            TrajectoryFinding(
                code="missing_transition",
                expected_index=expected_index,
                expected_from_state=transition.from_state,
                expected_to_state=transition.to_state,
            )
        )
    for actual_index in range(len(policy.required_transitions), len(trajectory.steps)):
        step = trajectory.steps[actual_index]
        findings.append(
            TrajectoryFinding(
                code="unexpected_transition",
                actual_index=actual_index,
                actual_from_state=step.state_before,
                actual_to_state=step.state_after,
            )
        )

    forbidden = {
        (transition.from_state, transition.to_state) for transition in policy.forbidden_transitions
    }
    forbidden_count = 0
    for actual_index, step in enumerate(trajectory.steps):
        if (step.state_before, step.state_after) in forbidden:
            forbidden_count += 1
            findings.append(
                TrajectoryFinding(
                    code="forbidden_transition",
                    actual_index=actual_index,
                    actual_from_state=step.state_before,
                    actual_to_state=step.state_after,
                )
            )

    ordered_steps = int(first.state_before == policy.initial_state)
    for index, (previous, current) in enumerate(
        zip(trajectory.steps, trajectory.steps[1:], strict=False), start=1
    ):
        if previous.state_after == current.state_before:
            ordered_steps += 1
        else:
            findings.append(
                TrajectoryFinding(
                    code="state_discontinuity",
                    actual_index=index,
                    expected_from_state=previous.state_after,
                    actual_from_state=current.state_before,
                )
            )

    seen_states = {policy.initial_state}
    loop_count = 0
    for actual_index, step in enumerate(trajectory.steps):
        if step.state_after in seen_states:
            loop_count += 1
            if not policy.allow_loops:
                findings.append(
                    TrajectoryFinding(
                        code="loop_detected",
                        actual_index=actual_index,
                        actual_from_state=step.state_before,
                        actual_to_state=step.state_after,
                    )
                )
        seen_states.add(step.state_after)

    premature_termination_count = 0
    for actual_index, step in enumerate(trajectory.steps[:-1]):
        if step.terminated:
            premature_termination_count += 1
            findings.append(
                TrajectoryFinding(
                    code="premature_termination",
                    actual_index=actual_index,
                    actual_to_state=step.state_after,
                )
            )

    final_step = trajectory.steps[-1]
    terminated = final_step.terminated and final_step.state_after in policy.terminal_states
    if not terminated:
        findings.append(
            TrajectoryFinding(
                code="unterminated",
                actual_index=len(trajectory.steps) - 1,
                actual_to_state=final_step.state_after,
            )
        )
    termination_violation_count = premature_termination_count + int(not terminated)

    metrics = TrajectoryMetrics(
        step_count=len(trajectory.steps),
        ordered_step_count=ordered_steps,
        required_transition_count=len(policy.required_transitions),
        observed_required_transition_count=observed_required,
        forbidden_transition_count=forbidden_count,
        termination_violation_count=termination_violation_count,
        loop_count=loop_count,
        sequence_score=observed_required
        / max(len(policy.required_transitions), len(trajectory.steps)),
        termination_score=1.0 if termination_violation_count == 0 else 0.0,
    )
    return TrajectoryReport(
        metrics=metrics,
        findings=tuple(findings),
        release_ready=not findings,
    )
