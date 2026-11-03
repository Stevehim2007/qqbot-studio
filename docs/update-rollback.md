# 更新通道与版本回滚

控制台右上角「检查更新」打开版本面板。所有操作都只改代码仓库，不写入实例数据目录。

## 通道

| 通道 | 来源 | 规则 |
| --- | --- | --- |
| 稳定版 stable | GitHub 正式发布 / `origin/main` | 只允许 `git merge --ff-only` 快进；分叉或领先提交会被拒绝 |
| 测试版 test | 显式 allowlist（`qqbot_studio/updater.py` 的 `TEST_BRANCHES` / `TEST_TAGS`） | 默认只登记 `feat/long-term-memory-test`；不接受任意分支、提交号或 URL |

## 回滚

- 可选择**当前大版本内任意正式标签**：`v4.0.0 … v4.y.z`，不限于上一个稳定版。
- 标签必须是形如 `v<major>.<minor>.<patch>` 的正式发布标签；`v4.1.1-backup-20260101` 之类后缀标签、分支名、提交号、URL 全部拒绝。
- 跨大版本回滚（例如 v4.x → v3.x）默认拒绝，需要人工按大版本升级/迁移文档处理。
- 列表来自 `git for-each-ref refs/tags`，按版本倒序，显示日期、短提交和标签说明。

## 安全流程

1. 获取独占锁 `~/.local/share/qqbot-studio/update.lock`（30 分钟过期），同一时间只允许一个更新/回滚。
2. 要求 `git status --porcelain` 为空；工作区有未提交修改时拒绝。
3. 校验目标标签存在且属于当前大版本。
4. schema 兼容检查：对比目标代码中的 `CONFIG_SCHEMA_VERSION` / `STUDIO_SCHEMA_VERSION` 与本地 `studio.json`、实例 `config.json` 的 `schema_version`。目标更低时默认拒绝，只有显式 `allow_schema_downgrade` 才继续，绝不静默降级。
5. 备份到 `~/.local/share/qqbot-studio/backups/<时间戳>-<标签>/`：
   - `code.bundle`：`git bundle create --all` 的完整代码备份；
   - `data/studio.json`、`data/config.json`、`data/secrets.json`、`data/.env`；
   - `data/instances/<id>/{instance.json,config.json,secrets.json,.env,compose.yml}`；
   - `manifest.json` 记录文件清单与各实例数据目录的大小/文件数（不复制 GB 级数据：数据目录从不被写入，测试断言其字节不变）。
   仅保留最近 8 份备份。
6. 执行 `git checkout --detach <标签>`（稳定版更新为 `merge --ff-only`），并核对 HEAD 提交。
7. 失败时自动切回原分支/提交；仍失败则保留现场并在错误中说明。
8. 成功后启动 `scripts/auto-update.sh <data-dir> restart`：跳过 `git pull`，同步内置插件并重启管理服务。
9. 写入 `~/.local/share/qqbot-studio/update-history.json`（最多 50 条，新的在前），错误信息用正则脱敏 token、password、API key、URL 凭据。

## 历史记录格式

```json
{
  "at": "2026-11-03T12:34:56+08:00",
  "action": "rollback",
  "channel": "rollback",
  "from": {"version": "4.2.0", "commit": "abc1234", "ref": "main", "tag": "v4.2.0"},
  "to": {"version": "4.0.4", "tag": "v4.0.4", "commit": "def5678"},
  "result": "success",
  "backup": "/home/user/.local/share/qqbot-studio/backups/20261103-123400-before-rollback-v4.0.4",
  "message": ""
}
```

`result` 取值：`success`、`failed`、`restored`（切换失败但已恢复原版本）。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/update/status` | 当前版本/提交/大版本/工作区/锁/通道 |
| GET | `/api/update/versions?channel=stable\|test` | 可选版本；stable 带 `same_major`、`current` 标记 |
| GET | `/api/update/history` | 最近 50 条记录（已脱敏） |
| POST | `/api/update/apply` | `{"channel": "stable"}` 或 `{"channel": "test", "test_ref": "<allowlist 名>"}` |
| POST | `/api/update/rollback` | `{"target": "v4.0.4", "allow_schema_downgrade": false}` |

接口只接受 JSON 参数；`target` / `test_ref` 必须是精确名称，服务端仍会二次校验，浏览器无法注入任意 Git ref。

## 限制

- Windows 版没有同一套网页更新/回滚，需按 Windows 仓库说明手动操作。
- 不自动 `git push`、不创建 release、不删除标签。
- 回滚不会降级 Docker 基础镜像，也不会改写实例 `.env` 之外的 AstrBot 数据库。
