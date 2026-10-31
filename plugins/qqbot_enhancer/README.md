# qqbot_enhancer

AstrBot v4.29 自动加载插件，提供：

- QQ 群聊中 @ 机器人必回（`AtAll` / `qq="all"` 不算），私聊必回；
- 非 @ 群消息按概率回复，并带每群/每用户冷却、最小长度与纯图片/纯表情过滤；
- `memory_mode` 可在“省 Token 直连模式”和“AstrBot 默认 Agent 长期记忆模式”之间切换；
- 图片消息优先走当前会话 provider 的原生视觉调用，失败后回退到可配置图片转述 provider；
- `/找图 关键词`（别名 `/搜图`）默认只用纯标准库 + 免 API Key 的 Bing Images HTML 搜索，并做中/日/英文标题描述相关性过滤；下载校验图片后发本地文件，失败保留远程 URL 兜底。百度旧第二源默认关闭，只有显式开启才可选；
- @/私聊支持保守自然语言找图，如“你去找几张大树的图”“帮我搜两张千早爱音图片”；非 @ 群消息不触发，命中后复用同一搜索下载流程并阻断默认 LLM；
- `/唱歌`、`/点歌 [关键词]` 从 `songs/` 读取用户自己的授权音频并用 `Comp.Record` 发送，不调用 LLM，不烧 token；
- 可选群聊 ambient：默认关闭；启用后缓存全群消息并按低频概率/关键词/轮次参与，回复优先用官方 `Comp.At` @ 发言人，失败降级 `@昵称`，默认有限冷却防刷屏；
- `/会话列表`、`/会话 名称`、`/新会话 名称 [提示词标题]`、`/删除会话 名称|短ID`（仅群聊）通过 AstrBot 官方 ConversationManager 操作命名会话：每个 conversation 保留自己的 persona/提示词/记忆；新会话可用模板并立即切换，已有会话只切换不新建；每群最多 `conversation_max_per_group=10`，达到上限新建前自动清理最老非当前，删除失败中止；API 缺失时明确降级不伪造成功；
- `/food 火锅`、`/food 南关区 火锅`（别名 `/吃什么`、`/饭店`）通过固定 HTTPS host `food.dlsz.top` 公开只读 API 查询最多 5 家餐厅，格式化店名/地点/标签/均分/口味/性价比/评价数/最新日期，带超时、大小、冷却和中文降级，不调用 LLM；
- `group_personas` 实例级群人格映射：direct 模式按群 ID 注入不同 system prompt，私聊/未知群 fallback default；astrbot memory 模式不再每轮强制覆盖已有 conversation，只在 `/新会话` 没有提示词模板时作为显式 fallback。
- Studio 提示词 profiles/active/pinned：提示词是模板库，pinned 是 `/新会话` 未指定模板时的默认；active 变化不会自动新建或切换会话。direct 仍使用 active prompt；astrbot 已有 conversation 保留自己的 persona/提示词/记忆。
- 提示词容量统一：最多 100 个 profile，每个 prompt 最多 12000 字符，`prompt_profiles` JSON 最多 2600000 字符；9×12000 合法，100×12000 在总上限内。Studio prompt/plugin config 请求体对该场景使用受控 8 MiB 上限，其它 API 仍使用更小的默认请求体限制。
- direct 模式群聊身份上下文：prompt 包含群号、发送者昵称/QQ 和最近消息 `[昵称(QQ)]` 前缀，避免多人混为同一人；QQ/昵称仅用于本次 LLM 理解，回复中自动脱敏；astrbot memory 模式无稳定逐消息 hook 时安全降级。
- `/option`（别名 `/插件用法`、`/用法`）显示本插件命令、当前模式与配置入口，并用 AstrBot 公开的 `Context.get_all_stars()` 列出已加载插件元数据；不暴露 Key，不枚举不稳定的内部 handler。
- 从 `stickers/` 读取本地图片，按关键词、情绪或随机方式附加到回复，带冷却和防重复；
- @/私聊收到纯 `Comp.Image`/`Comp.Face` 时优先用自然短句反应，可按概率附加内置或收藏表情；
- AI 可结合语境决定是否发表情：direct 模式通过系统提示中的私有 `[[sticker:标签]]` 标记选择内置情绪/收藏分类；astrbot memory 模式用本地关键词/情绪/分类匹配 + 概率。不增加 LLM tool、电脑控制、沙箱或终端权限。
- 可选 AI 自动识别收藏任意群成员纯图片：默认关闭；开启后优先使用当前会话 DeepSeek Flash 视觉 provider，失败回退本地小图/方图启发式，严格限频，不调用电脑控制/终端。
- 受限安全工具意图路由：模型只能输出白名单 marker，调用 search_images / send_sticker / collect_sticker / list_sticker_categories；禁止任意文件路径、URL 下载、shell/命令、电脑控制或任意插件函数。
- 显式 @/私聊自然语言发表情：如“给我发个嘲讽表情包”“来一个开心的表情”“发张猫猫表情包”“发个表情”，直接选安全内置/收藏表情，不调用 LLM；非 @ 群聊不触发。
- 显式 @/私聊自然语言重分类最近收藏：如“刚刚的表情包是嘲讽的意思”，按本地情绪映射或当前会话 provider 受控分类，把最近收藏安全移动到新分类；无缓存时明确“没有找到”，不会谎称没保存。
- `/收藏表情 [分类]`、`/表情分类`、`/表情列表 [分类]`、`/发表情 [分类]` 管理用户收藏表情，保存到 AstrBot 插件数据目录；`/表情状态`（别名 `/表情诊断`）只读说明保存失败原因；收藏表情也参与正常回复。
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

