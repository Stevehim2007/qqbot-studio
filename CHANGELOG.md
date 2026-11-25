## v5.0.0-beta.45（测试版）— 群聊上下文不再漏记「没被回复的消息」

- `qqbot_core_reply 4.3.22`：把「记录群消息」提到参与判定之前。此前 `participation.group_probability=0`
  的群会在概率门处直接返回、什么都不记 → 群聊上下文永远为空。现在每条进站群消息先落 history，
  再决定回不回；被 @ 时模型能看到群里完整的对话。
- 配套 `4.3.21`：注入时记 `group context injected: messages=… chars=… group=…`，方便确认上下文真的进了 prompt。
- 插件仓 tag `v4.4.0-beta.76`；四台实例已升级（bot-1/4/7/11 = 4.3.22）。
- 验证：core_reply 274 tests、插件仓 8 tests、主项目 901 tests OK。

## v5.0.0-beta.44（测试版）— 群聊上下文可观测（core_reply 4.3.21）

- `qqbot_core_reply 4.3.21`：每次把群聊上下文注入 system prompt 时记一行
  `group context injected: messages=… chars=… group=…`，便于确认「到底有没有读到群里的对话」。
- 插件仓 tag `v4.4.0-beta.75`；主项目 `v5.0.0-beta.43` 起已含 `QQBOT_PLUGIN_MARKET_MIRROR` 与 30s/重试，
  服务器实测：镜像取 catalog 1.1s、取 132KB ZIP 1.6s，bot-1 插件市场已从陈旧的本地 catalog（4.0.x）
  切到线上 4.3.20/3.4.2。
- 验证：core_reply 273 tests、插件仓 8 tests、主项目 901 tests OK。

## v5.0.0-beta.43（测试版）— 群聊上下文进模型 + 插件市场镜像/重试

- `qqbot_core_reply 4.3.20`：新增「群聊上下文」注入——把最近 N 条群消息（含群友之间、非 @ 的对话）
  按时间顺序塞进 system prompt，模型终于能看到群里发生了什么，而不是只看到自己参与的轮次。
  新增四个参数（`群内参与`分组）：`group_context_enabled`（默认开）、`group_context_limit`（默认 30 条）、
  `group_context_ttl_seconds`（默认 3600 秒）、`group_context_max_chars`（默认 3000）。关闭后回落到旧的
  「群聊说话人参考」块（8 条 / 10 分钟，仅用于区分谁在说话）。
  代价：每次被 @ 时多带一些上下文，token 与延迟略增，可用 `group_context_limit` 调小。
- 插件市场：新增 `QQBOT_PLUGIN_MARKET_MIRROR` 环境变量（例如 `https://gh-proxy.com/`），
  把 GitHub 上的 `catalog.json` 与插件 ZIP 自动改写走镜像；镜像主机自动计入受信主机。
  远程拉取超时 15s → 30s，并对连接类失败重试一次——国内服务器直连
  `raw.githubusercontent.com` 常见 16–30s 或只有 IPv6 解析，之前表现为「远程插件市场返回 HTTP 错误」。
- 验证：core_reply 273 tests、插件仓 8 tests、主项目 901 tests OK。

## v5.0.0-beta.42（测试版）— 入站可见 + 重复投递去重

- `qqbot_core_reply 4.3.19`：每条进站消息先记 `event received: umo=… private=… text=…`，
  平台没把消息送进来 vs 插件把消息静默丢掉，一眼可分。
- 重复投递去重：同 `umo + 平台 message_id` 在 15 秒内只处理一次，第二次记
  `duplicate inbound event ignored` 后直接返回——QQ 官方通道会重投同一条消息，之前会回两遍。
- 实测：bot-1 容器有到腾讯 443 的 established 连接（QQ 官方 ws 在线）；
  bot-11 的 NapCat 容器里 QQ 进程已死（只剩 entrypoint），重启后需要扫二维码登录才能收 OneBot 消息。
- 验证：core_reply 270 tests、主项目全量 898 tests OK。

## v5.0.0-beta.41（测试版）— 群消息静默跳过可见 + 防「装死」

- `qqbot_core_reply 4.3.18`：非 @ 群消息按概率跳过时**不再毫无日志**，每条都记
  `group participation skipped: probability=… ignored=… group=… text=…`，排查"机器人不理我"有据可依。
- 新增 `participation.silence_nudge_after`（默认 4，0=关闭）：同一个群连续这么多条消息没被回复后，
  下一条强制参与一次（概率按 1.0 走冷却门），日志记 `group participation nudged after N silent skips`，
  避免 12% 概率下看起来像掉线。成功参与后计数清零。
- `qqbot_memory_bank 0.7.1` 部署到 bot-1，辅助 AI key 复制到位：bot-1 现在也有群聊近况摘要与四层记忆。
- 说明：bot-1 此前"只回第一条"是策略问题——`participation.mode=probability`、`group_probability=0.12`
  下 88% 的非 @ 群消息会被静默丢弃；重启无效，因为不是卡死。
- 验证：core_reply 268 tests、memory_bank 19 tests、主项目全量 898 tests OK。

## v5.0.0-beta.40（测试版）— 群聊近况摘要（辅助模型）

- `qqbot_memory_bank 0.7.0`：新增**群聊近况摘要**。`observe_message`（priority 118，早于回复硬门）
  把每条群消息收进按群的滚动缓冲，累计 `digest_every_turns`（默认 20）条后交给**辅助模型**
  （`aux_ai.generate`，DeepSeek 等独立 key，不动主 provider）压成一段中文近况；
  摘要存进 `knowledge.db`（`entry_kind='digest'`，按 scope+persona 隔离，只留最新 `digest_keep` 条）。
- 注入：`inject_memory_context` 在 prompt 最前面加一段「群聊近况（辅助模型摘要）」，
  所以机器人被 @ 时才把这段时间的群聊"读"进上下文——不回复的时候只花辅助模型的便宜 token。
- 新参数（`qqbot_memory_bank` 配置，`ui_group=群聊近况摘要`）：`digest_enabled`（默认开）、
  `digest_every_turns`（20）、`digest_keep`（2）、`digest_max_chars`（400）。
- `qqbot_memory_bank 0.7.1`：摘要走独立 `entry_kind='digest'`，并在 `search()` 里排除，
  避免它混进「个人记忆」/`memory_search` 结果；`add_digest` 只保留最新 `digest_keep` 条。
- 需配套 `qqbot_memory_bank 0.7.1`（`v4.4.0-beta.71`）。该插件与辅助 AI key 目前只在 bot-11。

## v5.0.0-beta.39（测试版）— 被 @ 门挡掉的群消息也进入上下文

- `qqbot_core_reply 4.3.17`：`participation.group_mention_only` 的硬门在 `stop_event()` 之前
  新增 `_observe_without_reply()`，把未 @ 机器人的群消息写进该群对话历史——**不调用任何模型、零 token**。
  效果：群里整天聊天但机器人没被 @ 时，它依然"读过"这些消息；之后被 @ 时能看到刚才聊了什么。
- 关系网/事实记忆本来就不受该门影响：`qqbot_memory_bank` 的 `observe_message` 挂 priority 118，
  早于 core_reply 的硬门 120，注释即写明"extraction is independent of whether the main bot chose to reply"；
  实测 bot-11 的 `knowledge.db` 已有 34 条记忆，最近几条为当日写入、来源 `aux`。
- 需配套 `qqbot_core_reply 4.3.17`（`v4.4.0-beta.69`）。

## v5.0.0-beta.38（测试版）— qqbot_core_reply 参数分组、删掉点歌文案键

- `qqbot_core_reply` 的 108 个参数全部补上 `ui_group`，设置页从"115 项平铺"变成 13 个分组：
  基础 15 / 群内参与 19 / 冷却 4 / 上下文 5 / 人格 23 / 模型与识图 12 / 氛围插话 8 /
  表情回应 3 / 会话管理 5 / 点歌 4 / 命令与回复 8 / 安全工具 1 / 内部 1。
- 删除 7 个纯文案参数（`song_success_prefix`、`song_disabled_reply`、`song_empty_reply`、
  `song_not_found_reply`、`song_too_large_reply`、`song_error_reply`、`song_cooldown_reply`），
  文案改为插件内常量；四台实例的现值本来就等于默认值，行为不变。schema 与默认值键集仍严格相等。
- 需配套 `qqbot_core_reply 4.3.16`（`v4.4.0-beta.68`）。

## v5.0.0-beta.37（测试版）— 修复群策略页 ReferenceError

- 修复 beta.36 引入的 `collapsedPolicyPlugins is not defined`：卡片收起判定的两个辅助函数
  原先定义在模块作用域，引用了 `buildGroupPolicySection()` 内部的局部状态，导致整段群策略页脚本报错。
  现已移入 `buildGroupPolicySection()` 内部，并加静态测试守住作用域（断言函数带 4 空格缩进、位于函数体内）。

## v5.0.0-beta.36（测试版）— 全量显示时插件卡片不再一次全展开

- 群策略页勾选「显示全部参数」后，每个插件卡片默认**收起**，只保留标题栏（插件名、参数个数、启用开关）；
  点「▸ 展开（N）」才展开该插件的参数，展开内容在卡片内部滚动（`max-height: 44vh`），
  卡片占位不再随参数数量膨胀。
