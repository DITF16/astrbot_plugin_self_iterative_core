"""Developer tools backed by per-plugin runtime state."""

import asyncio
import json
import logging
import time
from pathlib import Path

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext

from .file_manager import FileManager
from .knowledge import KnowledgeStore
from .log_manager import LogManager


class DeveloperRuntime:
    def __init__(self, context, config, data_path, plugin_path):
        self.context = context
        self.config = config
        self.data_path = Path(data_path)
        self.files = FileManager(plugin_path, data_path)
        self.knowledge = KnowledgeStore(self.data_path / "knowledge.sqlite3")
        self.lock = asyncio.Lock()
        self.logs = None
        self.closed = False

    async def initialize(self):
        await asyncio.to_thread(
            self.knowledge.import_seed,
            Path(__file__).resolve().parents[1] / "knowledge/seed.sqlite3",
        )
        # The broker is internal to AstrBot; keep a bounded fallback for portability.
        from astrbot.core.log import LogManager as HostLogManager

        broker = getattr(HostLogManager, "_log_broker", None)
        self.logs = LogManager(self.config, broker)

    def allowed(self, event):
        return not self.config.get("enable_whitelist", True) or str(
            event.get_sender_id()
        ) in {str(uid) for uid in self.config.get("whitelist_users", [])}

    async def references(self, query, purpose):
        rows = await asyncio.to_thread(self.knowledge.search, query, "", 6, purpose)
        return [
            {
                "id": r["id"],
                "key": r["key"],
                "kind": r["kind"],
                "category": r["category"],
                "title": r["title"],
                "source": r["source"],
                "applicability": r["applicability"],
                "summary": r.get("summary", ""),
                "related": r.get("related", []),
                "verification": r.get("verification", "documented"),
            }
            for r in rows
        ]

    async def execute(self, operation, event, args):
        if self.closed:
            return {"ok": False, "error": "Plugin runtime has been terminated"}
        if not self.allowed(event):
            return {"ok": False, "error": "当前用户不在开发工具白名单中"}
        if self.config.get("verbose_steps", True) and operation in {
            "dev_write_file",
            "dev_write_files",
            "dev_load_plugin",
            "dev_rollback_plugin",
        }:
            try:
                from astrbot.api.event import MessageChain

                await self.context.send_message(
                    event.unified_msg_origin,
                    MessageChain().message(
                        f"开发操作：{operation} · {args.get('plugin_name', '')}"
                    ),
                )
            except Exception:
                logging.getLogger("astrbot").debug(
                    "Developer progress notification unavailable", exc_info=True
                )
        async with self.lock:
            try:
                return await self._execute(operation, args)
            except Exception as exc:
                try:
                    entry = await asyncio.to_thread(
                        self.knowledge.record_failure,
                        operation,
                        str(args.get("plugin_name", "")),
                        exc,
                    )
                except Exception:
                    logging.getLogger("astrbot").exception(
                        "Failed to persist developer tool failure"
                    )
                    entry = None
                return {"ok": False, "error": str(exc), "experience_id": entry}

    async def _execute(self, operation, args):
        plugin = args.get("plugin_name", "")
        if operation == "dev_search_knowledge":
            if args.get("topics"):
                return {
                    "ok": True,
                    "plan": await asyncio.to_thread(
                        self.knowledge.plan, args["topics"]
                    ),
                }
            result = {
                "ok": True,
                "entries": await asyncio.to_thread(
                    self.knowledge.search,
                    args.get("query", ""),
                    args.get("category", ""),
                    args.get("limit", 6),
                    "explicit development lookup",
                    args.get("include_archived", False),
                ),
            }
            for entry in result["entries"]:
                entry["total_characters"] = len(entry["content"])
                entry.pop("content")
            result["next"] = (
                "Read relevant entry IDs with dev_read_knowledge; follow related keys as needed."
            )
            return result
        if operation == "dev_read_knowledge":
            return {
                "ok": True,
                "entry": await asyncio.to_thread(
                    self.knowledge.read, args["entry_id"], args.get("offset", 0)
                ),
            }
        if operation == "dev_save_knowledge":
            entry = await asyncio.to_thread(self.knowledge.save_with_profile, args)
            current = await asyncio.to_thread(self.knowledge.read, entry)
            return {"ok": True, "entry_id": entry, "revision": current["revision"]}
        if operation == "dev_knowledge_categories":
            if args.get("old") or args.get("new"):
                await asyncio.to_thread(
                    self.knowledge.rename_category, args.get("old"), args.get("new")
                )
            return {
                "ok": True,
                "categories": await asyncio.to_thread(self.knowledge.categories),
            }
        if operation == "dev_read_file":
            result = await self.files.read_file(
                plugin, args["file_path"], args.get("start", 1), args.get("lines", 200)
            )
            result["references"] = await self.references(
                f"{args['file_path']} {args.get('topic', '')}", "before editing"
            )
            return {"ok": True, **result}
        if operation == "dev_list_files":
            names = await asyncio.to_thread(self.files.files, plugin)
            offset = max(0, int(args.get("offset", 0)))
            return {
                "ok": True,
                "files": names[offset : offset + 200],
                "total": len(names),
            }
        if operation in {"dev_write_file", "dev_write_files"}:
            changes = args["changes"] if operation == "dev_write_files" else [args]
            refs = await self.references(
                args["topic"] + " " + " ".join(c["file_path"] for c in changes),
                "write validation",
            )
            result = await asyncio.to_thread(self.files.apply, plugin, changes)
            return {
                **result,
                "references": refs,
                "operation_time": time.time(),
                "next": "Call dev_load_plugin for explicit load/reload status; test the changed behavior.",
            }
        if operation == "dev_backup_plugin":
            return {
                "ok": True,
                "backup_id": await asyncio.to_thread(
                    self.files.snapshot, plugin, args.get("reason", "manual")
                ),
            }
        if operation == "dev_list_backups":
            return {
                "ok": True,
                "backups": await asyncio.to_thread(self.files.history, plugin),
            }
        if operation == "dev_rollback_plugin":
            return await asyncio.to_thread(
                self.files.rollback, plugin, args["backup_id"]
            )
        if operation == "dev_check_logs":
            return {
                "ok": True,
                "logs": self.logs.get_logs(
                    args.get("lines", self.config.get("log_lines_default", 100)),
                    plugin,
                    args.get("since", 0),
                ),
                "note": "No logs does not prove success or failure. Use load results and behavior tests.",
            }
        if operation == "dev_list_plugins":
            return {
                "ok": True,
                "plugins": [
                    {
                        "name": p.name,
                        "directory": p.root_dir_name,
                        "active": p.activated,
                    }
                    for p in self.context.get_all_stars()
                ],
            }
        manager = self.context._star_manager
        if operation == "dev_load_plugin":
            root = self.files.path(plugin)
            if Path(manager.plugin_store_path).resolve() != self.files.base_path:
                raise ValueError(
                    "Configured plugin directory differs from the host plugin store"
                )
            if not ((root / "main.py").is_file() or (root / f"{plugin}.py").is_file()):
                raise ValueError("No supported plugin entry point")
            metadata = next(
                (p for p in self.context.get_all_stars() if p.root_dir_name == plugin),
                None,
            )
            if metadata and not metadata.activated:
                raise ValueError(
                    "Plugin is disabled; enable it in AstrBot before reloading"
                )
            if metadata:
                success, error = await manager.reload(metadata.name)
            elif plugin in manager.failed_plugin_dict:
                success, error = await manager.reload_failed_plugin(plugin)
            else:
                async with manager._pm_lock:
                    success, error = await manager.load(specified_dir_name=plugin)
            result = {"ok": bool(success), "error": error, "behavior_tested": False}
            if not success:
                result["experience_id"] = await asyncio.to_thread(
                    self.knowledge.record_failure,
                    "plugin load",
                    plugin,
                    RuntimeError(str(error)),
                )
                if args.get("rollback_backup_id"):
                    result["rollback"] = await asyncio.to_thread(
                        self.files.rollback, plugin, args["rollback_backup_id"]
                    )
                    if plugin in manager.failed_plugin_dict:
                        restored, restore_error = await manager.reload_failed_plugin(
                            plugin
                        )
                    else:
                        async with manager._pm_lock:
                            restored, restore_error = await manager.load(
                                specified_dir_name=plugin
                            )
                    result["restored_load"] = {"ok": restored, "error": restore_error}
            return result
        if operation == "dev_uninstall_plugin":
            metadata = self.context.get_registered_star(plugin)
            if not metadata or metadata.reserved:
                raise ValueError("Plugin does not exist or is built in")
            backup = await asyncio.to_thread(
                self.files.snapshot, metadata.root_dir_name, "before uninstall"
            )
            await manager.uninstall_plugin(
                plugin_name=plugin, delete_config=False, delete_data=False
            )
            return {
                "ok": True,
                "backup_id": backup,
                "directory": metadata.root_dir_name,
            }
        raise ValueError("Unknown developer operation")

    def shutdown(self):
        self.closed = True
        if self.logs:
            self.logs.shutdown()


