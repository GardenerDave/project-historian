---
id: REV-release-notes-120-checksum-addendum
kind: revision
title: Release notes addendum corrected the published tarball checksum
assertion_class: observed_fact
status: active
source_ids: [SRC-EXAMPLE-NOTES]
evidence: [{source_id: SRC-EXAMPLE-NOTES, locator: sources/release-notes-120.md#L10-L12, sha256: c59587385700ad5f8700b4e88ad0337ccb51d747d6ad28e22b3145c672309cbe, hash_status: verified}]
relationships: [{type: revises, target: ART-harbor-notes-120-tarball}]
ingestion: {method: manual, source_fingerprint: example-corpus/rev-release-notes-120-checksum-addendum}
---

Historical account

On 2026-03-13 an addendum corrected a transcription typo in the tarball checksum that was first published with the 1.2.0 release notes. The revision record revises the artifact record that pointed at the originally published checksum line; both the original statement and the correction remain in the corpus, and the correction is the one that should be used.
