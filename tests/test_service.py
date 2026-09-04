from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import historian.cli as cli_mod
import historian.service as service_mod


def _fake_retrieve(question, *, work_root=None):
    rid = service_mod.request_id()
    base = Path(work_root or ".work/historian_queries") / rid
    base.mkdir(parents=True, exist_ok=True)
    (base / "query.json").write_text(json.dumps([{"id": rid, "question": question}]) + "\n")
    (base / "retrieval.json").write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "id": rid,
                        "question": question,
                        "semantic_candidates": [],
                        "semantic_selection": [],
                        "structured_selection": [],
                        "hybrid_record_ids": ["CLM-example-unmeasured"],
                        "retrieval_channels": {"CLM-example-unmeasured": ["semantic"]},
                        "retrieval_provenance_by_channel": {"CLM-example-unmeasured": {"semantic": {"rank": 1}}},
                        "parsed_constraints": {},
                    }
                ]
            }
        )
        + "\n"
    )
    return {
        "request_id": rid,
        "question": question,
        "question_fingerprint": service_mod.question_fingerprint(question),
        "status": "ok",
        "selected_record_ids": ["CLM-example-unmeasured"],
        "evidence": [{"record_id": "CLM-example-unmeasured"}],
        "retrieval_provenance": {"CLM-example-unmeasured": {"semantic": {"rank": 1}}},
        "parsed_constraints": {},
        "runtime": {"work_root": str(work_root), "query_path": str(base / "query.json"), "retrieval_path": str(base / "retrieval.json"), "base_dir": str(base)},
    }


def _fake_query(question, *, endpoint=None, work_root=None, max_tokens=1536):
    base = _fake_retrieve(question, work_root=work_root)
    rid = base["request_id"]
    reasoner_dir = Path(work_root or ".work/historian_queries") / rid / "reasoner"
    reasoner_dir.mkdir(parents=True, exist_ok=True)
    (reasoner_dir / f"{rid}.transaction.json").write_text(
        json.dumps({"query_id": rid, "state": "COMPLETE", "transport_status": "http_200"}) + "\n"
    )
    (reasoner_dir / f"{rid}.result.json").write_text(
        json.dumps(
            {
                "query_id": rid,
                "question": question,
                "parsed_response": {
                    "answer": "The example capability was considered unmeasured because transport failures prevented measurement.",
                    "cited_record_ids": ["CLM-example-unmeasured"],
                    "evidence_used": ["CLM-example-unmeasured"],
                    "uncertainty_or_limitations": "",
                    "contradictions_or_missing_evidence": [],
                },
                "validation": {
                    "schema_valid": {"valid": True, "errors": []},
                    "grounding_valid": {"valid": True, "errors": []},
                    "contract_valid": True,
                },
            }
        )
        + "\n"
    )
    return {
        **base,
        "status": "ok",
        "answer": "The example capability was considered unmeasured because transport failures prevented measurement.",
        "cited_record_ids": ["CLM-example-unmeasured"],
        "evidence_used": ["CLM-example-unmeasured"],
        "uncertainty_or_limitations": "",
        "contradictions_or_missing_evidence": [],
        "validation": {"schema_valid": {"valid": True, "errors": []}, "grounding_valid": {"valid": True, "errors": []}, "contract_valid": True},
    }


def _fake_reasoner_run(query_id, run_dir, endpoint, retrieval_results, max_tokens=1536):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{query_id}.transaction.json").write_text(
        json.dumps({"query_id": query_id, "state": "COMPLETE", "transport_status": "http_200"}) + "\n"
    )
    (run_dir / f"{query_id}.result.json").write_text(
        json.dumps(
            {
                "query_id": query_id,
                "question": "Why was the example capability considered unmeasured?",
                "parsed_response": {
                    "answer": "The example capability was considered unmeasured because transport failures prevented measurement.",
                    "cited_record_ids": ["CLM-example-unmeasured"],
                    "evidence_used": ["CLM-example-unmeasured"],
                    "uncertainty_or_limitations": "",
                    "contradictions_or_missing_evidence": [],
                },
                "validation": {
                    "schema_valid": {"valid": True, "errors": []},
                    "grounding_valid": {"valid": True, "errors": []},
                    "contract_valid": True,
                },
            }
        )
        + "\n"
    )


def test_retrieve_reuses_shared_path_and_keeps_request_ids_distinct(tmp_path, monkeypatch):
    calls = []

    def fake_request_id():
        return f"op-{len(calls)+1:02d}"

    monkeypatch.setattr(service_mod, "request_id", fake_request_id)
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))

    a = service_mod.retrieve("Why was the example capability considered unmeasured?", work_root=tmp_path)
    calls.append(a["request_id"])
    b = service_mod.retrieve("Why was the example capability considered unmeasured?", work_root=tmp_path)
    assert a["request_id"] != b["request_id"]
    assert a["question_fingerprint"] == b["question_fingerprint"]
    assert (tmp_path / a["request_id"]).exists()
    assert (tmp_path / b["request_id"]).exists()
    assert "expected_record_ids" not in json.dumps(a)
    assert "required_citation_ids" not in json.dumps(a)


