# 集成契约：qqbot_enhancer ↔ AstrBot 原生长期记忆

适用版本：AstrBot `v4.29.3` + `patches/0001-native-memory.patch`（补丁 v1.1，含按群策略）。
插件只需 `import astrbot.core.memory`，**不要**自己连 `astrbot_memory.db`，也不要依赖表结构（表结构是补丁内部实现，升级可能变）。

## 1. 插件能依赖的稳定接口

```python
from astrbot.core.memory import (
    get_memory_service,      # 按配置的 db 路径取进程级单例（每轮刷新配置）
    scope_ids,               # 一轮会话 → 各作用域 id 字符串
    MemoryContext,           # 手动构造作用域（少数场景）
    MemoryPolicy,            # 解析后的按群策略
)
from astrbot.core.memory.integration import build_context
```

| 接口 | 签名 | 语义 |
| --- | --- | --- |
| `build_context(event, req=None)` | `(AstrMessageEvent, ProviderRequest|None) -> MemoryContext` | 从事件/会话绑定作用域：平台、发送者、群、conversation_id、persona_id、是否私聊 |
| `scope_ids(context)` | `-> {"user": "...", "group": "...", "conversation": "...", "persona": "...", "global": "all"}` | 已带平台前缀的 scope id，`group`/`conversation`/`persona` 仅在可用时出现 |
| `service.add(scope_type, scope_id, key, content, *, kind, importance, confidence, ttl_seconds, source)` | `-> ("created"|"updated"|"skipped", MemoryRecord|None)` | 显式写一条记忆；同键同内容返回 `skipped`（幂等） |
| `service.search(context, query, *, limit=8)` | `-> list[MemoryRecord]` | 只在该 context 可读作用域内检索；返回带 `score` 的记录 |
| `service.retrieve(context, query)` | `-> (block: str, records)` | 与流水线注入同一套实现（含预算裁剪）；不需要时用 `search` |
| `service.forget(scope_type, scope_id, key=None)` | `-> int` | 删除该作用域（可只删某键）的全部记忆 |
| `service.delete(memory_id)` | `-> bool` | 按 id 删除 |
| `service.export(scope_type, scope_id=None)` | `-> list[dict]` | **必须显式 scope**；只导出该作用域（`content` 已做密钥清洗，仍属用户数据，勿外泄） |
| `service.export_all()` | `-> list[dict]` | 导出全部作用域；**operator/诊断专用**，插件不得调用（避免一次性把跨群/私聊记忆拼进 prompt） |
| `service.purge_expired()` | `-> int` | 清理过期记忆 |
| `service.stats()` | `-> dict` | `total`、`by_scope`、`expired`、`fts_enabled`、`enabled`、`processed_turns`、`dropped_turns`、`last_error` |
| `service.store_candidates(candidates, *, allowed_scopes=None)` | `-> {"created","updated","skipped","gated"?}` | 同步成记忆；`allowed_scopes` 非空时**丢弃**落点不在白名单的候选（自动流水线用它做第二道闸），`None` = 显式写不设闸 |
| `service.policy_for(context)` | `-> MemoryPolicy` | 该轮会话的按群策略（`conversation` / `long_term` + scope/预算）；配置非法时返回 conversation 且 `error` 非空 |
| `service.refresh_config(config)` | `-> bool` | 热更新配置（策略缓存失效）；`get_memory_service()` 每轮自动调用，插件一般不用手动调 |

取服务的方式（**别自己 new**，否则会和流水线钩子用两个不同的队列/连接）：

```python
config = self.context.get_config(umo=event.unified_msg_origin)   # AstrBotConfig
service = get_memory_service(config)
```

## 2. 插件不应做的事