- 下拉框选中某个插件时，该插件自动展开；手动展开/收起的状态在全量模式切换后仍保留。
- 网格改为 `align-items: start`，收起与展开的卡片顶部对齐，不会互相拉伸。

## v5.0.0-beta.35（测试版）— 群策略参数有简介、选中框重做、清理重复参数

- 群策略页每个参数控制项下面新增一行简介：`param_schema` 现在为**每个**字段返回 `description`
  （手写的 33 条策略参数逐条写文案，其余从插件 `_conf_schema.json` 生成），控制台直接渲染，不再只靠 hover。
- 重做群策略页所有下拉框（插件筛选、scope 类型、模板、枚举参数、标记表）：统一 34px 高度、
  9px 圆角、CSS 绘制的箭头（无 `url()`、无图片资源）、hover 边框反馈与 focus 光环，移动端 38px 满宽。
- 清理 `qqbot_core_reply` 的重复参数：`reply_probability`（= `participation.group_probability`）、
  `group_cooldown_seconds`（= `cooldown.seconds`）、`llm_timeout_seconds`（= `llm.timeout_seconds`）
  从 schema / 默认值表移除，编辑器不再重复展示；旧实例里已存的值仍然生效（运行时代码先读规范键、再回退旧键）。
- 需配套 `qqbot_safe_tools 3.4.2`、`qqbot_core_reply 4.3.15`（`v4.4.0-beta.67`）。

## v5.0.0-beta.34（测试版）— 删掉不生效的默认 LLM 开关

- `qqbot_core_reply` 移除 `block_default_llm_for_group_non_at`：该参数只存在于 schema 与默认值表，
  运行时代码从不读取（`reply_pipeline.block_default_llm()` 20 处调用都是无条件阻断），
  开与关没有任何区别，留着只会误导。
- 「消息进不进 AI」现在只有一个开关：`participation.group_mention_only`，描述统一改为
  「只有真实 @ 机器人的群消息会进入 AI 处理，其余群消息在事件层直接丢弃、不消耗模型；私聊不受影响」，
  群策略页标签同步为「群消息进 AI 的开关（只允许 @）」。
- Studio 的旧版参数分组表（`qqbot_studio/static/services/plugin-config.js`）同步移除该键。
- 需配套 `qqbot_core_reply 4.3.14`（`v4.4.0-beta.66`）。

## v5.0.0-beta.33（测试版）— 群策略页按插件折叠 + 常用参数默认展示

- 群策略页不再一次性平铺所有插件、所有参数：每张插件卡片新增「▾ 收起 / ▸ 展开」，
  顶部新增「插件」下拉框（`全部插件` 或只看单个插件），选单个后可一键「显示全部插件」。
- 默认展示常用参数（`GROUP_POLICY_IMPORTANT`）：core_reply 8 项（对话模式、回复概率、
  群内必须 @、@ 才回复、模型置信度、群内闲聊、工具指令需 @、允许主动接话），
  stickers 4 项、food 3 项、memory_bank 3 项、memory_commands / image_search / web_reader /
  expression_state / safe_tools / weather 各 2–3 项，fortune / scheduler 各 1 项；
  其余参数仍可通过「显示全部参数」或参数名单查看。
- 未安装在该实例的插件会显示「该实例未安装此插件，或没有可按群覆盖的参数」，不再渲染空卡片。
- 样式：`.group-policy-filter-row` / `.group-policy-plugin-filter` / `.group-policy-collapse` /
  `.group-policy-card-body`，≤640px 时下拉框占满一行。
- 测试：策略 UI 静态契约 36。

## v5.0.0-beta.32（测试版）— 群策略接口默认返回精简快照

- `GET group-policies/<type>/<id>` 默认按 `include_schema=0` 返回，前端显式带上该参数：
  内嵌 500+ 字段的完整快照有 215KB，超过插件桥 64KiB 响应上限会直接
  `response_too_large`，群策略页因此打不开；精简快照只回 `schema_digests`，前端再逐插件惰性
  拉 `group-param-schema/<plugin>`。
- `?include_schema=1` 仍可显式要求内嵌 schema（本地调试/小插件集）。
- 需配套 `qqbot_safe_tools 3.4.1`（`v4.4.0-beta.65`）：把 `include_schema` 透传到
  `policy.scope`，此前该参数在控制桥里被丢弃。
- 测试：主项目 897、server 49、策略 UI 静态契约 35。

## v5.0.0-beta.31（测试版）— 插件参数一键加入群策略

- 插件参数编辑器每个字段新增「加入群策略」开关：登记 `plugin_id + 参数路径`，群策略页即可按群覆盖该参数，不再只能在群策略页里翻已有参数。
- `studio-group-policy-prefs.json` 新增 `registered`（`{plugin_id: {config_path: policy_param}}`），服务端按 `^[A-Za-z0-9_.:-]{1,160}$` 清洗、每插件最多 500 条、坏项丢弃，文件仍为 `0600`；旧文件按空表兼容。
- 编辑器按 `capability_policy.ParamSpec.config_key` 把编辑器配置键映射到运行时语义名（如 `participation.group_probability` → `participation.reply_probability`）；敏感字段与仅全局字段显示「仅全局」且禁用，未登记字段提示需补 `capability_policy`，避免"点了不生效"。
- 群策略页：登记过的参数即使被"参数名单"收窄也保持可见，并标注来源路径（`.group-policy-field-registered` / `.group-policy-prefs-path`）。
- 移动端（≤640px）字段头换行，开关独占一行居中，避免挤压标题。
- 文档：[分群插件启用与分群参数设计](docs/group-plugin-config.md) 新增 §6.2。
- **任意标量参数按群覆盖（config.overlay.v1）**：`qqbot_safe_tools` 新增 `spec_registry`，按 `_conf_schema.json` 自动生成 `ParamSpec`（手写 spec 优先）；`qqbot_core_reply` / `qqbot_food` / `qqbot_stickers` 各自 vendored `policy_overlay.py`，事件入口一次性取策略、`ConfigReader`/`_cfg`/`PluginConfig.raw` 先查 overlay 再查自身配置，`creator_task` 校验阻断子任务隐式继承（后台任务用 `detached()`）。插件版本：core_reply 4.3.13、food 3.0.6、stickers 3.0.4、safe_tools 3.4.0（发布 tag `v4.4.0-beta.64`）。
- `policy.param_schema` 新增 `overlay_supported`/`unsupported`/`digest`/`registry_digest`/`scalar_count` 与每字段 `source`/`system_managed`；`policy.scope` 支持 `?include_schema=0`（只回 `schema_digests`），前端按插件惰性拉 schema，群策略页不再一次性嵌 500+ 字段。
- 群策略页默认只显示「已登记 + 已星标」参数，新增「显示全部参数」开关；保存名单不再把未渲染插件的 `null`（全部）悄悄收窄。
- 服务端对 `registered` 做逐条校验（插件已登记、路径↔策略参数匹配、非敏感/系统字段、schema 项需 `overlay_supported`），不合法返回 400。
- 文档：[分群插件启用与分群参数设计](docs/group-plugin-config.md) 新增 §6.3。
- 测试：主项目 897、core_reply 264、food 203、stickers 173、safe_tools 242，均 OK。

## v5.0.0-beta.30（测试版）— 辅助 AI Key 权限修正

- `qqbot_aux_ai.json` 统一为 `0660`（Studio 用户与 AstrBot 容器均可读写，其他用户不可读）；`set_aux_ai` 写入后同步 chmod。
- 修复已存在 `0640` 文件时 Studio 保存 API Key 报 `Permission denied` 的问题。
- 保存后控制台只显示掩码，不回显完整 Key；配置不进入日志或仓库。
- 测试：主项目全量 880 tests OK；memory_bank 16、memory_commands 151。

## v5.0.0-beta.29（测试版）— 辅助 AI 专用 API Key

- Studio「辅助模型」新增“辅助 AI 专用 API”设置：启用开关、Base URL、模型、API Key、超时；Key 只写本机 `plugin_data/qqbot_aux_ai.json`（0640），界面只显示掩码，不回显完整值。
- 新增 `GET/POST /api/instances/<id>/aux-ai`；`qqbot_memory_bank` 与 `qqbot_memory_commands` 的辅助调用优先使用该 Key，未配置/失败才回退 AstrBot 主模型。
- 辅助调用覆盖：记忆路由、自动提取、阶段摘要、episode 摘要；不用于主聊天。
- 测试：主项目全量 880 tests OK；memory_bank 16、memory_commands 151。
- 发布：插件仓库 `v4.4.0-beta.63`；`qqbot_memory_bank 0.6.0`、`qqbot_memory_commands 4.3.3`。

## v5.0.0-beta.28（测试版）— 记忆建议收件箱

- Studio 的“AI 记忆库”面板新增待审核建议列表，可接受/忽略 AI 路由到人格记忆、群记忆、用户知识库的建议；`episode/open_topic` 只能忽略，卡片仍由卡片写入者管理。
- 新增 `GET /api/instances/<id>/memory-inbox`、`POST /api/instances/<id>/memory-inbox/resolve`。
- `qqbot_memory_bank 0.5.0` 的 `memory_inbox` 仍只保存建议，不自动写核心记忆/用户资料；`auto_write_core_memory` 默认关闭。
- 测试：主项目 880 tests OK；memory_bank 16、memory_commands 151。
- 发布：插件仓库 `v4.4.0-beta.62`。

