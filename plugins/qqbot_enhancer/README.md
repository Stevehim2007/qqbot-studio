# qqbot_enhancer

AstrBot v4.29 自动加载插件，提供：

- QQ 群聊中 @ 机器人必回（`AtAll` / `qq="all"` 不算），私聊必回；
- 非 @ 群消息按概率回复，并带每群/每用户冷却、最小长度与纯图片/纯表情过滤；
- 图片消息优先走当前会话 provider 的原生视觉调用，失败后回退到可配置图片转述 provider；
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
    ├── happy.png
    ├── sad.png
    ├── angry.png
    └── thinking.png
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

## 依赖

无额外 pip 依赖；只使用 Python 标准库与 AstrBot v4.29 自带的 `astrbot.api`、LLM/provider 与消息组件 API。插件内不包含任何第三方 API Key。
