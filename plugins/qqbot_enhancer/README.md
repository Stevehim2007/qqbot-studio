# qqbot_enhancer

AstrBot v4.29 自动加载插件，提供：

- QQ 群聊中 @ 机器人必回（`AtAll` / `qq="all"` 不算），私聊必回；
- 非 @ 群消息按概率回复，并带每群/每用户冷却、最小长度与纯图片/纯表情过滤；
- `memory_mode` 可在“省 Token 直连模式”和“AstrBot 默认 Agent 长期记忆模式”之间切换；
- 图片消息优先走当前会话 provider 的原生视觉调用，失败后回退到可配置图片转述 provider；
- `/找图 关键词`（别名 `/搜图`）默认只用纯标准库 + 免 API Key 的 Bing Images HTML 搜索，并做中/日/英文标题描述相关性过滤；下载校验图片后发本地文件，失败保留远程 URL 兜底。百度旧第二源默认关闭，只有显式开启才可选；
- @/私聊支持保守自然语言找图，如“你去找几张大树的图”“帮我搜两张千早爱音图片”；非 @ 群消息不触发，命中后复用同一搜索下载流程并阻断默认 LLM；
- `/唱歌`、`/点歌 [关键词]` 从 `songs/` 读取用户自己的授权音频并用 `Comp.Record` 发送，不调用 LLM，不烧 token；
- `/option`（别名 `/插件用法`、`/用法`）显示本插件命令、当前模式与配置入口，并用 AstrBot 公开的 `Context.get_all_stars()` 列出已加载插件元数据；不暴露 Key，不枚举不稳定的内部 handler。
- 从 `stickers/` 读取本地图片，按关键词、情绪或随机方式附加到回复，带冷却和防重复；
- @/私聊收到纯 `Comp.Image`/`Comp.Face` 时优先用自然短句反应，可按概率附加内置或收藏表情；
- AI 可结合语境决定是否发表情：direct 模式通过系统提示中的私有 `[[sticker:标签]]` 标记选择内置情绪/收藏分类；astrbot memory 模式用本地关键词/情绪/分类匹配 + 概率。不增加 LLM tool、电脑控制、沙箱或终端权限。
- 可选 AI 自动识别收藏任意群成员纯图片：默认关闭；开启后优先使用当前会话 DeepSeek Flash 视觉 provider，失败回退本地小图/方图启发式，严格限频，不调用电脑控制/终端。
- 受限安全工具意图路由：模型只能输出白名单 marker，调用 search_images / send_sticker / collect_sticker / list_sticker_categories；禁止任意文件路径、URL 下载、shell/命令、电脑控制或任意插件函数。
- `/收藏表情 [分类]`、`/表情分类`、`/表情列表 [分类]`、`/发表情 [分类]` 管理用户收藏表情，保存到 AstrBot 插件数据目录；收藏表情也参与正常回复。
- `/收藏表情` 优先保存引用消息里的图片；没有引用时使用本会话内存中最近 3 张图片缓存（TTL 300 秒，不落盘、不记录用户文字/ID）。自动收藏默认关闭。

## 文件

```text
qqbot_enhancer/
├── main.py
├── metadata.yaml          # AstrBot 要求的插件元数据
├── _conf_schema.json      # WebUI 配置 Schema
├── README.md
├── songs/                  # 用户自行放置授权音频；仓库不附带版权歌曲
│   └── README.md
└── stickers/
    ├── sticker_rules.json
    ├── happy.png       # 8 张自绘占位 PNG
    ├── sad.png
    ├── angry.png
    ├── thinking.png
    ├── love.png
    ├── sleepy.png
    ├── surprised.png
    └── cool.png
```

## 安装

把整个目录复制或软链接到 AstrBot 的 `data/plugins/qqbot_enhancer`，然后在 AstrBot WebUI 插件页重载/重启 AstrBot：

```bash
cp -a plugins/qqbot_enhancer /path/to/AstrBot/data/plugins/
# 或者开发时：
ln -s "$PWD/plugins/qqbot_enhancer" /path/to/AstrBot/data/plugins/qqbot_enhancer
```

AstrBot 会扫描 `data/plugins/` 下的目录，读取 `metadata.yaml` 和 `main.py`，自动识别继承 `astrbot.api.star.Star` 的插件类。无需 `@register`（该装饰器在 v4.29 源码中已废弃）。首次加载后会生成插件配置：

