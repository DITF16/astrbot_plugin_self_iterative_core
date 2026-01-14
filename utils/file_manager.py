import os


class FileManager:
    def __init__(self, base_path: str = "./data/plugins"):
        self.base_path = base_path

    def _get_full_path(self, plugin_name: str, file_path: str) -> str:
        full_path = os.path.join(self.base_path, plugin_name, file_path)
        return os.path.abspath(full_path)

    def write_file(self, plugin_name: str, file_path: str, content: str) -> str:
        full_path = self._get_full_path(plugin_name, file_path)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"成功写入文件: {plugin_name}/{file_path}"
        except Exception as e:
            return f"写入文件失败: {str(e)}"

    def read_file(self, plugin_name: str, file_path: str) -> str:
        full_path = self._get_full_path(plugin_name, file_path)
        try:
            if not os.path.exists(full_path):
                return "文件不存在。"
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"读取文件失败: {str(e)}"

    def list_files(self, plugin_name: str) -> str:
        plugin_path = os.path.join(self.base_path, plugin_name)
        if not os.path.exists(plugin_path):
            return "插件目录不存在。"
        file_list = []
        for root, dirs, files in os.walk(plugin_path):
            for file in files:
                rel_path = os.path.relpath(os.path.join(root, file), plugin_path)
                file_list.append(rel_path)
        return "\n".join(file_list) if file_list else "目录为空。"