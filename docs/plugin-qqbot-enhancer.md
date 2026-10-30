# QQ Bot 增强插件（qqbot_enhancer）

本文档对应 `plugins/qqbot_enhancer/`。插件目标版本是 **AstrBot v4.29**，接口核对基于 AstrBot 官方仓库标签 `v4.29.3`（v4.29 系列最新补丁）的源码与官方中文文档。插件不使用 AstrBot WebUI/数据库私有接口，不修改 `qqbot_studio` 代码，也没有硬编码任何第三方 API Key。

## 1. 功能概览

- 群里被机器人 `@` 时必回；`AtAll`（@全体成员）或 `At.qq == "all"` 不算被机器人 `@`。
- 私聊消息必回。
- 非 `@` 群消息默认按 12% 概率回复，受每群冷却、每用户冷却、最小长度、纯图片/纯表情/空消息过滤限制。
- 支持 `direct` / `astrbot` 两种 `memory_mode`：前者省 Token 直连模型，后者交给 AstrBot 默认 Agent 使用长期会话历史。
- 图片消息优先尝试当前会话 provider 的原生多模态能力；失败后回退到插件配置的视觉 provider，或 AstrBot 的全局图片转述 provider。
- `/找图 关键词`（别名 `/搜图`）默认只用免 API Key 的 Bing Images HTML 找图，并基于标题/描述做中/日/英文相关性过滤；服务端下载并校验图片后使用本地 `Comp.Image.fromFileSystem` 发送，下载全失败时保留远程 URL 兜底。百度旧第二源默认关闭且默认绝不调用。带超时、安全搜索、结果数、群/用户冷却、大小限制和中文失败降级。
- @/私聊支持保守的自然语言找图意图，例如“你去找几张大树的图”“帮我搜两张千早爱音图片”；非 @ 群消息不触发，避免误伤普通聊天。
- `/唱歌`、`/点歌 [关键词]` 从 `songs/` 读取用户自己的本地授权音频，用 `Comp.Record.fromFileSystem` 发送；不调用 LLM/TTS/音乐生成，不消耗 token。
- `/option`（别名 `/插件用法`、`/用法`）显示本插件命令、当前模式和配置入口；通过公开 `Context.get_all_stars()` 列出可可靠读取的已加载插件元数据，不暴露配置值/API Key，不枚举不稳定的内部 handler 命令。
- 从插件的 `stickers/` 目录发送本地表情包；支持关键词、情绪和随机选择，并带会话冷却、防连续重复、文件缺失降级。
- @/私聊收到无文字纯图片/表情包或 `Comp.Face` 时，优先用自然短句反应，不调用 LLM；可按概率附加内置/收藏表情。
- 支持 `/收藏表情 [分类]`、`/表情分类`、`/表情列表 [分类]`、`/发表情 [分类]`，收藏表情持久化到 AstrBot 插件数据目录并参与正常回复。
- AI 语境选表情：direct 模式通过系统提示中的私有 `[[sticker:标签]]` 标记按表情情绪/收藏分类附一个本地表情；astrbot memory 模式用本地关键词/情绪/分类匹配 + 概率。不使用电脑控制、沙箱、终端或额外 LLM tool。
- 指令前缀消息不参与概率回复，避免抢占 AstrBot 指令。
- 回复成功后阻止 AstrBot 默认 LLM 再回复一次，避免重复。

## 2. 文件与自动加载

```text
plugins/qqbot_enhancer/
├── main.py                 # 插件入口，继承 astrbot.api.star.Star
├── metadata.yaml           # v4.29 插件元数据
├── _conf_schema.json       # AstrBot WebUI 配置 Schema
├── README.md
├── songs/                  # 用户自行放置授权音频；仓库不附带版权歌曲
│   └── README.md
└── stickers/               # 8 张自绘 96x96 PNG，无外部版权素材
    ├── sticker_rules.json
    ├── happy.png
    ├── sad.png
    ├── angry.png
    ├── thinking.png
    ├── love.png
    ├── sleepy.png
    ├── surprised.png
    └── cool.png
```

AstrBot v4.29 的实际加载规则（不是本文档发明）：

- `astrbot/core/star/star_manager.py` 会扫描插件目录，优先寻找 `<目录>/main.py`；找不到时寻找 `<目录>/<目录名>.py`。因此本插件入口必须是 `main.py`。
- 同一个 loader 使用 `metadata.yaml` 或 `metadata.yml` 读取元数据。必需字段是 `name`、`desc`、`version`、`author`；`display_name`、`support_platforms`、`astrbot_version` 等为可选字段。**v4.29 不要求 `plugin.json`**。
- `astrbot/core/star/base.py` 中 `Star.__init_subclass__` 会自动把继承 `Star` 的类注册到插件注册表。`astrbot/core/star/register/star.py` 仍保留旧 `@register` 装饰器，但源码已标注 deprecated，因此本插件不需要也不使用 `@register`。
- 插件目录下有 `_conf_schema.json` 时，loader 会生成 `data/config/<目录名>_config.json`，并把配置对象传给 `__init__(context, config)`。本插件使用这个官方配置路径，不自行发明配置格式。

相关官方源码/文档：

- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/star_manager.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/base.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/register/star.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/plugin-new.md>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/guides/plugin-config.md>

## 3. 安装

### 通过 QQ Bot Studio 部署（推荐）

使用本项目的 `./start.sh` 部署时，部署器会自动：

1. 把 `plugins/qqbot_enhancer/` 复制到实例的 `data/astrbot/plugins/qqbot_enhancer`；
2. 在 `data/astrbot/config/qqbot_enhancer_config.json` 中写入配置；
3. 把向导里填写的提示词同步到插件 `system_prompt`；
4. 再启动 AstrBot，让 AstrBot 首次启动时自动加载插件。

因此正常部署不需要手动复制插件。以下手动步骤用于独立 AstrBot 或其他调试场景。

### 手动安装

1. 确保运行 AstrBot **v4.29**。插件 `metadata.yaml` 声明 `astrbot_version: ">=4.29,<5"`。
2. 把整个目录放到 AstrBot 的插件目录：

   ```bash
   cp -a plugins/qqbot_enhancer /path/to/AstrBot/data/plugins/
   ```

   开发时也可软链接：

   ```bash
   ln -s "$PWD/plugins/qqbot_enhancer" \
     /path/to/AstrBot/data/plugins/qqbot_enhancer
   ```

3. 重启 AstrBot，或在 WebUI 的插件页重载插件。只要目录里存在 `main.py`、`metadata.yaml` 和继承 `Star` 的类，AstrBot 就会自动加载；不需要运行额外 Python 进程。
4. 打开 WebUI 插件配置，按需调整概率/冷却/视觉 provider/表情包选项。配置保存到：

   ```text
   data/config/qqbot_enhancer_config.json
   ```

5. 不需要 `requirements.txt`：插件只用 Python 标准库和 AstrBot 自带 API。

### 卸载

在 WebUI 禁用或卸载插件，或停止 AstrBot 后删除 `data/plugins/qqbot_enhancer`。如果不再使用，可同时删除 `data/config/qqbot_enhancer_config.json`。运行时冷却状态只存在内存中，重启即清空；插件不会把数据写入 `qqbot_studio`。

## 4. 行为与配置

### 4.1 @ / 私聊 / @全体

插件读取 `event.get_messages()`，逐个检查 `Comp.At`：

- `Comp.AtAll` 跳过；
- `qq` 强转为字符串后为 `all`/`everyone` 的兼容段跳过；
- `qq == event.get_self_id()` 才算被机器人 `@`。

`AtAll` 从 `At` 继承而来，因此代码先判断 `AtAll` 再判断普通 `At`。群聊 `@` 和私聊不受概率、每群冷却、每用户冷却、纯图片/纯表情过滤限制；只要消息不是指令，就会进入回复流程。即使 LLM 调用失败，也会发送配置的兜底文本，保证“必回”。

### 4.2 非 @ 群消息概率回复

依次满足以下条件才会进入随机回复：

1. 非私聊且不是被机器人 `@`；
2. 不是以 `ignore_command_prefixes` 或 AstrBot `wake_prefix` 开头；
3. 去除纯空消息后，文本长度不少于 `min_message_chars`；
4. 纯图片/纯表情默认不参与概率回复（可分别关闭 `ignore_pure_image`、`ignore_pure_face`）；
5. 距本群上一次概率回复已超过 `group_cooldown_seconds`；
6. 距本用户上一次概率回复已超过 `user_cooldown_seconds`；
7. 随机数小于 `reply_probability`。

冷却在调用 LLM 前写入，防止并发消息连发。@ 和私聊不会更新随机冷却状态。

### 4.3 配置项

