from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from historian.chatgpt_evidence import CandidateEvidenceResult, resolve_candidate_evidence
from historian.review_lifecycle import lifecycle_log_path, load_lifecycle_events, replay_lifecycle_events
from historian.privacy_representation import (
    PrivacySafeRepresentation,
    candidate_requires_privacy_representation,
    load_privacy_safe_representation,
    require_semantic_review_ready,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ROOT = ROOT / ".work" / "chatgpt-intake"
DEFAULT_HOLD_SOURCE_MODE = "auto"

_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "hold",
    "in",
    "into",
    "is",
    "it",
    "later",
    "model",
    "of",
    "on",
    "or",
    "project",
    "process",
    "proposal",
    "review",
    "that",
    "the",
    "this",
    "to",
    "using",
    "with",
}


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token and token not in _STOPWORDS]


def _bigrams(tokens: Iterable[str]) -> set[str]:
    sequence = list(tokens)
    return {f"{sequence[idx]} {sequence[idx + 1]}" for idx in range(len(sequence) - 1)}


def _safe_line_value(value: str | None) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class CorroborationHoldTarget:
    candidate_id: str
    source_artifact: str
    source_mode: str
    title: str
    original_state: str
    corroborating_evidence: str
    disposition: str
    safe_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorroborationOpportunity:
    opportunity_id: str
    status: str
    held_candidate_id: str
    held_source_artifact: str
    held_title: str
    new_candidate_id: str
    source_namespace: str
    match_basis: dict[str, Any]
    held_reference: dict[str, Any]
    new_reference: dict[str, Any]
    evidence_reference: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CorroborationSurfaceResult:
    status: str
    candidate_id: str
    source_namespace: str
    privacy_state: str | None
    hold_source_mode: str
    opportunities: list[CorroborationOpportunity] = field(default_factory=list)
    active_hold_targets: list[CorroborationHoldTarget] = field(default_factory=list)
    blocked_hold_targets: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: str | None = None
    candidate_reference: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidate_id": self.candidate_id,
            "source_namespace": self.source_namespace,
            "privacy_state": self.privacy_state,
            "hold_source_mode": self.hold_source_mode,
            "opportunities": [item.to_dict() for item in self.opportunities],
            "active_hold_targets": [item.to_dict() for item in self.active_hold_targets],
            "blocked_hold_targets": [dict(item) for item in self.blocked_hold_targets],
            "blocked_reason": self.blocked_reason,
            "candidate_reference": dict(self.candidate_reference),
        }