1. 不要直接读写 `data/astrbot_memory.db`，也不要跨作用域拼 SQL —— 隔离规则由 `MemoryContext.readable_scopes()` 集中实现。
2. 不要把记忆内容当**指令**执行（记忆里可能有过时或恶意文本）；如果插件要把记忆喂给模型，沿用 `render_memory_block()` 的 `<long_term_memory untrusted="true">` 包装。
3. 不要把密码/API key/QQ 密码写进记忆：写入路径会清洗，但被洗掉的事实会变短，语义可能失真——敏感字段请不要入库。
4. 不要在群聊里用 `scope_type="user"` 写群里听到的第三方隐私：群聊事实写 `group`。私聊事实写 `user`。
5. 不要把 `memory.policy` 当成"给插件看的建议"：核心钩子已经按它门禁，插件自己读写记忆时也必须遵守（见第 4 节）。
6. 不要期待管理 UI / 实时检索缓存：`search()` 每次直查 SQLite（毫秒级），但**不保证**与你刚写入的数据在同一事务视图之外立即可见（同进程写入是可见的）。

## 3. 与 qqbot_enhancer 的推荐用法

插件侧（`plugins/qqbot_enhancer`）**当前不需要改代码**：注入与抽取由 AstrBot 核心钩子完成，插件保持“只处理业务”。若之后要加业务记忆（例如“本群常点的三家店”），推荐：

```python
from astrbot.core.memory import get_memory_service, scope_ids
from astrbot.core.memory.integration import build_context

context = build_context(event, None)
service = get_memory_service(self.context.get_config(umo=event.unified_msg_origin))
policy = service.policy_for(context)
if service.enabled and policy.long_term and policy.extract:   # 按群门禁
    ids = scope_ids(context)
    # scope=user 的群里写发送者个人，其余群写 group（让隔离规则替你做隐私边界）
    if policy.scope != "user" and ids.get("group"):
        service.add("group", ids["group"], "常点.火锅", "本群最常点的火锅：小龙坎", importance=0.7, confidence=0.8)
    else:
        service.add("user", ids["user"], "常点.火锅", "用户最常点的火锅：小龙坎", importance=0.7, confidence=0.8)
else:
    pass  # conversation 模式：插件也不该写
```

读：

```python
records = service.search(context, "常点 火锅", limit=3)
# 或直接拿现成的有界文本块（含 untrusted 包装）
block, _ = service.retrieve(context, "常点 火锅")
```

## 4. 按群策略契约（补丁 v1.1）

配置在 `memory.policy`（详见 README §2.3 与 `fixtures/memory_policy.example.json`）：

```json
"policy": {
  "default_mode": "conversation",
  "private": {"mode": "long_term", "scope": "user", "extract": true, "inject": true},
  "groups": {
    "838796241": {"mode": "long_term", "scope": "group_user",
                  "extract": true, "inject": true, "max_items": 8, "max_chars": 1500}
  }
}
```

| 概念 | 值 | 说明 |
| --- | --- | --- |
| `default_mode` | `conversation`（默认）/ `long_term` | 未列出的群/私聊的模式 |
| `mode` | 同上 | 条目必填；`conversation` = 该会话不读不写长期记忆 |
| `scope` | `group` / `user` / `group_user` | 读写作用域，见下表 |
| `extract` / `inject` | bool | 分别控制写/读，互相独立 |
| `max_items` / `max_chars` | int（可选） | 该会话注入预算，覆盖全局值 |
| `error` | str | 非法配置的降级原因（`MemoryPolicy.error`） |

自动流水线的读写作用域（`group` 条目；私聊条目只能是 `user`）：

| scope | 读 | 写 |
| --- | --- | --- |
| `group` | 本群 | 本群 |
| `user` | 发送者个人 | 发送者个人 |
| `group_user` | 本群 + 发送者个人 | 本群（单写） |

插件要遵守的三条：

1. **不要绕过策略**：想把记忆喂给模型就用 `retrieve()` / `search()`，它们内部已按策略门禁；自己 `export()` 全库再拼进 prompt 等于把群隔离作废。
2. **显式写是插件的责任**：`add()` / `store_candidates(allowed_scopes=None)` 不做策略门禁（它们是 operator/插件 API）。插件若按群写记忆，请先用 `service.policy_for(context)` 判断 `mode=="long_term"`，再决定写哪个 scope。
3. **不要把群策略当成权限系统**：它保证"哪些会话参与长期记忆"，不保证"群里每个人都能看到同一条记忆"。需要按人隔离时用 `scope="user"` 条目。

