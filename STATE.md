# STATE

## Goal

最终模块化测试版：运行时只保留七个独立插件，移除 qqbot_enhancer / qqbot_core_full 的运行时依赖，保留实例 config/data/会话/记忆，生产 bot-1 不做破坏性切换。

## Decisions

- 实施分支：主项目 `feat/plugin-repository-4.3`；插件仓库 `.qqbot-plugins-staging` 的 `feat/final-modular-4.3`；两条 main 均未改动。
- 插件仓库按插件分支开发（feat/final-*），发布分支通过按目录 checkout 汇总，再用 `scripts/build_release.py` 生成 v4.3.0-beta.4 的七个 ZIP、catalog.json、manifest.json。
- 新实例部署不再复制 enhancer：`DeployManager._seed_final_reply_config` 只写 `qqbot_core_reply_config.json` 的初始 system_prompt，并提示从插件市场安装七件套。
- 旧实例迁移用 `scripts/migrate_qqbot_plugins.py`：默认 dry-run；`--apply` 写目标配置并做字节级备份；`--retire` 只移动旧插件代码到 `studio-plugins/retired-plugin-code/<ts>/`，不删除配置/plugin_data/DB/会话/记忆。
- S0 盘点保存在 `migrations/modular-v2-v3/`（265 实际字段、78 入口、23 别名；契约 264 与实测 265 的差异已在 baseline.json 记录）。
- S1/S2：`plugin_state.py` / `plugin_transactions.py` / `plugin_control.py`，固定 RPC、签名、nonce 重放拒绝、owner epoch、崩溃恢复。
- S8：deployer/auto-update/post-update-test 不再安装或编译旧插件；`tests/test_no_legacy_runtime.py` 作为最终门禁。

## Changed files

主项目（feat/plugin-repository-4.3）：
- `qqbot_studio/deployer.py`, `plugin_control.py`, `plugin_state.py`, `plugin_transactions.py`, `plugin_market.py`, `server.py`, `studio.py`
- `qqbot_studio/plugin_final_migration.py`, `scripts/migrate_qqbot_plugins.py`
- `scripts/auto-update.sh`, `scripts/post-update-test.sh`, `README.md`
- `migrations/modular-v2-v3/*`
- `tests/test_owner_epoch.py`, `tests/test_plugin_control.py`, `tests/test_plugin_final_migration.py`, `tests/test_no_legacy_runtime.py`, `tests/test_deployer.py`

插件仓库（feat/final-modular-4.3，v4.3.0-beta.4）：
- safe_tools: registry/auth/confirmation/dispatcher/call_ledger/control_server + 完整 marker 服务
- memory_commands: scope/authorization/backend/commands/provider/store
- stickers: sticker_service（单写者、分类/资产/索引）+ canonical schema
- core_reply / food / web_reader / image_search: ABI v1 provider、分页、HTTP 安全读取/搜索、Food 草稿与提交

## Tests

- 插件仓库：`cd .qqbot-plugins-staging && python3 -m unittest discover -s tests -p 'test_*.py' -v` → 7/7 OK（含七个插件各自单测子进程）。
- 七个插件单测合计：core 223、safe_tools 177、web_reader 99、image_search 177、food 198、stickers 169、memory 127。
- 主项目：`python3 -m unittest discover -s tests -p 'test_*.py' -q` → 840 tests，4 failures + 1 error，全部集中在 `tests/test_assistant_ext.py::AssistantConfigStartTests`；本沙箱把 8764-8799 端口 bind 全部返回 EADDRINUSE（ss 无监听、SO_REUSEADDR/IPv4/IPv6 均失败），属环境限制，非本次改动回归。其余 835 项通过。
- 迁移脚本：`python3 scripts/migrate_qqbot_plugins.py --instance <instance>`（dry-run）。

## Known failures / limits

- 本机没有服务器 SSH 凭据（id_ed25519 被 192.144.206.201 拒绝），QA bot-4 的实机部署与 E2E 尚未执行。
- v4.3.0-beta.4 的 GitHub Release 尚未创建（无 API token），市场下载 URL 暂不可用；QA 可用本地 artifacts ZIP 解压安装。
- `migrations/modular-v2-v3/field-map.json` 的 261 个 blocked 是相对基线插件 schema 的快照；最终 schema 已覆盖大部分字段，退出前需按最终插件重新生成一次盘点。
- 旧实例（bot-1）仍保留 enhancer 代码与配置，属过渡状态；最终切换需在 QA 验收后单独执行。

## Next step

1. 提供服务器 SSH 访问后：把七个 ZIP 解压到 bot-4 实例 `data/astrbot/plugins/`，用迁移脚本 dry-run/apply 生成配置，重启 AstrBot，跑 E7/E8 隔离 E2E。
2. QA 通过后：发布 v4.3.0-beta.4 Release（七个 ZIP），再按 S8 清单对 bot-1 做可回滚切换。
