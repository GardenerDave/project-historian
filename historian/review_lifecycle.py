from __future__ import annotations

"""Append-only lifecycle events and deterministic replay for review state.

The lifecycle log is an immutable historical record. Current active state and
aggregate counts are derived by replaying the ordered event list.
"""

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable

LifecycleEventType = str
AUTHORITATIVE_SOURCE_NAMESPACE = "current-chatgpt-export"

VALID_EVENT_TYPES = {
    "evidence_resolved",
    "privacy_deferred",
    "privacy_clear_for_semantic_review",
    "accept_for_next_packet",
    "duplicate",
    "low_value",
    "hold_for_corroboration",
    "promoted",
}
SEMANTIC_FINALITY_EVENT_TYPES = {
    "accept_for_next_packet",
    "duplicate",
    "low_value",
    "hold_for_corroboration",
    "promoted",
}
PRIVACY_TRANSITION_EVENT_TYPES = {
    "privacy_deferred",
    "privacy_clear_for_semantic_review",
}


def _require_nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {field_name}")
    return value.strip()


def _require_positive_sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("sequence must be a positive integer")
    if value <= 0:
        raise ValueError("sequence must be a positive integer")
    return value


@dataclass(frozen=True)
class AuthoritativeCandidate:
    candidate_id: str
    source_namespace: str
    source_conversation_id: str
    source_locator: str
    title: str | None = None
    privacy_state: str | None = None


CandidateResolver = Callable[[str, str], AuthoritativeCandidate | None]


@dataclass(frozen=True)
class ReviewLifecycleEvent:
    schema_version: str
    event_id: str
    candidate_id: str
    source_namespace: str
    event_type: str
    sequence: int = 0
    evidence_locator: str | None = None
    batch_id: str | None = None
    run_id: str | None = None
    recorded_at: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.schema_version, "schema_version")
        _require_nonempty(self.event_id, "event_id")
        _require_nonempty(self.candidate_id, "candidate_id")
        _require_nonempty(self.source_namespace, "source_namespace")
        _require_nonempty(self.event_type, "event_type")
        if isinstance(self.sequence, bool):
            raise ValueError("sequence must be non-negative")
        if not isinstance(self.sequence, int):
            raise ValueError("sequence must be non-negative")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"invalid event type: {self.event_type}")
        if self.event_type == "evidence_resolved":
            _require_nonempty(self.evidence_locator, "evidence_locator")


@dataclass(frozen=True)
class DerivedCandidateState:
    candidate_id: str
    source_namespace: str
    events: list[ReviewLifecycleEvent] = field(default_factory=list)
    evidence_resolved: bool = False
    latest_evidence_locator: str | None = None
    privacy_deferred_active: bool = False
    current_semantic_disposition: str | None = None
    promoted: bool = False
    active_hold: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleReplayResult:
    events: list[ReviewLifecycleEvent]
    states: dict[str, DerivedCandidateState]
    aggregate: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [asdict(event) for event in self.events],
            "states": {candidate_id: state.to_dict() for candidate_id, state in self.states.items()},
            "aggregate": dict(self.aggregate),
        }


def _validate_lifecycle_log(events: Iterable[ReviewLifecycleEvent]) -> list[ReviewLifecycleEvent]:
    ordered = list(events)
    seen_event_ids: set[str] = set()
    seen_sequences: set[int] = set()
    for event in ordered:
        _require_positive_sequence(event.sequence)
        if event.event_id in seen_event_ids:
            raise ValueError(f"duplicate lifecycle event_id: {event.event_id}")
        seen_event_ids.add(event.event_id)
        if event.sequence in seen_sequences:
            raise ValueError(f"duplicate lifecycle sequence: {event.sequence}")
        seen_sequences.add(event.sequence)
    return ordered


def _serialize_lifecycle_events(events: Iterable[ReviewLifecycleEvent]) -> str:
    payload = {"schema_version": "historian.review_lifecycle.v1", "events": [asdict(event) for event in events]}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def lifecycle_log_path(work_root: Path, event_log_path: Path | None = None) -> Path:
    if event_log_path is not None:
        return event_log_path
    return work_root / "review-lifecycle-v1" / "lifecycle.jsonl"


def append_event_to_log(
    path: Path,
    event: ReviewLifecycleEvent,
    *,
    resolver: CandidateResolver,
) -> list[ReviewLifecycleEvent]:
    history = load_lifecycle_events(path) if path.exists() else []
    appended = append_lifecycle_event(history, event, resolver=resolver)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        "\n".join(json.dumps(asdict(item), sort_keys=True) for item in appended) + ("\n" if appended else ""),
        encoding="utf-8",
    )
    tmp_path.replace(path)
    reloaded = load_lifecycle_events(path)
    replay_lifecycle_events(reloaded)
    return reloaded


def _validate_resolver(candidate_id: str, source_namespace: str, resolver: CandidateResolver) -> AuthoritativeCandidate:
    candidate = resolver(candidate_id, source_namespace)
    if candidate is None:
        raise LookupError(f"no authoritative candidate for {candidate_id} in namespace {source_namespace}")
    if candidate.candidate_id != candidate_id:
        raise ValueError(f"resolver returned mismatched candidate id for {candidate_id}")
    if candidate.source_namespace != source_namespace:
        raise ValueError(
            f"resolver returned mismatched namespace for {candidate_id}: {candidate.source_namespace}"
        )
    _require_nonempty(candidate.source_conversation_id, "source_conversation_id")
    _require_nonempty(candidate.source_locator, "source_locator")
    return candidate