## 餐厅查询 `/food`

```text
/food 火锅
/food 南关区 火锅
/food 火锅 南关区
```

- 固定 `https://food.dlsz.top`，只读公开接口：
  - `/api/locations/search?q=...`
  - `/api/restaurants?q=...&location_id=...`（无地点直接 q）
- 使用标准库 HTTPS、超时、响应大小限制、固定 host 重定向校验；用户参数只做 `urlencode`，拒绝 URL/路径/控制字符，不拼 shell、不调用 LLM。
- 回复最多 5 家（`food_max_results`，硬上限 5），格式化店名、地点、标签、均分/口味/性价比、评价数、最新评价日期，附 `https://food.dlsz.top`。
- 无结果/地点无匹配/超时/接口非 JSON/网络错误均中文降级；每次用户会话冷却 `food_cooldown_seconds`；`should_call_llm(True)` + yield + stop_event，不额外调用 LLM。
- 相关配置：`food_enabled`、`food_timeout_seconds`、`food_max_response_bytes`、`food_max_results`、`food_cooldown_seconds`、关键词/地点长度限制及回复模板。
- 真实 QQ 展示和站点数据长期稳定性未验证；API Key、用户 ID 不写日志，不输出原始 JSON/路径。

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

### 收藏目录权限（容器/宿主机可读）

收藏目录在 `StarTools.get_data_dir(plugin_name="qqbot_enhancer")/collected_stickers`。插件创建目录使用 **0755**、收藏图片和 `index.json` 使用 **0644**，保存后显式 `chmod`，避免容器 root/umask 生成 0600/0700 导致宿主机管理服务 `Permission denied`。启动时会做一次 best-effort 迁移：`os.walk(followlinks=False)`，只迁移 `collected_stickers` 子树内的普通目录/普通文件，跳过符号链接和非普通文件，越界路径绝不 chmod；失败只记录日志，不删除收藏、不崩溃。

不要使用 `0777`。若 AstrBot 容器与宿主机管理服务是不同用户，且自动迁移仍因父目录权限失败，可在宿主机项目根目录以有权限的管理员执行（路径按实际 AstrBot data 根调整）：

```bash
DATA_DIR="data/astrbot/plugin_data/qqbot_enhancer"
chmod 755 "$DATA_DIR" "$DATA_DIR/collected_stickers"
find -P "$DATA_DIR/collected_stickers" -type d -exec chmod 755 {} +
find -P "$DATA_DIR/collected_stickers" -type f -exec chmod 644 {} +
```

这些命令不会删除收藏内容；`find -P` 不跟随符号链接。真实 AstrBot 容器与宿主机用户权限的 E2E 未验证。

## 自然语言发表情（显式 @/私聊）

`sticker_natural_language_enabled=true` 时，插件会在显式 @ 机器人或私聊文本中识别明确请求，例如：

- `给我发个嘲讽表情包`、`来一个嘲讽/阴阳/无语/鄙视/挖苦表情`：映射到安全 emotion，优先 `angry`，缺失时 `cool`；
- `来一个开心的表情`：`happy`；难过/伤心→`sad`，生气→`angry`，思考→`thinking`，喜欢→`love`，困→`sleepy`，惊讶→`surprised`，酷→`cool`；
- `发张猫猫表情包`：先精确匹配收藏分类名；
- `发个表情`：从内置+收藏池随机一个。

