# 记忆内容管理（Studio 面板 + HTTP API）

Studio 的「记忆内容」面板管理 AstrBot 长期记忆**数据库里的内容**，与
[`memory-policy.md`](memory-policy.md)（按群 `memory.policy` 策略）是两件事：
策略决定哪个群读写记忆，本功能决定这些记忆记录怎么查看/编辑/导出/导入/整理。

- 后端模块：[`../qqbot_studio/memory_admin.py`](../qqbot_studio/memory_admin.py)
- 路由：[`../qqbot_studio/server.py`](../qqbot_studio/server.py)（`/api/instances/<id>/memory/...`）
- 面板：[`../qqbot_studio/static/console.js`](../qqbot_studio/static/console.js) 的
  `buildMemoryContentSection`；样式在 `console.css` 的 `.memory-content-*`
- 测试：[`../tests/test_memory_admin.py`](../tests/test_memory_admin.py)（40 个用例 + 静态面板契约）

## 1. 边界与隐私

- 只读写实例数据目录里的 SQLite 边车 `data/astrbot_memory.db`（核心补丁的默认路径），
  **不修改** `integrations/astrbot-memory` 核心补丁、不改 `memory.policy`、不写聊天路径。
- 每个读/写操作都必须显式给出 `scope_type` + `scope_id`；**没有**“列出全部 scope”或
  “跨 scope 汇总”的接口，因此一个响应永远只包含一个 scope 的记录。
- `scope_type` 只有 5 个真实存储值：`user` / `group` / `conversation` / `persona` / `global`。
  `group_user`、`speaker/group_member` 是**策略名**（策略决定写 group 还是 user），不是数据库里的
  scope 值，传进来会 400 而不是做静默重映射。
- 返回内容永不带数据库路径、其他 scope 的 id/内容；错误信息里的路径会被替换成“记忆数据库”。
- 密钥/密码：写入前拒绝（`sk-`、`ghp_`、`Bearer …`、`password=…`、`密码是…`、英文系词
  `password is …`、`api key is …` 等，与核心 `text.py` 同一套规则）；读取、导出时再脱敏一次，
  命中时记录带 `redacted: true`。
- 备份/导出文件：目录 0700、文件 0600，临时文件 + `os.replace` 原子落盘。
- 读查询使用 SQLite **只读 URI**（`file:...?mode=ro`），读写连接都设 `busy_timeout`（8s）、
  写操作单事务（`BEGIN IMMEDIATE` / `COMMIT` / 失败 `ROLLBACK`）。
- Docker 部署下宿主机用户可能无权读写该库：先尝试宿主文件，遇到权限错误自动改用
  `docker exec <容器> python3 -c <固定脚本>` 在容器内执行**同一批参数化语句**（固定 argv、无 shell、
  路径白名单 `/AstrBot/data/`、只允许 SELECT/INSERT/UPDATE/DELETE 单语句）。两条通道语义一致。

## 2. 端点

所有端点都要 `X-QB-Token`（与 Studio 其余接口一致）；GET 的 scope 放在 query，写操作放在 JSON body。
`<id>` 为实例 id。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/instances/<id>/memory/stats?scope_type=&scope_id=` | 该 scope 的条数/命中/过期/kind 分布/最后更新时间 |
| GET | `/api/instances/<id>/memory/records?scope_type=&scope_id=&q=&limit=&offset=` | 分页列表（`limit` 1-50，默认 20；`q` 只匹配内容/键） |
| GET | `/api/instances/<id>/memory/records/<record_id>?scope_type=&scope_id=` | 单条详情 |
| POST | `/api/instances/<id>/memory/records` | 手动新增（body 含 scope + content …；`dedup=skip\|overwrite`） |
| PATCH | `/api/instances/<id>/memory/records/<record_id>` | 编辑正文/键/kind/重要度/置信度/过期 |
| DELETE | `/api/instances/<id>/memory/records/<record_id>` | 删除单条（需 `confirm=<record_id>`） |
| POST | `/api/instances/<id>/memory/forget` | 忘记整个 scope（需 `confirm=<scope_id>`） |
| POST | `/api/instances/<id>/memory/export` | 导出该 scope（JSON，`schema_version: 1`） |
| POST | `/api/instances/<id>/memory/import` | 预览（`apply:false`）或导入（`apply:true` + `confirm`） |
| POST | `/api/instances/<id>/memory/reflect` | 整理预览 / 应用（`apply:true` + `confirm`） |

示例：

```bash
TOKEN=...; BASE=http://127.0.0.1:8765
curl -s -H "X-QB-Token: $TOKEN" \
  "$BASE/api/instances/bot-1/memory/records?scope_type=group&scope_id=aiocqhttp%3A838796241&limit=20&offset=0"
curl -s -X POST -H "X-QB-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"scope_type":"group","scope_id":"aiocqhttp:838796241","content":"群主不吃香菜","kind":"preference"}' \
  "$BASE/api/instances/bot-1/memory/records"