```text
data/config/qqbot_enhancer_config.json
```

更完整的安装、配置、限制说明见仓库根目录 `docs/plugin-qqbot-enhancer.md`。

## 图片搜索

```text
/找图 橘猫
/搜图 风景 山
```

@ 机器人或私聊时还可自然语言触发（非 @ 群消息不触发）：

```text
你去找几张大树的图
帮我搜两张千早爱音图片
```

自然语言识别只接受带有明确找/搜/发/来等动作、图片名词和可提取关键词的请求；`我找不到图片了`、`这张图真好看` 等普通聊天不会误触发。命中后与命令共用搜索、下载、冷却和本地文件发送流程，并阻断默认 LLM。

默认只用 Bing Images HTML（`https://www.bing.com/images/search`），不自动调用百度。插件优先解析 Bing 结果标题/描述，对中文、日文变体（如 `千早 愛音`）、英文别名（如 `Anon Chihaya`）做 NFKC/大小写/常见异体字规范化后计算相关性。低于 `image_search_min_relevance_score`（默认 0.6）或没有标题/描述元数据的候选会被丢弃；没有相关候选时宁可回复“没找到”，不发送牛头不对马嘴的图片。

百度图片 JSON 旧第二源保留为可选代码，但 `image_search_second_source_enabled` **默认 false**，默认绝不请求百度，也不会自动回退。只有管理员显式开启后才可能作为可选第二源。插件不递归抓站，搜索和候选图片下载均带 Referer/User-Agent、超时和大小限制。

相关性是词面/标题元数据过滤，不是视觉内容理解；不额外调用视觉 LLM。真实 QQ 端图片展示未验证。

下载到临时文件时必须同时通过：

- 公网 HTTP(S) URL 和最终重定向 URL 校验；
- `Content-Type` 图片类型检查（允许 octet-stream + magic 兜底）；
- JPEG/PNG/GIF/WebP/BMP/AVIF magic 检查；
- `image_search_download_max_bytes` 大小上限、单图下载超时和重定向次数限制。

成功使用官方 `Comp.Image.fromFileSystem(...)` 发送本地文件，并登记到 AstrBot 事件临时文件清理列表；如果全部下载失败，才回退到 `Comp.Image.fromURL(...)` 远程 URL。可在 WebUI 配置搜索超时、下载超时/大小/尝试次数、发送图片数（1–5）、安全搜索级别、响应大小上限、群/用户冷却和各条提示文案。

Bing/Baidu 页面/JSON 结构变化、网络限制或反爬都可能导致无结果；这不是官方 API 级保证。插件不访问真实网络做单测，真实搜索仅作为非自动化冒烟检查。

## 本地点歌 / 唱歌

把有授权的本地音频放入 `songs/` 后：

```text
/唱歌
/唱歌 文件名关键词
/点歌
/点歌 文件名关键词
```

插件只扫描 `songs/` 直属文件，支持 `.mp3`、`.wav`、`.ogg`、`.flac`、`.m4a`、`.aac`，拒绝路径穿越、符号链接和超过 `song_max_file_bytes` 的文件。发送使用 AstrBot 官方 `Comp.Record.fromFileSystem(...)`；**不调用 LLM、不调用 TTS/音乐生成接口，因此不消耗 token**。仓库不含任何版权歌曲。能否在 QQ/协议端播放取决于适配器对音频格式的支持，建议优先 WAV。

## 插件用法 `/option`

```text
/option
/插件用法
/用法
```

`/option` 不调用 LLM，输出中文纯文本，包含：

- 本插件版本和 `/找图`、`/唱歌`、`/option` 等命令；
- 当前 `memory_mode`、@/私聊必回、非 @ 概率与冷却；
- 配置入口：AstrBot WebUI → 插件 → QQ Bot 增强回复 → 配置；
- 通过 AstrBot v4.29 公开 API `Context.get_all_stars()` 读取的已加载插件名称/版本/描述，受最大数量与字符数限制。

AstrBot v4.29 没有稳定的公开 API 把 handler 映射回用户命令，因此 `/option` **不会伪称枚举其他插件命令**，也不会访问内部 `star_handlers_registry`。输出不读取/不展示插件配置值或 API Key。

可配置 `option_include_other_plugins`、`option_max_plugins`、`option_max_chars` 控制插件列表和输出长度。

## 用户表情反应与收藏

### 自动反应