| 配置 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `memory_mode` | string | `direct` | `direct`=省 Token 直连；`astrbot`=使用 AstrBot 默认 Agent 的会话历史 |
| `image_search_enabled` | bool | `true` | 启用 `/找图`、`/搜图` 及自然语言找图 |
| `image_search_natural_language_enabled` | bool | `true` | 允许 @/私聊自然语言找图；非 @ 群消息不触发 |
| `image_search_timeout_seconds` | int | `12` | Bing HTML / 百度 JSON 搜索页单次 HTTP 超时 |
| `image_search_result_count` | int | `1` | 每次命令最多发送的图片数（1–5） |
| `image_search_safe_search` | string | `strict` | Bing `adlt` 安全搜索：`strict`/`moderate`/`off` |
| `image_search_group_cooldown_seconds` | int | `15` | 图片搜索每群冷却 |
| `image_search_user_cooldown_seconds` | int | `10` | 图片搜索每用户冷却 |
| `image_search_max_response_bytes` | int | `2000000` | 搜索结果 HTML 最大字节数 |
| `image_search_max_keyword_chars` | int | `100` | 搜索关键词最大长度 |
| `image_search_second_source_enabled` | bool | `false` | 百度图片 JSON 旧第二源，默认关闭；默认绝不自动调用 |
| `image_search_relevance_enabled` | bool | `true` | 启用标题/描述相关性过滤 |
| `image_search_min_relevance_score` | float | `0.6` | 候选最低相关性分数；越高越保守 |
| `image_search_download_timeout_seconds` | int | `20` | 单张候选图片下载超时 |
| `image_search_download_max_bytes` | int | `8000000` | 单张候选图片最大下载字节数 |
| `image_search_download_attempts` | int | `8` | 每次命令最多尝试下载的候选数 |
| `image_search_success_prefix` | text | `为你找到这些图片：` | 成功发送图片前的文字 |
| `image_search_usage_reply` | text | 内置用法提示 | 缺少关键词提示 |
| `image_search_disabled_reply` | text | 内置提示 | 功能关闭提示 |
| `image_search_empty_reply` | text | 内置提示 | 无安全结果提示 |
| `image_search_error_reply` | text | 内置提示 | 网络/解析失败提示 |
| `image_search_timeout_reply` | text | 内置提示 | 超时提示 |
| `image_search_cooldown_reply` | text | `图片搜索冷却中，请约 {seconds} 秒后再试。` | 冷却提示模板 |
| `song_enabled` | bool | `true` | 启用 `/唱歌`、`/点歌` 本地点歌 |
| `song_group_cooldown_seconds` | int | `15` | 本地点歌每群冷却 |
| `song_user_cooldown_seconds` | int | `10` | 本地点歌每用户冷却 |
| `song_max_file_bytes` | int | `20000000` | 本地单曲最大字节数 |
| `song_success_prefix` | text | `🎵 本地歌曲：{name}` | 发送音频前的文字，支持 `{name}` |
| `song_disabled_reply` | text | 内置提示 | 点歌关闭提示 |
| `song_empty_reply` | text | 内置提示 | `songs/` 内无可用音频提示 |
| `song_not_found_reply` | text | 内置提示 | 关键词无匹配提示 |
| `song_too_large_reply` | text | 内置提示 | 音频超大小限制提示 |
| `song_error_reply` | text | 内置提示 | 本地音频组件构造失败提示 |
| `song_cooldown_reply` | text | `点歌冷却中，请约 {seconds} 秒后再试。` | 冷却提示模板 |
| `option_include_other_plugins` | bool | `true` | `/option` 是否列已加载插件元数据 |
| `option_max_plugins` | int | `20` | `/option` 最多显示插件数 |
| `option_max_chars` | int | `1800` | `/option` 输出最大字符数 |
| `reply_probability` | float | `0.12` | 非 @ 群消息回复概率 |
| `group_cooldown_seconds` | int | `30` | 每群概率回复冷却 |
| `user_cooldown_seconds` | int | `90` | 每用户概率回复冷却 |
| `min_message_chars` | int | `3` | 非 @ 群聊文本最小长度 |
| `max_input_chars` | int | `2000` | 送入 LLM 的用户文本最大长度 |
| `max_reply_chars` | int | `500` | 单条回复最大长度 |
| `ignore_pure_image` | bool | `true` | 非 @ 群聊忽略纯图片 |
| `ignore_pure_face` | bool | `true` | 非 @ 群聊忽略纯 QQ 表情 |
| `block_default_llm_for_group_non_at` | bool | `true` | 非 @ 群消息未触发本插件时，阻止默认 LLM 回复 |
| `ignore_command_prefixes` | list[str] | `["/"]` | 不参与概率回复的指令前缀 |
| `inherit_astrbot_wake_prefix` | bool | `true` | 同时使用 AstrBot 全局 `wake_prefix` |
| `system_prompt` | text | 内置简洁中文提示 | 直连 provider 时使用；无群人格映射时的旧默认 |
| `group_personas` | text(JSON) | `{}` | 实例级群人格映射；direct 按群注入，astrbot 模式绑定每 UMO 的 AstrBot Persona |
| `group_persona_enabled` | bool | `true` | 启用群人格映射 |
| `group_persona_max_groups` | int | `50` | 最多群映射数量 |
| `group_persona_max_prompt_chars` | int | `4000` | 单个人格 prompt 最大长度 |
| `group_persona_max_name_chars` | int | `64` | 人格名称最大长度 |
| `group_identity_context_enabled` | bool | `true` | direct 群聊身份上下文 |
| `group_identity_history_limit` | int | `8` | 最近用户消息条数 |
| `group_identity_history_ttl_seconds` | int | `600` | 身份上下文内存 TTL |
| `group_identity_message_max_chars` | int | `200` | 历史单条最大字符数 |
| `group_identity_nickname_max_chars` | int | `32` | 昵称最大字符数 |
| `group_identity_redact_in_reply` | bool | `true` | 回复中脱敏昵称/QQ/群号 |
| `group_identity_agent_prompt_max_chars` | int | `500` | astrbot memory 当前发送者注入最大长度 |
| `conversation_switch_enabled` | bool | `true` | 启用群聊 AstrBot 会话切换命令 |
| `conversation_title_max_chars` | int | `32` | 会话标题最大长度 |
| `conversation_list_limit` | int | `20` | `/会话列表` 最多显示条数 |
| `conversation_auto_create_enabled` | bool | `true` | `/会话 名称` 未找到时自动新建 |
| `ambient_group_chat_enabled` | bool | `false` | 启用群聊全量短期缓存/低频参与 |
| `ambient_group_reply_probability` | float | `0.03` | 非 @ ambient 参与概率 |
| `ambient_group_keyword_probability` | float | `0.5` | 命中关键词时参与概率 |
| `ambient_group_keywords` | list | `["机器人","小助手"]` | 关键词/点名列表 |
| `ambient_group_round_interval` | int | `0` | 每 N 条消息允许一次轮次门控（0 关闭） |
| `ambient_group_cache_limit` | int | `20` | 每群缓存条数 |
| `ambient_group_cache_ttl_seconds` | int | `300` | 缓存 TTL |
| `ambient_group_min_interval_seconds` | int | `60` | ambient 回复最小间隔 |
| `llm_timeout_seconds` | int | `45` | 单次 LLM/视觉请求超时 |
| `native_vision_first` | bool | `true` | 先尝试当前 provider 原生视觉 |
| `vision_provider_id` | string | `""` | 插件指定的视觉/图片转述 provider，WebUI 使用官方 `_special: select_provider` |
| `auto_use_astrbot_caption_provider` | bool | `true` | 自动使用 AstrBot 的图片转述 provider |
| `image_caption_prompt` | text | 内置客观描述提示 | 图片转述提示词 |
| `max_caption_chars` | int | `1200` | 图片描述最大长度 |
| `image_unavailable_reply` | text | 内置说明 | 原生视觉和转述都不可用时的兜底 |
| `llm_error_reply` | text | 内置说明 | 文本 LLM 失败兜底 |
| `empty_reply_fallback` | text | 内置短句 | LLM 返回空文本兜底 |
| `sticker_enabled` | bool | `true` | 启用本地表情包 |
| `sticker_probability` | float | `0.35` | 每次回复附加表情包的概率 |
| `sticker_cooldown_seconds` | int | `120` | 同一会话表情包冷却 |
| `sticker_prevent_repeat` | bool | `true` | 避免连续发同一张 |
| `sticker_ai_select_enabled` | bool | `true` | 启用 AI 语境选表情 |
| `sticker_ai_marker_enabled` | bool | `true` | direct 模式注入私有表情标签说明 |
| `sticker_ai_select_probability` | float | `0.6` | AI 语境选表情概率 |
| `sticker_ai_auto_collect_enabled` | bool | `false` | 可选 AI 自动识别收藏任意群成员纯图片 |
| `sticker_ai_auto_collect_probability` | float | `0.2` | 每条纯图片触发 AI 判断的概率 |
| `sticker_ai_auto_collect_max_per_hour` | int | `5` | 每会话每小时最多自动收藏尝试次数 |
| `sticker_ai_auto_collect_default_category` | string | `未分类` | AI 自动收藏默认分类 |
| `sticker_ai_auto_collect_require_image` | bool | `true` | 只处理 Comp.Image |
| `sticker_ai_auto_collect_provider_id` | string | `""` | 视觉 provider；留空用当前会话（DeepSeek Flash 1017 可用） |
| `sticker_ai_auto_collect_min_confidence` | float | `0.6` | AI 表情判定最低置信度 |
| `sticker_ai_auto_collect_saved_reply` | text | `这个表情我收下了（{category}）。` | 保存成功回复模板 |
| `sticker_natural_language_enabled` | bool | `true` | 启用显式 @/私聊自然语言发表情 |
| `sticker_natural_language_send_prefix` | string | `给你一个表情：` | 自然语言发表情前缀 |
| `sticker_natural_language_not_found_reply` | text | `没有找到「{target}」相关的表情，换个分类或情绪试试吧。` | 无匹配回复 |
| `sticker_natural_language_empty_reply` | text | `暂时没有可发送的表情。` | 表情池为空回复 |
| `sticker_natural_language_cooldown_reply` | text | `表情刚发过，稍等一下再试吧。` | 冷却中回复 |
| `sticker_natural_language_reclassify_enabled` | bool | `true` | 启用最近收藏自然语言重分类 |
| `sticker_natural_language_reclassify_ai_enabled` | bool | `true` | 本地无法解析时用当前会话 provider 受控分类 |
| `sticker_natural_language_reclassify_min_confidence` | float | `0.6` | AI 重分类最低置信度 |
| `sticker_natural_language_reclassify_moved_reply` | text | `已把刚才那个表情移到分类「{category}」。` | 移动成功回复 |
| `sticker_natural_language_reclassify_cached_reply` | text | `已把刚才那张图保存到分类「{category}」。` | 缓存新收藏回复 |
| `sticker_natural_language_reclassify_same_reply` | text | `刚才那个表情已经是「{category}」分类了。` | 同分类回复 |
| `sticker_natural_language_reclassify_not_found_reply` | text | `没有找到最近的表情或可收藏的缓存图片。` | 无记录/缓存回复 |
| `sticker_natural_language_reclassify_unclear_reply` | text | `没听清要分到哪个分类，可以发 /收藏表情 分类 手动指定。` | 无法解析回复 |
| `sticker_natural_language_reclassify_failed_reply` | text | `分类失败，旧收藏和分类未改动，请稍后再试。` | 移动失败回复 |
| `sticker_natural_language_reclassify_provider_id` | string | `""` | 语义 AI provider；留空用当前会话 |
| `sticker_natural_language_reclassify_memorized_reply` | text | `已记住：这个表情用于表达{meaning}（分类：{category}）。` | 语义标注成功回复 |
| `sticker_natural_language_reclassify_added_reply` | text | `已把刚才那个表情加入分类「{categories}」。` | 追加分类回复 |
| `sticker_natural_language_reclassify_removed_reply` | text | `已把刚才那个表情移出分类「{categories}」。` | 移除分类回复 |
| `sticker_natural_language_reclassify_categories_same_reply` | text | `刚才那个表情的分类没有变化。` | 分类无变化回复 |
| `sticker_semantic_tags_enabled` | bool | `true` | 启用 tags/meaning/tone 语义元数据与按标签调用 |
| `collected_sticker_max_tags` | int | `12` | 每个表情最多标签数 |
| `collected_sticker_max_tag_chars` | int | `24` | 单标签最大字符数 |
| `collected_sticker_max_meaning_chars` | int | `200` | meaning 最大字符数 |
| `collected_sticker_max_tone_chars` | int | `24` | tone 最大字符数 |
| `collected_sticker_multi_category_enabled` | bool | `true` | 启用一个文件多分类归属 |
| `collected_sticker_max_categories` | int | `8` | 每个表情最多分类数 |
| `collected_sticker_category_separators` | string | `,，、;；\|/` | 多分类分隔符 |
| `ai_unified_tool_protocol_enabled` | bool | `true` | 统一 direct AI 工具/表情协议 |
| `sticker_random_fallback_enabled` | bool | `false` | 无 marker 时是否允许旧随机贴纸兜底 |
| `safe_tools_enabled` | bool | `true` | 启用受限安全工具 intent router |
| `safe_tools_marker_enabled` | bool | `true` | direct 模式注入白名单工具 marker 说明 |
| `safe_tools_max_actions_per_reply` | int | `1` | 单条回复最多工具数 |
| `safe_tools_search_max_count` | int | `1` | search_images 单次最多图片数（硬上限 1） |
| `safe_tools_max_categories` | int | `20` | list_sticker_categories 最多展示分类数 |
| `safe_tools_allow_search_images` | bool | `true` | 允许 search_images |
| `safe_tools_allow_send_sticker` | bool | `true` | 允许 send_sticker |
| `safe_tools_allow_collect_sticker` | bool | `true` | 允许 collect_sticker |
| `safe_tools_allow_list_categories` | bool | `true` | 允许 list_sticker_categories |
| `safe_tools_sticker_prefix` | string | `给你一个表情：` | send_sticker 前缀 |
| `safe_tools_collect_cooldown_seconds` | int | `30` | collect_sticker 每会话冷却 |
| `max_images_per_message` | int | `4` | 单条消息最多送入 provider 的图片数 |
| `user_sticker_reaction_enabled` | bool | `true` | @/私聊纯图片/Face 自动短句反应 |
| `user_sticker_reaction_probability` | float | `0.85` | 自动表情反应概率 |
| `user_sticker_reaction_replies` | list[str] | 内置 4 条 | 随机自然短句列表 |
| `user_sticker_reaction_attach_sticker` | bool | `true` | 短句反应是否可附加本地表情 |
| `collected_sticker_enabled` | bool | `true` | 启用收藏表情保存/管理/使用 |
| `collected_sticker_max_count` | int | `200` | 收藏表情最大数量 |
| `collected_sticker_max_file_bytes` | int | `8388608` | 单张收藏表情最大字节数 |
| `collected_sticker_use_probability` | float | `0.5` | 正常回复时优先使用收藏表情的概率 |
| `collected_sticker_category_max_chars` | int | `32` | 收藏分类最大长度 |
| `collected_sticker_list_limit` | int | `30` | `/表情列表` 最多展示条数 |
| `pending_sticker_ttl_seconds` | int | `300` | 最近媒体内存缓存 TTL；每会话最多 3 张 |
| `auto_collect_user_sticker_enabled` | bool | `false` | 默认关闭；显式开启才做小图/方图本地启发式自动收藏 |
| `auto_collect_sticker_max_file_bytes` | int | `2000000` | 自动收藏候选最大字节数 |
| `auto_collect_sticker_max_dimension` | int | `512` | 自动收藏候选最大宽/高 |
| `auto_collect_sticker_default_category` | string | `未分类` | 自动收藏默认分类 |
| `auto_collect_sticker_reply_enabled` | bool | `true` | 自动收藏后是否短回复 |
| `auto_collect_sticker_cooldown_seconds` | int | `60` | 自动收藏每会话冷却 |
| `auto_collect_sticker_saved_reply` | text | `收下了，已收藏到「{category}」。` | 自动收藏成功回复模板 |