## v5.0.0-beta.27（测试版）— AI 记忆路由与注入收口

- `qqbot_memory_bank 0.5.0`：新增 `memory_router.py`，由辅助模型判断 `user_fact / persona_memory / group_memory / episode / open_topic / knowledge_doc / ignore`，不再用固定关键词决定记忆去向。
- 新增 `memory_inbox` 待审核表：AI 不能静默改人格/群核心/用户资料；`auto_write_core_memory` 默认关闭，非事实类建议进入待审核。
- 事实写入 `knowledge_entry`；人格/群核心写入 `memory_layer`（需显式开启+置信度）；用户资料只能人工导入；卡片和文档不自动改写。
- `qqbot_memory_commands 4.3.2`：AI episode 摘要在没有实质内容时返回 `[[NONE]]`，不再生成垃圾 episode。
- 注入收口：bot-11 关闭 `cards_inject_enabled`，记忆卡片由 `qqbot_memory_bank` 统一按 scope/persona 去重召回，避免卡片与 memory_bank 重复注入。
- 文档：[四层记忆与自动召回](docs/memory-layers.md) 增加 AI 路由、`memory_inbox`、所有权与注入收口说明。
- 发布：插件仓库 `v4.4.0-beta.62`；`qqbot_memory_bank 0.5.0`、`qqbot_memory_commands 4.3.2`。

## v5.0.0-beta.26（测试版）— 记忆面板写入路由修复

- 修复 Studio 的 persona/group memory、知识库导入、AI 记忆导入/删除 POST 分支误放在 GET 处理器中，导致界面写入返回 404；现已移到 `do_POST` 并保持 GET 查询独立。
- 真实服务器验证：人格记忆/群记忆 GET 200；POST 新增、重复提示、查询、删除完整通过。
- 测试：server 47 tests OK（新增 persona/group memory POST 路由覆盖）；主项目 880 tests OK。

## v5.0.0-beta.25（测试版）— 四层记忆修复与测试环境收口

- `qqbot_memory_bank 0.4.1`：同内容重复添加不再清空已有标题/标签/优先级；只在显式提供非空标题/标签或正优先级时更新对应字段。
- Studio 人格/群记忆保存同一规则；新增 `test_memory_layers_persona_group_and_delete` 覆盖隔离、去重、优先级和删除。
- 修复测试对固定 6185 端口的依赖：测试使用系统分配的空闲端口，主项目全量 879 tests OK（不再受本机端口段占用影响）。
- 发布：插件仓库 `v4.4.0-beta.61`；主项目 `5.0.0-beta.25`。

## v5.0.0-beta.24（测试版）— 四层记忆与自动召回

- `qqbot_memory_bank 0.4.0` 新增 `memory_layer`：人格记忆（按 persona_id）与群记忆（按群 scope）常驻注入，支持标题、标签、优先级、同内容去重和删除。
- 新增自动召回 `@filter.on_llm_request()`：每轮按预算注入人格记忆、群记忆、共享知识库 top-k、当前用户 AI 记忆；可用 `auto_recall_enabled` 全局关闭，退回纯工具检索。
- Studio 新增“人格记忆”“群记忆”面板和 `/api/instances/<id>/persona-memory`、`/group-memory` GET/POST/DELETE；scope 使用统一下拉选择器。
- 明确四层边界：人格/群核心记忆常驻；个人 AI 记忆按 actor 隔离；用户导入知识库只读并自动 RAG；`memory_cards`、`persona_corpus`、AstrBot 原生记忆保留但不混表。
- 真实 bot-11 验证自动召回日志：`memory auto recall: layers=2 docs=1 facts=0 chars=103`；测试数据已清理，`group_mention_only` 已恢复。
- 文档：新增 [四层记忆与自动召回](docs/memory-layers.md)，重写文档索引/STATE，更新 V7 使用手册。
- 测试：memory_bank 11 tests OK；主项目 server/deployer 新增路由编译通过；全量端口占用失败仍为环境问题。

## v5.0.0-beta.23（测试版）— scope 选择器与知识库 UI 优化

- 记忆卡片、表达状态、用户导入知识库、AI 记忆库统一使用 scope 选择器：先选“群聊 OneBot / 群聊官方 / 私聊 / 全局 / 自定义”，再填 ID，不需要手写完整 scope。
- 知识库（用户导入）重排：scope 与 persona 查询栏、导入卡片、正文/标题/标签分区、结果卡片和删除按钮统一布局。
- 所有记忆/群策略输入框、下拉框、文本域统一 36px 高度、10px 圆角、focus ring 和移动端宽度。
- 记忆卡片维护按钮移回记忆卡片面板；表达状态面板不再混入卡片维护。
- Studio 46 tests OK；deployer 34 tests（3 个本机端口占用失败为环境问题）。

## v5.0.0-beta.22（测试版）— 用户导入知识库与 AI 记忆彻底分开

- 用户导入知识库改为独立表 `knowledge_doc`，只读、可导入/删除/检索，模型通过 `knowledge_search` 查询，不再和 AI 自动提取记忆混在同一结果里。
- AI 自动提取记忆仍走 `knowledge_entry`，按 `scope + persona_id + actor_hash` 隔离，只通过 `memory_add/search/forget` 使用。
- Studio 新增“知识库（用户导入）”面板；原“AI 记忆库”面板只展示 AI 记忆，不再导入资料。
- `qqbot_memory_bank 0.3.0`；插件包 `v4.4.0-beta.59`。

## v5.0.0-beta.21（测试版）— 群策略名单、知识库身份与 AI episode

- 群策略新增“参数名单”和“标记表”：按插件勾选组策略可见参数，常用参数可星标后在一个表里快速修改；名单保存在 Studio 数据目录，不依赖插件控制桥。
- 知识记忆库新增用户身份：`actor_hash/actor_label` 隔离 AI 记忆，用户导入的 `curated` 资料为共享只读；旧数据标记为 `legacy` 共享区。
- 知识记忆库新增 Studio 导入/删除 curated 条目；`memory_search` 返回当前用户 + 共享资料，`memory_forget` 不会删除 curated。
- 新增 `cards_episode_ai_enabled`：完整收尾可由辅助模型生成 120–200 字叙事 episode，失败回退规则或跳过；知识库记事实、episode 记叙事。
- 表达状态、好感度控制台入口补 scope/群号说明；个人资料查询按群号显示成员昵称与分数。
- `qqbot_memory_commands 4.3.1`、`qqbot_memory_bank 0.2.0`；插件包 `v4.4.0-beta.58`。

## v5.0.0-beta.20（测试版）— V7 记忆收口与使用手册

- 记忆卡片补齐 FTS5 trigram 索引、48 张归档目标、purpose 使用记录、sweep/export/delete/metrics。
- 收尾节奏改为阶段摘要每 6 轮、完整收尾每 8 轮，避免 sliding window 重复 episode。
- 新增 `cards_group_member_enabled` 第六 scope 开关。
- Studio 记忆卡片面板新增重建索引/清理归档/导出；新增 `/memory-cards/maintenance` API。
- 新增 `docs/v7-feature-guide.md`，覆盖 V1–V7 功能意义与用法。
- memory_commands 4.3.0；memory_bank 自动抽取与工具化知识库保持可选。
- 可选辅助收尾默认关闭，失败回退规则路径。

## v5.0.0-beta.19（测试版）— 人格会话绑定与记忆卡治理

- `qqbot_core_reply 4.3.1`：AstrBot memory 模式下新增 `persona.agent_bridge_enabled`，把当前 prompt profile 同步为 AstrBot 会话人格；人格切换时新建/重绑会话，避免旧人格和历史把“麻衣”回答成旧身份。
- 麻衣提示词加入群聊回应原则：默认不接、无点名/直接提问不回复、不必要时只输出 `[[NO_REPLY]]`。
- Studio 新增人格语料库入口/API：按 persona/profile 增删角色台词、示例对话、设定片段，支持标签和清空。
- `qqbot_memory_commands 4.2.1`：wrapup 生成记忆卡前清洗 `[[NO_REPLY]]`、mqqapi、markdown 链接和 @ 噪声；空内容不再落库。
- 已清理 bot-1/bot-11 历史脏卡片（备份后删除重复/媒体/噪声 episode），并补写入侧过滤防复发。

## v5.0.0-beta.18（测试版）— V6 情绪与表达状态

- 新增独立插件 `qqbot_expression_state 1.0.0`：情绪 valence/arousal（单事件 ±0.15、45 分钟半衰）与 8 维风格（30 条样本门槛、确定性统计、pin/opt-out/reset/export）。
- `qqbot_core_reply 4.3.0`：社交块后注入表达块（≤120 字，社交+表达 ≤260），表达块仅作风格参考，不覆盖安全/工具/人格规则。
- Studio 新增表达状态 API 与面板：按 scope 查看情绪/风格、opt-out、pin 维度、清空、导出。
- `qqbot_safe_tools 3.3.3`：强化好感度工具提醒；仍由模型决定 delta，后端只做限幅/冷却/幂等/审计。
- 插件包 `v4.4.0-beta.42`；bot-11 已加载 core 4.3.0 / safe 3.3.3 / expression 1.0.0。

## v5.0.0-beta.17（测试版）— 统一人格参与