@ 机器人或私聊发来不带文字的纯图片/表情包，或纯 QQ `Comp.Face` 时，插件按 `user_sticker_reaction_probability` 用配置的短句自然回应，不调用 LLM。`user_sticker_reaction_attach_sticker=true` 时还可能附加一个本地表情，仍受 `sticker_probability` / `sticker_cooldown_seconds` 以及收藏表情使用概率控制。非 @ 群消息不进入该自动反应分支。

### 收藏与管理

```text
/收藏表情 [分类]
/表情分类
/表情列表 [分类]
/发表情 [分类]
```

- 发送图片/表情包时同时使用 `/收藏表情 开心` 即可收藏；不带分类默认 `未分类`。
- 也支持先发图片/表情，再引用该消息后 @ 机器人发 `/收藏表情 开心`，或随后单独 @ 机器人发 `/收藏表情 开心`；后者会从该会话最近 3 张图片缓存中取最新一张。
- 缓存只存在于内存，默认 TTL 300 秒，每会话最多 3 张；不保存用户文字、用户 ID 或绝对路径。
- `auto_collect_user_sticker_enabled` **默认 false**。即使显式开启，也只会对明显的小图/方图做本地启发式自动收藏，受大小/尺寸/冷却/总数量限制，不会无限采集普通大图。
- 使用 AstrBot 公开 `StarTools.get_data_dir(...)`，实际落盘：

  ```text
  data/plugin_data/qqbot_enhancer/collected_stickers/<分类>/<sha256>.<ext>
  data/plugin_data/qqbot_enhancer/collected_stickers/index.json
  ```

- 分类只允许中文、字母、数字、`-`、`_`；拒绝路径穿越、绝对路径和符号链接。
- 图片校验 magic、大小上限；文件与 `index.json` 均原子写入；SHA-256 全局去重。
- `index.json` 仅保存 sha256、文件名、分类、大小、时间、来源适配器名，不保存用户文字/用户 ID。
- `/表情列表` 只显示分类和文件名，不输出绝对路径。
- `collected_sticker_enabled`、`collected_sticker_max_count`、`collected_sticker_max_file_bytes`、`collected_sticker_use_probability` 等可在 WebUI 配置。
- 旧内置 8 张 stickers 保持兼容，和收藏表情一起参与正常回复。

**限制：** `Comp.Face` 只有 QQ 表情 id，没有可保存的图片字节，因此纯 `Comp.Face` 可以触发短句反应，但 `/收藏表情` 会提示改用图片/贴纸形式。没有实现破坏性删除命令；需要清理时由管理员直接管理插件数据目录。

## AI 语境发表情

- `sticker_ai_select_enabled=true`：启用语境选表情。
- direct 模式：插件只把内置情绪标签和收藏分类名注入系统提示，不暴露路径/文件内容；模型合适时在回复末尾返回一个 `[[sticker:happy]]` / `[[sticker:猫猫]]`。插件删除标记并附一个安全匹配的本地表情；marker 模式开启但没有返回标记时，模型的选择即“不发表情”，不会额外随机补一张。
- astrbot memory 模式：默认 Agent 回复不做提示注入，插件在 `on_decorating_result` 用本地关键词/情绪/收藏分类匹配 + `sticker_ai_select_probability` 概率追加一个表情。
- 一次最多一个；非法标记、未知标签、缺失文件、冷却中都会只保留纯文本，标记不会显示。
- 不注册 LLM tool，不启用 AstrBot 电脑控制/沙箱/终端权限，也不允许模型直接读取本地文件。
- `sticker_ai_marker_enabled` 控制 direct 模式标记注入；`sticker_ai_select_probability` 默认 0.6。direct marker 路径使用 `sticker_ai_select_probability` + 冷却/防重复；旧 `sticker_probability` 随机路径在 AI 选择关闭、marker 关闭、用户表情反应和 memory 本地路径中继续生效。

## AI 自动识别收藏（默认关闭）

`sticker_ai_auto_collect_enabled` **默认 false**，默认不会对任何普通群图片自动调用视觉模型或收藏。显式开启后：

