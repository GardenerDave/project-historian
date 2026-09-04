---
id: FAI-ci-4821-search-endpoint-flake
kind: failure
title: CI run 4821 failed on search endpoint result ordering
assertion_class: observed_fact
status: active
source_ids: [SRC-EXAMPLE-CI]
evidence: [{source_id: SRC-EXAMPLE-CI, locator: sources/ci-run-4821.log#L12-L18, sha256: 2d84749863df8ba619626be9ce04ae7b8acdfb65313e948a437f59a4ba980d4e, hash_status: verified}]
relationships: []
ingestion: {method: manual, source_fingerprint: example-corpus/fai-ci-4821-search-endpoint-flake}
---

Historical account

CI run 4821 failed intermittently on 2026-03-05: the search endpoint returned results in a different order for two identical queries, reproducing on only one of three attempts. The run log records the failing test, the observed ordering difference, and the pass/fail summary. This is a synthetic example failure with no counterpart in any real project.
