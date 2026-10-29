# qqbot_enhancer

AstrBot v4.29 自动加载插件，提供：

- QQ 群聊中 @ 机器人必回（`AtAll` / `qq="all"` 不算），私聊必回；
- 非 @ 群消息按概率回复，并带每群/每用户冷却、最小长度与纯图片/纯表情过滤；
- `memory_mode` 可在“省 Token 直连模式”和“AstrBot 默认 Agent 长期记忆模式”之间切换；
- 图片消息优先走当前会话 provider 的原生视觉调用，失败后回退到可配置图片转述 provider；
- `/找图 关键词`（别名 `/搜图`）使用纯标准库 + 免 API Key 的 Bing Images HTML 搜索并发送网络图片，带超时、安全搜索、结果数、群/用户冷却和失败降级；
- 从 `stickers/` 读取本地图片，按关键词、情绪或随机方式附加到回复，带冷却和防重复。

## 文件

```text
qqbot_enhancer/
├── main.py
├── metadata.yaml          # AstrBot 要求的插件元数据
├── _conf_schema.json      # WebUI 配置 Schema
├── README.md
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

搜索固定使用 Bing Images HTML 页面（`https://www.bing.com/images/search`），只发起一次搜索结果页 GET，不下载原图、不递归爬站、不需要 API Key。插件解析页面中的真实图片直链，并只保留公网 HTTP(S)、图片扩展名的 URL，再通过 `Comp.Image.fromURL(...)` 发送。无结果、超时、HTTP 失败或没有安全直链时返回配置的中文降级提示。

可在 WebUI 配置搜索超时、发送图片数（1-5）、安全搜索级别、响应大小上限、群/用户冷却和各条提示文案。Bing HTML 结构变化、网络限制或搜索源反爬都可能导致无结果；这不是 API 级保证。

## 依赖

无额外 pip 依赖；只使用 Python 标准库与 AstrBot v4.29 自带的 `astrbot.api`、LLM/provider 与消息组件 API。插件内不包含任何第三方 API Key。示例表情包均为项目自绘 PNG。
