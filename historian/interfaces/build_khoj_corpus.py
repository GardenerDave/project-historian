from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "interfaces" / "khoj" / "corpus"
ID_RE = re.compile(r"^[A-Z][A-Z0-9]{2,7}-[a-z0-9][a-z0-9-]*$")
CORPUS_MARKER = "# Historian Khoj retrieval corpus"


def _require_fresh_output(out: Path) -> None:
    if out.is_symlink() or out.exists():
        raise ValueError(
            f"{out}: refusing to build: destination already exists; "
            "this builder never replaces or deletes an existing path — choose a fresh destination"
        )


def _field(text: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", text, re.M)
    return match.group(1).strip() if match else ""


def _items(value: str) -> list[str]:
    return re.findall(r"[A-Z][A-Z0-9]{2,7}-[a-z0-9][a-z0-9-]*|SRC-[A-Z0-9-]+", value)


def _objects(value: str) -> list[str]:
    return re.findall(r"\{([^{}]*)\}", value)


def _object_field(obj: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\s*(.*?)(?:,\s*\w+:|$)", obj)
    return (match.group(1).strip() if match else "").strip(" '\"")


def _record(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end < 0:
        raise ValueError(f"invalid canonical record: {path}")
    return {k: _field(text[4:end], k) for k in ("id", "kind", "title", "assertion_class", "status", "source_ids", "evidence", "relationships")}, text[end + 5:].strip()


def canonical_record_ids(source: Path = ROOT / "records") -> list[str]:
    return [data["id"] for data, _ in sorted((_record(path) for path in source.rglob("*.md")), key=lambda item: item[0]["id"])]


def projected_record_ids(output: Path) -> list[str]:
    records = output / "records"
    if not records.exists():
        return []
    return sorted(path.stem for path in records.glob("*.md"))


def validate_projection(source: Path = ROOT / "records", output: Path = DEFAULT_OUT) -> int:
    expected = canonical_record_ids(source)
    projected = projected_record_ids(output)
    if projected != expected:
        expected_set = set(expected)
        projected_set = set(projected)
        missing = sorted(expected_set - projected_set)
        extra = sorted(projected_set - expected_set)
        raise AssertionError(
            "retrieval projection is incomplete or stale: "
            f"missing={missing}, extra={extra}, expected={len(expected)}, projected={len(projected)}"
        )
    return len(expected)


def _label(value: str) -> str:
    return value.replace("_", " ").upper()


def _evidence_lines(value: str) -> list[str]:
    lines = []
    for obj in _objects(value):
        source = _object_field(obj, "source_id")
        locator = _object_field(obj, "locator")
        digest = _object_field(obj, "sha256")
        status = _object_field(obj, "hash_status")
        if status == "verified" and digest != "null":
            proof = f"Verified SHA-256: {digest}"
        elif status == "git-object-id":
            proof = f"Git object: {locator}"
        elif status == "per-file-manifest":
            proof = "Verified via per-file manifest"
        elif status == "not-yet-recorded":
            proof = "Digest not yet recorded"
        else:
            proof = f"Hash status: {status or 'not recorded'}"
        lines.append(f"- Source `{source}`; evidence locator `{locator}`; {proof}.")
    return lines or ["- No evidence entries recorded."]


def _relationship_lines(value: str) -> list[str]:
    lines = []
    for obj in _objects(value):
        lines.append(f"- This record --{_object_field(obj, 'type')}--> {_object_field(obj, 'target')}.")
    return lines or ["- No relationships recorded."]


def _record_page(data: dict[str, str], body: str) -> str:
    rid = data["id"]
    if not ID_RE.match(rid):
        raise ValueError(f"invalid record ID: {rid}")
    lines = [f"# {data['title']}", "", f"Stable ID: `{rid}`", f"Kind: `{data['kind']}`", f"Assertion class: **{_label(data['assertion_class'])}**", f"Status: **{_label(data['status'])}**", "", "Historical account", "", body or "(no body recorded)", "", "Source IDs"]
    lines += [f"- `{sid}`" for sid in _items(data["source_ids"])] or ["- None recorded."]
    lines += ["", "Evidence locators"] + _evidence_lines(data["evidence"])
    lines += ["", "Relationships (direction: this record --relationship--> target)"] + _relationship_lines(data["relationships"])
    return "\n".join(lines) + "\n"


def build(out: Path = DEFAULT_OUT) -> int:
    records = [_record(path) for path in sorted((ROOT / "records").rglob("*.md"))]
    _require_fresh_output(out)
    (out / "records").mkdir(parents=True)
    (out / "sources").mkdir()
    for data, body in records:
        (out / "records" / f"{data['id']}.md").write_text(_record_page(data, body), encoding="utf-8")

    manifest = json.loads((ROOT / "sources" / "manifest.json").read_text(encoding="utf-8"))
    for source in sorted(manifest["sources"], key=lambda item: item["source_id"]):
        sid = source["source_id"]
        restricted = "codex" in source.get("kind", "").lower()
        if restricted:
            continue
        safe = {k: v for k, v in source.items() if k not in {"original_path", "archived_path"} or not restricted}
        lines = [f"# Source {sid}", "", "Safe provenance metadata only; source content is not copied into this corpus.", ""]
        lines += [f"- {key}: `{value}`" for key, value in sorted(safe.items())]
        (out / "sources" / f"{sid}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (out / "CORPUS.md").write_text(CORPUS_MARKER + """

This is disposable, deterministic retrieval material generated from Project Historian. Canonical authority remains `records/`, the schema, manifests, and explicitly referenced evidence. Khoj indexes this corpus only; it has no write-back or approval authority.

Included: canonical Historian records and safe provenance metadata for non-Codex source IDs. Excluded: restricted raw history, sanitized Codex rollout content, all arbitrary Codex files, the source project repository, SilverBullet pages, credentials, and external documents.

Rebuild with `python3 historian/interfaces/build_khoj_corpus.py --output <fresh-directory>`. This builder never replaces or deletes an existing destination: building into an existing path fails closed, so choose a fresh destination for each rebuild. Canonical records cannot be modified.
""", encoding="utf-8")
    validate_projection(ROOT / "records", out)
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(f"projected canonical records: {build(args.output)}")


if __name__ == "__main__":
    main()
