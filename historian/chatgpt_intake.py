from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import secrets
import subprocess
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_WORK_ROOT = Path(__file__).resolve().parents[1] / ".work" / "chatgpt-intake"
try:
    _PATH_IS_RELATIVE_TO = Path.is_relative_to
except AttributeError:  # pragma: no cover - Python < 3.9 compatibility guard
    _PATH_IS_RELATIVE_TO = None


def private_root(work_root: Path = DEFAULT_WORK_ROOT) -> Path:
    return work_root / "private"


def staging_root(work_root: Path = DEFAULT_WORK_ROOT) -> Path:
    return work_root / "staging"


def cleared_root(work_root: Path = DEFAULT_WORK_ROOT) -> Path:
    return work_root / "cleared"


def secret_path(work_root: Path = DEFAULT_WORK_ROOT) -> Path:
    return private_root(work_root) / "opaque-id.key"


def private_provenance_path(work_root: Path = DEFAULT_WORK_ROOT) -> Path:
    return private_root(work_root) / "provenance.json"


def inventory_path(work_root: Path = DEFAULT_WORK_ROOT) -> Path:
    return staging_root(work_root) / "inventory.json"


@dataclass(frozen=True)
class ChatGPTConversationSource:
    kind: str
    root: Path
    shard_paths: tuple[Path, ...] = ()
    shard_meta: tuple[dict[str, Any], ...] = ()
    source_label: str | None = None


SHARD_RE = re.compile(r"^conversations-(\d+)\.json$")


def _repo_root() -> Path | None:
    try:
        return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _path_is_ignored(path: Path) -> bool:
    result = subprocess.run(["git", "check-ignore", "-q", "--", str(path)], capture_output=True, text=True, check=False)
    return result.returncode == 0


def _path_is_inside_repo(path: Path) -> bool:
    repo_root = _repo_root()
    if repo_root is None:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if _PATH_IS_RELATIVE_TO is not None:
        return resolved.is_relative_to(repo_root)
    return str(resolved).startswith(str(repo_root))  # pragma: no cover


def raw_export_protected(export_path: Path, work_root: Path = DEFAULT_WORK_ROOT) -> bool:
    repo_root = _repo_root()
    if repo_root is None:
        return False
    try:
        resolved = export_path.resolve()
    except OSError:
        return False
    if _PATH_IS_RELATIVE_TO is not None:
        in_repo = resolved.is_relative_to(repo_root)
    else:  # pragma: no cover
        in_repo = str(resolved).startswith(str(repo_root))
    if not in_repo:
        return True
    return _path_is_ignored(resolved)


