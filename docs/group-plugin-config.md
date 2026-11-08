# 分群插件启用与分群参数设计

> 状态：下一版设计（当前 4.3.1 尚未实现）。
> 目标：同一个 AstrBot 进程内，不同 QQ 群可以启用不同插件，并覆盖不同的插件参数，不通过卸载/加载插件实现。

## 1. 结论

能做到，而且应该做成分层策略，而不是“每个群复制一份插件”：

```text
实例默认配置
  └─ 群模板（可选）
       └─ 群覆盖（启用/停用 + 允许覆盖的参数）
            └─ 私聊覆盖（独立，不继承群配置）
```

运行时按 `群号 / 私聊会话` 读取策略快照，插件入口先过门控，再读取本群参数。AstrBot 仍然只加载一份插件代码。

## 2. 为什么不能靠卸载/加载插件

AstrBot 的插件加载是进程级/实例级，不是群级。卸载插件会影响所有群；为每个群加载不同副本还会造成：

- 同一事件被多个插件实例处理，重复回复/重复工具调用；
- 配置、数据、定时任务、缓存各自为政；
- 升级、回滚、审计困难。

因此必须是**运行时门控 + 参数解析**，代码只有一份，策略按群解析。

## 3. 数据模型

建议新增一张群策略表（SQLite），由 Studio/控制面单写：

```text
group_plugin_policy
  group_id        TEXT      -- 裸群号或 platform:group_id
  plugin_id       TEXT
  enabled         INTEGER   -- 0/1
  params_json     TEXT      -- 只存允许群覆盖的字段
  template_id     TEXT      -- 可选
  revision        INTEGER   -- 每次写入 +1
  updated_by      TEXT
  updated_at      TEXT
  PRIMARY KEY (group_id, plugin_id)
```

另设：

```text
group_plugin_template
  template_id, name, params_json, revision, ...
```

私聊不写入 `group_id` 表，使用独立的 `private_plugin_policy`（或 `session_scope='private'` 行），避免把私聊配置误当群配置。

### 3.1 参数 schema 标记

每个插件 `_conf_schema.json` 的字段增加元数据：

```json
{
  "group_overridable": true,
  "group_scope": "group",
  "sensitive": false,
  "group_min": 0,
  "group_max": 1
}
```

含义：

| 标记 | 作用 |
|---|---|
| `group_overridable` | 是否允许群覆盖；false 时 UI 显示“继承全局”，API 拒绝写入 |
| `group_scope` | `group` / `private` / `both`；私聊只接受 private/both |
| `sensitive` | 密钥、全局资源、安全开关等，只允许实例管理员在全局改 |
| `group_min` / `group_max` | 群覆盖值的额外边界；仍要过原 schema 校验 |

不允许覆盖的典型字段：

- API key、token、provider secret；
- 全局数据库路径、插件安装目录；
- 安全工具的总开关/签名密钥；
- 会影响其他群的数据保留期、全局限流。

允许覆盖的典型字段：

- `qqbot_core_reply`：非 @ 概率、时间段、回复长度、人格/提示词 profile、是否允许工具；
- `qqbot_safe_tools`：本群工具白名单、冷却、是否需要确认；
- `qqbot_food`：默认城市/位置、搜索条数、排序、是否允许提交评价；
- `qqbot_stickers`：装饰概率、AI 装饰/收藏开关与概率、候选数量；
- `qqbot_memory_commands`：本群是否允许写记忆、可见 scope、结果条数。

## 4. 优先级与合并规则

```text
最终值 = 实例默认
        ← 群模板（如果群绑定了模板）
        ← 群覆盖（最高）
```

规则：

1. 只合并 `group_overridable=true` 的字段；其他字段永远取全局值。
2. 群覆盖是“字段级覆盖”，不是整份配置替换；没写的字段继承上一层。
3. `enabled` 是独立门控，不参与参数合并。
4. 私聊单独解析，不继承任何群模板/群覆盖。
5. 非法值不生效：记录审计和 warning，回退到上一层，不影响其他群。
6. 空对象/`null` 表示清除覆盖，回到继承。
7. 每次写入 `revision += 1`；运行时缓存按 `group_id + plugin_id + revision` 失效。
8. 配置热更新：下一轮消息生效，不要求重启 AstrBot。

## 5. 运行时读取

建议增加一个轻量策略服务（可放在 `qqbot_safe_tools` 或 Studio 控制面）：

```python
policy = group_policy.snapshot(group_id, plugin_id)
if not policy.enabled:
    return  # 插件入口直接跳过
params = policy.params  # 已合并、已校验的 dict
```

各插件不要直接读全局 `_conf_schema` 后再自己判断群；统一从策略服务取最终值。这样可以保证：

- 只有一个合并实现；
- 审计/缓存/回滚只有一处；
- 新插件只要声明 schema 标记就能接入。

