# qqbot_enhancer

AstrBot v4.29 自动加载插件，提供：

- QQ 群聊中 @ 机器人必回（`AtAll` / `qq="all"` 不算），私聊必回；
- 非 @ 群消息按概率回复，并带每群/每用户冷却、最小长度与纯图片/纯表情过滤；
- `memory_mode` 可在“省 Token 直连模式”和“AstrBot 默认 Agent 长期记忆模式”之间切换；
- 图片消息优先走当前会话 provider 的原生视觉调用，失败后回退到可配置图片转述 provider；
- `/找图 关键词`（别名 `/搜图`）使用纯标准库 + 免 API Key 的 Bing Images HTML / 百度图片 JSON 搜索，下载并校验图片后用本地文件发送，失败保留远程 URL 兜底；带超时、安全搜索、结果数、群/用户冷却和失败降级；
- @/私聊支持保守自然语言找图，如“你去找几张大树的图”“帮我搜两张千早爱音图片”；非 @ 群消息不触发，命中后复用同一搜索下载流程并阻断默认 LLM；
- `/唱歌`、`/点歌 [关键词]` 从 `songs/` 读取用户自己的授权音频并用 `Comp.Record` 发送，不调用 LLM，不烧 token；
- `/option`（别名 `/插件用法`、`/用法`）显示本插件命令、当前模式与配置入口，并用 AstrBot 公开的 `Context.get_all_stars()` 列出已加载插件元数据；不暴露 Key，不枚举不稳定的内部 handler。
- 从 `stickers/` 读取本地图片，按关键词、情绪或随机方式附加到回复，带冷却和防重复。

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

默认搜索 Bing Images HTML（`https://www.bing.com/images/search`）；当 Bing 结果不足时，使用免 API Key 的百度图片 JSON 第二搜索源。插件不递归抓站，只请求搜索结果页/JSON，然后对候选图片分别发起带 Referer、User-Agent 的下载请求。

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

## 依赖

无额外 pip 依赖；只使用 Python 标准库与 AstrBot v4.29 自带的 `astrbot.api`、LLM/provider 与消息组件 API。插件内不包含任何第三方 API Key。示例表情包均为项目自绘 PNG。
