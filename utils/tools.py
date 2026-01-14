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

        result = file_manager.write_file(plugin_name, file_path, content)

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
        content = file_manager.read_file(plugin_name, file_path)
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
            result = file_manager.list_files(plugin_name)
            return result
        except Exception as e:
            return f"Error listing files: {str(e)}"


@dataclass
class LoadPluginTool(FunctionTool[AstrAgentContext]):
    name: str = "dev_load_plugin"
    description: str = (
        "Load a NEWLY created plugin into AstrBot. "
        "USAGE RULE: Call this ONLY when you have created a completely NEW plugin folder. "
        "DO NOT call this if you are upgrading/modifying an existing function (AstrBot automatically hot-reloads)."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "plugin_dir_name": {
                    "type": "string",
                    "description": "The directory name of the plugin to load.",
                },
            },
            "required": ["plugin_dir_name"],
        }
    )

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        plugin_dir_name = kwargs.get("plugin_dir_name") or kwargs.get("plugin_dir")

        if not plugin_dir_name:
            return "Error: Missing parameter 'plugin_dir_name'."

        ctx = context.context.context
        event = context.context.event
        star_manager: PluginManager = ctx._star_manager

        if not star_manager:
            return "Error: Could not access PluginManager."

        await _send_tip(context, f"🔄 正在尝试加载插件: {plugin_dir_name} ...")

        try:
            success, error_msg = await star_manager.load(specified_dir_name=plugin_dir_name)
            if success:
                try:
                    chain = MessageChain().message(f"✅ 插件 {plugin_dir_name} 加载成功！")
                    await ctx.send_message(event.unified_msg_origin, chain)
                except Exception:
                    pass
                return f"Plugin '{plugin_dir_name}' loaded successfully! You can now test it."
            else:
                # 加载失败时，使用当前人格分析原因
                try:
                    provider_id = await ctx.get_current_chat_provider_id(event.unified_msg_origin)

                    prompt = (
                        f"The plugin '{plugin_dir_name}' failed to load with the following error:\n"
                        f"{error_msg}\n\n"
                        f"Please act as your current persona. Briefly analyze this error for the user "
                        f"and apologize, then confidently state that you are about to fix the code. "
                        f"Keep it short (under 50 words)."
                    )

                    llm_resp = await ctx.llm_generate(chat_provider_id=provider_id, prompt=prompt)

                    if llm_resp and llm_resp.completion_text:
                        chain = MessageChain().message(f"❌ {llm_resp.completion_text}")
                        await ctx.send_message(event.unified_msg_origin, chain)
                    else:
                        chain = MessageChain().message(f"❌ 加载失败: {error_msg}。正在尝试修复...")
                        await ctx.send_message(event.unified_msg_origin, chain)

                except Exception as e:
                    chain = MessageChain().message(f"❌ 加载失败，正在自动修复... (Error: {str(e)})")
                    await ctx.send_message(event.unified_msg_origin, chain)

                return f"Failed to load plugin '{plugin_dir_name}'. Error: {error_msg}. Please check the logs."

        except Exception as e:
            return f"Exception during loading: {str(e)}"


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
        "   - 'File changed' ONLY (no other msg) = Plugin not active. Call 'dev_load_plugin'. "
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