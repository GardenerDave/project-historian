# Historian V1 record specification

Every canonical record is Markdown with YAML-like front matter. Required fields:

```yaml
id: EVT-<16 lowercase hex characters>
kind: event | evidence | claim | experiment | decision | hypothesis | failure | milestone | artifact | revision
title: short human-readable title
assertion_class: observed_fact | contemporary_interpretation | retrospective_interpretation
status: active | superseded | disputed | candidate
source_ids: [SRC-...]
evidence: [{source_id: SRC-..., locator: path#anchor-or-json-key, sha256: <64 hex> | null, hash_status: verified | not-yet-recorded | git-object-id | per-file-manifest}]
relationships: [{type: supports | contradicts | supersedes | revises | caused_by | derived_from | duplicates, target: <record-id>}]
ingestion: {method: manual | git | deterministic-file, source_fingerprint: ...}
```

Stable IDs are explicit durable slugs matching `^[A-Z][A-Z0-9]{2,7}-[a-z0-9][a-z0-9-]*$`. Human-readable IDs are canonical for hand-authored records; deterministic Git ingestion uses `EVE-` plus the first 16 hex characters of SHA-256(kind + ':' + stable key). A stable key is never a title alone when a source locator is available. Re-running ingestion must produce the same ID and update nothing destructively. ID changes require an explicit migration and reference update.

An observed fact is directly evidenced. A contemporary interpretation is an interpretation recorded by the project at the time. A retrospective interpretation is added later and must say what it infers and why. Contradictions are records/relationships, never silently resolved. Revisions create a new record and `supersedes` the prior one.

Evidence locators must be inspectable and typed by convention: repository-relative path plus `#json.path`, `#Lstart-Lend`, or `#heading`; absolute archival paths are permitted for archived sources; a Git commit is a 40-hex object ID. Source files remain outside this repository when they are already canonical elsewhere; their SHA-256 and tracking state are recorded. `sha256: null` is allowed only with an explicit `hash_status`, never with prose such as “see source file”.

The model is intentionally file-based rather than a graph database. Relationships are explicit links, and search is deterministic substring search over canonical Markdown.

Relationship direction is always written as `source-record --relationship--> target-record` (or a source ID when the schema permits it). Read the relationship from the record that declares it toward its `target`; do not reverse it while projecting or rendering. Use `derived_from` when the declaring record is based on the target record, `supports` when the declaring source supports the target statement, and `caused_by` when the declaring record names its cause.
