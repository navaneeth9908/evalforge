from __future__ import annotations


def test_valid_trajectory_satisfies_order_transition_and_termination_contracts() -> None:
    from evalforge.contracts import (
        AgentTrajectory,
        StateTransition,
        TrajectoryPolicy,
        TrajectoryStep,
    )
    from evalforge.trajectories import evaluate_trajectory

    policy = TrajectoryPolicy(
        schema_version=1,
        initial_state="planning",
        terminal_states=("completed",),
        required_transitions=(
            StateTransition(from_state="planning", to_state="researching"),
            StateTransition(from_state="researching", to_state="completed"),
        ),
        forbidden_transitions=(StateTransition(from_state="planning", to_state="completed"),),
    )
    trajectory = AgentTrajectory(
        schema_version=1,
        steps=(
            TrajectoryStep(
                step_id="step-1",
                state_before="planning",
                action="search",
                state_after="researching",
            ),
            TrajectoryStep(
                step_id="step-2",
                state_before="researching",
                action="answer",
                state_after="completed",
                terminated=True,
            ),
        ),
    )

    report = evaluate_trajectory(policy, trajectory)

    assert report.release_ready is True
    assert report.metrics.model_dump() == {
        "step_count": 2,
        "ordered_step_count": 2,
        "required_transition_count": 2,
        "observed_required_transition_count": 2,
        "forbidden_transition_count": 0,
        "termination_violation_count": 0,
        "loop_count": 0,
        "sequence_score": 1.0,
        "termination_score": 1.0,
    }
    assert report.findings == ()


def test_reordered_trajectory_reports_sequence_and_state_evidence() -> None:
    from evalforge.contracts import (
        AgentTrajectory,
        StateTransition,
        TrajectoryPolicy,
        TrajectoryStep,
    )
    from evalforge.trajectories import evaluate_trajectory

    policy = TrajectoryPolicy(
        schema_version=1,
        initial_state="planning",
        terminal_states=("completed",),
        required_transitions=(
            StateTransition(from_state="planning", to_state="researching"),
            StateTransition(from_state="researching", to_state="completed"),
        ),
    )
    trajectory = AgentTrajectory(
        schema_version=1,
        steps=(
            TrajectoryStep(
                step_id="step-2",
                state_before="researching",
                action="answer",
                state_after="completed",
                terminated=True,
            ),
            TrajectoryStep(
                step_id="step-1",
                state_before="planning",
                action="search",
                state_after="researching",
            ),
        ),
    )

    report = evaluate_trajectory(policy, trajectory)

    assert report.release_ready is False
    assert report.metrics.ordered_step_count == 0
    assert report.metrics.observed_required_transition_count == 0
    assert report.metrics.sequence_score == 0.0
    assert [finding.model_dump(exclude_none=True) for finding in report.findings] == [
        {
            "code": "initial_state_mismatch",
            "actual_index": 0,
            "expected_from_state": "planning",
            "actual_from_state": "researching",
        },
        {
            "code": "sequence_mismatch",
            "expected_index": 0,
            "actual_index": 0,
            "expected_from_state": "planning",
            "expected_to_state": "researching",
            "actual_from_state": "researching",
            "actual_to_state": "completed",
        },
        {
            "code": "sequence_mismatch",
            "expected_index": 1,
            "actual_index": 1,
            "expected_from_state": "researching",
            "expected_to_state": "completed",
            "actual_from_state": "planning",
            "actual_to_state": "researching",
        },
        {
            "code": "state_discontinuity",
            "actual_index": 1,
            "expected_from_state": "completed",
            "actual_from_state": "planning",
        },
        {
            "code": "premature_termination",
            "actual_index": 0,
            "actual_to_state": "completed",
        },
        {"code": "unterminated", "actual_index": 1, "actual_to_state": "researching"},
    ]


def test_skipped_required_transition_and_forbidden_shortcut_are_reported() -> None:
    from evalforge.contracts import (
        AgentTrajectory,
        StateTransition,
        TrajectoryPolicy,
        TrajectoryStep,
    )
    from evalforge.trajectories import evaluate_trajectory

    policy = TrajectoryPolicy(
        schema_version=1,
        initial_state="planning",
        terminal_states=("completed",),
        required_transitions=(
            StateTransition(from_state="planning", to_state="researching"),
            StateTransition(from_state="researching", to_state="completed"),
        ),
        forbidden_transitions=(StateTransition(from_state="planning", to_state="completed"),),
    )
    trajectory = AgentTrajectory(
        schema_version=1,
        steps=(
            TrajectoryStep(
                step_id="step-1",
                state_before="planning",
                action="shortcut",
                state_after="completed",
                terminated=True,
            ),
        ),
    )

    report = evaluate_trajectory(policy, trajectory)

    assert report.release_ready is False
    assert report.metrics.observed_required_transition_count == 0
    assert report.metrics.forbidden_transition_count == 1
    assert report.metrics.termination_score == 1.0
    assert [finding.model_dump(exclude_none=True) for finding in report.findings] == [
        {
            "code": "sequence_mismatch",
            "expected_index": 0,
            "actual_index": 0,
            "expected_from_state": "planning",
            "expected_to_state": "researching",
            "actual_from_state": "planning",
            "actual_to_state": "completed",
        },
        {
            "code": "missing_transition",
            "expected_index": 1,
            "expected_from_state": "researching",
            "expected_to_state": "completed",
        },
        {
            "code": "forbidden_transition",
            "actual_index": 0,
            "actual_from_state": "planning",
            "actual_to_state": "completed",
        },
    ]


