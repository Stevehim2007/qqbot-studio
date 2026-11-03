# 集成契约：qqbot_enhancer ↔ AstrBot 原生长期记忆

适用版本：AstrBot `v4.29.3` + `patches/0001-native-memory.patch`。
插件只需 `import astrbot.core.memory`，**不要**自己连 `astrbot_memory.db`，也不要依赖表结构（表结构是补丁内部实现，升级可能变）。

## 1. 插件能依赖的稳定接口

```python
from astrbot.core.memory import (
    get_memory_service,      # 按配置的 db 路径取进程级单例
    scope_ids,               # 一轮会话 → 各作用域 id 字符串
    MemoryContext,           # 手动构造作用域（少数场景）
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
| `service.export(scope_type=None, scope_id=None)` | `-> list[dict]` | 导出（含 `content`，已做密钥清洗；仍属用户数据，勿外泄） |
| `service.purge_expired()` | `-> int` | 清理过期记忆 |
| `service.stats()` | `-> dict` | `total`、`by_scope`、`expired`、`fts_enabled`、`enabled`、`processed_turns`、`dropped_turns`、`last_error` |
| `service.store_candidates(candidates)` | `-> {"created","updated","skipped"}` | 同步成记忆（候选来自 `extract.heuristic_extract` 或插件自有抽取） |

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
5. 不要期待管理 UI / 实时检索缓存：`search()` 每次直查 SQLite（毫秒级），但**不保证**与你刚写入的数据在同一事务视图之外立即可见（同进程写入是可见的）。

## 3. 与 qqbot_enhancer 的推荐用法

插件侧（`plugins/qqbot_enhancer`）**当前不需要改代码**：注入与抽取由 AstrBot 核心钩子完成，插件保持“只处理业务”。若之后要加业务记忆（例如“本群常点的三家店”），推荐：

```python
from astrbot.core.memory import get_memory_service, scope_ids
from astrbot.core.memory.integration import build_context

context = build_context(event, None)
service = get_memory_service(self.context.get_config(umo=event.unified_msg_origin))
if service.enabled:
    ids = scope_ids(context)
    # 群聊写 group，私聊写 user（让隔离规则替你做隐私边界）
    if ids.get("group"):
        service.add("group", ids["group"], "常点.火锅", "本群最常点的火锅：小龙坎", importance=0.7, confidence=0.8)
    else:
        service.add("user", ids["user"], "常点.火锅", "用户最常点的火锅：小龙坎", importance=0.7, confidence=0.8)
```

读：

```python
records = service.search(context, "常点 火锅", limit=3)
# 或直接拿现成的有界文本块（含 untrusted 包装）
block, _ = service.retrieve(context, "常点 火锅")
```

## 4. 行为保证（插件可依赖）

- 记忆开关 `memory.enable=false` 时：`service.enabled is False`，`retrieve()` 返回 `("", [])`，`enqueue_turn()` 返回 `False`，**不产生任何 IO**（除进程内单例）。
- 记忆库损坏/目录不可写：`retrieve()` 返回 `("", [])` 并写 `service.last_error`；**不会**向插件抛异常。
- 写入幂等：同键同内容返回 `skipped`，不会产生两行；重复 `add()` 不会刷新时间戳。
- 检索结果一定落在 `context` 的白名单作用域内；`group` 与 `user` 互不可见（群不读私聊、私聊不读群）。
- 每条注入 ≤ `max_chars`、条数 ≤ `max_items`；`export()` 原样返回该作用域记录（`content` 已清洗密钥）。

## 5. 版本与变更纪律

| 项 | 值 |
| --- | --- |
| AstrBot | `v4.29.3`（`f61a7f4`） |
| 补丁 | `patches/0001-native-memory.patch` |
| 契约版本 | `1`（本文件） |

- 本契约只覆盖上表接口；`MemoryStore` 的表结构、FTS 表名、打分权重是**内部实现**，可随补丁变化；
- 破坏性变更（改函数签名/返回值、改隔离规则）必须升 `契约版本` 并在本文件记录；
- 升级 AstrBot 后先跑 `scripts/verify-patch.sh`：`test_patch_wiring.py` 会验证两个核心钩子仍在预期位置，失败即说明契约不再成立。
