from __future__ import annotations


def _candidate(
    case_id: str,
    *,
    confidence: float,
    judge_scores: tuple[float, ...],
    prompt: str = "Synthetic prompt",
    candidate_output: str = "Synthetic answer",
) -> object:
    from evalforge.human_review import ReviewCandidate

    return ReviewCandidate(
        schema_version=1,
        case_id=case_id,
        prompt=prompt,
        candidate_output=candidate_output,
        confidence=confidence,
        judge_scores=judge_scores,
    )


def test_queue_selection_is_deterministic_for_low_confidence_and_disagreement() -> None:
    from evalforge.human_review import ReviewCandidate, ReviewQueuePolicy, select_review_queue

    candidates = (
        _candidate("stable", confidence=0.9, judge_scores=(0.8, 0.85)),
        _candidate("low", confidence=0.3, judge_scores=(0.7, 0.72)),
        _candidate("split", confidence=0.8, judge_scores=(0.2, 0.9)),
        _candidate("both", confidence=0.2, judge_scores=(0.1, 0.9)),
    )
    assert all(isinstance(candidate, ReviewCandidate) for candidate in candidates)
    policy = ReviewQueuePolicy(
        schema_version=1,
        maximum_confidence=0.4,
        minimum_disagreement=0.5,
        maximum_items=3,
    )

    queue = select_review_queue(tuple(reversed(candidates)), policy)

    assert queue.schema_version == 1
    assert [item.case_id for item in queue.items] == ["both", "low", "split"]
    assert [item.selection_reasons for item in queue.items] == [
        ("low_confidence", "judge_disagreement"),
        ("low_confidence",),
        ("judge_disagreement",),
    ]
    assert queue.total_candidates == 4
    assert queue.selected_count == 3
    assert queue.policy_sha256 == policy.sha256
    assert all(item.schema_version == 1 for item in queue.items)
    assert all(item.evidence_sha256 == item.original_evidence.sha256 for item in queue.items)


def test_queue_jsonl_export_is_privacy_aware_and_round_trips() -> None:
    from evalforge.human_review import (
        ReviewQueuePolicy,
        export_review_queue_jsonl,
        import_review_queue_jsonl,
        select_review_queue,
    )

    queue = select_review_queue(
        (
            _candidate(
                "private-case",
                confidence=0.2,
                judge_scores=(0.1, 0.9),
                prompt="Patient alice@example.test asks about account 44.",
                candidate_output="Private synthetic response.",
            ),
        ),
        ReviewQueuePolicy(
            schema_version=1,
            maximum_confidence=0.4,
            minimum_disagreement=0.5,
            maximum_items=10,
        ),
    )

    redacted = export_review_queue_jsonl(queue)
    redacted_rows = import_review_queue_jsonl(redacted)
    assert "alice@example.test" not in redacted
    assert "Private synthetic response" not in redacted
    assert redacted_rows[0].privacy_mode == "redacted"
    assert redacted_rows[0].prompt is None
    assert redacted_rows[0].candidate_output is None
    assert redacted_rows[0].evidence_sha256 == queue.items[0].evidence_sha256

    full = export_review_queue_jsonl(queue, privacy_mode="full")
    full_rows = import_review_queue_jsonl(full)
    assert full.endswith("\n")
    assert full_rows[0].privacy_mode == "full"
    assert full_rows[0].prompt == queue.items[0].original_evidence.prompt
    assert full_rows[0].candidate_output == queue.items[0].original_evidence.candidate_output
    assert export_review_queue_jsonl(queue, privacy_mode="full") == full


