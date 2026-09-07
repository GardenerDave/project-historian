from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from historian.source_locator import normalize_source_locator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ROOT = ROOT / ".work" / "chatgpt-intake"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_conversation_title(conversation: dict[str, Any]) -> str:
    return str(conversation.get("title") or conversation.get("conversation_title") or "").strip()


def _conversation_id(conversation: dict[str, Any]) -> str:
    value = conversation.get("id") or conversation.get("conversation_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing conversation identity")
    return value.strip()


def _group_provenance(work_root: Path = DEFAULT_WORK_ROOT) -> dict[str, dict[str, Any]]:
    provenance = _load_json(work_root / "private" / "provenance.json")
    return {row["opaque_private_id"]: row for row in provenance["items"]}


def _group_inventory(work_root: Path = DEFAULT_WORK_ROOT) -> dict[str, dict[str, Any]]:
    inventory = _load_json(work_root / "staging" / "inventory.json")
    return {row["opaque_source_id"]: row for row in inventory["items"]}


def _load_reviewed_candidates(work_root: Path = DEFAULT_WORK_ROOT) -> dict[str, dict[str, Any]]:
    path = work_root / "lineage-extraction-v1" / "reviewed-candidates.json"
    if not path.exists():
        return {}
    reviewed = _load_json(path)
    by_source_opaque: dict[str, dict[str, Any]] = {}
    for row in reviewed:
        original = row.get("original_candidate", {})
        source_opaque = original.get("source_opaque_id")
        if isinstance(source_opaque, str) and source_opaque:
            by_source_opaque[source_opaque] = row
    return by_source_opaque


def _load_candidate_briefs(work_root: Path = DEFAULT_WORK_ROOT) -> list[dict[str, Any]]:
    path = work_root / "lineage-extraction-v1" / "candidate-briefs.json"
    if not path.exists():
        return []
    payload = _load_json(path)
    return list(payload.get("items", []))


def _load_current_relevance_rows(work_root: Path = DEFAULT_WORK_ROOT) -> list[dict[str, Any]]:
    inventory = _load_json(work_root / "staging" / "inventory.json")
    rows = []
    for row in inventory["items"]:
        relevance = row.get("relevance_signals") or {}
        if int(relevance.get("keyword_total") or 0) > 0:
            rows.append(row)
    return rows


def _load_review_batch_dispositions(work_root: Path = DEFAULT_WORK_ROOT) -> dict[str, dict[str, Any]]:
    path = work_root / "review-batch-002" / "review-batch-002.md"
    if not path.exists():
        return {}
    pattern = re.compile(
        r"^\|\s*`(?P<id>[a-z0-9]{24})`\s*\|\s*(?P<theme>[^|]+?)\s*\|\s*`(?P<privacy>[^`]+)`\s*\|\s*(?P<novelty>[^|]+?)\s*\|\s*(?P<durability>[^|]+?)\s*\|\s*`(?P<disposition>[^`]+)`\s*\|\s*$"
    )
    dispositions: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        dispositions[match.group("id")] = {
            "theme": match.group("theme").strip(),
            "privacy": match.group("privacy").strip(),
            "novelty": match.group("novelty").strip(),
            "durability": match.group("durability").strip(),
            "disposition": match.group("disposition").strip(),
            "source": "review-batch-002",
        }
    return dispositions


def _load_background_semantic_rows(work_root: Path = DEFAULT_WORK_ROOT) -> list[dict[str, Any]]:
    path = work_root / "relevance-v2" / "human-review-v1b" / "review-manifest.json"
    if not path.exists():
        return []
    payload = _load_json(path)
    return list(payload.get("items", []))


def _load_promoted_source_ids(repo_root: Path = ROOT) -> set[str]:
    promoted: set[str] = set()
    for record_path in repo_root.glob("records/**/*.md"):
        text = record_path.read_text(encoding="utf-8")
        for payload in re.findall(r"^source_ids:\s*\[(.*?)\]\s*$", text, flags=re.M):
            for token in payload.split(","):
                value = token.strip().strip("[]")
                if value:
                    promoted.add(value)
    return promoted