命中后直接发送 1 张本地表情，**不调用 LLM**；失败、分类/情绪不存在、冷却中、池为空只回中文纯文本，不猜路径、不泄漏文件名。非 @ 群聊不触发；slash 命令优先，AI sticker marker 和 safe_tools marker 优先于自然语言启发式。一次最多一个附件，冷却/防重复沿用 sticker 配置。相关配置：`sticker_natural_language_enabled`、`sticker_natural_language_send_prefix`、`sticker_natural_language_not_found_reply`、`sticker_natural_language_empty_reply`、`sticker_natural_language_cooldown_reply`。真实 QQ 展示未验证。

## 自然语言重分类最近收藏

内存按 `unified_msg_origin` 记录最近一次成功收藏的 `{sha256, category, filename, saved_at}`，不记录用户 ID/文字。显式 @/私聊下支持：

- `刚刚的表情包是嘲讽的意思`
- `刚才那个表情包分类为猫猫`
- `这个表情是阴阳怪气`
- `把上一个表情改成无语分类`
- `刚刚那个是开心的`

处理顺序：

1. 保守本地句式解析；嘲讽/阴阳/无语/鄙视/挖苦 → 现有 emotion `angry`（缺失时 `cool`，本版本默认 `angry`），开心/难过/生气/思考/喜欢/困/惊讶/酷 → happy/sad/angry/thinking/love/sleepy/surprised/cool；安全短分类名可直接作为新分类。
2. 本地无法确定且 `sticker_natural_language_reclassify_ai_enabled=true` 时，调用当前会话 provider（DeepSeek Flash 可）做受控 JSON 分类，只发送分类意图文本，不传用户 ID/路径，不注册 tool、不调用电脑控制/终端；置信度低于 `sticker_natural_language_reclassify_min_confidence` 视为失败。
3. 优先定位本会话最近一次收藏；若无但当前消息/引用/最近缓存有图，则直接收藏到目标分类。移动使用 `CollectedStickerStore.reclassify` 原子移动 + 原子 index；失败回滚旧文件/index，不删除收藏；目录 0755、文件 0644，分类/文件名/符号链接/路径安全校验沿用。
4. 回复：移动成功“已把刚才那个表情移到分类 X”；语义标注成功“已记住：这个表情用于表达…（分类：X）”；已在目标分类“已经是…”；无最近收藏/缓存“没有找到最近…”；分类失败“分类失败，旧收藏和分类未改动”；分类意图不完整提示可用 `/收藏表情 分类`，不会谎称没保存。
5. 成功标注会在 index 写入 `tags`/`meaning`/`tone`/`updated_at`；旧 index 缺字段自动补空值。之后发送/搜索收藏表情会匹配 category/tags/meaning，safe_tools `send_sticker` 也支持 `tag`。

普通聊天“我觉得这个表情很嘲讽”不会触发；非 @ 群聊不触发；命令和 marker 文本优先。配置：`sticker_natural_language_reclassify_enabled`、`..._ai_enabled`、`..._min_confidence` 及 6 个回复模板。真实 AstrBot provider/QQ E2E 未验证。

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
- `send_sticker(category="猫猫")` / `send_sticker(emotion="happy")` / `send_sticker(tag="嘲讽")`：三选一，只从内置/收藏表情池选择；不接受文件路径。
- `collect_sticker(category="猫猫")`：只使用当前消息/引用/本会话最近缓存图片；不接受任意文件路径。
- `list_sticker_categories()`：只返回安全分类名和数量，不返回路径。

示例模型输出：

```text
好呀 [[tool:send_sticker emotion="happy"]]
```

插件会删除 marker，校验工具名/参数/权限/冷却后执行；失败、非法参数、无权限或多 marker 时只回退普通文本，不泄漏内部路径/Key，不调用 `eval/exec/subprocess`，不执行 shell/终端/电脑控制，也不接受模型提供的任意 URL 下载。每会话搜索/收藏冷却及收藏大小/数量限制沿用现有逻辑。配置前缀 `safe_tools_*`；真实 AstrBot/QQ E2E 未验证。

## 全局收藏池与一个表情多分类

