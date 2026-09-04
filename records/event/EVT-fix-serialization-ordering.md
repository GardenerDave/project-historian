---
id: EVT-fix-serialization-ordering
kind: event
title: Search results sorted deterministically
assertion_class: observed_fact
status: active
source_ids: [SRC-EXAMPLE-GIT-LOG]
evidence: [{source_id: SRC-EXAMPLE-GIT-LOG, locator: sources/git-log-excerpt.txt#L9-L10, sha256: 4f5610b038e672298456cc8bbd12966b61a017f5c1e0de3eea497d50e448d1ca, hash_status: verified}]
relationships: [{type: caused_by, target: FAI-ci-4821-search-endpoint-flake}]
ingestion: {method: manual, source_fingerprint: example-corpus/evt-fix-serialization-ordering}
---

Historical account

On 2026-03-06 a change landed that sorts search results deterministically in the search endpoint, followed a day later by a regression test for result ordering. The record declares its cause explicitly: the CI run 4821 failure it was written to eliminate.