def _parse_hold_table(path: Path, *, source_artifact: str, source_mode: str) -> list[CorroborationHoldTarget]:
    if not path.exists():
        return []
    rows: list[CorroborationHoldTarget] = []
    for line in _load_text(path).splitlines():
        if not line.startswith("| `"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 4:
            continue
        candidate_id = _safe_line_value(parts[0].strip("` "))
        disposition = _safe_line_value(parts[-1].strip("` "))
        if disposition not in {"REMAIN_HOLD", "hold_for_corroboration"}:
            continue
        title = _safe_line_value(parts[1].strip("` "))
        middle = [part.strip("` ") for part in parts[2:-1]]
        original_state = _safe_line_value(middle[0] if middle else "")
        corroborating_evidence = _safe_line_value(" ".join(middle[1:]) if len(middle) > 1 else "")
        safe_text = " ".join(piece for piece in [title, original_state, corroborating_evidence] if piece)
        rows.append(
            CorroborationHoldTarget(
                candidate_id=candidate_id,
                source_artifact=source_artifact,
                source_mode=source_mode,
                title=title,
                original_state=original_state,
                corroborating_evidence=corroborating_evidence,
                disposition=disposition,
                safe_text=safe_text,
            )
        )
    return rows


def _legacy_hold_targets(work_root: Path = DEFAULT_WORK_ROOT) -> list[CorroborationHoldTarget]:
    v1 = _parse_hold_table(work_root / "corroboration-holds-v1" / "adjudication.md", source_artifact="corroboration-holds-v1", source_mode="legacy")
    v2 = _parse_hold_table(work_root / "corroboration-holds-v2" / "adjudication.md", source_artifact="corroboration-holds-v2", source_mode="legacy")
    return sorted(v1 + v2, key=lambda row: row.candidate_id)


def _privacy_safe_representation_for_hold(
    candidate_id: str,
    source_namespace: str,
    *,
    work_root: Path,
    evidence: CandidateEvidenceResult,
) -> PrivacySafeRepresentation | None:
    rep_path = work_root / "privacy-representation-v1" / f"{candidate_id}.json"
    if rep_path.exists():
        return require_semantic_review_ready(
            load_privacy_safe_representation(rep_path),
            expected_candidate_id=candidate_id,
            expected_source_namespace=source_namespace,
        )
    if candidate_requires_privacy_representation(evidence.privacy_state):
        raise ValueError(f"privacy-safe representation required for privacy-gated hold {candidate_id}")
    return None


def _candidate_safe_text(
    candidate_id: str,
    source_namespace: str,
    *,
    work_root: Path,
    blocked_hold_targets: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    evidence = resolve_candidate_evidence(candidate_id, source_namespace, work_root=work_root, show_text=False)
    if evidence.status != "resolved":
        blocked_hold_targets.append(
            {
                "candidate_id": candidate_id,
                "source_namespace": source_namespace,
                "status": evidence.status,
                "reason": evidence.error or evidence.status,
            }
        )
        return None, None, None
    try:
        representation = _privacy_safe_representation_for_hold(
            candidate_id,
            source_namespace,
            work_root=work_root,
            evidence=evidence,
        )
    except ValueError as exc:
        blocked_hold_targets.append(
            {
                "candidate_id": candidate_id,
                "source_namespace": source_namespace,
                "status": "blocked",
                "reason": str(exc),
            }
        )
        return None, None, None
    if representation is not None and representation.proposition:
        candidate_reference = _build_candidate_reference(evidence, representation)
        text = _candidate_text(evidence, representation)
        title = evidence.conversation_title or str(evidence.safe_metadata.get("title") or evidence.safe_metadata.get("topic") or candidate_id)
        return text, candidate_reference, title
    if evidence.conversation_title or evidence.safe_metadata:
        candidate_reference = _build_candidate_reference(evidence)
        text = _candidate_text(evidence, None)
        title = evidence.conversation_title or str(evidence.safe_metadata.get("title") or evidence.safe_metadata.get("topic") or candidate_id)
        return text, candidate_reference, title
    blocked_hold_targets.append(
        {
            "candidate_id": candidate_id,
            "source_namespace": source_namespace,
            "status": "blocked",
            "reason": "insufficient privacy-safe proposition material",
        }
    )
    return None, None, None


def _active_hold_targets_from_lifecycle(work_root: Path, event_log: Path) -> tuple[list[CorroborationHoldTarget], list[dict[str, Any]]]:
    if not event_log.exists():
        return [], []
    lifecycle_result = replay_lifecycle_events(load_lifecycle_events(event_log))
    targets: list[CorroborationHoldTarget] = []
    blocked: list[dict[str, Any]] = []
    for candidate_id, state in sorted(lifecycle_result.states.items()):
        if not state.active_hold:
            continue
        text, _reference, title = _candidate_safe_text(candidate_id, state.source_namespace, work_root=work_root, blocked_hold_targets=blocked)
        if text is None:
            continue
        targets.append(
            CorroborationHoldTarget(
                candidate_id=candidate_id,
                source_artifact="lifecycle-log",
                source_mode="lifecycle",
                title=title or candidate_id,
                original_state=state.current_semantic_disposition or "hold_for_corroboration",
                corroborating_evidence=state.latest_evidence_locator or "",
                disposition="hold_for_corroboration",
                safe_text=text,
            )
        )
    return sorted(targets, key=lambda row: row.candidate_id), blocked


def load_active_hold_targets(
    work_root: Path = DEFAULT_WORK_ROOT,
    *,
    source_mode: str = DEFAULT_HOLD_SOURCE_MODE,
    event_log: Path | None = None,
) -> tuple[list[CorroborationHoldTarget], list[dict[str, Any]]]:
    if source_mode not in {"auto", "legacy", "lifecycle"}:
        raise ValueError("invalid hold source mode")
    if source_mode == "legacy":
        return _legacy_hold_targets(work_root), []
    if source_mode == "lifecycle":
        return _active_hold_targets_from_lifecycle(work_root, event_log or lifecycle_log_path(work_root))
    event_log_path = event_log or lifecycle_log_path(work_root)
    if event_log_path.exists():
        lifecycle_targets, blocked = _active_hold_targets_from_lifecycle(work_root, event_log_path)
        return lifecycle_targets, blocked
    return _legacy_hold_targets(work_root), []


def _opportunity_id(
    held: CorroborationHoldTarget,
    *,
    new_candidate_id: str,
    source_namespace: str,
    evidence_reference: Mapping[str, Any],
    shared_terms: Iterable[str],
) -> str:
    payload = "|".join(
        [
            held.candidate_id,
            new_candidate_id,
            source_namespace,
            str(evidence_reference.get("bounded_evidence_locator") or ""),
            ",".join(sorted(shared_terms)),
            held.safe_text,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _build_candidate_reference(evidence: CandidateEvidenceResult, representation: PrivacySafeRepresentation | None = None) -> dict[str, Any]:
    if representation is not None:
        return {
            "candidate_id": representation.candidate_id,
            "source_namespace": representation.source_namespace,
            "evidence_reference": representation.evidence_reference or {},
            "semantic_review_ready": representation.semantic_review_ready,
            "privacy_safe": representation.privacy_safe,
            "epistemic_shape": representation.epistemic_shape,
        }
    return {
        "candidate_id": evidence.candidate_id,
        "source_namespace": evidence.source_namespace,
        "conversation_id": evidence.conversation_id,
        "shard_name": evidence.shard_name,
        "bounded_evidence_locator": evidence.bounded_evidence_locator,
        "message_ids": list(evidence.message_ids),
        "message_count": evidence.message_count,
        "privacy_state": evidence.privacy_state,
    }


def _candidate_text(evidence: CandidateEvidenceResult, representation: PrivacySafeRepresentation | None = None) -> str:
    if representation is not None and representation.proposition:
        return " ".join(
            part
            for part in [
                representation.proposition,
                representation.epistemic_shape or "",
                evidence.conversation_title or "",
            ]
            if part
        )
    return " ".join(
        part
        for part in [
            evidence.conversation_title or "",
            evidence.safe_metadata.get("title") if isinstance(evidence.safe_metadata, Mapping) else "",
            evidence.safe_metadata.get("topic") if isinstance(evidence.safe_metadata, Mapping) else "",
        ]
        if part
    )


def _score_match(held_text: str, candidate_text: str) -> tuple[int, list[str], list[str]]:
    held_tokens = _tokenize(held_text)
    candidate_tokens = _tokenize(candidate_text)
    held_bigram_set = _bigrams(held_tokens)
    candidate_bigram_set = _bigrams(candidate_tokens)
    shared_terms = sorted(set(held_tokens) & set(candidate_tokens))
    shared_bigrams = sorted(held_bigram_set & candidate_bigram_set)
    score = len(shared_terms) + (2 * len(shared_bigrams))
    return score, shared_terms, shared_bigrams


def find_hold_corroboration_opportunities(
    new_candidate_id: str,
    source_namespace: str,
    *,
    work_root: Path = DEFAULT_WORK_ROOT,
    privacy_representation_path: Path | None = None,
    hold_source_mode: str = DEFAULT_HOLD_SOURCE_MODE,
    event_log: Path | None = None,
) -> CorroborationSurfaceResult:
    evidence = resolve_candidate_evidence(new_candidate_id, source_namespace, work_root=work_root, show_text=False)
    if evidence.status != "resolved":
        return CorroborationSurfaceResult(
            status=evidence.status,
            candidate_id=new_candidate_id,
            source_namespace=source_namespace,
            privacy_state=evidence.privacy_state,
            hold_source_mode=hold_source_mode,
            blocked_reason=evidence.error or evidence.status,
            candidate_reference=evidence.safe_metadata if isinstance(evidence.safe_metadata, dict) else {},
        )

    representation: PrivacySafeRepresentation | None = None
    if candidate_requires_privacy_representation(evidence.privacy_state):
        if privacy_representation_path is None:
            return CorroborationSurfaceResult(
                status="blocked",
                candidate_id=new_candidate_id,
                source_namespace=source_namespace,
                privacy_state=evidence.privacy_state,
                hold_source_mode=hold_source_mode,
                blocked_reason="privacy-safe representation required for privacy-gated candidate",
                candidate_reference=_build_candidate_reference(evidence),
            )
        representation = require_semantic_review_ready(
            load_privacy_safe_representation(privacy_representation_path),
            expected_candidate_id=new_candidate_id,
            expected_source_namespace=source_namespace,
        )
    elif privacy_representation_path is not None:
        representation = require_semantic_review_ready(
            load_privacy_safe_representation(privacy_representation_path),
            expected_candidate_id=new_candidate_id,
            expected_source_namespace=source_namespace,
        )

    candidate_text = _candidate_text(evidence, representation)
    candidate_reference = _build_candidate_reference(evidence, representation)
    active_hold_targets, blocked_hold_targets = load_active_hold_targets(
        work_root=work_root,
        source_mode=hold_source_mode,
        event_log=event_log,
    )
    opportunities: list[CorroborationOpportunity] = []
    seen_hold_ids: set[str] = set()

    for held in active_hold_targets:
        if held.candidate_id in seen_hold_ids:
            continue
        seen_hold_ids.add(held.candidate_id)
        score, shared_terms, shared_bigrams = _score_match(held.safe_text, candidate_text)
        if score < 4 or not shared_bigrams:
            continue
        evidence_reference = candidate_reference.get("evidence_reference") if isinstance(candidate_reference, Mapping) else None
        if not isinstance(evidence_reference, Mapping):
            evidence_reference = candidate_reference
        opportunities.append(
            CorroborationOpportunity(
                opportunity_id=_opportunity_id(
                    held,
                    new_candidate_id=new_candidate_id,
                    source_namespace=source_namespace,
                    evidence_reference=evidence_reference,
                    shared_terms=shared_terms + shared_bigrams,
                ),
                status="possible_hold_intersection",
                held_candidate_id=held.candidate_id,
                held_source_artifact=held.source_artifact,
                held_title=held.title,
                new_candidate_id=new_candidate_id,
                source_namespace=source_namespace,
                match_basis={
                    "match_basis_version": "lexical_review_trigger_v1",
                    "match_type": "lexical_review_trigger",
                    "score": score,
                    "shared_terms": shared_terms,
                    "shared_bigrams": shared_bigrams,
                    "candidate_text": candidate_text,
                    "held_text": held.safe_text,
                    "note": "operator inspection candidate; lexical overlap is a wake-up signal, not corroboration evidence",
                },
                held_reference={
                    "candidate_id": held.candidate_id,
                    "source_artifact": held.source_artifact,
                    "title": held.title,
                    "original_state": held.original_state,
                    "corroborating_evidence": held.corroborating_evidence,
                    "disposition": held.disposition,
                },
                new_reference=candidate_reference,
                evidence_reference=dict(evidence_reference),
            )
        )

    opportunities.sort(key=lambda item: (-int(item.match_basis["score"]), item.held_candidate_id, item.opportunity_id))
    return CorroborationSurfaceResult(
        status="ok",
        candidate_id=new_candidate_id,
        source_namespace=source_namespace,
        privacy_state=evidence.privacy_state,
        hold_source_mode=hold_source_mode
        if hold_source_mode != "auto"
        else ("lifecycle" if (event_log or lifecycle_log_path(work_root)).exists() else "legacy"),
        opportunities=opportunities,
        active_hold_targets=active_hold_targets,
        blocked_hold_targets=blocked_hold_targets,
        candidate_reference=candidate_reference,
    )


def render_corroboration_json(result: CorroborationSurfaceResult) -> str:
    return json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"


def render_corroboration_markdown(result: CorroborationSurfaceResult) -> str:
    lines = [
        "# Hold Corroboration",
        "",
        f"- candidate_id: `{result.candidate_id}`",
        f"- source_namespace: `{result.source_namespace}`",
        f"- privacy_state: `{result.privacy_state}`",
        f"- hold_source_mode: `{result.hold_source_mode}`",
        f"- status: `{result.status}`",
        f"- active_hold_targets: `{len(result.active_hold_targets)}`",
    ]
    if result.blocked_hold_targets:
        lines.extend(["", "## Blocked Hold Targets"])
        for target in result.blocked_hold_targets:
            lines.append(f"- `{target.get('candidate_id')}`: `{target.get('reason')}`")
    if result.blocked_reason:
        lines.append(f"- blocked_reason: `{result.blocked_reason}`")
    if result.candidate_reference:
        lines.extend(["", "## Candidate Reference"])
        for key in sorted(result.candidate_reference):
            lines.append(f"- {key}: `{result.candidate_reference[key]}`")
    lines.extend(["", "## Opportunities"])
    if not result.opportunities:
        lines.append("- None")
    for opportunity in result.opportunities:
        lines.extend(
            [
                f"- `{opportunity.opportunity_id}` -> `{opportunity.held_candidate_id}`",
                f"  - status: `{opportunity.status}`",
                f"  - score: `{opportunity.match_basis['score']}`",
                f"  - shared_terms: `{', '.join(opportunity.match_basis['shared_terms'])}`",
            ]
        )
    return "\n".join(lines) + "\n"
