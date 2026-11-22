# QQ Bot Studio V7 功能使用手册

> 适用版本：主项目 `5.0.0-beta.20` 以后；记忆插件 `qqbot_memory_commands 4.3.0`；core `4.3.10`；safe_tools `3.3.5`；expression_state `1.0.0`；memory_bank `0.1.0`。
>
> 本文是“功能 + 意义 + 使用方法”的完整说明。V1–V7 的验收口径：单测、API 测试和可自动化的 QQ 实机测试通过；需要真人多账号的项在文中注明测试边界。

## 1. 先看版本口径

项目里有三套容易混淆的版本：

| 版本线 | 含义 | 当前状态 |
|---|---|---|
| `v5.0.0-beta.x` | QQ Bot Studio 主项目发布版本 | 已部署 |
| `v4.4.0-beta.x` | 插件仓库发布版本 | 已发布 |
| V1–V9 | `next-major-framework` 产品阶段 | V7 主体完成 |
| V4.1–V4.5 | `memory-system-final-framework` 记忆子系统专项验收 | 本次补齐 |

**结论：** 本次交付把 V7 及以前的功能路径补齐；V4.1–V4.5 的必做项进入验收，V4.6+ 的扩展项不在本次范围。

## 2. 安装与进入控制台

```bash
git clone https://github.com/Stevehim2007/qqbot-studio.git
cd qqbot-studio
./install.sh --ip 0.0.0.0 --port 8765
```

部署完成后打开：

```text
http://服务器IP:8765/console
```

控制台里与本文相关的入口：

- **实例 → 插件**：安装/更新/删除插件；查看插件参数。
- **插件策略 / 群策略**：按群覆盖插件参数、白名单、模板。
- **记忆卡片 v4**：查看活跃/归档卡片、阶段摘要、重建索引、清理归档、导出。
- **人格语料库**：按人格/profile 添加角色台词、对话、设定。
- **表达状态**：查看情绪/风格、opt-out、pin、清空、导出。
- **NapCat 登录**：只在真正未登录且二维码新鲜时显示；已登录显示 online。

## 3. V1：辅助模型预算与能力策略

### 3.1 功能意义

辅助模型（判断、抽取、总结）如果不设预算，会在群里无限调用。V1 用两层控制：

1. **能力策略**：某个群/私聊允许或禁止哪些动作（读记忆、写记忆、搜索、工具等）。
2. **辅助预算**：`off/light/standard/advanced/full` 五档，按日/任务预留与提交额度。

### 3.2 使用方法

- 控制台 **插件策略**：选择实例 → 群/模板/全局 → 勾选能力 → 保存。
- 辅助预算：在策略页选择档位；或使用安全工具接口：
  - `policy.snapshot`
  - `policy.scope`
  - `aux.budget.status`
- 需要审计时查看控制桥返回的 `revision` 和 `audit`。

### 3.3 注意

- 群策略字段采用后端 `bool/int/float` 类型；前端显示为 `boolean/integer/number`。
- 保存失败时 UI 会显示真实 `forbidden_argument`/`invalid_param`，不会再只报 HTTPError。

## 4. V2：群策略与分群插件参数

### 4.1 功能意义

同一个插件在不同群应有不同行为：测试群允许高概率接话，生产群只回 @；某群禁用记忆，另一个群开启。V2 提供：

- 全局默认 → 群模板 → 单群覆盖的三层合并。
- 每个群独立 `revision`，并发保存用 CAS 防覆盖。
- 群管理员只能改允许的字段，不能改全局。
- 审计记录谁在什么时间改了什么。

### 4.2 使用方法

1. 控制台 → 实例 → **群策略**。
2. 输入群 ID：官方群填 32 位 `group_openid`；OneBot 群填数字群号。
3. 选择模板或逐项覆盖。
4. 保存后立即生效（插件读取群策略；少数缓存字段重启 AstrBot 生效）。

常用覆盖：

- `core_reply.participation.group_probability`
- `core_reply.participation.mode`（probability / model / unified，AI 参与入口）
- `core_reply.participation.model_confidence`
- `core_reply.participation.unified_require_name`
- `core_reply.participation.unified_names`
- `core_reply.participation.group_mention_only`（群聊只允许真实 @）
- `memory_commands.cards_inject_enabled`
- `memory_commands.cards_auto_wrapup_enabled`
- `safe_tools.favorability_enabled`
- `expression_state.apply_effects`

