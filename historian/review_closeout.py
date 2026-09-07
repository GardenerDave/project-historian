from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from historian.chatgpt_review_index import build_current_intake_candidate_lookup, build_review_index
from historian.review_lifecycle import (
    AUTHORITATIVE_SOURCE_NAMESPACE,
    LifecycleReplayResult,
    ReviewLifecycleEvent,
    lifecycle_log_path,
    load_lifecycle_events,
    replay_lifecycle_events,
)

ROOT = Path(__file__).resolve().parents[1]

HANDLED_LIFECYCLE_DISPOSITIONS = {
    "accept_for_next_packet",
    "duplicate",
    "low_value",
    "hold_for_corroboration",
    "promoted",
}


@dataclass(frozen=True)
class CloseoutSection:
    counts: dict[str, int] = field(default_factory=dict)
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {"counts": dict(self.counts)}
        if self.items:
            payload["items"] = list(self.items)
        return payload


@dataclass(frozen=True)
class ReviewCloseoutResult:
    population: dict[str, int]
    provenance: dict[str, int]
    review_state: dict[str, int]
    queue_state: dict[str, int]
    historical_event_stats: dict[str, int]
    excluded: dict[str, int]
    integrity: dict[str, Any]
    ordinary_reviewable_candidate_ids: list[str] = field(default_factory=list)
    active_state_candidate_ids: dict[str, list[str]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "population": dict(self.population),
            "provenance": dict(self.provenance),
            "review_state": dict(self.review_state),
            "queue_state": dict(self.queue_state),
            "historical_event_stats": dict(self.historical_event_stats),
            "excluded": dict(self.excluded),
            "integrity": dict(self.integrity),
            "ordinary_reviewable_candidate_ids": list(self.ordinary_reviewable_candidate_ids),
            "active_state_candidate_ids": {key: list(value) for key, value in self.active_state_candidate_ids.items()},
            "summary": dict(self.summary),
        }


def _load_events(event_log: Path | None) -> list[ReviewLifecycleEvent]:
    if event_log is None or not event_log.exists():
        return []
    return load_lifecycle_events(event_log)


def _validate_lifecycle_candidates(result: LifecycleReplayResult, lookup: dict[str, dict[str, Any]]) -> None:
    for candidate_id, state in result.states.items():
        row = lookup.get(candidate_id)
        if row is None:
            raise LookupError(f"lifecycle references unknown candidate {candidate_id}")
        if row.get("source_namespace") not in {None, AUTHORITATIVE_SOURCE_NAMESPACE}:
            raise LookupError(f"lifecycle references stale namespace for {candidate_id}")
        if state.source_namespace != AUTHORITATIVE_SOURCE_NAMESPACE:
            raise LookupError(f"lifecycle state has mismatched namespace for {candidate_id}")


