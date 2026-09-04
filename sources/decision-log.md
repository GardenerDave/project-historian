# Harbor Notes — example decision log (synthetic)

This log is fictional example data for the Project Historian example corpus.
Dates, participants, and decisions are synthetic.

## 2026-03-06 — Freeze the serialization format for search results

L5  status: accepted
L6  participants: Robin Example, Alex Example
L7  decision: search endpoint responses use a frozen, explicit field order;
L8      no new fields may be added without a format revision
L9  rationale: a stable wire format makes result-ordering regressions detectable

## 2026-03-12 — Adopt a weekly release train

L12 status: proposed
L13 participants: Alex Example
L14 proposal: cut a tagged release every Friday while the search endpoint
L15     stability streak is at least 25 runs
