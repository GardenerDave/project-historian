---
id: HYP-timezone-handling-flake-cause
kind: hypothesis
title: Hypothesis that local timezone handling caused the ordering flake
assertion_class: contemporary_interpretation
status: superseded
source_ids: [SRC-EXAMPLE-CI]
evidence: [{source_id: SRC-EXAMPLE-CI, locator: sources/ci-run-4821.log#L14-L16, sha256: 2d84749863df8ba619626be9ce04ae7b8acdfb65313e948a437f59a4ba980d4e, hash_status: verified}]
relationships: [{type: derived_from, target: FAI-ci-4821-search-endpoint-flake}]
ingestion: {method: manual, source_fingerprint: example-corpus/hyp-timezone-handling-flake-cause}
---

Historical account

At the time of the failure, the working hypothesis was that local timezone handling in the search endpoint produced different default orderings between requests. The hypothesis was derived from the observed failure record and was later superseded by a retrospective claim that identifies a different cause. This record preserves the contemporary interpretation exactly as it was held, which is why it remains in the corpus with superseded status instead of being deleted.
