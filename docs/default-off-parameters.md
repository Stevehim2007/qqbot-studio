# 默认关闭参数清单

> 本文件是本地开发/运维说明，不上传 GitHub。  
> “默认关闭”指新安装或缺少该键时的默认值；生产实例可能已在 Studio 中显式开启。  
> 修改前先确认作用域：实例级、按群级、还是仅 Studio 控制。

## 最重要的默认关闭项

| 参数 | 插件 | 默认 | 说明 | 开启位置 |
|---|---|---|---|---|
| `group_admin_override_enabled` | `qqbot_safe_tools` | `false` | 允许 QQ 群主/管理员在群内用 `/分群` 修改本群非敏感参数 | Studio → 插件管理 → `qqbot_safe_tools` → 参数 |
| `group_admin_allow_bot_admin` | `qqbot_safe_tools` | `true`（新增） | 允许 AstrBot 实例管理员在群内使用 `/分群`，不要求其是 QQ 群管理员 | 同上；默认开启，可关闭 |
| `control_bridge_enabled` | `qqbot_safe_tools` | `false` | 打开 Studio ↔ 插件的本机控制桥。关闭时群策略/预算页会 `bridge_unavailable` | Studio → 插件管理 → `qqbot_safe_tools`；同时 Studio 进程需 `QQBOT_PLUGIN_CONTROL_ENABLED=1` |
| `help_command_enabled` | `qqbot_core_reply` | `true`（新增） | 是否响应 AstrBot 内置 `/help`；关闭后 `/help` 不再响应，普通聊天不受影响 | Studio → 插件管理 → `qqbot_core_reply`；也可在群策略中按群覆盖 |
| `qqbot_core_reply.enabled` | `qqbot_core_reply` | `false` | 核心回复总开关。生产必须显式为 `true`，否则机器人不回复 | Studio → 插件管理 → `qqbot_core_reply` |

## 全量布尔默认关闭项

### qqbot_core_reply

| 参数 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 核心回复总开关（生产需显式开启） |
| `ambient_group_chat_enabled` | `false` | 群聊低频 ambient 参与；关闭时只按概率/显式 @ 参与 |
| `user_sticker_reaction_enabled` | `false` | @/私聊纯图片或表情时用本地短句反应，不调用 LLM |
| `cooldown.admin_bypass` | `false` | 管理员绕过冷却，由 safe_tools 授权策略决定 |

### qqbot_memory_commands

| 参数 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | `/记忆` 与 `/记忆管理` 命令总开关 |
| `destructive_enabled` | `false` | 允许删除、忘记等破坏性操作 |
| `allow_shared_persona` | `false` | 允许读取/切换已列出的共享人格 |
| `allow_global_scope` | `false` | 允许访问 global scope；需 policy 同时授权 |
| `persona_admin_shared_edit` | `false` | 允许管理员显式编辑共享人格 |

### qqbot_safe_tools

| 参数 | 默认 | 说明 |
|---|---|---|
| `control_bridge_enabled` | `false` | 本机控制桥；关闭时 Studio 群策略/预算接口不可用 |
| `group_admin_override_enabled` | `false` | 群管理员可覆盖本群参数的总开关 |
| `group_admin_allow_bot_admin` | `true` | 新增；实例管理员可在群内使用 `/分群` |

### qqbot_stickers

| 参数 | 默认 | 说明 |
|---|---|---|
| `sticker_ai_decorate_enabled` | `false` | AI 自动从本地表情中选择一张追加到回复 |
| `sticker_ai_collect_enabled` | `false` | AI 判断图片是否值得收藏为表情 |
| `sticker_natural_language_reclassify_ai_enabled` | `false` | 本地语义无法解析时允许 AI 分类 |
| `auto_collect_user_sticker_enabled` | `false` | 本地启发式自动收藏纯图片 |
| `sticker_ai_auto_collect_enabled` | `false` | 旧的本地启发式自动收藏开关 |

### qqbot_web_reader

| 参数 | 默认 | 说明 |
|---|---|---|
| `allow_http` | `false` | 允许读取明文 HTTP；默认仅 HTTPS |
| `fetch.allow_http` | `false` | `fetch` 子配置的 HTTP 开关 |

## 分群页为什么只显示部分参数

群策略页的小项来自 `qqbot_safe_tools/capability_policy.py` 的 `PLUGIN_PARAM_SPECS`，这是**安全白名单**，不是插件全部配置：

- 只暴露运行时真正读取、且允许按群覆盖的非敏感参数；
- `provider_id`、API key、secret、文件路径、管理员/白名单、工具授权、记忆 scope 等属于实例级或敏感项，不开放给群；
- `sensitive=true` 或 `group_overridable=false` 的参数即使出现在 schema 中，也不会在群策略页可编辑；
- 新增可覆盖参数需要同时补：`ParamSpec`、插件读取逻辑、测试、文档。

因此“所有参数都可改”不是目标；目标是**所有安全、按群有意义的参数都可改**。如果某个参数确实需要按群调整，应在对应插件里补策略读取并加入白名单。

## 修改后验证

1. Studio → 群策略 → 输入群号 → 加载策略；
2. 修改后保存，确认 revision 增加；
3. 在群里发下一条消息验证；
4. 检查审计记录；
5. 失败时先看 `/console` 的 503 `bridge_unavailable`、409 revision、422 参数校验。
