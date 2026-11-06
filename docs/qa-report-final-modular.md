# 最终模块化 QA 报告（bot-4 / qa-core）

日期：2026-11-06  
环境：Studio `4.3.0-beta.4`，AstrBot `4.29.3`，实例 `bot-4`（qa-core，容器 `qqbot-bot-4-astrbot`）。生产 `bot-1` 未做任何插件/配置变更。

## 1. 部署

- 七个 ZIP 由 `qqbot-plugins` 发布分支构建（v4.3.0-beta.4），上传后按 `catalog.json` 逐包校验 SHA256 与大小：7/7 通过。
- bot-4 原插件目录与配置先备份：
  - `~/qqbot-bot4-plugins-backup-20261106-121218.tar.gz`
  - `~/qqbot-bot4-config-backup-20261106-121218/`
  - 配置迁移前再次备份：`config/.plugin-final-migration-backup-1793938604-394cd7/`
- 原子替换七个插件目录后重启 AstrBot。启动日志 14 条插件加载/描述记录（7×2），无 `ERRO`/Traceback。
- 重启后插件目录只含：`qqbot_core_reply`、`qqbot_safe_tools`、`qqbot_web_reader`、`qqbot_image_search`、`qqbot_food`、`qqbot_stickers`、`qqbot_memory_commands`。

## 2. 运行时单测（容器内真实 AstrBot 4.29.3）

逐插件执行 `python -m unittest discover -s tests`：

| 插件 | 测试数 | 结果 |
|---|---:|---|
| qqbot_core_reply | 223 | OK |
| qqbot_safe_tools | 177 | OK |
| qqbot_web_reader | 99 | OK |
| qqbot_image_search | 177 | OK |
| qqbot_food | 198 | OK |
| qqbot_stickers | 171 | OK |
| qqbot_memory_commands | 127 | OK |
| **合计** | **1172** | **全绿** |

## 3. 数据保留

`qqbot_stickers` 从旧索引 bootstrap 后：65 个分类、71 张收藏全部保留（旧目录 71 个条目）。修复了 bootstrap 在分类数超过配置上限时抛 `category limit reached` 导致插件初始化失败的问题；新增回归测试 `test_service_bootstrap.py`。

## 4. 配置迁移（bot-4 实测）

- dry-run：源为 `qqbot_enhancer_config.json`；9 个无目标键被列入 `unmapped_source_keys`（`web_auto_fetch_*` 5 项、`conversation_auto_create_enabled`、`ambient_group_reply_probability`、`ai_unified_tool_protocol_enabled`、`safe_tools_max_categories`），无静默丢弃。
- `--apply`：写入七个目标配置，备份 8 个旧/目标文件；已有非默认值作为 conflict 保留（core_reply 的 `system_prompt`/`llm_timeout_seconds`、web_reader 3 项、image_search 1 项、memory 1 项）。重启后插件加载正常。

## 5. 真实网络业务冒烟（容器内）

- Food：`/api/tags` 返回标签列表；`/api/restaurants` 返回餐厅记录（固定 `https://food.dlsz.top`）。
- Web Reader：抓取 `https://example.com` 成功；`http://127.0.0.1:6185/`、`http://169.254.169.254/` 均以 `blocked_ip` 拒绝。
- Image Search：关键词「千早爱音」经 Bing 返回 15 个候选。

## 6. 未覆盖项

- **QQ 消息级 E2E**：bot-4 未启动 SnowLuma/QQ 适配器（避免与生产 bot-1 争用账号），未做真实群/私聊消息回环。
- **GitHub Release**：v4.3.0-beta.4 的 ZIP 尚未上传 Release（无 GitHub API token），市场下载 URL 暂不可用；QA 使用本地 ZIP 直接部署。
- **bot-1 生产切换**：按约定未执行；其 enhancer/core_full 代码、配置和数据保持原样。

## 7. 回滚

将备份 tar 解回 bot-4 插件目录，或从 `config/.plugin-final-migration-backup-*` 还原配置，然后重启容器即可；bot-1 无变更无需回滚。