def _extract_locator(candidate: dict[str, Any], provenance_row: dict[str, Any]) -> str:
    locator = candidate.get("source_locator")
    if isinstance(locator, str) and locator.strip():
        return normalize_source_locator(locator.strip())
    shard_name = provenance_row.get("source_shard")
    conversation_id = provenance_row.get("conversation_id")
    if isinstance(shard_name, str) and isinstance(conversation_id, str):
        return normalize_source_locator(f"{conversation_id}#{shard_name}")
    raise ValueError("missing evidence locator")


def _inventory_index(work_root: Path = DEFAULT_WORK_ROOT) -> dict[str, int]:
    inventory = _load_json(work_root / "staging" / "inventory.json")
    index: dict[str, int] = {}
    for idx, row in enumerate(inventory["items"]):
        index[row["opaque_source_id"]] = idx
    return index


def resolve_unique_provenance(
    source_opaque_id: str,
    provenance_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    matches = [row for row in provenance_rows if row.get("opaque_private_id") == source_opaque_id]
    if not matches:
        raise LookupError(f"no provenance match for {source_opaque_id}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous provenance for {source_opaque_id}: {len(matches)} matches")
    return matches[0]


def resolve_unique_inventory_index(
    source_opaque_id: str,
    inventory_rows: Iterable[dict[str, Any]],
) -> int | None:
    matches = [idx for idx, row in enumerate(inventory_rows) if row.get("opaque_source_id") == source_opaque_id]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"ambiguous inventory identity for {source_opaque_id}: {len(matches)} matches")
    return matches[0]


@dataclass(frozen=True)
class ReviewIndexResult:
    summary: dict[str, Any]
    reviewable_rows: list[dict[str, Any]]
    excluded_rows: dict[str, list[dict[str, Any]]]
    unresolved_rows: list[dict[str, Any]]
    ambiguous_rows: list[dict[str, Any]]
    already_reviewed_rows: list[dict[str, Any]]
    already_promoted_rows: list[dict[str, Any]]
    reconciliation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_candidate_row(
    candidate: dict[str, Any],
    provenance_row: dict[str, Any],
    inventory_rows: Iterable[dict[str, Any]],
    review_dispositions_by_source: dict[str, dict[str, Any]],
    promoted_source_ids: set[str],
) -> dict[str, Any]:
    source_opaque_id = candidate["source_opaque_id"]
    review_record = review_dispositions_by_source.get(source_opaque_id)
    privacy_state = str(candidate.get("privacy_review_state") or candidate.get("source_privacy_state") or "privacy_review_required")
    already_promoted = source_opaque_id in promoted_source_ids
    review_state = review_record["disposition"] if review_record else None
    already_reviewed = review_record is not None or already_promoted
    reviewable = privacy_state != "quarantined" and not already_reviewed
    message_ids = provenance_row.get("message_ids") or []
    locator = candidate.get("source_locator")
    if not isinstance(locator, str) or not locator.strip():
        if isinstance(message_ids, list) and message_ids:
            locator = f"{provenance_row['conversation_id']}#{message_ids[0]}..{message_ids[-1]}"
        else:
            locator = f"{provenance_row['conversation_id']}#{provenance_row.get('root_message_id') or ''}"
    locator = normalize_source_locator(locator)
    return {
        "stable_candidate_id": source_opaque_id,
        "candidate_id": source_opaque_id,
        "source_opaque_id": source_opaque_id,
        "conversation_id": provenance_row["conversation_id"],
        "conversation_index": resolve_unique_inventory_index(source_opaque_id, inventory_rows),
        "title": _safe_conversation_title(provenance_row),
        "shard_name": provenance_row["source_shard"],
        "bounded_evidence_locator": locator,
        "normalized_bounded_evidence_locator": locator,
        "privacy_state": privacy_state,
        "semantic_tier": candidate.get("source_stratum") or candidate.get("stratum") or "relevance_positive",
        "semantic_score": int(candidate.get("relevance_signals", {}).get("keyword_total") or candidate.get("source_v2_rank") or candidate.get("semantic_score") or 0),
        "source_stratum": candidate.get("source_stratum") or candidate.get("stratum") or "relevance_positive",
        "relevance_signals": candidate.get("relevance_signals") or {},
        "already_reviewed": already_reviewed,
        "already_promoted": already_promoted,
        "prior_disposition": review_state,
        "reviewable": reviewable,
        "evidence_source": "current-intake inventory/provenance",
        "source_namespace": "current-chatgpt-export",
    }


