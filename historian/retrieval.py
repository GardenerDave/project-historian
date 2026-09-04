"""Small, local-only semantic retrieval over the generated Historian corpus."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FIELD_RE = re.compile(r"^(Stable ID|Assertion class|Status):\s+`?(?:\*+)?([^`*\n]+)", re.M)


@dataclass(frozen=True)
class Document:
    record_id: str
    title: str
    assertion_class: str
    status: str
    source_file: str
    corpus_file: str
    text: str


class RetrievalStateMismatch(RuntimeError):
    """Raised when generated embeddings no longer match their corpus or encoder."""


RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANKER_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
BI_ENCODER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BI_ENCODER_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def normalize_encoder_identity(value: str) -> tuple[str, str | None] | None:
    """Normalize only the pinned model ID or its exact HF snapshot path."""
    if value == BI_ENCODER_MODEL:
        return BI_ENCODER_MODEL, None
    marker = "/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/"
    if marker in value:
        revision = value.rsplit(marker, 1)[1].split("/", 1)[0]
        if revision == BI_ENCODER_REVISION and value == value.rsplit("/", 1)[0] + "/" + revision:
            return BI_ENCODER_MODEL, revision
    return None


def encoder_identity_matches(requested: str, manifest_source: str, manifest_revision: str | None) -> bool:
    requested_identity = normalize_encoder_identity(requested)
    source_identity = normalize_encoder_identity(manifest_source)
    return bool(requested_identity and requested_identity[0] == BI_ENCODER_MODEL and source_identity and source_identity[0] == BI_ENCODER_MODEL and manifest_revision == BI_ENCODER_REVISION and (source_identity[1] in (None, BI_ENCODER_REVISION)))


def load_documents(corpus: Path) -> list[Document]:
    docs = []
    for path in sorted((corpus / "records").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fields = dict(FIELD_RE.findall(text))
        record_id = fields["Stable ID"]
        docs.append(Document(record_id, text.splitlines()[0].removeprefix("# "), fields["Assertion class"], fields["Status"], f"records/{record_id}.md", str(path.relative_to(corpus)), text))
    return docs


def corpus_fingerprint(docs: list[Document]) -> str:
    payload = [{"record_id": d.record_id, "corpus_file": d.corpus_file, "text": d.text} for d in docs]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_state(manifest: dict[str, Any], docs: list[Document], model_name: str | None = None, revision: str | None = None) -> None:
    ids = [d.record_id for d in docs]
    files = [d.corpus_file for d in docs]
    if manifest.get("corpus_sha256") != corpus_fingerprint(docs):
        raise RetrievalStateMismatch("retrieval state corpus fingerprint does not match current corpus; explicitly rebuild state")
    if manifest.get("document_count") != len(docs):
        raise RetrievalStateMismatch("retrieval state document count does not match current corpus; explicitly rebuild state")
    if manifest.get("record_ids") != ids or manifest.get("corpus_files") != files:
        raise RetrievalStateMismatch("retrieval state ordered record mapping does not match current corpus; explicitly rebuild state")
    if manifest.get("encoder_revision") != BI_ENCODER_REVISION:
        raise RetrievalStateMismatch("retrieval state encoder revision is not the pinned revision; explicitly rebuild state")
    if model_name is not None and not encoder_identity_matches(model_name, manifest.get("encoder_model", ""), manifest.get("encoder_revision")):
        raise RetrievalStateMismatch("requested encoder model conflicts with retrieval state; use the state encoder or explicitly rebuild")
    if revision is not None and revision != BI_ENCODER_REVISION:
        raise RetrievalStateMismatch("requested encoder revision conflicts with retrieval state; explicitly rebuild")


def validate_reranker_identity(model_name: str, revision: str) -> None:
    if model_name != RERANKER_MODEL or revision != RERANKER_REVISION:
        raise RetrievalStateMismatch("reranker identity/revision is not the pinned evaluation configuration")


def load_reranker(model_name: str = RERANKER_MODEL, revision: str = RERANKER_REVISION):
    validate_reranker_identity(model_name, revision)
    from sentence_transformers import CrossEncoder
    return CrossEncoder(
        model_name,
        revision=revision,
        device="cpu",
        trust_remote_code=False,
        local_files_only=True,
    )


def _norm(embeddings):
    import numpy as np
    return embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-12)


def build_state(corpus: Path, state: Path, model_name: str, revision: str | None = None) -> dict[str, Any]:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    docs = load_documents(corpus)
    state.mkdir(parents=True, exist_ok=True)
    encoder = SentenceTransformer(
        model_name,
        revision=revision,
        device="cpu",
        trust_remote_code=False,
        local_files_only=True,
    )
    started = time.perf_counter()
    embeddings = _norm(encoder.encode([d.text for d in docs], convert_to_numpy=True, normalize_embeddings=False, show_progress_bar=False))
    np.save(state / "embeddings.npy", embeddings)
    manifest = {"encoder_model": model_name, "encoder_revision": revision, "reranker_model": None, "dimensionality": int(embeddings.shape[1]), "document_count": len(docs), "record_ids": [d.record_id for d in docs], "corpus_files": [d.corpus_file for d in docs], "documents": [asdict(d) | {"text": None} for d in docs], "corpus_sha256": corpus_fingerprint(docs), "indexing_seconds": time.perf_counter() - started}
    (state / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def search(state: Path, corpus: Path, query: str, model_name: str | None = None, reranker_name: str = RERANKER_MODEL, top_k: int = 10, revision: str | None = None, reranker_revision: str | None = None) -> dict[str, Any]:
    import numpy as np
    from sentence_transformers import CrossEncoder, SentenceTransformer
    manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
    docs = load_documents(corpus)
    validate_state(manifest, docs, model_name, revision)
    validate_reranker_identity(reranker_name, reranker_revision or RERANKER_REVISION)
    encoder = SentenceTransformer(
        manifest["encoder_model"],
        revision=manifest.get("encoder_revision"),
        device="cpu",
        trust_remote_code=False,
        local_files_only=True,
    )
    q = _norm(encoder.encode([query], convert_to_numpy=True, normalize_embeddings=False, show_progress_bar=False))[0]
    vectors = np.load(state / "embeddings.npy")
    scores = vectors @ q
    order = np.argsort(-scores, kind="stable")[:top_k]
    semantic = [{"record_id": docs[i].record_id, "cosine_score": float(scores[i]), "rank": rank + 1} for rank, i in enumerate(order)]
    reranker = load_reranker(reranker_name, reranker_revision or RERANKER_REVISION)
    pairs = [(query, docs[i].text) for i in order]
    rr_scores = np.asarray(reranker.predict(pairs, show_progress_bar=False), dtype=float)
    rr_order = np.argsort(-rr_scores, kind="stable")
    reranked = [{"record_id": docs[order[j]].record_id, "cosine_score": float(scores[order[j]]), "reranker_score": float(rr_scores[j]), "rank": rank + 1} for rank, j in enumerate(rr_order)]
    return {"query": query, "semantic_candidates": semantic, "semantic": semantic[:5], "reranked": reranked[:5], "dimensionality": manifest["dimensionality"]}


def evaluate(corpus: Path, state: Path, fixture: Path, model_name: str | None, reranker_name: str, candidate_k: int = 10, final_k: int = 5, revision: str | None = None, reranker_revision: str | None = None) -> dict[str, Any]:
    queries = json.loads(fixture.read_text(encoding="utf-8"))
    results = []
    for item in queries:
        result = search(state, corpus, item["query"], model_name, reranker_name, candidate_k, revision, reranker_revision)
        expected = set(item["expected_ids"])
        for key in ("semantic", "reranked"):
            result[key] = result[key][:final_k]
            result[key + "_hit"] = bool(expected.intersection(x["record_id"] for x in result[key]))
            result[key + "_hit_at_1"] = int(bool(expected.intersection(x["record_id"] for x in result[key][:1])))
            result[key + "_hit_at_3"] = int(bool(expected.intersection(x["record_id"] for x in result[key][:3])))
            result[key + "_recall_at_5"] = len(expected.intersection(x["record_id"] for x in result[key][:5])) / len(expected)
            ranks = [x["rank"] for x in result[key] if x["record_id"] in expected]
            result[key + "_first_expected_rank"] = min(ranks) if ranks else None
            result[key + "_reciprocal_rank"] = 1 / min(ranks) if ranks else 0.0
        results.append({"id": item["id"], "expected_ids": item["expected_ids"], **result})
    return {"model": model_name, "reranker": reranker_name, "candidate_k": candidate_k, "final_k": final_k, "queries": results}
