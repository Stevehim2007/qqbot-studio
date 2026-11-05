# 插件市场 v1（测试版基础）

> 状态：`4.3.0-beta.1` 测试分支基础实现。正式的七插件拆分尚未开始；`qqbot_enhancer`
> 继续是主回复与 safe marker 的唯一 owner。本文只描述本测试版已经落地的插件安装/更新/删除、
> 分组和配置迁移预览基础。

## 1. 边界与安全规则

- 只使用 Python 标准库；不运行 `pip`，不修改 `site-packages`，不执行插件包内代码。
- 插件代码只写入实例目录：
  `data/astrbot/plugins/<plugin_id>`。
- 配置 `data/astrbot/config/<plugin_id>_config.json`、`plugin_data/`、AstrBot 数据库、
  会话和其它实例文件不属于市场代码目录，安装/更新/删除都不会删除它们。
- 安装包必须提供固定 SHA-256；远程来源只接受受信 HTTPS 主机。
- ZIP 必须只有唯一顶层目录，且目录名与 manifest `id` 完全一致；拒绝路径穿越、
  绝对路径、反斜杠、重复/大小写冲突条目、符号链接、特殊文件、加密条目和异常压缩比。
- 安装/更新先解压到实例内的暂存区，再原子替换；已有代码在替换前备份到
  `studio-plugins/backups/plugins/<id>/`。
- 只有市场登记表中的插件目录可以被删除。`qqbot_enhancer` 是保留 ID，市场不能安装、
  覆盖、更新或删除它。
- 删除插件成功后，只清理 `studio-plugins/state.sqlite3` 中的分组关系；不会操作任何
  插件代码目录以外的文件。
- 分组删除只执行 SQLite `DELETE`，绝不调用文件删除函数。

## 2. 固定市场来源

默认读取实例内：

```text
<instance>/studio-plugins/market/catalog.json
<instance>/studio-plugins/market/index.json
<instance>/studio-plugins/market/manifest.json
```

也可以固定一个受信 HTTPS URL，放在：

```text
<instance>/studio-plugins/market/source.json
```

```json
{"url": "https://plugins.example/catalog.json"}
```

远程主机必须在以下任一位置登记：

- `<instance>/studio-plugins/market/trusted-hosts.json`：

```json
{"hosts": ["plugins.example"]}
```

- 环境变量 `QQBOT_PLUGIN_MARKET_HOSTS=plugins.example,*.cdn.example`
- 构造 `PluginMarket(..., trusted_hosts=[...])`（运行时集成/测试用）

通配符只允许最左边的 `*.` 形式，例如 `*.cdn.example`。

## 3. Manifest 与 catalog

单个 manifest 示例：

```json
{
  "id": "example_plugin",
  "name": "Example Plugin",
  "version": "1.0.0",
  "sha256": "64 位小写或大写十六进制",
  "download": "example_plugin-1.0.0.zip",
  "description": "可选的说明",
  "astrbot": ">=4.29,<5",
  "recommended_groups": ["QQ Bot"]
}
```

本地 catalog 的 `download` 是相对于 catalog 的路径，并且必须仍在
`studio-plugins/market/` 内。远程 catalog 可给相对 URL 或受信 HTTPS URL。
`root` 可选；如提供必须等于 `id`。未知 manifest 键会保存在 `extra` 中，不会被丢弃。

## 4. HTTP API

所有请求沿用现有控制台 `X-QB-Token` 鉴权和本地/远程 host 校验。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/instances/<iid>/plugin-market` | 读取缓存的市场、安装状态和分组摘要 |
| POST | `/api/instances/<iid>/plugin-market/refresh` | 校验并刷新固定本地/受信 URL 市场；body 可带 `source` |
| GET | `/api/instances/<iid>/plugin-groups` | 查询分组和多对多成员 |
| POST | `/api/instances/<iid>/plugin-groups` | 带 `confirm:true` 和可选 `idempotency_key` 的分组便捷操作 |
| POST | `/api/instances/<iid>/plugin-groups/preview` | 生成一次性分组变更预览令牌 |
| POST | `/api/instances/<iid>/plugin-groups/apply` | `confirm:true` + `preview_token` 应用预览 |
| POST | `/api/instances/<iid>/plugin-operations/preview` | 预览安装/更新/删除，返回 `preview_token` |
| POST | `/api/instances/<iid>/plugin-operations` | `confirm:true` + `preview_token` 执行操作 |
| GET | `/api/instances/<iid>/plugin-operations/<id>` | 查询持久化操作状态 |

旧 `/api/instances/<iid>/plugins`、插件启停和插件参数保存路由保持不变。

分组 `actions` 示例：

```json
{"actions": [
  {"op": "create", "name": "QQ Bot"},
  {"op": "rename", "group_id": "grp-...", "name": "新名称"},
  {"op": "add", "group_id": "grp-...", "plugin_id": "example_plugin"},
  {"op": "remove", "group_id": "grp-...", "plugin_id": "example_plugin"},
  {"op": "delete", "group_id": "grp-..."}
]}
```

`apply`、批量变更和插件操作都支持 `idempotency_key`。同一个键重放同一意图会返回原结果
（`idempotent:true`）；同一个键用于不同意图会返回 `409`。所有接口错误都只返回安全消息，
不返回本地绝对路径、堆栈或凭据样式文本。

## 5. 状态位置

```text
<instance>/studio-plugins/state.sqlite3    # 分组、预览、幂等记录、操作记录
<instance>/studio-plugins/registry.json    # 市场安装登记表
<instance>/studio-plugins/market/          # 固定 catalog、source.json、trusted-hosts.json
<instance>/studio-plugins/backups/plugins/ # 更新前代码备份
<instance>/studio-plugins/staging/          # 安装/删除暂存（成功后清理）
<instance>/studio-plugins/downloads/        # 校验后的安装包缓存
```

如果 SQLite 或登记表损坏，代码不会自动删除/重建它；接口会返回明确的安全错误，避免把
损坏状态误当成“空状态”从而覆盖用户数据。

## 6. 配置 field-map 预览基础

`qqbot_studio/plugin_config_migration.py` 提供：

- `load_field_map()` / `read_field_map()`：读取、校验声明式 field-map；
- `preview_config_migration()`：只读生成迁移预览。

预览保证：

- 不写旧配置，也不写目标配置；
- 目标配置已有的键永远不被覆盖，只标记为 `conflict`；
- 未在 field-map 中出现的源字段进入 `unknown_fields`；
- 目标端 only 键进入 `preserved_target_keys`；
- field-map 顶层未知键和条目未知元数据分别保留在 `extensions` / `metadata`；
- 只支持 `copy/string/bool/int/float` 声明式转换，不执行任意代码。

本测试版**不会实际搬移 264 个字段**；这一步仅为后续拆分提供可测试的 dry-run 基础。

## 7. 测试

```bash
python3 -m unittest discover -s tests -q
```

专项覆盖见 `tests/test_plugin_market.py` 与 `tests/test_plugin_market_api.py`。

## 8. AstrBot root 配置权限

AstrBot 容器默认以 root 写 bind mount，首次加载新插件时可能生成：

```text
root:root 0600  data/astrbot/config/qqbot_*_config.json
```

Studio 以 ubuntu 运行时会读不到，并误报“配置损坏”。插件安装/重启后运行：

```bash
./scripts/fix-astrbot-config-ownership.sh
```

默认使用：

```text
ASTRBOT_CONTAINER=qqbot-bot-1-astrbot
HOST_UID=1000
HOST_GID=1001
```

该方案不改变 AstrBot 的 root 运行方式，只修正插件配置文件的宿主机所有权。