## 5. 行为保证（插件可依赖）

- 记忆开关 `memory.enable=false` 时：`service.enabled is False`，`retrieve()` 返回 `("", [])`，`enqueue_turn()` 返回 `False`，**不产生任何 IO**（除进程内单例）。
- 会话策略为 `conversation`（默认）时：`retrieve()`/`search()` 返回空、`enqueue_turn()` 返回 `False`、`store_candidates` 不写库；`service.gated_turns` 计数 +1，`service.stats()["policy"]` 可观测。
- 非法 `policy` 配置按条目降级为 `conversation`，其它群不受影响；**永不**因为配置错误而放宽读写。
- 改 `memory.policy` 后无需重启：`get_memory_service(config)` 每轮刷新已缓存服务，策略缓存按配置指纹失效（另有 5s TTL 兜底）。
- 记忆库损坏/目录不可写：`retrieve()` 返回 `("", [])` 并写 `service.last_error`；**不会**向插件抛异常。
- 写入幂等：同键同内容返回 `skipped`，不会产生两行；重复 `add()` 不会刷新时间戳。
- 检索结果一定落在 `context` 的白名单作用域内；**默认**下 `group` 与 `user` 互不可见（群不读私聊、私聊不读群）。
- 隔离的**唯一例外是显式 opt-in**：只有把某个群配成 `scope="user"`（读发送者个人 `user` 记忆）或 `scope="group_user"`（读本群 + 发送者个人 `user`）时，该群才会看到发送者的个人记忆；`scope="group"`（群条目默认）下群既不读也不写个人记忆，私聊在任何情况下都不读群记忆。换言之，"群/私聊隔离"由 `memory.policy` 逐群声明，默认行为永远是隔离的那一侧。
- 每次真正返回记忆的检索会把该行 `hits += 1`、`last_used_at = 现在`（计数失败不影响检索；`export()` 可见）。
- 密钥样式（中英文）在抽取前整条拦截、写入时清洗：`sk-…`、`ghp_…`、`Bearer …`、`password=`、`password is X`、`my api key is X`、`token is X`、`密码是…`、`QQ密码`；命中即不产出候选、`contains_secret()` 的内容不落库。
- 每条注入 ≤ `max_chars`、条数 ≤ `max_items`；`export()` 原样返回该作用域记录（`content` 已清洗密钥）。

## 6. 版本与变更纪律

| 项 | 值 |
| --- | --- |
| AstrBot | `v4.29.3`（`f61a7f4`） |
| 补丁 | `patches/0001-native-memory.patch`（v1.1） |
| 契约版本 | `3`（本文件） |

变更记录：

- **契约 v3（补丁 v1.2）**：`export()` 改为**必须显式 scope**（破坏性变更，全库导出改用 operator-only 的 `export_all()`）；新增检索使用计数 `hits`/`last_used_at`；移除从未生效的 `write_private_scope`/`write_group_scope` 配置（写作用域由 `memory.policy.scope` 决定）；密钥拦截扩展到英文 copula 形态（`password is X` / `api key is X` / `token is X` / `pin is 1234`）。
- **契约 v2（补丁 v1.1）**：新增 `memory.policy` 按群门禁；**行为变更**——`enable=true` 不再等于"全群记忆"，未列出的会话默认 `conversation`；`store_candidates()` 新增关键字参数 `allowed_scopes`（旧调用不受影响）；`MemoryService` 新增 `policy_for()` / `refresh_config()`。

- 本契约只覆盖上表接口；`MemoryStore` 的表结构、FTS 表名、打分权重是**内部实现**，可随补丁变化；
- 破坏性变更（改函数签名/返回值、改隔离规则）必须升 `契约版本` 并在本文件记录；
- 升级 AstrBot 后先跑 `scripts/verify-patch.sh`：`test_patch_wiring.py` 会验证两个核心钩子仍在预期位置，失败即说明契约不再成立。