def intake_boundary_ok(export_path: Path, *, work_root: Path = DEFAULT_WORK_ROOT) -> tuple[bool, list[dict[str, Any]]]:
    private_root_ignored = True
    staging_root_ignored = True
    cleared_root_ignored = True
    key_path_ignored = True
    private_provenance_ignored = True
    inventory_path_ignored = True
    if _path_is_inside_repo(private_root(work_root)):
        private_root_ignored = _path_is_ignored(private_root(work_root))
    if _path_is_inside_repo(staging_root(work_root)):
        staging_root_ignored = _path_is_ignored(staging_root(work_root))
    if _path_is_inside_repo(cleared_root(work_root)):
        cleared_root_ignored = _path_is_ignored(cleared_root(work_root))
    if _path_is_inside_repo(secret_path(work_root)):
        key_path_ignored = _path_is_ignored(secret_path(work_root))
    if _path_is_inside_repo(private_provenance_path(work_root)):
        private_provenance_ignored = _path_is_ignored(private_provenance_path(work_root))
    if _path_is_inside_repo(inventory_path(work_root)):
        inventory_path_ignored = _path_is_ignored(inventory_path(work_root))
    checks = [
        {"name": "export_exists", "ok": export_path.exists()},
        {"name": "private_root_ignored", "ok": private_root_ignored},
        {"name": "staging_root_ignored", "ok": staging_root_ignored},
        {"name": "cleared_root_ignored", "ok": cleared_root_ignored},
        {"name": "key_path_ignored", "ok": key_path_ignored},
        {"name": "private_provenance_ignored", "ok": private_provenance_ignored},
        {"name": "inventory_path_ignored", "ok": inventory_path_ignored},
        {"name": "raw_export_protected", "ok": raw_export_protected(export_path, work_root=work_root)},
    ]
    return all(item["ok"] for item in checks), checks

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"\b(?:sk-|rk-|pk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{8,}\b", re.I)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]{12,}\b", re.I)),
    ("auth_query_token", re.compile(r"(?i)(?:[?&](?:token|access_token|auth|authorization)=)[A-Za-z0-9._\-+/=]{12,}")),
    ("password_assignment", re.compile(r"(?i)\bpassword\s*[:=]\s*[^\\s]{6,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("high_entropy_token", re.compile(r"(?i)(?:\b(?:secret|token|credential|api[_-]?key|passwd|password)\b[^A-Za-z0-9]{0,12})([A-Za-z0-9+/=_-]{24,})")),
]
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\b(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b")),
    ("address", re.compile(r"\b\d{1,5}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Parkway|Pkwy)\b", re.I)),
    ("account_id", re.compile(r"\b[A-Z0-9]{8,}-[A-Z0-9]{4,}\b", re.I)),
]


@dataclass
class Detection:
    category: str
    severity: str
    count: int


@dataclass
class MessageNode:
    message_id: str
    parent_id: str | None
    role: str | None
    create_time: int | None
    update_time: int | None
    text: str
    content_text_count: int = 0
    content_metadata_count: int = 0
    content_unknown_count: int = 0
    attachments: list[dict[str, Any]] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _stream_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_path_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    return _stream_sha256(path, chunk_size=chunk_size)


def load_export(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("conversations"), list):
        return payload["conversations"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"{path}: unsupported ChatGPT export shape")


def _parse_shard_index(path: Path) -> int | None:
    match = SHARD_RE.match(path.name)
    return int(match.group(1)) if match else None


def resolve_conversation_source(source: Path, *, work_root: Path = DEFAULT_WORK_ROOT) -> ChatGPTConversationSource:
    if source.is_dir():
        shard_pairs: list[tuple[int, Path]] = []
        for child in source.iterdir():
            shard_index = _parse_shard_index(child)
            if shard_index is None:
                continue
            if not child.is_file():
                raise ValueError(f"{child}: shard member must be a regular file")
            shard_pairs.append((shard_index, child))
        if shard_pairs:
            indices = [index for index, _ in shard_pairs]
            if len(indices) != len(set(indices)):
                raise ValueError(f"{source}: duplicate shard index")
            ordered = tuple(path for _, path in sorted(shard_pairs, key=lambda item: item[0]))
            shard_meta = tuple(_build_shard_meta(path) for path in ordered)
            return ChatGPTConversationSource(kind="sharded_json", root=source, shard_paths=ordered, shard_meta=shard_meta, source_label=source.name)
        raise ValueError(f"{source}: no conversation shards found")
    if source.is_file() and zipfile.is_zipfile(source):
        return prepare_zip_conversation_source(source, work_root=work_root)
    if source.is_file():
        return ChatGPTConversationSource(kind="single_json", root=source, source_label=source.name)
    raise ValueError(f"{source}: unsupported ChatGPT export source")


def _safe_zip_member(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\"):
        return False
    normalized = Path(name)
    return not any(part == ".." for part in normalized.parts) and SHARD_RE.match(normalized.name) is not None


def prepare_zip_conversation_source(zip_path: Path, *, work_root: Path = DEFAULT_WORK_ROOT) -> ChatGPTConversationSource:
    if _path_is_inside_repo(work_root) and not _path_is_ignored(work_root):
        raise ValueError("intake work root must be ignored")
    zip_digest = _stream_sha256(zip_path)
    extract_root = work_root / "raw-extract" / f"{zip_path.name}-{zip_digest[:16]}"
    if extract_root.is_symlink():
        raise ValueError(f"{zip_path}: extraction root is a symbolic link")
    if extract_root.exists():
        if extract_root.is_file():
            raise ValueError(f"{zip_path}: extraction root is not a directory")
        unexpected = sorted(str(child) for child in extract_root.iterdir() if child.is_dir() and not child.is_symlink())
        if unexpected:
            raise ValueError(f"{zip_path}: extraction root contains unexpected directories: {unexpected}")
        for child in extract_root.iterdir():
            child.unlink()
    extract_root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(extract_root, 0o700)
    except OSError:
        pass
    with zipfile.ZipFile(zip_path) as archive:
        selected_infos: list[zipfile.ZipInfo] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            if info.filename.startswith("/") or info.filename.startswith("\\") or ".." in Path(info.filename).parts:
                raise ValueError(f"{zip_path}: unsafe archive member {info.filename}")
            if not _safe_zip_member(info.filename):
                continue
            mode = info.external_attr >> 16
            if mode and not stat.S_ISREG(mode):
                raise ValueError(f"{zip_path}: archive member must be a regular file: {info.filename}")
            selected_infos.append(info)
        if not selected_infos:
            raise ValueError(f"{zip_path}: no conversation shards found")
        indices = [_parse_shard_index(Path(info.filename)) for info in selected_infos]
        if any(index is None for index in indices):
            raise ValueError(f"{zip_path}: malformed shard name")
        if len(indices) != len(set(indices)):
            raise ValueError(f"{zip_path}: duplicate shard index")
        ordered_infos = [info for _, info in sorted(zip(indices, selected_infos), key=lambda item: item[0])]
        shard_paths: list[Path] = []
        shard_meta: list[dict[str, Any]] = []
        shard_manifest: dict[str, Any] = {
            "source_kind": "sharded_json",
            "source_label": zip_path.name,
            "source_zip_sha256": zip_digest,
            "source_root": str(extract_root),
            "discovered_shard_count": len(ordered_infos),
            "prepared_shard_count": 0,
            "shards": [],
        }
        for info in ordered_infos:
            target = extract_root / Path(info.filename).name
            with archive.open(info, "r") as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            shard_paths.append(target)
            meta = _build_shard_meta(target)
            shard_meta.append(meta)
            shard_manifest["shards"].append(meta)
        shard_manifest["prepared_shard_count"] = len(shard_paths)
    private_root(work_root).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(private_root(work_root), 0o700)
    except OSError:
        pass
    shard_manifest_path = private_root(work_root) / "shard-manifest.json"
    shard_manifest_path.write_text(json.dumps(shard_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(shard_manifest_path, 0o600)
    except OSError:
        pass
    if not shard_paths:
        raise ValueError(f"{zip_path}: no conversation shards found")
    ordered = tuple(path for _, path in sorted(zip(indices, shard_paths), key=lambda item: item[0]))
    return ChatGPTConversationSource(kind="sharded_json", root=zip_path, shard_paths=ordered, shard_meta=tuple(shard_meta), source_label=zip_path.name)


def _build_shard_meta(path: Path) -> dict[str, Any]:
    return {
        "index": _parse_shard_index(path),
        "name": path.name,
        "shard_name": path.name,
        "shard_size": path.stat().st_size,
        "shard_sha256": _stream_sha256(path),
        "source_label": path.name,
    }


def _load_conversation_batches_from_source(source: ChatGPTConversationSource):
    if source.kind == "single_json":
        payload = _load_json(source.root)
        conversations, meta = _coerce_conversation_payload(payload, source_label=source.source_label or source.root.name)
        yield meta, conversations
        return
    if source.kind == "sharded_json":
        for index, shard_path in enumerate(source.shard_paths):
            payload = _load_json(shard_path)
            shard_conversations, meta = _coerce_conversation_payload(payload, source_label=shard_path.name)
            shard_info = dict(source.shard_meta[index]) if index < len(source.shard_meta) else _build_shard_meta(shard_path)
            shard_info["conversation_count"] = len(shard_conversations)
            shard_info["source_kind"] = "sharded_json"
            shard_info["source_label"] = meta.get("source_label", shard_path.name)
            yield shard_info, shard_conversations
        return
    raise ValueError(f"{source.root}: unsupported conversation source kind")


def _coerce_conversation_payload(payload: Any, *, source_label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("conversations"), list):
        conversations = [conv for conv in payload["conversations"] if isinstance(conv, dict)]
        return conversations, {"source_label": source_label}
    if isinstance(payload, list):
        conversations = [conv for conv in payload if isinstance(conv, dict)]
        return conversations, {"source_label": source_label}
    raise ValueError(f"{source_label}: unsupported ChatGPT export shape")


def _source_fingerprint(source: ChatGPTConversationSource) -> str:
    digest = hashlib.sha256()
    if source.kind == "single_json":
        digest.update(_stream_sha256(source.root).encode("utf-8"))
        return digest.hexdigest()
    if source.kind == "sharded_json":
        for shard_path, shard_meta in zip(source.shard_paths, source.shard_meta):
            shard_digest = str(shard_meta.get("shard_sha256") or _stream_sha256(shard_path))
            digest.update(shard_path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(shard_meta.get("shard_size") or shard_path.stat().st_size).encode("utf-8"))
            digest.update(b"\0")
            digest.update(shard_digest.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()
    raise ValueError(f"{source.root}: unsupported conversation source kind")


def _coerce_text(content: Any) -> tuple[str, int, int, int]:
    if isinstance(content, str):
        return content, 1, 0, 0
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            text_parts, metadata_count, unknown_count = _coerce_parts(parts)
            return "\n".join(text_parts), len(text_parts), metadata_count, unknown_count
        if isinstance(content.get("text"), str):
            return content["text"], 1, 0, 0
    if isinstance(content, list):
        text_parts, metadata_count, unknown_count = _coerce_parts(content)
        return "\n".join(text_parts), len(text_parts), metadata_count, unknown_count
    return "", 0, 0, 1


def _coerce_parts(parts: list[Any]) -> tuple[list[str], int, int]:
    text_parts: list[str] = []
    metadata_count = 0
    unknown_count = 0
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, (int, float, bool)) or part is None:
            metadata_count += 1
        elif isinstance(part, dict):
            kind = str(part.get("type") or part.get("kind") or part.get("content_type") or "").lower()
            text_value = part.get("text")
            if isinstance(text_value, str):
                text_parts.append(text_value)
                continue
            if kind in {"text", "message", "tool_result", "tool-output"}:
                joined = part.get("parts")
                if isinstance(joined, list):
                    nested_text, nested_meta, nested_unknown = _coerce_parts(joined)
                    text_parts.extend(nested_text)
                    metadata_count += nested_meta
                    unknown_count += nested_unknown
                    continue
                metadata_count += 1
                continue
            if kind in {"image", "file", "reference", "url", "citation", "tool", "audio", "video"}:
                metadata_count += 1
                continue
            unknown_count += 1
        else:
            unknown_count += 1
    return text_parts, metadata_count, unknown_count


def _walk_messages(raw_messages: list[dict[str, Any]]) -> dict[str, MessageNode]:
    nodes: dict[str, MessageNode] = {}
    for raw in raw_messages:
        message_id = str(raw.get("id") or raw.get("message_id") or "")
        if not message_id:
            raise ValueError("conversation contains a message without an id")
        if message_id in nodes:
            raise ValueError(f"duplicate message id: {message_id}")
        text, text_count, metadata_count, unknown_count = _coerce_text(raw.get("content"))
        node = MessageNode(
            message_id=message_id,
            parent_id=raw.get("parent_id"),
            role=raw.get("author", {}).get("role") if isinstance(raw.get("author"), dict) else raw.get("role"),
            create_time=raw.get("create_time"),
            update_time=raw.get("update_time"),
            text=text,
            content_text_count=text_count,
            content_metadata_count=metadata_count,
            content_unknown_count=unknown_count,
            attachments=list(raw.get("attachments") or raw.get("files") or []),
        )
        nodes[message_id] = node
    for node in nodes.values():
        if node.parent_id and node.parent_id in nodes:
            nodes[node.parent_id].children.append(node.message_id)
    return nodes


def _conversation_messages(conversation: dict[str, Any]) -> list[MessageNode]:
    messages = conversation.get("mapping")
    raw_messages: list[dict[str, Any]] = []
    parent_key_to_message_id: dict[str, str] = {}
    if isinstance(messages, dict):
        for mapping_key, node in messages.items():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if isinstance(message, dict):
                message_id = str(message.get("id") or node.get("id") or "")
                if message_id:
                    parent_key_to_message_id[str(mapping_key)] = message_id
                raw_messages.append(
                    {
                        "id": message_id,
                        "parent_id": node.get("parent"),
                        "author": message.get("author"),
                        "role": message.get("role"),
                        "create_time": message.get("create_time"),
                        "update_time": message.get("update_time"),
                        "content": message.get("content"),
                        "attachments": message.get("attachments"),
                        "files": message.get("files"),
                    }
                )
    elif isinstance(conversation.get("messages"), list):
        raw_messages = [m for m in conversation["messages"] if isinstance(m, dict)]
    if not raw_messages:
        raise ValueError("conversation contains no parseable messages")
    for raw in raw_messages:
        parent_id = raw.get("parent_id")
        if isinstance(parent_id, str) and parent_id in parent_key_to_message_id:
            raw["parent_id"] = parent_key_to_message_id[parent_id]
    nodes = _walk_messages(raw_messages)
    ordered: list[MessageNode] = []
    roots = [node for node in nodes.values() if not node.parent_id or node.parent_id not in nodes]
    if not roots:
        raise ValueError("conversation has no root message")
    visited: set[str] = set()
    active: set[str] = set()
    for root in sorted(roots, key=lambda n: (n.create_time is None, n.create_time or 0, n.message_id)):
        stack: list[tuple[MessageNode, int]] = [(root, 0)]
        while stack:
            node, state = stack.pop()
            if state == 0:
                if node.message_id in active:
                    raise ValueError("conversation message graph contains a cycle")
                if node.message_id in visited:
                    raise ValueError("conversation message graph revisits a completed node")
                active.add(node.message_id)
                ordered.append(node)
                stack.append((node, 1))
                children = sorted(node.children, key=lambda cid: (nodes[cid].create_time is None, nodes[cid].create_time or 0, cid))
                for child_id in reversed(children):
                    child = nodes[child_id]
                    if child.message_id in active:
                        raise ValueError("conversation message graph contains a cycle")
                    if child.message_id in visited:
                        raise ValueError("conversation message graph revisits a completed node")
                    stack.append((child, 0))
            else:
                active.remove(node.message_id)
                visited.add(node.message_id)
    return ordered


def detect_categories(text: str) -> list[Detection]:
    findings: dict[str, Detection] = {}
    for category, pattern in SECRET_PATTERNS:
        count = len(pattern.findall(text))
        if count:
            findings[category] = Detection(category, "high", count)
    for category, pattern in PII_PATTERNS:
        count = len(pattern.findall(text))
        if count:
            findings[category] = Detection(category, "medium" if category != "address" else "high", count)
    return sorted(findings.values(), key=lambda item: item.category)


def sanitize_text(text: str) -> tuple[str, list[Detection]]:
    findings = detect_categories(text)
    sanitized = text
    for _, pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("[SECRET_REDACTED]", sanitized)
    for category, pattern in PII_PATTERNS:
        replacement = f"[{category.upper()}_REDACTED]"
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized, findings


def _ensure_secret(secret_file: Path) -> bytes:
    if secret_file.exists():
        return secret_file.read_bytes().strip()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_bytes(32)
    secret_file.write_bytes(value)
    try:
        os.chmod(secret_file, 0o600)
    except OSError:
        pass
    return value


def opaque_id(secret: bytes, raw_identity: str) -> str:
    return hmac.new(secret, raw_identity.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def _conversation_stats(nodes: list[MessageNode]) -> dict[str, Any]:
    text = "\n".join(node.text for node in nodes)
    message_ids = {node.message_id for node in nodes}
    role_counts: dict[str, int] = {}
    for node in nodes:
        role_counts[node.role or "unknown"] = role_counts.get(node.role or "unknown", 0) + 1
    return {
        "message_count": len(nodes),
        "role_counts": role_counts,
        "approx_text_size": len(text.encode("utf-8")),
        "text_part_count": sum(node.content_text_count for node in nodes),
        "metadata_part_count": sum(node.content_metadata_count for node in nodes),
        "unknown_part_count": sum(node.content_unknown_count for node in nodes),
        "branch_count": sum(1 for node in nodes if len(node.children) > 1),
        "leaf_count": sum(1 for node in nodes if not node.children),
        "has_branches": any(len(node.children) > 1 for node in nodes),
        "date_range": {
            "created_min": min((n.create_time for n in nodes if n.create_time is not None), default=None),
            "created_max": max((n.create_time for n in nodes if n.create_time is not None), default=None),
            "updated_max": max((n.update_time for n in nodes if n.update_time is not None), default=None),
        },
    }


def inventory_export(export_path: Path, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict[str, Any]:
    boundary_ok, _ = intake_boundary_ok(export_path, work_root=work_root)
    if not boundary_ok:
        raise ValueError("intake boundary validation failed")
    source = resolve_conversation_source(export_path, work_root=work_root)
    source_hash = _source_fingerprint(source)
    secret = _ensure_secret(secret_path(work_root))
    private_records: list[dict[str, Any]] = []
    public_records: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    seen_conversation_ids: set[str] = set()
    shard_count = 0
    conversations_total = 0
    conversations_inventory = 0
    quarantined = 0
    pii_flagged = 0
    relevance_signal_conversations = 0
    shard_manifest: dict[str, Any] = {
        "source_kind": source.kind,
        "source_label": source.source_label or source.root.name,
        "source_fingerprint": source_hash,
        "shards": [],
        "discovered_shard_count": 0,
        "processed_shard_count": 0,
    }
    for batch_meta, conversations in _load_conversation_batches_from_source(source):
        shard_count += 1
        if source.kind == "sharded_json":
            shard_manifest["shards"].append(batch_meta)
            shard_manifest["discovered_shard_count"] = shard_count
        for index, conversation in enumerate(conversations):
            conversations_total += 1
            conv_id = str(conversation.get("id") or conversation.get("conversation_id") or f"conv-{conversations_total - 1}")
            opaque = opaque_id(secret, f"{source_hash}:{conv_id}:{conversations_total - 1}")
            shard_name = batch_meta.get("shard_name") if isinstance(batch_meta, dict) else None
            if conv_id in seen_conversation_ids:
                anomalies.append(
                    {
                        "opaque_source_id": opaque,
                        "issue": "duplicate_conversation_id",
                        "error_code": "duplicate_conversation_id",
                        "status": "quarantined",
                    }
                )
                private_records.append(
                    {
                        "opaque_private_id": opaque,
                        "conversation_id": conv_id,
                        "raw_archive_path": str(export_path),
                        "raw_archive_sha256": source_hash,
                        "error_code": "duplicate_conversation_id",
                        "source_shard": shard_name,
                    }
                )
                continue
            seen_conversation_ids.add(conv_id)
            try:
                messages = _conversation_messages(conversation)
                stats = _conversation_stats(messages)
                raw_text = "\n".join(node.text for node in messages)
                findings = detect_categories(raw_text)
                message_ids = [node.message_id for node in messages]
                message_id_set = set(message_ids)
                private_records.append(
                    {
                        "opaque_private_id": opaque,
                        "conversation_id": conv_id,
                        "raw_archive_path": str(export_path),
                        "raw_archive_sha256": source_hash,
                        "source_kind": source.kind,
                        "source_shard": shard_name,
                        "message_ids": message_ids,
                        "parent_message_ids": [node.parent_id for node in messages],
                        "root_message_id": next((node.message_id for node in messages if node.parent_id is None or node.parent_id not in message_id_set), None),
                        "branch_structure": {
                            "has_branches": stats["has_branches"],
                            "branch_count": stats["branch_count"],
                            "leaf_count": stats["leaf_count"],
                        },
                        "title": conversation.get("title"),
                        "timestamps": {
                            "created_min": stats["date_range"]["created_min"],
                            "created_max": stats["date_range"]["created_max"],
                            "updated_max": stats["date_range"]["updated_max"],
                        },
                        "detector_categories": [item.category for item in findings],
                    }
                )
                privacy_risk = "quarantined" if any(item.severity == "high" for item in findings) else ("privacy_review_required" if findings else "safe_metadata_only")
                public_records.append(
                    {
                        "opaque_source_id": opaque,
                        "intake_version": "v1",
                        "sanitizer_identity": "historian.chatgpt_intake.v1",
                        "transformations": ["parse", "inventory"],
                        "message_count": stats["message_count"],
                        "role_counts": stats["role_counts"],
                        "approx_text_size": stats["approx_text_size"],
                        "text_part_count": stats["text_part_count"],
                        "metadata_part_count": stats["metadata_part_count"],
                        "unknown_part_count": stats["unknown_part_count"],
                        "date_range": stats["date_range"],
                        "branch_count": stats["branch_count"],
                        "leaf_count": stats["leaf_count"],
                        "has_branches": stats["has_branches"],
                        "has_attachments": any(node.attachments for node in messages),
                        "detector_categories": [item.category for item in findings],
                        "privacy_risk": privacy_risk,
                        "relevance_signals": build_relevance_signals(conversation, raw_text),
                    }
                )
                conversations_inventory += 1
                if privacy_risk == "quarantined":
                    quarantined += 1
                if privacy_risk != "safe_metadata_only":
                    pii_flagged += 1
                if public_records[-1]["relevance_signals"]["keyword_total"] > 0:
                    relevance_signal_conversations += 1
            except ValueError as exc:
                anomalies.append(
                    {
                        "opaque_source_id": opaque,
                        "issue": "malformed_conversation",
                        "error_code": "malformed_conversation",
                        "status": "quarantined",
                    }
                )
                private_records.append(
                    {
                        "opaque_private_id": opaque,
                        "conversation_id": conv_id,
                        "raw_archive_path": str(export_path),
                        "raw_archive_sha256": source_hash,
                        "source_kind": source.kind,
                        "source_shard": shard_name,
                        "error_code": "malformed_conversation",
                        "parser_error": type(exc).__name__,
                    }
                )
        shard_manifest["processed_shard_count"] += 1
    work_root.mkdir(parents=True, exist_ok=True)
    private_root(work_root).mkdir(parents=True, exist_ok=True)
    staging_root(work_root).mkdir(parents=True, exist_ok=True)
    cleared_root(work_root).mkdir(parents=True, exist_ok=True)
    for directory in (private_root(work_root), staging_root(work_root), cleared_root(work_root)):
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
    private_file = private_provenance_path(work_root)
    private_file.write_text(json.dumps({"items": private_records, "archive_sha256": source_hash, "shard_manifest": shard_manifest}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(private_file, 0o600)
    except OSError:
        pass
    inventory_file = inventory_path(work_root)
    inventory_file.write_text(json.dumps({"items": public_records, "anomalies": anomalies}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "complete",
        "conversation_shards": shard_count,
        "conversations_total": conversations_total,
        "conversations_inventory": conversations_inventory,
        "anomalies": len(anomalies),
        "quarantined": quarantined,
        "pii_flagged": pii_flagged,
        "relevance_signal_conversations": relevance_signal_conversations,
        "inventory_location": str(inventory_file),
        "private_provenance_location": str(private_file),
    }


DEFAULT_RELEVANCE_KEYWORDS = ["historian", "codex", "prompt patch", "logic probe", "router", "handoff", "qwen", "semantic classification", "provenance"]


def relevance_keywords() -> list[str]:
    override = os.environ.get("HISTORIAN_INTAKE_RELEVANCE_KEYWORDS")
    if override:
        return [item.strip() for item in override.split(",") if item.strip()]
    return list(DEFAULT_RELEVANCE_KEYWORDS)


def build_relevance_signals(conversation: dict[str, Any], raw_text: str) -> dict[str, Any]:
    haystack = f"{conversation.get('title', '')}\n{raw_text}".lower()
    hits = {keyword: haystack.count(keyword) for keyword in relevance_keywords() if haystack.count(keyword)}
    return {"keyword_hits": hits, "keyword_total": sum(hits.values())}


def preflight(export_path: Path, *, work_root: Path = DEFAULT_WORK_ROOT) -> dict[str, Any]:
    ok, checks = intake_boundary_ok(export_path, work_root=work_root)
    return {"ok": ok, "checks": checks}