## 5. V3：AI 参与判定（三种模式）

### 5.1 功能意义

机器人不应每条群消息都回复。V3 提供三种成本/自然度不同的模式：

| 模式 | 是否每条都跑完整模型 | 谁决定回不回 | 适合 |
|---|---:|---|---|
| `probability` | 否 | 概率 + 冷却 + @/命令直通 | 省资源、稳定少回 |
| `model` | 否（先跑小判定器） | 判定器看最近上下文，输出 speak/observe | 平衡 |
| `unified` | 是 | 同一人格在一次生成中决定回复或 `[[NO_REPLY]]` | 最像人、最贵 |

### 5.2 配置

在 `qqbot_core_reply_config.json` 的 `participation` 下：

```json
{
  "participation": {
    "mode": "model",
    "model_confidence": 0.6,
    "model_timeout_seconds": 4,
    "model_max_tokens": 48,
    "model_daily_limit": 300,
    "unified_addressed_only": true,
    "unified_require_name": false,
    "unified_names": ["麻衣", "衣衣"],
    "unified_block_names": ["电子羊"],
    "user_cooldown_seconds": 90,
    "group_cooldown_seconds": 30
  }
}
```

### 5.3 使用建议

- 生产群只回 @：`mode=probability`，`group_probability=0`，`ambient_group_chat_enabled=false`。
- 测试群要人味：`mode=unified`，只给测试群开；配合 `unified_addressed_only=true`。
- 要 AI 判断但不希望 100% 回复：`mode=model`，让判定器看 `@你`、`@别人`、引用、发送者、最近 8 条历史；短句/纯标点/别人对话默认 observe。
- `unified_names` 只放当前人格名；旧账号名放 `unified_block_names`，避免换人格后旧名字还能唤醒。
- 冷却按用户/群分别计算，observe 不消耗冷却。

### 5.4 重要边界

- `@`、私聊、显式命令永远直通，不经过“是否接话”判定。
- `unified` 没接话时模型输出 `[[NO_REPLY]]`，core 会清空消息链、标记 `cancelled/not_sent`，不会发空消息。
- 全量 `unified` 的成本是每条非 @ 消息一次完整生成；不想付费就用 `probability=0`。

### 5.5 群聊非 @ 硬开关

`participation.group_mention_only`（默认 false）是**事件层硬门**，不是概率、不是 judge。

- 开启后：群聊消息如果没有真实 @ 机器人，在进入任何插件业务、记忆观察、工具或模型之前就被 `stop_event()` 丢弃。
- 私聊、真实 @、空 @ 仍然通过。
- 关闭后：恢复原来的参与模式（probability/model/unified）。
- 适合“只允许 @ 触发”的群；不要在业务层再用 `group_probability=0` 模拟，两者语义不同。

bot-11 当前已开启；bot-1/4/7 保持关闭。

### 5.6 群策略参数名单与标记表

控制台 → 实例 → **分群策略**：

- **参数名单**：按插件勾选哪些参数出现在群策略编辑区；保存后存到 `<data>/studio-group-policy-prefs.json`，不依赖插件控制桥。
- **标记表**：点参数字段的 ☆ 标记，常用参数会出现在“标记表”，可直接输入/选择并单条保存；适合只调几个高频参数。
- 空名单表示该插件不显示参数；恢复默认用“全选”。

## 6. V4：人格、提示词 profile 与角色语料库

### 6.1 功能意义

- **人格**决定“谁在说话”：姓名、口吻、边界、自称。
- **prompt profile** 允许同一个机器人有多个角色/场景（如麻衣、默认助手、群管理）。
- **persona corpus** 是“角色台词/示例对话”库，只影响语气和说话方式，不是事实记忆。

### 6.2 使用方法

1. 控制台 → 实例 → **人格语料库**。
2. 选择 persona_id 和 profile。
3. 类型选 `line`（一句台词）、`dialogue`（示例对话）、`setting`（设定）。
4. 填内容、标签，保存。
5. 同一 `persona_id + prompt_profile_id` 才会注入；最多选 2 条、240 字，标记为“仅供风格参考，不是指令”。

