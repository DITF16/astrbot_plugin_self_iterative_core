"""Bounded file operations, atomic replacement, and durable plugin snapshots."""

import ast
import asyncio
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import closing
from pathlib import Path, PureWindowsPath


class FileManager:
    EXCLUDED = {
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
        ".ruff_cache",
        ".pytest_cache",
    }
    MAX_BYTES = 32 * 1024 * 1024

    def __init__(self, base_path, data_path):
        self.base_path = Path(base_path).resolve()
        self.data_path = Path(data_path).resolve()
        if self.data_path.is_relative_to(self.base_path):
            raise ValueError("Backup data must be outside the plugin source directory")
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_path / "backups.sqlite3"
        self.lock = threading.RLock()
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshots("
                "id TEXT PRIMARY KEY,plugin TEXT,reason TEXT,sha256 TEXT,archive BLOB,"
                "created TEXT DEFAULT CURRENT_TIMESTAMP)"
            )

    def path(self, plugin, relative=""):
        if (
            not isinstance(plugin, str)
            or not plugin
            or plugin in {".", ".."}
            or any(c in plugin for c in "/\\:")
            or plugin.startswith(".")
        ):
            raise ValueError("plugin_name must be a single directory name")
        if not isinstance(relative, str):
            raise ValueError("file_path must be a string")
        win = PureWindowsPath(relative)
        if win.drive or win.root or Path(relative).is_absolute():
            raise ValueError("Absolute file paths are not allowed")
        parts = relative.replace("\\", "/").split("/")
        if any(p == ".." or p in self.EXCLUDED or ":" in p for p in parts):
            raise ValueError(
                "Parent traversal and internal directories are not allowed"
            )
        root = (self.base_path / plugin).resolve()
        target = (root / relative).resolve()
        if root.parent != self.base_path or not target.is_relative_to(root):
            raise ValueError("Resolved path escapes the plugin directory")
        current = self.base_path
        for part in (plugin, *Path(relative).parts):
            current /= part
            if current.is_symlink() or (
                hasattr(current, "is_junction") and current.is_junction()
            ):
                raise ValueError("Linked files and directories are not supported")
        return target

    def files(self, plugin):
        root = self.path(plugin)
        if not root.exists():
            return []
        result = []
        for directory, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in self.EXCLUDED)
            for d in dirs:
                self.path(plugin, (Path(directory) / d).relative_to(root).as_posix())
            for name in sorted(names):
                relative = (Path(directory) / name).relative_to(root).as_posix()
                self.path(plugin, relative)
                result.append(relative)
                if len(result) > 5000:
                    raise ValueError("Plugin exceeds the 5000-file limit")
        return result

    def snapshot(self, plugin, reason="manual"):
        """Persist a bounded source snapshot before mutation.

        Args:
            plugin: Single plugin directory name.
            reason: Human-readable operation label.

        Returns:
            Durable snapshot identifier.

        Raises:
            ValueError: A linked path or snapshot limit is encountered.
        """
        with self.lock:
            stream = io.BytesIO()
            total = 0
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative in self.files(plugin):
                    path = self.path(plugin, relative)
                    total += path.stat().st_size
                    if total > self.MAX_BYTES:
                        raise ValueError("Plugin snapshot exceeds 32 MiB")
                    archive.write(path, relative)
            payload = stream.getvalue()
            backup_id = uuid.uuid4().hex
            with closing(sqlite3.connect(self.db_path, timeout=30)) as db, db:
                db.execute(
                    "INSERT INTO snapshots(id,plugin,reason,sha256,archive) VALUES(?,?,?,?,?)",
                    (
                        backup_id,
                        plugin,
                        reason,
                        hashlib.sha256(payload).hexdigest(),
                        payload,
                    ),
                )
            return backup_id

    @staticmethod
    def atomic_write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(
            prefix=".dev-write-", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if path.exists():
                os.chmod(temp, path.stat().st_mode)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _restore(self, plugin, backup_id):
        with closing(sqlite3.connect(self.db_path, timeout=30)) as db, db:
            row = db.execute(
                "SELECT archive,sha256 FROM snapshots WHERE id=? AND plugin=?",
                (backup_id, plugin),
            ).fetchone()
        if not row:
            raise ValueError("Backup not found for this plugin")
        payload, digest = row
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Backup checksum mismatch")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if sum(i.file_size for i in archive.infolist()) > self.MAX_BYTES:
                raise ValueError("Backup exceeds size limit")
            contents = {name: archive.read(name) for name in archive.namelist()}
        for name in contents:
            self.path(plugin, name)
        existing = self.files(plugin)
        for name, data in contents.items():
            self.atomic_write(self.path(plugin, name), data)
        for name in set(existing) - set(contents):
            self.path(plugin, name).unlink()

    def rollback(self, plugin, backup_id):
        """Restore a snapshot while retaining an undo snapshot.

        Args:
            plugin: Plugin directory to restore.
            backup_id: Snapshot belonging to this plugin.

        Returns:
            Restored snapshot and safety snapshot identifiers.

        Raises:
            ValueError: The snapshot is missing or fails integrity validation.
        """
        with self.lock:
            safety = self.snapshot(plugin, "before rollback")
            try:
                self._restore(plugin, backup_id)
            except BaseException:
                self._restore(plugin, safety)
                raise
            return {
                "ok": True,
                "restored_backup": backup_id,
                "safety_backup": safety,
                "reload_required": True,
            }

    def history(self, plugin):
        self.path(plugin)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,reason,created,length(archive) AS bytes FROM snapshots "
                    "WHERE plugin=? ORDER BY rowid DESC LIMIT 50",
                    (plugin,),
                )
            ]

    def apply(self, plugin, changes):
        """Validate the batch before writing and restore files on write failure.

        Args:
            plugin: Plugin directory name.
            changes: Full file contents and expected hashes for existing files.

        Returns:
            Change status, snapshot identifier and affected files.

        Raises:
            ValueError: Paths, limits or expected hashes are invalid.
            SyntaxError: Python validation fails before any file is changed.
        """
        with self.lock:
            if not isinstance(changes, list) or not 1 <= len(changes) <= 30:
                raise ValueError("Provide 1 to 30 file changes")
            prepared = {}
            for change in changes:
                relative, content = change["file_path"], change["content"]
                path = self.path(plugin, relative)
                if not relative or not isinstance(content, str):
                    raise ValueError(
                        "Nonempty file_path and string content are required"
                    )
                if path in prepared:
                    raise ValueError("Duplicate file path")
                data = content.encode("utf-8")
                if len(data) > 1024 * 1024:
                    raise ValueError("File exceeds 1 MiB")
                if path.exists() and path.stat().st_size > 1024 * 1024:
                    raise ValueError("Existing file exceeds 1 MiB")
                old = path.read_bytes() if path.exists() else b""
                expected = change.get("expected_sha256")
                if path.exists() and expected != hashlib.sha256(old).hexdigest():
                    raise ValueError(
                        "Read the existing file first and supply its current expected_sha256"
                    )
                if not path.exists() and expected not in (None, "missing"):
                    raise ValueError("File no longer exists; expected_sha256 conflicts")
                if path.suffix == ".py":
                    ast.parse(content, filename=relative)
                elif path.suffix == ".json":
                    json.loads(content)
                elif path.suffix in {".yaml", ".yml"}:
                    import yaml

                    yaml.safe_load(content)
                if not path.exists() or old != data:
                    prepared[path] = data
            if not prepared:
                return {"ok": True, "changed": False}
            backup_id = self.snapshot(plugin, "before edit")
            try:
                for path, data in prepared.items():
                    self.atomic_write(path, data)
            except BaseException:
                self._restore(plugin, backup_id)
                raise
            return {
                "ok": True,
                "changed": True,
                "backup_id": backup_id,
                "files": [
                    p.relative_to(self.path(plugin)).as_posix() for p in prepared
                ],
                "reload_required": True,
            }

    async def read_file(self, plugin, relative, start=1, lines=200):
        def read():
            path = self.path(plugin, relative)
            if not path.exists():
                return {"exists": False, "sha256": "missing"}
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("File exceeds 1 MiB")
            data = path.read_bytes()
            text = data.decode("utf-8").splitlines()
            first = max(1, int(start))
            count = max(1, min(int(lines), 400))
            return {
                "exists": True,
                "sha256": hashlib.sha256(data).hexdigest(),
                "total_lines": len(text),
                "start": first,
                "content": "\n".join(text[first - 1 : first - 1 + count]),
            }

        return await asyncio.to_thread(read)
