from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from historian.chatgpt_evidence import CandidateEvidenceResult


DEFAULT_SCHEMA_VERSION = "historian.privacy_representation.v1"
PRIVACY_GATE_STATES = {"privacy_review_required", "privacy_deferred"}
SUPPORTED_EPISTEMIC_SHAPES = {
    "exploration",
    "hypothesis",
    "recommendation",
    "decision",
    "planned experiment",
    "executed experiment",
    "measurement",
    "demonstrated capability",
    "revision",
    "milestone",
    "claim",
}
DISALLOWED_CONTENT_KEYS = {
    "content",
    "conversation_text",
    "full_text",
    "full_transcript",
    "message_content",
    "message_text",
    "private_text",
    "raw_text",
    "source_text",
    "text",
    "transcript",
    "transcript_text",
}


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {field_name}")
    return value.strip()


def _collect_privacy_issues(payload: Any, *, _path: str = "") -> list[str]:
    issues: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if key in DISALLOWED_CONTENT_KEYS:
                issues.append(f"{_path}{key}")
            issues.extend(_collect_privacy_issues(value, _path=f"{_path}{key}."))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            issues.extend(_collect_privacy_issues(item, _path=f"{_path}{index}."))
    return issues


def _evidence_reference_from_result(result: CandidateEvidenceResult) -> dict[str, Any]:
    reference = {
        "candidate_id": result.candidate_id,
        "source_namespace": result.source_namespace,
        "conversation_id": result.conversation_id,
        "shard_name": result.shard_name,
        "shard_path": result.shard_path,
        "bounded_evidence_locator": result.bounded_evidence_locator,
        "message_ids": list(result.message_ids),
        "message_count": result.message_count,
    }
    return {key: value for key, value in reference.items() if value is not None and value != [] and value != {}}


@dataclass(frozen=True)
class PrivacySafeRepresentation:
    schema_version: str
    candidate_id: str
    source_namespace: str
    privacy_safe: bool
    semantic_review_ready: bool
    proposition: str | None = None
    epistemic_shape: str | None = None
    evidence_reference: dict[str, Any] | None = None
    transformation_method: str = "manual.regeneration"
    transformation_version: str = DEFAULT_SCHEMA_VERSION
    insufficiency_reason: str | None = None
    privacy_issues: list[str] = field(default_factory=list)
    safe_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if not self.privacy_safe:
            return "privacy_unsafe"
        if self.semantic_review_ready:
            return "privacy_clear_for_semantic_review"
        return "privacy_safe_but_semantically_insufficient"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status
        return payload


def build_privacy_safe_representation(
    evidence_result: CandidateEvidenceResult,
    *,
    proposition: str | None = None,
    epistemic_shape: str | None = None,
    transformation_method: str = "manual.regeneration",
    transformation_version: str = DEFAULT_SCHEMA_VERSION,
    safe_metadata: dict[str, Any] | None = None,
) -> PrivacySafeRepresentation:
    if evidence_result.status != "resolved":
        raise ValueError(f"cannot build privacy-safe representation from {evidence_result.status}")
    candidate_id = _require_text(evidence_result.candidate_id, "candidate_id")
    source_namespace = _require_text(evidence_result.source_namespace, "source_namespace")
    proposition = proposition.strip() if isinstance(proposition, str) and proposition.strip() else None
    epistemic_shape = epistemic_shape.strip() if isinstance(epistemic_shape, str) and epistemic_shape.strip() else None
    if epistemic_shape is not None and epistemic_shape not in SUPPORTED_EPISTEMIC_SHAPES:
        raise ValueError(f"unsupported epistemic shape: {epistemic_shape}")
    evidence_reference = _evidence_reference_from_result(evidence_result)
    review_ready = proposition is not None and epistemic_shape is not None and bool(evidence_reference)
    insufficiency_reason = None
    if not review_ready:
        if proposition is None and epistemic_shape is None:
            insufficiency_reason = "metadata-only representation"
        elif proposition is None:
            insufficiency_reason = "missing proposition"
        elif epistemic_shape is None:
            insufficiency_reason = "missing epistemic shape"
        else:
            insufficiency_reason = "insufficient representation"
    return PrivacySafeRepresentation(
        schema_version=transformation_version,
        candidate_id=candidate_id,
        source_namespace=source_namespace,
        privacy_safe=True,
        semantic_review_ready=review_ready,
        proposition=proposition,
        epistemic_shape=epistemic_shape,
        evidence_reference=evidence_reference,
        transformation_method=transformation_method,
        transformation_version=transformation_version,
        insufficiency_reason=insufficiency_reason,
        privacy_issues=[],
        safe_metadata=dict(safe_metadata or evidence_result.safe_metadata or {}),
    )