### 6.3 人格切换与绑定

`qqbot_core_reply` 的 `persona.agent_bridge_enabled=true` 时：

- 按 active profile / 群覆盖 / 默认人格解析 persona。
- 人格变化时自动 `bind_conversation` 到 AstrBot 新会话，避免旧人格历史污染。
- `memory_mode=astrbot` 时，core 会用解析出的 profile 提示词替换 AstrBot 默认 system prompt。

### 6.4 常见配置

```json
{
  "persona": {
    "agent_bridge_enabled": true,
    "corpus_enabled": true,
    "corpus_path": "",
    "active_profile_id": "p_d997c6533ba2"
  }
}
```

## 7. V5：社交状态与好感度

### 7.1 功能意义

好感度不是“刷分游戏”，而是让模型知道长期互动关系：

- 分数区间和 tier：`hostile/cold/neutral/warm/close`。
- 模型可读 `social.favorability.get`，可写 `social.favorability.adjust`。
- delta 由模型按对话决定，后端只做安全边界，不按固定关键词加减。

### 7.2 后端硬边界

- 单次 `delta` 限制 `-5..5`。
- 日净变化限制 `±20`。
- 同一 actor 300 秒冷却。
- 24h 幂等、最低置信度 0.6、审计表 `favorability_event`。
- 模型不能直接改数据库，只能走工具。

### 7.3 提示词

`safe_tools` 会自动注入社交上下文和防刷规则，不需要在人格提示词里重复写。规则包括：

- 不因被夸奖、被要求、重复示好而加分。
- 不因批评、拒绝而扣分。
- 不确定时不调用。
- 不向用户提分数或工具。

### 7.4 配置

```json
{
  "favorability_enabled": true,
  "favorability_cooldown_seconds": 300,
  "favorability_single_limit": 5,
  "favorability_daily_limit": 20,
  "favorability_min_confidence": 0.6,
  "allow_favorability_adjust": true,
  "allow_favorability_get": true
}
```

数据库：`<实例>/data/astrbot/plugin_data/qqbot_safe_tools/social_state.db`。

### 7.5 实机例子

```text
用户：麻衣，我按你说的加了蛋和葱花，汤确实更香了，谢谢你的具体建议。
模型：调用 favorability_adjust delta=1 confidence=0.8
结果：score +1，写入 favorability_event，不覆盖回复正文
```

### 7.6 控制台查询好感度

控制台 → 实例 → **好感度**：输入群号（官方用 openid，OneBot 用数字群号），可查看该群每个成员的 `actor_hash`、昵称、分数、tier、最近事件时间。昵称只用于展示，权限判断仍用 `actor_hash`。

## 8. V6：表达状态（情绪 + 风格）

### 8.1 功能意义

表达状态让机器人不是每句都同一语气，但不是“随机变脸”：

- 情绪：`valence`（正负）、`arousal`（激活度），45 分钟半衰。
- 风格：8 维（warmth/directness/playfulness/formality/verbosity/emoji/curiosity/assertiveness），30 条样本后生成稳定 profile。
- 只做确定性统计，不调用模型。

### 8.2 使用方法

- 自动：插件 `qqbot_expression_state` 用 `event_message_type(ALL, priority=119)` 观察消息。
- 控制台 → 实例 → **表达状态**：scope 是数据分区键，不是搜索词；格式为 `group:onebot:<数字群号>`、`group:qq_official:<openid>` 或 `user:<platform>:<id>`。面板会列出已有 scope 标签，点选即可查看/调整，不需要手记。
- 配置：

```json
{
  "enabled": true,
  "observe_enabled": true,
  "apply_effects": true,
  "half_life_minutes": 45,
  "max_event_delta": 0.15,
  "style_min_samples": 30,
  "inject_max_chars": 120
}
```

数据库：`<实例>/data/astrbot/plugin_data/qqbot_expression_state/expression_state.db`。

### 8.3 scope 是什么

表达状态的 scope 是数据分区键，不是搜索词：

- `group:onebot:<数字群号>`：OneBot 群。
- `group:qq_official:<openid>`：官方群。
- `user:<platform>:<id>`：用户级。

