# Example corpus (synthetic)

This directory holds a small, entirely synthetic example corpus for Project
Historian. It exists so that validation, search, provenance checking, and the
retrieval corpus builder all work out of the box without any private project
history.

Everything here is fictional example data. The project ("Harbor Notes"), all
record IDs, source IDs, commit ids, checksums, people, dates, and logs are
synthetic. Nothing in this corpus describes a real project or real events, and
it must not be read as history of any actual system.

## Layout

- `records/<kind>/<ID>.md` — example records following `schema/SPEC.md`,
  including events, a failure, decisions (accepted and candidate), a
  hypothesis (superseded), a retrospective claim, a milestone, an artifact, a
  preserved evidence log, and a revision.
- `sources/manifest.json` — the example source manifest. Its SHA-256 values
  are real and match the retained example source files, so evidence hashes can
  be verified mechanically.
- `sources/` — the retained example source files cited by the records.
  Evidence locators are relative to this corpus root.

## Using it

In a distribution of Project Historian, this corpus is placed at the
distribution root, so the standard commands work directly:

```sh
python3 -m historian.cli validate
python3 -m historian.cli search milestone
python3 historian/interfaces/build_khoj_corpus.py
```

`validate` checks every record's structure, IDs, source references, evidence
hashes, and relationship targets; `search` does deterministic substring search
over the corpus; the corpus builder projects the records into a local
retrieval corpus under `interfaces/khoj/corpus/`.

This example corpus is not a replacement for a real evidence corpus. It
demonstrates the format and the tooling on data that is safe to share.
