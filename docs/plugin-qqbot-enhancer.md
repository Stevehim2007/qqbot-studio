# QQ Bot 增强插件（qqbot_enhancer）

本文档对应 `plugins/qqbot_enhancer/`。插件目标版本是 **AstrBot v4.29**，接口核对基于 AstrBot 官方仓库标签 `v4.29.3`（v4.29 系列最新补丁）的源码与官方中文文档。插件不使用 AstrBot WebUI/数据库私有接口，不修改 `qqbot_studio` 代码，也没有硬编码任何第三方 API Key。

## 1. 功能概览

- 群里被机器人 `@` 时必回；`AtAll`（@全体成员）或 `At.qq == "all"` 不算被机器人 `@`。
- 私聊消息必回。
- 非 `@` 群消息默认按 12% 概率回复，受每群冷却、每用户冷却、最小长度、纯图片/纯表情/空消息过滤限制。
- 支持 `direct` / `astrbot` 两种 `memory_mode`：前者省 Token 直连模型，后者交给 AstrBot 默认 Agent 使用长期会话历史。
- 图片消息优先尝试当前会话 provider 的原生多模态能力；失败后回退到插件配置的视觉 provider，或 AstrBot 的全局图片转述 provider。
- `/找图 关键词`（别名 `/搜图`）通过免 API Key 的 Bing Images HTML + 百度图片 JSON 双搜索源找图；服务端下载并校验图片后使用本地 `Comp.Image.fromFileSystem` 发送，下载全失败时保留远程 URL 兜底。带超时、安全搜索、结果数、群/用户冷却、大小限制和中文失败降级。
- @/私聊支持保守的自然语言找图意图，例如“你去找几张大树的图”“帮我搜两张千早爱音图片”；非 @ 群消息不触发，避免误伤普通聊天。
- `/唱歌`、`/点歌 [关键词]` 从 `songs/` 读取用户自己的本地授权音频，用 `Comp.Record.fromFileSystem` 发送；不调用 LLM/TTS/音乐生成，不消耗 token。
- `/option`（别名 `/插件用法`、`/用法`）显示本插件命令、当前模式和配置入口；通过公开 `Context.get_all_stars()` 列出可可靠读取的已加载插件元数据，不暴露配置值/API Key，不枚举不稳定的内部 handler 命令。
- 从插件的 `stickers/` 目录发送本地表情包；支持关键词、情绪和随机选择，并带会话冷却、防连续重复、文件缺失降级。
- @/私聊收到无文字纯图片/表情包或 `Comp.Face` 时，优先用自然短句反应，不调用 LLM；可按概率附加内置/收藏表情。
- 支持 `/收藏表情 [分类]`、`/表情分类`、`/表情列表 [分类]`、`/发表情 [分类]`，收藏表情持久化到 AstrBot 插件数据目录并参与正常回复。
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
| `image_search_second_source_enabled` | bool | `true` | Bing 结果不足时启用百度图片 JSON 第二源 |
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
| `system_prompt` | text | 内置简洁中文提示 | 直连 provider 时使用 |
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