### 4.4 长期对话记忆模式

`memory_mode` 有两个选项：

- `direct`（默认）：插件直接调用 provider，只发送稳定的系统提示词和当前消息，
  不携带跨轮对话历史。Token 更省，适合低概率陪聊。
- `astrbot`：插件把本次回复交给 AstrBot 默认 Agent 处理。AstrBot 会使用自己的
  会话管理器、人格、上下文历史和 Agent 能力；历史按群聊/私聊会话分别保存。

在 `astrbot` 模式下：

1. @ 和私聊消息会直接交给默认 Agent；
2. 非 @ 群消息只有在概率、冷却、过滤条件都通过后，才把该条消息标记为可唤醒，
   让默认 Agent 处理；未命中时仍然阻断默认 LLM，避免机器人对每条群消息都回复；
3. 表情包通过 `on_decorating_result` 钩子追加，不阻断 Agent 的主回复；
4. 对话历史、会话切换、`/reset` 和 `/new` 由 AstrBot 自带会话功能管理。

记忆模式的 token 成本会随历史变长而增加。可以用 `/reset` 清空当前会话上下文，
用 `/new` 开启新会话；也可以降低 `reply_probability`、增大冷却或缩短保留的历史轮数。

### 4.5 `/找图` 网络图片搜索与自然语言找图

命令形式：

```text
/找图 橘猫
/搜图 风景 山
```

@ 机器人或私聊时，也支持保守的自然语言意图：

```text
你去找几张大树的图
帮我搜两张千早爱音图片
给我来一张猫猫壁纸
```

自然语言识别约束：

- **仅对 @ 机器人和私聊生效**。非 @ 群消息即使命中概率回复，也不会转成找图，避免把普通聊天批量变成搜索请求。
- 触发表达式需要同时具备“找/搜/搜索/查找/发/来/整/搞/要”等动作词、图片名词（图/图片/照片/壁纸/头像等），以及可提取的关键词。
- 从“帮我/给我/你/去/一些/关于/的图片”等礼貌或量词中提取关键词；例如“帮我搜两张千早爱音图片” → `千早爱音`。
- 数量词会映射结果数：一/两/三/四/五、几张、一些；最终仍限制在 1–5 张，并沿用 `image_search_result_count` 的默认值。
- 保守拒绝普通聊天和否定表达，例如 `我找不到图片了`、`我没找到大树图片`、`这张图真好看`、`发几张图`、`不要发图片`。无法确定时不拦截，继续走原聊天流程。
- 命中后复用与 `/找图` 完全相同的搜索、下载、校验、临时文件与远程 URL 兜底流程；`memory_mode=astrbot` 时也直接执行搜索并 `should_call_llm(True)` + `stop_event()`，避免默认 Agent 再生成一条重复回复。
- 自然语言找图与命令共用 `image_search_group_cooldown_seconds` / `image_search_user_cooldown_seconds`。

命令由独立的 AstrBot `@filter.command("找图", alias={"搜图"}, priority=110)` 处理器负责，优先级高于普通 ALL 监听器（100），因此不会被概率回复逻辑吞掉。命令本身完成后 `should_call_llm(True)` + `stop_event()`，不会让默认 LLM 再解释一遍。`alias` 参数和 `priority` 均在 v4.29 `CommandFilter`/handler 注册器源码中；下载成功后使用 `Comp.Image.fromFileSystem`，全失败时使用 `Comp.Image.fromURL` 兜底：

- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/filter/command.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/guides/listen-message-event.md>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/message/components.py>

搜索与下载实现约束：

- 默认且正常只请求 Bing Images HTML（`https://www.bing.com/images/search`），带 `setlang=zh-hans/en-US`、`adlt` 安全参数和结果数量。百度图片 JSON（`https://image.baidu.com/search/acjson`）旧代码保留，但 `image_search_second_source_enabled` **默认 false**，默认不会请求、不会自动回退；只有管理员显式开启后才可能作为可选第二源。
- 不递归抓站、不提供任意搜索 endpoint 配置；搜索结果请求与图片下载请求都在 `asyncio.to_thread` 中执行，不阻塞 AstrBot 事件循环。
- 搜索页/JSON 最多读取 `image_search_max_response_bytes + 1` 字节，检查 `Content-Length`、`Content-Type`；单张图片最多读取 `image_search_download_max_bytes + 1` 字节。
- Bing 优先解析 `a.iusc[m]` JSON 的 `murl` 以及 `t`/`desc`/`purl` 标题、描述、来源页；百度旧代码解析 `middleURL`/`thumbURL`/`hoverURL`/`replaceUrl` 和 `fromPageTitleEnc`/`fromPageTitle` 等标题字段。没有标题/描述元数据的 fallback URL 会被相关性过滤保守丢弃。
- 搜索与下载 URL 都必须通过公网 HTTP(S) 过滤：拒绝 `file://`、`javascript:`、带 userinfo URL、localhost/`.local`/`.internal` 等内部域名，以及私有/回环/链路本地/保留 IP；重定向链最多 3 次，最终 URL 再校验一次。百度图片代理可无扩展名，最终由下载 Content-Type + magic 校验。
- 相关性策略（`image_search_relevance_enabled=true`，默认阈值 0.6）：对关键词与标题/描述做 NFKC、casefold、常见日文异体字（如 `愛→爱`）和标点空白规范化；CJK 使用压缩字符串匹配，英文使用完整词匹配；提供少量名称别名（如 `千早爱音` ↔ `Anon Chihaya`）。无标题/描述或低于阈值的候选不进入下载。
- 单张图片下载必须同时通过：Content-Type 是图片（或 octet-stream 且 magic 有效）、JPEG/PNG/GIF/WebP/BMP/AVIF magic、文件大小上限。下载请求带浏览器 User-Agent 和对应搜索源的 Referer。
- 成功时写入 `tempfile` 并登记 `event.track_temporary_local_file(...)`，用 `Comp.Image.fromFileSystem(...)` 发送本地文件；单张失败会自动尝试下一个候选。只有全部下载失败时才用远程 `Comp.Image.fromURL(...)` 兜底。
- 默认发送 1 张，可配置到 5 张；下载尝试次数可配。群/用户冷却在网络请求前占用；缺少关键词、无结果、超时、网络错误均有独立中文提示配置。

**限制：** Bing/Baidu 页面或 JSON 都不是稳定官方 API，页面改版、地区/语言差异、反爬验证、网络不可达时可能解析不到结果。相关性过滤是词面/标题描述启发式，不是视觉内容识别；摘要缺失、语言别名缺失、第三方标题误导时会保守地少发或不发，宁可“没找到”也不发无关图。插件不下载原图到插件目录，临时文件由 AstrBot 事件清理；图片 URL 结构合法但下载失败时会尝试远程 URL 兜底，是否需要登录/防盗链由 QQ 协议端决定。若最终平台仍无法加载，插件收不到平台侧渲染结果。

### 4.6 图片搜索的可选 LLM tool

v4.29 确实提供 `@filter.llm_tool(...)` 官方装饰器，但本版本**没有**注册 `search_images` LLM tool。原因是默认 Agent 的 tool 结果更适合返回文本/结构化数据，而本需求的可靠交付方式是“命令/显式意图直接发送图片”；在 tool 内发送图片还会涉及 Agent 后续总结和潜在重复回复。

自然不会因此失去找图能力：插件在 @/私聊的 `on_qq_message` 中做保守的正则意图识别，命中后直接复用命令的搜索下载流程，并且即使 `memory_mode=astrbot` 也不会把该条交给默认 Agent。若未来需要让 Agent 自主决定调用图片搜索，可基于官方 `filter.llm_tool` 单独设计 tool 返回协议。

### 4.7 本地唱歌 / 点歌（不消耗 LLM token）

命令：

```text
/唱歌
/唱歌 文件名关键词
/点歌
/点歌 文件名关键词
```

- 从插件目录 `songs/` 读取直属音频文件，不递归、不跟随符号链接，拒绝 `../` 路径穿越。
- 支持 `.mp3`、`.wav`、`.ogg`、`.flac`、`.m4a`、`.aac`；单文件大小受 `song_max_file_bytes` 限制，空文件和超限文件跳过。
- 发送使用 AstrBot 官方 `Comp.Record.fromFileSystem(...)`；处理函数直接回复并阻断默认 LLM，不调用 `Context.llm_generate()`、TTS 或音乐生成服务，因此不产生模型 token。
- 群/用户冷却沿用 `song_group_cooldown_seconds` / `song_user_cooldown_seconds`；没有可用音频、关键词无匹配、文件超限、组件构造失败均有中文降级提示。
- 仓库只提供 `songs/README.md`，**不附带任何版权歌曲**。管理员需自行确认音频授权和平台格式支持；部分 AstrBot/QQ 适配器可能只可靠播放 WAV。

### 4.8 `/option` 插件用法与配置入口

```text
/option
/插件用法
/用法
```

命令由 `@filter.command("option", alias={"插件用法", "用法"}, priority=110)` 注册，使用 `should_call_llm(True)` + `yield` + `finally stop_event()`，避免默认 LLM 再回复一遍。输出为中文纯文本，至少包含：

- 本插件展示名与版本；
- `/找图`、`/搜图`、`/唱歌`、`/点歌`、`/收藏表情`、`/表情分类`、`/表情列表`、`/发表情`、`/option` 命令；
- 当前 `memory_mode`、@/私聊必回、非 @ 概率、群/用户冷却和自然语言找图开关；
- 配置入口：AstrBot WebUI → 插件 → QQ Bot 增强回复 → 配置。

已加载插件枚举使用 AstrBot v4.29 **公开 Context API**：

```python
stars = self.context.get_all_stars()  # list[StarMetadata]
```

依据：

- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/context.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/star.py>

`StarMetadata` 的公开字段包括 `name`、`display_name`、`version`、`desc`、`activated`、`root_dir_name` 等。`/option` 只使用这些元数据字段，并受 `option_include_other_plugins`、`option_max_plugins`、`option_max_chars` 限制。

**覆盖范围限制：** AstrBot v4.29 没有稳定的公开 API 能把 handler 全名映射为用户命令名；本插件不访问内部 `star_handlers_registry`，也不会为其他插件猜测或伪造 `/命令`。因此其他插件只列可利用公开 `get_all_stars()` 可靠取得的名称/版本/描述/激活状态，不列 handler/子命令。如果 `get_all_stars()` 不可用，输出明确说明“仅列本插件”。输出不读取插件配置值，不暴露 API Key。

### 4.9 用户表情反应、收藏与分类

#### 自动反应

对 @ 机器人或私聊发来的**无文字纯图片/表情包**、或纯 `Comp.Face`：

1. 先检查 `user_sticker_reaction_enabled` 和 `user_sticker_reaction_probability`；
2. 命中后从 `user_sticker_reaction_replies` 随机选择一条中文短句；
3. 不调用 LLM，设置 `should_call_llm(True)` 并 yield；
4. `user_sticker_reaction_attach_sticker=true` 时，可能通过正常贴纸路径附赠一个内置或收藏表情，仍受 `sticker_probability`、`sticker_cooldown_seconds`、`collected_sticker_use_probability` 控制；
5. 非 @ 群消息不进入该自动反应分支，继续走原有概率/冷却逻辑。

带文字的图片仍按原识图/聊天逻辑处理，避免把带问题或说明内容的图片误判成纯表情包。

#### 收藏命令

```text
/收藏表情 [分类]
/表情分类
/表情列表 [分类]
/发表情 [分类]
```

- `/收藏表情 [分类]` 的媒体解析优先级：
  1. 当前消息中的 `Comp.Image`；
  2. 引用消息中通过 AstrBot `Comp.Reply.chain` 可读取的图片；
  3. 本会话内存中最近 3 张用户图片缓存的最新一张（TTL 默认 300 秒）。
- 不带分类时默认 `未分类`。QQ 原生 `Comp.Face` 只有 id、没有可保存的图片字节，因此不能收藏，会中文提示改用图片/贴纸形式。
- 支持自然语言显式收藏，例如引用图片后 @ 机器人说“收藏这个表情 猫猫”；也支持先发图片，再单独 @ 机器人发送 `/收藏表情 猫猫` 从最近缓存取图。
- 最近媒体缓存只存在内存：按 `unified_msg_origin` 每会话最多 3 张已解码图片，不写盘，不记录用户文字、用户 ID 或绝对路径；过期或会话过多时淘汰。
- `auto_collect_user_sticker_enabled` **默认 false**。默认绝不会因用户发普通图片而自动收藏。只有管理员显式开启后，纯图片 @/私聊才可能通过本地尺寸/宽高比/大小启发式自动收藏明显的小图/方图；仍受 max bytes、max dimension、每会话冷却和总数量限制，大图不会自动采集。
- `/表情分类` 输出分类与数量；`/表情列表 [分类]` 输出文件名（不含绝对路径）；`/发表情 [分类]` 随机发送一张收藏图片。
- 没有实现删除命令；管理员需要清理时手动管理插件数据目录。