- `qqbot_core_reply 4.2.0`：新增 `participation.mode=unified`。非 @ 群消息不再先调用独立判定器，而是把参与判断放进同一个 AstrBot 人格请求；模型不想接时只输出 `[[NO_REPLY]]`，`on_decorating_result` 丢弃该结果。
- @、私聊、命令仍直通；`probability`/`model` 模式保留作为低成本回退。
- 测试计划补充 unified 行为与“你怎么看”说明；单测 253 OK。

## v5.0.0-beta.16（测试版）— V5 社交与参与模型

- `qqbot_core_reply 4.1.0`：新增 `participation.mode=model`，群内非 @ 消息可由模型输出 `speak/observe` 决定是否接话；@/私聊/命令仍直通，超时/预算/非法 JSON 自动回退旧概率路径。
- 说话人标签：历史消息带稳定 `actor_hash` 与昵称短码，群内 roster 注入；prompt 不再包含 QQ/openid/群号。
- `qqbot_safe_tools 3.3.0`：新增 `social_state`，好感度支持模型工具 `favorability_adjust`（模型决定 delta，后端限幅/日上限/冷却/幂等/审计）与只读 `favorability_get`；默认关闭，开启后按定性 tier 注入 prompt。
- 防刷好感：prompt 强制禁止因夸奖/要求/重复示好加分，批评/拒绝不得扣分，单次克制，不确定不调用，不向用户提及分数。
- 新增可选 `persona_corpus`（D39）：按 `persona_id + prompt_profile_id` 隔离角色台词/示例，默认关闭，最多注入 2 条/240 字，明确标为非指令。
- 插件包 `v4.4.0-beta.37`；bot-11 已启用 model 参与、persona corpus 与好感度工具。

## v5.0.0-beta.15（测试版）— NapCat 二维码登录与 v4 收口

- Studio 新增 NapCat 登录状态/二维码 API：自动读取容器内二维码，不依赖 WebUI。
- 部署向导新增二维码面板、刷新登录、重置登录（重置前自动备份 QQ 登录态）。
- 修复 AI 助手测试固定端口受宿主环境影响的问题。

# 更新日志

## v5.0.0-beta.15（测试版）— 记忆卡片 v4

- 卡片库 schema v2：新增来源键、极性、标签、关系绑定、置顶、使用次数、过期时间、来源回合、合并去向等字段；旧 v3 数据库按 additive migration 自动升级。
- 卡片检索改为查询相关性 + 重要性 + 真实使用新近度 + 关系权重 + 开放话题/目的 + 清晰度 + 置顶的排序。
- 新增会话状态 `session_state`（阶段摘要、最多两个开放话题、purpose、最后收尾 revision）与审计表 `audit_log`。
- 收尾卡片写入会话状态和审计；卡片 touch 只更新 last_used/use_count，不再重置衰减时钟。
- Studio 记忆卡片面板显示 v4 版本、置顶/使用次数/标签/关系，并支持按 scope 查询。
- `qqbot_memory_commands` 版本 4.2.0；插件包 `v4.4.0-beta.36`。

## v5.0.0-beta.13（测试版）— 内置 NapCat 快速登录与 OneBot 配置修复

- 内置 NapCat 注入 `ACCOUNT`，已有登录态时自动快速登录，不再每次落到二维码。
- NapCat OneBot 配置同时写入全局 `onebot11.json` 与按 QQ 的配置文件，使用 `network.websocketClients` 标准结构。
- 修复全局配置 token 为空导致反向 WS 鉴权失败、AstrBot 收不到事件的问题。
- 生产 bot-11 已验证：快速登录成功、OneBot11 网络配置加载、`aiocqhttp` 适配器已连接。

## v5.0.0-beta.12（测试版）— 官方 openid 与 OneBot 数字 ID 适配

- 群人格配置同时接受官方 32 位 openid 与 OneBot 数字群号，拒绝其他格式。
- 群策略快照返回 `scope_kind`，Studio 显示“官方 openid / OneBot 数字 ID / 自定义 ID”。
- 群白名单、群策略、记忆 scope、Food 群位置的文案区分 openid 与数字群号。
- OneBot 向导明确第三方 QQ 号不是官方机器人 AppID；README 增加平台 ID 不混用说明。

## v5.0.0-beta.11（测试版）— 实例号按高水位复用

- 新建实例优先复用高水位之下最近的空闲编号；已有 bot-1..7、bot-11 时，下一个是 bot-10。

## v5.0.0-beta.10（测试版）— 向导首屏字段切换与实例号复用

- 修正向导页面加载时未绑定平台模式 change 事件，导致选择 OneBot 后仍显示 AppID/AppSecret。
- 删除实例后新建实例复用最小空闲编号，不再永久顺延。

## v5.0.0-beta.9（测试版）— NapCat 运行状态与容器修复

- 实例状态返回内置 NapCat 运行状态；NapCat 停止时提示修复。
- “修复容器”现在同时检查/启动内置 NapCat。

## v5.0.0-beta.8（测试版）— 向导 OneBot 网关字段接线

- 修正部署向导选择 OneBot 后内置/外部网关字段不切换、payload 缺失 `onebot_gateway` 的问题。
- 内置 NapCat 模式隐藏外部端口/Token 字段，外部网关模式才显示。

## v5.0.0-beta.7（测试版）— NapCat 一键登录链接

- NapCat WebUI 状态链接自动带上 token，部署完成页可直接打开扫码登录。

## v5.0.0-beta.6（测试版）— 内置 NapCat 网关与扫码登录

- 部署向导 OneBot 模式新增「内置 NapCat / 外部网关」二选一。
- 内置 NapCat 自动生成反向 WebSocket 配置、Token、端口和 `onebot11_<QQ>.json`，部署完成页可直接打开 WebUI 扫码登录。
- 外部 OneBot 继续使用 QQ 号 + 端口/Token。
- 修复 QQ 号在 OneBot 模式下被当作 legacy 字段清理的问题。

## v5.0.0-beta.5（测试版）— OneBot QQ 号语义修正

- 外部 OneBot v11 模式新增机器人 QQ 号字段（5–12 位数字），保存到实例配置并在状态页显示。
- 官方模式继续使用 AppID/AppSecret + openid，不再允许 `qq_account`，避免两种接入语义混用。
- OneBot 模式接受 `qq_account`，官方模式在 Studio/API 层拒绝该字段。
- 新增 store/deployer/server/wizard 回归测试。

## v5.0.0-beta.4（测试版）— 双接入框架

- 新增外部 OneBot v11 网关模式：单 AstrBot 容器、`aiocqhttp` 反向 WebSocket、自动分配端口与自动生成 Token。
- 向导/控制台支持在 QQ 官方机器人与外部 OneBot 网关之间选择；状态页显示 WebSocket 地址、Token 与连接状态。
- 两种模式共用现有插件与数据分层；官方 openid 与 OneBot 数字 ID 不混用，切换模式不自动迁移身份数据。
- 修复群策略布尔/数字参数保存与回显、控制桥插件错误响应被误报为协议版本错误的问题。
- 新增双模式配置/状态/前端静态测试；全量测试 862 项通过。

## v5.0.0-beta.3（测试版）— 群策略参数修复

- 修复群策略布尔参数没有按复选框渲染、保存时被忽略的问题：统一把后端的 `bool/int/float` 类型映射到前端的 `boolean/integer/number`。
- 群策略保存后会重新读取 `effective`，布尔、数字、文本参数都会回显；`revision` 冲突仍按 409/422 返回。
- 继续保留“未接入运行时的字段不应当被误认为已生效”的边界；`persona.profile_id` 等仍是展示/规划字段，修改不会影响当前运行。

本项目使用独立版本线：

- Linux 主项目：`qqbot_studio.__version__`
- QQ 增强插件：`plugins/qqbot_enhancer/metadata.yaml`
- Windows 发行版：Windows 仓库独立版本，内嵌 Linux core 版本单独记录
- AI 助手 B：独立仓库 `qqbot-studio-ai-assistant`

## v5.0.0-beta.2（测试版）— 记忆卡片 v3

- 记忆系统 v3：增加 active/archive 记忆卡、清晰度半衰期、容量上限、append-only 事件审计与 `memory_cards.db`。
- 新增确定性收尾：按轮次生成会话摘要、阶段摘要、未完成话题，可选生成 purpose 卡片；`wrapup_ledger` 保证幂等。
- `/记忆 卡片` 查看当前 scope 卡片，`/记忆 收尾` 立即生成收尾卡片；每 6 轮自动收尾，可通过插件参数关闭或调整。
- 回复前注入当前会话最多 4 张卡片，包在 `untrusted="true"` 块中；卡片文本不作为指令执行，不替代五 scope 原始记忆。
- Studio 记忆面板增加卡片统计与最近卡片列表；新增只读 `/api/instances/{id}/memory-cards`。
- 记忆命令插件版本 4.1.0；新增/更新测试覆盖卡片存储、衰减、容量、收尾幂等和注入去重。

## v5.0.0-beta.1（测试版）— QQ 官方机器人 5.0 基线

