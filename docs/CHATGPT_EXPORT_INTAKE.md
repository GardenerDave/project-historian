# ChatGPT Export Intake V1

This document defines the first privacy boundary for local ChatGPT export intake.

## Trust zones

- `RAW PRIVATE ARCHIVE`: the original export, immutable, local only, never committed, never canonical.
- `PRIVATE INTAKE / INVENTORY`: parsed, quarantined, and sanitized working material in ignored storage.
- `PRIVACY + SECRET SANITIZATION`: deterministic redaction and blocking checks before any derived material crosses the boundary.
- `PRIVATE PROJECT-RELEVANCE STAGING`: local-only derived metadata used for later manual selection.
- `PRIVACY-CLEARED HISTORIAN STAGING`: only opaque, derivation-aware metadata that passed the privacy gate.

## Data flow

1. Parse the export in read-only mode.
2. Validate the privacy boundary before any archive processing.
3. Inventory conversations with opaque IDs.
4. Record private provenance in ignored storage.
5. Write detailed inventory metadata only to ignored staging.

## Input sources

- A single ChatGPT conversation JSON file.
- An ordered shard set named `conversations-000.json`, `conversations-001.json`, and so on.
- A ZIP archive may be used as a narrow source-preparation step, but only the allowed shard members are extracted.

## Shard semantics

- Shard ordering is numeric, not lexical.
- Shard provenance records the shard name and private shard hash.
- Sharded inventory processes one shard at a time and does not merge the whole corpus into a single in-memory conversation list.
- ZIP preparation uses a source-specific private extraction directory under `.work/chatgpt-intake/raw-extract/` and records a private shard manifest.
- Missing or malformed shard structure fails closed.
- Duplicate shard numbers fail closed.
- Duplicate raw conversation IDs across shards are quarantined, not silently merged.

## Sanitizer role

Historian does not reuse an external text sanitizer, because the audited external feature was a gated token-diagnostic pipeline, not a general-purpose redaction library.

Historian instead uses a small local privacy gate for V1, with that audit informing the no-inference, fail-closed posture.

## Threat model

- Raw export content must not leak into Git, canonical records, prompts, or services.
- Raw IDs, titles, and source paths should not appear in tracked artifacts by default.
- Secrets should normally stop the boundary rather than being merely redacted.
- PII is tracked as privacy-review metadata; this V1 does not emit a content redaction derivative.
- Ambiguous personal material is retained only for later supervised review.

## Guarantees

- Private paths are ignored by Git.
- Inventory is deterministic for a fixed export and secret.
- Detailed inventory artifacts are written only under ignored local storage.
- The CLI prints aggregate summaries only.
- Provenance is split into private and Historian-visible layers.
- The inventory command enforces the boundary internally before reading archive content.

## Non-guarantees

- This V1 does not prove complete PII coverage.
- This V1 does not classify project relevance semantically.
- This V1 does not ingest canonical Historian records.
- This V1 does not make the export safe for sharing.

## Private provenance

Private provenance stays in ignored storage and may include:

- opaque private ID;
- raw conversation/message IDs;
- raw archive path;
- raw source hashes;
- timestamps;
- detector categories.

Historian-visible provenance keeps only:

- opaque source ID;
- intake version;
- sanitizer identity;
- transformation steps;
- detector categories;
- coarse relevance signals.
- The raw archive SHA-256 stays private and is not printed by the CLI.

## Git boundary

The intake workspace is rooted under `.work/chatgpt-intake/`, which is ignored.
The preflight command checks that the private paths are ignored and that the export path is not accidentally tracked as ordinary untracked content.
If protection cannot be established, the boundary is treated as failed.

## ZIP extraction scope

- Only `conversations-<n>.json` members are eligible for shard extraction.
- Unsafe ZIP members with absolute paths or `..` traversal are rejected.
- ZIP members are validated before any shard bytes are written.
- Asset files such as `file-*.dat` are not extracted for this inventory stage.
- `chat.html`, `user.json`, `shared_conversations.json`, and other non-conversation members are ignored.

## Quarantine semantics

- `safe_metadata_only`: no blocking findings.
- `privacy_review_required`: non-blocking PII or ambiguous privacy material was observed.
- `quarantined`: high-severity secret material should not cross the boundary without review.
- Structural shard failures abort inventory.

## Operator workflow

1. Provide a local export path.
2. Run `historian chatgpt-intake preflight <export-path>`.
3. Run `historian chatgpt-intake inventory <export-path>`.
4. Review only the ignored private outputs.
5. Do not promote anything to canonical Historian until a later supervised tranche.
6. Treat terminal output as sensitive; the CLI intentionally prints aggregate-only summaries to reduce screen-recording leakage.
