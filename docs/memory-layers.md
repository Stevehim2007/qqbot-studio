# 四层记忆与自动召回

> 适用版本：主项目 `5.0.0-beta.25` 以后；`qqbot_memory_bank 0.4.1` 以后。
>
> 本文说明“人格记忆 / 群记忆 / 个人 AI 记忆 / 共享知识库”四层数据、控制台入口、注入方式、配置和当前边界。

## 1. 为什么要分四层

旧实现把“用户导入资料”“AI 提取事实”“对话卡片”“AstrBot 原生长期记忆”混在一起，结果会出现：

- 用户导入的群规被 AI 当成自己的记忆改写；
- 一个人的偏好被注入到另一个人的对话；
- 群记忆和人设冲突；
- 每轮把整本资料塞进 prompt，token 爆炸；
- 卡片重复、episode 变成原文拼接。

四层记忆按“谁拥有、何时注入、能否被模型写”拆开。

## 2. 四层定义

| 层 | 数据表 | 归属 | 写入者 | 读取方式 | 典型内容 |
|---|---|---|---|---|---|
| 人格记忆 | `memory_layer(layer='persona')` | `owner_id = persona_id` | Studio 人格记忆面板 | 每次新会话常驻注入 | 身份、口癖、底层设定、不能违背的边界 |
| 群记忆 | `memory_layer(layer='group')` | `owner_id = group:<platform>:<群号/openid>` | Studio 群记忆面板 | 每次该群会话常驻注入 | 群规、群内梗、长期约定、群背景 |
| 个人 AI 记忆 | `knowledge_entry` | `scope + persona_id + actor_hash` | 模型 `memory_add`、辅助 AI 自动提取 | 按需工具 `memory_search`；可选自动召回 | 用户偏好、习惯、经历、约定 |
| 共享知识库 | `knowledge_doc` | `scope + persona_id` | 仅 Studio 导入/删除 | 每轮自动 RAG top-k；模型 `knowledge_search` 只读 | 群规文档、设定集、世界观、资料 |

另外保留但不混入四层：

- `memory_cards`：对话派生卡片/未完成话题，走记忆策略开关；
- `persona_corpus`：角色台词/示例，只影响说话风格；
- AstrBot 原生长期记忆：高级/原生面板，只读/清理/导出。

## 3. 存储位置

`<实例>/data/astrbot/plugin_data/qqbot_memory_bank/knowledge.db`

表：

### 3.1 `memory_layer`

```sql
id, layer, owner_id, scope, title, content, tags, priority,
content_hash, created_at, updated_at
```

- 唯一索引：`(layer, owner_id, content_hash)`；相同内容重复保存会更新标题/标签/优先级，不会重复插入。
- `priority` 高的先注入；同优先级按更新时间倒序。
- 人格层 `scope=''`；群层 `scope=owner_id`，便于控制台按群查。

### 3.2 `knowledge_entry`（个人 AI 记忆）

```sql
id, scope, persona_id, content, tags, source, confidence,
content_hash, created_at, updated_at,
actor_hash, actor_label, entry_kind
```

- 唯一索引：`(scope, persona_id, actor_hash, content_hash)`。
- `actor_hash` 不是 QQ 号，是 `sha256(scope + sender_id)` 前 16 位；用于隔离用户，避免明文 ID。
- `entry_kind`：`ai`（模型写入）、`legacy`（旧数据）、用户导入已迁移到 `knowledge_doc`。

### 3.3 `knowledge_doc`（共享知识库）

```sql
id, scope, persona_id, title, content, tags, source,
content_hash, created_at, updated_at
```

- 唯一索引：`(scope, persona_id, content_hash)`。
- 模型只能通过 `knowledge_search` 读取，不能写入、修改、删除。
- 控制台“知识库（用户导入）”是唯一写入口。

## 4. 自动召回

`qqbot_memory_bank` 注册 `@filter.on_llm_request()`，在每次主模型请求前：

1. 跳过命令（`/`、`[[`、`【`）和少于 4 字的短消息；
2. 读当前 `persona_id` 与 `scope`；
3. 注入 `[人格记忆]`：最多 `memory_layer_max_items` 条 / `memory_layer_max_chars` 字；
4. 注入 `[群记忆]`：同上；
5. 注入 `[共享知识库参考]`：按当前消息检索 top-k（默认 3），单条最多 420 字；
6. 注入 `[个人记忆]`：当前 `actor_hash` 的事实，最多 3 条；
7. 总预算 `auto_recall_max_chars`（默认 1200），超预算截断；
8. 设置事件 extra `qqbot_memory_bank_auto_recall=true`，并记录日志：
   `memory auto recall: layers=... docs=... facts=... chars=...`

注意：这是**追加**到 `req.system_prompt`，不覆盖 core/persona 提示词。召回的是文本参考，不是指令；资料内容不应写成“你必须……”来绕过人格/安全规则。

## 5. 控制台入口

实例 → 记忆：

| 面板 | 作用 |
|---|---|
| 人格记忆（常驻） | 选 persona_id，添加/删除身份、口癖、设定；每条有标题/标签/优先级 |
| 群记忆（常驻） | 用 scope 选择器选群，添加/删除群规、群背景 |
| 记忆卡片 | 未完成话题/卡片维护；重建索引/清理归档/导出 |
| 表达状态 | 情绪/风格快照、scope chips、pin/opt-out/reset/export |
| 群成员好感度 | 输入群号查成员分数/等级/最近事件 |
| 知识库（用户导入） | scope + persona，导入资料、查询、删除；模型只读 |
| AI 记忆库（自动提取） | 按 scope + persona 查 AI 事实和 actor 元信息；不提供导入 |
| 人格语料库 | 按 persona/profile 管理角色台词/示例 |
| 记忆与检索策略 | 四层注入开关、预算、top-k、卡片/收尾频率 |
| 高级原生记忆 | AstrBot 原生记忆只读/清理/导出 |

