"""Add documented corrections grounded in the checked AstrBot host source."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.knowledge import KnowledgeStore  # noqa: E402


def seed(path):
    store = KnowledgeStore(path)
    entries = [
        (
            "sqlite-close",
            "错误经验/数据存储",
            "SQLite 事务上下文不会自动关闭连接",
            "症状：Windows 临时数据库清理出现 WinError 32。原因：with sqlite3.connect(...) 只提交或回滚事务，"
            "不会关闭连接。修复：使用 contextlib.closing 包装连接，并在其内部 with db 管理事务。"
            "验证：修复前12项测试均因清理时数据库占用失败；修复后12项全部通过，临时数据库正常删除。",
            "self_iterative_core 1.2.0: utils/knowledge.py:connect; utils/file_manager.py; tests/test_core.py",
            "experience",
        ),
        (
            "storage-path-return-type",
            "数据存储",
            "路径函数返回字符串，应使用 Path 包装",
            "原 storage.md 示例直接对 get_astrbot_data_path() 使用 / 运算，会导致 TypeError。"
            "AstrBot 4.29.1 返回 str。正确示例：\n"
            "from pathlib import Path\nfrom astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path\n"
            "data_dir = Path(get_astrbot_plugin_data_path()) / self.name\ndata_dir.mkdir(parents=True, exist_ok=True)\n"
            "写入大文件在线程中执行。运行数据与知识补充放在此目录，避免插件更新覆盖。",
            "AstrBot 1b10813c: astrbot/core/utils/astrbot_path.py:get_astrbot_data_path/get_astrbot_plugin_data_path",
            "reference",
        ),
        (
            "host-reload-rules",
            "插件结构与生命周期",
            "热重载范围与显式重载结果",
            "ASTRBOT_RELOAD=1 启用监听，PythonFilter 只监听 Python 文件；YAML/JSON 更新需要显式重载。"
            "await context._star_manager.reload(metadata.name) 返回 (success,error_message)。这是宿主内部接口，"
            "需要版本约束。调用前确认 metadata 存在；当前宿主对不存在的名称可能退化为重载全部。"
            "开始 Loading 日志不等于完成，成功加载也不等于功能测试通过。新插件按目录名 load；"
            "失败插件按目录名 reload_failed_plugin。多文件写入的中间状态可能被监听，需检查最终加载结果。",
            "AstrBot 1b10813c: astrbot/core/star/star_manager.py:_watch_plugins_changes/reload/load",
            "reference",
        ),
        (
            "config-getattr-deque",
            "错误经验/插件配置",
            "缺失配置通过 getattr 读取导致日志缓冲无上限",
            "症状：deque(maxlen=None)。原因：AstrBotConfig.__getattr__ 对缺失项返回 None，getattr 默认参数不生效。"
            "修复：config.get('log_max_history',1000)，再校验正整数。"
            "验证：提取 AstrBot 4.29.1 的真实 __getattr__ 与旧日志管理器组合，观察 maxlen=None；"
            "新实现测试缺失配置时 maxlen=1000。",
            "AstrBot 1b10813c: astrbot/core/config/astrbot_config.py:__getattr__; tests/test_core.py",
            "experience",
        ),
        (
            "logger-propagation",
            "错误经验/日志诊断",
            "独立插件 Logger 不向父 Logger 传播",
            "症状：dev_check_logs 缺少其他插件的错误。原因：Star 使用独立 astrbot.plugin.<name> Logger，"
            "GetLogger 设置 propagate=False，仅监听 root/astrbot 会遗漏。优先读取统一 LogBroker；"
            "内部 broker 不可用时挂载现有独立 Logger，卸载时移除 Handler。"
            "日志重复只在传播链同时挂载时发生，不能假定宿主所有日志重复。",
            "AstrBot 1b10813c: astrbot/core/log.py:GetLogger/get_plugin_logger; tests/test_core.py",
            "experience",
        ),
        (
            "module-global-lifecycle",
            "错误经验/生命周期",
            "星号导入不会跟随模块变量重新赋值",
            "旧 main.py 使用 from .utils.tools import * 导入 None，初始化后 main.log_manager 仍是 None，"
            "导致 terminate 跳过清理。使用实例持有 Runtime/Handler，terminate 关闭当前实例。"
            "工具注册使用 Context.add_llm_tools；当前宿主会把 utils.tools 下工具归属到主模块，无需手写归属路径。",
            "AstrBot 1b10813c: astrbot/core/star/context.py:_resolve_tool_handler_module_path; plugin main.py",
            "experience",
        ),
        (
            "knowledge-workflow",
            "开发环境与发布",
            "按任务检索文档与经验，验证后补充知识",
            "编写前按实际 API 和任务调用 dev_search_knowledge；相关内容分块读取，引用返回的条目 ID。"
            "读取源码获得 SHA256，修改现有文件时提交 expected_sha256 防止覆盖别人修改。"
            "发现文档缺漏调用 dev_save_knowledge，提供来源、适用版本和验证结果；同一 key 更新保留修订历史。"
            "实际工具失败自动记为待验证经验；修复后补全症状、原因、方案、测试结果并移入对应分类。"
            "dev_knowledge_categories 可查看、重命名或合并分类；新条目可直接使用新分类。",
            "self_iterative_core 1.2.0: utils/knowledge.py; utils/tools.py",
            "reference",
        ),
        (
            "live-plugin-behavior-test",
            "错误经验/插件测试",
            "不要直接导入插件源码进行行为测试",
            "症状：使用 importlib 直接加载插件 main.py 时出现 No module named 'astrbot'，或类对象与宿主加载状态不一致。原因：直接导入绕过了 AstrBot 的插件管理器、模块路径和实例生命周期。"
            "修复：先调用 dev_load_plugin，再通过 dev_test_plugin 检查 AstrBot 已注册的活动实例和 Handler；需要端到端验证时再发送真实测试事件。"
            "验证：dev_test_plugin 返回 loaded_and_registered、behavior_tested=true，并报告实时实例类名和 Handler 数量；未加载时明确返回 not_loaded。",
            "self_iterative_core 1.4.6: utils/tools.py:dev_load_plugin/dev_test_plugin; AstrBot core.star.star_manager",
            "experience",
        ),
    ]
    for key, category, title, content, source, kind in entries:
        store.save(f"supplement:{key}", category, title, content, source, kind)
    print(f"Added {len(entries)} verified-source supplements")


if __name__ == "__main__":
    seed(sys.argv[1])