控制台面板会列出已有 scope，可点击切换；不要手填“关键词”。`pin` 固定某个风格维度，`optout` 关闭该 scope 的观察/注入，`reset` 清空。

### 8.4 注意

- 用户 opt-out 后不观察、不注入。
- pin 的维度不会被自动 profile 覆盖。
- 表达块最多 120 字，和社交块合计受控。

## 9. V7：记忆系统

V7 是本轮重点，分三层：

1. **L0 原生记忆**：AstrBot 会话 + 长期记忆，五种 scope。
2. **L1 派生卡片**：`memory_cards.db` 的 episode/open_topic/purpose/fact/preference 等。
3. **L2 知识记忆库**：`qqbot_memory_bank` 的结构化知识条目，模型按需工具检索。

### 9.1 五 scope 与平台命名空间

- `user`：个人偏好。
- `group`：群共享事实。
- `conversation`：当前会话。
- `persona`：人格绑定记忆。
- `global`：全局稳定事实。

平台前缀：

- 官方：`group:qq_official:<32位openid>` / `user:qq_official:<openid>`
- OneBot：`group:onebot:<数字群号>` / `user:onebot:<数字QQ号>`

**不要把 openid 和数字 QQ 号互转。**

### 9.2 卡片 v4 的字段与生命周期

- `source_key`：scope+kind+content 的哈希，防重复。
- `polarity`、`tags`、`relationship_target/kind`、`source_turn_ids`、`pinned`、`expires_at`、`use_count`。
- `session_state`：阶段摘要、未完成话题、purpose、last_wrapup_revision。
- `audit_log`：谁在何时对哪张卡做了什么。
- 归档：低 clarity/长期未用/超过容量；活跃上限 60，超过时归档到 48。
- `sweep`：归档衰减卡 + 清理 365 天前归档卡。

### 9.3 收尾节奏

- 每 6 轮：阶段摘要，只写 `session_state` + `wrapup_ledger(kind='phase')`，不生成重复 episode 卡。
- 每 8 轮：完整收尾，生成 durable episode/open_topic/purpose 卡，写 session_state/audit。
- 配置：

```json
{
  "cards_auto_wrapup_enabled": true,
  "cards_wrapup_every_turns": 8,
  "cards_phase_every_turns": 6,
  "cards_purpose_enabled": true,
  "cards_group_member_enabled": false,
  "cards_inject_enabled": true,
  "cards_max_cards": 4,
  "cards_max_chars": 480
}
```

`cards_group_member_enabled=true` 时群聊卡片 scope 变为 `group_member:<platform>:<群号>:<发送者>`，实现群内每人独立卡片；默认关闭，使用群共享 scope。

### 9.4 检索

- FTS5 trigram 索引，中文可搜；`card_fts` 由触发器维护。
- 五因子评分：relevance、recency、importance、clarity、usage；FTS 命中额外 +0.25。
- 每 kind 配额、persona/actor 过滤、过期过滤。
- 默认注入最多 4 张/480 字；purpose 卡注入时写 `purpose_usage`。

### 9.5 写入防脏数据

`durable_episode()` 只保留用户明确说出的长期事实（我叫/我喜欢/我讨厌/我住在/记住/计划/生日/过敏等），最多 3 条/240 字；过滤：

- `[[NO_REPLY]]`、`mqqapi://`、`ComponentType.`
- markdown 图片/链接、@name(id)
- 助手回显、寒暄、问句、纯媒体

记忆卡面板可执行：

- 重建索引
- 清理归档（365 天）
- 导出
- API 还支持 `metrics/audit/delete/purpose`：
  `POST /api/instances/<id>/memory-cards/maintenance`
  body: `{"action":"metrics","scope":"group:onebot:123"}`

### 9.6 命令

群内默认要求 @ 机器人（`utility_commands_require_at=true`）：

```text
@机器人 /记忆
@机器人 /记忆 查看
@机器人 /记忆 搜索 火锅
@机器人 /记忆 添加 我喜欢吃火锅
@机器人 /记忆 卡片
@机器人 /记忆 收尾
@机器人 /记忆 预览清空
@机器人 /记忆 确认 <token>
@机器人 /记忆 取消 <token>
```

