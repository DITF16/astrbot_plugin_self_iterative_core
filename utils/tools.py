import os
from pydantic import Field
from typing import Optional
from pydantic.dataclasses import dataclass
from astrbot.api.event import MessageChain
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.star.star_manager import PluginManager


from .file_manager import FileManager
from .log_manager import LogManager

file_manager: Optional[FileManager] = None
log_manager: Optional[LogManager] = None
TOOL_CONFIG: dict = {}


def init_managers(config: dict):
    """根据传入的配置初始化管理器"""
    global file_manager, log_manager, TOOL_CONFIG
    TOOL_CONFIG = config if config else {}

    base_path = config.get("plugin_base_dir", "./data/plugins")
    file_manager = FileManager(base_path=base_path)
    if log_manager is not None:
        try:
            log_manager.shutdown()
        except Exception:
            pass

    log_manager = LogManager(config=config)

async def _send_tip(context: ContextWrapper[AstrAgentContext], message: str):
    if not TOOL_CONFIG.get("verbose_steps", True):
        return

    try:
        ctx = context.context.context
        event = context.context.event
        chain = MessageChain().message(message)
        await ctx.send_message(event.unified_msg_origin, chain)
    except Exception:
        pass


@dataclass
class WriteFileTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_write_file"
    description: str = (
        "Write full content to a file to create or modify a plugin. "
        "CRITICAL RULES FOR DEBUGGING & FIXING: "
        "1. STOP! Do NOT guess the code if you are fixing a bug (especially API errors). "
        "2. EXECUTION ORDER: "
        "   (A) Call 'dev_check_logs' to get the traceback. "
        "   (B) Call 'dev_read_file' to inspect the buggy code. "
        "   (C) Call 'astr_kb_search' (if available) to verify the correct API usage. "
        "   (D) [FALLBACK] If 'astr_kb_search' is unhelpful or unavailable, you MUST use a web search tool "
        "       (e.g. 'web_search_tavily', 'fetch_url') to search the official docs at 'https://docs.astrbot.app/'. "
        "       Keywords: '最小实例', '主动消息', '定义 Tool', '注册 Tool'. "
        "   (E) ONLY THEN call 'dev_write_file' to apply the fix. "
        "3. LOG INTERPRETATION RULES (AFTER WRITING): "
        "   (A) NO USER COMMAND IN LOGS: If logs don't show the user's command, it means the user hasn't sent it yet. "
        "       THIS IS NORMAL. It is NOT an error. Do NOT try to fix the code. Just inform the user to test it. "
        "   (B) RELOAD SUCCESS: If logs show 'File changed' AND ('Loading plugin...' OR 'Reloading...'), the fix is applied. "
        "   (C) RELOAD ERROR: If logs show 'File changed' AND an Error/Traceback follows, the fix failed. Try again. "
        "   (D) NOT INSTALLED: If logs show 'File changed' BUT there is NO 'Loading/Reloading' message afterwards, "
        "       it means the plugin is not loaded. You MUST call 'dev_load_plugin' immediately to install it. "
        "4. FOR MODIFICATIONS: You MUST read the file first to preserve existing logic. "
        "5. Writes are atomic; always write the FULL file content."
        "6. MANDATORY VERIFICATION: After calling 'dev_write_file', you MUST immediately call 'dev_check_logs' to verify the execution result based on the 'LOG INTERPRETATION RULES' below."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "plugin_name": {
                    "type": "string",
                    "description": "The name of the plugin directory (e.g., 'Auto_Fly').",
                },
                "file_path": {
                    "type": "string",
                    "description": "Relative file path (e.g., 'main.py').",
                },
                "content": {
                    "type": "string",
                    "description": "The FULL content of the file.",
                },
            },
            "required": ["plugin_name", "file_path", "content"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not file_manager: return "Error: FileManager not initialized."

        plugin_name = kwargs.get("plugin_name")
        file_path = kwargs.get("file_path")
        content = kwargs.get("content")

        if not all([plugin_name, file_path, content]):
            return "Error: Missing required parameters (plugin_name, file_path, or content)."

        await _send_tip(context, f"📝 正在编写文件: {plugin_name}/{file_path} ...")

        result = await file_manager.write_file(plugin_name, file_path, content)

        return (
            f"{result}\n"
            f"[System Hint] File updated. AstrBot is detecting changes.\n"
            f"--> Please call 'dev_check_logs' NOW to verify the reload status based on the 'LOG INTERPRETATION RULES' in the tool description."
        )


@dataclass
class ReadFileTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_read_file"
    description: str = (
        "Read the content of an existing file. "
        "MANDATORY STEP: Always use this tool FIRST when modifying code or analyzing bugs. "
        "Combine the code content with the error logs from 'dev_check_logs' to diagnose issues accurately."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string", "description": "The plugin directory name."},
                "file_path": {"type": "string", "description": "Relative file path."},
            },
            "required": ["plugin_name", "file_path"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not file_manager: return "Error: FileManager not initialized."
        plugin_name = kwargs.get("plugin_name")
        file_path = kwargs.get("file_path")
        await _send_tip(context, f"📖 正在读取文件: {plugin_name}/{file_path} ...")
        content = await file_manager.read_file(plugin_name, file_path)
        return content


@dataclass
class ListFilesTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_list_files"
    description: str = (
        "List all files in a specific plugin directory. "
        "Use this if you need to check the file structure of a plugin directory."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string", "description": "The plugin directory name."},
            },
            "required": ["plugin_name"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not file_manager: return "Error: FileManager not initialized."

        plugin_name = kwargs.get("plugin_name") or kwargs.get("dir_path") or "."
        if "/plugins/" in plugin_name:
            plugin_name = plugin_name.split("/plugins/")[-1]
        plugin_name = plugin_name.lstrip("/").lstrip(".")
        if not plugin_name: plugin_name = "."

        try:
            result = await file_manager.list_files(plugin_name)
            return result
        except Exception as e:
            return f"Error listing files: {str(e)}"


@dataclass
class LoadPluginTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_load_plugin"
    description: str = (
        "Automatically load all uninstalled plugins."
        "WHEN TO USE:"
        "1. After creating a new plugin."
        "2. When the user asks to 'install', 'load', or 'activate' plugin(s)."
        "3. After modifying a plugin, if logs only show '检测到文件变化' but not '正在重载/正在载入'."
        "NO PARAMETERS NEEDED - just call it directly."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        ctx = context.context.context
        event = context.context.event
        star_manager: PluginManager = ctx._star_manager

        if not star_manager:
            return "Error: Could not access PluginManager."

        base_path = TOOL_CONFIG.get("plugin_base_dir", "./data/plugins")

        try:
            all_dirs_on_disk = set()
            for item in os.listdir(base_path):
                item_path = os.path.join(base_path, item)
                if os.path.isdir(item_path):
                    if os.path.exists(os.path.join(item_path, "main.py")) or \
                       os.path.exists(os.path.join(item_path, "metadata.yaml")):
                        all_dirs_on_disk.add(item)
        except Exception as e:
            return f"Error scanning plugin directory: {str(e)}"

        loaded_plugins = ctx.get_all_stars()
        loaded_dir_names = {p.root_dir_name for p in loaded_plugins} if loaded_plugins else set()

        unloaded_dirs = all_dirs_on_disk - loaded_dir_names

        if not unloaded_dirs:
            return (
                "All plugins already loaded. "
                f"({len(loaded_dir_names)} plugins active)"
            )

        results = []
        success_count = 0
        fail_count = 0

        for dir_name in sorted(unloaded_dirs):
            try:
                success, error_msg = await star_manager.load(specified_dir_name=dir_name)
                if success:
                    results.append(f"✅ {dir_name}")
                    success_count += 1
                else:
                    results.append(f"❌ {dir_name}: {error_msg}")
                    fail_count += 1
            except Exception as e:
                results.append(f"❌ {dir_name}: {str(e)}")
                fail_count += 1

        try:
            if success_count > 0:
                msg = f"✅ 已加载 {success_count} 个新插件" + (f"，{fail_count} 个失败" if fail_count else "")
                chain = MessageChain().message(msg)
                await ctx.send_message(event.unified_msg_origin, chain)
        except Exception:
            pass

        return f"Loaded {success_count}, Failed {fail_count}. Details:\n" + "\n".join(results)



@dataclass
class CheckLogsTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_check_logs"
    description: str = (
        "Check recent system logs from memory. "
        "INTERPRETATION RULES: "
        "1. POST-EDIT SILENCE (IMPORTANT): If you just wrote or modified a command and see NO log record of it being run -> This is NORMAL. It means the user hasn't tested it yet. STOP tool usage immediately and wait for user confirmation/testing. "
        "2. MISSING PREFIXES: If logs show a command without a prefix (e.g., 'hello' instead of '/hello') -> This is NORMAL (interception). Do NOT treat this as a bug unless the user explicitly complains. "
        "3. COMMAND USAGE STANDARDS: "
        "   - Basic: Simply use `@filter.command('name')`. Accept this as the correct foundation. "
        "   - Advanced: For 带参指令, 指令组, or 指令别名, DO NOT GUESS. You MUST refer to the Developer Documentation to verify the correct syntax. "
        "4. RELOAD STATUS: "
        "   - 'File changed' + 'Loading/Reloading' = Success. "
        "   - 'File changed' + Error = Fix it. "
        "   - 'File changed' ONLY (no other msg) = Plugin not installed. Call 'dev_load_plugin'. "
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to retrieve (default 100).",
                },
            },
            "required": [],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not log_manager: return "Error: LogManager not initialized."
        default_lines = TOOL_CONFIG.get("log_lines_default", 100)
        lines = kwargs.get("lines")
        if lines is None:
            lines = default_lines

        await _send_tip(context, f"🔍 正在检查最近 {lines} 行日志...")
        logs = log_manager.get_logs(lines)
        # 简单截断防止过长
        if len(logs) > 4000:
            logs = "...(truncated)...\n" + logs[-4000:]
        return f"Recent Logs (In-Memory Intercept):\n{logs}"


@dataclass
class ListPluginsTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_list_plugins"
    description: str = (
        "List all installed and loaded plugins. "
        "Use this tool to find the correct 'plugin_name' (for uninstallation) or 'plugin_dir_name' (for file modification). "
        "If you are looking for a specific feature (e.g. sticker, weather), use this list to identify the responsible plugin."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        ctx = context.context.context
        all_plugins = ctx.get_all_stars()

        if not all_plugins:
            return "No plugins found."

        msg_lines = ["Current Loaded Plugins:"]
        msg_lines.append(f"{'Plugin Name':<30} | {'Dir Name':<30} | {'Status'}")
        msg_lines.append("-" * 80)

        for plugin in all_plugins:
            status = "Active" if plugin.activated else "Disabled"
            msg_lines.append(f"{plugin.name:<30} | {plugin.root_dir_name:<30} | {status}")

        return "\n".join(msg_lines)


@dataclass
class UninstallPluginTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_uninstall_plugin"
    description: str = (
        "Uninstall a plugin by its NAME. "
        "IMPORTANT: You MUST provide the 'plugin_name' (from metadata.yaml), NOT the directory name. "
        "Use 'dev_list_plugins' first if you are unsure about the name. "
        "This keeps plugin config and data by default."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "plugin_name": {
                    "type": "string",
                    "description": "The exact name of the plugin to uninstall (e.g., 'astrbot_plugin_random_emoji_images').",
                },
            },
            "required": ["plugin_name"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        plugin_name = kwargs.get("plugin_name")

        if not plugin_name:
            return "Error: Missing parameter 'plugin_name'."

        ctx = context.context.context
        star_manager: PluginManager = ctx._star_manager

        if not star_manager:
            return "Error: Could not access PluginManager."

        try:
            # 调用卸载，默认保留数据
            await star_manager.uninstall_plugin(plugin_name=plugin_name, delete_config=False, delete_data=False)
            return f"Plugin '{plugin_name}' uninstalled successfully."
        except Exception as e:
            return f"Failed to uninstall plugin '{plugin_name}': {str(e)}"