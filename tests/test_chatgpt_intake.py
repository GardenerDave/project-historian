from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
import unittest
import tempfile
import zipfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from historian.chatgpt_intake import intake_boundary_ok, inventory_export, preflight, prepare_zip_conversation_source, raw_export_protected, resolve_conversation_source, sanitize_text


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict | list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def synthetic_export(tmp_path: Path) -> Path:
    payload = {
        "conversations": [
            {
                "id": "conv_ordinary",
                "title": "Example project planning",
                "create_time": 100,
                "update_time": 120,
                "mapping": {
                    "root": {
                        "id": "msg_root",
                        "parent": None,
                        "message": {
                            "id": "msg_root",
                            "author": {"role": "user"},
                            "create_time": 100,
                            "content": {"parts": ["Discuss prompt patch sequencing with Historian and the example project."]},
                        },
                    },
                    "branch": {
                        "id": "msg_branch",
                        "parent": "root",
                        "message": {
                            "id": "msg_branch",
                            "author": {"role": "assistant"},
                            "create_time": 110,
                            "content": {"parts": ["Use provenance and router boundaries."]},
                        },
                    },
                    "branch_two": {
                        "id": "msg_branch_two",
                        "parent": "root",
                        "message": {
                            "id": "msg_branch_two",
                            "author": {"role": "assistant"},
                            "create_time": 115,
                            "content": {"parts": ["A second synthetic branch."]},
                        },
                    },
                },
            },
            {
                "id": "conv_secret",
                "title": "Billing question",
                "messages": [
                    {
                        "id": "m1",
                        "parent_id": None,
                        "role": "user",
                        "create_time": 200,
                        "content": "My email is alice@example.com and my token is sk-test-1234567890abcdef.",
                    }
                ],
            },
            {
                "id": "conv_personal",
                "messages": [
                    {
                        "id": "p1",
                        "parent_id": None,
                        "role": "user",
                        "create_time": 300,
                        "content": "Call me at (212) 555-0100 and send the summary to 10 Main Street.",
                    }
                ],
            },
            {
                "id": "conv_structured",
                "messages": [
                    {
                        "id": "s1",
                        "parent_id": None,
                        "role": "user",
                        "create_time": 400,
                        "content": {
                            "parts": [
                                "Structured export part",
                                {"type": "image", "url": "https://example.invalid/image.png"},
                                {"kind": "metadata", "value": {"nested": True}},
                            ]
                        },
                    }
                ],
            },
        ]
    }
    return write_json(tmp_path / "conversations.json", payload)


def malformed_export(tmp_path: Path) -> Path:
    return write_json(
        tmp_path / "malformed.json",
        {
            "conversations": [
                {
                    "id": "good-1",
                    "messages": [
                        {
                            "id": "g1",
                            "parent_id": None,
                            "role": "user",
                            "create_time": 1,
                            "content": "valid one",
                        }
                    ],
                },
                {
                    "id": "bad",
                    "messages": [{}],
                },
                {
                    "id": "good-2",
                    "messages": [
                        {
                            "id": "g2",
                            "parent_id": None,
                            "role": "assistant",
                            "create_time": 2,
                            "content": "valid two",
                        }
                    ],
                },
            ]
        },
    )


def sharded_export_dir(tmp_path: Path, *, duplicate: bool = False) -> Path:
    export_dir = tmp_path / "shards"
    export_dir.mkdir()
    write_json(
        export_dir / "conversations-001.json",
        [
            {
                "id": "conv_b",
                "messages": [
                    {"id": "b1", "parent_id": None, "role": "user", "create_time": 2, "content": "second shard"}
                ],
            }
        ],
    )
    write_json(
        export_dir / "conversations-000.json",
        [
            {
                "id": "conv_a",
                "messages": [
                    {"id": "a1", "parent_id": None, "role": "user", "create_time": 1, "content": "first shard"}
                ],
            }
        ],
    )
    if duplicate:
        write_json(
            export_dir / "conversations-002.json",
            [
                {
                    "id": "conv_a",
                    "messages": [
                        {"id": "a2", "parent_id": None, "role": "assistant", "create_time": 3, "content": "duplicate shard"}
                    ],
                }
            ],
        )
    else:
        write_json(
            export_dir / "conversations-002.json",
            [
                {
                    "id": "conv_c",
                    "messages": [
                        {"id": "c1", "parent_id": None, "role": "assistant", "create_time": 3, "content": "third shard"}
                    ],
                }
            ],
        )
    return export_dir


