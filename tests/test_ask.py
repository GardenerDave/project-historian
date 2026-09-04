from __future__ import annotations

import json
from pathlib import Path

import pytest

import historian.ask as ask_mod


def test_operational_ask_reuses_accepted_components_and_writes_only_runtime_artifacts(tmp_path, monkeypatch):
    captured = {}

    def fake_service_ask(question, *, endpoint=None, work_root=None, max_tokens=1536):
        captured["question"] = question
        captured["endpoint"] = endpoint
        captured["work_root"] = work_root
        return {
            "request_id": "op-1",
            "question": question,
            "question_fingerprint": "fp",
            "status": "ok",
            "selected_record_ids": ["CLM-example-unmeasured"],
            "answer": "The example capability was left unmeasured because transport failed.",
            "cited_record_ids": ["CLM-example-unmeasured"],
            "evidence_used": ["CLM-example-unmeasured"],
            "uncertainty_or_limitations": "",
            "contradictions_or_missing_evidence": [],
            "validation": {"schema_valid": {"valid": True}, "grounding_valid": {"valid": True}, "contract_valid": True},
        }

    monkeypatch.setattr(ask_mod, "service_ask", fake_service_ask)
    result = ask_mod.ask("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)
    assert result["status"] == "ok"
    assert result["cited_record_ids"] == ["CLM-example-unmeasured"]
    assert result["answer"] == "The example capability was left unmeasured because transport failed."
    assert captured["question"] == "Why was the example capability considered unmeasured?"
    assert captured["endpoint"] == "http://example"
    assert captured["work_root"] == tmp_path
    assert not Path("interfaces/reasoner/evaluations").joinpath("operational").exists()


def test_operational_ask_reports_endpoint_failure_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ask_mod,
        "service_ask",
        lambda *args, **kwargs: {"request_id": "op-1", "status": "failed", "error": "endpoint unavailable"},
    )
    result = ask_mod.ask("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)
    assert result["status"] == "failed"
    assert "endpoint unavailable" in result["error"]


def test_operational_ask_surfaces_malformed_reasoner_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ask_mod,
        "service_ask",
        lambda *args, **kwargs: {
            "status": "failed",
            "request_id": "op-1",
            "validation": {"schema_valid": {"valid": False}, "grounding_valid": {"valid": False}, "contract_valid": False},
        },
    )
    result = ask_mod.ask("Why was the example capability considered unmeasured?", endpoint="http://example", work_root=tmp_path)
    assert result["status"] == "failed"
    assert result["validation"]["contract_valid"] is False