def _state_from_events(events: Iterable[ReviewLifecycleEvent]) -> DerivedCandidateState:
    ordered = sorted(_validate_lifecycle_log(events), key=lambda event: (event.sequence, event.event_id))
    candidate_id = ordered[0].candidate_id if ordered else ""
    source_namespace = ordered[0].source_namespace if ordered else ""
    evidence_resolved = False
    latest_evidence_locator: str | None = None
    privacy_deferred_active = False
    current_semantic_disposition: str | None = None
    promoted = False
    history: list[ReviewLifecycleEvent] = []

    for event in ordered:
        history.append(event)
        if event.event_type == "evidence_resolved":
            evidence_resolved = True
            latest_evidence_locator = event.evidence_locator or latest_evidence_locator
        elif event.event_type == "privacy_deferred":
            privacy_deferred_active = True
        elif event.event_type == "privacy_clear_for_semantic_review":
            privacy_deferred_active = False
        elif event.event_type in {"accept_for_next_packet", "duplicate", "low_value", "hold_for_corroboration"}:
            current_semantic_disposition = event.event_type
        elif event.event_type == "promoted":
            promoted = True

    active_hold = current_semantic_disposition == "hold_for_corroboration" and not promoted
    if current_semantic_disposition == "accept_for_next_packet" and promoted:
        active_hold = False
    return DerivedCandidateState(
        candidate_id=candidate_id,
        source_namespace=source_namespace,
        events=history,
        evidence_resolved=evidence_resolved,
        latest_evidence_locator=latest_evidence_locator,
        privacy_deferred_active=privacy_deferred_active,
        current_semantic_disposition=current_semantic_disposition,
        promoted=promoted,
        active_hold=active_hold,
    )


def replay_lifecycle_events(events: Iterable[ReviewLifecycleEvent]) -> LifecycleReplayResult:
    ordered = sorted(_validate_lifecycle_log(events), key=lambda event: (event.sequence, event.event_id))
    grouped: dict[str, list[ReviewLifecycleEvent]] = {}
    for event in ordered:
        grouped.setdefault(event.candidate_id, []).append(event)

    states = {candidate_id: _state_from_events(candidate_events) for candidate_id, candidate_events in grouped.items()}
    aggregate = {
        "unique_authoritative_candidates": len(states),
        "evidence_resolved_candidates": sum(1 for state in states.values() if state.evidence_resolved),
        "active_privacy_deferred_candidates": sum(1 for state in states.values() if state.privacy_deferred_active),
        "active_hold_candidates": sum(1 for state in states.values() if state.active_hold),
        "accept_for_next_packet_candidates": sum(
            1 for state in states.values() if state.current_semantic_disposition == "accept_for_next_packet" and not state.promoted
        ),
        "duplicate_candidates": sum(1 for state in states.values() if state.current_semantic_disposition == "duplicate" and not state.promoted),
        "low_value_candidates": sum(1 for state in states.values() if state.current_semantic_disposition == "low_value" and not state.promoted),
        "promoted_candidates": sum(1 for state in states.values() if state.promoted),
        "total_events": len(ordered),
    }
    return LifecycleReplayResult(events=ordered, states=states, aggregate=aggregate)


def validate_lifecycle_event(
    event: ReviewLifecycleEvent,
    *,
    prior_events: Iterable[ReviewLifecycleEvent],
    resolver: CandidateResolver,
) -> AuthoritativeCandidate:
    candidate = _validate_resolver(event.candidate_id, event.source_namespace, resolver)
    prior_state = _state_from_events(prior_events)
    if event.event_type in SEMANTIC_FINALITY_EVENT_TYPES:
        if not prior_state.evidence_resolved:
            raise ValueError("semantic finality requires prior evidence_resolved event")
        if prior_state.privacy_deferred_active:
            raise ValueError("semantic finality requires privacy to be cleared first")
    if event.event_type == "promoted" and prior_state.current_semantic_disposition != "accept_for_next_packet":
        raise ValueError("promoted requires prior accept_for_next_packet disposition")
    return candidate


def append_lifecycle_event(
    history: Iterable[ReviewLifecycleEvent],
    event: ReviewLifecycleEvent,
    *,
    resolver: CandidateResolver,
) -> list[ReviewLifecycleEvent]:
    ordered = list(history)
    validate_lifecycle_event(event, prior_events=ordered, resolver=resolver)
    expected_sequence = len(ordered) + 1
    if event.sequence not in {0, expected_sequence}:
        raise ValueError(f"sequence must be {expected_sequence} for append-only history")
    appended = replace(event, sequence=expected_sequence)
    ordered.append(appended)
    return ordered


def save_lifecycle_events(events: Iterable[ReviewLifecycleEvent], path: Path) -> Path:
    events = list(events)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".jsonl":
        path.write_text("\n".join(json.dumps(asdict(event), sort_keys=True) for event in events) + ("\n" if events else ""), encoding="utf-8")
    else:
        path.write_text(_serialize_lifecycle_events(events), encoding="utf-8")
    return path


def load_lifecycle_events(path: Path) -> list[ReviewLifecycleEvent]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        rows = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("invalid lifecycle event log")
    events = [ReviewLifecycleEvent(**row) for row in rows]
    _validate_lifecycle_log(events)
    return events
