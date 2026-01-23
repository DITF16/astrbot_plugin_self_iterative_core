import os
import asyncio
from typing import List


class FileManager:
    def __init__(self, base_path: str = "./data/plugins"):
        self.base_path = base_path

    def _get_full_path(self, plugin_name: str, file_path: str) -> str:
        full_path = os.path.join(self.base_path, plugin_name, file_path)
        return os.path.abspath(full_path)

    # ========== 同步内部方法（在线程池中执行） ==========

    def _sync_write_file(self, full_path: str, content: str) -> None:
        """同步写入，仅供内部调用"""
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _sync_read_file(self, full_path: str) -> str:
        """同步读取，仅供内部调用"""
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()

    def _sync_list_files(self, plugin_path: str) -> List[str]:
        """同步遍历目录，仅供内部调用"""
        file_list = []
        for root, dirs, files in os.walk(plugin_path):
            for file in files:
                rel_path = os.path.relpath(os.path.join(root, file), plugin_path)
                file_list.append(rel_path)
        return file_list

    # ========== 异步公开方法 ==========

    async def write_file(self, plugin_name: str, file_path: str, content: str) -> str:
        full_path = self._get_full_path(plugin_name, file_path)
        try:
            await asyncio.to_thread(self._sync_write_file, full_path, content)
            return f"成功写入文件: {plugin_name}/{file_path}"
        except Exception as e:
            return f"写入文件失败: {str(e)}"

    async def read_file(self, plugin_name: str, file_path: str) -> str:
        full_path = self._get_full_path(plugin_name, file_path)
        try:
            exists = await asyncio.to_thread(os.path.exists, full_path)
            if not exists:
                return "文件不存在。"
            content = await asyncio.to_thread(self._sync_read_file, full_path)
            return content
        except Exception as e:
            return f"读取文件失败: {str(e)}"

    async def list_files(self, plugin_name: str) -> str:
        plugin_path = os.path.join(self.base_path, plugin_name)
        try:
            exists = await asyncio.to_thread(os.path.exists, plugin_path)
            if not exists:
                return "插件目录不存在。"
            file_list = await asyncio.to_thread(self._sync_list_files, plugin_path)
            return "\n".join(file_list) if file_list else "目录为空。"
        except Exception as e:
            return f"列出文件失败: {str(e)}"