- 主项目正式切换到 QQ 官方机器人接入：AstrBot 单容器、AppID/AppSecret，不再需要 SnowLuma、OneBot 或 noVNC。
- 安装向导、控制台、实例状态、群策略、记忆策略、白名单全部改为官方 openid 语义。
- 新增插件市场默认 GitHub catalog；新实例无需本地市场种子即可发现插件。
- 插件市场支持运行时依赖声明与 pip/apt 安装确认；连续点击安装改为单操作串行保护。
- 新实例默认只安装最小三件套：`qqbot_safe_tools`、`qqbot_memory_commands`、`qqbot_core_reply`。
- 新增 `qqbot_scheduler`、`qqbot_fortune`、`qqbot_weather` 三个本地工具插件；天气图按中国天气网样式生成并只发图片。
- `/option` 改为用户功能清单与逐条说明；工具命令在群聊必须真实 @ 才触发。
- 组内插件过滤、移除后不刷新、插件市场首屏分组标签修复。
- 下一阶段开始记忆系统 v3。

## v4.4.0-beta.23（测试版）— 插件分组过滤与本地移除

- 已加入任意分组的插件不再出现在其他分组的“可添加插件”下拉列表。
- 从分组移除插件改为本地更新，不重新加载整个插件市场，保留当前滚动位置和展开状态。
- 添加插件时同步从所有分组下拉列表移除，避免同一插件重复出现在多个分组候选中。
- `node --check` 与 70 项静态测试通过。

## v4.4.0-beta.27（测试版）— 插件操作串行保护

- 前端一次只允许一个插件安装/更新/删除操作，避免连续点击造成请求堆积。
- 后端 `PluginMarket` 增加操作门闩：已有操作时 API 立即返回 `409 operation_in_progress`，不再让多个请求排队占满 Studio 线程。
- 插件预览/安装仍会做目录权限修复，避免 root-owned 插件目录导致 500。

## v4.4.0-beta.26（测试版）— 新实例默认最小插件

- 新实例部署完成后自动安装最小可运行三件套：`qqbot_safe_tools`、`qqbot_memory_commands`、`qqbot_core_reply`。
- 按拓扑顺序安装并在每一步重启 AstrBot，避免依赖未加载导致安装被拒绝。
- 修复 `safe_tools` 可选依赖造成的依赖环，新实例可正常安装基础插件。
- 其他 food/stickers/web/image/fortune/weather/scheduler 不预装。

## v4.4.0-beta.25（测试版）— 插件安装权限修复

- 安装/更新插件前自动通过 AstrBot 容器把 `data/astrbot/plugins` 所有者修复为 Studio 运行用户。
- `PluginMarket` 遇到 `PermissionError` 时返回真实可读错误，不再只显示“查看终端日志”。
- 修复新建实例首次安装插件时 `root:root` 插件目录导致的 500。

## v4.4.0-beta.24（测试版）— 默认 GitHub 插件市场源

- 新建实例未配置市场时，默认从 GitHub 官方 catalog 拉取插件目录。
- 默认信任 `raw.githubusercontent.com`、`github.com`、`objects.githubusercontent.com`。
- 插件仓库增加 `market/catalog.json`，下载地址指向 GitHub raw 制品。
- Release workflow 动态校验插件数量，并上传 catalog、manifest、group registry。

## v4.4.0-beta.21（测试版）— 插件市场首屏分组修复

- 插件市场首屏改为始终显示「插件分组」标签页，翻页后也保持同一视图状态。
- 修复首屏 `marketOnly` 隐藏分组、翻到第二页才出现分组标签的问题。
- 前端 `node --check` 与静态测试通过。

## v4.4.0-beta.20（测试版）— 前端 official-only 收口与 openid 文案

- 安装向导、控制台、实例状态全部改为 QQ 官方机器人 AppID/AppSecret 单容器，不再出现 SnowLuma/noVNC/OneBot/扫码登录入口。
- 群策略、记忆策略、群白名单的标识文案从 QQ 群号改为群 openid；scope 示例改为 `qq_official:<openid>`。
- 纯 @ 回复与工具命令 mention 守卫修复：无真实 At 时不再触发 `/option`、`/定时`、`/每日运势`、`/天气`。
- 前端静态检查、`node --check` 与主项目全量测试通过。

## v4.4.0-beta.19（测试版）— 官方-only 清理、插件依赖与新工具插件

- 删除安装向导、控制台和项目内的 SnowLuma/noVNC 入口，安装只支持 QQ 官方 AppID/AppSecret 单容器。
- 插件市场支持运行时依赖声明；安装前检查 Python/system 依赖，用户确认后执行 pip/apt，无法满足则取消安装。
- 新增 `qqbot_scheduler`（/定时）、`qqbot_fortune`（/每日运势）、`qqbot_weather`（/天气）三个不调用聊天模型的插件。
- `/option` 改为用户功能列表与逐条详情，不暴露实例设置/插件参数/管理员入口。
- 更新脚本、官方 openid、群策略默认值继承和纯 @ 回复的修复纳入本版。

## v4.4.0-beta.16（测试版）— QQ 官方接入安装与控制台细节

- 官方模式状态识别补齐；安装向导、README 与旧 SnowLuma 文案区分。
- 官方模式只运行 AstrBot，群策略/插件 identity 使用官方 openid。
- 保留旧实例 SnowLuma 兼容路径。

## v4.4.0-beta.15（测试版）— QQ 官方机器人接入迁移

- 新增官方机器人接入方式：AstrBot 直接使用 `qq_official`，不再依赖 SnowLuma/OneBot/noVNC。
- 安装向导支持官方 AppID/AppSecret；官方模式只生成/运行 AstrBot 单容器。
- 插件身份、群策略、记忆、人格和群管理员判断兼容官方 `openid`。
- 保留 SnowLuma 旧实例兼容路径，但新安装默认走官方接入。
- 本版为官方接入迁移测试版，需在 bot 测试群验证 @、全量群消息、群策略与工具调用。

## v4.4.0-beta.14（测试版）— 群策略默认值继承旧参数

- 群策略的“插件参数”默认值现在识别核心回复的旧 `reply_probability` 键：当规范键仍是默认值、旧键被显式修改时，展示旧键值。
- safe_tools 3.2.5 的 `param_schema` 增加 `legacy_key`，不改变群覆盖/继承语义。
- 修复设置页把旧键设置 0 仍显示 0.12 的问题。

## v4.4.0-beta.13（测试版）— 更新自检兼容无 Node 环境

- `scripts/post-update-test.sh` 在服务器未安装 `node` 时跳过前端语法检查，不再阻断自动更新。
- 引导路径执行 `scripts/update.sh` 时输出写入 `release-update.log`（0600），带上 from/to/channel。
- 保持更新失败时旧管理进程继续运行。

## v4.4.0-beta.12（测试版）— 更新脚本引导兼容

- `scripts/auto-update.sh` 增加一次性引导路径：旧版更新器切到本版本后也会执行 `scripts/update.sh`。
- 新版更新器同步执行脚本并设置 `QQBOT_UPDATE_SCRIPT_ALREADY_RAN=1`，避免自动更新重启路径重复执行。
- 增加更新脚本契约测试：禁止网络下载、必须可执行、必须有超时。

## v4.4.0-beta.11（测试版）— 版本自带更新脚本

- 更新器在切换到新版本后、重启管理服务前执行 `scripts/update.sh`。
- 正式版和测试版共用同一机制；脚本只来自已签出的 Git 版本，固定路径，不下载远程内容。
- 脚本超时 600 秒、大小上限 256 KiB；非零退出会恢复原版本并写入更新历史。
- 更新日志写入数据目录 `release-update.log`（0600），输出经脱敏。
- 更新响应和历史记录增加 `update_script` 执行结果。

## v4.4.0-beta.10（测试版）— 群策略默认值改为插件参数

- 群策略默认值改为读取插件“插件参数”的实时值，不再只显示策略层硬编码默认。
- Studio 群策略 GET 响应增加 `plugin_configs`，仅暴露策略 schema 引用的配置键。
- 前端参数提示区分“群覆盖 / 插件参数 / 默认”；未覆盖时显示插件参数实际值。
- safe_tools 3.2.4：参数 schema 增加 `config_key`，可把策略参数映射回插件配置键。

## v4.4.0-beta.9（测试版）— 群策略默认值展示

- 群策略参数 API 增加 `defaults`，UI 在未设置时显示插件默认值，而不是“默认：未设置”。
- 布尔参数默认值也正确回显；数值/枚举参数使用默认值作为占位和提示。
- safe_tools 3.2.3：策略 schema 为 10 个插件参数补充默认值，前端可正确继承。

## v4.4.0-beta.8（测试版）— 控制桥错误可读性

- Docker exec 传输在插件拒绝请求（401/403/404/422）时保留插件返回的 JSON 错误，不再统一误报为“docker 传输失败”。
- 修复生产 `control_bridge_scope_allowlist` 被写成 `globaladmin` 后导致的 `scope_not_allowed`；控制桥支持 `global,admin` 逗号列表。
- 新增 `DockerExecTransport` 非零退出返回 JSON 的回归测试。

## v4.4.0-beta.7（测试版）— 插件分组独立加载

- 设置页的插件/记忆/策略/统计改为独立 `Promise.allSettled`，任一接口失败不再连带清空插件分组。
- 插件分组缺失或失败时保留错误状态，显示“重新加载分组”并自动重试一次。
- 主项目测试 848 tests OK；插件侧保持 core 4.0.6 / safe_tools 3.2.2。

## v4.4.0-beta.6（测试版）— 群策略体验与权限修复