#### 数据目录与安全

使用 AstrBot v4.29 公开的 `StarTools.get_data_dir(plugin_name=...)`，不会写回插件源码目录：

```text
data/plugin_data/qqbot_enhancer/collected_stickers/<分类>/<sha256>.<ext>
data/plugin_data/qqbot_enhancer/collected_stickers/index.json
```

依据：

- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/star_tools.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/guides/storage.md>

安全与一致性：

- 分类只允许中文、字母、数字、`-`、`_`，有最大长度；拒绝 `../`、`/`、绝对路径等。
- 文件名完全由服务端生成：SHA-256 + 由 magic 判定的扩展名；不使用用户输入拼路径。
- 分类目录、文件、`index.json` 均做符号链接/目录边界检查；所有写入使用同目录临时文件 + `os.replace`，index 更新为原子写。
- 全局 SHA-256 去重；重复收藏不会新增文件，并提示已有分类。
- 写入前检查 `collected_sticker_max_file_bytes`，保存前检查 `collected_sticker_max_count`；图片格式通过 magic 校验。
- `index.json` 只保存 sha256、文件名、分类、大小、创建时间、来源适配器名；不保存用户文字、用户 ID 或其他隐私。
- 冷启动时加载 `index.json`；缺失文件、非法分类/文件名、超限/损坏条目会跳过。旧内置 8 张 stickers 不受影响。

收藏表情会参与 `_maybe_pick_sticker`：当 `collected_sticker_use_probability` 命中且收藏可用时，优先从收藏池（可按消息中出现的分类名筛选）选择；否则继续使用内置 stickers。已有冷却和防重复逻辑同时生效。

**限制：** 数据目录写权限、index 手工损坏、收藏图片跨容器/协议端可读性均未做真实部署验证；插件不会自动把用户发送过的图片全部收藏，必须显式使用 `/收藏表情`。

### 4.10 AI 语境发表情（不使用电脑控制）

#### direct 模式：私有标签协议

当 `sticker_ai_select_enabled=true`、`sticker_ai_marker_enabled=true` 且 `memory_mode=direct` 时，插件在发送给 provider 的 system prompt 末尾追加一段极短说明：

```text
可选表情标签：happy, sad, ... 收藏分类名 ...
仅在确实贴切时，于回复末尾原样附加一个
[[sticker:标签]] 标记；不需要就别输出标记，不要输出多个，不要解释标记。
```

- 可用标签只来自内置 stickers 的合法 `emotion` 值和收藏表情的安全分类名；不暴露绝对路径、文件名或文件内容。
- 模型回复中的私有标记会被正则提取并从用户可见文本删除；合法标签通过 `sticker_ai_select_probability`、`sticker_cooldown_seconds`、`sticker_prevent_repeat` 检查后，附一个 `Comp.Image.fromFileSystem` 本地表情。
- 一次最多一个。多个标记、未知标签、非法字符、文件缺失、冷却中都只保留纯文本，绝不让标记泄漏。
- marker 模式开启但没有返回标记时，视为模型选择“不发表情”，不再随机补一张；仅当 `sticker_ai_select_enabled` 或 `sticker_ai_marker_enabled` 关闭时，才保留旧 `_maybe_pick_sticker` 随机路径（`sticker_probability`/收藏使用概率等）。
- 不注册 LLM tool，不需要 AstrBot 电脑控制/沙箱/终端权限，模型只能从注入的安全标签列表选择。

#### astrbot memory 模式：本地匹配 + 概率

默认 Agent 回复不保证能稳定返回插件私有标记，因此插件在 `on_decorating_result` 中：

1. 仅处理带 `qqbot_enhancer_memory_reply` 标记的 Agent 回复；
2. 若回复链里已有图片，则不再追加，保证一次最多一个；
3. 通过 `sticker_ai_select_probability` 做一次概率判断；
4. 命中后复用 `_maybe_pick_sticker`：优先本地关键词/情绪匹配、收藏分类匹配，再按 `sticker_probability`/冷却/防重复选择一个内置或收藏表情。

这不是视觉大模型判断，也不读取聊天历史文件；只是一个不消耗额外 token 的本地启发式。若需要更精确的语义判断，可在未来引入独立可选的本地/远程分类器，但本版本没有增加任何 LLM tool 或系统工具权限。

配置：`sticker_ai_select_enabled`、`sticker_ai_marker_enabled`、`sticker_ai_select_probability`。真实 QQ 端对本地表情展示/顺序仍未验证。

### 4.11 可选 AI 自动识别收藏（默认关闭）

`sticker_ai_auto_collect_enabled` **默认 false**；默认不会自动调用视觉 provider，也不会自动收藏普通群图片。

显式开启后，对**任意群成员**的无文字纯 `Comp.Image`：

1. 本地安全门槛：magic/格式、`auto_collect_sticker_max_file_bytes`、`auto_collect_sticker_max_dimension`；纯 `Comp.Face` 没有图片字节，不处理。
2. `sticker_ai_auto_collect_probability` 概率门；`sticker_ai_auto_collect_max_per_hour` 每会话每小时最多尝试次数；同时复用 `auto_collect_sticker_cooldown_seconds` 和收藏总数量限制。
3. 优先调用视觉 provider：
   - `sticker_ai_auto_collect_provider_id` 非空时使用该 provider；
   - 留空时使用 `Context.get_current_chat_provider_id(umo=...)` 的当前会话 provider。部署 DeepSeek Flash 1017 视觉模型时可以默认使用；
   - 只发送当前图片 `base64://...` 和固定 JSON 分类提示；不传用户文字、ID、文件路径，不启用工具/电脑控制。
4. 视觉模型只负责输出：
   ```json
   {"is_sticker": true, "category": "猫猫", "confidence": 0.95}
   ```
   解析失败/无 provider/调用失败/低置信度时回退本地小图/方图启发式；AI 明确返回 `is_sticker=false` 则不收藏。
5. 保存复用已有 `_save_collected_payload`：分类安全化、SHA-256 去重、原子文件/index 写入、数量/大小上限；保存成功只回一条短句 `sticker_ai_auto_collect_saved_reply`（只显示分类）。
6. 失败、非表情、大图、纯 Face、超频、超限都不回复、不阻塞正常聊天；不会泄露用户文字/用户 ID/绝对路径/API Key。

限制：真实视觉 provider 的图片识别准确性、DeepSeek Flash/Qwen 等实际视觉接口和 QQ 端展示均未验证；视觉判断会产生额外 token 消耗，默认关闭。AI 建议分类也只通过本地安全正则，非法分类回退默认分类。

### 4.12 模块化接口与依赖装配（1.19.0）

`main.py` 是 AstrBot 组合根：保留 `Star`、`filter` 装饰器、事件 hander、`Context`/`Comp` 适配和模块装配。业务能力拆分为：

| 模块 | 职责 |
|---|---|
| `interfaces.py` | `StickerRepository` / `MediaDecoder` / `ImageSearchProvider` / `SongRepository` / `LLMService` Protocol；`MediaPayload` / `StickerRecord` / `ImageCandidate` 数据对象 |
| `config.py` | 190 个默认配置集中定义、默认值兜底、旧 config 缺失键兼容、类型读取 |
| `media.py` | 图片 magic/尺寸、base64 解码、媒体引用、公网 HTTP(S) URL 过滤 |
| `image_search.py` | 中/日/英文相关性规范化、别名/阈值过滤纯函数 |
| `songs.py` | 本地授权音频路径解析、超大文件检测、歌曲 repository |
| `sticker_store.py` | 收藏 index 原子写/加载、旧 index 兼容、0755/0644 权限迁移、分类目录/文件校验、分类/去重查询辅助 |
| `llm.py` | sticker marker 提取、AI 判定 JSON 严格解析、注入标签指令 |
| `safe_tools.py` | 白名单 tool marker 解析、参数 schema/路径/URL/命令拒绝、指令文本生成 |
| `sticker_intent.py` | 显式自然语言表情请求解析、情绪/收藏分类安全解析 |
| `sticker_reclassify.py` | 最近收藏重分类句式/情绪映射、AI JSON 解析 |
| `main.py` | AstrBot handler/事件编排、网络/LLM 适配、依赖装配 |

兼容性：

- 配置 schema 仍 180 项、`DEFAULTS` 与 schema 完全一致；
- 旧 config 只需提供已有字段，新字段通过 `ConfigReader` 默认值补齐；
- 旧收藏 `index.json` 版本/字段仍可读取，旧内置 8 张贴纸保持不变；
- `main.py` 优先 `from .config/...`，直接测试 harness 使用同目录绝对导入 fallback；新增 `__init__.py` 后插件目录可作为 package 部署；
- 测试包含一个真实 package 相对导入加载 smoke，验证 main 可导入。

限制：`main.py` 仍需承载 AstrBot 装饰器 handler，未将命令实现全部搬到 commands 模块；真实 AstrBot 实例复制部署和 QQ E2E 未验证。此重构不改变用户可见行为。

### 4.13 受限安全工具意图路由

AstrBot v4.29 的 tool/function-calling API 没有被本插件当作稳定公开依赖；本版本使用**插件内私有 marker/intent router**，不注册任意 LLM tool，也不启用电脑控制/沙箱/终端。

#### 白名单与参数 schema

| 工具 | 参数 | 执行边界 |
|---|---|---|
| `search_images` | `keyword`（1–100 字）、`count`（1–5，实际附件硬上限 1） | 只调用现有 Bing 搜索/安全下载/大小 magic 校验；不接受 URL/path；复用图片搜索群/用户冷却；一次最多 1 张 |
| `send_sticker` | `category` / `emotion` / `tag` 三选一 | 只从内置/收藏表情池取；分类/标签安全正则；不接受路径；复用 sticker 冷却/防重复；最多 1 个附件 |
| `collect_sticker` | 可选 `category` | 只从当前消息/引用/本会话最近缓存取图；禁止任意文件路径；复用收藏大小/数量/原子写/去重；独立冷却 |
| `list_sticker_categories` | 无参数 | 只回安全分类名+数量，不含路径/Key；数量有上限 |

模型格式示例：

```text
好呀 [[tool:send_sticker emotion="happy"]]
```

解析器只接受 `[A-Za-z_][A-Za-z0-9_]*=值` 参数，白名单外工具/参数直接拒绝；任何参数包含 `..`、`/`、`\`、`://`、控制字符或命令关键字都拒绝。多个 marker、无效 marker、无权限、冷却中、工具执行失败时删除 marker 并回退纯文本；不泄露内部路径/Key，一次最多一个附件。没有 `eval/exec/subprocess`，没有 shell/终端/电脑控制，也没有模型自定义网络 URL 下载。

direct 模式仅向 system prompt 注入简短白名单说明；memory 模式不注入提示，但会解析 Agent 最终文本中的合法 marker，并在 `on_decorating_result` 替换回复链。与现有 sticker AI marker、`/option`、收藏/搜索/唱歌兼容。

#### 配置

`safe_tools_enabled`、`safe_tools_marker_enabled`、`safe_tools_max_actions_per_reply`、`safe_tools_search_max_count`、`safe_tools_max_categories`、`safe_tools_allow_search_images`、`safe_tools_allow_send_sticker`、`safe_tools_allow_collect_sticker`、`safe_tools_allow_list_categories`、`safe_tools_sticker_prefix`、`safe_tools_collect_cooldown_seconds`。

真实 AstrBot tool 联调未做（设计上不依赖 tool API）；真实 QQ E2E 未验证。

### 4.14 收藏目录权限与宿主机可读（1.7.1）

问题背景：AstrBot 容器内的插件若以 root/严格 umask 创建 `collected_stickers`，容易出现 0700/0600；宿主机上的 ubuntu 管理服务虽然能访问项目目录，却会在 `plugin_data/qqbot_enhancer/collected_stickers` 处收到 `Permission denied`。

本版本规则：