def sharded_export_zip(tmp_path: Path, *, unsafe_member: bool = False) -> Path:
    export_dir = sharded_export_dir(tmp_path)
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for shard in sorted(export_dir.glob("conversations-*.json")):
            archive.write(shard, arcname=shard.name)
        archive.writestr("chat.html", "<html></html>")
        if unsafe_member:
            archive.writestr("../conversations-999.json", "[]")
    return zip_path


def deep_chain_export(tmp_path: Path, depth: int = 1500) -> Path:
    messages = []
    for index in range(depth):
        messages.append(
            {
                "id": f"d{index}",
                "parent_id": None if index == 0 else f"d{index - 1}",
                "role": "user" if index % 2 == 0 else "assistant",
                "create_time": index,
                "content": f"depth node {index}",
            }
        )
    return write_json(tmp_path / "deep.json", {"conversations": [{"id": "deep", "messages": messages}]})


def rootless_cycle_export(tmp_path: Path) -> Path:
    return write_json(
        tmp_path / "cycle.json",
        {
            "conversations": [
                {
                    "id": "cycle",
                    "mapping": {
                        "a": {
                            "id": "a",
                            "parent": "c",
                            "message": {
                                "id": "a",
                                "author": {"role": "user"},
                                "create_time": 1,
                                "content": "cycle a",
                            },
                        },
                        "b": {
                            "id": "b",
                            "parent": "a",
                            "message": {
                                "id": "b",
                                "author": {"role": "assistant"},
                                "create_time": 2,
                                "content": "cycle b",
                            },
                        },
                        "c": {
                            "id": "c",
                            "parent": "b",
                            "message": {
                                "id": "c",
                                "author": {"role": "assistant"},
                                "create_time": 3,
                                "content": "cycle c",
                            },
                        },
                    },
                },
                {
                    "id": "valid",
                    "messages": [
                        {
                            "id": "v1",
                            "parent_id": None,
                            "role": "user",
                            "create_time": 10,
                            "content": "valid after cycle",
                        }
                    ],
                },
            ]
        },
    )


