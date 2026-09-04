"""Deterministic, schema-aware Historian evidence selection."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from historian.retrieval import load_documents

ASSERTION_TERMS = {
    "observed fact": "observed_fact",
    "observed facts": "observed_fact",
    "contemporary interpretation": "contemporary_interpretation",
    "contemporary interpretations": "contemporary_interpretation",
    "retrospective interpretation": "retrospective_interpretation",
    "retrospective interpretations": "retrospective_interpretation",
}
STATUS_TERMS = {x: x for x in ("active", "candidate", "disputed", "superseded")}
QUERY_CHAIN_STRONG_TERMS = (
    "sequence",
    "progression",
    "chain",
    "trace",
    "chronology",
    "timeline",
)
QUERY_CHAIN_RELATIONAL_TERMS = ("connect", "relate", "link")
QUERY_CHAIN_PAIR_PATTERNS = (
    r"\bfrom\b(?:\W+\w+){1,8}\W+\bto\b",
    r"\bbetween\b(?:\W+\w+){1,8}\W+\b(?:and|to)\b",
    r"\bbefore\b(?:\W+\w+){1,8}\W+\bafter\b",
    r"\bthrough\b(?:\W+\w+){1,8}\W+\bto\b",
)


def _stem(token: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "", token.lower())
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokenize(text: str) -> set[str]:
    return {_stem(token) for token in re.findall(r"[A-Za-z0-9]+", text or "") if _stem(token)}


def _contains_token(text: str, token: str) -> bool:
    return bool(re.search(r"\b" + re.escape(token) + r"\b", text))


def _query_intent(query: str) -> dict[str, object]:
    text = query.lower()
    chain = any(_contains_token(text, term) for term in QUERY_CHAIN_STRONG_TERMS) or any(
        re.search(pattern, text) for pattern in QUERY_CHAIN_PAIR_PATTERNS
    )
    if not chain and any(_contains_token(text, term) for term in QUERY_CHAIN_RELATIONAL_TERMS):
        chain = any(_contains_token(text, term) for term in ("to", "through", "between", "with"))
    return {
        "chain": chain,
        "assertion_terms": {
            value for key, value in ASSERTION_TERMS.items() if re.search(r"\b" + re.escape(key) + r"\b", text)
        },
        "status_terms": {
            value for key, value in STATUS_TERMS.items() if re.search(r"\b" + re.escape(key) + r"\b", text)
        },
        "query_tokens": _tokenize(text),
    }


def constraint(q: str) -> dict[str, set[str]]:
    intent = _query_intent(q)
    out: dict[str, set[str]] = {}
    if intent["assertion_terms"]:
        out["assertion_class"] = set(intent["assertion_terms"])  # type: ignore[arg-type]
    if intent["status_terms"]:
        out["status"] = set(intent["status_terms"])  # type: ignore[arg-type]
    return out


def _front(path: Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text:
        raise ValueError(f"malformed record frontmatter: {path}")
    head = text[4 : text.index("\n---\n", 4)]
    return text, dict(re.findall(r"^(id|kind|title|assertion_class|status):\s*(.+)$", head, re.M))


def _extract_timestamp(text: str) -> datetime | None:
    match = re.search(r"at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-\d{2}:\d{2})", text)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1))
    except ValueError:
        return None


def _record_text(doc) -> str:
    return f"{getattr(doc, 'title', '')}\n{getattr(doc, 'text', '')}"


def _query_overlap_score(query_tokens: set[str], doc) -> int:
    title_tokens = _tokenize(getattr(doc, "title", ""))
    text_tokens = _tokenize(getattr(doc, "text", ""))
    return len(query_tokens & title_tokens) * 2 + len(query_tokens & text_tokens)


def _intent_score(intent: dict[str, object], doc, timestamps: dict[str, datetime | None]) -> int:
    if not intent["chain"]:
        return 0
    score = 0
    record_text = _record_text(doc).lower()
    query_tokens = intent["query_tokens"]  # type: ignore[assignment]
    if query_tokens:
        score += len(query_tokens & _tokenize(record_text)) * 2
    if getattr(doc, "source_file", "").startswith("event/") or getattr(doc, "kind", "") == "event":
        score += 2
    if timestamps.get(getattr(doc, "record_id", "")) is not None:
        score += 1
    return score


def structured_index(canonical_root: Path, corpus: Path = Path("interfaces/khoj/corpus")):
    docs = load_documents(corpus)
    ids = {d.record_id for d in docs}
    nodes = {d.record_id: {"out": [], "in": []} for d in docs}
    edges = []
    malformed = []
    duplicates = set()
    seen = set()

    for p in sorted(canonical_root.rglob("*.md")):
        text, meta = _front(p)
        rid = meta.get("id")
        if not rid:
            continue
        line = re.search(r"^relationships:\s*(.*)$", text[: text.index("\n---\n", 4)], re.M)
        if not line:
            raise ValueError(f"missing relationships field: {p}")
        value = line.group(1).strip()
        if not (value.startswith("[") and value.endswith("]")):
            raise ValueError(f"malformed relationships: {p}")
        objs = re.findall(r"\{([^{}]*)\}", value)
        if value not in ("[]", "") and not objs:
            raise ValueError(f"malformed relationships: {p}")
        if value.count("{") != value.count("}") or value.count("[") != 1 or value.count("]") != 1:
            raise ValueError(f"malformed relationships: {p}")
        for obj in objs:
            tm = re.fullmatch(r"\s*type:\s*([^,]+),\s*target:\s*([^,]+)\s*", obj)
            if not tm or not tm.group(1).strip() or not tm.group(2).strip():
                raise ValueError(f"malformed relationship declaration: {p}")
            typ, target = tm.group(1).strip(), tm.group(2).strip()
            edge = (rid, typ, target)
            if edge in seen:
                duplicates.add(edge)
                continue
            seen.add(edge)
            edges.append(
                {
                    "source_id": rid,
                    "relationship_type": typ,
                    "target_id": target,
                    "target_in_corpus": target in ids,
                }
            )
            if rid in nodes:
                nodes[rid]["out"].append((typ, target))
            if target in nodes:
                nodes[target]["in"].append((typ, rid))
            elif target in ids:
                # defensive, though `ids` and `nodes` should stay aligned
                nodes[target] = {"out": [], "in": []}
    return nodes, edges, {
        "canonical_records": len({e["source_id"] for e in edges} | ids),
        "declared_relationships": len(edges),
        "in_corpus_edges": sum(e["target_in_corpus"] for e in edges),
        "external_targets": sum(not e["target_in_corpus"] for e in edges),
        "malformed": len(malformed),
        "duplicates": len(duplicates),
    }


def expand_derived_from(records, nodes, corpus_ids=None, max_added_records=None):
    """Deterministically append one-hop outgoing derived_from targets."""
    corpus_ids = set(corpus_ids or nodes.keys())
    seed_ids = [r["record_id"] for r in records]
    selected = set(seed_ids)
    seen_targets = set()
    expanded = []
    budget = max_added_records if max_added_records is not None else len(seed_ids)
    for seed_rank, seed_id in enumerate(seed_ids, start=1):
        for relationship_type, target_id in nodes.get(seed_id, {"out": [], "in": []})["out"]:
            if relationship_type != "derived_from":
                continue
            if target_id not in corpus_ids or target_id in selected or target_id in seen_targets:
                continue
            expanded.append(
                {
                    "record_id": target_id,
                    "expanded_from": seed_id,
                    "relationship_type": relationship_type,
                    "hop": 1,
                    "seed_rank": seed_rank,
                    "expansion_order": len(expanded) + 1,
                }
            )
            seen_targets.add(target_id)
            selected.add(target_id)
            if len(expanded) >= budget:
                return expanded
    return expanded


def _feature_row(doc, semantic, nodes, query, intent, seed_ids, anchor_ids, timestamps):
    rid = doc.record_id
    neighbours = nodes.get(rid, {"out": [], "in": []})
    one_hop = {target for _, target in neighbours["out"] + neighbours["in"]}
    direct = one_hop & seed_ids
    two_hop = set()
    for n in one_hop:
        nbrs = nodes.get(n, {"out": [], "in": []})
        two_hop.update(target for _, target in nbrs["out"] + nbrs["in"])
    query_overlap = _query_overlap_score(intent["query_tokens"], doc)
    intent_bonus = _intent_score(intent, doc, timestamps)
    return {
        "record_id": rid,
        "direct_seed_link_count": len(direct),
        "two_hop_seed_reach_count": len(two_hop & seed_ids),
        "anchor_seed_link_count": len(one_hop & anchor_ids),
        "anchor_two_hop_reach_count": len(two_hop & anchor_ids),
        "min_seed_hops": 0 if rid in seed_ids else (1 if direct else (2 if two_hop & seed_ids else 999)),
        "semantic_rank": next(x["rank"] for x in semantic if x["record_id"] == rid),
        "cosine_score": next(x["cosine_score"] for x in semantic if x["record_id"] == rid),
        "assertion_class": doc.assertion_class,
        "status": doc.status,
        "kind": getattr(doc, "kind", getattr(doc, "source_file", "").split("/", 1)[0]),
        "schema_eligible": (
            not intent["assertion_terms"]
            and not intent["status_terms"]
            or (
                (not intent["assertion_terms"] or doc.assertion_class.lower().replace(" ", "_") in intent["assertion_terms"])
                and (not intent["status_terms"] or doc.status.lower() in intent["status_terms"])
            )
        ),
        "query_overlap": query_overlap,
        "intent_bonus": intent_bonus,
        "chronology_timestamp": timestamps.get(rid).isoformat() if timestamps.get(rid) else None,
    }


def select(docs, semantic, nodes, q, final_k=5):
    intent = _query_intent(q)
    seed_ids = {x["record_id"] for x in semantic[:5]}
    anchor_cutoff = 12 if intent["chain"] else 8
    anchor_ids = {x["record_id"] for x in semantic[:anchor_cutoff]}
    timestamps = {d.record_id: _extract_timestamp(getattr(d, "text", "")) for d in docs}
    feats = [_feature_row(d, semantic, nodes, q, intent, seed_ids, anchor_ids, timestamps) for d in docs]
    eligible = [f for f in feats if f["schema_eligible"]]
    if not eligible:
        raise ValueError("no schema-eligible records for query")
    first = min(eligible, key=lambda x: x["semantic_rank"])
    rest = [x for x in eligible if x is not first]
    if intent["chain"]:
        rest.sort(
            key=lambda x: (
                -x["intent_bonus"],
                -x["anchor_seed_link_count"],
                -x["direct_seed_link_count"],
                -x["anchor_two_hop_reach_count"],
                -x["two_hop_seed_reach_count"],
                -x["query_overlap"],
                x["semantic_rank"],
                x["record_id"],
            )
        )
    else:
        rest.sort(
            key=lambda x: (
                -x["direct_seed_link_count"],
                -x["two_hop_seed_reach_count"],
                x["min_seed_hops"],
                x["semantic_rank"],
                x["record_id"],
            )
        )
    return [
        first
    ] + rest[: final_k - 1], feats, {"chain": intent["chain"], "assertion_class": set(intent["assertion_terms"]), "status": set(intent["status_terms"])}


def _canonical_metadata(record_id, root=Path("records")):
    for p in root.rglob(f"{record_id}.md"):
        text, meta = _front(p)
        head = text[4 : text.index("\n---\n", 4)]
        return meta, re.search(r"^source_ids:\s*(.*)$", head, re.M).group(1), re.search(r"^evidence:\s*(.*)$", head, re.M).group(1)
    raise ValueError(f"canonical record not found: {record_id}")


def evidence_bundle(result, docs, nodes):
    selected = {x["record_id"] for x in result["final"]}
    by = {d.record_id: d for d in docs}
    expanded = expand_derived_from(result["final"], nodes, corpus_ids=by.keys())
    expanded_ids = [x["record_id"] for x in expanded]
    records = []
    for x in result["final"]:
        d = by[x["record_id"]]
        meta, sources, evidence = _canonical_metadata(d.record_id)
        body = d.text.split("Historical account", 1)[-1].split("Source IDs", 1)[0].strip()
        records.append(
            {
                "record_id": d.record_id,
                "title": d.title,
                "kind": meta["kind"],
                "assertion_class": d.assertion_class,
                "status": d.status,
                "historical_account": body,
                "source_ids": re.findall(r"SRC-[A-Z0-9-]+", sources),
                "evidence_locators": re.findall(r"locator:\s*([^,}]+)", evidence),
                "relationships": [
                    {"source_id": d.record_id, "relationship_type": t, "target_id": n, "traversal": "outgoing"}
                    for t, n in nodes[d.record_id]["out"]
                    if n in selected
                ]
                + [
                    {"source_id": n, "relationship_type": t, "target_id": d.record_id, "traversal": "incoming"}
                    for t, n in nodes[d.record_id]["in"]
                    if n in selected
                ],
                "retrieval": x,
            }
        )
    for item in expanded:
        d = by[item["record_id"]]
        meta, sources, evidence = _canonical_metadata(d.record_id)
        body = d.text.split("Historical account", 1)[-1].split("Source IDs", 1)[0].strip()
        records.append(
            {
                "record_id": d.record_id,
                "title": d.title,
                "kind": meta["kind"],
                "assertion_class": d.assertion_class,
                "status": d.status,
                "historical_account": body,
                "source_ids": re.findall(r"SRC-[A-Z0-9-]+", sources),
                "evidence_locators": re.findall(r"locator:\s*([^,}]+)", evidence),
                "relationships": [
                    {"source_id": item["expanded_from"], "relationship_type": item["relationship_type"], "target_id": d.record_id, "traversal": "expanded"}
                ],
                "retrieval": {
                    "record_id": d.record_id,
                    "expanded_from": item["expanded_from"],
                    "relationship_type": item["relationship_type"],
                    "hop": item["hop"],
                    "seed_rank": item["seed_rank"],
                    "expansion_order": item["expansion_order"],
                },
                "evidence_role": "expanded_derived_from_context",
            }
        )
    return {
        "query": result["query"],
        "parsed_constraints": result.get("schema_constraints", {}),
        "records": records,
        "selected_record_ids": [x["record_id"] for x in result["final"]],
        "expanded_record_ids": expanded_ids,
        "evidence_record_ids": [r["record_id"] for r in records],
        "expansion_provenance": expanded,
    }


def materialize_hybrid_bundle(hybrid, docs, nodes, canonical_root=Path("records"), max_added_records=None):
    by = {d.record_id: d for d in docs}
    selected_records = []
    selected = set(hybrid["record_ids"])
    for rid in hybrid["record_ids"]:
        if rid not in by:
            raise ValueError(f"hybrid record is absent from corpus: {rid}")
        selected_rows = [z for z in hybrid["semantic_selection"] + hybrid["structured_selection"] if z["record_id"] == rid]
        if not selected_rows:
            raise ValueError(f"missing selected record in retrieval result: {rid}")
        d = by[rid]
        meta, sources, evidence = _canonical_metadata(rid, canonical_root)
        provenance = hybrid.get("retrieval_provenance_by_channel", {}).get(rid, {})
        if isinstance(provenance, list):
            provenance = {item.get("channel"): item.get("provenance") for item in provenance if isinstance(item, dict)}
        if not isinstance(provenance, dict):
            provenance = {}
        selected_records.append(
            {
                "record_id": rid,
                "title": d.title,
                "kind": meta["kind"],
                "assertion_class": d.assertion_class,
                "status": d.status,
                "historical_account": d.text.split("Historical account", 1)[-1].split("Source IDs", 1)[0].strip(),
                "source_ids": re.findall(r"SRC-[A-Z0-9-]+", sources),
                "evidence_locators": re.findall(r"locator:\s*([^,}]+)", evidence),
                "relationships": [
                    {"source_id": rid, "relationship_type": t, "target_id": n, "traversal": "outgoing"}
                    for t, n in nodes[rid]["out"]
                    if n in selected
                ]
                + [
                    {"source_id": n, "relationship_type": t, "target_id": rid, "traversal": "incoming"}
                    for t, n in nodes[rid]["in"]
                    if n in selected
                ],
                "retrieval_channels": hybrid["retrieval_channels"][rid],
                "retrieval_provenance_by_channel": provenance,
                "retrieval": selected_rows[0],
                "schema_eligible": selected_rows[0].get("schema_eligible", True),
                "evidence_role": "primary_constrained_evidence"
                if selected_rows[0].get("schema_eligible", True)
                else "supplemental_semantic_context",
            }
        )
    expanded = expand_derived_from(selected_records, nodes, corpus_ids=by.keys(), max_added_records=max_added_records)
    records = list(selected_records)
    for item in expanded:
        d = by[item["record_id"]]
        meta, sources, evidence = _canonical_metadata(d.record_id, canonical_root)
        records.append(
            {
                "record_id": d.record_id,
                "title": d.title,
                "kind": meta["kind"],
                "assertion_class": d.assertion_class,
                "status": d.status,
                "historical_account": d.text.split("Historical account", 1)[-1].split("Source IDs", 1)[0].strip(),
                "source_ids": re.findall(r"SRC-[A-Z0-9-]+", sources),
                "evidence_locators": re.findall(r"locator:\s*([^,}]+)", evidence),
                "relationships": [
                    {"source_id": item["expanded_from"], "relationship_type": item["relationship_type"], "target_id": d.record_id, "traversal": "expanded"}
                ],
                "retrieval_channels": [],
                "retrieval_provenance_by_channel": {},
                "retrieval": {
                    "record_id": d.record_id,
                    "expanded_from": item["expanded_from"],
                    "relationship_type": item["relationship_type"],
                    "hop": item["hop"],
                    "seed_rank": item["seed_rank"],
                    "expansion_order": item["expansion_order"],
                },
                "schema_eligible": True,
                "evidence_role": "expanded_derived_from_context",
            }
        )
    return {
        "query": hybrid["query"],
        "parsed_constraints": hybrid.get("parsed_constraints", {}),
        "record_ids": hybrid["record_ids"],
        "selected_record_ids": [r["record_id"] for r in selected_records],
        "expanded_record_ids": [r["record_id"] for r in records if r not in selected_records],
        "evidence_record_ids": [r["record_id"] for r in records],
        "retrieval_channels": hybrid["retrieval_channels"],
        "records": records,
        "selected_records": selected_records,
        "expanded_records": expanded,
        "expected_ids": hybrid.get("expected_ids"),
        "coverage": hybrid.get("coverage"),
    }


def materialize_frozen_retrieval_result(row, docs, nodes, canonical_root=Path("records"), max_added_records=None):
    hybrid = {
        "query": row["question"],
        "parsed_constraints": row.get("parsed_constraints", {}),
        "record_ids": row["hybrid_record_ids"],
        "retrieval_channels": row.get("retrieval_channels", {}),
        "retrieval_provenance_by_channel": row.get("retrieval_provenance_by_channel", {}),
        "semantic_selection": row.get("semantic_selection", []),
        "structured_selection": row.get("structured_selection", []),
    }
    return materialize_hybrid_bundle(hybrid, docs, nodes, canonical_root, max_added_records=max_added_records)


def bundle_markdown(bundle):
    return "\n\n".join(
        f"## [{r['record_id']}] {r['title']}\n"
        f"Kind: {r['kind']}\n"
        f"Assertion class: {r['assertion_class']}\n"
        f"Status: {r['status']}\n\n"
        f"Historical account:\n{r['historical_account']}\n\n"
        f"Source IDs: {', '.join(r['source_ids'])}\n"
        f"Evidence locators: {', '.join(r['evidence_locators'])}\n"
        f"Relevant relationships: {json.dumps(r['relationships'], sort_keys=True)}\n"
        f"Retrieval provenance: {json.dumps(r['retrieval'], sort_keys=True)}"
        for r in bundle["records"]
    )


def hybrid_bundle(result):
    sem = result["semantic_candidates"][:5]
    structured = result["final"][:5]
    order = []
    channels = {}
    for channel, items in (("semantic", sem), ("structured", structured)):
        for item in items:
            rid = item["record_id"]
            channels.setdefault(rid, []).append(channel)
            if rid not in order:
                order.append(rid)
    expected = result.get("expected_ids", [])
    recovered = sorted(set(expected) & set(order))
    return {
        "query": result["query"],
        "parsed_constraints": result.get("schema_constraints", {}),
        "semantic_selection": sem,
        "structured_selection": structured,
        "record_ids": order,
        "retrieval_channels": channels,
        "expected_ids": expected,
        "coverage": {
            "recovered_ids": recovered,
            "recall": len(recovered) / len(expected) if expected else None,
            "full": set(expected) <= set(order),
        },
    }


def reasoner_view(bundle):
    forbidden = {"expected_ids", "coverage", "pass", "recovered_ids", "fixture", "fixture_id", "acceptance", "evaluation"}

    def scrub(x, root=False):
        if isinstance(x, dict):
            if not root and any(k.lower() in forbidden for k in x):
                raise ValueError("evaluation field leaked into reasoner view")
            return {k: scrub(v) for k, v in x.items() if k.lower() not in forbidden}
        if isinstance(x, list):
            return [scrub(v) for v in x]
        return x

    return scrub(bundle, True)


def reasoning_request(question, bundle):
    return {
        "system": "Answer only from supplied Historian evidence. Cite stable record IDs for every material claim; distinguish observed facts from interpretations; retain candidate/disputed status; state insufficiency; do not infer unsupported capability conclusions; return the grounded-answer JSON contract.",
        "user": question + "\n\nEVIDENCE BUNDLE:\n" + bundle_markdown(reasoner_view(bundle)),
    }


def validate_answer(answer, bundle):
    required = {
        "answer",
        "cited_record_ids",
        "evidence_used",
        "uncertainty_or_limitations",
        "contradictions_or_missing_evidence",
    }
    errors = []
    if not isinstance(answer, dict):
        return {"valid": False, "errors": ["answer must be an object"]}
    errors += [f"missing required field: {x}" for x in required - set(answer)]
    if set(answer) - required:
        errors.append("unexpected top-level fields present")
    for k in ("cited_record_ids", "evidence_used"):
        if k in answer and not isinstance(answer[k], list):
            errors.append(f"{k} must be a list")
    if errors:
        return {"valid": False, "errors": errors}
    ids = {r["record_id"] for r in bundle["records"]}
    if len(answer["cited_record_ids"]) != len(set(answer["cited_record_ids"])):
        errors.append("duplicate citations")
    if len(answer["evidence_used"]) != len(set(answer["evidence_used"])):
        errors.append("duplicate evidence_used")
    if not set(answer["cited_record_ids"]) <= ids:
        errors.append("citation outside supplied evidence")
    if not set(answer["evidence_used"]) <= ids:
        errors.append("evidence_used outside supplied evidence")
    if not set(answer["cited_record_ids"]) <= set(answer["evidence_used"]):
        errors.append("cited_record_ids must be a subset of evidence_used")
    return {"valid": not errors, "errors": errors}