- 对任意群成员的**无文字纯 Comp.Image**（纯 Comp.Face 无图片字节，不处理）：
  1. 本地校验 magic、文件大小、图片宽高；大图/未知尺寸直接跳过；
  2. 按 `sticker_ai_auto_collect_probability` 触发；
  3. 优先调用视觉 provider：`sticker_ai_auto_collect_provider_id` 留空时使用当前会话 provider（部署 DeepSeek Flash 1017 视觉模型时默认即可）；
  4. 让模型只返回 JSON：是否表情包、建议分类、置信度；低于 `sticker_ai_auto_collect_min_confidence` 或 provider 失败时回退本地小图/方图启发式；
  5. 通过后使用既有 SHA-256/分类安全/原子写/总数上限保存；成功只回一条短句“这个表情我收下了（分类）”。
- `sticker_ai_auto_collect_max_per_hour` 每会话每小时限制尝试次数，并复用 `auto_collect_sticker_cooldown_seconds`、`auto_collect_sticker_max_file_bytes`、`auto_collect_sticker_max_dimension`、`collected_sticker_max_count`。
- 不保存用户文字、用户 ID、绝对路径；不注册 LLM tool，不启用电脑控制/沙箱/终端，模型只看到当前图片与固定分类提示。
- 开启后可能产生视觉 provider token 消耗；真实 AstrBot + DeepSeek/Qwen/QQ 视觉端到端未验证。

## 受限安全工具意图路由

不依赖 AstrBot v4.29 的 tool/function-calling API（未把它当作稳定公开依赖），而是使用插件内私有 marker/intent router。模型只能输出白名单工具标记，插件解析、校验后调用自己已有的受限能力：

- `search_images(keyword="...", count=1)`：仅复用 Bing 搜索与下载安全管线；参数不能是路径/URL/命令；单条回复最多 1 张。
- `send_sticker(category="猫猫")` 或 `send_sticker(emotion="happy")`：只从内置/收藏表情池选择；不接受文件路径。
- `collect_sticker(category="猫猫")`：只使用当前消息/引用/本会话最近缓存图片；不接受任意文件路径。
- `list_sticker_categories()`：只返回安全分类名和数量，不返回路径。

示例模型输出：

```text
好呀 [[tool:send_sticker emotion="happy"]]
```

插件会删除 marker，校验工具名/参数/权限/冷却后执行；失败、非法参数、无权限或多 marker 时只回退普通文本，不泄漏内部路径/Key，不调用 `eval/exec/subprocess`，不执行 shell/终端/电脑控制，也不接受模型提供的任意 URL 下载。每会话搜索/收藏冷却及收藏大小/数量限制沿用现有逻辑。配置前缀 `safe_tools_*`；真实 AstrBot/QQ E2E 未验证。

## 代码架构（1.7.0）

`main.py` 仍是唯一的 AstrBot 入口/组合根，保留 `Star`、`filter` 装饰器、事件编排和依赖装配；可独立单测的业务能力已拆到包内模块：

- `interfaces.py`：`StickerRepository`、`MediaDecoder`、`ImageSearchProvider`、`SongRepository`、`LLMService` 等 Protocol 和 `MediaPayload`/`StickerRecord`/`ImageCandidate` 数据对象。
- `config.py`：120 个默认配置集中定义 + `ConfigReader` 类型读取/旧 config 兼容。
- `media.py`：图片 magic/尺寸、base64、媒体引用和公网 HTTP(S) URL 安全过滤。
- `image_search.py`：中/日/英文相关性规范化、别名/阈值过滤。
- `songs.py`：本地授权音频的安全路径解析/列表仓库。
- `sticker_store.py`：收藏 index 原子写/旧 index 兼容/分类目录与文件校验。
- `llm.py`：direct sticker marker 提取、AI 判定 JSON 解析、标签指令生成。
- `safe_tools.py`：白名单工具 marker 解析、参数 schema/路径/URL/命令拒绝、指令文本生成。
- `__init__.py`：让插件目录可作为 Python package 部署，`main.py` 优先相对导入；测试 harness 也支持同目录模块 fallback。

未变：所有配置键、schema 120 项、命令、收藏 index 格式、8 张内置 stickers 和现有行为；没有引入第三方依赖。`main.py` 因 AstrBot 装饰器/事件处理仍需保留，未把命令 handler 全部搬出。真实 AstrBot 实例部署与 QQ 端到端显示未验证。

## 依赖

无额外 pip 依赖；只使用 Python 标准库与 AstrBot v4.29 自带的 `astrbot.api`、LLM/provider 与消息组件 API。插件内不包含任何第三方 API Key。示例表情包均为项目自绘 PNG。
