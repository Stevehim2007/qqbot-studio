# STATE

## 当前目标

QQ Bot Studio 5.0 测试线：QQ 官方 + OneBot/NapCat 双平台，12 个独立插件，四层记忆（人格/群/个人 AI/共享知识库）与自动召回；保持已有实例数据、会话、记忆和 QQ 登录态。

## 版本

| 项目 | 版本 |
|---|---|
| 主项目 | `5.0.0-beta.42`（tag `v5.0.0-beta.42`） |
| 插件仓库 tag | `v4.4.0-beta.73` |
| core_reply | `4.3.19` |
| safe_tools | `3.4.2` |
| food | `3.0.6` |
| stickers | `3.0.4` |
| memory_commands | `4.3.3` |
| memory_bank | `0.7.1`（bot-1、bot-11） |
| expression_state | `1.0.0`（仅 bot-11） |

## 当前架构

- Studio：`qqbot_studio/`，Python HTTP 控制台 + Docker Compose 部署；单端口 8765，实例目录在 `~/.local/share/qqbot-studio/instances/`。
- 平台：
  - `qq_official`：AppID/AppSecret，群/用户 ID 为 openid；
  - OneBot v11：外部网关或内置 NapCat，数字群号/QQ 号。
- 插件仓库：`.qqbot-plugins-staging/`；构建脚本 `scripts/build_release.py`；12 个 ZIP + `manifest.json` + `catalog.json`。
- 记忆：
  - `qqbot_memory_commands 4.3.3`：卡片、收尾、purpose、FTS、Studio 维护；
  - `qqbot_memory_bank 0.7.1`：`memory_layer`（人格/群核心记忆）、`knowledge_entry`（个人 AI 事实 + `entry_kind='digest'` 群聊近况摘要）、`knowledge_doc`（用户导入共享资料）、自动召回；
  - `qqbot_expression_state 1.0.0`：情绪/风格，不调用模型；
  - `qqbot_core_reply 4.3.19`：唯一聊天 owner，人格注入、参与判定、群聊非 @ 硬门（挡掉的也静默记历史）、概率跳过可见 + 防装死 nudge、表达块注入。
- 群参数覆盖（v5.0.0-beta.31 新增）：`qqbot_safe_tools/spec_registry.py` 按 `_conf_schema.json` 生成标量 spec；core_reply/food/stickers 各自 `policy_overlay.py`（`config.overlay.v1`）在事件入口注入 `ContextVar`，`ConfigReader`/`_cfg`/`PluginConfig.raw` 先查 overlay；Studio 插件参数编辑器可把任意标量参数「加入群策略」，服务端逐条校验 `registered`。
- 控制台记忆结构：人格记忆、群记忆、个人 AI 记忆、知识库（用户导入）、记忆卡片、表达状态、群成员好感度、人格语料库、记忆与检索策略、高级原生记忆。

## 当前部署

- 服务器：`/home/ubuntu/qqbot-studio`，`5.0.0-beta.42`；`/console` HTTP 200。
- bot-1：QQ 官方，生产实例；健康。
- bot-11：OneBot + 内置 NapCat，`qq_account=871247845`；AstrBot healthy，aiocqhttp 已连接。
- bot-4 / bot-7：保留实例；健康。
- 已部署（2026-11-23）：主项目 beta.41、插件 core_reply 4.3.18（四台）/ safe_tools 3.4.2（四台）/ food 3.0.6 / stickers 3.0.4；bot-1 与 bot-11 装 memory_bank 0.7.1，辅助 key `/AstrBot/data/plugin_data/qqbot_aux_ai.json`（root 0600）两台都有。
- 实测（2026-11-23 直接读容器内 `data/astrbot/plugins/*/metadata.yaml`）：bot-1/4/7/11 全部 core 4.3.19 / safe_tools 3.4.2 / memory_commands 4.3.3；四台 `qqbot_memory_commands/aux.py` 均含 `from . import aux_ai` 相对导入修复。bot-1 与 bot-11 另装 memory_bank 0.7.1；bot-11 还有 expression 1.0.0。
- 注意：`studio-plugins/registry.json` 里的版本是旧的（4.1.0 / 3.2.5），插件实际是手工装进容器 data 目录的，判断线上版本要看 `metadata.yaml` 而不是 registry。

