"""Generic deterministic retrieval assembly over a supplied query fixture."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
try:
    from sentence_transformers import SentenceTransformer
except ModuleNotFoundError:  # pragma: no cover - exercised only in model-free tests
    SentenceTransformer = None

from historian.retrieval import _norm, load_documents
from historian.structured_librarian import select, structured_index

RETRIEVAL_REQUIREMENTS = "requirements-retrieval.txt"
RETRIEVAL_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
ROOT = Path(__file__).resolve().parents[1]


def run(queries, out):
    root = ROOT
    if SentenceTransformer is None:
        raise ModuleNotFoundError(
            "sentence_transformers is required for retrieval execution; install the retrieval runtime with "
            f"`python3 -m pip install -r {RETRIEVAL_REQUIREMENTS}` "
            f"and provision the pinned embedding model `{RETRIEVAL_MODEL_ID}`."
        )
    docs = load_documents(root / "interfaces/khoj/corpus")
    manifest = json.load(open(root / "interfaces/retrieval/state/manifest.json"))
    embeddings = np.load(root / "interfaces/retrieval/state/embeddings.npy")
    encoder = SentenceTransformer(
        manifest["encoder_model"],
        revision=manifest["encoder_revision"],
        device="cpu",
        trust_remote_code=False,
        local_files_only=True,
    )
    nodes, _, audit = structured_index(root / "records")
    rows = []
    fixture = json.load(open(queries))
    seen = set()

    for q in fixture:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not isinstance(q.get("question"), str) or q["id"] in seen:
            raise ValueError("invalid or duplicate query")
        seen.add(q["id"])
        question_vec = _norm(
            encoder.encode([q["question"]], convert_to_numpy=True, normalize_embeddings=False, show_progress_bar=False)
        )[0]
        scores = embeddings @ question_vec
        order = np.argsort(-scores, kind="stable")
        semantic = [
            {"record_id": docs[i].record_id, "cosine_score": float(scores[i]), "rank": r + 1}
            for r, i in enumerate(order)
        ]
        structured, features, schema_constraints = select(docs, semantic, nodes, q["question"], final_k=10)
        record_ids = []
        channels = {}
        for channel, items in (("semantic", semantic[:5]), ("structured", structured)):
            for item in items:
                rid = item["record_id"]
                channels.setdefault(rid, {})
                channels[rid][channel] = item
                if rid not in record_ids and len(record_ids) < 10:
                    record_ids.append(rid)
        parsed_constraints = {}
        for key, value in schema_constraints.items():
            if isinstance(value, bool):
                parsed_constraints[key] = value
            else:
                parsed_constraints[key] = sorted(value)
        rows.append(
            {
                "id": q["id"],
                "question": q["question"],
                "semantic_candidates": semantic,
                "semantic_selection": semantic[:5],
                "structured_selection": structured,
                "hybrid_record_ids": record_ids,
                "retrieval_channels": {k: list(v) for k, v in channels.items()},
                "retrieval_provenance_by_channel": channels,
                "parsed_constraints": parsed_constraints,
                "feature_values": features,
                "query_intent": {"chain": bool(schema_constraints.get("chain"))},
            }
        )
    metadata = {
        "document_count": len(docs),
        "corpus_fingerprint": manifest.get("corpus_sha256"),
        "model": manifest["encoder_model"],
        "revision": manifest["encoder_revision"],
        "embedding_dimension": int(embeddings.shape[1]),
        "retrieval_state": {
            "manifest": "interfaces/retrieval/state/manifest.json",
            "embeddings": "interfaces/retrieval/state/embeddings.npy",
        },
        "graph_audit": audit,
        "query_fixture_sha256": hashlib.sha256(Path(queries).read_bytes()).hexdigest(),
    }
    json.dump({**metadata, "queries": rows}, open(out, "w"), indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.queries, args.output)
