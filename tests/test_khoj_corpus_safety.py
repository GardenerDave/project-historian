"""Deletion-safety tests for the exported Khoj corpus builder.

Contract under test: the builder may write only into a destination that
does not yet exist. Any pre-existing destination — a directory (even one
that looks exactly like a Historian-generated corpus), a file, a symlink
(including a broken one), or any other filesystem object — is refused
with an error before anything is written and is left byte-for-byte
untouched. There is no in-place rebuild: deterministic output is proven
by building the same input into two fresh destinations and comparing
bytes.

The tests are context-agnostic: they pass against the repository's real
records/ tree and against the synthetic public example corpus mapped to
records/ in the exported candidate.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from historian.interfaces.build_khoj_corpus import CORPUS_MARKER, build, validate_projection


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class KhojCorpusOutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="khoj-corpus-safety-")
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_build_creates_generated_corpus_with_marker(self):
        out = self.base / "corpus"
        record_count = len(list((ROOT / "records").rglob("*.md")))
        self.assertEqual(build(out), record_count)
        self.assertEqual(validate_projection(ROOT / "records", out), record_count)
        self.assertTrue((out / "CORPUS.md").read_text(encoding="utf-8").startswith(CORPUS_MARKER))

    def test_two_independent_fresh_builds_are_byte_identical(self):
        first = self.base / "corpus-first"
        second = self.base / "corpus-second"
        self.assertEqual(build(first), build(second))
        self.assertEqual(_tree_hashes(first), _tree_hashes(second))

    def test_second_build_to_existing_destination_fails_closed_and_preserves_it(self):
        out = self.base / "corpus"
        count = build(out)
        before = _tree_hashes(out)
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            build(out)
        self.assertEqual(_tree_hashes(out), before)
        self.assertEqual(len(list((out / "records").glob("*.md"))), count)

    def test_existing_directory_with_valid_corpus_marker_is_still_refused(self):
        built = self.base / "built-corpus"
        build(built)
        lookalike = self.base / "moved-corpus"
        lookalike.mkdir()
        (lookalike / "CORPUS.md").write_text((built / "CORPUS.md").read_text(encoding="utf-8"), encoding="utf-8")
        (lookalike / "records").mkdir()
        for path in (built / "records").glob("*.md"):
            (lookalike / "records" / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        before = _tree_hashes(lookalike)
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            build(lookalike)
        self.assertEqual(_tree_hashes(lookalike), before)

    def test_existing_ordinary_directory_is_refused_and_unchanged(self):
        out = self.base / "corpus"
        out.mkdir()
        (out / "operator-notes.txt").write_text("operator content", encoding="utf-8")
        nested = out / "nested"
        nested.mkdir()
        (nested / "keep.md").write_text("keep me", encoding="utf-8")
        before = _tree_hashes(out)
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            build(out)
        self.assertEqual(_tree_hashes(out), before)
        self.assertEqual((out / "operator-notes.txt").read_text(encoding="utf-8"), "operator content")
        self.assertEqual((nested / "keep.md").read_text(encoding="utf-8"), "keep me")

    def test_existing_file_destination_is_refused_and_unchanged(self):
        out = self.base / "corpus"
        out.write_text("operator file", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            build(out)
        self.assertEqual(out.read_text(encoding="utf-8"), "operator file")
        self.assertTrue(out.is_file())

    def test_existing_symlink_destination_is_refused_and_target_untouched(self):
        real = self.base / "real-target"
        real.mkdir()
        (real / "operator.txt").write_text("operator content", encoding="utf-8")
        out = self.base / "corpus"
        out.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            build(out)
        self.assertTrue(out.is_symlink())
        self.assertEqual((real / "operator.txt").read_text(encoding="utf-8"), "operator content")

    def test_existing_broken_symlink_destination_is_refused(self):
        out = self.base / "corpus"
        out.symlink_to(self.base / "does-not-exist")
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            build(out)
        self.assertTrue(out.is_symlink())


if __name__ == "__main__":
    unittest.main()