- 插件创建的收藏根目录/分类目录：**0755**（可遍历、不可写/执行文件）。
- 收藏图片文件和 `index.json`：**0644**（可读，不可执行）。
- 原子写完成后显式 `chmod`，不依赖 umask；临时文件删除/替换逻辑不变。
- 启动时对 `collected_stickers` 做一次 best-effort 迁移：
  - `os.walk(..., followlinks=False)`，只处理普通目录/普通文件；
  - 目录 chmod 0755、文件 chmod 0644；
  - 跳过符号链接和非普通文件；
  - 每个路径先 `resolve()` 并限制在插件 data 根内，越界绝不 chmod；
  - 失败只记录可操作日志，不删除收藏、不抛到聊天层；若父目录 `plugin_data/qqbot_enhancer` 无权限，插件仍可启动并返回存储不可用降级。
- 不改变分类安全、SHA-256 去重、文件大小/数量上限和原子写入。

若自动迁移无法修复父目录权限，可在宿主机项目根目录用有权限管理员执行（路径按实际 data 根调整）：

```bash
DATA_DIR="data/astrbot/plugin_data/qqbot_enhancer"
chmod 755 "$DATA_DIR" "$DATA_DIR/collected_stickers"
find -P "$DATA_DIR/collected_stickers" -type d -exec chmod 755 {} +
find -P "$DATA_DIR/collected_stickers" -type f -exec chmod 644 {} +
```

不要使用 `0777`，也不要删除 `collected_stickers` 或 `index.json`。`find -P` 不跟随符号链接。真实 AstrBot 容器与宿主机 ubuntu 管理用户仍可能存在 UID/GID、挂载/ACL/SELinux 差异，本版本未做真实容器 E2E；如仍 Permission denied，需要由部署管理员调整挂载或同组权限，而不是放宽到 0777。

### 4.15 显式自然语言发表情（1.8.0）

只在显式 @ 机器人或私聊文本中触发，非 @ 群消息不会误触发。支持“发/来/给我一个/给我发个 … 表情/表情包/贴纸/图”的明确请求：

- `给我发个嘲讽表情包` / `来一个阴阳怪气的表情` / `无语表情` / `鄙视/挖苦表情`：映射到安全现有 emotion，优先 `angry`，缺失时 `cool`；
- 开心/高兴→`happy`，难过/伤心→`sad`，生气→`angry`，思考→`thinking`，喜欢→`love`，困→`sleepy`，惊讶→`surprised`，酷→`cool`；
- `发张猫猫表情包`：先精确匹配收藏分类名（不区分英文大小写，中文精确）；不走文件路径猜测；
- `发个表情`：从内置+收藏池随机一张；
- 目标分类/情绪不存在：中文提示“没有找到…”，不发送图片、不进入 LLM；
- 冷却/防重复沿用 `sticker_cooldown_seconds`、`sticker_prevent_repeat`、`_last_sticker_at/_last_sticker_file`；冷却中发中文纯文本。
- 一次最多一张，成功回复链为文字前缀 + 一个本地 `Comp.Image.fromFileSystem`，不调用 LLM，不调用 safe_tools/电脑控制/终端。

优先级：slash 命令先由命令 handler 处理；AI sticker marker 或 safe_tools marker 文本不会被当作自然语言请求；无 marker 的显式自然语言请求才进入本分支。所有失败仅回退中文文本，不泄漏绝对路径/文件名/Key。真实 QQ 展示未验证。

配置：`sticker_natural_language_enabled`、`sticker_natural_language_send_prefix`、`sticker_natural_language_not_found_reply`、`sticker_natural_language_empty_reply`、`sticker_natural_language_cooldown_reply`。

### 4.16 自然语言重分类最近收藏（1.12.0）

修复实际场景：自动收藏已保存在 `未分类`，用户随后说“刚刚的表情包是嘲讽的意思”，插件仍应定位最近一次收藏并移动分类，而不是回复“没存下来”。

#### 最近收藏内存记录

- 每个 `unified_msg_origin` 仅记录最近一次成功收藏：`{sha256, category, filename, saved_at}`。
- 不记录用户 ID、用户文字或绝对路径；进程重启后自动清空，最多 256 个会话。
- 手动收藏、自动收藏、AI 自动收藏、safe_tools 收藏都通过 `_save_collected_payload`，因此共用该记录。

#### 识别与分类

只在显式 @/私聊中识别保守句式，例如：
- 刚刚的表情包是嘲讽的意思
- 刚才那个表情包分类为猫猫
- 这个表情是阴阳怪气
- 把上一个表情改成无语分类
- 刚刚那个是开心的

本地映射：
- 嘲讽/阴阳/无语/鄙视/挖苦 → 现有 emotion `angry`（本版本选择首个安全 emotion；`cool` 为兜底语义）；
- 开心/高兴→happy，难过→sad，生气→angry，思考→thinking，喜欢→love，困→sleepy，惊讶→surprised，酷→cool；
- 安全的短分类名（中文/字母/数字/-/_，≤32）直接作为目标分类，例如猫猫。

本地无法确定且 `sticker_natural_language_reclassify_ai_enabled=true` 时，调用**当前会话 provider**（部署 DeepSeek Flash 时默认可用）做受控 JSON 分类：
```json
{"category":"猫猫","confidence":0.9}
```
只发送分类意图文本，不传用户 ID/路径/图片；不注册 LLM tool、不启用电脑控制/沙箱/终端；低于 `sticker_natural_language_reclassify_min_confidence` 或 provider 失败时回退本地/纯文本提示，不移动旧收藏。

#### 安全移动

`CollectedStickerStore.reclassify(entries, sha256, new_category)`：
- 校验 SHA-256、分类 normalizer、文件名、旧路径、符号链接、新分类目录；
- `os.replace(old,new)` 原子移动，随后 chmod 0644、目录 0755；
- 原子写 index；index 写入失败时把文件移回旧分类并保留旧 index；
- 已在目标分类返回 `same`，不重复移动；
- 目标冲突/符号链接/越界返回安全失败；不删除旧收藏。

若没有最近收藏记录但当前消息/引用/最近缓存有图：直接保存到目标分类。无媒体则回复“没有找到最近的表情或可收藏的缓存图片”。分类意图不完整提示可用 `/收藏表情 分类`，不会谎称没保存。

普通聊天“我觉得这个表情很嘲讽”不触发；非 @ 群聊不触发；命令和 marker 文本优先。

语义元数据：成功标注时 index 条目增加 `tags`/`meaning`/`tone`/`updated_at`（旧 index 缺省为空数组/空字符串/0）；`sticker_semantic_tags_enabled=false` 时只保存分类。选择收藏表情时除 category 外会匹配 tags/meaning；AI marker 的标签候选包含收藏 tags/categories；safe_tools `send_sticker` 支持 `category`/`emotion`/`tag` 三选一。真实 AstrBot provider/QQ E2E 未验证。

### 4.17 全局收藏池与多分类归属（1.12.0）

收藏池是**全局单例**：所有群/私聊共用同一 `collected_stickers/index.json` 和文件池；只有“最近一次收藏”和“最近媒体”按 `unified_msg_origin` 内存记录，用于重分类定位。任意会话的 `/表情分类`、`/表情列表 分类`、`/发表情 分类` 都查询全局池，不做会话隔离。

多分类数据模型（向后兼容旧 index）：
- 每个条目同时保存 `category`（主分类/旧兼容字段）和 `categories`（安全分类数组，第一项为主分类）。
- 旧 index 无 `categories` 时自动迁移为 `[category]`，文件不移动、不重写内容。
- 一个文件只有一份，物理路径继续放在主分类目录；追加其他分类只更新 index，不复制文件；移除主分类时选择下一个分类为主分类并原子移动文件。
- 分类去重、安全正则（中文/字母/数字/-/_、长度限制）、最大分类数由 `collected_sticker_max_categories` 控制。
- `StickerStore.update_categories/add_categories/remove_categories` 原子 index 写入；需要移动主分类时 `os.replace`，index 失败会回滚文件；目录 0755/文件 0644；符号链接/路径/文件名安全校验保留。

命令语义：
- `/收藏表情 嘲讽,无语`、`/收藏表情 猫猫，可爱`：分隔符由 `collected_sticker_category_separators` 控制，第一个为主分类；同图 SHA-256 去重，不复制文件。
- `/表情分类`：按分类归属计数（一个多分类表情在每个归属各计 1 次），同时显示唯一图片总数。
- `/表情列表 分类`、`/发表情 分类`：按 `categories` 包含匹配，跨会话可见。
- 自然语言“加入分类/同时放到多个分类”会追加； “移出分类/只放到/只保留”会在明确语义下移除/替换；不因为普通聊天误改。
- tags/meaning/tone 与 categories 同条目保存；选择逻辑支持分类/tags/meaning/情绪匹配，safe_tools 的 `send_sticker` 支持 `category`/`emotion`/`tag`。

配置：`collected_sticker_multi_category_enabled=true`、`collected_sticker_max_categories=8`、`collected_sticker_category_separators`、`sticker_semantic_tags_enabled=true` 及 tags/meaning/tone 长度上限。真实 QQ/AstrBot 多分类展示未验证。

### 4.18 统一 AI 工具/表情调用协议（1.13.0）

统一原则：

- `ai_unified_tool_protocol_enabled=true` 时，direct 模式只注入一套 `[[tool:...]]` 白名单说明；AI 需要发表情时使用 `send_sticker(category|emotion|tag)`。
- 旧 `[[sticker:tag]]` 继续兼容解析，但同一回复不会同时执行两套：safe_tools marker 存在时先走 `_route_safe_tool`，旧 sticker marker 会被清理；safe_tool 非法/失败只回退清理后的文字，不再随机补图。
- 一次事件最多一条文字 + 一个图片/表情附件；memory 模式在原 Agent 结果链已有 Image 时不追加；无工具 marker 时只允许本地关键词/情绪/tags/category 匹配追加一张，不再随机补第二张。
- `sticker_random_fallback_enabled=false` 默认关闭无 marker 随机补发；旧随机路径需要在兼容模式/开关明确开启时使用。
- direct/memory 都只产生一个 result；不会生成第二个人格文字或第二个附件。工具日志只记录工具名和 success/failure，不记录参数值、路径、Key。
- 兼容项：旧 `sticker_ai_marker_enabled`/`sticker_ai_select_*`、safe_tools 权限/冷却、命令、`/option`、搜索/收藏/唱歌。

配置：`ai_unified_tool_protocol_enabled`、`sticker_random_fallback_enabled`；旧 safe_tools/sticker marker 配置保留。真实多 provider/QQ 展示未验证。

### 4.19 餐厅查询 `/food`（1.14.0）

固定只读接口 `https://food.dlsz.top`：

- `/api/locations/search?q=...`
- `/api/restaurants?q=...&location_id=...`

命令：

```text
/food 火锅
/food 南关区 火锅
/food 火锅 南关区
```

规则：
- 固定 host `food.dlsz.top`，仅 HTTPS，标准库 HTTP(S)，不拼接 shell/命令。
- 关键词/地点只做长度、控制字符、URL/path 校验后 `urlencode`；不允许用户自定义 host/URL。
- 两段参数会尝试地点在前/在后，先查 locations，命中后 q + location_id 查 restaurants；无地点直接 q。
- 请求有 `food_timeout_seconds` 超时、`food_max_response_bytes` 大小限制、固定 host 重定向校验、非 JSON/网络错误/无结果中文降级。
- 最多展示 `food_max_results`（硬上限 5）家：店名、地点、标签、均分/口味/性价比、评价数、最新评价日期，附 `https://food.dlsz.top`；不输出原始 JSON/路径。
- `event.should_call_llm(True)` + yield + `stop_event`，不额外 LLM；每用户会话 `food_cooldown_seconds` 冷却。
- 日志只记通用失败，不记用户 ID/Key/完整查询 URL。

真实网络冒烟（当前环境）：
- `/food 火锅`：返回 5 家，首条“回转小火锅自助…”，地点/标签/评分/评价日期格式化正常，LLM 调用 0。
- `/food 南关区 火锅`、`/food 火锅 南关区`：locations 找到南关区 id=7632，restaurants 带 location_id 请求返回 0 条，中文无结果降级；LLM 调用 0。
- 这是站点当前数据/位置映射结果，不代表长期稳定；真实 QQ 展示未验证。

### 4.20 按群人格 `group_personas`（1.15.0）

数据格式（实例级 JSON 字符串，旧配置 `group_personas={}` 时完全保持旧行为）：

