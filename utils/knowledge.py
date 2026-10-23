"""Searchable references, editable categories, and evidence-based lessons."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager, nullcontext
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
                CREATE TABLE IF NOT EXISTS entry_profiles(
                    key TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',
                    aliases TEXT NOT NULL DEFAULT '[]', apis TEXT NOT NULL DEFAULT '[]',
                    related TEXT NOT NULL DEFAULT '[]', members TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active', replacement TEXT NOT NULL DEFAULT '',
                    applicability TEXT NOT NULL DEFAULT 'Version not verified',
                    verification TEXT NOT NULL DEFAULT 'documented',
                    origin TEXT NOT NULL DEFAULT 'runtime');
                CREATE TABLE IF NOT EXISTS profile_revisions(
                    id INTEGER PRIMARY KEY, key TEXT NOT NULL, profile TEXT NOT NULL,
                    created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS retired_keys(key TEXT PRIMARY KEY);
                """
            )
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                db.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def save(
        self, key, category, title, content, source, kind="reference", *, _db=None
    ):
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
        with nullcontext(_db) if _db is not None else self.connect() as db:
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
                db.execute(
                    "UPDATE entry_profiles SET origin='runtime' WHERE key=?", (key,)
                )
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

    def set_profile(
        self,
        key,
        summary="",
        aliases=None,
        apis=None,
        related=None,
        members=None,
        status="active",
        replacement="",
        applicability="Version not verified",
        verification="documented",
        origin="runtime",
        *,
        _db=None,
    ):
        """Save explicit editorial metadata without inferring a category.

        Args:
            key: Existing stable entry key.
            summary: Concise task-oriented description.
            aliases: Human-authored task phrases.
            apis: Explicit API identifiers.
            related: Related entry keys.
            members: Original source entry keys comprising the complete card.
            status: Active, archived or superseded.
            replacement: Preferred replacement key when superseded.
            applicability: Evidence-backed version applicability text.
            verification: Documented, source_checked, tested or observed.
            origin: Packaged or runtime ownership of the metadata.

        Raises:
            ValueError: The profile or its target is invalid.
        """
        if status not in {"active", "archived", "superseded"}:
            raise ValueError("Invalid knowledge status")
        if verification not in {"documented", "source_checked", "tested", "observed"}:
            raise ValueError("Invalid verification state")
        if origin not in {"runtime", "packaged"}:
            raise ValueError("Invalid profile origin")
        if any(
            not isinstance(v, str) or len(v) > 2000
            for v in (summary, applicability, replacement)
        ):
            raise ValueError("Profile text must be bounded strings")
        lists = [aliases or [], apis or [], related or [], members or []]
        if any(
            not isinstance(v, list)
            or len(v) > 100
            or any(
                not isinstance(item, str) or not item or len(item) > 300 for item in v
            )
            for v in lists
        ):
            raise ValueError("Tags and relationships must be bounded lists of strings")
        with nullcontext(_db) if _db is not None else self.connect() as db:
            if not db.execute("SELECT 1 FROM entries WHERE key=?", (key,)).fetchone():
                raise ValueError("Knowledge entry not found")
            for target in [
                *(related or []),
                *([replacement] if replacement else []),
            ]:
                if (
                    target == key
                    or not db.execute(
                        "SELECT 1 FROM entries WHERE key=?", (target,)
                    ).fetchone()
                ):
                    raise ValueError(f"Invalid related entry: {target}")
            db.execute(
                "INSERT OR REPLACE INTO entry_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    summary,
                    *(json.dumps(v, ensure_ascii=False) for v in lists),
                    status,
                    replacement,
                    applicability,
                    verification,
                    origin,
                ),
            )
            profile = dict(
                db.execute(
                    "SELECT * FROM entry_profiles WHERE key=?", (key,)
                ).fetchone()
            )
            previous = db.execute(
                "SELECT profile FROM profile_revisions WHERE key=? ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
            serialized = json.dumps(profile, ensure_ascii=False, sort_keys=True)
            if not previous or previous[0] != serialized:
                db.execute(
                    "INSERT INTO profile_revisions(key,profile) VALUES(?,?)",
                    (key, serialized),
                )

    def save_with_profile(self, args):
        """Commit content and explicit metadata together, preserving unspecified fields."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM retired_keys WHERE key=?", (args["key"],)
            ).fetchone():
                raise ValueError(
                    "Retired duplicate key; update the current knowledge card instead"
                )
            old = db.execute(
                "SELECT * FROM entries WHERE key=?", (args["key"],)
            ).fetchone()
            if old:
                if args.get("expected_revision") != old["revision"]:
                    raise ValueError(
                        "Knowledge revision conflict; read the current entry and retry"
                    )
            elif args.get("expected_revision", 0) != 0:
                raise ValueError("Knowledge entry no longer exists")
            previous = db.execute(
                "SELECT * FROM entry_profiles WHERE key=?", (args["key"],)
            ).fetchone()
            profile = dict(previous) if previous else {}
            for field in ("aliases", "apis", "related", "members"):
                if field in profile:
                    profile[field] = json.loads(profile[field])
            profile.pop("key", None)
            profile["origin"] = "runtime"
            for field in (
                "summary",
                "aliases",
                "apis",
                "related",
                "status",
                "replacement",
                "applicability",
                "verification",
            ):
                if field in args:
                    profile[field] = args[field]
            fields = {
                k: args.get(k, old[k] if old else None)
                for k in ("key", "category", "title", "content", "source")
            }
            entry = self.save(
                *(fields[k] for k in ("key", "category", "title", "content", "source")),
                args.get("kind", old["kind"] if old else "reference"),
                _db=db,
            )
            self.set_profile(args["key"], **profile, _db=db)
            if old and self.read_revision(db, entry) == old["revision"]:
                db.execute(
                    "UPDATE entries SET revision=revision+1,updated=CURRENT_TIMESTAMP WHERE id=?",
                    (entry,),
                )
                db.execute(
                    "INSERT INTO revisions SELECT id,revision,category,title,content,source FROM entries WHERE id=?",
                    (entry,),
                )
            return entry

    @staticmethod
    def read_revision(db, entry):
        return db.execute(
            "SELECT revision FROM entries WHERE id=?", (entry,)
        ).fetchone()[0]

    def compact(self):
        """Remove redundant source copies; retain current cards and local corrections."""
        with self.connect() as db:
            cards = db.execute(
                "SELECT e.*,p.members FROM entries e JOIN entry_profiles p USING(key) WHERE e.key LIKE 'card:%'"
            ).fetchall()
            migrated = set()
            for card in cards:
                content = card["content"]
                for key in json.loads(card["members"]):
                    original = db.execute(
                        "SELECT e.*,p.origin FROM entries e LEFT JOIN entry_profiles p USING(key) WHERE e.key=?",
                        (key,),
                    ).fetchone()
                    if (
                        original
                        and original["origin"] != "packaged"
                        and original["revision"] > 1
                    ):
                        baseline = db.execute(
                            "SELECT content FROM revisions WHERE entry_id=? ORDER BY revision LIMIT 1",
                            (original["id"],),
                        ).fetchone()
                        if baseline and baseline[0] in content:
                            content = content.replace(baseline[0], original["content"])
                            migrated.add(key)
                    elif original:
                        migrated.add(key)
                if content != card["content"]:
                    self.save(
                        card["key"],
                        card["category"],
                        card["title"],
                        content,
                        card["source"],
                        card["kind"],
                        _db=db,
                    )
            for row in db.execute(
                "SELECT e.id,e.key,e.revision,p.origin,p.status FROM entries e LEFT JOIN entry_profiles p USING(key) WHERE e.key NOT LIKE 'card:%'"
            ).fetchall():
                if row["key"] not in migrated and not (
                    row["origin"] == "packaged" and row["status"] != "active"
                ):
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO retired_keys VALUES(?)", (row["key"],)
                )
                db.execute("DELETE FROM consultations WHERE entry_id=?", (row["id"],))
                db.execute("DELETE FROM revisions WHERE entry_id=?", (row["id"],))
                db.execute("DELETE FROM profile_revisions WHERE key=?", (row["key"],))
                db.execute("DELETE FROM entry_profiles WHERE key=?", (row["key"],))
                db.execute("DELETE FROM entries WHERE id=?", (row["id"],))
            db.execute("DELETE FROM sources")
            db.execute("PRAGMA user_version=3")
        with self.connect() as db:
            db.execute("VACUUM")

    def search(
        self, query="", category="", limit=6, purpose="lookup", include_archived=False
    ):
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
            profiles = {
                r["key"]: dict(r) for r in db.execute("SELECT * FROM entry_profiles")
            }
            rows = db.execute(
                "SELECT * FROM entries WHERE (?='' OR category=?)", (category, category)
            ).fetchall()
            ranked = []
            for row in rows:
                profile = profiles.get(row["key"], {})
                if not include_archived and profile.get("status", "active") != "active":
                    continue
                heading = f"{row['category']} {row['title']}".lower()
                body = row["content"].lower()
                aliases = json.loads(profile.get("aliases", "[]"))
                apis = json.loads(profile.get("apis", "[]"))
                labels = " ".join([*aliases, *apis, profile.get("summary", "")]).lower()
                score = sum(
                    5 * (t in heading) + 4 * (t in labels) + (t in body) for t in tokens
                )
                score += sum(
                    15
                    for alias in [*aliases, *apis]
                    if re.search(
                        r"(?<![a-zA-Z_0-9])"
                        + re.escape(alias.lower())
                        + r"(?![a-zA-Z_0-9])"
                        if re.search(r"[a-zA-Z]", alias)
                        else re.escape(alias.lower()),
                        query.lower(),
                    )
                )
                if query.strip() == row["key"]:
                    score += 10000
                if profile.get("verification") == "observed":
                    score *= 0.4
                if score or not tokens:
                    item = dict(row)
                    item.update(
                        {
                            k: profile.get(k, default)
                            for k, default in (
                                ("summary", ""),
                                ("status", "active"),
                                ("replacement", ""),
                                ("applicability", "Version not verified"),
                                ("verification", "documented"),
                            )
                        }
                    )
                    item["related"] = json.loads(profile.get("related", "[]"))
                    item["apis"] = apis
                    item["score"] = score
                    ranked.append((score, row["id"], item))
            ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
            result = [item for _, _, item in ranked[:limit]]
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
                    "SELECT e.category,e.kind,count(*) AS count FROM entries e "
                    "LEFT JOIN entry_profiles p ON e.key=p.key "
                    "WHERE coalesce(p.status,'active')='active' GROUP BY e.category,e.kind"
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
            profile = db.execute(
                "SELECT * FROM entry_profiles WHERE key=?", (row["key"],)
            ).fetchone()
            if profile:
                result["profile"] = dict(profile)
                for field in ("aliases", "apis", "related", "members"):
                    result["profile"][field] = json.loads(result["profile"][field])
            start = max(0, int(offset))
            result["total_characters"] = len(result["content"])
            result["content"] = result["content"][start : start + 6000]
            result["offset"] = start
            return result

    def plan(self, topics, limit_per_topic=3):
        """Retrieve independent facets supplied by the caller, without classifying docs.

        Args:
            topics: Explicit development subtasks, such as configuration and image sending.
            limit_per_topic: Maximum direct matches for each subtask.

        Returns:
            Per-topic evidence and related-entry indexes, including gaps.
        """
        if (
            not isinstance(topics, list)
            or not 1 <= len(topics) <= 8
            or any(
                not isinstance(t, str) or not t.strip() or len(t) > 500 for t in topics
            )
        ):
            raise ValueError("Provide 1 to 8 explicit development topics")
        groups = []
        for topic in topics:
            hits = self.search(
                topic,
                limit=max(1, min(int(limit_per_topic), 4)),
                purpose="task facet: " + topic,
            )
            with self.connect() as db:
                related = []
                seen = {h["key"] for h in hits}
                for hit in hits:
                    for key in hit["related"]:
                        if key in seen:
                            continue
                        row = db.execute(
                            "SELECT e.id,e.key,e.category,e.title,p.summary,p.status "
                            "FROM entries e LEFT JOIN entry_profiles p ON e.key=p.key WHERE e.key=?",
                            (key,),
                        ).fetchone()
                        if row and row["status"] == "active":
                            related.append(dict(row))
                            seen.add(key)
                groups.append(
                    {
                        "topic": topic,
                        "matches": [
                            {k: v for k, v in hit.items() if k != "content"}
                            for hit in hits
                        ],
                        "related": related[:6],
                        "gap": not hits,
                    }
                )
        return groups

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
                    "UPDATE entry_profiles SET origin='runtime' WHERE key=?",
                    (row["key"],),
                )
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
            retired = source.execute(
                "SELECT 1 FROM sqlite_master WHERE name='retired_keys'"
            ).fetchone()
            if retired:
                db.executemany(
                    "INSERT OR IGNORE INTO retired_keys VALUES(?)",
                    source.execute("SELECT key FROM retired_keys"),
                )
            for row in source.execute("SELECT name,sha256,raw FROM sources"):
                db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?)", row)
            for row in source.execute(
                "SELECT key,kind,category,title,content,source FROM entries"
            ):
                if db.execute(
                    "SELECT 1 FROM retired_keys WHERE key=?", (row[0],)
                ).fetchone():
                    continue
                # Runtime edits take precedence over packaged references on upgrades.
                db.execute(
                    "INSERT OR IGNORE INTO entries"
                    "(key,kind,category,title,content,source) VALUES(?,?,?,?,?,?)",
                    row,
                )
                local_profile = db.execute(
                    "SELECT origin FROM entry_profiles WHERE key=?", (row[0],)
                ).fetchone()
                if local_profile and local_profile[0] == "packaged":
                    old = db.execute(
                        "SELECT kind,category,title,content,source FROM entries WHERE key=?",
                        (row[0],),
                    ).fetchone()
                    if tuple(old) != tuple(row[1:]):
                        db.execute(
                            "UPDATE entries SET kind=?,category=?,title=?,content=?,source=?,"
                            "revision=revision+1,updated=CURRENT_TIMESTAMP WHERE key=?",
                            (*row[1:], row[0]),
                        )
            db.execute(
                "INSERT OR IGNORE INTO revisions SELECT "
                "id,revision,category,title,content,source FROM entries"
            )
            if source.execute(
                "SELECT 1 FROM sqlite_master WHERE name='entry_profiles'"
            ).fetchone():
                for profile in source.execute("SELECT * FROM entry_profiles"):
                    if not db.execute(
                        "SELECT 1 FROM entries WHERE key=?", (profile[0],)
                    ).fetchone():
                        continue
                    existing = db.execute(
                        "SELECT origin FROM entry_profiles WHERE key=?", (profile[0],)
                    ).fetchone()
                    local = db.execute(
                        "SELECT revision FROM entries WHERE key=?", (profile[0],)
                    ).fetchone()
                    if existing and existing[0] == "runtime":
                        continue
                    if not existing and local and local[0] > 1:
                        continue
                    db.execute(
                        "INSERT OR REPLACE INTO entry_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        profile,
                    )
                    serialized = json.dumps(
                        dict(
                            zip(
                                [
                                    c[1]
                                    for c in source.execute(
                                        "PRAGMA table_info(entry_profiles)"
                                    )
                                ],
                                profile,
                            )
                        ),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    previous = db.execute(
                        "SELECT profile FROM profile_revisions WHERE key=? ORDER BY id DESC LIMIT 1",
                        (profile[0],),
                    ).fetchone()
                    if not previous or previous[0] != serialized:
                        db.execute(
                            "INSERT INTO profile_revisions(key,profile) VALUES(?,?)",
                            (profile[0], serialized),
                        )
        with closing(sqlite3.connect(seed)) as source:
            if source.execute("PRAGMA user_version").fetchone()[0] >= 3:
                self.compact()

    def record_failure(self, stage, target, error):
        digest = hashlib.sha256(f"{stage}:{error}".encode()).hexdigest()[:20]
        key = f"failure:{digest}"
        with self.connect() as db:
            existing = db.execute(
                "SELECT id FROM entries WHERE key=?", (key,)
            ).fetchone()
            if existing:
                return existing[0]
        entry = self.save(
            key,
            "错误经验/待验证",
            f"{stage}: {type(error).__name__}",
            f"对象：{target}\n实际错误：{error}\n状态：已观察，修复方法待验证。"
            "遇到相同问题先定位原因，不应将本条视为已验证的解决方案。",
            f"runtime:{stage}",
            "experience",
        )
        self.set_profile(key, summary=str(error)[:300], verification="observed")
        return entry
