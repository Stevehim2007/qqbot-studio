# QQ Bot 增强插件（qqbot_enhancer）

本文档对应 `plugins/qqbot_enhancer/`。插件目标版本是 **AstrBot v4.29**，接口核对基于 AstrBot 官方仓库标签 `v4.29.3`（v4.29 系列最新补丁）的源码与官方中文文档。插件不使用 AstrBot WebUI/数据库私有接口，不修改 `qqbot_studio` 代码，也没有硬编码任何第三方 API Key。

## 1. 功能概览

- 群里被机器人 `@` 时必回；`AtAll`（@全体成员）或 `At.qq == "all"` 不算被机器人 `@`。
- 私聊消息必回。
- 非 `@` 群消息默认按 12% 概率回复，受每群冷却、每用户冷却、最小长度、纯图片/纯表情/空消息过滤限制。
- 支持 `direct` / `astrbot` 两种 `memory_mode`：前者省 Token 直连模型，后者交给 AstrBot 默认 Agent 使用长期会话历史。
- 图片消息优先尝试当前会话 provider 的原生多模态能力；失败后回退到插件配置的视觉 provider，或 AstrBot 的全局图片转述 provider。
- `/找图 关键词`（别名 `/搜图`）通过免 API Key 的 Bing Images HTML 页面搜索网络图片并发送；带超时、安全搜索、结果数、群/用户冷却、响应大小限制和中文失败降级。
- 从插件的 `stickers/` 目录发送本地表情包；支持关键词、情绪和随机选择，并带会话冷却、防连续重复、文件缺失降级。
- 指令前缀消息不参与概率回复，避免抢占 AstrBot 指令。
- 回复成功后阻止 AstrBot 默认 LLM 再回复一次，避免重复。

## 2. 文件与自动加载

```text
plugins/qqbot_enhancer/
├── main.py                 # 插件入口，继承 astrbot.api.star.Star
├── metadata.yaml           # v4.29 插件元数据
├── _conf_schema.json       # AstrBot WebUI 配置 Schema
├── README.md
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
| `image_search_enabled` | bool | `true` | 启用 `/找图`、`/搜图` |
| `image_search_timeout_seconds` | int | `12` | Bing HTML 搜索页单次 HTTP 超时 |
| `image_search_result_count` | int | `1` | 每次命令最多发送的图片数（1–5） |
| `image_search_safe_search` | string | `strict` | Bing `adlt` 安全搜索：`strict`/`moderate`/`off` |
| `image_search_group_cooldown_seconds` | int | `15` | 图片搜索每群冷却 |
| `image_search_user_cooldown_seconds` | int | `10` | 图片搜索每用户冷却 |
| `image_search_max_response_bytes` | int | `2000000` | 搜索结果 HTML 最大字节数 |
| `image_search_max_keyword_chars` | int | `100` | 搜索关键词最大长度 |
| `image_search_success_prefix` | text | `为你找到这些图片：` | 成功发送图片前的文字 |
| `image_search_usage_reply` | text | 内置用法提示 | 缺少关键词提示 |
| `image_search_disabled_reply` | text | 内置提示 | 功能关闭提示 |
| `image_search_empty_reply` | text | 内置提示 | 无安全结果提示 |
| `image_search_error_reply` | text | 内置提示 | 网络/解析失败提示 |
| `image_search_timeout_reply` | text | 内置提示 | 超时提示 |
| `image_search_cooldown_reply` | text | `图片搜索冷却中，请约 {seconds} 秒后再试。` | 冷却提示模板 |
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

### 4.5 `/找图` 网络图片搜索

命令形式：

```text
/找图 橘猫
/搜图 风景 山
```

命令由独立的 AstrBot `@filter.command("找图", alias={"搜图"}, priority=110)` 处理器负责，优先级高于普通 ALL 监听器（100），因此不会被概率回复逻辑吞掉。命令本身完成后 `should_call_llm(True)` + `stop_event()`，不会让默认 LLM 再解释一遍。`alias` 参数和 `priority` 均在 v4.29 `CommandFilter`/handler 注册器源码中；发送使用 `Comp.Image.fromURL`：

- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/star/filter/command.py>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/docs/zh/dev/star/guides/listen-message-event.md>
- <https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/message/components.py>

搜索实现约束：

- 固定请求 Bing Images HTML 页面：`https://www.bing.com/images/search`，使用 `urllib`，不需要 API Key；不提供任意 URL 配置，避免把搜索请求引向内部地址。
- 只发起**一次**搜索结果页 GET，不下载原图、不递归抓取站外页面。`/找图` 不是一个通用爬虫。
- 请求在线程中执行（`asyncio.to_thread`），不会阻塞 AstrBot 事件循环；搜索超时可配置。
- 响应最多读取 `image_search_max_response_bytes + 1` 字节，一旦超过配置上限立即失败；还检查 `Content-Length`、`Content-Type` 和最终重定向 URL。
- 解析 Bing `a.iusc[m]` 中的 JSON `murl`，并对缺少该包装的响应做有限的 `"murl":"..."` / 图片扩展名 URL 兜底解析。每页最多检查 200 个候选。
- 只接受公网 `http://` / `https://` URL；拒绝 `file://`、`javascript:`、带 userinfo 的 URL、localhost/`.local`/`.internal` 等内部域名，以及私有/回环/链路本地/保留 IP。图片路径必须有图片扩展名（`.jpg/.jpeg/.png/.gif/.webp/.bmp/.avif`）。
- 发送使用官方 `Comp.Image.fromURL(url)`；默认发送 1 张，可配置到 5 张。结果页中重复 URL 会去重。
- 群冷却和用户冷却在网络请求前占用，防止并发刷屏；命令缺少关键词、关闭搜索、无结果、超时、网络错误均有独立中文提示配置。