```json
{
  "default": {"name": "默认", "prompt": "你是默认人格"},
  "groups": {
    "123456789": {"name": "群A", "prompt": "你是A群专属人格"},
    "987654321": "你是B群专属人格"
  }
}
```

#### direct 模式（已实现）

- `_effective_system_prompt(event)` 通过 `event.unified_msg_origin` 对应的群 ID 解析 prompt；私聊使用 `default`，未知群 fallback `default`，无 default 回退旧 `system_prompt`。
- 群 ID 仅允许 1–20 位数字；prompt/name 长度和映射数量受 `group_persona_*` 限制；非法 JSON、非法 group id、超长 prompt 被忽略，service 不抛异常并回退旧配置。
- direct 模式无 AstrBot 持久会话历史，两个群的 prompt 不会互相污染；日志不输出 prompt。
- 旧 `system_prompt`、AstrBot 默认人格和其它旧配置键继续兼容。

#### astrbot memory 模式（已实现，基于 AstrBot v4.29.3 官方 API）

已核实 AstrBot v4.29.3 官方文档和源码提供以下稳定插件能力：

- `filter.on_waiting_llm_request()`：在请求构建前触发；
- `filter.on_llm_request()`：可修改 `ProviderRequest.system_prompt`；
- `Context.persona_manager` 的 `get_persona/create_persona/update_persona`；
- `Context.conversation_manager` 的 `get_curr_conversation_id/get_conversation/new_conversation/update_conversation`，
  且 `update_conversation(..., persona_id=...)` 是文档化用法。

实现：

- `memory_mode=astrbot` 且启用 `group_persona_enabled` 时，按 `event.unified_msg_origin`（群会话/私聊会话）解析 `group_personas`。
- 为每个映射创建专用 AstrBot Persona：`qqbot_enhancer_group_<群号>`；`default` 使用
  `qqbot_enhancer_group_default`。绝不修改 `default`/全局人格或其它群的人格。
- `on_waiting_llm_request` 在 AstrBot 构建请求前把当前 UMO 的 conversation 绑定到该 Persona；
  AstrBot 自己注入 Persona，同时保留该 UMO 的持久会话历史。不同群使用不同 conversation + persona。
- 若 AstrBot 的 manager API 不完整，`on_llm_request` 仅对本轮请求追加“Group Persona Instructions”作为
  per-request fallback，不修改全局人格；`/option` 会明确显示 fallback/降级状态。
- 未映射群：有 `default` 时使用默认映射；没有 default 时保留 AstrBot 当前配置的全局人格，不伪造按群隔离。
- Provider/model/API Key 完全沿用 AstrBot 当前会话的 DeepSeek 默认 provider；插件不新增 Key、不创建第二套 provider、
  不直接调用 LLM。
- AstrBot 中删除群映射不会自动删除旧的专用 Persona（避免误删），只是不再绑定。群 ID/prompt/名称限制继续沿用
  `group_persona_*` 配置。

官方依据：
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/plugin.md#llm-请求时>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/plugin.md#人格设定管理器-personamanager>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/plugin.md#update_conversation>

限制：AstrBot 真实多群 E2E、真实 QQ 展示和 Persona DB 并发写入未在本环境验证；单测使用 mock
PersonaManager/ConversationManager/Provider。两群必须分别发送消息才能创建并绑定各自会话。

#### API/管理

复用已有、带 token 校验的 AstrBot/Studio 插件配置 API，不新增无鉴权端点：
- `GET /api/instances/<instance>/plugins/qqbot_enhancer/config`
- `POST /api/instances/<instance>/plugins/qqbot_enhancer/config`
- 请求头 `X-QB-Token: <管理服务 token>`；body 示例：

```json
{"values":{"group_personas":"{\"default\":{\"name\":\"默认\",\"prompt\":\"你是默认人格\"},\"groups\":{\"123456789\":\"你是A群人格\"}}"},"restart":false}
```

管理 API 只读写配置值，不记录 token/prompt 到日志。真实 AstrBot 多群人格 E2E、Agent 会话级 persona 能力均未验证。

### 4.20.1 Studio 提示词 profiles / active 切换

插件配置新增（由 Studio 提示词 API 原子同步，不建议手工编辑）：

- `prompt_profiles`: JSON 字符串，数组中每项含 `{id,name,prompt,pinned,created_at,updated_at}`；
- `prompt_profile_active_id`: 当前 active profile id。

规则：

- direct 模式下一轮使用 active profile 的 prompt；旧 `system_prompt` 只作迁移/fallback。
- astrbot memory 模式为每个 active profile 使用专用 Persona
  `qqbot_enhancer_prompt_<profile_id>`；检测到 conversation 当前 persona 与 active profile
  persona 不同时，通过官方 `ConversationManager.new_conversation(..., persona_id=...)`
  新建会话，避免旧会话历史串人格。
- 群人格优先级：`group_personas.groups[群号]` 精确映射 > active prompt profile >
  `group_personas.default` > 旧 `system_prompt`。因此群专属人格优先，未知群使用 active profile，
  没有 active profile 时才用 group default/legacy。
- Persona/Conversation Manager 不可用时，仅用 `on_llm_request` 做单轮 prompt fallback；
  `/option` 会显示降级，不修改 AstrBot 全局人格、不新增 Key、不切换 provider。
- profile/prompt 的 API 契约和 conversation_pending 语义见
  [`docs/ui-api-contract-v2.md`](ui-api-contract-v2.md) 第 12 节。

### 4.21 群聊身份识别上下文（1.16.0）

direct 模式 `_build_prompt` 增加结构化身份信息：

- 场景（群聊/私聊）、群号（有则）、发送者昵称、发送者 QQ（有则）。
- 明确提示“QQ/群号/昵称仅用于区分当前会话成员，禁止在回复中输出”。
- 最近用户消息按内存 `unified_msg_origin` 记录，prompt 每条前缀 `[昵称(QQ)]` 内容；当前消息同样前缀。
- 无昵称时使用 `未知` 或 `未知(QQ)`；私聊群号为 `无（私聊）`；纯图片/表情/空消息使用安全占位。
- 历史仅内存保存，受 `group_identity_history_limit`、`group_identity_history_ttl_seconds`、单条长度和会话上限限制；不写盘、不记录用户 ID/文字到日志。
- 回复脱敏：当 `group_identity_redact_in_reply=true` 时，模型回复中的当前/历史昵称、QQ、群号会被移除，避免直接展示。错误/日志不输出 prompt 或身份标识。
- 旧配置/关闭开关：`group_identity_context_enabled=false` 时保持旧 prompt 结构；旧 `system_prompt` 不受影响。

AstrBot memory 模式：使用官方 `filter.on_llm_request(event, req)` hook，仅向当前轮 `req.system_prompt` 追加极短的群号/昵称/QQ 和“身份仅用于区分当前群成员、不要输出”说明；不修改 event 文本、conversation history、provider/model/key，不写日志身份，不串群。私聊不注入群号。限制：AstrBot 历史消息仍没有逐消息身份前缀，只能保证当前轮 sending context；history/persona 绑定能力沿用既有官方 API 结论。

真实 AstrBot/QQ 多群身份、记忆和展示 E2E 未验证。

### 4.22 群聊按名称切换 AstrBot 会话（1.18.0）

仅群聊命令，使用 AstrBot v4.29.3 官方 `ConversationManager`，不直接写数据库：

- `/会话列表`：`get_conversations(umo)`，显示标题和安全短 id/当前标记，最多 `conversation_list_limit`。
- `/会话 名称` / `/切换会话 名称`：按标题精确查找，`switch_conversation(umo,cid)`；未找到且 `conversation_auto_create_enabled` 时走新建。
- `/新会话 名称`：同名存在则拒绝重复；否则 `new_conversation(umo,title=...,persona_id=...)` 后切换。
- 新建/切换后绑定当前群人格：复用现有 `AgentPersonaBridge`/`_sync_agent_group_persona`；保持同一 UMO，不同群会话隔离。
- 标题去空白、拒绝控制字符/路径分隔符，长度限制；API 缺失/异常时明确中文降级，不伪造成功；私聊只提示默认当前会话。
- 所有命令 `should_call_llm(True)` + yield + stop，不调用 LLM；不修改 provider/model/key。
- 配置：`conversation_switch_enabled`、`conversation_title_max_chars`、`conversation_list_limit`、`conversation_auto_create_enabled`。真实 AstrBot/QQ E2E 未验证。

### 4.23 群聊 ambient 读全群/低频参与/@ 发言人（1.19.0）

`ambient_group_chat_enabled` 默认 **false**，关闭时不缓存不参与，旧 @/概率路径不变。

启用时：
- 所有群消息进入按 UMO 隔离的内存短期缓存，含昵称/QQ/时间/文本/图片占位；`ambient_group_cache_limit` 条、`ambient_group_cache_ttl_seconds` TTL，不写盘、不记录敏感日志。
- 明确 @ 机器人仍必回。
- 非 @ 参与门控：低频 `ambient_group_reply_probability`、关键词/点名 `ambient_group_keywords` + `ambient_group_keyword_probability`、可选每 N 条轮次、`ambient_group_min_interval_seconds` 同群冷却；避免每条插话。
- @ 发言人：优先官方 `Comp.At(qq=...)`；组件构造失败/不可用时降级 `@昵称` 文本，绝不把 QQ 写进文本。
- direct 模式复用 group identity/persona/tools/sticker 管线；memory 模式给 Agent 结果链加上发言人 At（已有 At 不重复），仍受一次一条文字+一个附件约束。
- 回复/日志不暴露 QQ/群号；缓存按群隔离，不串群。
- 配置：`ambient_group_chat_enabled=false`、`ambient_group_reply_probability=0.03`、`ambient_group_keyword_probability=0.5`、`ambient_group_keywords`、`ambient_group_round_interval`、缓存条数/TTL、`ambient_group_min_interval_seconds`。
- 真实 AstrBot/OneBot 官方 At 组件与 QQ 群聊 E2E 未验证；不同适配器 At 支持可能不同，已做文本降级。

## 5. 与 AstrBot 默认 LLM 管线避免重复

插件自己调用 provider，属于“显式调用 LLM”路线，不把同一条消息再交给默认 Agent 管线。实际流程是：

```python
# 重要：AstrBot 的 should_call_llm(True) 才表示“禁止默认 LLM”。
# ProcessStage 的条件是 not event.call_llm，不能按方法名字面传 False。
event.should_call_llm(True)
yield result          # 官方允许：yield 会先进入后续 ResultDecorate/Respond 阶段
event.stop_event()    # 在 finally 中执行，阻止默认 LLM 和后续 handler
```

依据：

- `AstrMessageEvent.stop_event()`、`should_call_llm()` 在 v4.29 源码中存在：
  <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/platform/astr_message_event.py>
- AstrBot 官方文档明确说明：终止事件传播后，后续步骤（包括请求 LLM）不会再执行，示例也是先 `yield` 再 `event.stop_event()`：
  <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/guides/listen-message-event.md#控制事件传播>
- ProcessStage 只在 `not event._has_send_oper and event.is_at_or_wake_command and not event.call_llm` 等条件成立时走默认 Agent：
  <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/pipeline/process_stage/stage.py>

因此：

- 本插件已经发送回复时：`event.should_call_llm(True)` + `event.stop_event()` 双重阻止默认 LLM 重复回复。
- 非 @ 群消息没有触发本插件时：默认会调用 `event.should_call_llm(True)`（可配置关闭），避免 AstrBot 因 `@全体`、`wake_prefix` 或“引用机器人”而额外回一条。注意这里 **True 表示禁止默认 LLM**；真实事件的 `event.call_llm` 默认是 `False`，而 ProcessStage 的判断是 `not event.call_llm`。
- 指令前缀消息会提前 `return`，不修改 `event.call_llm`、不 `stop_event()`，让 AstrBot 指令和其他插件正常执行。

### 5.1 `yield` 后执行 `finally: stop_event()` 会不会导致结果发不出去？

不会。AstrBot v4.29 的 PipelineScheduler 在某个 async generator `yield` 时，会先递归执行后续阶段（ResultDecorateStage、RespondStage），然后才回到该 generator 继续执行 `finally`：

- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/pipeline/scheduler.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/pipeline/context_utils.py>（`call_handler` 在 yield 时调用 `event.set_result(ret)`）
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/pipeline/respond/stage.py>