- 默认搜索 Bing Images HTML（`https://www.bing.com/images/search`）；当 Bing 候选不足或失败时，使用百度图片 JSON（`https://image.baidu.com/search/acjson`）作为第二源，均不需要 API Key。`image_search_safe_search=strict` 会映射为 Bing 的 `adlt` 参数；百度 JSON 没有等价可控参数，第二源内容安全由百度自身策略决定。
- 不递归抓站、不提供任意搜索 endpoint 配置；搜索结果请求与图片下载请求都在 `asyncio.to_thread` 中执行，不阻塞 AstrBot 事件循环。
- 搜索页/JSON 最多读取 `image_search_max_response_bytes + 1` 字节，检查 `Content-Length`、`Content-Type`；单张图片最多读取 `image_search_download_max_bytes + 1` 字节。
- Bing 解析 `a.iusc[m]` JSON 的 `murl`，并做有限的 `"murl":"..."` / 图片扩展名 URL 兜底；百度解析 `data[].middleURL/thumbURL/hoverURL/replaceUrl`。每页最多检查 200 个候选。
- 搜索与下载 URL 都必须通过公网 HTTP(S) 过滤：拒绝 `file://`、`javascript:`、带 userinfo URL、localhost/`.local`/`.internal` 等内部域名，以及私有/回环/链路本地/保留 IP；重定向链最多 3 次，最终 URL 再校验一次。百度图片代理可无扩展名，最终由下载 Content-Type + magic 校验。
- 单张图片下载必须同时通过：Content-Type 是图片（或 octet-stream 且 magic 有效）、JPEG/PNG/GIF/WebP/BMP/AVIF magic、文件大小上限。下载请求带浏览器 User-Agent 和对应搜索源的 Referer。
- 成功时写入 `tempfile` 并登记 `event.track_temporary_local_file(...)`，用 `Comp.Image.fromFileSystem(...)` 发送本地文件；单张失败会自动尝试下一个候选。只有全部下载失败时才用远程 `Comp.Image.fromURL(...)` 兜底。
- 默认发送 1 张，可配置到 5 张；下载尝试次数可配。群/用户冷却在网络请求前占用；缺少关键词、无结果、超时、网络错误均有独立中文提示配置。

**限制：** Bing/Baidu 页面或 JSON 都不是稳定官方 API，页面改版、地区/语言差异、反爬验证、网络不可达时可能解析不到结果。插件不下载原图到插件目录，临时文件由 AstrBot 事件清理；图片 URL 结构合法但下载失败时会尝试远程 URL 兜底，是否需要登录/防盗链由 QQ 协议端决定。若最终平台仍无法加载，插件收不到平台侧渲染结果。

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

- `/收藏表情` 必须同一条消息带有 `Comp.Image`；不带分类时默认 `未分类`。QQ 原生 `Comp.Face` 只有 id、没有可保存的图片字节，因此不能收藏，会中文提示改用图片/贴纸形式。
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
9. **Bing Images HTML 和百度图片 JSON 都不是官方稳定 API。** 搜索依赖第三方当前页面/JSON 结构；改版、地区差异、反爬验证或网络限制会导致无结果/失败。单测使用本地 fixture，不访问真实网络。
10. **搜索图片已在服务端下载并校验 magic/Content-Type/大小，但平台侧是否真正显示仍未验证。** 下载成功后会发送本地文件；远程 URL 只作为全部下载失败时兜底。QQ/协议端或本地文件回调服务仍可能在真实部署中失败，插件无法看到平台侧渲染/播放结果。
11. **没有实现可选 LLM tool。** 当前只提供 `/找图`、`/搜图` 命令和 @/私聊自然语言意图识别。AstrBot v4.29 的 `filter.llm_tool` 是真实 API，但本版本没有注册 `search_images` tool。
12. **URL 公网过滤不做 DNS 解析后的二次 IP 校验。** 下载前会拒绝明显的私有 IP、回环、内部域名和非 HTTP(S) 地址，但公网域名仍可能解析到内网地址（DNS rebinding/CNAME）。本插件确实会通过 urllib 下载图片，因此生产环境若威胁模型要求严格 SSRF 防护，应再加 DNS 解析后 IP 校验和网络出口策略。
13. **搜索关键词会发送给 Bing/百度。** 与所有第三方搜索一样，不应在关键词中提交密码、Token 或其他敏感信息。
14. **自然语言找图采用保守正则，不调用小模型做意图分类。** 复杂句式、方言、否定/反问、错别字可能漏识别或故意不触发；无法确定时交给普通聊天。当前只对 @/私聊生效，非 @ 群消息不转成搜索。
15. **本地点歌未内置任何歌曲，也不生成音乐/TTS。** 必须由管理员把有授权的本地音频放入 `songs/`。真实 QQ/协议端对 mp3/wav 等格式的播放支持未验证，部分适配器可能只可靠支持 WAV；本地文件是否能被容器/协议端读取也取决于部署路径。
16. **点歌只做名字关键词匹配，不解析标签/歌手/歌词。** 文件缺失、空文件、超限、符号链接和路径穿越会被跳过或提示，不会从网络下载歌曲。
17. **`/option` 不能可靠枚举其他插件的具体命令。** AstrBot v4.29 的 `Context.get_all_stars()` 能返回插件元数据，但没有稳定的公开 API 把 handler 返回为用户命令；因此 `/option` 只列其他插件的名称/版本/描述/激活状态，不读取内部 handler registry、不猜测命令，也不展示任何插件配置值或 API Key。
18. **纯 `Comp.Face` 不能收藏。** QQ Face 段只有表情 id，没有可保存的图片文件；可以触发自然短句反应，但 `/收藏表情` 只能保存带 `Comp.Image` 的消息，并会明确提示。
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