def test_cyclic_trajectory_reports_revisited_state() -> None:
    from evalforge.contracts import (
        AgentTrajectory,
        StateTransition,
        TrajectoryPolicy,
        TrajectoryStep,
    )
    from evalforge.trajectories import evaluate_trajectory

    transitions = (
        StateTransition(from_state="planning", to_state="researching"),
        StateTransition(from_state="researching", to_state="planning"),
        StateTransition(from_state="planning", to_state="completed"),
    )
    trajectory = AgentTrajectory(
        schema_version=1,
        steps=(
            TrajectoryStep(
                step_id="step-1",
                state_before="planning",
                action="search",
                state_after="researching",
            ),
            TrajectoryStep(
                step_id="step-2",
                state_before="researching",
                action="retry",
                state_after="planning",
            ),
            TrajectoryStep(
                step_id="step-3",
                state_before="planning",
                action="answer",
                state_after="completed",
                terminated=True,
            ),
        ),
    )
    disallowed_report = evaluate_trajectory(
        TrajectoryPolicy(
            schema_version=1,
            initial_state="planning",
            terminal_states=("completed",),
            required_transitions=transitions,
        ),
        trajectory,
    )
    allowed_report = evaluate_trajectory(
        TrajectoryPolicy(
            schema_version=1,
            initial_state="planning",
            terminal_states=("completed",),
            required_transitions=transitions,
            allow_loops=True,
        ),
        trajectory,
    )

    assert disallowed_report.release_ready is False
    assert disallowed_report.metrics.loop_count == 1
    assert disallowed_report.metrics.sequence_score == 1.0
    assert disallowed_report.metrics.termination_score == 1.0
    assert [finding.model_dump(exclude_none=True) for finding in disallowed_report.findings] == [
        {
            "code": "loop_detected",
            "actual_index": 1,
            "actual_from_state": "researching",
            "actual_to_state": "planning",
        }
    ]
    assert allowed_report.release_ready is True
    assert allowed_report.metrics.loop_count == 1
    assert allowed_report.findings == ()


def test_trajectory_contracts_reject_ambiguous_rules_and_duplicate_steps() -> None:
    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import AgentTrajectory, TrajectoryPolicy

    transition = {"from_state": "planning", "to_state": "completed"}
    base_policy = {
        "schema_version": 1,
        "initial_state": "planning",
        "terminal_states": ["completed"],
        "required_transitions": [transition],
    }
    invalid_policies = (
        {**base_policy, "schema_version": True},
        {**base_policy, "terminal_states": ["completed", "completed"]},
        {**base_policy, "required_transitions": [transition, transition]},
        {**base_policy, "forbidden_transitions": [transition]},
    )
    for payload in invalid_policies:
        with pytest.raises(ValidationError):
            TrajectoryPolicy.model_validate(payload)

    duplicate_step = {
        "step_id": "step-1",
        "state_before": "planning",
        "action": "answer",
        "state_after": "completed",
        "terminated": True,
    }
    with pytest.raises(ValidationError, match="step IDs must be unique"):
        AgentTrajectory.model_validate(
            {"schema_version": 1, "steps": [duplicate_step, duplicate_step]}
        )


def test_trajectory_cannot_continue_after_a_termination_signal() -> None:
    from evalforge.contracts import (
        AgentTrajectory,
        StateTransition,
        TrajectoryPolicy,
        TrajectoryStep,
    )
    from evalforge.trajectories import evaluate_trajectory

    policy = TrajectoryPolicy(
        schema_version=1,
        initial_state="planning",
        terminal_states=("completed",),
        required_transitions=(
            StateTransition(from_state="planning", to_state="researching"),
            StateTransition(from_state="researching", to_state="completed"),
        ),
    )
    trajectory = AgentTrajectory(
        schema_version=1,
        steps=(
            TrajectoryStep(
                step_id="step-1",
                state_before="planning",
                action="search",
                state_after="researching",
                terminated=True,
            ),
            TrajectoryStep(
                step_id="step-2",
                state_before="researching",
                action="answer",
                state_after="completed",
                terminated=True,
            ),
        ),
    )

    report = evaluate_trajectory(policy, trajectory)

    assert report.release_ready is False
    assert report.metrics.termination_violation_count == 1
    assert report.metrics.termination_score == 0.0
    assert [finding.model_dump(exclude_none=True) for finding in report.findings] == [
        {
            "code": "premature_termination",
            "actual_index": 0,
            "actual_to_state": "researching",
        }
    ]
