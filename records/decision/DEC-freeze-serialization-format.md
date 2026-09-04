---
id: DEC-freeze-serialization-format
kind: decision
title: Freeze the serialization format for search results
assertion_class: observed_fact
status: active
source_ids: [SRC-EXAMPLE-DECISIONS]
evidence: [{source_id: SRC-EXAMPLE-DECISIONS, locator: sources/decision-log.md#L5-L9, sha256: e5d6940c0880691730374074c76e5097c4f03a19fbdb99705be41cd06040b516, hash_status: verified}]
relationships: []
ingestion: {method: manual, source_fingerprint: example-corpus/dec-freeze-serialization-format}
---

Historical account

On 2026-03-06 the fictional Harbor Notes team accepted a decision to freeze the search endpoint's serialization format: responses use an explicit, documented field order, and no new fields may be added without a format revision. The recorded rationale is that a stable wire format makes result-ordering regressions mechanically detectable. The decision log entry is the evidence for this record.
