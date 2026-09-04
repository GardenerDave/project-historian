from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from historian import acceptance_retrieval, reasoner_client
from historian.retrieval import load_documents
from historian.structured_librarian import (
    materialize_frozen_retrieval_result,
    reasoner_view,
    reasoning_request,
    structured_index,
)

DEFAULT_WORK_ROOT = Path(".work") / "historian_queries"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8765
PIPELINE_LOCK = threading.Lock()


def normalize_question(question: str) -> str:
    return " ".join(question.strip().split())


def question_fingerprint(question: str) -> str:
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()


def request_id() -> str:
    return "op-" + str(uuid.uuid4())


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write((json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _runtime_dirs(work_root: Path, rid: str) -> dict[str, Path]:
    base = work_root / rid
    return {
        "base": base,
        "query": base / "query.json",
        "retrieval": base / "retrieval.json",
        "reasoner": base / "reasoner",
    }


def _public_evidence_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "api_version": "v1",
        "request_id": payload["request_id"],
        "question": payload["question"],
        "question_fingerprint": payload["question_fingerprint"],
        "status": payload["status"],
        "selected_record_ids": payload.get("selected_record_ids", []),
        "evidence": payload.get("evidence", []),
        "retrieval_provenance": payload.get("retrieval_provenance", {}),
        "parsed_constraints": payload.get("parsed_constraints", {}),
    }


def _public_query_response(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") != "ok":
        return {
            "api_version": "v1",
            "request_id": payload["request_id"],
            "question": payload["question"],
            "question_fingerprint": payload["question_fingerprint"],
            "status": "failed",
            "error_code": payload.get("error_code", "internal_error"),
            "error": payload.get("error", "Historian query failed"),
        }
    return {
        "api_version": "v1",
        "request_id": payload["request_id"],
        "question": payload["question"],
        "question_fingerprint": payload["question_fingerprint"],
        "status": payload["status"],
        "selected_record_ids": payload.get("selected_record_ids", []),
        "answer": payload.get("answer"),
        "cited_record_ids": payload.get("cited_record_ids", []),
        "evidence_used": payload.get("evidence_used", []),
        "uncertainty_or_limitations": payload.get("uncertainty_or_limitations", ""),
        "contradictions_or_missing_evidence": payload.get("contradictions_or_missing_evidence", []),
        "validation": payload.get("validation", {}),
    }


def _frozen_retrieval_contract(question: str, rid: str, work_root: Path) -> tuple[dict[str, Any], Path]:
    dirs = _runtime_dirs(work_root, rid)
    dirs["base"].mkdir(parents=True, exist_ok=True)
    query_fixture = [{"id": rid, "question": question}]
    _write_json(dirs["query"], query_fixture)
    acceptance_retrieval.run(dirs["query"], dirs["retrieval"])
    retrieval = _read_json(dirs["retrieval"])
    row = next(row for row in retrieval["queries"] if row["id"] == rid)
    repo_root = Path(__file__).resolve().parents[1]
    docs = load_documents(repo_root / "interfaces/khoj/corpus")
    nodes, _, _ = structured_index(repo_root / "records")
    bundle = materialize_frozen_retrieval_result(row, docs, nodes)
    evidence = reasoner_view(bundle)
    return {
        "request_id": rid,
        "question": question,
        "question_fingerprint": question_fingerprint(question),
        "status": "ok",
        "selected_record_ids": row["hybrid_record_ids"],
        "evidence": evidence["records"],
        "retrieval_provenance": row.get("retrieval_provenance_by_channel", {}),
        "parsed_constraints": row.get("parsed_constraints", {}),
        "runtime": {
            "work_root": str(work_root),
            "query_path": str(dirs["query"]),
            "retrieval_path": str(dirs["retrieval"]),
            "base_dir": str(dirs["base"]),
        },
    }, dirs["base"]


def _locked_retrieve(question: str, *, work_root: Path | str | None = None) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a nonempty string")
    root = Path(work_root) if work_root is not None else DEFAULT_WORK_ROOT
    rid = request_id()
    payload, _ = _frozen_retrieval_contract(question, rid, root)
    return payload


def retrieve(question: str, *, work_root: Path | str | None = None) -> dict[str, Any]:
    with PIPELINE_LOCK:
        return _locked_retrieve(question, work_root=work_root)


def query(
    question: str,
    *,
    endpoint: str | None = None,
    work_root: Path | str | None = None,
    max_tokens: int = 1536,
) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a nonempty string")
    endpoint = endpoint or os.environ.get("HISTORIAN_REASONER_ENDPOINT")
    if not endpoint:
        return {
            "request_id": request_id(),
            "question": question,
            "question_fingerprint": question_fingerprint(question),
            "status": "failed",
            "error_code": "reasoner_unavailable",
            "error": "HISTORIAN_REASONER_ENDPOINT is required",
        }
    root = Path(work_root) if work_root is not None else DEFAULT_WORK_ROOT
    rid = request_id()
    try:
        with PIPELINE_LOCK:
            retrieval_payload, base = _frozen_retrieval_contract(question, rid, root)
            reasoner_dir = base / "reasoner"
            reasoner_dir.mkdir(parents=True, exist_ok=True)
            reasoner_client.run(rid, reasoner_dir, endpoint, retrieval_payload["runtime"]["retrieval_path"], max_tokens=max_tokens)
            tx_path = reasoner_dir / f"{rid}.transaction.json"
            result_path = reasoner_dir / f"{rid}.result.json"
            tx = _read_json(tx_path) if tx_path.exists() else {}
            result = _read_json(result_path) if result_path.exists() else {}
        parsed = result.get("parsed_response") if isinstance(result, dict) else None
        validation = result.get("validation") if isinstance(result, dict) else None
        status = "ok" if tx.get("state") == "COMPLETE" and isinstance(validation, dict) and validation.get("contract_valid") else "failed"
        error_code = None
        if status != "ok":
            if tx.get("state") == "COMPLETE" and isinstance(validation, dict):
                error_code = "reasoner_invalid_response"
            else:
                error_code = "reasoner_unavailable"
        return {
            "request_id": rid,
            "question": question,
            "question_fingerprint": question_fingerprint(question),
            "status": status,
            "error_code": error_code,
            "selected_record_ids": retrieval_payload["selected_record_ids"],
            "answer": parsed.get("answer") if isinstance(parsed, dict) else None,
            "cited_record_ids": parsed.get("cited_record_ids") if isinstance(parsed, dict) else [],
            "evidence_used": parsed.get("evidence_used") if isinstance(parsed, dict) else [],
            "uncertainty_or_limitations": parsed.get("uncertainty_or_limitations") if isinstance(parsed, dict) else "",
            "contradictions_or_missing_evidence": parsed.get("contradictions_or_missing_evidence") if isinstance(parsed, dict) else [],
            "validation": validation if isinstance(validation, dict) else {},
            "runtime": {
                "work_root": str(root),
                "request_dir": str(base),
                "retrieval_path": retrieval_payload["runtime"]["retrieval_path"],
                "reasoner_dir": str(reasoner_dir),
            },
        }
    except Exception as exc:
        error_text = f"Historian query failed: {exc}"
        error_code = "internal_error"
        if isinstance(exc, RuntimeError) and "endpoint unavailable" in str(exc):
            error_text = "Historian reasoner endpoint is unavailable"
            error_code = "reasoner_unavailable"
        elif isinstance(exc, OSError):
            error_text = f"Historian reasoner transport failed: {exc}"
            error_code = "reasoner_unavailable"
        elif isinstance(exc, json.JSONDecodeError):
            error_text = "Historian reasoner returned malformed JSON"
            error_code = "reasoner_invalid_response"
        return {
            "request_id": rid,
            "question": question,
            "question_fingerprint": question_fingerprint(question),
            "status": "failed",
            "error_code": error_code,
            "error": error_text,
        }


def ask(question: str, *, endpoint: str | None = None, work_root: Path | str | None = None, max_tokens: int = 1536) -> dict[str, Any]:
    return query(question, endpoint=endpoint, work_root=work_root, max_tokens=max_tokens)


def _normalize_bind_host(host: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    return host


def _is_loopback_host(host: str) -> bool:
    host = _normalize_bind_host(host)
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def serve(host: str = DEFAULT_BIND_HOST, port: int = DEFAULT_BIND_PORT, work_root: Path | str | None = None, endpoint: str | None = None) -> ThreadingHTTPServer:
    host = _normalize_bind_host(host)
    if not _is_loopback_host(host):
        raise SystemExit("non-loopback service access is intentionally unsupported until an authenticated transport is designed")
    root = Path(work_root) if work_root is not None else DEFAULT_WORK_ROOT
    root.mkdir(parents=True, exist_ok=True)

    class HistorianHandler(BaseHTTPRequestHandler):
        server_version = "Historian/1.0"

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError(f"malformed JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def do_GET(self):  # noqa: N802
            if self.path == "/v1/health":
                self._send_json(HTTPStatus.OK, {"api_version": "v1", "status": "ok", "service": "historian", "mode": "read-only"})
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"api_version": "v1", "status": "failed", "error": "not found"})

        def do_POST(self):  # noqa: N802
            try:
                body = self._read_body()
                question = body.get("question")
                if not isinstance(question, str) or not question.strip():
                    raise ValueError("question must be a nonempty string")
                if self.path == "/v1/evidence":
                    payload = _public_evidence_response(retrieve(question, work_root=root))
                    self._send_json(HTTPStatus.OK, payload)
                elif self.path == "/v1/query":
                    payload = _public_query_response(query(question, endpoint=endpoint or os.environ.get("HISTORIAN_REASONER_ENDPOINT"), work_root=root))
                    status = HTTPStatus.OK if payload.get("status") == "ok" else HTTPStatus.BAD_GATEWAY
                    self._send_json(status, payload)
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"api_version": "v1", "status": "failed", "error": "not found"})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"api_version": "v1", "status": "failed", "error": str(exc)})
            except SystemExit as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"api_version": "v1", "status": "failed", "error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"api_version": "v1", "status": "failed", "error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, format, *args):  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), HistorianHandler)
    server.daemon_threads = True
    return server