- 修复首次打开设置时插件分组偶发不显示：分组加载失败会保留错误状态、显示重试按钮，并在缺失时自动重试一次。
- 群策略页增加插件名称/ID/说明、参数中文标签与悬停说明；只展示已安装插件和经过安全筛选的可覆盖参数。
- 美化群策略按钮、输入框、模板 JSON 编辑器和 focus/hover 状态。
- 新增 `qqbot_core_reply.help_command_enabled`（默认 true），可在实例或群策略中关闭 `/help` 响应；裸 `help ...` 仍按普通聊天处理。
- 修复 `/分群` 权限误判：支持 sender role 为对象/字典、AstrBot 实例管理员 `admins_id`，并新增 `group_admin_allow_bot_admin`（默认 true）。
- 新增本地文档 `docs/default-off-parameters.md`，说明默认关闭参数与群策略安全白名单边界。
- 插件版本：core_reply 4.0.6、safe_tools 3.2.2；主项目测试 848 tests OK。

## v4.4.0-beta.5（测试版）— 修复 V2 群策略 RPC 白名单

- 将 `policy.scope`、`policy.audit`、`policy.templates`、`policy.param_schema`、`policy.scope_reset`、`policy.template_set/delete/apply` 加入 Studio 固定 RPC 方法白名单，修复群策略页 400 `control_method_not_allowed`。
- 主项目测试 848 tests OK；插件侧版本仍为 core 4.0.5 / safe_tools 3.2.1。

## v4.4.0-beta.4（测试版）— V2 分群策略控制面

- Studio 新增“群策略”分类：群/私聊 scope 查询、插件启停、字段级参数覆盖、模板应用、整群重置、审计记录。
- 新增 `/api/instances/{id}/group-policies/*`、`/group-param-schema/*`、`/group-policy-templates/*`，支持 GET/POST/PUT 与 revision CAS。
- safe_tools 新增 `policy.scope/scope_reset/audit/templates/template_set/template_delete/template_apply/param_schema` 控制方法。
- core_reply 新增 `/分群 状态|开启|关闭|重置|模板|概率` 群管理员命令；默认关闭 `group_admin_override_enabled`，开启后仅允许本群非敏感参数。
- 插件版本：core_reply 4.0.5、safe_tools 3.2.1；主项目测试 `848 tests OK`。

## v4.4.0-beta.3（测试版）— 对齐插件控制桥 RPC 协议

- Studio 控制桥请求改为 safe_tools 实际使用的 `digest`/`hmac`/单 scope 协议，响应解析支持插件的 `{ok, request_id, result}`。
- 保留本地模拟控制服务器的签名响应解析，测试覆盖两种响应。
- 生产实连验证：`/aux-budget`、`/aux-budget/usage`、`/plugin-policy` 均返回 200；预算写入与读取正常。

## v4.4.0-beta.2（测试版）— 修复控制桥 RPC 路径

- 修复 Studio 控制桥默认端点从 `/control` 改为插件实际监听的 `/rpc`，解决 `/aux-budget`、`/plugin-policy` 全部 503 的问题。
- 保留 `/control`、`/v1/control` 为兼容允许路径；新增默认端点契约测试。

## v4.4.0-beta.1（测试版）— V1 分群能力策略与辅助预算底座

- 新增 `capability_policy.json` v2 侧车：实例默认 < 群模板 < 群/私聊覆盖，字段级合并、revision CAS、原子写和审计。
- 新增 `aux_budget.json` + `aux_ledger.db`：五档预算（off/light/standard/advanced/full）、13 项辅助功能、reserve/commit/release、80% 省电、100% 停止、跨月 cycle 和幂等 request_id。
- safe_tools 新增 `policy.*`、`aux_budget.*` 控制方法和 Studio 辅助模型/预算 API；设置页可切换档位并单独开关 13 项功能。
- core_reply/food/stickers/memory_commands 接入分群策略门控；Food 的 `default_city` 只在群位置推断失败后兜底。
- 测试通道改为自动发现最新 `alpha/beta/rc` 预发布标签，不再写死分支白名单。
- 插件版本：core 4.0.4、safe_tools 3.2.0、food 3.0.4、stickers 3.0.3、memory_commands 4.0.2。
- 插件仓库发布 `v4.4.0-beta.1`；主项目测试 tag 同名。

## v4.3.5（正式版）— 更新自检兼容无 Node 环境

- `scripts/post-update-test.sh` 在服务器未安装 `node` 时跳过前端语法检查，不再阻断自动更新。
- 引导路径执行 `scripts/update.sh` 时输出写入 `release-update.log`（0600），带上 from/to/channel。
- 保持更新失败时旧管理进程继续运行。

## v4.3.4（正式版）— 更新脚本引导兼容

- `scripts/auto-update.sh` 增加一次性引导路径：旧版更新器切到本版本后也会执行 `scripts/update.sh`。
- 新版更新器同步执行脚本并设置 `QQBOT_UPDATE_SCRIPT_ALREADY_RAN=1`，避免自动更新重启路径重复执行。
- 增加更新脚本契约测试：禁止网络下载、必须可执行、必须有超时。

## v4.3.3（正式版）— 版本自带更新脚本

- 稳定版和测试版更新在切换代码后、重启服务前自动执行新版本内的 `scripts/update.sh`。
- 脚本固定路径、只从已签出的 Git 版本读取，不下载远程内容；超时 600 秒、大小上限 256 KiB。
- 脚本失败会恢复原版本并写入更新历史；输出写入 `release-update.log`（0600，已脱敏）。
- 更新响应和历史记录增加 `update_script` 执行结果。
- README 更新通道说明改为测试版自动发现 alpha/beta/rc 预发布标签。

## v4.3.2（正式版）— 测试更新通道修复

- 测试通道不再写死 `feat/plugin-repository-4.3`，改为自动发现最新的 `vX.Y.Z-alpha.N` / `beta.N` / `rc.N` 预发布 tag。
- `check_update(channel="test")` 可按预发布 tag 比较当前版本；`apply_update("test")` 不指定 ref 时自动选最新预发布版本。
- 更新面板文案同步为“自动获取最新 alpha/beta/rc 预发布版本”。
- 全仓 `845 tests OK`（main 分支）。

## v4.3.1（正式版）— 插件市场、七插件运行时与通用工具调用

- 主项目合并插件市场、插件分组、安装/更新/删除、SHA-256 与 ZIP 安全校验、原子替换和操作预览/确认。
- 七个自包含插件：`qqbot_core_reply`、`qqbot_safe_tools`、`qqbot_food`、`qqbot_stickers`、`qqbot_memory_commands`、`qqbot_web_reader`、`qqbot_image_search`；不再依赖 `qqbot_enhancer` 运行时。
- `qqbot_core_reply` 4.0.3：memory 模式注入 safe_tools 目录；修复工具回复后 turn 未释放导致的会话锁死；`help me ...` 不再被误识别为 `/help`。
- `qqbot_safe_tools` 3.1.1：manifest `tools.json` 自动聚合、marker 与 `qqbot_safe_tool` native bridge；provider registry 路由修复。
- `qqbot_food` 3.0.3：`长春` 可匹配 `长春市`，优先城市级地点；保留歧义与单字保护。
- `qqbot_stickers` 3.0.2：删除死配置 `sticker_probability` 与旧迁移开关，装饰概率只读 `sticker_decorate_probability`。
- `qqbot_memory_commands` 4.0.1：移除旧 `memory_commands_*` 配置 fallback，只读 canonical 键。
- 插件仓库正式发布 `v4.3.1`；主项目版本提升为 `4.3.1`。
- 合并后全仓 `841 tests OK`。

## v4.3.0（正式版）— 长期记忆与记忆管理

- 正式合并长期记忆、按群策略、记忆内容管理、记忆聊天命令、reflection、LLM 抽取、语义检索、群内说话人 scope。
- 新增/更新 Studio 记忆内容面板与插件参数保存上限（264 字段 schema 可保存）。
- 修复 memory logger 的 `plugin_tag` / `short_levelname` 日志字段，避免日志触发导致 agent 中断。
- 更新/回滚确认框移到顶部；更新后自动测试与确认清单继续生效。

## v4.2.3（正式版）— 参数与更新体验修复

- 插件字段保存上限从 256 提升到 2048，适配当前 264 字段 schema。
- 更新/回滚确认框移动到弹窗上方并保持可见。

## v4.3.0-beta.4 — 七插件最终模块化测试版

- 插件仓库发布 beta 系列：七个自包含插件 ZIP、catalog/manifest v2、ABI v1 依赖与冲突声明；safe_tools 3.0.0、stickers 3.0.0、memory_commands 4.0.0 完成。
- 主项目新增 S0 盘点、owner epoch/迁移事务/固定 RPC 控制桥；部署器不再复制 `qqbot_enhancer`，新实例提示从插件市场安装七件套。
- 新增 `scripts/migrate_qqbot_plugins.py` 与 `plugin_final_migration.py`：dry-run/apply/retire，配置字节级备份，只移动旧代码目录，保留 config/data/会话/记忆。
- 新增最终运行时门禁 `tests/test_no_legacy_runtime.py`：七插件集合、无旧包导入、部署脚本不复制旧插件、部署不产生旧插件目录。

- 记忆策略增加 `data/astrbot/memory_policy.json` 持久化 sidecar；AstrBot 重启重写 `cmd_config.json` 后策略不再丢失，面板读取 sidecar 优先。

### 记忆内容管理（Studio，t33）