- 收藏池全局共享：所有群聊/私聊共用同一 `collected_stickers/index.json` 和文件池；只有最近收藏/最近媒体按会话内存定位。
- 每个条目的 `category` 保留为主分类/旧兼容字段，新增 `categories` 安全分类数组；旧 index 无 `categories` 时自动视为 `[category]`，不改文件内容。
- 一个文件只有一份：主分类决定物理目录，追加其它分类只更新 index，不复制文件；移除主分类时选择下一分类为主分类并原子移动文件，index 失败回滚。
- `/收藏表情 嘲讽,无语`、`/收藏表情 猫猫，可爱` 支持多分类，第一个为主分类；分隔符由 `collected_sticker_category_separators` 配置。
- `/表情分类` 按归属计数（多分类重复计归属）并同时显示唯一图片总数；`/表情列表 分类`、`/发表情 分类` 按 categories 包含匹配，跨会话全局可见。
- 自然语言“加入分类/同时放到多个分类”追加；“移出/只放到/只保留”在明确语义下移除/替换；普通聊天不误改。
- `update_categories/add_categories/remove_categories` 原子 index 更新，目录 0755/文件 0644，路径/符号链接/分类/文件名安全校验；不删除用户数据。
- tags/meaning/tone 与 categories 同条目保存，选择可按分类/tags/meaning/情绪匹配。真实 QQ/AstrBot 多分类展示未验证。

## 统一 AI 工具/表情调用协议

- `ai_unified_tool_protocol_enabled=true` 时，direct 模式只注入一套安全 `[[tool:...]]` 说明；`send_sticker(category|emotion|tag)` 是 AI 主动发表情的唯一推荐路径。
- 旧 `[[sticker:...]]` 仍解析兼容，但同一回复不会同时执行两套；若同时出现 safe_tools 与旧 sticker marker，优先按 safe_tools 路由，失败只清理 marker 回退文字。
- 一次事件最多一条文字 + 一个图片/表情附件；memory 模式只解析 Agent 已输出的工具 marker，或按本地语义匹配追加一张，不再随机补第二张；已有图片不再追加。
- `sticker_random_fallback_enabled` 默认 false：无 marker 时默认不随机补发（旧行为仅在显式兼容开关/旧非统一模式路径下可用）。
- safe_tools 白名单、权限、冷却、防重复、搜索/收藏限制不变；日志只记录工具名和成功/失败，不输出参数值、路径或 Key。
- 旧配置 `sticker_ai_marker_enabled`、`sticker_ai_select_enabled`、`sticker_ai_select_probability`、safe_tools 配置继续兼容。

## 按群人格 `group_personas`

实例级配置 `group_personas` 是一个 JSON 字符串（由 AstrBot/Studio 的 token 保护插件配置 API 管理）：

```json
{
  "default": {"name": "默认", "prompt": "你是默认人格"},
  "groups": {
    "123456789": {"name": "群A", "prompt": "你是A群专属人格"},
    "987654321": "你是B群专属人格"
  }
}
```

- `memory_mode=direct`：插件根据 `event.unified_msg_origin` 对应的群 ID 选择 prompt；私聊和未知群使用 `default`；无 `default` 时回退旧 `system_prompt`。
- `memory_mode=astrbot`：不再用 `on_waiting_llm_request` 每轮把 group persona 覆盖到已有会话；一个 conversation 保留自己的 persona/提示词/记忆。只有 `/新会话` 才用官方 `ConversationManager.new_conversation(title=..., persona_id=...)` 创建并立即切换，`/会话` 只切换已有。群 persona 仅作为无提示词模板时的显式 fallback；官方 manager API 不完整时安全拒绝创建/绑定，不假装按群隔离。
- 安全限制：群 ID 仅数字 1–20 位；默认/群 prompt 长度、数量、名称长度由 `group_persona_*` 限制；非法 JSON/非法群 ID/超长 prompt 被忽略并回退旧配置；不记录 prompt 到日志。
- 旧的 `system_prompt` 配置继续有效；`group_personas={}` 时不改变任何行为。direct 模式不携带跨群会话历史，避免记忆污染。
- 配置 API（已有 Studio/AstrBot token 保护，不新增无鉴权接口）：
  - `GET /api/instances/<instance>/plugins/qqbot_enhancer/config`
  - `POST /api/instances/<instance>/plugins/qqbot_enhancer/config`，body `{"values":{"group_personas":"{...}"},"restart":false}`
  - 请求头 `X-QB-Token: <管理服务 token>`；不要使用用户输入/cookie 作为 token。
