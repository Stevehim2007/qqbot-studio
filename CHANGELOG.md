# 更新日志

本项目使用独立版本线：

- Linux 主项目：`qqbot_studio.__version__`
- QQ 增强插件：`plugins/qqbot_enhancer/metadata.yaml`
- Windows 发行版：Windows 仓库独立版本，内嵌 Linux core 版本单独记录
- AI 助手 B：独立仓库 `qqbot-studio-ai-assistant`


## v4.0.1

- 向导增加步骤下拉菜单。
- 部署完成后环境/账号/部署前三步锁定，只保留“完成与运行”；刷新后仍保持完成页。
- 保留提示词只读、服务控制、扫码和日志功能。
- 全量 441 tests OK。

## v4.0.0 — 2026-10-31

### 会话语义

- 一个 AstrBot conversation 绑定一个提示词、一个 persona 和一段独立记忆。
- 提示词列表改为模板库；置顶模板只作为新建会话的默认模板。
- 切换/置顶提示词不再触发 pending 或自动重建当前会话。
- `/新会话 名称 [提示词标题]` 创建会话、立即切换并绑定对应 persona。
- `/会话 名称` 只切换已有会话，使用目标会话自己的提示词/persona/记忆。
- `/会话列表` 显示标题、提示词、当前 x / 上限 y。
- 达到 `conversation_max_per_group`（默认 10）时，新建会话自动删除最老的非当前会话；失败则中止创建。
- `/删除会话 名称|短ID` 支持安全删除；删除当前会话前先切换。
- 向导中的机器人提示词改为只读，模板统一在控制台模板库管理。

### 配置与权限

- 插件配置损坏时按 `.bak` → 内存缓存 → Studio 镜像 → schema 默认值自动恢复。
- 损坏文件保留并生成 `.corrupt-*` 快照；显式保存才修复主文件。
- 记忆模式不再因一次解析失败而被重置为 `direct`。
- AI 工具权限读取失败保留上次有效 UI 值，并提供刷新重试。
- 提示词模板容量上限统一为 100 个 × 12000 字符，序列化 JSON 上限 2.6M，专用请求体 8 MiB。

### 群聊与表情

- 群聊身份上下文：昵称/QQ/群号只用于区分成员，不直接输出。
- ambient 群聊默认关闭；开启后低频参与、@ 必回、官方 @ 组件优先，失败降级 `@昵称`。
- ambient 全群上下文仅群聊注入；私聊不注入群聊文案。
- `/表情状态`/`/表情诊断` 显示收藏开关、权限、数量、大小限制、最近媒体和 AI 工具注入状态。
- AstrBot memory 模式注入短表情工具目录，修复模型误称“没装表情素材”；工具 marker 实际发送一张本地表情。
- 提示词模板文件名/内容变化按 mtime 热读取，改名后无需重启即可被下一轮 `/新会话` 识别。
- 收藏表情全局共享、支持多分类、语义标签 `tags/meaning/tone`。
- 统一 AI 工具 marker；一次回复最多一条文字 + 一个附件。

### 其它

- `/food` 接入 `food.dlsz.top`，固定 HTTPS、最多 5 家、不额外调用 LLM。
- 提示词换行保留；SnowLuma `#sl` 默认关闭；插件 schema 缺失回退主项目内置 schema。
- Ponytail 第二阶段低风险精简：源码/测试净减少约 195 行，新增运行依赖 0。
- Linux 真实容器 E2E 已覆盖 AstrBot 4.29.3 + SnowLuma 2.2.1、三端口、0.0.0.0、WebUI/noVNC 200、OneBot WebSocket 握手；QQ 扫码仍需人工。

### 验证

- Linux：438 tests OK。
- 插件：205 tests OK。
- 真实 Linux Docker E2E：AstrBot/SnowLuma/OneBot 自动部分通过；QQ 扫码仍需人工。

## v3.13.0

- 会话级提示词/persona/记忆基础语义。
- 提示词模板库、`/新会话`、`/会话列表`、pinned 默认模板。
- 损坏配置备份恢复、AI 权益/记忆模式读取稳定性。
- 表情保存诊断。

## v3.12.1

- Ponytail 第二阶段优化与最终 QA。
- 共享前端 API 工具、`gitutil.py`、测试 fixture 去重。

## v3.12.0

- 提示词模板/按名会话/ambient 群聊。
- QA P1 修复：容错、persona 冲突、私聊边界、容量上限。
- Linux Docker/AstrBot/SnowLuma E2E harness。

## v3.11.0

- 提示词列表、`/新会话`、ambient 低频参与、群身份上下文。

## v3.10.1

- AstrBot memory 模式当前发送者身份注入。
- 群 Persona/Conversation 官方 API 接入。

## v3.10.0

- AstrBot 每群独立 Persona/会话记忆。
- 群身份上下文、会话安全切换。

## v3.9.1

- Ponytail 第一阶段低风险清理。
- 配置/统计/提示词可靠性修复。

## v3.9.0

- `/food` 接入 food.dlsz.top。
- 群人格、schema 回退、`#sl` 关闭、提示词换行。
- AI 工具权限控制台入口。

## 更早版本

Linux v3.8.0 及更早：参见 Git 历史、`docs/qa-report-v2.md` 与各版本 tag。

Windows 发行版独立维护，正式 Release 与内嵌 core/plugin 版本以 Windows 仓库 README/VALIDATION 为准。