也就是说，`yield result` 先让 RespondStage 拿到并发送结果；handler 恢复执行后才进入 `finally` 设置 stop。mock 测试 `test_yielded_result_occurs_before_stop_event` 检查了“迭代到结果时 event 尚未 stopped，生成器结束后才 stopped”，作为这一语义的回归保护。

注意：本插件优先级为 `100`，处理器成功回复后会终止事件传播，因此后续其他插件的普通消息监听器不会继续处理该条消息。这是为避免重复回复而刻意设置的行为。

## 6. 图片识别与降级

### 6.1 实际使用的 AstrBot API

- 从 `event.message_obj.message` / `event.get_messages()` 中取 `Comp.Image`。
- 图片引用优先取 `.url`、`.file`、`.path` 中可用的值；若都不可用，调用官方 `Image.convert_to_base64()`，包装成 `base64://...`。
- 调用 `Context.llm_generate(chat_provider_id=..., prompt=..., image_urls=[...])`。
  `Context.llm_generate` 是 v4.5.7 起提供的官方插件 LLM 接口，内部调用 provider 的 `text_chat(...)`：
  <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/context.py>
  <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/guides/ai.md>
- AstrBot 的 OpenAI-compatible provider 会把本地路径、URL、`base64://` 等媒体引用预处理成 base64 data URL：
  <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/provider/sources/openai_source.py>

### 6.2 原生视觉优先

插件先从 `Context.get_current_chat_provider_id(umo=event.unified_msg_origin)` 取当前会话 provider id，然后读取该 provider 实例的 `provider_config["modalities"]`：

- 列表包含 `"image"`：先走原生视觉；
- 列表为空：按 AstrBot 迁移兼容逻辑视为“未配置”，尝试原生视觉；
- 列表明确存在但不含 `"image"`：跳过原生视觉，直接走图片转述；
- `modalities` 未声明：无法确定，仍先尝试原生视觉，失败后降级。

当前材料显示 DeepSeek-V4.1-Flash 的模型能力包含 image 输入，且 AstrBot 的 OpenAI-compatible provider 支持把图片引用预处理成 data URL，因此可用时会直接走 `context.llm_generate(..., image_urls=...)`。插件**没有写死 DeepSeek 模型名或 API Key**；最终能否看图仍取决于部署时 AstrBot provider 是否选对模型、endpoint 是否兼容以及实际 API 返回。

注意：AstrBot 的 OpenAI-compatible provider 在部分图片请求错误时会自动去掉图片做文本重试。若发生这种内部降级，插件可能拿到一个基于文本的普通回复，而不一定知道模型实际没看到图片。这是上游 provider 行为，插件无法可靠区分。

### 6.3 图片转述降级

原生视觉请求抛异常或返回空文本时，按以下顺序选择图片转述 provider：

1. 插件配置 `vision_provider_id`（WebUI 通过官方 `_special: select_provider` 选择，不保存 Key）；
2. 若 `auto_use_astrbot_caption_provider=true`，读取 AstrBot 全局配置：
   `provider_settings.default_image_caption_provider_id`。该字段是 AstrBot v4.29 中官方用于“对话模型不支持图片时调用图片转述模型”的配置：
   <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/astr_main_agent.py>

图片转述也走 `context.llm_generate(chat_provider_id=转述provider, image_urls=...)`。拿到描述后，再请求当前文本 provider 生成最终回复；若第二步失败，则直接把图片描述作为简短回复返回。

如果原生视觉不可用且没有配置任何图片转述 provider，插件发送 `image_unavailable_reply` 中的兜底文本。这保证用户能收到回复，但**不伪装成已经看懂图片**。

## 7. 本地表情包

### 7.1 发送方式

使用官方消息组件构建消息链：

```python
chain = [
    Comp.Plain(reply_text),
    Comp.Image.fromFileSystem(str(sticker_path)),
]
yield event.chain_result(chain)
```

`Image.fromFileSystem`、`Plain`、`chain_result` 均为 v4.29 真实 API：

- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/message/components.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/guides/send-message.md>

本插件也核对过 `Comp.Face(id=...)` 可发送 QQ 原生表情（仅 aiocqhttp 可靠支持），但本地表情包统一用 `Image` 发送，避免各平台 `Face` 支持差异。

### 7.2 规则文件

`stickers/sticker_rules.json` 使用：

```json
{
  "stickers": [
    {
      "file": "happy.png",
      "keywords": ["哈哈", "开心"],
      "emotion": "happy"
    }
  ],
  "emotion_keywords": {
    "happy": ["高兴", "愉快", "兴奋"]
  }
}
```

选择顺序：

1. 消息文本命中任一贴纸的 `keywords`（不区分大小写）→ 从命中的贴纸中随机；
2. 否则命中 `emotion_keywords` 的情绪词 → 从该情绪对应 `emotion` 的贴纸中随机；
3. 否则从全部有效贴纸中随机；
4. 若开启防重复且候选多于一张，排除本会话上一张后再随机；
5. 附加表情包还受 `sticker_probability` 和同一会话的 `sticker_cooldown_seconds` 控制。

`_load_sticker_entries()` 只接受 `stickers/` 同目录下的文件，拒绝 `../` 路径穿越；支持的扩展名是 `.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`。**不建议用 SVG 作为发送素材**：部分 QQ/协议端不支持 SVG 图片消息，建议先转成 PNG。manifest 缺失、JSON 损坏、单张图片被删除时，插件都会降级为纯文本，不抛异常。

### 7.3 示例素材

仓库自带 8 张 96×96 PNG 占位表情（`happy.png`、`sad.png`、`angry.png`、`thinking.png`、`love.png`、`sleepy.png`、`surprised.png`、`cool.png`），全部由本项目用标准库绘制/生成，合计约 4.6 KB，不含第三方版权素材或外部 CDN 引用。替换时请保持合法来源和相应授权。

## 8. 已知限制与未验证项

以下限制是明确的，不应被理解为已验证功能：

1. **未进行真实 QQ/AstrBot 联调。** 当前实现通过 AstrBot v4.29 源码核对 API，并用无 AstrBot 安装的 mock 单测验证行为；没有在真实 NapCat/QQ 账号、AstrBot WebUI 和真实模型端点上跑过端到端流程。
2. **`direct` 模式不包含 AstrBot 默认 Agent 的人格、完整会话历史、工具/技能和上下文压缩。** 该模式只发送插件 `system_prompt`、当前消息（以及可选图片描述），会话是单轮的。需要长期上下文时应把 `memory_mode` 设为 `astrbot`；`astrbot` 模式的 token 成本会随历史增长。
3. **图片转述不是原生视觉的等价替代。** 它依赖管理员在 AstrBot 中配置支持图片输入的 provider。未配置或配置的 provider 不支持图片时，只能返回配置的兜底文本；无法凭插件自行获得视觉能力。
4. **DeepSeek/OpenAI-compatible 的实际视觉行为以部署为准。** 插件的 modality 判断基于 provider 配置；若 provider 内部自动文本重试，插件可能无法判断模型是否真的看到了图片。
5. **本地表情发送的跨平台兼容性未验证。** 通用 `Image.fromFileSystem` 是 AstrBot 官方 API，但本地文件是否能被协议端读取取决于部署路径、回调文件服务/协议端所在机器等环境因素；失败时 AstrBot 侧会记录日志，插件本身只会降级组装纯文本。
6. **冷却/防重复状态只在内存中，重启清空，且只在单实例内有效。** 多进程/多机器人实例不会共享冷却。
7. **高优先级 handler 成功回复后会 `stop_event()`。** 这会阻止后续插件再处理同一条消息，属于有意的去重策略；若部署中还有其他必须同时响应普通消息的插件，需要评估顺序和冲突。
8. **纯图片/纯表情过滤只针对非 @ 群消息。** @ 机器人和私聊消息即使只有图片/表情也会处理，这是“必回”的设计取舍。
9. **Bing Images HTML（以及默认关闭的百度旧源）都不是官方稳定 API。** 搜索依赖第三方当前页面/JSON 结构；改版、地区差异、反爬验证或网络限制会导致无结果/失败。单测使用本地 fixture，不访问真实网络。
10. **搜索图片已在服务端下载并校验 magic/Content-Type/大小，但平台侧是否真正显示仍未验证。** 下载成功后会发送本地文件；远程 URL 只作为全部下载失败时兜底。QQ/协议端或本地文件回调服务仍可能在真实部署中失败，插件无法看到平台侧渲染/播放结果。
11. **没有实现可选 LLM tool。** 当前只提供 `/找图`、`/搜图` 命令和 @/私聊自然语言意图识别。AstrBot v4.29 的 `filter.llm_tool` 是真实 API，但本版本没有注册 `search_images` tool。
12. **URL 公网过滤不做 DNS 解析后的二次 IP 校验。** 下载前会拒绝明显的私有 IP、回环、内部域名和非 HTTP(S) 地址，但公网域名仍可能解析到内网地址（DNS rebinding/CNAME）。本插件确实会通过 urllib 下载图片，因此生产环境若威胁模型要求严格 SSRF 防护，应再加 DNS 解析后 IP 校验和网络出口策略。
13. **搜索关键词默认只发送给 Bing。** 百度只在管理员显式开启可选第二源时才会收到关键词；默认关闭。与所有第三方搜索一样，不应在关键词中提交密码、Token 或其他敏感信息。
22. **相关性过滤不是视觉理解。** 默认不额外调用视觉 LLM，只用标题/描述和有限别名做保守过滤；标题不准确、没有摘要、跨语言别名未覆盖时会少发或不发，但不应发明显无关图。可降低 `image_search_min_relevance_score` 放宽，但会增加误配风险。
14. **自然语言找图采用保守正则，不调用小模型做意图分类。** 复杂句式、方言、否定/反问、错别字可能漏识别或故意不触发；无法确定时交给普通聊天。当前只对 @/私聊生效，非 @ 群消息不转成搜索。
15. **本地点歌未内置任何歌曲，也不生成音乐/TTS。** 必须由管理员把有授权的本地音频放入 `songs/`。真实 QQ/协议端对 mp3/wav 等格式的播放支持未验证，部分适配器可能只可靠支持 WAV；本地文件是否能被容器/协议端读取也取决于部署路径。
16. **点歌只做名字关键词匹配，不解析标签/歌手/歌词。** 文件缺失、空文件、超限、符号链接和路径穿越会被跳过或提示，不会从网络下载歌曲。
17. **`/option` 不能可靠枚举其他插件的具体命令。** AstrBot v4.29 的 `Context.get_all_stars()` 能返回插件元数据，但没有稳定的公开 API 把 handler 返回为用户命令；因此 `/option` 只列其他插件的名称/版本/描述/激活状态，不读取内部 handler registry、不猜测命令，也不展示任何插件配置值或 API Key。
18. **纯 `Comp.Face` 不能收藏。** QQ Face 段只有表情 id，没有可保存的图片文件；可以触发自然短句反应，但 `/收藏表情` 只能保存带 `Comp.Image` 的消息，并会明确提示。
18a. **引用图片读取依赖 `Comp.Reply.chain` 是否被适配器填充。** v4.29 公共消息组件提供该字段，但不同协议端可能只给引用文字/为空；此时自动回退到本会话最近媒体缓存。插件不会读取任意历史消息或伪造引用能力。
18b. **普通图片不会自动收藏。** `auto_collect_user_sticker_enabled` 默认 false；即使显式开启，也是本地尺寸/大小/宽高比启发式，不调用视觉模型，不做全量采集；大图/长图/非图片格式会跳过。
22. **direct 模式依赖模型遵循私有标签格式。** 不同 OpenAI-compatible 模型的 system prompt 遵循度不同；不返回标记时只会走原随机表情路径或纯文本，不会额外调用模型/工具重试。未知/非法/多标记会被安全删除，不会泄漏。
23. **astrbot memory 模式是本地启发式，不是真正语境理解。** 它不会注入系统提示、不额外调用 LLM，只用关键词/情绪/收藏分类匹配与概率；命中不到时可能回退到原随机 sticker 逻辑。真实 Agent 结果链已有图片时不会追加第二个表情。
24. **没有增加任何电脑控制、沙箱、终端或 LLM tool 权限。** 模型不能读取本地文件；只从插件提供的安全标签/分类列表中选择。真实 AstrBot + QQ 对本地图片显示/顺序未验证。
25. **AI 自动识别收藏默认关闭，开启后才会消耗视觉 token。** 视觉 provider 对“表情包/贴纸”的判断、分类建议和 DeepSeek Flash 1017 实际视觉能力未在真实 AstrBot/QQ E2E 验证；调用失败会本地启发式降级，不会阻塞聊天。
26. **AI 自动收藏仅支持 Comp.Image。** Comp.Face 只有 QQ 表情 id、无图片字节，不处理；大图/未知尺寸/纯风景/截图可能被跳过或交由本地启发式判断。每会话每小时次数、冷却、总数量、文件大小均有硬限制。
27. **模块化重构不保证真实 AstrBot 实例复制部署零配置。** 新增 package `__init__.py`、main 相对导入和 test harness fallback 已在 mock/package smoke 中验证；但真实 AstrBot 插件加载器、Windows 路径/文件权限和 QQ E2E 未验证。`main.py` 仍保留 handler，业务模块未改变用户可见行为。
28. **安全工具通过私有 marker 而非 AstrBot tool API。** 本版本未验证/依赖 AstrBot v4.29 tool/function-calling 稳定性；模型可能不输出 marker、格式不合法或被直接拒绝，此时只回退普通文本。工具白名单不含任意 URL/文件/命令/电脑控制；真实 QQ 群聊工具联调未验证。一次回复最多一个附件。
29. **0755/0644 权限迁移只解决文件模式问题。** 若宿主机 ubuntu 管理服务与容器进程 UID/GID 不同、挂载点无遍历权限、ACL/SELinux 拒绝，0755/0644 仍可能无法读取；插件会记录日志但不提升到 0777，也不删除收藏。真实 AstrBot 容器 + 宿主机管理服务 E2E 未验证。
30. **自然语言发表情不是语义理解。** 只匹配明确的“发/来/给我…表情包”句式；普通聊天、非 @ 群聊、命令、marker 文本不会触发。嘲讽等映射到安全现有 emotion，不是按图片内容判断；真实 QQ 展示与用户自定义分类覆盖范围未验证。
31. **最近收藏重分类只作用于内存记录中的最近一次收藏。** 进程重启、其他会话/并发覆盖或 index 手工修改后，插件可能找不到最近记录而提示没找到；不会猜测或批量修改旧收藏。AI 分类仅提供分类名建议，实际移动仍走本地安全校验/回滚；真实 DeepSeek/provider 与 QQ 重分类 E2E 未验证。
32. **多分类是 index 归属关系，不是复制文件。** 主分类决定物理目录；追加分类不复制文件，移除主分类会移动文件。外部手工编辑 index、磁盘移动或旧版本缓存可能导致分类归属与物理路径不一致；插件会跳过无效条目，不删除数据。真实 QQ/AstrBot 多分类展示未验证。
33. **语义 tags/meaning/tone 和 AI 分类建议不保证语义准确。** 本地情绪映射与 AI JSON 都可能误判；只写入通过长度/字符/路径安全校验的元数据。AI 失败/低置信度不移动旧收藏；真实 DeepSeek/provider/QQ E2E 未验证。
34. **统一协议依赖模型遵循安全 marker 格式。** 不输出 marker、marker 非法或 safe_tool 无权限时只回退文字；旧 `[[sticker:...]]` 仍兼容但不再和新 safe_tool 同时触发。真实 AstrBot/QQ 的人格一致性、图片展示和 provider 兼容性未做端到端验证。
35. **`/food` 依赖第三方公开站点 `food.dlsz.top` 的当前接口/数据。** 站点路径、字段、位置映射、评价时间或可用性变化可能导致空结果/错误；当前环境真实冒烟火锅正常、南关区 location_id 当前无餐厅。真实 QQ 展示、长期 API 稳定性和站点权限策略未验证；不记录 Key/用户 ID，只读公开接口。
36. **按群人格在 direct 与 astrbot memory 模式均实现。** astrbot 模式基于 AstrBot v4.29.3 官方 `PersonaManager`、`ConversationManager.update_conversation(persona_id=...)` 和 `on_waiting_llm_request/on_llm_request`：按 UMO 绑定专用 Persona，保留会话记忆和默认 DeepSeek provider，不新增 Key。manager API 不可用时仅做单轮 system prompt fallback，不改全局人格。真实 AstrBot 多群 session/persona 行为、Studio 配置 API 端到端和 QQ 展示未验证；不记录 prompt/token。
37. **群聊身份上下文是 direct 插件内内存历史 + memory 当前轮 system prompt 注入，不替代 AstrBot 完整会话。** direct 只保留最近有限条消息；memory 通过 `on_llm_request` 只给当前发送者元数据，历史消息仍无逐消息身份前缀；多实例/重启/并发会丢失历史。QQ/昵称脱敏可能误删回复中的同名字词；真实 QQ 多群身份/记忆、Agent hook 和展示 E2E 未验证。不要把 QQ/昵称写入日志或发送给非 LLM 的下游。
38. **ambient 是低频启发式参与，不是真正语义理解。** 关键词/概率/轮次可能误参与或漏参与；缓存和冷却均为内存态，重启丢失；官方 `Comp.At` 在不同协议适配器可能不可用，降级为 `@昵称` 文本。QQ 号/群号只在 LLM 上下文和官方 At 使用，不在文本/日志输出；真实 QQ E2E 未验证。
19. **自动表情反应只针对无文字图片/Face。** 带文字的图片仍按识图/聊天逻辑处理。默认 `user_sticker_reaction_enabled=true` 时，@/私聊中的无文字纯图片会优先走短句反应而不是原生视觉理解；需要视觉理解时应带文字、关闭该开关，或显式调整概率。
20. **收藏表情数据目录/权限/index 损坏未做真实部署验证。** 使用公开 `StarTools.get_data_dir()` 和原子写入，但磁盘写满、权限变化、手工破坏 index、跨容器读取等仍可能导致收藏不可用；插件会降级提示，不会主动删除用户文件。没有实现破坏性删除命令。
21. **收藏表情不会自动收集所有用户图片。** 只有显式执行 `/收藏表情` 才会保存；不会保存朋友圈/聊天原文、用户 ID 或敏感路径。收藏文件跨 QQ/协议端可读性仍取决于部署与文件服务。

