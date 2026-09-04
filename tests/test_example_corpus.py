"""Example-corpus tests.

These tests validate the synthetic example corpus that ships with Project
Historian. They run unchanged in both places the corpus can live:

- in a source checkout, where the corpus is under examples/corpus/;
- in a public distribution tree, where the same corpus is installed at the
  root as records/ and sources/.

All assertions are about the synthetic corpus itself (never about any real
evidence corpus), so the module is safe to distribute.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = ROOT / "examples" / "corpus"
CORPUS_ROOT = EXAMPLE_ROOT if (EXAMPLE_ROOT / "records").is_dir() else ROOT

sys.path.insert(0, str(ROOT))

from historian.cli import CLASSES, ID_RE, KINDS, SHA_RE, STATUSES, frontmatter, stable_id  # noqa: E402


def corpus_records():
    return sorted((CORPUS_ROOT / "records").rglob("*.md"))


def corpus_manifest():
    return json.loads((CORPUS_ROOT / "sources" / "manifest.json").read_text(encoding="utf-8"))


class ExampleCorpusTests(unittest.TestCase):
    def test_corpus_is_present_and_nontrivial(self):
        records = corpus_records()
        self.assertGreaterEqual(len(records), 10)
        kinds = {p.parent.name for p in records}
        self.assertLessEqual({"event", "failure", "decision", "hypothesis", "claim", "milestone", "artifact", "evidence", "revision"}, kinds)

    def test_records_have_valid_structure_ids_and_vocabularies(self):
        seen, fingerprints = set(), set()
        for p in corpus_records():
            data, _ = frontmatter(p)
            for key in ("id", "kind", "title", "assertion_class", "status", "source_ids", "evidence", "relationships", "ingestion"):
                self.assertIn(key, data, f"{p.name}: missing {key}")
            self.assertIn(data["kind"], KINDS, p.name)
            self.assertIn(data["assertion_class"], CLASSES, p.name)
            self.assertIn(data["status"], STATUSES, p.name)
            self.assertTrue(ID_RE.match(data["id"]), p.name)
            self.assertNotIn(data["id"], seen, p.name)
            seen.add(data["id"])
            fingerprint = re.search(r"source_fingerprint:\s*([^,}]+)", data["ingestion"])
            self.assertIsNotNone(fingerprint, p.name)
            self.assertNotIn(fingerprint.group(1).strip(), fingerprints, p.name)
            fingerprints.add(fingerprint.group(1).strip())
        self.assertIn("superseded", {frontmatter(p)[0]["status"] for p in corpus_records()})
        self.assertIn("candidate", {frontmatter(p)[0]["status"] for p in corpus_records()})

    def test_source_and_evidence_references_resolve(self):
        known = {entry["source_id"] for entry in corpus_manifest()["sources"]}
        for p in corpus_records():
            data, _ = frontmatter(p)
            for sid in re.findall(r"SRC-[A-Z0-9-]+", data["source_ids"]):
                self.assertIn(sid, known, f"{p.name}: unknown source_id {sid}")
            for sid in re.findall(r"source_id:\s*(SRC-[A-Z0-9-]+)", data["evidence"]):
                self.assertIn(sid, known, f"{p.name}: unknown evidence source_id {sid}")

    def test_relationship_targets_resolve_within_corpus_or_sources(self):
        known = {frontmatter(p)[0]["id"] for p in corpus_records()}
        known |= {entry["source_id"] for entry in corpus_manifest()["sources"]}
        for p in corpus_records():
            data, _ = frontmatter(p)
            for target in re.findall(r"target:\s*([^,}]+)", data["relationships"]):
                self.assertIn(target.strip(), known, f"{p.name}: unresolved relationship target {target}")

    def test_evidence_hashes_are_wellformed_and_verifiable(self):
        manifest = corpus_manifest()
        by_id = {entry["source_id"]: entry for entry in manifest["sources"]}
        for p in corpus_records():
            data, _ = frontmatter(p)
            hashes = re.findall(r"sha256:\s*([^,}\]]+)", data["evidence"])
            self.assertTrue(hashes, p.name)
            for value in hashes:
                value = value.strip()
                if value == "null":
                    self.assertIn("hash_status:", data["evidence"], p.name)
                else:
                    self.assertTrue(SHA_RE.match(value), f"{p.name}: bad sha256 {value}")
            for match in re.finditer(r"source_id:\s*(SRC-[A-Z0-9-]+),\s*locator:\s*([^,]+),\s*sha256:\s*([0-9a-f]{64})", data["evidence"]):
                sid, locator, sha = match.groups()
                retained = CORPUS_ROOT / locator.split("#")[0]
                self.assertTrue(retained.is_file(), f"{p.name}: missing retained source {locator}")
                self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), sha, f"{p.name}: {sid} hash mismatch")
                self.assertEqual(by_id[sid]["sha256"], sha, f"{p.name}: {sid} disagrees with manifest")

    def test_manifest_tracks_all_retained_example_sources(self):
        manifest = corpus_manifest()
        self.assertEqual(manifest["schema"], "project-historian-v1-source-manifest")
        retained = {p.name for p in (CORPUS_ROOT / "sources").iterdir() if p.name != "manifest.json"}
        tracked = {Path(entry["original_path"]).name for entry in manifest["sources"] if entry["sha256"]}
        self.assertEqual(retained, tracked)

    def test_deterministic_substring_search_over_corpus(self):
        def search(term):
            term = term.lower()
            return [p for p in corpus_records() if term in p.read_text(encoding="utf-8").lower()]
        self.assertIn(CORPUS_ROOT / "records" / "milestone" / "MIL-search-endpoint-stable.md", search("milestone"))
        self.assertIn(CORPUS_ROOT / "records" / "hypothesis" / "HYP-timezone-handling-flake-cause.md", search("superseded"))
        self.assertTrue(search("synthetic"))

    def test_stable_ids_are_deterministic(self):
        self.assertEqual(stable_id("event", "example-key"), stable_id("event", "example-key"))
        self.assertNotEqual(stable_id("event", "example-key"), stable_id("event", "other-key"))

    def test_cli_starts_and_validates_the_installed_corpus(self):
        env_root = str(ROOT)
        for args in (["--help"], ["validate"]):
            result = subprocess.run(
                [sys.executable, "-m", "historian.cli", *args],
                cwd=env_root, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(
            [sys.executable, "-m", "historian.cli", "validate"],
            cwd=env_root, capture_output=True, text=True, timeout=120,
        )
        self.assertRegex(result.stdout, r"valid records: \d+")
        self.assertIn("search", subprocess.run(
            [sys.executable, "-m", "historian.cli", "--help"],
            cwd=env_root, capture_output=True, text=True, timeout=120,
        ).stdout)

    def test_cli_search_runs_over_installed_records(self):
        result = subprocess.run(
            [sys.executable, "-m", "historian.cli", "search", "milestone"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip())
        for line in result.stdout.splitlines():
            self.assertTrue(line.startswith("records/"), line)


if __name__ == "__main__":
    unittest.main()