class ChatGPTIntakeTests(unittest.TestCase):
    def test_sanitize_text_redacts_synthetic_secrets(self):
        sanitized, findings = sanitize_text("email alice@example.com token sk-test-1234567890abcdef")
        self.assertIn("[EMAIL_REDACTED]", sanitized)
        self.assertIn("[SECRET_REDACTED]", sanitized)
        self.assertTrue(all("alice@example.com" not in repr(item) for item in findings))

    def test_inventory_export_writes_private_and_staging_outputs(self):
        with mock.patch("historian.chatgpt_intake.secrets.token_bytes", return_value=b"x" * 32):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                export_path = synthetic_export(tmp_path)
                work_root = tmp_path / "intake"
                result = inventory_export(export_path, work_root=work_root)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["conversations_total"], 4)
                self.assertEqual(result["conversations_inventory"], 4)
                private_path = work_root / "private" / "provenance.json"
                staging_path = work_root / "staging" / "inventory.json"
                self.assertTrue(private_path.exists())
                self.assertTrue(staging_path.exists())
                public = json.loads(staging_path.read_text(encoding="utf-8"))
                self.assertTrue(all("opaque_source_id" in item for item in public["items"]))
                self.assertTrue(any(item["privacy_risk"] == "quarantined" for item in public["items"]))
                structured = next(item for item in public["items"] if item["opaque_source_id"])
                self.assertIn("branch_count", structured)
                self.assertIn("text_part_count", structured)
                private = json.loads(private_path.read_text(encoding="utf-8"))
                self.assertTrue(any(record["conversation_id"] == "conv_secret" for record in private["items"]))
                self.assertTrue(any(record.get("branch_structure", {}).get("branch_count", 0) > 0 for record in private["items"]))
                self.assertTrue(all("sk-test-1234567890abcdef" not in json.dumps(record) for record in private["items"]))
                self.assertEqual(private_path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("archive_sha256", json.dumps(public))

    def test_conversation_messages_handle_deep_valid_tree_without_recursion_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = deep_chain_export(tmp_path, depth=1500)
            conversations = json.loads(export_path.read_text(encoding="utf-8"))["conversations"]
            nodes = __import__("historian.chatgpt_intake", fromlist=["_conversation_messages"])._conversation_messages(conversations[0])
            self.assertEqual(len(nodes), 1500)
            self.assertEqual(nodes[0].message_id, "d0")
            self.assertEqual(nodes[-1].message_id, "d1499")

    def test_cycle_graph_is_quarantined_without_recursion_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = rootless_cycle_export(tmp_path)
            work_root = tmp_path / "intake"
            result = inventory_export(export_path, work_root=work_root)
            self.assertEqual(result["conversations_total"], 2)
            self.assertEqual(result["conversations_inventory"], 1)
            self.assertEqual(result["anomalies"], 1)
            public = json.loads((work_root / "staging" / "inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(len(public["anomalies"]), 1)
            self.assertIn("malformed_conversation", json.dumps(public["anomalies"]))

    def test_sharded_source_orders_numeric_suffixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = sharded_export_dir(tmp_path)
            resolved = resolve_conversation_source(source, work_root=tmp_path / "intake")
            self.assertEqual(resolved.kind, "sharded_json")
            self.assertEqual([path.name for path in resolved.shard_paths], [
                "conversations-000.json",
                "conversations-001.json",
                "conversations-002.json",
            ])

    def test_sharded_inventory_loads_one_shard_at_a_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = sharded_export_dir(tmp_path)
            work_root = tmp_path / "intake"
            loaded: list[str] = []

            original_conversation_messages = __import__("historian.chatgpt_intake", fromlist=["_conversation_messages"])._conversation_messages

            def tracking_load_json(path: Path):
                loaded.append(path.name)
                return json.loads(path.read_text(encoding="utf-8"))

            def tracking_conversation_messages(conversation: dict[str, object]):
                if conversation.get("id") == "conv_a":
                    self.assertEqual(loaded, ["conversations-000.json"])
                return original_conversation_messages(conversation)

            with mock.patch("historian.chatgpt_intake._load_json", side_effect=tracking_load_json):
                with mock.patch("historian.chatgpt_intake._conversation_messages", side_effect=tracking_conversation_messages):
                    result = inventory_export(source, work_root=work_root)

            self.assertEqual(result["conversation_shards"], 3)
            self.assertEqual(loaded, [
                "conversations-000.json",
                "conversations-001.json",
                "conversations-002.json",
            ])

    def test_sharded_inventory_counts_and_duplicate_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = sharded_export_dir(tmp_path, duplicate=True)
            work_root = tmp_path / "intake"
            result = inventory_export(source, work_root=work_root)
            self.assertEqual(result["conversation_shards"], 3)
            self.assertEqual(result["conversations_total"], 3)
            self.assertEqual(result["conversations_inventory"], 2)
            self.assertEqual(result["anomalies"], 1)
            public = json.loads((work_root / "staging" / "inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(len(public["anomalies"]), 1)
            self.assertIn("duplicate_conversation_id", json.dumps(public["anomalies"]))

    def test_malformed_shard_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "shards"
            source.mkdir()
            (source / "conversations-000.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                inventory_export(source, work_root=tmp_path / "intake")

    def test_zip_preparation_excludes_non_conversation_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = sharded_export_zip(tmp_path)
            work_root = tmp_path / "intake"
            resolved = prepare_zip_conversation_source(zip_path, work_root=work_root)
            self.assertEqual(resolved.kind, "sharded_json")
            self.assertEqual([path.name for path in resolved.shard_paths], [
                "conversations-000.json",
                "conversations-001.json",
                "conversations-002.json",
            ])
            extracted = sorted((work_root / "raw-extract").glob("**/*.json"))
            self.assertEqual([path.name for path in extracted], [
                "conversations-000.json",
                "conversations-001.json",
                "conversations-002.json",
            ])
            self.assertFalse((work_root / "raw-extract" / "chat.html").exists())
            self.assertTrue((work_root / "private" / "shard-manifest.json").exists())
            manifest = json.loads((work_root / "private" / "shard-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["discovered_shard_count"], 3)
            self.assertEqual(manifest["prepared_shard_count"], 3)
            self.assertEqual([item["name"] for item in manifest["shards"]], [
                "conversations-000.json",
                "conversations-001.json",
                "conversations-002.json",
            ])

    def test_zip_preparation_uses_requested_work_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = sharded_export_zip(tmp_path)
            work_root = tmp_path / "nested" / "custom-work"
            resolved = prepare_zip_conversation_source(zip_path, work_root=work_root)
            self.assertEqual(resolved.kind, "sharded_json")
            self.assertTrue((work_root / "raw-extract").exists())
            self.assertTrue(all(str(path).startswith(str(work_root / "raw-extract")) for path in resolved.shard_paths))

    def test_zip_unsafe_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = sharded_export_zip(tmp_path, unsafe_member=True)
            work_root = tmp_path / "intake"
            with self.assertRaisesRegex(ValueError, "unsafe archive member"):
                prepare_zip_conversation_source(zip_path, work_root=tmp_path / "intake")
            self.assertFalse(any((work_root / "raw-extract").glob("**/*.json")))
            self.assertFalse((work_root / "private" / "shard-manifest.json").exists())

    def test_zip_repeat_preparation_does_not_mix_stale_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = sharded_export_zip(tmp_path)
            work_root = tmp_path / "intake"
            first = prepare_zip_conversation_source(zip_path, work_root=work_root)
            first_root = first.shard_paths[0].parent
            stale_marker = first_root / "stale.marker"
            stale_marker.write_text("stale", encoding="utf-8")
            second = prepare_zip_conversation_source(zip_path, work_root=work_root)
            self.assertFalse(stale_marker.exists())
            self.assertEqual([path.name for path in first.shard_paths], [path.name for path in second.shard_paths])

    def test_zip_preparation_refuses_unexpected_subdirectory_without_deleting_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = sharded_export_zip(tmp_path)
            work_root = tmp_path / "intake"
            first = prepare_zip_conversation_source(zip_path, work_root=work_root)
            first_root = first.shard_paths[0].parent
            foreign = first_root / "foreign-dir"
            foreign.mkdir()
            (foreign / "operator.md").write_text("operator content", encoding="utf-8")
            stale = first_root / "stale.marker"
            stale.write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unexpected directories"):
                prepare_zip_conversation_source(zip_path, work_root=work_root)
            self.assertEqual((foreign / "operator.md").read_text(encoding="utf-8"), "operator content")
            self.assertTrue(stale.exists(), "refusal must happen before any cleanup deletion")

    def test_zip_preparation_clears_only_the_same_zip_extraction_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = sharded_export_zip(tmp_path)
            work_root = tmp_path / "intake"
            prepare_zip_conversation_source(zip_path, work_root=work_root)
            raw_extract = work_root / "raw-extract"
            sibling = raw_extract / f"other-{zip_path.name}-0000000000000000"
            sibling.mkdir()
            (sibling / "unrelated.json").write_text("unrelated", encoding="utf-8")
            private_marker = work_root / "private" / "shard-manifest.json"
            private_before = private_marker.read_bytes()
            prepare_zip_conversation_source(zip_path, work_root=work_root)
            self.assertEqual((sibling / "unrelated.json").read_text(encoding="utf-8"), "unrelated")
            self.assertFalse(any(raw_extract.glob(f"{zip_path.name}*/foreign*")))
            self.assertEqual(private_marker.read_bytes(), private_before)

    def test_zip_preparation_refuses_symlinked_extraction_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = sharded_export_zip(tmp_path)
            work_root = tmp_path / "intake"
            digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()[:16]
            raw_extract = work_root / "raw-extract"
            raw_extract.mkdir(parents=True)
            operator_dir = tmp_path / "operator-area"
            operator_dir.mkdir()
            (operator_dir / "operator.md").write_text("operator content", encoding="utf-8")
            extract_root = raw_extract / f"{zip_path.name}-{digest}"
            extract_root.symlink_to(operator_dir, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                prepare_zip_conversation_source(zip_path, work_root=work_root)
            self.assertEqual((operator_dir / "operator.md").read_text(encoding="utf-8"), "operator content")
            self.assertTrue(extract_root.is_symlink())

    def test_inventory_streams_archive_hash_once(self):
        with mock.patch("historian.chatgpt_intake.secrets.token_bytes", return_value=b"x" * 32):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                export_path = synthetic_export(tmp_path)
                work_root = tmp_path / "intake"
                calls = {"count": 0}

                original = Path.open

                def wrapped_open(self, *args, **kwargs):
                    if self == export_path and args and args[0] == "rb":
                        calls["count"] += 1
                    return original(self, *args, **kwargs)

                with mock.patch.object(Path, "open", wrapped_open):
                    inventory_export(export_path, work_root=work_root)

                self.assertEqual(calls["count"], 1)

    def test_malformed_input_fails_visibly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = malformed_export(tmp_path)
            work_root = tmp_path / "intake"
            result = inventory_export(export_path, work_root=work_root)
            self.assertEqual(result["conversations_total"], 3)
            self.assertEqual(result["conversations_inventory"], 2)
            self.assertEqual(result["anomalies"], 1)
            staging_path = work_root / "staging" / "inventory.json"
            public = json.loads(staging_path.read_text(encoding="utf-8"))
            self.assertEqual(len(public["anomalies"]), 1)
            anomalies = json.dumps(public["anomalies"])
            self.assertNotIn("malformed.json", anomalies)
            self.assertNotIn("alice@example.com", anomalies)
            self.assertNotIn("sk-test-1234567890abcdef", anomalies)

    def test_preflight_checks_git_ignore_and_export_presence(self):
        with tempfile.TemporaryDirectory() as export_tmp, tempfile.TemporaryDirectory(dir=ROOT) as repo_tmp:
            export_path = synthetic_export(Path(export_tmp))
            work_root = Path(repo_tmp) / ".work" / "chatgpt-intake"
            result = preflight(export_path, work_root=work_root)
            self.assertTrue(result["ok"])
            checks = {check["name"]: check["ok"] for check in result["checks"]}
            self.assertTrue(checks["export_exists"])
            self.assertTrue(checks["private_root_ignored"])
            self.assertTrue(checks["staging_root_ignored"])
            self.assertTrue(checks["cleared_root_ignored"])
            self.assertTrue(checks["key_path_ignored"])
            self.assertTrue(checks["private_provenance_ignored"])
            self.assertTrue(checks["inventory_path_ignored"])
            self.assertTrue(checks["raw_export_protected"])

    def test_preflight_cli_fails_closed_and_reports_failed_checks(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "historian.cli",
                    "chatgpt-intake",
                    "preflight",
                    str(export_path),
                    "--work-root",
                    str(tmp_path / "unsafe-work"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("failed_checks", proc.stdout)
            self.assertNotIn("alice@example.com", proc.stdout)

    def test_preflight_cli_succeeds_with_shared_work_root(self):
        with tempfile.TemporaryDirectory() as export_tmp, tempfile.TemporaryDirectory(dir=ROOT) as repo_tmp:
            export_path = synthetic_export(Path(export_tmp))
            work_root = Path(repo_tmp) / ".work" / "chatgpt-intake"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "historian.cli",
                    "chatgpt-intake",
                    "preflight",
                    str(export_path),
                    "--work-root",
                    str(work_root),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("\"status\": \"ok\"", proc.stdout)

    def test_preflight_fails_when_private_path_not_ignored(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            work_root = tmp_path / "nonignored-work"
            result = preflight(export_path, work_root=work_root)
            checks = {check["name"]: check["ok"] for check in result["checks"]}
            self.assertFalse(result["ok"])
            self.assertFalse(checks["private_root_ignored"])
            self.assertFalse(checks["raw_export_protected"])

    def test_inventory_allows_outside_repo_work_root_and_does_not_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            work_root = tmp_path / "unsafe-work"
            result = inventory_export(export_path, work_root=work_root)
            self.assertEqual(result["status"], "complete")
            self.assertTrue((work_root / "private").exists())
            self.assertTrue((work_root / "staging").exists())

    def test_inventory_refuses_repo_local_unignored_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            work_root = tmp_path / ".work" / "chatgpt-intake"
            with self.assertRaisesRegex(ValueError, "intake boundary validation failed"):
                inventory_export(export_path, work_root=work_root)

    def test_inventory_refuses_when_repo_identity_cannot_be_established(self):
        with mock.patch("historian.chatgpt_intake._repo_root", return_value=None):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                export_path = synthetic_export(tmp_path)
                work_root = tmp_path / "intake"
                ok, checks = intake_boundary_ok(export_path, work_root=work_root)
                self.assertFalse(ok)
                self.assertFalse(raw_export_protected(export_path, work_root=work_root))
                self.assertFalse(next(check["ok"] for check in checks if check["name"] == "raw_export_protected"))

    def test_cli_chatgpt_intake_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            work_root = tmp_path / "intake"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "historian.cli",
                    "chatgpt-intake",
                    "inventory",
                    str(export_path),
                    "--work-root",
                    str(work_root),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("\"status\": \"complete\"", proc.stdout)
            self.assertIn("\"conversations_total\"", proc.stdout)
            self.assertNotIn("conv_secret", proc.stdout)
            self.assertNotIn("sk-test-1234567890abcdef", proc.stdout)
            self.assertNotIn("archive_sha256", proc.stdout)
            self.assertEqual(proc.stderr.strip(), "")

    def test_cli_chatgpt_intake_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "historian.cli",
                    "chatgpt-intake",
                    "preflight",
                    str(export_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("\"status\": \"ok\"", proc.stdout)
            self.assertEqual(proc.stderr.strip(), "")

    def test_inventory_cli_prints_aggregate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            work_root = tmp_path / "intake"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "historian.cli",
                    "chatgpt-intake",
                    "inventory",
                    str(export_path),
                    "--work-root",
                    str(work_root),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("\"status\": \"complete\"", proc.stdout)
            self.assertIn("\"inventory_location\"", proc.stdout)
            self.assertNotIn("conv_secret", proc.stdout)
            self.assertNotIn("alice@example.com", proc.stdout)
            self.assertNotIn("sk-test-1234567890abcdef", proc.stdout)
            self.assertNotIn("archive_sha256", proc.stdout)

    def test_raw_archive_inside_repo_requires_ignore(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            export_path = synthetic_export(tmp_path)
            work_root = tmp_path / "intake"
            result = preflight(export_path, work_root=work_root)
            self.assertFalse(result["ok"])
            checks = {check["name"]: check["ok"] for check in result["checks"]}
            self.assertFalse(checks["raw_export_protected"])