def _build_current_candidate_index(
    current_rows: Iterable[dict[str, Any]],
    provenance_rows: Iterable[dict[str, Any]],
    inventory_rows: Iterable[dict[str, Any]],
    review_dispositions_by_source: dict[str, dict[str, Any]],
    promoted_source_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    provenance_rows = list(provenance_rows)
    reviewable_rows: list[dict[str, Any]] = []
    excluded_rows: dict[str, list[dict[str, Any]]] = {
        "unresolved": [],
        "ambiguous": [],
        "stale_namespace": [],
        "quarantined": [],
        "already_reviewed": [],
        "already_promoted": [],
    }
    for candidate in current_rows:
        source_opaque_id = candidate.get("opaque_source_id")
        if not isinstance(source_opaque_id, str) or not source_opaque_id:
            excluded_rows["unresolved"].append({"candidate_id": candidate.get("opaque_source_id"), "reason": "missing opaque_source_id"})
            continue
        try:
            provenance_row = resolve_unique_provenance(source_opaque_id, provenance_rows)
        except LookupError:
            excluded_rows["unresolved"].append({"candidate_id": source_opaque_id, "source_opaque_id": source_opaque_id, "reason": "no provenance match"})
            continue
        except ValueError as exc:
            excluded_rows["ambiguous"].append({"candidate_id": source_opaque_id, "source_opaque_id": source_opaque_id, "reason": str(exc)})
            continue
        row = _build_candidate_row(
            {
                "source_opaque_id": source_opaque_id,
                "relevance_signals": candidate.get("relevance_signals") or {},
                "privacy_review_state": candidate.get("privacy_risk"),
                "source_stratum": "relevance_positive",
            },
            provenance_row,
            inventory_rows,
            review_dispositions_by_source,
            promoted_source_ids,
        )
        if candidate.get("privacy_risk") == "quarantined":
            excluded_rows["quarantined"].append(row)
            continue
        if row["already_promoted"]:
            excluded_rows["already_promoted"].append(row)
        elif row["already_reviewed"]:
            excluded_rows["already_reviewed"].append(row)
        if row["reviewable"]:
            reviewable_rows.append(row)
    reviewable_rows = sorted(reviewable_rows, key=lambda row: (-int(row.get("semantic_score") or 0), row["conversation_index"] if row["conversation_index"] is not None else 10**9, row["stable_candidate_id"]))
    return reviewable_rows, excluded_rows


def build_candidate_brief_index(
    candidate_briefs: Iterable[dict[str, Any]],
    provenance_rows: Iterable[dict[str, Any]],
    inventory_rows: Iterable[dict[str, Any]],
    reviewed_by_source: dict[str, dict[str, Any]],
    promoted_source_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    provenance_rows = list(provenance_rows)
    reviewable_rows: list[dict[str, Any]] = []
    excluded_rows: dict[str, list[dict[str, Any]]] = {
        "unresolved": [],
        "ambiguous": [],
        "already_reviewed": [],
        "already_promoted": [],
    }
    for candidate in candidate_briefs:
        source_opaque_id = candidate.get("source_opaque_id")
        if not isinstance(source_opaque_id, str) or not source_opaque_id:
            excluded_rows["unresolved"].append({"candidate_id": candidate.get("candidate_id"), "reason": "missing source_opaque_id"})
            continue
        try:
            provenance_row = resolve_unique_provenance(source_opaque_id, provenance_rows)
        except LookupError:
            excluded_rows["unresolved"].append({"candidate_id": candidate.get("candidate_id"), "source_opaque_id": source_opaque_id, "reason": "no provenance match"})
            continue
        except ValueError as exc:
            excluded_rows["ambiguous"].append({"candidate_id": candidate.get("candidate_id"), "source_opaque_id": source_opaque_id, "reason": str(exc)})
            continue
        row = _build_candidate_row(candidate, provenance_row, inventory_rows, reviewed_by_source, promoted_source_ids)
        if row["already_promoted"]:
            excluded_rows["already_promoted"].append(row)
        elif row["already_reviewed"]:
            excluded_rows["already_reviewed"].append(row)
        if row["reviewable"]:
            reviewable_rows.append(row)
    reviewable_rows = sorted(reviewable_rows, key=lambda row: (row["semantic_score"] is None, -(row["semantic_score"] or 0), row["stable_candidate_id"]))
    return reviewable_rows, excluded_rows


def build_review_index(work_root: Path = DEFAULT_WORK_ROOT, *, repo_root: Path = ROOT) -> ReviewIndexResult:
    provenance = _group_provenance(work_root)
    inventory_map = _group_inventory(work_root)
    current_rows = _load_current_relevance_rows(work_root)
    review_dispositions_by_source = _load_review_batch_dispositions(work_root)
    promoted_source_ids = _load_promoted_source_ids(repo_root)
    reviewable_rows, excluded_rows = _build_current_candidate_index(
        current_rows,
        provenance.values(),
        inventory_map.values(),
        review_dispositions_by_source,
        promoted_source_ids,
    )

    control_background_rows: list[dict[str, Any]] = []
    for row in _load_background_semantic_rows(work_root):
        opaque = row.get("opaque_id")
        if isinstance(opaque, str) and opaque in provenance:
            resolved = provenance[opaque]
            control_background_rows.append(
                {
                    "stable_candidate_id": row.get("review_id"),
                    "source_opaque_id": opaque,
                    "conversation_id": resolved["conversation_id"],
                    "title": _safe_conversation_title(resolved),
                    "source_stratum": row.get("stratum") or "background",
                    "privacy_state": row.get("privacy_state"),
                    "reason": "control/background row excluded from current intake review queue",
                }
            )
        else:
            control_background_rows.append(
                {
                    "stable_candidate_id": row.get("review_id"),
                    "source_opaque_id": opaque,
                    "reason": "control/background row without current-intake provenance",
                    "source_stratum": row.get("stratum") or "background",
                    "privacy_state": row.get("privacy_state"),
                }
            )
    excluded_rows["control_background"] = control_background_rows

    stale_namespace_rows: list[dict[str, Any]] = []
    candidate_briefs = _load_candidate_briefs(work_root)
    for candidate in candidate_briefs:
        source_opaque = candidate.get("source_opaque_id")
        if not isinstance(source_opaque, str) or not source_opaque:
            stale_namespace_rows.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "reason": "missing source_opaque_id",
                    "source_namespace": "lineage-extraction-v1",
                }
            )
            continue
        if source_opaque not in provenance or source_opaque not in inventory_map:
            stale_namespace_rows.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "source_opaque_id": source_opaque,
                    "source_review_id": candidate.get("source_review_id"),
                    "reason": "stale_namespace",
                    "source_namespace": "lineage-extraction-v1",
                }
            )
    excluded_rows["stale_namespace"] = stale_namespace_rows

    current_total = len(inventory_map)
    current_relevance_positive = len(current_rows)
    current_quarantined = sum(1 for row in current_rows if row.get("privacy_risk") == "quarantined")
    current_promoted = len([row for row in reviewable_rows if row["already_promoted"]])
    current_reviewed = len(excluded_rows["already_reviewed"]) + len(excluded_rows["already_promoted"])
    remaining_reviewable = len(reviewable_rows)

    summary = {
        "current_export_total_conversations": current_total,
        "current_export_relevance_positive": current_relevance_positive,
        "current_export_relevance_negative": current_total - current_relevance_positive,
        "current_intake_rows": current_relevance_positive,
        "current_joined_rows": current_relevance_positive - len(excluded_rows["unresolved"]) - len(excluded_rows["ambiguous"]),
        "current_unresolved_rows": len(excluded_rows["unresolved"]),
        "current_ambiguous_rows": len(excluded_rows["ambiguous"]),
        "current_quarantined_rows": current_quarantined,
        "current_already_reviewed_rows": current_reviewed,
        "current_already_promoted_rows": len(excluded_rows["already_promoted"]),
        "current_remaining_reviewable_rows": remaining_reviewable,
        "historical_stale_candidate_briefs": len(candidate_briefs),
        "historical_stale_namespace_rows": len(stale_namespace_rows),
        "historical_control_background_rows": len(control_background_rows),
        "join_invariant": "exactly one current-intake provenance record per current relevance row; zero or many matches fail closed",
        "control_background_rule": "semantic ranking rows and historical lineage briefs without current-intake provenance are excluded",
    }
    reconciliation = {
        "current_export": {
            "total_conversations": current_total,
            "relevance_positive": current_relevance_positive,
            "relevance_negative": current_total - current_relevance_positive,
        },
        "current_relevance_population": {
            "provenance_resolved": current_relevance_positive - len(excluded_rows["unresolved"]) - len(excluded_rows["ambiguous"]),
            "unresolved": len(excluded_rows["unresolved"]),
            "ambiguous": len(excluded_rows["ambiguous"]),
            "quarantined": current_quarantined,
            "already_reviewed": current_reviewed,
            "already_promoted": len(excluded_rows["already_promoted"]),
            "remaining_reviewable": remaining_reviewable,
        },
        "review_history": {
            "accept_for_next_packet": sum(1 for row in review_dispositions_by_source.values() if row["disposition"] == "accept_for_next_packet"),
            "defer_privacy_review": sum(1 for row in review_dispositions_by_source.values() if row["disposition"] == "defer_privacy_review"),
            "reject_low_value": sum(1 for row in review_dispositions_by_source.values() if row["disposition"] == "reject_low_value"),
            "promoted": len(excluded_rows["already_promoted"]),
        },
        "historical_stale_artifacts": {
            "lineage_candidate_briefs": len(candidate_briefs),
            "stale_namespace": len(stale_namespace_rows),
            "control_background": len(control_background_rows),
        },
        "opaque_namespace_model": {
            "current_intake": "stable within the current 2026-09-02 export namespace",
            "historical_lineage_extraction": "stale, bounded experiment namespace",
        },
    }
    return ReviewIndexResult(
        summary=summary,
        reviewable_rows=reviewable_rows,
        excluded_rows=excluded_rows,
        unresolved_rows=excluded_rows["unresolved"],
        ambiguous_rows=excluded_rows["ambiguous"],
        already_reviewed_rows=excluded_rows["already_reviewed"],
        already_promoted_rows=excluded_rows["already_promoted"],
        reconciliation=reconciliation,
    )