## 9. 校验记录

已执行：

```bash
python3 -m py_compile plugins/qqbot_enhancer/*.py
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
python3 -m unittest tests.test_enhancer_plugin -v
```

当前增强插件测试为 **180 tests**，覆盖：@/AtAll 判定、私聊必回、概率与冷却、@ 绕过冷却、`memory_mode=direct/astrbot` 回归、非回复分支正确设置 `call_llm=True` 阻断默认 LLM、yield 先于 stop_event、纯图片/表情过滤、指令跳过、原生视觉调用、视觉失败后的 caption 回退、AstrBot 全局 caption provider、无视觉兜底、base64 图片引用、关键词/情绪表情包、冷却、防重复、图片缺失降级、图片搜索关键词/URL 编码、Bing HTML 解析（含 title/desc 元数据）、百度 JSON 解析、相关性中/日/英文规范化/阈值过滤/无相关候选不发图/百度第二源默认不调用、非 HTTP(S)/内网 URL 过滤、下载 magic/Content-Type/大小校验、下载失败重试下一个候选、远程 URL 兜底、第二搜索源、搜索结果数、搜索冷却、自然语言意图与关键词提取（大树/千早爱音）、@ 群聊和私聊自然语言发送、数量词覆盖结果数、非 @ 不触发、普通聊天不误伤、自然语言搜索冷却、本地点歌成功/关键词/冷却/空目录/超大文件/路径穿越且不调用 LLM、`/option` 本插件命令/配置入口/已加载插件元数据/输出截断/别名注册/不暴露 Key/registry 不可用降级、@/私聊 Comp.Image/Face 自动短句反应/附加本地表情/非 @ 不触发、收藏分类/去重/非法分类/超大小/数量上限/无图片/纯 Face/分类列表/列表不泄露路径/发表情/收藏表情参与正常回复/冷启动 index 加载/旧内置 8 张贴纸兼容、引用图片优先、最近媒体缓存取图、缓存最多 3 张/TTL 过期、默认不自动收藏、显式开启小图自动收藏、大图跳过、自动收藏冷却、direct 模式 marker 注入/合法标签/非法/多标签删除/无标记纯文本/无标记不随机补发、收藏分类标签、memory 模式本地语义匹配/概率/收藏分类匹配、无 LLM tool/电脑控制调用、AI 自动收藏默认关闭、任意群成员图片视觉判定收藏、AI 失败本地启发式降级、AI 非表情/大图/纯 Face 不收藏、每小时限频、provider overrides、恶意分类安全回退、无 tool/电脑控制、模块导入/package 相对导入/ConfigReader 旧配置/旧 index/媒体/歌曲/相关性/store/llm helper、安全工具白名单/参数 schema/路径/URL/命令拒绝、search_images/send_sticker/collect_sticker/list_sticker_categories mock、权限关闭回退、多 marker 拒绝、memory 模式工具链替换、无 eval/exec/subprocess、收藏目录 0755/文件 0644、已有树迁移、符号链接跳过、越界不 chmod、root 创建权限失败降级、旧 index/收藏读写回归、自然语言嘲讽/开心/收藏分类/通用表情/无匹配/非 @ 不触发/冷却/禁用降级、一次一张且不调用 LLM、最近收藏重分类本地/AI/缓存/无媒体/普通聊天/不完整指令、移动成功 0755/0644、store move/index 原子回滚/同分类/符号链接拒绝、AI 不可用保留旧分类、命令/marker 不误触发、tags/meaning/tone index 持久化与旧 index 缺省兼容、AI 语义分类、按 tags 触发自然语言/AI marker/safe_tools send_sticker、全局收藏跨会话查询/发送、多分类文件去重单份、多分类命令/计数/列表/发送、追加/移出/只放到分类、旧 index categories 迁移、store 分类原子更新/权限、统一 AI 协议单 prompt、双 marker 只执行一个附件、无 marker 不随机补发、legacy sticker marker 兼容、memory 语义 only/单附件、非法 tool marker 纯文本回退、/food 关键词/地点双顺序/location_id/无结果/地点无匹配/超时/接口错误/非 JSON/最多结果/冷却/URL/host 安全/命令别名/不调用 LLM、group_personas JSON 解析/长度数量限制、两个群 prompt 隔离、私聊/未知群 default fallback、非法映射旧配置回退、memory 模式安全降级且不泄漏 prompt、群聊身份上下文同群两昵称/QQ 区分、私聊/无昵称 fallback、历史条数限制、回复身份脱敏、关闭旧 prompt 兼容、memory on_llm_request 当前发送者身份注入/私聊/无昵称/无 provider 变更/异常安全降级/重复注入去重、option 不泄漏身份、群聊会话列表/新建/切换/同名拒绝/自动新建/跨群隔离/API 缺失与异常降级/群 persona 绑定、ambient 默认关闭/低频缓存不触发/概率参与/官方 At/昵称文本降级/冷却/TTL/跨群隔离/@ 必回/隐私。单测本身**不访问真实网络**。

非单测在线冒烟（不代表 AstrBot/QQ 端到端）：

- 真实网络 smoke（默认 Bing-only、相关性开启、第二源关闭）：
  - `/找图 千早爱音`：Bing 返回 6 个相关性候选；首条标题为 `【BanG Dream!It's MyGO】千早爱音_...`，分数 1.0；实际命令发送 1 个本地 JPEG（51,430 字节，magic `ffd8ff...`）。
  - `/找图 大树`：6 个候选标题均含“大树”，分数 1.0；实际命令发送 1 个本地 JPEG（270,280 字节）。
  - `/找图 cat`：6 个候选标题均含 cat，分数 1.0；实际命令发送 1 个本地 JPEG（9,342 字节）。
- 相关性过滤 fixture 另验证了 `千早 愛音`、`Anon Chihaya`、无关“山景/美食”标题和无 title/desc 候选：只有相关候选进入下载，全部无关时回复“没有找到合适的图片”且下载调用为 0。
- 百度旧第二源测试仅在显式 `image_search_second_source_enabled=true` 的 mock 中覆盖；默认关闭测试断言百度 fetch 调用 0 次。真实百度网络请求本次不作为默认路径依赖。
- 临时文件已在冒烟后删除，未发送到真实 QQ。

第三方页面/接口结构和网络策略随时可能变化，因此上述在线结果不构成长期保证。

本次执行全仓回归：`python3 -m unittest discover -s tests -q`，当前 **383 tests OK**；其中本插件 180 tests 全部通过。全仓数量会随其他模块开发变化，以实际运行结果为准。
