from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "historian_evidence": ROOT / "integrations" / "anythingllm" / "skills" / "historian_evidence",
    "historian_query": ROOT / "integrations" / "anythingllm" / "skills" / "historian_query",
}
PROJECT_LICENSE = "PolyForm Noncommercial License 1.0.0"
OSI_CLAIM_RE = re.compile(r"\bosi\b", re.IGNORECASE)


def run_sync_script(target_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "sync_anythingllm_skills.sh"), str(target_root)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def require_bash():
    if shutil.which("bash") is None:
        import pytest

        pytest.skip("bash is required to exercise the sync script")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_anythingllm_skill_manifests_are_project_controlled():
    for skill_name, skill_dir in SKILLS.items():
        manifest = _load_json(skill_dir / "plugin.json")
        handler = (skill_dir / "handler.js").read_text(encoding="utf-8")

        assert manifest["active"] is True
        assert manifest["hubId"] == skill_name
        assert manifest["entrypoint"]["file"] == "handler.js"
        assert "question" in manifest["entrypoint"]["params"]

        assert "http://127.0.0.1:8765/v1" in handler
        assert "expected_record_ids" not in handler
        assert "required_citation_ids" not in handler
        assert "answer_mode" not in handler
        assert "forbidden_misconception" not in handler
        assert "stable Historian record IDs" in manifest["description"]
        if skill_name == "historian_query":
            assert "final assistant response" in manifest["description"]
        else:
            assert "final assistant response" not in manifest["description"]


def test_plugin_manifests_declare_the_project_license():
    for skill_name, skill_dir in SKILLS.items():
        manifest = _load_json(skill_dir / "plugin.json")
        assert manifest["license"] != "MIT"
        assert manifest["license"] == PROJECT_LICENSE


def test_plugin_metadata_is_consistent_with_project_licensing_artifacts():
    license_text = (ROOT / "LICENSE.md").read_text(encoding="utf-8")
    assert "PolyForm Noncommercial License 1.0.0" in license_text
    commercial_text = (ROOT / "COMMERCIAL_USE.md").read_text(encoding="utf-8")
    assert "PolyForm Noncommercial" in commercial_text
    assert (ROOT / "COMMERCIAL_USE.md").is_file()


def test_exported_docs_do_not_describe_the_project_as_open_source():
    license_path = ROOT / "LICENSE.md"
    commercial_path = ROOT / "COMMERCIAL_USE.md"
    assert license_path.is_file()
    assert commercial_path.is_file()
    doc_paths = [license_path, commercial_path] + sorted((ROOT / "docs").glob("*.md"))
    assert doc_paths, "no documentation files found to scan"
    for path in doc_paths:
        text = path.read_text(encoding="utf-8").lower()
        assert "open source" not in text, f"{path.name}: describes the project as open source"
        assert not OSI_CLAIM_RE.search(text), f"{path.name}: references OSI open-source licensing"


def test_anythingllm_sync_script_is_present():
    script = ROOT / "scripts" / "sync_anythingllm_skills.sh"
    assert script.exists()
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert "storage/plugins/agent-skills" in script.read_text(encoding="utf-8")


def test_anythingllm_sync_script_contains_no_deletion_commands():
    script_text = (ROOT / "scripts" / "sync_anythingllm_skills.sh").read_text(encoding="utf-8")
    assert script_text.count("rm ") == 0
    assert "rmtree" not in script_text
    assert "unlink" not in script_text
    assert "-delete" not in script_text


def test_sync_script_refuses_unrelated_destination_and_preserves_it():
    require_bash()
    with tempfile.TemporaryDirectory(prefix="anythingllm-sync-") as tmp:
        target = Path(tmp) / "agent-skills"
        target.mkdir()
        foreign = target / "historian_evidence"
        foreign.mkdir()
        (foreign / "unrelated.txt").write_text("operator content", encoding="utf-8")
        result = run_sync_script(target)
        assert result.returncode != 0
        assert "refusing to sync" in result.stderr
        assert (foreign / "unrelated.txt").read_text(encoding="utf-8") == "operator content"
        assert not (target / "historian_query").exists(), "refusal must happen before any installation writes"


def test_sync_script_replaces_only_owned_skill_files_on_resync():
    require_bash()
    with tempfile.TemporaryDirectory(prefix="anythingllm-sync-") as tmp:
        target = Path(tmp) / "agent-skills"
        first = run_sync_script(target)
        assert first.returncode == 0, first.stderr
        for skill_name, skill_dir in SKILLS.items():
            dst = target / skill_name
            assert (dst / "handler.js").read_bytes() == (skill_dir / "handler.js").read_bytes()
            assert (dst / "plugin.json").read_bytes() == (skill_dir / "plugin.json").read_bytes()
        operator_note = target / "historian_evidence" / "operator-notes.txt"
        operator_note.write_text("operator customization", encoding="utf-8")
        unrelated = target / "other-skill"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("unrelated skill", encoding="utf-8")
        (target / "historian_evidence" / "handler.js").write_text("tampered", encoding="utf-8")
        second = run_sync_script(target)
        assert second.returncode == 0, second.stderr
        evidence_dst = target / "historian_evidence"
        assert (evidence_dst / "handler.js").read_bytes() == (SKILLS["historian_evidence"] / "handler.js").read_bytes()
        assert (evidence_dst / "plugin.json").read_bytes() == (SKILLS["historian_evidence"] / "plugin.json").read_bytes()
        assert operator_note.read_text(encoding="utf-8") == "operator customization"
        assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "unrelated skill"


def test_sync_script_refuses_file_destination():
    require_bash()
    with tempfile.TemporaryDirectory(prefix="anythingllm-sync-") as tmp:
        target = Path(tmp) / "agent-skills"
        target.mkdir()
        (target / "historian_evidence").write_text("operator file", encoding="utf-8")
        result = run_sync_script(target)
        assert result.returncode != 0
        assert "refusing to sync" in result.stderr
        assert (target / "historian_evidence").read_text(encoding="utf-8") == "operator file"


def test_anythingllm_workspace_prompt_requires_citations_and_finalization():
    prompt = (ROOT / "integrations" / "anythingllm" / "workspace_prompt.md").read_text(encoding="utf-8")
    assert "Historian is the authoritative, read-only source of project memory." in prompt
    assert "cite the stable Historian record IDs" in prompt
    assert "continue after the tool result" in prompt
    assert "Do not stop at tool-call syntax." in prompt
