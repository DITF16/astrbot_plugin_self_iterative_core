## 📋 **更新日志 (Changelog)**

### v1.1.1
- 添加更新日志

### v1.1.0
- ✨ 新增白名单权限控制功能
- 支持在管理面板配置 `enable_whitelist` 和 `whitelist_users`
- 未授权用户调用开发者工具时将进行提示

### v1.0.5
- 🐛 修复 LogManager 多线程竞态条件
- 为 `log_buffer` 添加线程锁，防止读写冲突引发 RuntimeError

### v1.0.4
- 🐛 清理 LogManager 资源管理
- 修复插件卸载后的内存泄漏和重复日志问题

### v1.0.3
- 🐛 修复 LoadPluginTool 同步遍历问题

### v1.0.2
- 🐛 修复 FileManager 同步 I/O 阻塞问题

### v1.0.1
- 📝 完善工具使用规则