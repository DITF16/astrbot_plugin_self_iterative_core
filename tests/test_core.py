"""Regression tests for code recovery, references, and logging boundaries."""

import asyncio
import hashlib
import json
import logging
import sqlite3
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.migrate_knowledge import migrate
from utils.file_manager import FileManager
from utils.knowledge import KnowledgeStore
from utils.log_manager import LogManager


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = FileManager(self.root / "plugins", self.root / "data")
        self.plugin = self.root / "plugins/demo"
        self.plugin.mkdir(parents=True)
        (self.plugin / "main.py").write_text("x = 1\n", encoding="utf-8")

    def change(self, content, path="main.py"):
        target = self.plugin / path
        return {
            "file_path": path,
            "content": content,
            "expected_sha256": hashlib.sha256(target.read_bytes()).hexdigest()
            if target.exists()
            else "missing",
        }

    def test_backup_rollback_and_undo_rollback(self):
        (self.plugin / "asset.bin").write_bytes(bytes(range(256)))
        (self.plugin / ".git").mkdir()
        (self.plugin / ".git/config").write_text("preserved")
        result = self.files.apply(
            "demo", [self.change("x = 2\n"), self.change("", "empty.py")]
        )
        restored = self.files.rollback("demo", result["backup_id"])
        self.assertEqual((self.plugin / "main.py").read_text(), "x = 1\n")
        self.assertFalse((self.plugin / "empty.py").exists())
        self.assertEqual((self.plugin / "asset.bin").read_bytes(), bytes(range(256)))
        self.assertEqual((self.plugin / ".git/config").read_text(), "preserved")
        self.files.rollback("demo", restored["safety_backup"])
        self.assertEqual((self.plugin / "main.py").read_text(), "x = 2\n")

    def test_validation_precedes_all_writes(self):
        with self.assertRaises(SyntaxError):
            self.files.apply(
                "demo", [self.change("x = 2"), self.change("def (", "bad.py")]
            )
        self.assertEqual((self.plugin / "main.py").read_text(), "x = 1\n")
        with self.assertRaises(json.JSONDecodeError):
            self.files.apply("demo", [self.change("{", "bad.json")])

    def test_conflict_does_not_overwrite(self):
        change = self.change("x = 2")
        (self.plugin / "main.py").write_text("x = 3")
        with self.assertRaises(ValueError):
            self.files.apply("demo", [change])
        self.assertEqual((self.plugin / "main.py").read_text(), "x = 3")

    def test_partial_write_restores_original(self):
        original = self.files.atomic_write
        calls = []

        def fail_once(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("injected disk failure")
            return original(path, data)

        with patch.object(self.files, "atomic_write", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.files.apply(
                    "demo", [self.change("x = 2"), self.change("x = 3", "new.py")]
                )
        self.assertEqual((self.plugin / "main.py").read_text(), "x = 1\n")
        self.assertFalse((self.plugin / "new.py").exists())

    def test_traversal_and_absolute_paths(self):
        for plugin, path in [
            ("../other", "x"),
            ("demo", "../x"),
            ("demo", "C:/x"),
            ("demo", ".git/config"),
            ("demo", "main.py:stream"),
        ]:
            with self.assertRaises(ValueError):
                self.files.path(plugin, path)

    def test_backup_cannot_cross_plugins(self):
        backup = self.files.snapshot("demo")
        with self.assertRaises(ValueError):
            self.files.rollback("other", backup)

    def test_read_hash_and_pagination(self):
        result = asyncio.run(self.files.read_file("demo", "main.py", 1, 1))
        self.assertEqual(result["content"], "x = 1")
        self.assertEqual(result["sha256"], self.change("")["expected_sha256"])

    def test_corrupt_snapshot_preserves_current_files(self):
        backup = self.files.snapshot("demo")
        with closing(sqlite3.connect(self.files.db_path)) as db, db:
            db.execute("UPDATE snapshots SET archive=? WHERE id=?", (b"broken", backup))
        with self.assertRaises(ValueError):
            self.files.rollback("demo", backup)
        self.assertEqual((self.plugin / "main.py").read_text(), "x = 1\n")

    def test_empty_source_migrates_without_loss(self):
        source = self.root / "empty_docs"
        source.mkdir()
        (source / "plugin.md").write_bytes(b"\r\n")
        migrate(source, self.root / "empty.sqlite3", True)
        with closing(sqlite3.connect(self.root / "empty.sqlite3")) as db:
            self.assertEqual(
                db.execute("SELECT raw FROM sources").fetchone()[0], b"\r\n"
            )

    def test_noop_does_not_create_backup(self):
        result = self.files.apply(
            "demo",
            [self.change((self.plugin / "main.py").read_bytes().decode("utf-8"))],
        )
        self.assertFalse(result["changed"])
        self.assertEqual(self.files.history("demo"), [])

    def test_migration_exact_bytes_and_fences(self):
        source = self.root / "docs"
        source.mkdir()
        raw = b"# AI\r\n## Tool\r\n```python\r\n# comment\r\n```\r\n"
        (source / "ai.md").write_bytes(raw)
        result = migrate(source, self.root / "seed.sqlite3", delete=True)
        self.assertTrue(result["deleted"])
        self.assertFalse((source / "ai.md").exists())
        with closing(sqlite3.connect(self.root / "seed.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT raw FROM sources").fetchone()[0], raw)
            self.assertEqual(
                db.execute("SELECT count(*) FROM entries").fetchone()[0], 2
            )

    def test_knowledge_revisions_categories_and_lessons(self):
        store = KnowledgeStore(self.root / "kb.sqlite3")
        entry = store.save(
            "message",
            "消息格式",
            "MessageChain 消息链",
            "使用 chain_result",
            "host source",
        )
        store.save(
            "message",
            "消息格式",
            "MessageChain 消息链",
            "使用 event.chain_result",
            "verified source",
        )
        self.assertEqual(store.search("消息链")[0]["id"], entry)
        self.assertEqual(store.rename_category("消息格式", "消息构造"), 1)
        store.record_failure("write", "demo", ValueError("conflict"))
        self.assertEqual(store.search("conflict")[0]["kind"], "experience")
        with store.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM revisions WHERE entry_id=?", (entry,)
                ).fetchone()[0],
                3,
            )

    def test_seed_import_preserves_runtime_edits(self):
        seed = KnowledgeStore(self.root / "seed.sqlite3")
        seed.save("key", "topic", "title", "original", "seed")
        runtime = KnowledgeStore(self.root / "runtime.sqlite3")
        runtime.import_seed(seed.path)
        runtime.save("key", "topic", "title", "local correction", "verified")
        runtime.import_seed(seed.path)
        self.assertEqual(runtime.search("correction")[0]["content"], "local correction")

    def test_logging_reads_bounded_host_broker_and_shutdown(self):
        broker = types.SimpleNamespace(log_cache=[
            {"time": 1, "level": "INFO", "data": "record 1", "plugin_name": "self"},
            {"time": 2, "level": "INFO", "data": "record 2", "plugin_name": "self"},
            {"time": 3, "level": "INFO", "data": "record 3", "plugin_name": "self"},
        ])
        manager = LogManager({"log_max_history": 2}, broker)
        self.assertEqual(len(manager.get_logs(100)), 2)
        self.assertIn("record 3", manager.get_logs(0)[-1]["data"])
        manager.shutdown()
        self.assertEqual(manager.get_logs(), [])

    def test_broker_preserves_plugin_filter(self):
        broker = types.SimpleNamespace(
            log_cache=[
                {"time": 1, "plugin_name": "one", "data": "a"},
                {"time": 2, "plugin_name": "two", "data": "b"},
            ]
        )
        handler = LogManager({}, broker)
        self.addCleanup(handler.shutdown)
        self.assertEqual(handler.limit, 1000)
        self.assertEqual(handler.get_logs(plugin="two", since=2)[0]["data"], "b")


if __name__ == "__main__":
    unittest.main()