def write_review_index(work_root: Path = DEFAULT_WORK_ROOT, *, repo_root: Path = ROOT) -> tuple[Path, Path, ReviewIndexResult]:
    out_dir = work_root / "review-index-v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = build_review_index(work_root=work_root, repo_root=repo_root)
    index_path = out_dir / "review-candidate-index.json"
    queue_path = out_dir / "remaining-ranked-review-queue.json"
    reconciliation_path = out_dir / "reconciliation.json"
    index_payload = result.to_dict()
    queue_payload = {
        "summary": result.summary,
        "reviewable_rows": result.reviewable_rows,
    }
    reconciliation_payload = result.reconciliation or result.summary
    index_path.write_text(json.dumps(index_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    queue_path.write_text(json.dumps(queue_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reconciliation_path.write_text(json.dumps(reconciliation_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index_path, queue_path, result


def build_current_intake_candidate_lookup(work_root: Path = DEFAULT_WORK_ROOT, *, repo_root: Path = ROOT) -> dict[str, dict[str, Any]]:
    result = build_review_index(work_root=work_root, repo_root=repo_root)
    rows: dict[str, dict[str, Any]] = {}
    for row in result.reviewable_rows:
        candidate_id = row.get("candidate_id") or row.get("stable_candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            rows[candidate_id] = row
    for bucket_rows in result.excluded_rows.values():
        for row in bucket_rows:
            candidate_id = row.get("candidate_id") or row.get("stable_candidate_id") or row.get("source_opaque_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                continue
            if row.get("source_namespace") not in {None, "current-chatgpt-export"}:
                continue
            rows.setdefault(candidate_id, row)
    return rows


def resolve_current_intake_candidate(candidate_id: str, work_root: Path = DEFAULT_WORK_ROOT, *, repo_root: Path = ROOT) -> dict[str, Any]:
    lookup = build_current_intake_candidate_lookup(work_root=work_root, repo_root=repo_root)
    row = lookup.get(candidate_id)
    if row is None:
        raise LookupError(f"no current-export candidate for {candidate_id}")
    return row