**限制：** Bing Images 是 HTML 页面而不是稳定 API；页面结构变化、地区/语言差异、反爬验证、网络不可达时都可能解析不到结果。插件不会下载图片校验真实内容，因此一个结构合法但已失效的图片 URL 可能交给 QQ 协议端后在平台侧加载失败；插件无法收到该平台侧加载结果。为减少风险，解析结果必须来自 Bing 页面、使用 HTTP(S)，并带图片扩展名。

### 4.6 图片搜索的可选 LLM tool

v4.29 确实提供 `@filter.llm_tool(...)` 官方装饰器，但本版本**没有**注册 `search_images` LLM tool。原因是默认 Agent 的 tool 结果更适合返回文本/结构化数据，而本需求的可靠交付方式是“命令直接发送图片”；在 tool 内发送图片还会涉及 Agent 后续总结和潜在重复回复。当前实现明确优先保证 `/找图` 命令可用、可 mock 测试。若后续需要“astrbot 记忆模式下自然语言找图”，可基于官方 `filter.llm_tool` 单独设计 tool 的返回协议。

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
9. **Bing Images HTML 不是官方 API。** 搜索依赖 Bing 当前页面结构；Bing 改版、地区差异、反爬验证或网络限制会导致无结果/失败。真实 Bing 请求与解析未在本环境做在线联调，单测使用本地 HTML fixture。
10. **图片 URL 未由插件下载校验。** 为防止带宽/SSRF/无限抓取，本插件不读取原图，只解析并发送 HTTP(S) 图片直链；URL 结构合法但已经失效时，QQ 协议端可能加载失败，插件无法从平台侧确认。
11. **没有实现可选 LLM tool。** 当前只提供命令 `/找图`、`/搜图`。AstrBot v4.29 的 `filter.llm_tool` 是真实 API，但本版本没有注册 `search_images` tool；`memory_mode=astrbot` 时自然语言找图不会自动调用本插件命令。
12. **URL 公网过滤不做 DNS 解析。** 插件拒绝明显的私有 IP、回环、内部域名和非 HTTP(S) 地址，但公网域名仍可能解析到内网地址。当前插件只把 URL 交给 QQ 协议端，不主动 fetch 该图片 URL；若后续增加服务端下载，必须补充 DNS/IP 解析后的二次 SSRF 校验。
13. **搜索关键词会发送给 Bing。** 与所有第三方搜索一样，不应在关键词中提交密码、Token 或其他敏感信息。

## 9. 校验记录

已执行：

```bash
python3 -m py_compile plugins/qqbot_enhancer/*.py
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
python3 -m unittest tests.test_enhancer_plugin -v
```

mock 测试覆盖：@/AtAll 判定、私聊必回、概率与冷却、@ 绕过冷却、`memory_mode=direct/astrbot` 回归、非回复分支正确设置 `call_llm=True` 阻断默认 LLM、yield 先于 stop_event、纯图片/表情过滤、指令跳过、原生视觉调用、视觉失败后的 caption 回退、AstrBot 全局 caption provider、无视觉兜底、base64 图片引用、关键词/情绪表情包、冷却、防重复、图片缺失降级、图片搜索关键词/URL 编码、Bing HTML 解析、非 HTTP(S)/内网 URL 过滤、成功发送、无结果/网络失败降级、搜索结果数、响应大小上限和搜索冷却。单测本身**不访问真实网络**。

另做了一次非单测的在线冒烟检查（仅验证搜索页可访问和解析，不代表 AstrBot/QQ 联调）：

- 用带浏览器 UA 的 `urllib`/curl 请求 Bing Images HTML，返回约 245 KB；
- 从页面解析出 34 个 `m` JSON 项，插件安全过滤后成功得到图片直链；
- 实际 `plugin._search_image_urls("橘猫")` 在线返回 3 个可通过校验的图片 URL。

Bing 页面结构和网络策略随时可能变化，因此上述在线结果不构成长期保证。