- 实例设置新增「记忆内容」面板：scope 选择（group/user/conversation/persona/global）、关键词搜索、
  分页、单条详情/编辑、单条删除、按 scope 忘记、导出 JSON、导入预览（冲突/非法/上限汇总）、
  整理 dry-run/apply；不可用时明确显示原因并禁用写操作，不显示伪造的已保存值。
- 新后端模块 `qqbot_studio/memory_admin.py` + 路由：
  `GET memory/stats|records[/<id>]`、`POST memory/records`、`PATCH/DELETE memory/records/<id>`、
  `POST memory/forget|export|import|reflect`。每个操作显式 scope，无跨 scope 列表；删除/忘记/导入/整理
  必须带 `confirm`（单条=记录 id，scope 级=scope_id）。
- 安全：密钥/密码样式写入即拒绝、读取与导出再脱敏；响应不暴露数据库路径；读用 SQLite 只读 URI，
  写为单事务（失败回滚）+ `busy_timeout` 8s；导入/整理 apply 前写 0700 目录 + 0600 原子备份。
- Docker 权限降级：宿主无权限时改用 `docker exec` 在容器内执行同一批参数化语句（固定 argv、无 shell）。
- 契约：[`docs/memory-admin-api.md`](docs/memory-admin-api.md)；测试 `tests/test_memory_admin.py` 40 个用例；
  全仓 760 tests OK。

### 长期记忆聊天命令（qqbot_enhancer 2.9.0，默认关闭）

- 新增 `/记忆 状态|搜索 <关键词>|删除 <id>|忘记 本群|我的|整理`：默认
  `memory_commands_enabled=false`；仅 AstrBot 管理员、QQ 群主/管理员或
  `memory_commands_allow_user_ids` 允许名单可用；群聊只管理本群 `group` 作用域，
  私聊只管理自己的 `user` 作用域，从不 `export_all()`。
- `删除/忘记` 另需 `memory_commands_destructive_enabled=true`；`忘记` 必须显式
  `本群|我的`；未装记忆补丁、服务关闭、id 不在当前作用域、服务未确认删除/忘记时
  明确失败，不伪造成功；输出先脱敏密钥再截断，条数与总长度均可配置。
- `整理` 为纯本地 dry-run 预览（完全重复/近似重复/低价值/过期），不调用 LLM、不写库。
- 测试：`tests/test_memory_commands.py` 36 + 插件 handler 8；全仓 720 tests OK（记忆内容管理合入后 760 tests OK）。

- 测试分支：继承正式版 `4.2.0`，新增 AstrBot 原生长期记忆、按群 `conversation/long_term` 策略、Studio 记忆策略面板、参数分组面板和 stable/test 更新回退功能。
- 测试版不等同于正式版；长期记忆/策略需真实 AstrBot/Docker E2E 后才可合并。
- 主仓库测试：671 tests OK；AstrBot memory patch：125 tests OK（评审快照）。
- 美食智能（qqbot_enhancer t28）：按群默认地点 `food_group_locations`（JSON）+ 群名安全推断城市（仅显式
  `省/市/州` 后缀），「长春火锅」在已知长春群自动补城市、区县合并为完整路径，未知群仍追问；
  已有位置完整路径直接复用 `location_id`，`位置已存在`/`LOCATION_EXISTS` 只重解析并重试一次，
  不重复创建、无半截写入。

## v4.2.0 — 更新通道（稳定/测试）与同大版本任意回滚

- 控制台新增「更新与回滚」面板：当前版本/提交/大版本/工作区状态，稳定版与测试版分页。
- 稳定版只快进 `origin/main`；测试版只接受登记的测试分支/预发布标签（默认 `feat/plugin-repository-4.3`），拒绝任意分支、提交号或 URL。
- 回滚支持当前大版本内**任意**正式小版本（如 v4.2.0 → v4.0.4），列表展示标签、日期、提交与说明；跨大版本回滚拒绝。
- 切换前自动备份代码（git bundle）与实例配置/密钥文件；失败自动恢复原 ref；同一时间仅允许一个任务（锁文件），脏工作区拒绝。
- 实例数据/数据库从不被覆盖；目标版本 schema 低于本地数据时默认拒绝，需显式“允许 schema 降级”。
- 新增 `update-history.json`（from/to 版本、提交、通道、时间、结果，错误脱敏）及 `/api/update/status|versions|history|rollback`。
- 新增真实 Git 仓库测试：同大版本任意选择、跨大版本拒绝、非法 ref/URL 拒绝、脏工作区拒绝、备份与失败恢复、数据不变、历史与通道。

### 记忆策略面板（Studio）

- 修复「cmd_config.json 权限被拒 → 面板显示不支持」：本机 `EACCES/EPERM` 时自动改用 AstrBot 容器内读写
  （`docker exec`，argv 直传不经 shell；容器内写 `*.studio.tmp` → fsync → `chmod 0600` → `os.replace`
  原子替换；容器名校验，载荷上限 96 KB）。两条通道都不可用（容器未启动/容器内拒绝）时返回 503 +
  `actions`（启动实例 / chown+chmod 修权限）、`supported` 仍为 true、`saved:false`，面板显示"未保存"，
  绝不伪造已保存状态。GET 新增 `read_channel/write_channel/readable/writable/container_running/
  permission_denied/actions`（`writable` 用 `os.access(W_OK)` 实测）。错误信息统一脱敏，日志/响应不含密钥。


- 控制台实例设置新增「记忆策略」面板：默认模式（conversation/long_term）、私聊模式、
  按群增删改行（群号、模式、scope=group/user/group_user、抽取/注入开关、最大条数/字符）。
- 新增 `GET/POST /api/instances/<id>/memory-policy`（兼容 `/api/memory-policy?instance=`）：
  读写 AstrBot 的 `memory.policy`，只替换该键，其它设置与 `memory.*` 其它键原样保留，原子写 0600。
- 面板明确提示：长期记忆只对列出的群生效，未列出的群与私聊保持 conversation（只用会话历史）；
  `memory.enable=false` 时提示策略不会生效；上游没有 `memory.policy` 在线接口，保存后需重启 AstrBot
  （或重新加载配置）才生效，响应 `restart_required=true`。
- 实例没有 AstrBot 配置时显示"不支持"（POST 409），不伪造已保存值；旧后端返回 501。
- 后端校验：模式/scope 枚举、布尔开关、`max_items 1..50`/`max_chars 80..8000` 整数、群号
  4-20 位数字或 `platform:群号`、重复行/未知字段/超过 200 行一律拒绝且不写文件；令牌校验不变。
- 只读 JSON 配置，从不读取记忆数据库内容；日志/响应不含其它配置值或密钥。
- 插件参数编辑器（T17 P2）：保存成功后刷新比较基线使「已修改」标记正确清除；校验失败时清除
  搜索/分组/只看已修改过滤并滚动聚焦到出错字段；保存载荷仍包含全部 256 个字段。
- 非法存量策略如实上报（t24/t26 复查修正）：private `scope=group`、`max_items=999` 越界、未知字段、
  非法 mode/scope/类型、strip 后重复群号都会逐条进 `warnings[]`（字段+原因+降级后果），每条策略新增
  `invalid`/`errors[]`/`unknown_fields[]`/`effective_mode`/`effective_scope` 和顶层 `invalid_rows`；
  不再把 private 的 `scope=group` 静默改成 `user`，也不会把非法条目显示成生效中的 long_term
  （面板标红 + 「实际生效：conversation」，重复群号显示「未知」）。`private` 未配置时如实显示跟随默认模式。
- 测试：`tests/test_memory_policy_studio.py`（33 项）、`tests/test_memory_policy_ui_static.py`（19 项，
  含 node 契约与 256 字段回归）；文档见 `docs/memory-policy.md`。未做真实 AstrBot 进程 E2E。

## v4.1.1 — 网页智能阅读 + 饭店搜索/地址创建 + AstrBot 断连修复

- 插件 2.7.0：URL+提问消息自动抓取网页标题和正文，作为不可信参考资料注入 direct/AstrBot memory；裸链接不抓取。
- 插件 2.6.0：提交评价时自动拆分完整地址并创建缺失位置（如中国牛逼市 → 中国 / 牛逼市）。
- 插件 2.5.0：新增自然语言 `search_food_restaurants`，支持名称、标签、位置筛选和按均分/口味/性价比/评价数/最新日期排序。
- 插件 2.4.0 修复：提交草稿按会话+发送者隔离；无效提交 marker 不再出现虚假“已提交”；网页抓取内容不会二次触发工具写入。
- Linux core：AstrBot 登录/插件列表遇 Broken pipe 有限重试、强制重新登录和可操作错误提示。
- food.dlsz.top 站点补丁（日新下拉、后台删除饭店）已交付但未部署；源目录只读，部署步骤见 docs/food-ai-contract/DEPLOYMENT_NOTICE.md。
- 全量 568 tests OK。

## 控制台插件参数分组导航（Studio UI）

- 插件参数弹窗新增左侧（移动端横向 chips）粘性分组导航：常用回复 / 识图·搜索 / 表情包 /
  本地点歌 / 会话·人格 / 记忆 / Food / 网页访问 / 安全工具 / 高级其他，带字段数量与
  点击跳转；分组可折叠，高级组默认折叠。
