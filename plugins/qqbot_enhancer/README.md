# LEGACY — 不要安装

这是 4.x 时代单体插件 `qqbot_enhancer` 的历史源码，仅用于旧行为回归测试（`tests/test_enhancer_plugin.py`）。

- 5.0 运行时不加载它，也不会由 Studio 插件市场安装。
- 当前替代插件在 `.qqbot-plugins-staging/plugins/`：`qqbot_core_reply`、`qqbot_safe_tools`、`qqbot_memory_commands`、`qqbot_memory_bank` 等。
- 不要把这个目录复制到 AstrBot 的 `data/plugins`；不要从旧实例迁移它的配置。
- 若未来删除历史测试资产，可连同 `tests/test_enhancer_plugin.py` 一起移除。
