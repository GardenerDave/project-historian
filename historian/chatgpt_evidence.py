from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from historian.chatgpt_intake import _conversation_messages, _conversation_stats, _load_json, load_export, resolve_conversation_source
from historian.chatgpt_review_index import build_current_intake_candidate_lookup
from historian.source_locator import normalize_source_locator

ROOT = Path(__file__).resolve().parents[1]
AUTHORITATIVE_SOURCE_NAMESPACE = "current-chatgpt-export"
DEFAULT_WORK_ROOT = ROOT / ".work" / "chatgpt-intake"


@dataclass(frozen=True)
class ResolvedEvidenceMessage:
    message_id: str
    parent_id: str | None
    role: str | None
    create_time: int | None
    update_time: int | None
    text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.text is None:
            payload.pop("text", None)
        return payload


@dataclass(frozen=True)
class CandidateEvidenceResult:
    status: str
    candidate_id: str
    source_namespace: str
    conversation_id: str | None = None
    shard_name: str | None = None
    shard_path: str | None = None
    raw_archive_path: str | None = None
    bounded_evidence_locator: str | None = None
    message_ids: list[str] = field(default_factory=list)
    message_count: int | None = None
    conversation_title: str | None = None
    privacy_state: str | None = None
    evidence_messages: list[ResolvedEvidenceMessage] = field(default_factory=list)
    evidence_range: dict[str, str] | None = None
    error: str | None = None
    safe_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_messages"] = [message.to_dict() for message in self.evidence_messages]
        return payload


