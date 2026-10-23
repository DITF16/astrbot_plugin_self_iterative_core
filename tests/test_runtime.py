"""Exercise tool schemas and runtime flows with isolated AstrBot interfaces."""

import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Generic, TypeVar
from unittest.mock import patch

from pydantic.dataclasses import dataclass

T = TypeVar("T")


@dataclass
class FunctionTool(Generic[T]):
    name: str
    description: str
    parameters: dict


modules = {
    name: types.ModuleType(name)
    for name in (
        "astrbot",
        "astrbot.core",
        "astrbot.core.agent",
        "astrbot.core.agent.tool",
        "astrbot.core.astr_agent_context",
        "astrbot.core.log",
    )
}
modules["astrbot.core.agent.tool"].FunctionTool = FunctionTool
modules["astrbot.core.astr_agent_context"].AstrAgentContext = object
modules["astrbot.core.log"].LogManager = types.SimpleNamespace(_log_broker=None)
with patch.dict(sys.modules, modules):
    tools = importlib.import_module("utils.tools")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = types.SimpleNamespace(get_all_stars=lambda: [])
        self.event = types.SimpleNamespace(get_sender_id=lambda: "123")
        self.runtime = tools.DeveloperRuntime(
            self.context,
            {"enable_whitelist": True, "whitelist_users": ["123"]},
            self.root / "data",
            self.root / "plugins",
        )
        self.addCleanup(self.runtime.shutdown)

    async def test_all_tool_schemas_construct_and_dispatch(self):
        registered = tools.create_tools(self.runtime)
        self.assertEqual(len({t.name for t in registered}), 15)
        context = types.SimpleNamespace(context=types.SimpleNamespace(event=self.event))
        result = await next(t for t in registered if t.name == "dev_list_plugins").call(
            context
        )
        self.assertTrue(json.loads(result)["ok"])

    async def test_permission_rejected_without_mutation(self):
        self.event.get_sender_id = lambda: "other"
        result = await self.runtime.execute("dev_save_knowledge", self.event, {})
        self.assertFalse(result["ok"])
        self.assertEqual(self.runtime.knowledge.categories(), [])

    async def test_failed_write_records_observed_experience(self):
        result = await self.runtime.execute(
            "dev_write_file",
            self.event,
            {
                "plugin_name": "demo",
                "file_path": "main.py",
                "content": "def (",
                "topic": "command",
            },
        )
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["experience_id"])
        row = self.runtime.knowledge.read(result["experience_id"])
        self.assertEqual(row["category"], "错误经验/待验证")
        self.assertFalse((self.root / "plugins/demo/main.py").exists())

    async def test_load_failure_restores_backup_and_retries(self):
        self.runtime.files.apply("demo", [{"file_path": "main.py", "content": "x = 1"}])
        result = self.runtime.files.apply(
            "demo",
            [
                {
                    "file_path": "main.py",
                    "content": "x = 2",
                    "expected_sha256": (
                        await self.runtime.files.read_file("demo", "main.py")
                    )["sha256"],
                }
            ],
        )

        class Manager:
            plugin_store_path = self.root / "plugins"
            failed_plugin_dict = {}
            _pm_lock = asyncio.Lock()
            attempts = 0

            async def load(inner, **kwargs):
                inner.attempts += 1
                return (
                    inner.attempts == 2,
                    None if inner.attempts == 2 else "injected load error",
                )

        manager = Manager()
        self.context._star_manager = manager
        loaded = await self.runtime.execute(
            "dev_load_plugin",
            self.event,
            {"plugin_name": "demo", "rollback_backup_id": result["backup_id"]},
        )
        self.assertFalse(loaded["ok"])
        self.assertTrue(loaded["restored_load"]["ok"])
        self.assertEqual((self.root / "plugins/demo/main.py").read_text(), "x = 1")

    async def test_shutdown_rejects_stale_tools(self):
        self.runtime.shutdown()
        self.assertFalse(
            (await self.runtime.execute("dev_list_plugins", self.event, {}))["ok"]
        )