def coerce_privacy_safe_representation(payload: PrivacySafeRepresentation | Mapping[str, Any]) -> PrivacySafeRepresentation:
    if isinstance(payload, PrivacySafeRepresentation):
        return payload
    if not isinstance(payload, Mapping):
        raise TypeError("privacy-safe representation must be a mapping or PrivacySafeRepresentation")
    candidate_id = _require_text(payload.get("candidate_id"), "candidate_id")
    source_namespace = _require_text(payload.get("source_namespace"), "source_namespace")
    schema_version = _require_text(payload.get("schema_version") or DEFAULT_SCHEMA_VERSION, "schema_version")
    proposition = payload.get("proposition")
    if isinstance(proposition, str):
        proposition = proposition.strip() or None
    else:
        proposition = None
    epistemic_shape = payload.get("epistemic_shape")
    if isinstance(epistemic_shape, str):
        epistemic_shape = epistemic_shape.strip() or None
    else:
        epistemic_shape = None
    evidence_reference = payload.get("evidence_reference")
    if not isinstance(evidence_reference, Mapping):
        evidence_reference = None
    else:
        evidence_reference = dict(evidence_reference)
    transformation_method = str(payload.get("transformation_method") or "manual.regeneration")
    transformation_version = str(payload.get("transformation_version") or schema_version)
    privacy_safe = bool(payload.get("privacy_safe"))
    semantic_review_ready = bool(payload.get("semantic_review_ready"))
    insufficiency_reason = payload.get("insufficiency_reason")
    if insufficiency_reason is not None:
        insufficiency_reason = str(insufficiency_reason)
    safe_metadata = payload.get("safe_metadata")
    if not isinstance(safe_metadata, Mapping):
        safe_metadata = {}
    else:
        safe_metadata = dict(safe_metadata)
    privacy_issues = list(payload.get("privacy_issues") or [])
    privacy_issues.extend(_collect_privacy_issues(payload))
    if any(issue.endswith(".privacy_safe") or issue == "privacy_safe" for issue in privacy_issues):
        privacy_safe = False
    if any(issue.endswith(".semantic_review_ready") or issue == "semantic_review_ready" for issue in privacy_issues):
        semantic_review_ready = False
    return PrivacySafeRepresentation(
        schema_version=schema_version,
        candidate_id=candidate_id,
        source_namespace=source_namespace,
        privacy_safe=privacy_safe,
        semantic_review_ready=semantic_review_ready,
        proposition=proposition,
        epistemic_shape=epistemic_shape,
        evidence_reference=evidence_reference,
        transformation_method=transformation_method,
        transformation_version=transformation_version,
        insufficiency_reason=insufficiency_reason,
        privacy_issues=sorted({issue for issue in privacy_issues if issue}),
        safe_metadata=safe_metadata,
    )


def validate_privacy_safe_representation(
    payload: PrivacySafeRepresentation | Mapping[str, Any],
    *,
    expected_candidate_id: str | None = None,
    expected_source_namespace: str | None = None,
) -> PrivacySafeRepresentation:
    representation = coerce_privacy_safe_representation(payload)
    if expected_candidate_id is not None and representation.candidate_id != expected_candidate_id:
        raise ValueError(
            f"representation candidate mismatch: expected {expected_candidate_id}, got {representation.candidate_id}"
        )
    if expected_source_namespace is not None and representation.source_namespace != expected_source_namespace:
        raise ValueError(
            f"representation namespace mismatch: expected {expected_source_namespace}, got {representation.source_namespace}"
        )
    if not representation.evidence_reference:
        raise ValueError("privacy-safe representation missing provenance reference")
    return representation


def require_semantic_review_ready(
    payload: PrivacySafeRepresentation | Mapping[str, Any],
    *,
    expected_candidate_id: str | None = None,
    expected_source_namespace: str | None = None,
) -> PrivacySafeRepresentation:
    representation = validate_privacy_safe_representation(
        payload,
        expected_candidate_id=expected_candidate_id,
        expected_source_namespace=expected_source_namespace,
    )
    if not representation.privacy_safe:
        raise ValueError("privacy-safe representation required")
    if not representation.semantic_review_ready:
        raise ValueError(representation.insufficiency_reason or "representation is not semantic-review-ready")
    if representation.epistemic_shape not in SUPPORTED_EPISTEMIC_SHAPES:
        raise ValueError("representation does not preserve a supported epistemic shape")
    if representation.privacy_issues:
        raise ValueError(f"privacy issues present: {', '.join(representation.privacy_issues)}")
    return representation


def candidate_requires_privacy_representation(privacy_state: str | None) -> bool:
    return str(privacy_state or "").strip() in PRIVACY_GATE_STATES


def load_privacy_safe_representation(path: Path) -> PrivacySafeRepresentation:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    return coerce_privacy_safe_representation(payload)


def render_privacy_safe_representation_json(payload: PrivacySafeRepresentation | Mapping[str, Any]) -> str:
    representation = coerce_privacy_safe_representation(payload)
    import json

    return json.dumps(representation.to_dict(), indent=2, sort_keys=True) + "\n"


def render_privacy_safe_representation_markdown(payload: PrivacySafeRepresentation | Mapping[str, Any]) -> str:
    representation = coerce_privacy_safe_representation(payload)
    lines = [
        "# Privacy-Safe Representation",
        "",
        f"- candidate_id: `{representation.candidate_id}`",
        f"- source_namespace: `{representation.source_namespace}`",
        f"- privacy_safe: `{str(representation.privacy_safe).lower()}`",
        f"- semantic_review_ready: `{str(representation.semantic_review_ready).lower()}`",
        f"- status: `{representation.status}`",
    ]
    if representation.epistemic_shape is not None:
        lines.append(f"- epistemic_shape: `{representation.epistemic_shape}`")
    if representation.insufficiency_reason is not None:
        lines.append(f"- insufficiency_reason: `{representation.insufficiency_reason}`")
    if representation.evidence_reference:
        lines.append("- evidence_reference:")
        for key in sorted(representation.evidence_reference):
            lines.append(f"  - {key}: `{representation.evidence_reference[key]}`")
    if representation.proposition is not None:
        lines.append("")
        lines.append("## Proposition")
        lines.append("")
        lines.append(representation.proposition)
    return "\n".join(lines) + "\n"