def build_review_closeout(work_root: Path = ROOT / ".work" / "chatgpt-intake", *, repo_root: Path = ROOT, event_log: Path | None = None) -> ReviewCloseoutResult:
    review_index = build_review_index(work_root=work_root, repo_root=repo_root)
    lookup = build_current_intake_candidate_lookup(work_root=work_root, repo_root=repo_root)
    lifecycle_path = lifecycle_log_path(work_root, event_log)
    events = _load_events(lifecycle_path)
    lifecycle_result = replay_lifecycle_events(events)
    _validate_lifecycle_candidates(lifecycle_result, lookup)

    active_state_candidate_ids = {
        "evidence_resolved": sorted(candidate_id for candidate_id, state in lifecycle_result.states.items() if state.evidence_resolved),
        "privacy_deferred": sorted(candidate_id for candidate_id, state in lifecycle_result.states.items() if state.privacy_deferred_active),
        "hold": sorted(candidate_id for candidate_id, state in lifecycle_result.states.items() if state.active_hold),
        "accept_for_next_packet": sorted(candidate_id for candidate_id, state in lifecycle_result.states.items() if state.current_semantic_disposition == "accept_for_next_packet" and not state.promoted),
        "duplicate": sorted(candidate_id for candidate_id, state in lifecycle_result.states.items() if state.current_semantic_disposition == "duplicate" and not state.promoted),
        "low_value": sorted(candidate_id for candidate_id, state in lifecycle_result.states.items() if state.current_semantic_disposition == "low_value" and not state.promoted),
        "promoted": sorted(candidate_id for candidate_id, state in lifecycle_result.states.items() if state.promoted),
    }
    handled_ids = {
        candidate_id
        for candidate_id, state in lifecycle_result.states.items()
        if state.promoted
        or state.privacy_deferred_active
        or state.active_hold
        or state.current_semantic_disposition in HANDLED_LIFECYCLE_DISPOSITIONS
    }
    ordinary_rows = [
        row
        for row in review_index.reviewable_rows
        if row["stable_candidate_id"] not in handled_ids
    ]
    ordinary_rows = sorted(ordinary_rows, key=lambda row: (-int(row.get("semantic_score") or 0), row["conversation_index"] if row["conversation_index"] is not None else 10**9, row["stable_candidate_id"]))

    current_total = review_index.summary["current_export_total_conversations"]
    current_relevance_positive = review_index.summary["current_export_relevance_positive"]
    current_quarantined = review_index.summary["current_quarantined_rows"]
    provenance = {
        "resolved": review_index.summary["current_joined_rows"],
        "unresolved": review_index.summary["current_unresolved_rows"],
        "ambiguous": review_index.summary["current_ambiguous_rows"],
    }
    population = {
        "total_authoritative_population": current_relevance_positive,
        "total_export_conversations": current_total,
        "relevance_positive": current_relevance_positive,
        "relevance_negative": review_index.summary["current_export_relevance_negative"],
        "quarantined": current_quarantined,
        "excluded": current_quarantined + review_index.summary["current_unresolved_rows"] + review_index.summary["current_ambiguous_rows"],
        "ordinary_eligible_population": len(review_index.reviewable_rows),
    }
    review_state = {
        "unique_lifecycle_candidates": lifecycle_result.aggregate["unique_authoritative_candidates"],
        "evidence_resolved": lifecycle_result.aggregate["evidence_resolved_candidates"],
        "active_privacy_deferred": lifecycle_result.aggregate["active_privacy_deferred_candidates"],
        "active_hold": lifecycle_result.aggregate["active_hold_candidates"],
        "current_accept_for_next_packet": lifecycle_result.aggregate["accept_for_next_packet_candidates"],
        "current_duplicate": lifecycle_result.aggregate["duplicate_candidates"],
        "current_low_value": lifecycle_result.aggregate["low_value_candidates"],
        "promoted": lifecycle_result.aggregate["promoted_candidates"],
        "candidates_with_repeated_events": sum(1 for state in lifecycle_result.states.values() if len(state.events) > 1),
    }
    queue_state = {
        "ordinary_reviewable_remaining": len(ordinary_rows),
    }
    historical_event_stats = {
        "lifecycle_event_count": lifecycle_result.aggregate["total_events"],
        "unique_authoritative_candidates": lifecycle_result.aggregate["unique_authoritative_candidates"],
        "candidates_with_repeated_events": review_state["candidates_with_repeated_events"],
    }
    excluded = {
        "quarantined": current_quarantined,
        "unresolved": review_index.summary["current_unresolved_rows"],
        "ambiguous": review_index.summary["current_ambiguous_rows"],
        "stale_namespace": review_index.summary["historical_stale_namespace_rows"],
        "control_background": review_index.summary["historical_control_background_rows"],
    }
    integrity = {
        "consistent": True,
        "status": "ok",
        "checks": [
            "authoritative candidate identity resolved exactly once",
            "malformed lifecycle log fails closed",
        ],
    }
    summary = {
        "scope": {
            "current_export": "2026-09-02 current ChatGPT export",
            "source_namespace": AUTHORITATIVE_SOURCE_NAMESPACE,
        },
        "population": population,
        "provenance": provenance,
        "review_state": review_state,
        "queue_state": queue_state,
        "historical_event_stats": historical_event_stats,
        "excluded": excluded,
    }
    return ReviewCloseoutResult(
        population=population,
        provenance=provenance,
        review_state=review_state,
        queue_state=queue_state,
        historical_event_stats=historical_event_stats,
        excluded=excluded,
        integrity=integrity,
        ordinary_reviewable_candidate_ids=[row["stable_candidate_id"] for row in ordinary_rows],
        active_state_candidate_ids=active_state_candidate_ids,
        summary=summary,
    )


def render_review_closeout_markdown(result: ReviewCloseoutResult) -> str:
    lines = [
        "# Review Closeout",
        "",
        "## Scope",
        f"- Current export: {result.summary['scope']['current_export']}",
        f"- Source namespace: {result.summary['scope']['source_namespace']}",
        "",
        "## Population",
    ]
    for key in [
        "total_export_conversations",
        "relevance_positive",
        "relevance_negative",
        "quarantined",
        "excluded",
        "ordinary_eligible_population",
    ]:
        lines.append(f"- {key.replace('_', ' ').capitalize()}: {result.population[key]}")
    lines.extend(
        [
            "",
            "## Provenance",
        ]
    )
    for key in ["resolved", "unresolved", "ambiguous"]:
        lines.append(f"- {key.capitalize()}: {result.provenance[key]}")
    lines.extend(
        [
            "",
            "## Review State",
        ]
    )
    review_order = [
        ("unique_lifecycle_candidates", "Unique lifecycle candidates"),
        ("evidence_resolved", "Evidence resolved"),
        ("active_privacy_deferred", "Active privacy deferred"),
        ("active_hold", "Active HOLD"),
        ("current_accept_for_next_packet", "Current accept for next packet"),
        ("current_duplicate", "Current duplicate"),
        ("current_low_value", "Current low value"),
        ("promoted", "Promoted"),
        ("candidates_with_repeated_events", "Candidates with repeated events"),
    ]
    for key, label in review_order:
        lines.append(f"- {label}: {result.review_state[key]}")
    lines.extend(
        [
            "",
            "## Queue State",
            f"- Ordinary reviewable remaining: {result.queue_state['ordinary_reviewable_remaining']}",
            "",
            "## Historical Event Statistics",
            f"- Lifecycle event count: {result.historical_event_stats['lifecycle_event_count']}",
            f"- Repeated review candidates: {result.historical_event_stats['candidates_with_repeated_events']}",
            "",
            "## Deferred States",
            f"- Active privacy deferred: {result.review_state['active_privacy_deferred']}",
            f"- Active HOLD: {result.review_state['active_hold']}",
            "",
            "## Canonicalization",
            f"- Promoted current-export records: {result.review_state['promoted']}",
            "",
            "## Integrity",
            f"- Status: {result.integrity['status']}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_review_closeout_json(result: ReviewCloseoutResult) -> str:
    return json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