def _load_provenance_by_candidate(work_root: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(work_root / "private" / "provenance.json")
    return {row["opaque_private_id"]: row for row in payload["items"]}


def _load_shard_conversations(raw_archive_path: Path, work_root: Path) -> tuple[list[dict[str, Any]], Path]:
    source = resolve_conversation_source(raw_archive_path, work_root=work_root)
    if source.kind != "sharded_json":
        conversations = load_export(source.root)
        if not isinstance(conversations, list):
            raise ValueError(f"{source.root}: unsupported ChatGPT export shape")
        return conversations, source.root
    shard_paths = {path.name: path for path in source.shard_paths}
    return [], source.root  # replaced by caller-specific shard resolution


def _load_shard_path(raw_archive_path: Path, shard_name: str, work_root: Path) -> Path:
    source = resolve_conversation_source(raw_archive_path, work_root=work_root)
    if source.kind == "single_json":
        if shard_name and shard_name != source.root.name:
            raise FileNotFoundError(f"missing shard {shard_name}")
        return source.root
    for shard_path in source.shard_paths:
        if shard_path.name == shard_name:
            return shard_path
    raise FileNotFoundError(f"missing shard {shard_name}")


def _parse_locator(locator: str | None) -> tuple[str | None, str | None, str | None]:
    if not locator or "#" not in locator:
        return None, None, None
    locator = normalize_source_locator(locator)
    conversation_part, selector = locator.split("#", 1)
    if ".." in selector:
        start, end = selector.split("..", 1)
    else:
        start = selector
        end = selector
    return conversation_part or None, start or None, end or None


def _select_bounded_messages(messages: list[Any], locator: str) -> tuple[list[Any], dict[str, str]]:
    _, start_id, end_id = _parse_locator(locator)
    if not start_id or not end_id:
        raise LookupError("locator unresolved")
    message_index = {node.message_id: idx for idx, node in enumerate(messages)}
    if start_id not in message_index or end_id not in message_index:
        raise LookupError("locator unresolved")
    start_idx = message_index[start_id]
    end_idx = message_index[end_id]
    if start_idx > end_idx:
        raise LookupError("locator unresolved")
    selected = messages[start_idx : end_idx + 1]
    return selected, {"start_message_id": start_id, "end_message_id": end_id}


def resolve_candidate_evidence(
    candidate_id: str,
    source_namespace: str,
    *,
    work_root: Path = DEFAULT_WORK_ROOT,
    show_text: bool = False,
) -> CandidateEvidenceResult:
    lookup = build_current_intake_candidate_lookup(work_root=work_root, repo_root=ROOT)
    row = lookup.get(candidate_id)
    if row is None:
        return CandidateEvidenceResult(status="candidate_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, error="no current-export candidate")
    if row.get("source_namespace") not in {None, source_namespace}:
        return CandidateEvidenceResult(status="candidate_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, error="stale or mismatched source namespace")
    if row.get("source_namespace") != AUTHORITATIVE_SOURCE_NAMESPACE and row.get("source_namespace") is not None:
        return CandidateEvidenceResult(status="candidate_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, error="stale or mismatched source namespace")
    provenance_map = _load_provenance_by_candidate(work_root)
    provenance_row = provenance_map.get(candidate_id)
    if provenance_row is None:
        return CandidateEvidenceResult(status="candidate_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, error="missing authoritative provenance")
    raw_archive_path = provenance_row.get("raw_archive_path")
    shard_name = provenance_row.get("source_shard")
    if not isinstance(raw_archive_path, str) or not raw_archive_path.strip() or not isinstance(shard_name, str) or not shard_name.strip():
        return CandidateEvidenceResult(status="source_unavailable", candidate_id=candidate_id, source_namespace=source_namespace, error="missing source shard metadata")
    raw_archive = Path(raw_archive_path)
    if not raw_archive.is_absolute():
        candidate_paths = [work_root / raw_archive, ROOT / raw_archive, raw_archive]
        raw_archive = next((path for path in candidate_paths if path.exists()), raw_archive)
    if not raw_archive.exists():
        return CandidateEvidenceResult(status="source_unavailable", candidate_id=candidate_id, source_namespace=source_namespace, error="raw archive unavailable")
    try:
        shard_path = _load_shard_path(raw_archive, shard_name, work_root)
    except FileNotFoundError as exc:
        return CandidateEvidenceResult(status="source_unavailable", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, error=str(exc))
    try:
        conversations = load_export(shard_path)
    except ValueError as exc:
        return CandidateEvidenceResult(status="malformed_source", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), error=str(exc))
    if not isinstance(conversations, list):
        return CandidateEvidenceResult(status="malformed_source", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), error="unsupported shard payload")
    matches = [conversation for conversation in conversations if isinstance(conversation, dict) and (conversation.get("id") or conversation.get("conversation_id")) == provenance_row["conversation_id"]]
    if not matches:
        return CandidateEvidenceResult(status="conversation_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), conversation_id=provenance_row["conversation_id"], error="conversation not found in shard")
    if len(matches) > 1:
        return CandidateEvidenceResult(status="conversation_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), conversation_id=provenance_row["conversation_id"], error="duplicate conversation identity in shard")
    conversation = matches[0]
    try:
        messages = _conversation_messages(conversation)
    except ValueError as exc:
        return CandidateEvidenceResult(status="malformed_source", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), conversation_id=provenance_row["conversation_id"], error=str(exc))
    locator = str(row.get("bounded_evidence_locator") or row.get("source_locator") or "")
    if not locator:
        return CandidateEvidenceResult(status="locator_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), conversation_id=provenance_row["conversation_id"], error="missing bounded evidence locator")
    try:
        locator = normalize_source_locator(locator)
    except ValueError as exc:
        return CandidateEvidenceResult(status="locator_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), conversation_id=provenance_row["conversation_id"], bounded_evidence_locator=locator, error=str(exc))
    locator_conversation, start_id, end_id = _parse_locator(locator)
    if locator_conversation not in {None, provenance_row["conversation_id"]}:
        return CandidateEvidenceResult(status="locator_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), conversation_id=provenance_row["conversation_id"], bounded_evidence_locator=locator, error="locator conversation mismatch")
    try:
        selected_messages, evidence_range = _select_bounded_messages(messages, locator)
    except LookupError as exc:
        return CandidateEvidenceResult(status="locator_unresolved", candidate_id=candidate_id, source_namespace=source_namespace, raw_archive_path=str(raw_archive), shard_name=shard_name, shard_path=str(shard_path), conversation_id=provenance_row["conversation_id"], bounded_evidence_locator=locator, error=str(exc))
    safe_metadata = _conversation_stats(messages)
    evidence_messages = [
        ResolvedEvidenceMessage(
            message_id=node.message_id,
            parent_id=node.parent_id,
            role=node.role,
            create_time=node.create_time,
            update_time=node.update_time,
            text=node.text if show_text else None,
        )
        for node in selected_messages
    ]
    return CandidateEvidenceResult(
        status="resolved",
        candidate_id=candidate_id,
        source_namespace=source_namespace,
        conversation_id=provenance_row["conversation_id"],
        shard_name=shard_name,
        shard_path=str(shard_path),
        raw_archive_path=str(raw_archive),
        bounded_evidence_locator=locator,
        message_ids=[node.message_id for node in selected_messages],
        message_count=len(selected_messages),
        conversation_title=str(conversation.get("title") or conversation.get("conversation_title") or ""),
        privacy_state=row.get("privacy_state"),
        evidence_messages=evidence_messages,
        evidence_range=evidence_range,
        safe_metadata=safe_metadata,
    )