别名：`/记忆管理`、`/memory`。会话/人格命令：`/会话 状态`、`/会话 新建`、`/人格 列表`、`/人格 切换 <id>`。

### 9.7 用户导入知识库与 AI 记忆

两套数据物理分开：

| | 用户导入知识库 | AI 记忆 |
|---|---|---|
| 表 | `knowledge_doc` | `knowledge_entry` |
| 写入 | Studio 导入/删除，模型不能写 | `memory_add` 或辅助 AI 自动提取 |
| 读取 | 模型调用 `knowledge_search` | 模型调用 `memory_search` |
| 分区 | `scope + persona_id`，导入资料共享 | `scope + persona_id + actor_hash`，按用户隔离 |
| 用途 | 群规、设定、资料、角色台词 | 偏好、习惯、约定等对话事实 |

控制台 → 实例 → **知识库（用户导入）**：选 scope/persona，填写标题、正文、标签后导入；可查询和删除。
控制台 → 实例 → **AI 记忆库（自动提取）**：只查看 AI 写入的事实和 actor 元信息，不提供导入。

`scope` 示例：

- `group:onebot:291690445`
- `group:qq_official:<openid>`
- `user:<platform>:<id>`
- `global`：所有 scope 可检索的公共资料

### 9.8 注入优化

- 默认卡片注入受 4 张/480 字限制。
- 若觉得 prompt 太重：设 `cards_inject_enabled=false`，让知识库只在工具调用时返回；或把 `cards_max_cards` 降到 2。
- 人格提示词、expression、social、corpus、history 仍各自有预算；不要把整段记忆手写进人格提示词。

### 9.9 AI episode（叙事记忆）

`cards_episode_ai_enabled` 与 `cards_episode_enabled` 是两条路径：

- `cards_episode_enabled=true`：规则版 `durable_episode()`，只收长期事实，最多 3 条/240 字。
- `cards_episode_ai_enabled=true`：完整收尾时由辅助 LLM 生成 120–200 字叙事，写“发生了什么/用户情绪/结果/未完成事项”，失败回退规则或跳过。
- 两者都关：不生成新 episode；知识库仍按工具/自动抽取记录事实。
- bot-11 当前：`cards_episode_enabled=true`、`cards_episode_ai_enabled=true`，用叙事 episode；知识库负责事实，二者分工。

## 10. 插件清单（本次 12 个）

| 插件 | 作用 |
|---|---|
| `qqbot_core_reply` | 唯一聊天 owner、persona、参与判定、回复注入 |
| `qqbot_safe_tools` | 能力策略、marker、工具执行、好感度 |
| `qqbot_memory_commands` | `/记忆`、卡片、L0 管理 |
| `qqbot_memory_bank` | 知识库工具、自动抽取 |
| `qqbot_expression_state` | 情绪/表达风格 |
| `qqbot_web_reader` | 网页读取 |
| `qqbot_image_search` | 图片搜索 |
| `qqbot_food` | 吃什么/提交/确认 |
| `qqbot_stickers` | 表情包 |
| `qqbot_weather` | 中国天气网 7 天 PNG |
| `qqbot_fortune` | 每日运势 |
| `qqbot_scheduler` | `/定时`、主动发送 |

## 11. 测试与验收

### 11.1 单测

```bash
cd "/home/stevehim/QQ Ai bot"
python3 -m unittest discover -s tests -q

cd .qqbot-plugins-staging/plugins/qqbot_memory_commands
python3 -m unittest discover -s tests -q

cd ../qqbot_core_reply && python3 -m unittest discover -s tests -q
cd ../qqbot_safe_tools && python3 -m unittest discover -s tests -q
cd ../qqbot_expression_state && python3 -m unittest discover -s tests -q
cd ../qqbot_memory_bank && python3 -m unittest discover -s tests -q
```

本次结果：

- 主项目：876 tests OK
- memory_commands：150 tests OK
- core_reply：258 tests OK
- safe_tools：231 tests OK
- expression_state：7 tests OK
- memory_bank：6 tests OK
- deployer：32 tests OK

### 11.2 验收表