当前增强插件测试为 **72 tests**，覆盖：@/AtAll 判定、私聊必回、概率与冷却、@ 绕过冷却、`memory_mode=direct/astrbot` 回归、非回复分支正确设置 `call_llm=True` 阻断默认 LLM、yield 先于 stop_event、纯图片/表情过滤、指令跳过、原生视觉调用、视觉失败后的 caption 回退、AstrBot 全局 caption provider、无视觉兜底、base64 图片引用、关键词/情绪表情包、冷却、防重复、图片缺失降级、图片搜索关键词/URL 编码、Bing HTML 解析、百度 JSON 解析、非 HTTP(S)/内网 URL 过滤、下载 magic/Content-Type/大小校验、下载失败重试下一个候选、远程 URL 兜底、第二搜索源、搜索结果数、搜索冷却、自然语言意图与关键词提取（大树/千早爱音）、@ 群聊和私聊自然语言发送、数量词覆盖结果数、非 @ 不触发、普通聊天不误伤、自然语言搜索冷却、本地点歌成功/关键词/冷却/空目录/超大文件/路径穿越且不调用 LLM、`/option` 本插件命令/配置入口/已加载插件元数据/输出截断/别名注册/不暴露 Key/registry 不可用降级、@/私聊 Comp.Image/Face 自动短句反应/附加本地表情/非 @ 不触发、收藏分类/去重/非法分类/超大小/数量上限/无图片/纯 Face/分类列表/列表不泄露路径/发表情/收藏表情参与正常回复/冷启动 index 加载/旧内置 8 张贴纸兼容。单测本身**不访问真实网络**。

非单测在线冒烟（不代表 AstrBot/QQ 端到端）：

- Bing Images HTML 对 `千早爱音` 返回候选，`plugin._search_image_candidates("千早爱音")` 得到 6 个候选；
- 实际下载首个候选成功，内容为 51,430 字节 JPEG，magic 为 `ffd8ff...`；其余候选部分失败后自动跳过；
- 百度 JSON 第二源对 `千早爱音` 返回约 39 KB JSON，解析出 3 个百度图片代理 URL；下载首个 URL 成功，实际内容为 WebP（14,484 字节，`RIFF...WEBP`），通过 magic 校验；
- `/找图 千早爱音` 命令真实网络冒烟：经同一搜索/下载管线得到 1 个本地 JPEG（51,430 字节），回复链为文字 + `Comp.Image.fromFileSystem` 本地图片，事件 `call_llm=True` 且 `stop_event()`；
- 私聊自然语言路径 `帮我搜两张千早爱音图片` 真实网络冒烟：下载到 2 个本地 JPEG 临时文件（51,430 / 20,410 字节），回复链包含 2 个本地 `Image` 组件，未进入默认 LLM；
- 下载失败原因实测：部分 `cdn.mfuns.net` 候选即使带 Referer/UA 仍返回 HTTP 403 Forbidden，插件按设计跳过并尝试下一候选；`picx.zhimg.com` / `pic1.zhimg.com` 候选成功通过 Content-Type + JPEG magic 校验；
- 以上临时文件已在冒烟后删除，未发送到真实 QQ。

第三方页面/接口结构和网络策略随时可能变化，因此上述在线结果不构成长期保证。

本次还执行了全仓回归：`python3 -m unittest discover -s tests -q`，当前 **212 tests OK**。全仓测试数量会随其他模块开发变化；以实际运行结果为准。
