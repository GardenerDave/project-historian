---
id: MIL-search-endpoint-stable
kind: milestone
title: Search endpoint stable across 30 consecutive CI runs
assertion_class: observed_fact
status: active
source_ids: [SRC-EXAMPLE-CI]
evidence: [{source_id: SRC-EXAMPLE-CI, locator: sources/ci-run-4821.log#L24-L27, sha256: 2d84749863df8ba619626be9ce04ae7b8acdfb65313e948a437f59a4ba980d4e, hash_status: verified}]
relationships: [{type: derived_from, target: EVT-fix-serialization-ordering}]
ingestion: {method: manual, source_fingerprint: example-corpus/mil-search-endpoint-stable}
---

Historical account

After the ordering fix, the CI stability re-check recorded 30 consecutive passing runs for the search endpoint with zero failures. The milestone is derived from the fix event and evidenced by the run summary section of the example CI log.