def test_query_reuses_shared_path_and_returns_grounded_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))
    monkeypatch.setattr(service_mod.reasoner_client, "run", _fake_reasoner_run)
    result = service_mod.query("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)
    assert result["status"] == "ok"
    assert result["question_fingerprint"] == service_mod.question_fingerprint("Why was the example capability considered unmeasured?")
    assert result["cited_record_ids"] == ["CLM-example-unmeasured"]
    assert result["evidence_used"] == ["CLM-example-unmeasured"]
    assert "runtime" in result


def test_evidence_only_does_not_call_reasoner(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))
    called = {"reasoner": False}

    def boom(*args, **kwargs):
        called["reasoner"] = True
        raise AssertionError("reasoner should not be called")

    monkeypatch.setattr(service_mod.reasoner_client, "run", boom)
    result = service_mod.retrieve("Why was the example capability considered unmeasured?", work_root=tmp_path)
    assert result["status"] == "ok"
    assert called["reasoner"] is False


def test_retrieve_and_query_share_pipeline_lock(tmp_path, monkeypatch):
    entries = []
    release = threading.Event()
    entered_first = threading.Event()
    entered_second = threading.Event()

    def blocking_frozen_retrieval_contract(question, rid, work_root):
        entries.append(("helper", rid))
        if len(entries) == 1:
            entered_first.set()
            release.wait(timeout=1)
        else:
            entered_second.set()
        return _fake_retrieve(question, work_root=work_root), Path(work_root) / rid

    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", blocking_frozen_retrieval_contract)
    monkeypatch.setattr(service_mod.reasoner_client, "run", _fake_reasoner_run)

    def run_retrieve():
        service_mod.retrieve("Why was the example capability considered unmeasured?", work_root=tmp_path)

    def run_query():
        service_mod.query("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)

    first = threading.Thread(target=run_retrieve)
    second = threading.Thread(target=run_query)
    first.start()
    assert entered_first.wait(timeout=1)
    second.start()
    time.sleep(0.1)
    assert len(entries) == 1
    assert not entered_second.is_set()
    release.set()
    first.join(timeout=1)
    assert entered_second.wait(timeout=1)
    second.join(timeout=1)
    assert len(entries) == 2


def test_query_reports_unavailable_endpoint_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))
    monkeypatch.setattr(service_mod.reasoner_client, "run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("endpoint unavailable")))
    result = service_mod.query("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)
    assert result["status"] == "failed"
    assert result["error_code"] == "reasoner_unavailable"
    assert "unavailable" in result["error"]
    assert "http://example" not in json.dumps(result)
    assert ".work" not in json.dumps(result)
    assert "Traceback" not in json.dumps(result)


def test_query_reports_transport_failure_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))
    monkeypatch.setattr(service_mod.reasoner_client, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("Operation not permitted")))
    result = service_mod.query("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)
    assert result["status"] == "failed"
    assert result["error_code"] == "reasoner_unavailable"
    assert "transport failed" in result["error"]


def test_query_without_reasoner_endpoint_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))
    result = service_mod.query("Why was the example capability considered unmeasured?", endpoint=None, work_root=tmp_path)
    assert result["status"] == "failed"
    assert result["error_code"] == "reasoner_unavailable"
    assert "HISTORIAN_REASONER_ENDPOINT is required" in result["error"]