对 core_reply 这种唯一回复 owner：

```text
收到事件
  → 解析 group_id/private
  → 读取 core_reply 群策略（enabled + 参数）
  → 若关闭：不参与回复
  → 若开启：用本群参数做参与判定、人格、时间段、工具注入
```

对 safe_tools 这种工具 owner：

```text
模型生成 marker/native tool
  → safe_tools 读取本群策略
  → 工具不在本群 allowlist：拒绝并返回可解释文案
  → 在 allowlist：按本群冷却/确认策略执行
```

## 5.1 群管理员改参数开关

保留一个实例级开关：

```json
{
  "group_admin_override_enabled": false,
  "group_admin_allowlist": []
}
```

- 默认关闭；只有实例管理员能打开。
- 打开后，QQ 群主/管理员（或显式 allowlist 中的 QQ 号）可以修改**本群允许覆盖的非敏感参数**。
- 群管理员不能修改 `sensitive=true` 字段、不能改其他群、不能看/导出/删除记忆/关系/好感数据。
- 所有群管理员修改都写审计：操作者、群、插件、字段、旧值→新值、时间；不记录消息正文。
- 群管理员身份优先取 QQ 事件角色；取不到时回退 `group_admin_allowlist`，避免误授权。

## 6. Studio UI / API

### UI

在“群策略”面板中按插件分组：

```text
qqbot_core_reply      [本群启用 ✓]  [覆盖 3 项]  [重置]
  ├─ 非 @ 概率        继承 0.12  →  本群 0.30
  ├─ 时间段          继承全局    →  本群自定义
  └─ 回复长度        继承 500   →  本群 800
qqbot_food            [本群启用 ✓]  [覆盖 1 项]
  └─ 默认城市        继承空      →  长春市
```

要求：

- 只显示 `group_overridable=true` 的字段；
- 显示“继承值 / 本群值 / 来源（模板/覆盖）”；
- “重置”只清本群覆盖，不动物理配置；
- 敏感字段不出现或只读；
- 保存前做与全局相同的 schema 校验；
- 所有写入记审计（谁、哪个群、哪个插件、旧值→新值），不记录密钥。

### API 草案

```text
GET  /api/instances/{id}/group-policies/{group_id}
PUT  /api/instances/{id}/group-policies/{group_id}/{plugin_id}
POST /api/instances/{id}/group-policies/{group_id}/{plugin_id}/reset
GET  /api/instances/{id}/group-policies/{group_id}/effective
```

写入 body 只接受 `enabled` 和 `params`；服务端按 schema 过滤、校验、合并、递增 revision。

## 7. 失败与安全

- 策略库损坏/读不到：回退全局默认，记录错误；不能把所有群误关。
- 群号缺失：按私聊/未知会话处理，使用全局默认。
- 非法群覆盖：该项回退上一层，不影响其他插件/群。
- 群覆盖尝试写敏感字段：API 拒绝，审计记录。
- 群管理员只能改本群允许项；实例管理员才能改模板/全局。
- 不把群策略写进插件 ZIP，不通过重装插件改变群配置。
- 不记录消息正文，只记录配置变更。

## 8. 验收标准

1. 同一个 AstrBot 进程，群 A `qqbot_food` 开启、群 B 关闭；群 A 查美食有结果，群 B 不响应。
2. 群 A `core_reply` 非 @ 概率 0.3，群 B 0.05；同一时间窗内命中率明显不同，互不影响。
3. 群 A `qqbot_stickers` 装饰概率 0.2，群 B 1.0；群 A 大多数不装饰，群 B 正常装饰。
4. 群 A 配置默认城市“长春市”，群 B 为空；群 A 说“我这块火锅店”按长春查，群 B 仍追问城市。
5. 群 A 覆盖一个不允许覆盖的字段：API 拒绝/忽略，值仍等于全局。
6. 修改群策略后无需重启，下一条消息生效；重启后配置仍在。
7. 私聊配置不继承群配置；群配置不影响私聊。
8. 非法值、坏策略库、缺群号均有回退，不产生 500/崩溃/跨群串配置。
9. 全量测试 + 真实 QQ 双群 E2E 通过。

## 9. 分阶段

- **P0**：策略表、schema 标记、合并/校验/缓存、core_reply + food 两个插件接入。
- **P1**：Studio 群策略面板、审计、模板、safe_tools 工具白名单。
- **P2**：其余插件接入；导入/导出/回滚；双群 E2E。

## 10. 与现有“插件分组”的区别

现有“插件分组”是插件市场的 UI 分类（把插件放进“QQ Bot”等组），不是运行时群开关。分群启用/分群参数是新的策略层，两者不要混用命名：

- 插件市场分组：`plugin_groups`，用于展示/筛选。
- 分群策略：`group_plugin_policy`，用于运行时启用和参数覆盖。
