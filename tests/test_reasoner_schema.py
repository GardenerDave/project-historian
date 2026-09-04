from __future__ import annotations

import json

import pytest

from historian.grounded_schema import SCHEMA, schema_for_record_ids, validate_schema
from historian.reasoner_client import MODEL


def test_schema_is_strict_and_client_payload_contract():
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == {
        "answer",
        "cited_record_ids",
        "evidence_used",
        "uncertainty_or_limitations",
        "contradictions_or_missing_evidence",
    }
    assert MODEL == "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf"


def test_dynamic_schema_uses_only_supplied_ids_and_stays_deterministic():
    schema = schema_for_record_ids(["B", "A"])
    assert schema["properties"]["cited_record_ids"]["items"]["enum"] == ["A", "B"]
    assert schema["properties"]["evidence_used"]["items"]["enum"] == ["A", "B"]
    assert schema["properties"]["cited_record_ids"]["items"]["enum"] == schema["properties"]["evidence_used"]["items"]["enum"]
    assert "C" not in schema["properties"]["evidence_used"]["items"]["enum"]


def test_dynamic_schema_allows_empty_arrays_and_leaves_answer_unconstrained():
    schema = schema_for_record_ids(["A"])
    assert schema["properties"]["answer"]["type"] == "string"
    assert schema["properties"]["cited_record_ids"]["items"]["enum"] == ["A"]
    assert schema["properties"]["evidence_used"]["items"]["enum"] == ["A"]
    assert validate_schema(
        {
            "answer": "insufficient evidence",
            "cited_record_ids": [],
            "evidence_used": [],
            "uncertainty_or_limitations": "limited evidence",
            "contradictions_or_missing_evidence": [],
        }
    )["valid"]


def test_two_distinct_evidence_packets_produce_two_distinct_allowed_id_schemas():
    schema_a = schema_for_record_ids(["A", "B"])
    schema_b = schema_for_record_ids(["C", "D"])
    assert schema_a["properties"]["cited_record_ids"]["items"]["enum"] == ["A", "B"]
    assert schema_b["properties"]["cited_record_ids"]["items"]["enum"] == ["C", "D"]
    assert schema_a["properties"]["evidence_used"]["items"]["enum"] != schema_b["properties"]["evidence_used"]["items"]["enum"]


def test_dynamic_schema_rejects_duplicate_supplied_ids():
    with pytest.raises(ValueError):
        schema_for_record_ids(["A", "A"])