def test_query_reports_malformed_reasoner_output_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))

    def fake_reasoner_run(query_id, run_dir, endpoint, retrieval_results, max_tokens=1536):
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"{query_id}.transaction.json").write_text(json.dumps({"query_id": query_id, "state": "COMPLETE", "transport_status": "http_200"}) + "\n")
        (run_dir / f"{query_id}.result.json").write_text(json.dumps({"query_id": query_id, "parsed_response": {"answer": 1}, "validation": {"schema_valid": {"valid": False}, "grounding_valid": {"valid": False}, "contract_valid": False}}) + "\n")

    monkeypatch.setattr(service_mod.reasoner_client, "run", fake_reasoner_run)
    result = service_mod.query("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)
    assert result["status"] == "failed"
    assert result["error_code"] == "reasoner_invalid_response"
    assert result["validation"]["contract_valid"] is False


def test_http_service_health_and_query(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", lambda question, rid, work_root: (_fake_retrieve(question, work_root=work_root), Path(work_root) / rid))
    monkeypatch.setattr(service_mod.reasoner_client, "run", _fake_reasoner_run)
    server = service_mod.serve(host="127.0.0.1", port=0, work_root=tmp_path, endpoint="http://example")
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health") as resp:
            health = json.loads(resp.read().decode("utf-8"))
        assert health["status"] == "ok"
        assert health["api_version"] == "v1"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/evidence",
            data=json.dumps({"question": "Why was the example capability considered unmeasured?"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            evidence = json.loads(resp.read().decode("utf-8"))
        assert evidence["status"] == "ok"
        assert evidence["api_version"] == "v1"
        assert evidence["selected_record_ids"] == ["CLM-example-unmeasured"]
        assert "query_path" not in json.dumps(evidence)
        assert "retrieval_path" not in json.dumps(evidence)
        assert "reasoner_dir" not in json.dumps(evidence)
        assert "base_dir" not in json.dumps(evidence)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/query",
            data=json.dumps({"question": "Why was the example capability considered unmeasured?"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            queried = json.loads(resp.read().decode("utf-8"))
        assert queried["status"] == "ok"
        assert queried["api_version"] == "v1"
        assert queried["cited_record_ids"] == ["CLM-example-unmeasured"]
        assert "runtime" not in queried
        assert "request_dir" not in json.dumps(queried)

        failing = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/query",
            data=json.dumps({"question": "Why was the example capability considered unmeasured?"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        monkeypatch.setattr(service_mod, "query", lambda *args, **kwargs: {"request_id": "op-1", "question": "q", "question_fingerprint": "fp", "status": "failed", "error_code": "reasoner_unavailable", "error": "Historian reasoner endpoint is unavailable", "runtime": {"request_dir": "/tmp/x"}})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(failing)
        assert exc.value.code == 502
    finally:
        server.shutdown()
        server.server_close()


def test_http_health_remains_responsive_while_pipeline_lock_held(tmp_path, monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def blocking_frozen_retrieval_contract(question, rid, work_root):
        entered.set()
        release.wait(timeout=1)
        return _fake_retrieve(question, work_root=work_root), Path(work_root) / rid

    monkeypatch.setattr(service_mod, "_frozen_retrieval_contract", blocking_frozen_retrieval_contract)
    monkeypatch.setattr(service_mod.reasoner_client, "run", _fake_reasoner_run)
    server = service_mod.serve(host="127.0.0.1", port=0, work_root=tmp_path, endpoint="http://example")
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    worker = threading.Thread(target=lambda: service_mod.retrieve("Why was the example capability considered unmeasured?", work_root=tmp_path))
    worker.start()
    assert entered.wait(timeout=1)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health") as resp:
            health = json.loads(resp.read().decode("utf-8"))
        assert health["status"] == "ok"
        assert health["api_version"] == "v1"
    finally:
        release.set()
        worker.join(timeout=1)
        server.shutdown()
        server.server_close()


def test_default_localhost_binding_and_nonloopback_protection():
    server = service_mod.serve(host="127.0.0.1", port=0, work_root=Path(".work") / "historian_queries", endpoint="http://example")
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
    server = service_mod.serve(host="localhost", port=0, work_root=Path(".work") / "historian_queries", endpoint="http://example")
    try:
        assert server.server_address[0] in {"127.0.0.1", "::1"}
    finally:
        server.server_close()
    with pytest.raises(SystemExit):
        service_mod.serve(host="0.0.0.0", port=0, work_root=Path(".work") / "historian_queries", endpoint="http://example")
    with pytest.raises(SystemExit):
        service_mod.serve(host="192.0.2.16", port=0, work_root=Path(".work") / "historian_queries", endpoint="http://example")


def test_cli_ask_uses_shared_service(monkeypatch, capsys):
    captured = {}

    def fake_service_ask(question, *, endpoint=None, work_root=None, max_tokens=1536):
        captured["question"] = question
        captured["endpoint"] = endpoint
        captured["work_root"] = work_root
        return {
            "status": "ok",
            "answer": "ok",
            "cited_record_ids": ["CLM-example-unmeasured"],
            "evidence_used": ["CLM-example-unmeasured"],
            "uncertainty_or_limitations": "",
            "contradictions_or_missing_evidence": [],
            "validation": {"schema_valid": {"valid": True}, "grounding_valid": {"valid": True}, "contract_valid": True},
        }

    monkeypatch.setattr(cli_mod, "historian_ask", fake_service_ask)
    rc = cli_mod.main(["ask", "Why was the example capability considered unmeasured?", "--endpoint", "http://example"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "ok"
    assert "cited_record_ids: CLM-example-unmeasured" in out
    assert "validation" not in out
    assert captured["question"] == "Why was the example capability considered unmeasured?"
    assert captured["endpoint"] == "http://example"


def test_cli_ask_failure_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_mod,
        "historian_ask",
        lambda *args, **kwargs: {"status": "failed", "request_id": "op-1", "error": "endpoint unavailable"},
    )
    rc = cli_mod.main(["ask", "Why was the example capability considered unmeasured?", "--endpoint", "http://example"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "endpoint unavailable" in out
    assert "request_id" in out
