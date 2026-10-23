"""Searchable references, editable categories, and evidence-based lessons."""

import hashlib
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


class KnowledgeStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources(
                    name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, raw BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS entries(
                    id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL,
                    content TEXT NOT NULL, source TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS revisions(
                    entry_id INTEGER, revision INTEGER, category TEXT,
                    title TEXT, content TEXT, source TEXT,
                    PRIMARY KEY(entry_id, revision));
                CREATE TABLE IF NOT EXISTS consultations(
                    id INTEGER PRIMARY KEY, entry_id INTEGER, purpose TEXT,
                    created TEXT DEFAULT CURRENT_TIMESTAMP);
                """
            )

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def save(self, key, category, title, content, source, kind="reference"):
        if kind not in {"reference", "experience"}:
            raise ValueError("kind must be reference or experience")
        if any(
            not isinstance(v, str) or not v.strip()
            for v in (key, category, title, content, source)
        ):
            raise ValueError(
                "key, category, title, content and source/evidence are required"
            )
        if len(content) > 64000:
            raise ValueError("Split entries larger than 64000 characters")
        with self.connect() as db:
            old = db.execute("SELECT * FROM entries WHERE key=?", (key,)).fetchone()
            if old and old["kind"] != kind:
                raise ValueError("An entry's kind cannot be changed")
            if old and all(
                old[k] == v
                for k, v in (
                    ("category", category),
                    ("title", title),
                    ("content", content),
                    ("source", source),
                )
            ):
                return old["id"]
            if old:
                db.execute(
                    "UPDATE entries SET category=?,title=?,content=?,source=?,"
                    "revision=revision+1,updated=CURRENT_TIMESTAMP WHERE id=?",
                    (category, title, content, source, old["id"]),
                )
                entry_id = old["id"]
            else:
                entry_id = db.execute(
                    "INSERT INTO entries(key,kind,category,title,content,source) VALUES(?,?,?,?,?,?)",
                    (key, kind, category, title, content, source),
                ).lastrowid
            db.execute(
                "INSERT INTO revisions SELECT id,revision,category,title,content,source "
                "FROM entries WHERE id=?",
                (entry_id,),
            )
            return entry_id

    def search(self, query="", category="", limit=6, purpose="lookup"):
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("query must be a string of at most 4000 characters")
        limit = max(1, min(int(limit), 12))
        tokens = set(
            re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*|[\u4e00-\u9fff]+", query.lower())
        )
        for token in list(tokens):
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                tokens.update(token[i : i + 2] for i in range(len(token) - 1))
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM entries WHERE (?='' OR category=?)", (category, category)
            ).fetchall()
            ranked = []
            for row in rows:
                heading = f"{row['category']} {row['title']}".lower()
                body = row["content"].lower()
                score = sum(5 * (t in heading) + (t in body) for t in tokens)
                if score or not tokens:
                    ranked.append(
                        (score, row["kind"] == "experience", row["id"], dict(row))
                    )
            result = [item[3] for item in sorted(ranked, reverse=True)[:limit]]
            for row in result:
                db.execute(
                    "INSERT INTO consultations(entry_id,purpose) VALUES(?,?)",
                    (row["id"], purpose[:300]),
                )
            db.execute(
                "DELETE FROM consultations WHERE id NOT IN "
                "(SELECT id FROM consultations ORDER BY id DESC LIMIT 2000)"
            )
            return result

    def categories(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT category,kind,count(*) AS count FROM entries GROUP BY category,kind"
                )
            ]

    def read(self, entry_id, offset=0):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM entries WHERE id=?", (int(entry_id),)
            ).fetchone()
            if row is None:
                raise ValueError("Knowledge entry not found")
            result = dict(row)
            start = max(0, int(offset))
            result["total_characters"] = len(result["content"])
            result["content"] = result["content"][start : start + 6000]
            result["offset"] = start
            return result

    def rename_category(self, old, new):
        if not old or not new or len(new) > 100:
            raise ValueError(
                "old and new category names are required (max 100 characters)"
            )
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM entries WHERE category=?", (old,)
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE entries SET category=?,revision=revision+1,"
                    "updated=CURRENT_TIMESTAMP WHERE id=?",
                    (new, row["id"]),
                )
                db.execute(
                    "INSERT INTO revisions SELECT id,revision,category,title,content,source "
                    "FROM entries WHERE id=?",
                    (row["id"],),
                )
            return len(rows)

    def import_seed(self, seed):
        """Import new packaged entries without replacing local corrections.

        Args:
            seed: Packaged SQLite reference database path.
        """
        with closing(sqlite3.connect(seed)) as source, self.connect() as db:
            for row in source.execute("SELECT name,sha256,raw FROM sources"):
                db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?)", row)
            for row in source.execute(
                "SELECT key,kind,category,title,content,source FROM entries"
            ):
                # Runtime edits take precedence over packaged references on upgrades.
                db.execute(
                    "INSERT OR IGNORE INTO entries"
                    "(key,kind,category,title,content,source) VALUES(?,?,?,?,?,?)",
                    row,
                )
            db.execute(
                "INSERT OR IGNORE INTO revisions SELECT "
                "id,revision,category,title,content,source FROM entries"
            )

    def record_failure(self, stage, target, error):
        digest = hashlib.sha256(f"{stage}:{error}".encode()).hexdigest()[:20]
        return self.save(
            f"failure:{digest}",
            "错误经验/待验证",
            f"{stage}: {type(error).__name__}",
            f"对象：{target}\n实际错误：{error}\n状态：已观察，修复方法待验证。"
            "遇到相同问题先定位原因，不应将本条视为已验证的解决方案。",
            f"runtime:{stage}",
            "experience",
        )