- 真实 AstrBot 多群人格/ConversationManager E2E 未验证；Agent 模式只能在宿主 AstrBot 支持时另行实现，当前明确降级。

## 群聊身份上下文

`group_identity_context_enabled=true` 时，direct 模式 `_build_prompt` 在本次 LLM 请求中增加：

```text
场景：群聊
群号：123456789
发送者昵称：小明
发送者QQ：20001
身份说明：昵称和QQ号仅用于区分当前会话中的群成员，禁止在回复中输出...
最近用户消息（每行格式 [昵称(QQ)] 内容）：
- [小红(20002)] 你好
当前用户消息：[小明(20001)] 在吗
```

- 最近消息历史按 `unified_msg_origin` 仅保存在内存，限制 `group_identity_history_limit`、TTL、单条消息长度；不写盘。
- 群号、昵称、QQ 仅用于本次 LLM 上下文；不输出到日志；模型回复中的当前/历史昵称、QQ、群号会按 `group_identity_redact_in_reply` 做脱敏。
- 无昵称显示 `未知`/`未知(QQ)`；私聊显示 `群号：无（私聊）`；旧配置 `group_identity_context_enabled=false` 时保持旧 prompt 结构。
- `memory_mode=astrbot`：通过 AstrBot 官方 `on_llm_request(event, req)` 只向**当前轮** `req.system_prompt` 追加群号/昵称/QQ 和“不要输出身份”的短说明；不修改 event 文本、conversation history、provider/model/key。历史消息仍没有逐消息身份前缀，这是当前轮安全增强，不是彻底修复。
- 不新增 LLM tool/电脑控制；QQ 号不能作为工具参数或文件路径。真实 AstrBot/QQ 多群身份 E2E 未验证。

## 群聊会话切换（会话级提示词/人格/记忆）

仅群聊命令，调用 AstrBot v4.29.3 官方 `ConversationManager`，不直接改数据库：

```text
/会话列表
/会话 复盘
/切换会话 复盘
/新会话 复盘
/新会话 复盘 猫猫提示词
/删除会话 复盘
/删除会话 a1b2c3d4
```

规则：
- 一个 conversation = 一个 persona + 一份提示词 + 一段记忆；`/会话 名称` 只切换到已有会话，**不再自动新建**，保留目标自己的 persona/提示词/记忆。
- 每群/UMO 最多 `conversation_max_per_group`（默认 10，1-50）。达到上限时 `/新会话` 在创建前用官方 `delete_conversation(umo,cid)` 删除最老的非 current 会话，再创建；删除失败或没有安全候选则中止，不超出上限、不删 current。
- `/删除会话 名称|短ID`：精确匹配；同名多会话拒绝并要求短 ID；删除当前会话时先切换到剩余最新会话并确认，随后删除；拒绝删除最后一个会话；异常不删错群、不伪造成功。
- `/会话列表` 显示“当前 x / 上限 y”，接近/达到上限时提示自动清理或先删除。
- `/新会话 名称 [提示词标题]`：
  - 不写提示词标题时使用 pinned 模板；没有 pinned 时回退 active/首个模板；
  - 指定标题时按模板名称/id 精确选择；
  - 创建时传 `title` + `persona_id`，立即 `switch_conversation`，不额外创建隐藏会话；
  - 模板不存在的命令不会创建任何会话。
- `/会话列表` 显示标题、提示词名称、安全短 id 和当前标记；同一 prompt persona id 始终映射同一提示词名称。
- active/pinned 配置变化不会自动新建/切换 conversation；`on_waiting_llm_request` 不再每轮覆盖 group persona。
- 群人格只用于 direct 模式或 `/新会话` 未找到提示词模板时的显式 fallback。
- 同名多会话拒绝自动取第一个；API 缺失/异常时明确失败，不伪称已切换。
- 真实 AstrBot ConversationManager/PersonaManager E2E 未验证。

## 表情保存诊断

`/表情状态` / `/表情诊断` 只读输出：收藏开关、存储目录权限、已收藏数量/上限、单图大小上限、当前消息是可保存图片/无图片/QQ 表情/超大/无法解码、最近媒体缓存有无。不会显示绝对路径、QQ 或用户文字；保存写入失败会明确提示存储不可用，不抛到用户侧通用异常。

## 提示词模板热更新

