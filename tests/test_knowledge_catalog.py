"""Curated retrieval, provenance and local upgrade regression tests."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from knowledge.catalog import CARDS
from scripts.build_catalog import build
from utils.knowledge import KnowledgeStore

SEED = Path(__file__).resolve().parents[1] / "knowledge/seed.sqlite3"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = KnowledgeStore(Path(temp.name) / "knowledge.sqlite3")
        self.store.import_seed(SEED)

    def test_sources_and_complete_manual_coverage(self):
        with self.store.connect() as db:
            sources = db.execute("SELECT raw,sha256 FROM sources").fetchall()
            self.assertEqual(len(sources), 15)
            for raw, digest in sources:
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertEqual(
            build(self.store.path), {"cards": 46, "original_entries_accounted_for": 216}
        )
        self.assertEqual(len(CARDS), 46)

    def test_natural_language_and_api_retrieval(self):
        cases = {
            "每天给群里主动推送图片": "message-active",
            "配置项": "config-schema",
            "llm_generate": "llm-generate",
            "注册工具": "tool-class",
            "聊天历史": "history-read",
            "等待用户下一条消息": "session-wait",
            "HTML 渲染": "render",
            "HTTP attachment_id": "http-message",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                hits = self.store.search(query, limit=3)
                self.assertIn("card:" + expected, [h["key"] for h in hits])
                self.assertTrue(all(h["status"] == "active" for h in hits))

    def test_independent_facets_and_unknown_gap(self):
        plan = self.store.plan(["配置项", "llm_generate", "主动发送"])
        self.assertEqual(len(plan), 3)
        for group in plan:
            self.assertFalse(group["gap"])
            self.assertTrue(group["related"])
            self.assertNotIn("content", group["matches"][0])
        self.assertTrue(self.store.plan(["qzxnonexistent987"])[0]["gap"])

    def test_archived_error_has_replacement(self):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status,replacement FROM entry_profiles WHERE key='doc:storage.md:2'"
            ).fetchone()
        self.assertEqual(tuple(row), ("superseded", "card:storage-files"))
        archived = self.store.search("storage", include_archived=True, limit=12)
        self.assertTrue(any(h["status"] != "active" for h in archived))

    def test_profile_failure_rolls_back_content(self):
        args = {
            "key": "new",
            "category": "test",
            "title": "test",
            "content": "body",
            "source": "test",
            "related": ["missing"],
        }
        with self.assertRaises(ValueError):
            self.store.save_with_profile(args)
        with self.store.connect() as db:
            self.assertIsNone(
                db.execute("SELECT 1 FROM entries WHERE key='new'").fetchone()
            )

    def test_local_changes_survive_upgrade(self):
        hit = self.store.search("_conf_schema.json", limit=1)[0]
        self.store.rename_category(hit["category"], "local-config")
        self.store.import_seed(SEED)
        self.assertEqual(self.store.read(hit["id"])["category"], "local-config")
        self.store.save_with_profile(
            {
                "key": hit["key"],
                "category": "local-config",
                "title": hit["title"],
                "content": "local correction",
                "source": "local test",
                "summary": "corrected",
            }
        )
        self.store.import_seed(SEED)
        self.assertEqual(self.store.read(hit["id"])["content"], "local correction")

    def test_repeated_failure_preserves_diagnosis(self):
        error = ValueError("observed failure")
        entry = self.store.record_failure("write", "demo", error)
        item = self.store.read(entry)
        self.store.save_with_profile(
            {
                "key": item["key"],
                "category": "lessons",
                "title": "diagnosed",
                "content": "verified fix",
                "source": "test evidence",
                "kind": "experience",
                "verification": "tested",
            }
        )
        self.assertEqual(self.store.record_failure("write", "demo", error), entry)
        self.assertEqual(self.store.read(entry)["profile"]["verification"], "tested")

    def test_legacy_database_upgrade_preserves_correction(self):
        legacy = KnowledgeStore(self.store.path.parent / "legacy.sqlite3")
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM entries WHERE key LIKE 'doc:%'").fetchall()
        for row in rows:
            legacy.save(
                *(row[k] for k in ("key", "category", "title", "content", "source")),
                row["kind"],
            )
        changed = rows[0]
        entry = legacy.save(
            changed["key"],
            "local",
            changed["title"],
            "legacy correction",
            "local evidence",
        )
        legacy.import_seed(SEED)
        self.assertEqual(legacy.read(entry)["content"], "legacy correction")
        self.assertNotIn("profile", legacy.read(entry))
        self.assertEqual(
            len(
                [h for h in legacy.search("", limit=12) if h["key"].startswith("card:")]
            ),
            12,
        )

    def test_packaged_updates_and_exact_related_lookup(self):
        packaged = KnowledgeStore(self.store.path.parent / "package.sqlite3")
        packaged.import_seed(SEED)
        key = "card:llm-generate"
        item = packaged.search(key, limit=1)[0]
        self.assertEqual(item["key"], key)
        packaged.save(
            key, item["category"], item["title"], "new package content", item["source"]
        )
        packaged.set_profile(key, summary="new package summary", origin="packaged")
        self.store.import_seed(packaged.path)
        upgraded = self.store.read(item["id"])
        self.assertEqual(upgraded["content"], "new package content")
        self.assertEqual(upgraded["profile"]["summary"], "new package summary")