def test_decision_import_requires_reviewer_provenance_and_preserves_original_evidence() -> None:
    import json

    import pytest

    from evalforge.human_review import (
        ReviewDecision,
        ReviewerProvenance,
        ReviewQueuePolicy,
        export_review_decisions_jsonl,
        import_review_decisions_jsonl,
        select_review_queue,
    )

    queue = select_review_queue(
        (
            _candidate(
                "case-a",
                confidence=0.1,
                judge_scores=(0.2, 0.8),
                candidate_output="Immutable synthetic evidence.",
            ),
        ),
        ReviewQueuePolicy(
            schema_version=1,
            maximum_confidence=0.4,
            minimum_disagreement=0.5,
            maximum_items=10,
        ),
    )
    item = queue.items[0]
    decision = ReviewDecision(
        schema_version=1,
        decision_id="decision-a",
        review_item_id=item.review_item_id,
        evidence_sha256=item.evidence_sha256,
        reviewer=ReviewerProvenance(
            schema_version=1,
            reviewer_id="reviewer-7",
            source="internal",
            review_session_id="session-2026-09-11",
            reviewed_at="2026-09-11T10:30:00Z",
        ),
        role="reviewer",
        outcome="accept",
        rationale="Synthetic evidence satisfies the rubric.",
    )

    payload = export_review_decisions_jsonl((decision,))
    imported = import_review_decisions_jsonl(payload, queue)

    assert imported == (decision,)
    assert queue.items[0].original_evidence.candidate_output == "Immutable synthetic evidence."
    assert queue.items[0].evidence_sha256 == item.original_evidence.sha256

    tampered = json.loads(payload)
    tampered["evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="original evidence"):
        import_review_decisions_jsonl(json.dumps(tampered), queue)

    missing_provenance = {
        key: value for key, value in json.loads(payload).items() if key != "reviewer"
    }
    with pytest.raises(ValueError, match="contract"):
        import_review_decisions_jsonl(json.dumps(missing_provenance), queue)

    duplicate_key = payload.replace('"outcome":"accept"', '"outcome":"reject","outcome":"accept"')
    with pytest.raises(ValueError, match="invalid or ambiguous"):
        import_review_decisions_jsonl(duplicate_key, queue)


def test_adjudication_summary_exposes_agreement_conflict_resolution_and_pending_items() -> None:
    from evalforge.human_review import (
        ReviewDecision,
        ReviewerProvenance,
        ReviewQueuePolicy,
        select_review_queue,
        summarize_adjudication,
    )

    queue = select_review_queue(
        (
            _candidate("conflicted", confidence=0.1, judge_scores=(0.1, 0.9)),
            _candidate("agreed", confidence=0.2, judge_scores=(0.1, 0.9)),
            _candidate("pending", confidence=0.3, judge_scores=(0.1, 0.9)),
        ),
        ReviewQueuePolicy(
            schema_version=1,
            maximum_confidence=0.4,
            minimum_disagreement=0.5,
            maximum_items=10,
        ),
    )
    by_case = {item.case_id: item for item in queue.items}

    def decision(
        decision_id: str,
        case_id: str,
        reviewer_id: str,
        outcome: str,
        *,
        role: str = "reviewer",
    ) -> ReviewDecision:
        item = by_case[case_id]
        return ReviewDecision.model_validate(
            {
                "schema_version": 1,
                "decision_id": decision_id,
                "review_item_id": item.review_item_id,
                "evidence_sha256": item.evidence_sha256,
                "reviewer": {
                    "schema_version": 1,
                    "reviewer_id": reviewer_id,
                    "source": "internal",
                    "review_session_id": "adjudication-session",
                    "reviewed_at": "2026-09-11T11:00:00Z",
                },
                "role": role,
                "outcome": outcome,
                "rationale": "Synthetic human decision.",
            }
        )

    decisions = (
        decision("conflict-a", "conflicted", "reviewer-a", "accept"),
        decision("conflict-b", "conflicted", "reviewer-b", "reject"),
        decision("resolution", "conflicted", "lead-reviewer", "reject", role="adjudicator"),
        decision("agreement-a", "agreed", "reviewer-a", "accept"),
        decision("agreement-b", "agreed", "reviewer-b", "accept"),
    )
    assert all(isinstance(item.reviewer, ReviewerProvenance) for item in decisions)

    summary = summarize_adjudication(queue, decisions)

    assert summary.schema_version == 1
    assert summary.total_items == 3
    assert summary.pending_count == 1
    assert summary.agreement_count == 1
    assert summary.conflict_count == 1
    assert summary.adjudicated_count == 1
    by_summary_case = {item.case_id: item for item in summary.items}
    assert by_summary_case["conflicted"].status == "adjudicated"
    assert by_summary_case["conflicted"].reviewer_outcomes == ("accept", "reject")
    assert by_summary_case["conflicted"].final_outcome == "reject"
    assert by_summary_case["agreed"].status == "agreement"
    assert by_summary_case["agreed"].final_outcome == "accept"
    assert by_summary_case["pending"].status == "pending"
    assert by_summary_case["pending"].final_outcome is None


def test_contracts_and_imports_fail_closed_on_coercion_and_invalid_adjudication() -> None:
    import json

    import pytest
    from pydantic import ValidationError

    from evalforge.human_review import (
        ReviewCandidate,
        ReviewDecision,
        ReviewerProvenance,
        ReviewQueuePolicy,
        export_review_decisions_jsonl,
        export_review_queue_jsonl,
        import_review_decisions_jsonl,
        import_review_queue_jsonl,
        select_review_queue,
    )

    valid_candidate = {
        "schema_version": 1,
        "case_id": "strict-case",
        "prompt": "Synthetic prompt",
        "candidate_output": "Synthetic output",
        "confidence": 0.2,
        "judge_scores": [0.1, 0.9],
    }
    for invalid in (
        {**valid_candidate, "schema_version": True},
        {**valid_candidate, "confidence": "0.2"},
        {**valid_candidate, "judge_scores": [0.1, True]},
        {**valid_candidate, "unknown": "field"},
    ):
        with pytest.raises(ValidationError):
            ReviewCandidate.model_validate(invalid)

    with pytest.raises(ValidationError):
        ReviewQueuePolicy.model_validate(
            {
                "schema_version": 1,
                "maximum_confidence": 0.4,
                "minimum_disagreement": 0.5,
                "maximum_items": True,
            }
        )
    with pytest.raises(ValidationError, match="valid UTC timestamp"):
        ReviewerProvenance.model_validate(
            {
                "schema_version": 1,
                "reviewer_id": "reviewer-a",
                "source": "internal",
                "review_session_id": "strict-session",
                "reviewed_at": "2026-99-99T25:61:61Z",
            }
        )

    queue = select_review_queue(
        (ReviewCandidate.model_validate(valid_candidate),),
        ReviewQueuePolicy(
            schema_version=1,
            maximum_confidence=0.4,
            minimum_disagreement=0.5,
            maximum_items=10,
        ),
    )
    invalid_item = queue.items[0].model_dump(mode="json")
    invalid_item["schema_version"] = True
    with pytest.raises(ValidationError, match="schema_version must be an integer"):
        type(queue.items[0]).model_validate(invalid_item)
    invalid_queue = queue.model_dump(mode="json")
    invalid_queue["schema_version"] = True
    with pytest.raises(ValidationError, match="schema_version must be an integer"):
        type(queue).model_validate(invalid_queue)

    exported = export_review_queue_jsonl(queue)
    duplicate_queue_key = exported.replace(
        '"schema_version":1', '"schema_version":2,"schema_version":1'
    )
    with pytest.raises(ValueError, match="invalid or ambiguous"):
        import_review_queue_jsonl(duplicate_queue_key)

    full_missing_text = json.loads(export_review_queue_jsonl(queue, privacy_mode="full"))
    del full_missing_text["prompt"]
    with pytest.raises(ValueError, match="contract"):
        import_review_queue_jsonl(json.dumps(full_missing_text))

    empty_queue = select_review_queue(
        (_candidate("stable-empty", confidence=0.9, judge_scores=(0.8, 0.85)),),
        ReviewQueuePolicy(
            schema_version=1,
            maximum_confidence=0.1,
            minimum_disagreement=0.9,
            maximum_items=10,
        ),
    )
    assert export_review_queue_jsonl(empty_queue) == ""
    assert import_review_queue_jsonl("") == ()

    item = queue.items[0]
    adjudicator_only = ReviewDecision.model_validate(
        {
            "schema_version": 1,
            "decision_id": "premature-adjudication",
            "review_item_id": item.review_item_id,
            "evidence_sha256": item.evidence_sha256,
            "reviewer": {
                "schema_version": 1,
                "reviewer_id": "lead-reviewer",
                "source": "internal",
                "review_session_id": "strict-session",
                "reviewed_at": "2026-09-11T12:00:00Z",
            },
            "role": "adjudicator",
            "outcome": "accept",
            "rationale": "Synthetic premature adjudication.",
        }
    )
    with pytest.raises(ValueError, match="requires conflicting"):
        import_review_decisions_jsonl(export_review_decisions_jsonl((adjudicator_only,)), queue)


def test_review_imports_reject_escaped_lone_unicode_surrogates() -> None:
    import json

    import pytest

    from evalforge.human_review import (
        ReviewDecision,
        ReviewerProvenance,
        ReviewQueuePolicy,
        export_review_decisions_jsonl,
        export_review_queue_jsonl,
        import_review_decisions_jsonl,
        import_review_queue_jsonl,
        select_review_queue,
    )

    queue = select_review_queue(
        (_candidate("unicode-case", confidence=0.1, judge_scores=(0.1, 0.9)),),
        ReviewQueuePolicy(
            schema_version=1,
            maximum_confidence=0.4,
            minimum_disagreement=0.5,
            maximum_items=10,
        ),
    )
    item = queue.items[0]
    decision = ReviewDecision(
        schema_version=1,
        decision_id="unicode-decision",
        review_item_id=item.review_item_id,
        evidence_sha256=item.evidence_sha256,
        reviewer=ReviewerProvenance(
            schema_version=1,
            reviewer_id="reviewer-a",
            source="internal",
            review_session_id="unicode-session",
            reviewed_at="2026-09-11T13:00:00Z",
        ),
        role="reviewer",
        outcome="accept",
        rationale="Synthetic rationale.",
    )

    queue_row = json.loads(export_review_queue_jsonl(queue))
    queue_row["case_id"] = "\ud800"
    decision_row = json.loads(export_review_decisions_jsonl((decision,)))
    decision_row["rationale"] = "\ud800"

    with pytest.raises(ValueError, match="invalid or ambiguous"):
        import_review_queue_jsonl(json.dumps(queue_row))
    with pytest.raises(ValueError, match="invalid or ambiguous"):
        import_review_decisions_jsonl(json.dumps(decision_row), queue)
