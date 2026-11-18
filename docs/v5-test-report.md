# V5 测试执行报告

测试对象：bot-11「电子羊」（OneBot 内置 NapCat，群 291690445）
对照组：bot-1 官方机器人（官方群 A29510C5672E21477F2B7F07329C65CA）
执行时间：2026-11-18
测试方案：`docs/v5-test-plan.md`

## 结果总表

| 用例 | 结果 | 证据/说明 |
|---|---|---|
| P0-A4 普通陈述不回复 | 通过 | “今天食堂的饭好难吃，随便说说。” 无发送 |
| P0-A5 直接问句回复 | 通过 | “电子羊你怎么看？” 收到一条回复 |
| P1-Observe 不相关消息 observe | 通过 | “今天天气不错，适合晒被子。” 无发送 |
| P1-Speak 点名后接话 | 通过 | “电子羊，你今天怎么这么安静？” 有回复 |
| P1-冷却 | 通过 | 连续 3 条“你怎么看？”只回 1 条 |
| P1-预算/日限额 | 通过 | 单元测试覆盖；真实连续消息触发冷却 |
| P0-A1 真实 @ | 未测 | bot-1 官方 API 不能伪造 @ 某个 OneBot 账号；需要用户账号 |
| P0-A2 空 @ | 未测 | 同上 |
| P0-A3 私聊 | 未测 | 需要用户私聊机器人 |
| P0-A6 @ 命令 | 部分 | `/option` 在临时关闭 require_at 后通过；`/记忆`、`/天气`、`/每日运势` 未在非 @ 条件下触发 |
| P2 多说话人 | 部分 | bot-4/7 不在测试群；只能用固定发送者 Onesleep/3889010444；标签/去标识单测通过 |
| P3 persona corpus | 部分 | `CorpusStore.select` 能按 tag 选中并生成 `[PERSONA_EXAMPLES]`；模型未按示例改变风格，软约束不保证 |
| P4 favorability | 未通过/待修 | 单测 230 OK；真实模型未调用 `favorability_adjust`，`social_state.db` 未创建 |
| P5 marker 安全 | 通过（窄） | 用户输入 `[[tool:favorability_adjust ...]]`/未知工具未执行，未创建 DB |
| P6 双平台命名空间 | 通过 | bot-1 卡片 scope 为 `group:qq_official:<openid>`；bot-11 为 `group:onebot:<群号>` |
| P7 回归 | 通过 | core 255 tests、safe_tools 230 tests、主项目 875 tests |
| P8 故障恢复 | 部分 | 多次重启 AstrBot 后 healthy；NapCat 曾 ECONNREFUSED 后自动重连；QR 在线时不显示过期码 |

## 本轮发现并修复

1. unified 模式原本对所有非 @ 消息都跑完整生成并回复。
   - 增加 `participation.unified_addressed_only`、`unified_names`、`unified_address_markers`。
   - 未点名/无问号/无第二人称的普通陈述在进入模型前被判定为“不需要接话”；不是概率。
2. unified 回复没有冷却，连续追问会连续回复。
   - 增加 `unified_cooldown_remaining` / `mark_unified_reply`，仅在真正发送后记冷却。
3. corpus 的 tag 之前要求整句等于 tag，中文问句几乎不会命中。
   - 改为 tag 子串匹配；`CorpusStore.select` 可直接验证。

## 未完成/限制

- 真实 @、空 @、私聊、多用户说话人无法仅靠官方 bot 主动 API 完成；需要用户账号或第二个 OneBot 账号。
- P4 需要模型主动选择工具；当前提示/工具描述不足以稳定触发，应继续调整工具说明或在测试里增加显式工具用例。
- 测试期间临时关闭过 `utility_commands_require_at`，已恢复 true；测试 corpus 已删除；bot-1 已恢复原配置并启动。
