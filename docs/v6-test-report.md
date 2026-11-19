# V6 情绪与表达状态 — 测试报告

测试时间：2026-11-19；对象 bot-11（OneBot 291690445）；输入源 bot-1 官方主动消息 API。

## 结果摘要
- 单元测试：core 255 OK；safe_tools 231 OK；expression 7 OK；deployer 31 OK；server 46 OK；主项目 `875 OK`。
- 插件加载：`qqbot_expression_state 1.0.0`、core 4.3.0、safe_tools 3.3.5 均加载；OneBot 适配器已连接。
- 真实观察：`expression_state.db` 记录 38 条风格观察，已生成 8 维风格向量；群 scope 为 `group:onebot:291690445`。
- Studio API：`GET /api/instances/bot-11/expression` 返回情绪/风格/optout；测试 scope 的 optout、pin、export、reset 均成功。
- prompt hook：`safe_tools hook: instruction_chars=651 injected=True`；表达块由 `PersonaService.expression_context()` 读取，缺插件/optout 时为空。
- 回归：unified addressed gate、90s 冷却仍生效；普通陈述不回复，点名/直接问句回复。

## 真实数据
- 情绪：中性推荐消息 valence=0.0；感谢反馈后 valence≈0.18；arousal=0.0。
- 风格：38 条样本，warmth≈0.06、directness≈0.25、verbosity≈0.19，其余维度≈0；`style_ready=true`。
- 风格向量低值是正常结果：测试语料以短句陈述为主，未出现高情绪/高 emoji 文本。

## P4 好感度端到端（已通过）
- 真实消息：“电子羊，你上次建议我煮面加个蛋，我试了，确实更好吃，谢谢你。”
- 模型自主输出 `[[tool:favorability_adjust delta=1 confidence=0.8 reason="采纳了煮面加蛋的建议并反馈确实更好吃"]]`。
- safe_tools 直接执行 self-owned marker，未经过 ABI dispatcher；`social_state.db` 创建成功。
- `favorability`：scope `291690445`，actor_hash `2bc3a6f2b858190a`，score `1.0`。
- `favorability_event`：delta `1.0`、reason_code `model_tool`、event_label `advice_worked`、source `model_tool`。
- 回复正文正常发送，没有追加“工具执行失败”提示。
- 根因链：self-owned descriptor 未 advertised → ABI dispatcher 无 provider → kwargs 展开与 `(context, args)` 签名不匹配 → `_social_scope_actor` 把 `AstrMessageEvent` 当 `RequestContext` 读，返回 `no_actor`。四处均已修复。

## 未通过 / 限制
- 真实 @、私聊、多用户说话人无法只靠 bot-1 官方主动 API 制造；bot-4/7 不在测试群。
- 真实 @、私聊、多用户说话人无法只靠 bot-1 官方主动 API 制造；bot-4/7 不在测试群。
- 表达块的“文风是否真的改变回复”仍是软约束，未做 A/B 人工评分。

## 配置与清理
- bot-11：`participation.mode=unified`、`memory_mode=astrbot`、`persona.corpus_enabled=true`、`favorability_enabled=true`。
- 测试 scope `group:onebot:test-v6` 已 reset；测试 corpus 已删除。
- `utility_commands_require_at` 测试后必须恢复 `true`。
