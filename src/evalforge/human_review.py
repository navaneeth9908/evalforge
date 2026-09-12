"""Deterministic, privacy-aware human-review queue workflows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from evalforge.contracts import (
    CaseIdentifier,
    NonEmptyText,
    OutputText,
    PromptText,
    ScoreThreshold,
    Sha256Digest,
    SliceLabel,
)
from evalforge.provenance import JsonValue, canonical_json_sha256

SelectionReason = Literal["low_confidence", "judge_disagreement"]
PrivacyMode = Literal["redacted", "full"]
ReviewRole = Literal["reviewer", "adjudicator"]
ReviewOutcome = Literal["accept", "reject", "uncertain"]
ReviewItemIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=71,
        max_length=71,
        pattern=r"^review-[0-9a-f]{64}$",
    ),
]
MAX_REVIEW_JSONL_BYTES = 1024 * 1024
MAX_REVIEW_ITEMS = 10000
ReviewedAt = Annotated[
    str,
    StringConstraints(
        min_length=20,
        max_length=20,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
    ),
]


def _require_json_number(value: object) -> object:
    if type(value) not in (int, float):
        raise ValueError("review scores must be JSON numbers")
    return value


def _model_sha256(model: BaseModel) -> str:
    return canonical_json_sha256(cast(JsonValue, model.model_dump(mode="json")))


def _review_item_id(*, policy_sha256: str, evidence_sha256: str) -> str:
    digest = canonical_json_sha256(
        {
            "evidence_sha256": evidence_sha256,
            "policy_sha256": policy_sha256,
            "review_item_id_schema_version": 1,
        }
    )
    return f"review-{digest}"


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("review JSONL contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"review JSONL constant {value!r} is not permitted")


def _require_valid_unicode(value: object) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _load_jsonl(payload: str) -> tuple[object, ...]:
    try:
        encoded = payload.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("review JSONL must contain valid Unicode") from exc
    if len(encoded) > MAX_REVIEW_JSONL_BYTES:
        raise ValueError("review JSONL exceeds byte limit")
    lines = payload.splitlines()
    if not lines or len(lines) > MAX_REVIEW_ITEMS or any(not line.strip() for line in lines):
        raise ValueError("review JSONL must contain one to 10000 non-empty rows")
    try:
        rows = tuple(
            json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
            for line in lines
        )
        for row in rows:
            _require_valid_unicode(row)
        return rows
    except (json.JSONDecodeError, RecursionError, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("review JSONL is invalid or ambiguous") from exc


class ReviewCandidate(BaseModel):
    """Immutable original evidence considered for human review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    case_id: CaseIdentifier
    prompt: PromptText
    candidate_output: OutputText
    confidence: ScoreThreshold
    judge_scores: tuple[ScoreThreshold, ...] = Field(min_length=2, max_length=100)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def require_numeric_confidence(cls, value: object) -> object:
        return _require_json_number(value)

    @field_validator("judge_scores", mode="before")
    @classmethod
    def require_numeric_judge_scores(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            for score in value:
                _require_json_number(score)
        return value

    @property
    def sha256(self) -> str:
        """Return the canonical digest binding the complete original evidence."""

        return _model_sha256(self)


class ReviewQueuePolicy(BaseModel):
    """Versioned thresholds selecting uncertain or disputed candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    maximum_confidence: ScoreThreshold
    minimum_disagreement: ScoreThreshold
    maximum_items: int = Field(ge=1, le=MAX_REVIEW_ITEMS, strict=True)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("maximum_confidence", "minimum_disagreement", mode="before")
    @classmethod
    def require_numeric_thresholds(cls, value: object) -> object:
        return _require_json_number(value)

    @property
    def sha256(self) -> str:
        """Return the canonical digest of the selection semantics."""

        return _model_sha256(self)


class ReviewItem(BaseModel):
    """Selected review work bound to immutable original evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    review_item_id: ReviewItemIdentifier
    case_id: CaseIdentifier
    evidence_sha256: Sha256Digest
    original_evidence: ReviewCandidate
    confidence: ScoreThreshold
    disagreement: ScoreThreshold
    selection_reasons: tuple[SelectionReason, ...] = Field(min_length=1, max_length=2)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_evidence_binding(self) -> Self:
        if self.case_id != self.original_evidence.case_id:
            raise ValueError("review item case ID must match original evidence")
        if self.evidence_sha256 != self.original_evidence.sha256:
            raise ValueError("review item digest must match original evidence")
        if self.confidence != self.original_evidence.confidence:
            raise ValueError("review item confidence must match original evidence")
        return self


class ReviewQueue(BaseModel):
    """Deterministically ordered human-review work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    policy_sha256: Sha256Digest
    total_candidates: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    selected_count: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    items: tuple[ReviewItem, ...] = Field(max_length=MAX_REVIEW_ITEMS)

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def require_consistent_counts_and_unique_cases(self) -> Self:
        if self.selected_count != len(self.items) or self.selected_count > self.total_candidates:
            raise ValueError("review queue counts must match selected items")
        if len({item.case_id for item in self.items}) != len(self.items):
            raise ValueError("review queue case IDs must be unique")
        return self


class PortableReviewItem(BaseModel):
    """Portable queue row with explicit privacy treatment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    review_item_id: ReviewItemIdentifier
    policy_sha256: Sha256Digest
    ordinal: int = Field(ge=1, le=MAX_REVIEW_ITEMS, strict=True)
    case_id: CaseIdentifier
    evidence_sha256: Sha256Digest
    selection_reasons: tuple[SelectionReason, ...] = Field(min_length=1, max_length=2)
    confidence: ScoreThreshold
    disagreement: ScoreThreshold
    privacy_mode: PrivacyMode
    prompt: PromptText | None = None
    candidate_output: OutputText | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("confidence", "disagreement", mode="before")
    @classmethod
    def require_numeric_scores(cls, value: object) -> object:
        return _require_json_number(value)

    @model_validator(mode="after")
    def require_privacy_shape(self) -> Self:
        has_text = self.prompt is not None or self.candidate_output is not None
        if self.privacy_mode == "redacted" and has_text:
            raise ValueError("redacted review rows must not contain prompt or output text")
        if self.privacy_mode == "full" and (self.prompt is None or self.candidate_output is None):
            raise ValueError("full review rows require prompt and output text")
        return self


class ReviewerProvenance(BaseModel):
    """Required identity, source, session, and UTC time for a human decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    reviewer_id: SliceLabel
    source: Literal["internal", "external", "vendor"]
    review_session_id: SliceLabel
    reviewed_at: ReviewedAt

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("reviewed_at")
    @classmethod
    def require_valid_utc_timestamp(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("reviewed_at must be a valid UTC timestamp") from exc
        return value


class ReviewDecision(BaseModel):
    """Versioned portable decision bound to one immutable evidence digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    decision_id: SliceLabel
    review_item_id: ReviewItemIdentifier
    evidence_sha256: Sha256Digest
    reviewer: ReviewerProvenance
    role: ReviewRole
    outcome: ReviewOutcome
    rationale: NonEmptyText

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


def select_review_queue(
    candidates: tuple[ReviewCandidate, ...], policy: ReviewQueuePolicy
) -> ReviewQueue:
    """Select and order low-confidence or disputed evidence deterministically."""

    if len(candidates) > MAX_REVIEW_ITEMS:
        raise ValueError("review candidates exceed item limit")
    if len({candidate.case_id for candidate in candidates}) != len(candidates):
        raise ValueError("review candidate case IDs must be unique")

    selected: list[ReviewItem] = []
    for candidate in candidates:
        disagreement = max(candidate.judge_scores) - min(candidate.judge_scores)
        reasons: list[SelectionReason] = []
        if candidate.confidence <= policy.maximum_confidence:
            reasons.append("low_confidence")
        if disagreement >= policy.minimum_disagreement:
            reasons.append("judge_disagreement")
        if reasons:
            evidence_sha256 = candidate.sha256
            selected.append(
                ReviewItem(
                    review_item_id=_review_item_id(
                        policy_sha256=policy.sha256,
                        evidence_sha256=evidence_sha256,
                    ),
                    case_id=candidate.case_id,
                    evidence_sha256=evidence_sha256,
                    original_evidence=candidate,
                    confidence=candidate.confidence,
                    disagreement=disagreement,
                    selection_reasons=tuple(reasons),
                )
            )

    selected.sort(
        key=lambda item: (
            -len(item.selection_reasons),
            item.confidence,
            -item.disagreement,
            item.case_id,
        )
    )
    selected = selected[: policy.maximum_items]
    return ReviewQueue(
        policy_sha256=policy.sha256,
        total_candidates=len(candidates),
        selected_count=len(selected),
        items=tuple(selected),
    )


def export_review_queue_jsonl(queue: ReviewQueue, *, privacy_mode: PrivacyMode = "redacted") -> str:
    """Serialize portable queue rows; sensitive text is excluded by default."""

    lines: list[str] = []
    for ordinal, item in enumerate(queue.items, start=1):
        row = PortableReviewItem(
            schema_version=1,
            review_item_id=item.review_item_id,
            policy_sha256=queue.policy_sha256,
            ordinal=ordinal,
            case_id=item.case_id,
            evidence_sha256=item.evidence_sha256,
            selection_reasons=item.selection_reasons,
            confidence=item.confidence,
            disagreement=item.disagreement,
            privacy_mode=privacy_mode,
            prompt=item.original_evidence.prompt if privacy_mode == "full" else None,
            candidate_output=(
                item.original_evidence.candidate_output if privacy_mode == "full" else None
            ),
        )
        lines.append(
            json.dumps(
                row.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


def import_review_queue_jsonl(payload: str) -> tuple[PortableReviewItem, ...]:
    """Strictly parse portable queue rows without reconstructing original evidence."""

    if payload == "":
        return ()
    try:
        rows = tuple(PortableReviewItem.model_validate(row) for row in _load_jsonl(payload))
    except ValidationError as exc:
        raise ValueError("review queue JSONL violates its contract") from exc
    if [row.ordinal for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("review queue ordinals must be contiguous and ordered")
    if len({row.review_item_id for row in rows}) != len(rows):
        raise ValueError("review queue item IDs must be unique")
    if len({row.case_id for row in rows}) != len(rows):
        raise ValueError("review queue case IDs must be unique")
    if len({row.policy_sha256 for row in rows}) != 1:
        raise ValueError("review queue rows must share one policy digest")
    return rows


def export_review_decisions_jsonl(decisions: tuple[ReviewDecision, ...]) -> str:
    """Serialize decisions in deterministic reviewer and item order."""

    ordered = sorted(
        decisions,
        key=lambda decision: (
            decision.review_item_id,
            decision.role,
            decision.reviewer.reviewer_id,
            decision.decision_id,
        ),
    )
    return "".join(
        json.dumps(
            decision.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for decision in ordered
    )


def import_review_decisions_jsonl(
    payload: str, original_queue: ReviewQueue
) -> tuple[ReviewDecision, ...]:
    """Parse decisions and fail closed unless every row binds to original evidence."""

    try:
        decisions = tuple(ReviewDecision.model_validate(row) for row in _load_jsonl(payload))
    except ValidationError as exc:
        raise ValueError("review decision JSONL violates its contract") from exc

    if len({decision.decision_id for decision in decisions}) != len(decisions):
        raise ValueError("review decision IDs must be unique")
    reviewer_keys = [
        (decision.review_item_id, decision.reviewer.reviewer_id, decision.role)
        for decision in decisions
    ]
    if len(set(reviewer_keys)) != len(reviewer_keys):
        raise ValueError("a reviewer may submit only one decision per item and role")

    item_by_id = {item.review_item_id: item for item in original_queue.items}
    for decision in decisions:
        item = item_by_id.get(decision.review_item_id)
        if item is None:
            raise ValueError("review decision refers to an unknown original evidence item")
        if decision.evidence_sha256 != item.evidence_sha256:
            raise ValueError("review decision does not match immutable original evidence")
    summarize_adjudication(original_queue, decisions)
    return decisions


class AdjudicationItemSummary(BaseModel):
    """Conflict and resolution state for one review item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_item_id: ReviewItemIdentifier
    case_id: CaseIdentifier
    reviewer_count: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    reviewer_outcomes: tuple[ReviewOutcome, ...] = Field(max_length=3)
    conflict: bool
    status: Literal["pending", "agreement", "conflict", "adjudicated"]
    final_outcome: ReviewOutcome | None = None


class AdjudicationSummary(BaseModel):
    """Versioned aggregate human-review agreement and adjudication evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    total_items: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    pending_count: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    agreement_count: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    conflict_count: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    adjudicated_count: int = Field(ge=0, le=MAX_REVIEW_ITEMS, strict=True)
    items: tuple[AdjudicationItemSummary, ...] = Field(max_length=MAX_REVIEW_ITEMS)


_OUTCOME_ORDER: dict[ReviewOutcome, int] = {"accept": 0, "reject": 1, "uncertain": 2}


def summarize_adjudication(
    queue: ReviewQueue, decisions: tuple[ReviewDecision, ...]
) -> AdjudicationSummary:
    """Summarize reviewer agreement, unresolved conflicts, and adjudicator outcomes."""

    item_by_id = {item.review_item_id: item for item in queue.items}
    decisions_by_item: dict[str, list[ReviewDecision]] = {
        item.review_item_id: [] for item in queue.items
    }
    decision_ids: set[str] = set()
    reviewer_keys: set[tuple[str, str, str]] = set()
    for decision in decisions:
        item = item_by_id.get(decision.review_item_id)
        if item is None or decision.evidence_sha256 != item.evidence_sha256:
            raise ValueError("adjudication decision does not match immutable original evidence")
        if decision.decision_id in decision_ids:
            raise ValueError("adjudication decision IDs must be unique")
        decision_ids.add(decision.decision_id)
        reviewer_key = (
            decision.review_item_id,
            decision.reviewer.reviewer_id,
            decision.role,
        )
        if reviewer_key in reviewer_keys:
            raise ValueError("a reviewer may submit only one decision per item and role")
        reviewer_keys.add(reviewer_key)
        decisions_by_item[decision.review_item_id].append(decision)

    summaries: list[AdjudicationItemSummary] = []
    for item in queue.items:
        item_decisions = decisions_by_item[item.review_item_id]
        reviewer_decisions = [
            decision for decision in item_decisions if decision.role == "reviewer"
        ]
        adjudicator_decisions = [
            decision for decision in item_decisions if decision.role == "adjudicator"
        ]
        if len(adjudicator_decisions) > 1:
            raise ValueError("review items may have at most one adjudicator decision")
        outcomes = tuple(
            sorted(
                {decision.outcome for decision in reviewer_decisions},
                key=_OUTCOME_ORDER.__getitem__,
            )
        )
        conflict = len(outcomes) > 1
        if adjudicator_decisions and not conflict:
            raise ValueError("adjudication requires conflicting reviewer decisions")
        if not reviewer_decisions:
            status: Literal["pending", "agreement", "conflict", "adjudicated"] = "pending"
            final_outcome = None
        elif conflict and adjudicator_decisions:
            status = "adjudicated"
            final_outcome = adjudicator_decisions[0].outcome
        elif conflict:
            status = "conflict"
            final_outcome = None
        else:
            status = "agreement"
            final_outcome = outcomes[0]
        summaries.append(
            AdjudicationItemSummary(
                review_item_id=item.review_item_id,
                case_id=item.case_id,
                reviewer_count=len(reviewer_decisions),
                reviewer_outcomes=outcomes,
                conflict=conflict,
                status=status,
                final_outcome=final_outcome,
            )
        )

    return AdjudicationSummary(
        total_items=len(summaries),
        pending_count=sum(item.status == "pending" for item in summaries),
        agreement_count=sum(item.status == "agreement" for item in summaries),
        conflict_count=sum(item.conflict for item in summaries),
        adjudicated_count=sum(item.status == "adjudicated" for item in summaries),
        items=tuple(summaries),
    )