- 分组低风险落地：Studio 内置前缀映射 + 可选 `ui_groups`/字段 `group` 元数据覆盖；
  未匹配字段进入「高级/其他」，256+ 字段全部保留，保存/校验/敏感字段过滤逻辑不变。
- 搜索支持 key/label/description/hint/分组名，字段显示「分组 › 字段」面包屑；
  无匹配/无已修改状态、自动展开命中分组；新增「只看已修改」过滤与「已修改」标记。
- 弹窗打开期间（并在同一页面内跨打开）记住选中分组、折叠状态与滚动位置；
  分组按钮支持 `aria-current`、箭头键导航、`aria-expanded`，Escape/关闭行为不变。

## v4.1.0 — 网页访问 + food.dlsz.top AI 提交

- 新增安全 AI 网站访问：`[[tool:fetch_webpage url="..." max_chars="N"]]`，固定 HTTP(S) GET、公网 IP/SSRF 防护、逐跳重定向校验、超时/大小/内容类型限制。
- 新增自然语言美食提交：`submit_food_review`，固定 `https://food.dlsz.top/api/upload`；店名/位置/味道分/性价比分/评价必填，缺项追问，不提交不完整数据。
- 密钥默认发送者 QQ，只注入 POST body；日期不提取，站点自动当天；重复饭店/位置/用户名错误映射为中文。
- 群聊只在食物相关消息/图片时参与；普通聊天不写生产数据；异食癖吐槽遵循安全边界。
- food.dlsz.top 站点位置搜索“日新”下拉点击修复 + 管理员删除饭店 API/UI 已交付为补丁；源目录只读，未部署。
- fetch 网页工具注入防护、非法 food marker 假成功修复；全量 504 tests OK。

## qqbot_enhancer 2.7.0（插件）

- 新增智能网页阅读：消息同时含公开 http(s) 链接与提问/阅读语境时（如“根据这个文章，怎么安装 REFramework
  <链接>”），自动用既有 `web_fetch.fetch_page_text` 读取该页，把标题+网址+正文摘录（默认 4000 字）作为
  **外部不可信参考**注入 direct system prompt 与 astrbot memory `on_llm_request`；不再只复述链接。
- 触发边界：`web_auto_fetch_enabled`、`web_auto_fetch_question_only`（默认仅提问语境）、
  `web_auto_fetch_max_urls=1`（最多 3）、`web_auto_fetch_max_chars=4000`、`web_auto_fetch_timeout_seconds=12；
  裸链接永不自动读取；群聊/私聊同一判断。
- 安全/成本：复用 SSRF/私网/元数据地址拦截、DNS 固定、逐跳重定向、大小/类型/超时限制；无 Cookie、无 JS、
  无浏览器、无文件/命令；页面正文已经 HTML 提取且 `[[` 中和为 `[ [`；同一 URL 成功缓存 5 分钟、失败 60 秒，
  同一事件只抓一次；自动阅读不依赖也不强制 `fetch_webpage` 工具。
- 失败时注入固定中文原因并要求如实告知（不编造）；`/option` 显示智能阅读状态；插件版本 2.7.0。
- 测试：新增 TestWebAutoFetch 16 例（direct/群聊触发、裸链接不触发、question_only 关闭、两链接只取一个、
  错误/超时、SSRF 不触达本地服务、本地文章端到端 HTML 提取、截断、去重缓存、`[[tool:` 中和、
  不强制工具 catalog、memory 幂等注入、/option）；插件 313 / 全仓 568 tests OK。
- 真实 CSDN 链接仅做 stdlib GET 探针；AstrBot / DeepSeek / QQ E2E 未验证。

## qqbot_enhancer 2.6.0（插件）

- `submit_food_review` 支持完整地址自动拆分与缺失位置创建：`中国牛逼市` → 已有“中国”下新建“牛逼市”；
  `中国/吉林省/长春市/南关区` 完整存在 → 直接用 location_id；未知前缀 → 整段作为 new_location_name。
  纯函数解析支持 `/ \ > → - —` 分隔符与 省/市/区/县/镇/乡/街道/路/街/州 后缀、`中国` 特例、长度上限、
  URL/控制字符拒绝与原文回退；仍只用现有 `GET /api/locations/search` + `POST /api/upload`，不新增站点端点。
- `位置已存在` 竞态：重新解析一次，拿到 id 才用 location_id 重试一次；仍不存在则回“位置已存在”，不重复建点。
  歧义/不安全输入中文追问、不部分写入；只有明确添加意图且字段齐全才解析/建点；密码/日期规则不变。
- 工具 catalog 提示 AI 可直接传完整地址，用户无需手动拆分；新增 `food_submit_location_max_parts` /
  `_part_max_chars` 配置（DEFAULTS/schema 251/251）。
- 测试：TestFoodLocationPath 12 例（纯解析/分隔符/中国特例/完整路径/缺失链/未知前缀/歧义/URL/
  字段缺失不建点/竞态重试/只重试一次/catalog）；插件 297 / 全仓 552 tests OK。

## qqbot_enhancer 2.5.0（插件）

- 新增自然语言餐厅搜索 `search_food_restaurants`（无需 slash）：固定 `https://food.dlsz.top` 的三个只读 GET
  （`/api/locations/search`、`/api/tags/search`、`/api/restaurants`），支持店名/多标签/地点组合、
  标签与地点精确匹配（歧义或不存在只追问，不发错结果），本地按均分/口味/性价比/评价数/店名/最新日期排序，
  `limit` 1–5 硬上限，输出店名/地点/标签/三项均分/评价数/最新日期 + 站点链接。
- 安全：复用 `/food` 的 HTTPS/host/path 校验、重定向校验、超时与响应大小；工具无 URL 参数、不 POST、不额外调用 LLM；
  一次回复最多一个工具；direct 与 astrbot memory 同一套安全 catalog；`/option` 显示搜索状态。
- 新增 15 个配置键（schema 与 DEFAULTS 234→249 同步）；插件版本 2.5.0。
- 测试：新增 TestFoodSearch 17 例（店名/标签/地点/组合、全部排序、limit、歧义/未知标签地点、无结果、
  错误/超时、固定 host/SSRF、direct+memory marker、一次调用、无 POST）；插件 285 / 全仓 527 tests OK。

## qqbot_enhancer 2.4.0（插件）

- 新增 AI 自然语言美食提交：群里自然说“帮我加到 food/记录评价”，AI 用 `submit_food_review`
  marker 提取店名/位置/味道分/性价比分/评价/标签，插件按 t1 契约 POST `https://food.dlsz.top/api/upload`。
- 写入门槛：只有明确添加意图或待补全草稿才写；普通聊天不写生产数据；缺字段中文追问、分数 0.1–5 校验、
  一次回复最多一次提交、重复用户名不重试；站点错误按 v1.1 `code`/v1 文本映射为可操作中文。
- 密钥默认发送者 QQ，由插件注入请求体，不进入 prompt/日志/回复；日期不提取，站点默认当天；用户名默认群昵称。
- 群聊非 @ 只对美食消息/图片参与（概率/冷却/最短字数），与 ambient 概率互斥不双触发；无文字美食图只追问不提交。
- direct 与 AstrBot memory 双模式注入幂等工具 catalog；`/option` 显示美食提交与美食话题状态。
- 新增 `docs/food-ai-submission.md`（契约/安全/测试证据/猫娘群 Bot 提示词）。
- 安全修复（t4 P1）：fetch_webpage 返回文本中和 `[[`、工具结果标记 `qqbot_enhancer_tool_processed`
  且 `on_decorating_result` 不再二次解析，恶意网页无法注入 `submit_food_review` 写入；
  被禁用/参数非法/字段超长/多条 marker 的提交工具整条替换为固定中文说明，不再保留模型“已提交”文字。
- 草稿隔离与无身份 fail safe：提交草稿按「会话 UMO + 发送者 ID」隔离，同群多用户交错补字段/确认
  不会串店名、分数、评价、用户名或密码；同一发送者跨群隔离；换店名重置旧草稿；TTL 过期清理；
  发送者 ID 为空时不建草稿、不写 cooldown、不提交，回固定中文说明。
- 全量 510 tests OK。

## v4.0.4

- 修复 AstrBot memory 结果链 marker 丢失 extra 时 `send_sticker` 不发图的问题。
- `on_decorating_result` 现在无论 memory extra 是否存在，只要发现 tool marker 就执行白名单工具并替换为真实附件。
- 失败时 marker 不残留，显示原因/可用分类；`/表情状态` 记录最近工具失败。
- 安全模糊匹配 category/tag/meaning，仅接受唯一最佳候选。
- 全量 453 tests OK。

## v4.0.3

- 内置 `qqbot_enhancer` 优先使用 bundled schema；实例 schema 权限拒绝/半写/损坏/空时自动回退并重试。
- 保存插件参数触发 AstrBot 热重载后，再次打开参数页不再因 schema 暂时不可读而阻塞。
- 移除 V1 根目录自动导入/移动；V1 文件不再被改写，当前 v4 实例保持不变。
- 全量 450 tests OK。

## v4.0.2

- 合并非 @ 群聊双概率：`reply_probability` 成为唯一普通参与概率。
- ambient 开启时只走 ambient 单一决策，未命中不再 fallback 旧 random；ambient 关闭才走 generic。
- `ambient_group_reply_probability` 弃用，旧配置不再叠加；关键词命中概率保留为独立分支。
- 全量 444 tests OK。

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