## 6. 配置

`qqbot_memory_bank_config.json`：

| 键 | 默认 | 说明 |
|---|---|---|
| `auto_recall_enabled` | `true` | 是否每轮自动召回四层 |
| `auto_recall_top_k` | `3` | 共享知识库 top-k |
| `auto_recall_max_chars` | `1200` | 自动召回总预算 |
| `auto_recall_personal_enabled` | `true` | 是否自动召回当前用户 AI 记忆 |
| `memory_layer_max_items` | `6` | 人格/群记忆每层最多条数 |
| `memory_layer_max_chars` | `600` | 人格/群记忆每层字符上限 |
| `auto_extract_enabled` | `true`（本项目实例） | 是否自动提取 AI 记忆 |
| `aux_enabled` | `true` | 辅助 AI 主提取，失败回退规则 |
| `extract_every_turns` | `4` | 每多少条用户消息提取一次 |
| `routing_mode` | `ai` | `ai` 辅助模型路由；`rules` 规则提取 |
| `routing_min_confidence` | `0.6` | 写人格/群核心记忆的最低置信度 |
| `auto_write_core_memory` | `false` | 是否允许 AI 自动写核心记忆；关闭时进收件箱 |
| `auto_recall_cards_enabled` | `true` | 是否由 memory_bank 统一召回记忆卡片 |

如果要“完全工具化、不自动注入”：设 `auto_recall_enabled=false`，模型只能用 `knowledge_search`/`memory_search`。

## 7. scope 格式

| 类型 | 格式 |
|---|---|
| OneBot 群 | `group:onebot:<数字群号>` |
| QQ 官方群 | `group:qq_official:<群 openid>` |
| OneBot 私聊 | `user:onebot:<QQ号>` |
| QQ 官方私聊 | `user:qq_official:<user openid>` |
| 全局资料 | `global` |

控制台的 scope 选择器会生成这些格式；不要手写混用 openid 和数字群号。

## 8. 当前验证与边界

已验证：

- `memory_layer` 增删改查、同内容去重、persona/group 隔离、排序；
- bot-11 真实消息触发自动召回日志：
  `memory auto recall: layers=2 docs=1 facts=0 chars=103`；
- 自动召回内容实际进入请求，模型回答引用了导入的“青苹果”资料；
- 测试用记忆层/资料已清理，`group_mention_only` 已恢复 `true`。

边界：

- 自动召回是 LIKE/关键词打分，不是 embedding 向量 RAG；中文同义改写召回有限。
- `persona_id` 依赖 `qqbot_core_reply` 在事件 extra 中 stamp；未 stamp 时回退 `default`。
- 人格/群记忆的“每层最多 6 条”是注入上限，不是存储上限。
- 共享知识库不会自动切块/摘要；长文档请自行拆成多条，单条建议 ≤1000 字。
- `knowledge_entry` 旧 `legacy` 数据没有 actor，按共享区处理；新 AI 事实才按用户隔离。

## 9. AI 路由与所有权（收口）

四层之间不再用固定关键词决定去向。`qqbot_memory_bank/memory_router.py` 把最近用户消息交给辅助模型，由模型返回 JSON 路由：

| route | 所有者 | 处理 |
|---|---|---|
| `user_fact` | AI 记忆库 | 按 `scope + persona_id + actor_hash` 写入 `knowledge_entry` |
| `persona_memory` | 人格记忆 | `auto_write_core_memory=true` 且置信度达标才写 `memory_layer(persona)`，否则进 `memory_inbox` |
| `group_memory` | 群记忆 | 同上，写 `memory_layer(group)` 或进收件箱 |
| `episode` / `open_topic` | 记忆卡片 | 只进 `memory_inbox` 建议；卡片正文由 memory_commands 的 AI episode 判断 |
| `knowledge_doc` | 用户导入知识库 | 只进 `memory_inbox` 建议，绝不自动改写用户资料 |
| `ignore` | 不保存 | 寒暄、问句、助手话、临时情绪、凭据等 |

辅助模型不可用时，才回退到规则提取 `user_fact`（置信度 0.55），不会静默写人格/群/资料。

`memory_inbox` 是待审核建议表，字段包含 `scope/persona_id/actor_hash/actor_label/route/content/confidence/reason/source/status`。待审核项不会被注入；Studio 的“AI 记忆库”面板底部会列出待审核建议，可“接受”写入人格/群记忆或用户知识库，也可“忽略”；`episode/open_topic` 只能人工忽略，卡片仍由卡片写入者管理。

记忆卡片的 `episode_summary()` 也改成 AI 判断：没有实质内容时返回 `[[NONE]]`，调用方过滤，不生成卡片。由此：

- 事实 → AI 记忆库；
- 人格/群规则 → 核心记忆或收件箱；
- 情节/未完成话题 → 卡片；
- 用户资料 → 只读知识库；
- 最近会话 → AstrBot 原生会话。

注入收口：bot-11 设置 `qqbot_memory_commands.cards_inject_enabled=false`，记忆卡片由 `qqbot_memory_bank` 的自动召回统一读取并按 scope/persona 去重，避免 `memory_cards` 与 memory_bank 两套注入重复。AstrBot 原生记忆仍保留为“最近会话”，不再由记忆卡片重复提供叙事背景。

注意：`auto_write_core_memory` 默认 `false`，AI 只能建议、不能自动改人格/群核心；需要自动写入时再显式打开。
