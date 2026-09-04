---
id: CLA-unordered-serialization-flake-cause
kind: claim
title: Unordered serialization, not timezone handling, caused the ordering flake
assertion_class: retrospective_interpretation
status: active
source_ids: [SRC-EXAMPLE-GIT-LOG, SRC-EXAMPLE-DECISIONS]
evidence: [{source_id: SRC-EXAMPLE-GIT-LOG, locator: sources/git-log-excerpt.txt#L9-L13, sha256: 4f5610b038e672298456cc8bbd12966b61a017f5c1e0de3eea497d50e448d1ca, hash_status: verified}, {source_id: SRC-EXAMPLE-DECISIONS, locator: sources/decision-log.md#L5-L9, sha256: e5d6940c0880691730374074c76e5097c4f03a19fbdb99705be41cd06040b516, hash_status: verified}]
relationships: [{type: supersedes, target: HYP-timezone-handling-flake-cause}, {type: supports, target: FAI-ci-4821-search-endpoint-flake}]
ingestion: {method: manual, source_fingerprint: example-corpus/cla-unordered-serialization-flake-cause}
---

Historical account

In retrospect, the intermittent ordering failure was caused by serialization of an unordered mapping when building search responses: two identical queries could serialize the same mapping in different orders. The fix commit sorts search results deterministically, and the frozen serialization format decision removed the class of change that allowed the unstable ordering. This interpretation infers the cause from the fix and the format decision, and it supersedes the earlier timezone hypothesis. It is synthetic example data.
