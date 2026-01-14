from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig
from .utils.tools import *


class PluginDeveloper(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

        init_managers(self.config)

        self.context.add_llm_tools(
            WriteFileTool(),
            ReadFileTool(),
            ListFilesTool(),
            LoadPluginTool(),
            CheckLogsTool(),
            ListPluginsTool(),
            UninstallPluginTool()
        )

    @filter.command("自迭代测试")
    async def ping(self, event: AstrMessageEvent):
        yield event.plain_result("自迭代核心已加载，配置已生效✅️")