| 阶段 | 验收 |
|---|---|
| V1 | 能力策略/辅助预算 API、按群覆盖、审计 revision |
| V2 | 群策略 CAS、模板、字段类型、群管理员边界 |
| V3 | probability/model/unified、@/命令直通、NO_REPLY 抑制、冷却 |
| V4 | persona/profile、agent bridge、corpus 注入、Studio 入口 |
| V5 | 好感读/写工具、单次/日/冷却/幂等/审计 |
| V6 | 情绪半衰、8 维风格、30 条门槛、opt-out/pin/reset/export |
| V7 | FTS、6/8 收尾、48 归档目标、purpose、sweep/export/delete、group_member、知识库工具 |

### 11.3 测试边界

- 官方 bot 主动消息可自动注入测试群；真实 @、私聊、多用户、多群仍需要真人账号或第二个 OneBot 账号。
- 官方群用 openid，OneBot 群用数字群号，不能混用；切换平台后旧群策略/白名单/记忆 scope 需按新平台重填。
- NapCat 登录态在 `data/napcat/qq`；重启后如未快速登录，用控制台二维码登录，不要自动清空登录态。

## 12. 故障排查

| 现象 | 检查 |
|---|---|
| 控制台保存群策略 HTTPError | 看返回 code：`forbidden_argument`/`invalid_param`；强刷 console.js |
| 辅助模型“控制桥未启用” | 确认 Studio 启动带 `QQBOT_PLUGIN_CONTROL_ENABLED=1`；`/api/instances/<id>/plugin-control` |
| 记忆卡片只显示 default profile | `qqbot_core_reply_config.json` 权限需 `root:ubuntu 0640`；Studio 会自动修复后重试 |
| 群内不回复 | 检查 `platform_settings.id_whitelist` 是否含 `qqbot-onebot:GroupMessage:<群号>`；检查参与模式/冷却 |
| @ 别人也被回复 | 检查 explicit 分支；@ 消息直通，需要元数据/成员偏好规则，不要只靠 prompt |
| 重启后 NapCat 不登录 | `data/napcat/qq` 是否挂载；不要删除；用控制台二维码；看 `正在快速登录` 日志 |
| 记忆卡很乱 | 已加 durable 清洗；旧卡用维护接口 `sweep/delete` 或备份后清理 |

## 13. 回滚

- 关闭 V6：`expression_state.enabled=false` 或删除插件。
- 关闭好感写入：`favorability_enabled=false`，读取仍可用。
- 关闭卡片注入：`cards_inject_enabled=false`。
- 回到纯 @ 回复：`participation.mode=probability`、`group_probability=0`、`ambient_group_chat_enabled=false`；或打开 `participation.group_mention_only=true` 做事件层硬门。
- 数据库迁移前先备份 `memory_cards.db`、`social_state.db`、`expression_state.db`、`persona_corpus.json`。

## 14. 一句话总结

V1–V7 现在不是“堆功能”，而是四层控制：**能力/预算控制成本，群策略控制边界，人格/参与控制像不像人，记忆/表达/好感控制长期关系**。需要少回就调参与模式；需要人味就只给测试群开 unified；需要记忆就分卡片、知识库、语料库三条线，不要把所有东西塞进一个 prompt。

## 15. V4.6+ 扩展路线（以后做，条件触发）

这些不是 V7 的欠账，而是 v4 之后的条件扩展；默认都不启用。

| 能力 | 触发条件 | 说明 |
|---|---|---|
| 卡片 embedding 融合 | L0 语义检索开启率 >30%，或 BM25 top-k 抽检合格率 <80% | 0.7 BM25 + 0.3 cosine；纯 BM25 回退不变 |
| 跨会话 LLM 反思 `reflection.cross_session` | full 档、每周一次、默认关 | 只产出报告，不自动改卡 |
| LLM 语义去重 `fuzzy` | standard 档起 | LLM 只做候选过滤，阈值规则不变 |
| `card.organize` 卡片整理 | full 档 + 用户确认 | 默认关；需要用户显式开启 |
| purpose 主动实验评估 | V4.3 上线后收集 offered/used ≥1 个月 | 用数据决定是否改默认值 |
| 多实例/跨 bot 卡片共享 | 产品线授权 + 同步机制立项 | 不在当前架构内，单独设计 |

原则：V4.6+ 只能“增强”，不能改变 V4.1–V4.5 的确定性基线；任何增强失败都必须回退到 BM25/规则路径。