```

`scope_id` 是 `平台:会话 id`（例如 `aiocqhttp:838796241`、私聊 `aiocqhttp:10001`；`global` 用 `all`）。
平台名以 AstrBot 实际平台适配器为准，面板会从记忆策略里带出已配置的群号做提示。

## 3. 记录字段

列表/详情/导出返回同一种记录对象：

```json
{
  "id": 12, "key": "偏好.香菜", "content": "群主不吃香菜", "kind": "preference",
  "importance": 0.7, "confidence": 0.8, "hits": 3, "source": "heuristic",
  "created_at": "2026-11-03T08:00:00+00:00", "updated_at": "...",
  "last_used_at": "", "expires_at": "", "expires_in_seconds": null,
  "expired": false, "redacted": false
}
```

- `kind`：`fact/preference/identity/profile/habit/note`（写入白名单，未知值 400）。
- 新增/编辑限制：`content` ≤ 200 字符（核心 `MAX_CONTENT_CHARS`）、`key`（`mem_key`）≤ 64，
  `importance/confidence` 0–1，`ttl_seconds` 60–366 天或 `expires_at`（Unix 秒，必须晚于现在；
  传 `null` 表示永久/清除 TTL），未知字段直接 400。
- 单 scope 上限 500 条（与核心淘汰上限一致）：新增/导入超过时 409，不做静默淘汰。

## 4. 导入 / 恢复

请求体：`{scope_type, scope_id, records:[...], apply:false|true, dedup:"skip"|"overwrite", skip_invalid, confirm}`。

- `records` 既接受导出文件里的记录对象，也接受最小对象 `{mem_key|key, content, kind, importance,
  confidence, expires_at|ttl_seconds, created_at}`；导出附带的展示字段（id/hits/source/iso 时间/redacted）
  会被安全忽略，时间优先取 `*_epoch`。
- 预览返回 `preview: {total, valid, invalid[{index,reason}], conflicts[{index,key,kind,existing_id,existing_content}],
  near_duplicates[], would_create, would_update, would_skip, expiry_dropped[], exceeds_scope_cap, cap, dedup}`。
- 冲突判定与核心一致：同 scope 同 `mem_key` → 冲突（`skip` 跳过 / `overwrite` 覆盖该条）；
  同 scope 同内容指纹 → 一律跳过（核心不会重复写入相同内容）；相似度 ≥0.92 只做提示。
- `apply:true` 要求 `confirm=<scope_id>`；存在非法记录时必须显式 `skip_invalid:true`，否则 400。
- 写入是**单事务**：任一条失败整体回滚（`tests/test_memory_admin.py::test_write_batch_rolls_back_on_constraint_error`），
  不会写半份；事务前先写该 scope 的 0600 备份（`<实例数据>/memory-backups/memory-*-import-*.json`）。
- 只写目标 scope，其他 scope 的记录保持原样（导出→导入到另一个 scope 的往返测试覆盖）。
- 已经过期的记录在导入时去掉 TTL（记为 `expiry_dropped`），避免整份文件因一条过期记录失败。

## 5. 整理 / Reflection（dry-run 优先）

`POST .../memory/reflect`，`{scope_type, scope_id, apply:false}` 返回：

- `duplicates[]`：同 scope 内相似度 ≥0.92 的近重复组，`keep_id` 为保留项，其余为清理候选；
- `contradictions[]`：去掉否定词后同义、但极性相反的组合（**只报告，永不自动删除**）；
- `expired[]` / `low_value[]`：已过期与低价值（0 命中、低重要度、30 天未更新）预览；
- `counts{records,duplicates,contradictions,expired,low_value}`。

`apply:true`（需 `confirm=<scope_id>`）只清理“重复组里的非保留项 + 已过期记录”，先写 0600 备份再单事务删除；
矛盾永远保留。实现是**纯本地、确定性、不调用 LLM** 的 Studio 侧分析（`source: "studio"`），
与 `docs/memory-management-plan.md` 第 6 节的语义一致；当核心暴露服务级 reflect API 时，
面板/接口可改为委托该服务而不改 UI 契约。

## 6. 状态码

| 状态 | 场景 |
|---|---|
| 200 | 成功；预览类接口返回 `apply:false` 与 `preview` |
| 400 | scope 非法/缺失、id 非法、字段校验失败、内容疑似密钥、缺 `confirm`、导入含非法记录 |
| 403 | 缺少/错误 `X-QB-Token` |
| 404 | 实例不存在、记录不存在或不属于该 scope、未知子路由 |
| 405 | 该路径不支持该 HTTP 方法（例如 PATCH 非记录路径） |
| 409 | 记忆键冲突、超过 scope 500 条上限 |
| 503 | 数据库不可读/已损坏且容器也不可用：`{ok:false, available:false, permission_denied, empty, corrupt, actions[], message}` |

数据库文件尚未创建时，读接口返回空统计/空列表（`available:true, writable:false`，提示先在聊天里产生一条记忆或重启 AstrBot），不会由 Studio 建库；真正不可读/损坏时返回 503。不可用时响应会给出可执行步骤（例如“让 AstrBot 容器保持运行，Studio 会通过 docker exec 读写”），
面板显示同样的说明并禁用写操作，**不会**显示伪造的已保存值/空成功。

## 7. 已知限制

- 只支持默认库路径 `data/astrbot_memory.db`；自定义 `memory.db_path` 暂不读取，会明确提示。
- 容器通道要求镜像内有 `python3`；容器停止时写操作返回 503（含修复步骤）。
- 面板不提供“扫描所有 scope”：故意不支持跨 scope 列举，避免一个页面泄露其他群/私聊的 scope 列表。
- 与 AstrBot 并发写同一库时依赖 SQLite 事务与 `busy_timeout`；没有额外的跨进程锁。
- 未做真实 AstrBot 进程内 E2E（与 `integrations/astrbot-memory/README.md` §6 同口径）；
  本功能只经 SQLite 文件与容器通道验证。