@dataclass
class DeveloperTool(FunctionTool[AstrAgentContext]):
    runtime: object = Field(default=None, exclude=True)

    async def call(self, context, **kwargs):
        result = await self.runtime.execute(self.name, context.context.event, kwargs)
        return json.dumps(result, ensure_ascii=False)


def create_tools(runtime):
    string = {"type": "string"}
    integer = {"type": "integer"}
    file_change = {
        "type": "object",
        "properties": {
            "file_path": string,
            "content": string,
            "expected_sha256": string,
        },
        "required": ["file_path", "content"],
    }
    definitions = [
        (
            "dev_read_knowledge",
            "Read a knowledge entry in 6000-character pages by ID from search results.",
            {"entry_id": integer, "offset": integer},
            ["entry_id"],
        ),
        (
            "dev_read_file",
            "Read code before editing; returns SHA256 and relevant references/lessons.",
            {
                "plugin_name": string,
                "file_path": string,
                "topic": string,
                "start": integer,
                "lines": integer,
            },
            ["plugin_name", "file_path"],
        ),
        (
            "dev_list_files",
            "List source files in one plugin, excluding repository and cache directories.",
            {"plugin_name": string, "offset": integer},
            ["plugin_name"],
        ),
        (
            "dev_write_file",
            "Validate, back up and atomically replace one full file. Read first and pass its SHA256. "
            "Search knowledge BEFORE composing code; topic describes APIs being changed. Call dev_load_plugin afterwards.",
            {"plugin_name": string, "topic": string, **file_change["properties"]},
            ["plugin_name", "file_path", "content", "topic"],
        ),
        (
            "dev_write_files",
            "Validate all changes then back up once and replace files. Recover files on write failure. "
            "Search references before composing. Hot reload may observe intermediate files; use explicit load verification.",
            {
                "plugin_name": string,
                "topic": string,
                "changes": {"type": "array", "items": file_change},
            },
            ["plugin_name", "topic", "changes"],
        ),
        (
            "dev_load_plugin",
            "Load or reload ONE directory; use returned status rather than guessing logs. "
            "Optional rollback_backup_id restores code and retries load on failure. Behavior still needs testing.",
            {"plugin_name": string, "rollback_backup_id": string},
            ["plugin_name"],
        ),
        (
            "dev_check_logs",
            "Read unified host logs by plugin/time. Missing logs is inconclusive. "
            "Record diagnosed errors and verified fixes using dev_save_knowledge with kind=experience.",
            {"plugin_name": string, "lines": integer, "since": {"type": "number"}},
            [],
        ),
        (
            "dev_list_plugins",
            "List plugin metadata names, directories and enabled status.",
            {},
            [],
        ),
        (
            "dev_uninstall_plugin",
            "Back up then uninstall by METADATA NAME, retaining configuration/data.",
            {"plugin_name": string},
            ["plugin_name"],
        ),
        (
            "dev_backup_plugin",
            "Create a full source/resource snapshot; excludes .git and caches.",
            {"plugin_name": string, "reason": string},
            ["plugin_name"],
        ),
        (
            "dev_list_backups",
            "List the last 50 snapshots for a plugin directory.",
            {"plugin_name": string},
            ["plugin_name"],
        ),
        (
            "dev_rollback_plugin",
            "Restore source/resources by backup ID, first backing up current files. "
            "Files created after the snapshot are removed; data/config outside source are retained. Reload afterwards.",
            {"plugin_name": string, "backup_id": string},
            ["plugin_name", "backup_id"],
        ),
        (
            "dev_search_knowledge",
            "Search categorized development docs AND error lessons BEFORE writing relevant code. "
            "Pass topics for independent development facets, or query for one lookup. "
            "Returns indexes; read relevant IDs fully and follow related entries.",
            {
                "query": string,
                "category": string,
                "limit": integer,
                "topics": {
                    "type": "array",
                    "items": string,
                    "minItems": 1,
                    "maxItems": 8,
                },
                "include_archived": {"type": "boolean"},
            },
            [],
        ),
        (
            "dev_save_knowledge",
            "Add/correct documentation or summarize errors with evidence. "
            "Use a stable key to revise entries; kind is reference or experience. "
            "Read existing entries first and pass expected_revision; update only changed fields. "
            "New entries need category/title/content/source. Correct the existing key instead of duplicating it. "
            "Include symptom, cause, fix, actual validation and version. Mark untested fixes as pending. "
            "New category names create categories automatically.",
            {
                "expected_revision": integer,
                **dict.fromkeys(
                    (
                        "key",
                        "category",
                        "title",
                        "content",
                        "source",
                        "kind",
                        "summary",
                        "replacement",
                        "applicability",
                    ),
                    string,
                ),
                **{
                    field: {"type": "array", "items": string}
                    for field in ("aliases", "apis", "related")
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "archived", "superseded"],
                },
                "verification": {
                    "type": "string",
                    "enum": ["documented", "source_checked", "tested", "observed"],
                },
            },
            ["key"],
        ),
        (
            "dev_knowledge_categories",
            "List categories or rename/merge old into new while retaining revision history.",
            {"old": string, "new": string},
            [],
        ),
    ]
    return [
        DeveloperTool(
            name=name,
            description=description,
            runtime=runtime,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
        )
        for name, description, properties, required in definitions
    ]