Studio 更新提示词模板后不必重启 AstrBot：

- 插件每次解析模板或执行 `/新会话 名称 [提示词标题]` 时，从固定只读路径 `data/config/qqbot_enhancer_config.json` 检查最新 `prompt_profiles` / `prompt_profile_active_id`。
- 路径由 `StarTools.get_data_dir(plugin_name="qqbot_enhancer")` 推导，只读该固定文件，不接受命令/用户输入路径。
- 缓存键为 `(mtime_ns, size)`：文件未变化直接复用内存结果；改名、替换或 Studio 原子写入后立即重读。坏 JSON/缺文件/不可读/符号链接/超过大小上限会安全回退 AstrBot 配置对象，不崩溃、不读取任意路径。
- 找不到用户输入的新模板时，诊断会列出当前实际可用模板名称/id，不泄露文件路径或 Key。
- pinned/active、profile 容量、persona 绑定、旧模板名/id 和已有 conversation 仍兼容。

## AstrBot memory 表情/工具 catalog

`memory_mode=astrbot` 时 Agent 自己构建 system prompt，插件无法依赖 direct 的系统提示。为避免模型误说“没有表情素材”：

- safe_tools 开启、marker 开启、`send_sticker` 允许且当前有可用内置/收藏素材时，官方 `on_llm_request` 注入短 `# Sticker Tool Catalog`。
- catalog 说明有本地内置/收藏表情、可调用一次统一安全协议：
  `[[tool:send_sticker emotion="happy"]]` / `category="..."` / `tag="..."`，一次最多一个附件；不暴露路径/QQ/群号，最多列安全情绪/分类/标签。
- 注入幂等：替换旧 marker，不堆叠；不修改 conversation history/provider/model/key，不额外调用 LLM。
- safe_tools 关闭、send_sticker 被禁用或没有可用素材时静默不注入，`/表情状态` 明确显示注入状态/素材数量。
- direct 模式继续走既有 `_effective_system_prompt`，不重复注入 catalog。
- memory Agent 返回的 `[[tool:send_sticker ...]]` 由 `on_decorating_result` 解析并替换为一条文字+一张本地表情；非法/禁用/无匹配 marker 回退纯文本。

真实 AstrBot Agent hook、tool marker 输出和 QQ 表情展示 E2E 未验证。

## 代码架构（2.1.0）

`main.py` 仍是唯一的 AstrBot 入口/组合根，保留 `Star`、`filter` 装饰器、事件编排和依赖装配；可独立单测的业务能力已拆到包内模块：

- `interfaces.py`：`StickerRepository`、`MediaDecoder`、`ImageSearchProvider`、`SongRepository`、`LLMService` 等 Protocol 和 `MediaPayload`/`StickerRecord`/`ImageCandidate` 数据对象。
- `config.py`：193 个默认配置集中定义 + `ConfigReader` 类型读取/旧 config 兼容。
- `media.py`：图片 magic/尺寸、base64、媒体引用和公网 HTTP(S) URL 安全过滤。
- `image_search.py`：中/日/英文相关性规范化、别名/阈值过滤。
- `songs.py`：本地授权音频的安全路径解析/列表仓库。
- `sticker_store.py`：收藏 index 原子写/旧 index 兼容/分类目录与文件校验。
- `llm.py`：direct sticker marker 提取、AI 判定 JSON 解析、标签指令生成。
- `sticker_intent.py`：显式自然语言表情请求解析、情绪/收藏分类安全解析。
- `sticker_reclassify.py`：最近收藏重分类句式/情绪映射、AI JSON 解析。
- `safe_tools.py`：白名单工具 marker 解析、参数 schema/路径/URL/命令拒绝、指令文本生成。
- `__init__.py`：让插件目录可作为 Python package 部署，`main.py` 优先相对导入；测试 harness 也支持同目录模块 fallback。

未变：所有配置键、schema 193 项、命令、收藏 index 格式、8 张内置 stickers 和现有行为；没有引入第三方依赖。`main.py` 因 AstrBot 装饰器/事件处理仍需保留，未把命令 handler 全部搬出。真实 AstrBot 实例部署与 QQ 端到端显示未验证。

## 依赖

无额外 pip 依赖；只使用 Python 标准库与 AstrBot v4.29 自带的 `astrbot.api`、LLM/provider 与消息组件 API。插件内不包含任何第三方 API Key。示例表情包均为项目自绘 PNG。
