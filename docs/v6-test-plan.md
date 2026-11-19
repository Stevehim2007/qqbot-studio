# V6 情绪与表达状态 — 测试方案

目标实例：bot-11（OneBot/内置 NapCat，测试群 291690445）。bot-1 官方主动消息作为测试输入源；bot-1 测完停止，避免自动回复循环。

## P0 加载与隔离
- 插件 `qqbot_expression_state 1.0.0` 加载；`expression_state.db` 落在 `plugin_data/qqbot_expression_state/`。
- 不加载该插件时 core 4.3.0 行为与 4.2.1 一致（表达块为空）。
- scope 必须是 `group:onebot:<群号>` / `group:qq_official:<openid>`；不得混用。

## P1 情绪
- 单条消息 valence/arousal 变化不超过 ±0.15。
- 45 分钟半衰；只读快照不写库。
- 正/负/中性三类文案各观察一次，标签合理；不因单条夸张文本跳到极端。

## P2 风格
- 30 条样本前 `style_ready=false`；达到 30 条后生成 8 维向量。
- 向量每维 0..1；相同输入统计确定；emoji/问号/长度维度可解释。
- 不足 30 条时不把风格写进 prompt。

## P3 prompt 注入
- 社交块 ≤160 字，表达块 ≤120 字，合计 ≤260 字。
- 表达块只作“内部参考”，不得覆盖安全规则、工具规则、人格规则。
- 无插件/opt-out/`apply_effects=false` 时表达块为空。
- AstrBot 模式 `on_llm_request` 和 direct 模式都要注入。

## P4 管理面
- Studio GET 按 scope 返回情绪、风格样本数、8 维向量、optout。
- optout/pin/reset/export 四个写操作成功；测试 scope 用完即清。
- 文件权限必须允许 Studio 与 AstrBot 两侧读写，不能出现 `readonly database`。

## P5 安全
- DB 不保存消息原文、QQ 号、openid、群号明文（scope 除外）；不保存密钥。
- 用户输入 `[[tool:...]]` 不能直接改表达状态；只有插件观察/Studio 授权写。
- 导出内容不含隐私字段。

## P6 回归
- core 255、safe_tools 230、expression 7、deployer 31、server 46 全通过。
- unified addressed gate、90s 冷却、@/命令/私聊直通不回归。
- 重启 bot-11 后插件仍加载，DB 可读。

## P7 故障
- AstrBot 重启、NapCat 短暂断连后表达状态不丢。
- DB 损坏/不可写时 core 降级为空表达块，不阻塞回复。

## 阻断标准
- 表达块超过 260 字、覆盖安全规则、scope 串平台、Studio 写失败、核心单测失败 → 阻断发布。