## 已验证
- 群聊近况摘要（bot-1，2026-11-23）：容器内用真实辅助 key 喂 5 条消息 → 日志 `memory digest updated scope=group:qq_official:PROBEDIGEST0002 chars=39 messages=5`，`knowledge.db` 出现 `entry_kind='digest'`/`source='aux'` 行；`inject_memory_context` 的 `system_prompt` 含 `[群聊近况（辅助模型摘要）]`；测试行已删（`count=0`）。
- 概率跳过可见性：`qqbot_core_reply 4.3.19` 每条跳过记 `group participation skipped: probability=… ignored=…`，连续 `participation.silence_nudge_after`（默认 4）条后强制参与一次（`probability=1.0`）。

- `memory_layer`：人格/群 CRUD、同内容去重、隔离、优先级排序；memory_bank 11 tests OK。
- 自动召回：bot-11 真实消息日志 `memory auto recall: layers=2 docs=1 facts=0 chars=103`，模型回答引用了导入的“青苹果”资料；测试数据已清理，`group_mention_only` 已恢复 `true`。
- 群聊非 @ 硬门：bot-11 日志 `group mention hard gate: dropped unmentioned group message`，无后续模型/工具调用。
- 好感度：模型自主 `favorability_adjust delta=1 confidence=0.8`，`social_state.db` 记录 scope `291690445`、score 2.0。
- 表达状态：真实消息后 `expression context injected mode=astrbot chars=20`。
- 测试：core 258、safe 231、memory 151、bank 11、expression 7、studio server 46、deployer 34（3 个本机 6185 端口段失败为环境占用）。
- 主项目全量 893 tests OK（beta.31，新增 13 个群策略登记测试）；测试使用系统空闲端口，不再依赖固定 6185。
- 插件参数「加入群策略」（beta.31）：主项目全量 **897 tests OK**；三个插件 overlay 契约（core_reply 264、food 203、stickers 173）与 safe_tools 242 全 OK；`registered` 映射、`0600` 文件权限、坏项丢弃、旧文件兼容、服务端 400 校验（不存在参数/敏感字段/不匹配/非对象）、`include_schema=0`、惰性 schema、默认收窄均有覆盖。

## 已知边界

- 自动召回是 LIKE/关键词 + 简单打分，不是 embedding 向量 RAG；中文同义改写有限。
- `persona_id` 依赖 core 在事件 extra 中 stamp；未 stamp 时回退 `default`。
- `knowledge_entry` 旧 `legacy` 数据没有 actor，按共享区处理。
- 共享知识库不自动切块/摘要，长文档需拆条。
- 真实 @、私聊、多用户测试受账号限制；bot-1 官方主动消息只能固定发送者。
- `memory_cards` 与 `persona_corpus` 保留，分别服务对话卡片和角色风格，不与四层混表。

## 下一步

1. 提交当前四层记忆代码、文档、删除冗余文件；打 tag 并推送。
2. 用干净端口环境重跑主项目全量测试。
3. 浏览器手工验收 Studio 新面板（人格/群记忆、scope 选择器、知识库导入）。
4. 视需要补 embedding/BGE 自动召回和知识库切块。

5. 已发布（2026-11-22）：插件 `v4.4.0-beta.64`（overlay）/ `beta.65`（include_schema）/ `beta.66`（core_reply 去死开关）/ `beta.67`（参数简介 + 去重复键）/ `beta.68`（参数分组 + 删点歌文案）/ `beta.69`（硬门消息进上下文）/ `beta.70`/`beta.71`（群聊近况摘要），主项目 `v5.0.0-beta.31`–`v5.0.0-beta.42`，均已 push。
6. 已部署：服务器 beta.35 + core_reply 4.3.15 / food 3.0.6 / stickers 3.0.4 / safe_tools 3.4.2；备份 `/home/ubuntu/deploy-backup-20261122-180436`。
7. 待人工验收：浏览器/手机打开 `/console` → 插件参数页点「加入群策略」→ 群策略页出现该参数 → 按群保存 → 日志确认 overlay 生效；再验「显示全部参数」与复杂类型（v2）仅全局提示。
8. v2 计划：`list` 15 个键（整表替换语义）与 `object` 类型覆盖。
