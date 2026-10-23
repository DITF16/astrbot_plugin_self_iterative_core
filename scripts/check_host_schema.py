"""Validate tool construction against host classes without booting AstrBot."""

import ast
import sys
import types
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any, Generic, TypeVar
from unittest.mock import patch

import jsonschema
from pydantic import Field, model_validator
from pydantic.dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check(host_root):
    """Extract and exercise the exact tool dataclasses from the selected host.

    Args:
        host_root: AstrBot source checkout root.

    Returns:
        Number of constructed and validated developer tools.
    """
    source = ast.parse(
        (Path(host_root) / "astrbot/core/agent/tool.py").read_text(encoding="utf-8")
    )
    classes = [
        node
        for node in source.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"ToolSchema", "FunctionTool"}
    ]
    module = types.ModuleType("host_tool_validation")
    sys.modules[module.__name__] = module
    ns = module.__dict__
    ns.update(
        {
            "dataclass": dataclass,
            "Field": Field,
            "model_validator": model_validator,
            "Generic": Generic,
            "TContext": TypeVar("TContext"),
            "ParametersType": dict,
            "jsonschema": jsonschema,
            "Any": Any,
            "Callable": Callable,
            "Awaitable": Awaitable,
            "AsyncGenerator": AsyncGenerator,
            "MessageEventResult": object,
            "ToolExecResult": str,
        }
    )
    code = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *classes,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(code), "host_tool_classes", "exec"), ns)
    modules = {
        name: types.ModuleType(name)
        for name in (
            "astrbot",
            "astrbot.core",
            "astrbot.core.agent",
            "astrbot.core.agent.tool",
            "astrbot.core.astr_agent_context",
        )
    }
    modules["astrbot.core.agent.tool"].FunctionTool = ns["FunctionTool"]
    modules["astrbot.core.astr_agent_context"].AstrAgentContext = object
    with patch.dict(sys.modules, modules):
        from utils.tools import create_tools

        registered = create_tools(object())
        for tool in registered:
            jsonschema.Draft202012Validator.check_schema(tool.parameters)
        return len(registered)


if __name__ == "__main__":
    print(f"Actual host tool classes: {check(sys.argv[1])} tool schemas PASS")
