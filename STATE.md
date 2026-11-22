# STATE

## 当前目标

QQ Bot Studio 5.0 测试线：QQ 官方 + OneBot/NapCat 双平台，12 个独立插件，四层记忆（人格/群/个人 AI/共享知识库）与自动召回；保持已有实例数据、会话、记忆和 QQ 登录态。

## 版本

| 项目 | 版本 |
|---|---|
| 主项目 | `5.0.0-beta.30`（tag `v5.0.0-beta.30`） |
| 插件仓库 tag | `v4.4.0-beta.61` / `60ae636` |
| core_reply | `4.3.12` |
| safe_tools | `3.3.5` |
| memory_commands | `4.3.1` |
| memory_bank | `0.4.1` |
| expression_state | `1.0.0` |

## 当前架构

- Studio：`qqbot_studio/`，Python HTTP 控制台 + Docker Compose 部署；单端口 8765，实例目录在 `~/.local/share/qqbot-studio/instances/`。
- 平台：
  - `qq_official`：AppID/AppSecret，群/用户 ID 为 openid；
  - OneBot v11：外部网关或内置 NapCat，数字群号/QQ 号。
- 插件仓库：`.qqbot-plugins-staging/`；构建脚本 `scripts/build_release.py`；12 个 ZIP + `manifest.json` + `catalog.json`。
- 记忆：
  - `qqbot_memory_commands 4.3.1`：卡片、收尾、purpose、FTS、Studio 维护；
  - `qqbot_memory_bank 0.4.1`：`memory_layer`（人格/群核心记忆）、`knowledge_entry`（个人 AI 事实）、`knowledge_doc`（用户导入共享资料）、自动召回；
  - `qqbot_expression_state 1.0.0`：情绪/风格，不调用模型；
  - `qqbot_core_reply 4.3.12`：唯一聊天 owner，人格注入、参与判定、群聊非 @ 硬门、表达块注入。
- 控制台记忆结构：人格记忆、群记忆、个人 AI 记忆、知识库（用户导入）、记忆卡片、表达状态、群成员好感度、人格语料库、记忆与检索策略、高级原生记忆。

## 当前部署

- 服务器：`/home/ubuntu/qqbot-studio`，`5.0.0-beta.30`；`/console` HTTP 200。
- bot-1：QQ 官方，生产实例；健康。
- bot-11：OneBot + 内置 NapCat，`qq_account=871247845`；AstrBot healthy，aiocqhttp 已连接。
- bot-4 / bot-7：保留实例；健康。
- 所有实例已装 core 4.3.12 / safe_tools 3.3.5 / memory_commands 4.3.1；bot-11 另装 memory_bank 0.4.1 / expression 1.0.0。

## 已验证

- `memory_layer`：人格/群 CRUD、同内容去重、隔离、优先级排序；memory_bank 11 tests OK。
- 自动召回：bot-11 真实消息日志 `memory auto recall: layers=2 docs=1 facts=0 chars=103`，模型回答引用了导入的“青苹果”资料；测试数据已清理，`group_mention_only` 已恢复 `true`。
- 群聊非 @ 硬门：bot-11 日志 `group mention hard gate: dropped unmentioned group message`，无后续模型/工具调用。
- 好感度：模型自主 `favorability_adjust delta=1 confidence=0.8`，`social_state.db` 记录 scope `291690445`、score 2.0。
- 表达状态：真实消息后 `expression context injected mode=astrbot chars=20`。
- 测试：core 258、safe 231、memory 151、bank 11、expression 7、studio server 46、deployer 34（3 个本机 6185 端口段失败为环境占用）。
- 主项目全量 880 tests OK；测试已改为使用系统空闲端口，不再依赖固定 6185。

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
