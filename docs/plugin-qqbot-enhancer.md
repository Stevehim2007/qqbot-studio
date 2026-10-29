# QQ Bot 增强插件（qqbot_enhancer）

本文档对应 `plugins/qqbot_enhancer/`。插件目标版本是 **AstrBot v4.29**，接口核对基于 AstrBot 官方仓库标签 `v4.29.3`（v4.29 系列最新补丁）的源码与官方中文文档。插件不使用 AstrBot WebUI/数据库私有接口，不修改 `qqbot_studio` 代码，也没有硬编码任何第三方 API Key。

## 1. 功能概览

- 群里被机器人 `@` 时必回；`AtAll`（@全体成员）或 `At.qq == "all"` 不算被机器人 `@`。
- 私聊消息必回。
- 非 `@` 群消息默认按 12% 概率回复，受每群冷却、每用户冷却、最小长度、纯图片/纯表情/空消息过滤限制。
- 图片消息优先尝试当前会话 provider 的原生多模态能力；失败后回退到插件配置的视觉 provider，或 AstrBot 的全局图片转述 provider。
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
└── stickers/
    ├── sticker_rules.json
    ├── happy.png
    ├── sad.png
    ├── angry.png
    └── thinking.png
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

仓库自带 4 张 96×96 PNG 占位表情（`happy.png`、`sad.png`、`angry.png`、`thinking.png`），由本项目用标准库生成，合计约 2.4 KB，不含第三方版权素材或外部 CDN 引用。替换时请保持合法来源和相应授权。

## 8. 已知限制与未验证项

以下限制是明确的，不应被理解为已验证功能：

1. **未进行真实 QQ/AstrBot 联调。** 当前实现通过 AstrBot v4.29 源码核对 API，并用无 AstrBot 安装的 mock 单测验证行为；没有在真实 NapCat/QQ 账号、AstrBot WebUI 和真实模型端点上跑过端到端流程。
2. **显式直连 provider 不包含 AstrBot 默认 Agent 的人格、完整会话历史、工具/技能和上下文压缩。** 目前只发送插件 `system_prompt`、当前消息（以及可选图片描述），会话是单轮的。若需要完整 Agent 上下文，应改为让默认管线处理或另行使用 `event.request_llm()`/`tool_loop_agent()`，但那会改变本插件的去重与回复逻辑。
3. **图片转述不是原生视觉的等价替代。** 它依赖管理员在 AstrBot 中配置支持图片输入的 provider。未配置或配置的 provider 不支持图片时，只能返回配置的兜底文本；无法凭插件自行获得视觉能力。
4. **DeepSeek/OpenAI-compatible 的实际视觉行为以部署为准。** 插件的 modality 判断基于 provider 配置；若 provider 内部自动文本重试，插件可能无法判断模型是否真的看到了图片。
5. **本地表情发送的跨平台兼容性未验证。** 通用 `Image.fromFileSystem` 是 AstrBot 官方 API，但本地文件是否能被协议端读取取决于部署路径、回调文件服务/协议端所在机器等环境因素；失败时 AstrBot 侧会记录日志，插件本身只会降级组装纯文本。
6. **冷却/防重复状态只在内存中，重启清空，且只在单实例内有效。** 多进程/多机器人实例不会共享冷却。
7. **高优先级 handler 成功回复后会 `stop_event()`。** 这会阻止后续插件再处理同一条消息，属于有意的去重策略；若部署中还有其他必须同时响应普通消息的插件，需要评估顺序和冲突。
8. **纯图片/纯表情过滤只针对非 @ 群消息。** @ 机器人和私聊消息即使只有图片/表情也会处理，这是“必回”的设计取舍。

## 9. 校验记录

已执行：

```bash
python3 -m py_compile plugins/qqbot_enhancer/main.py
python3 -m unittest discover -s tests -p 'test_enhancer_plugin.py' -v
```

mock 测试覆盖：@/AtAll 判定、私聊必回、概率与冷却、@ 绕过冷却、非回复分支正确设置 `call_llm=True` 阻断默认 LLM、yield 先于 stop_event、纯图片/表情过滤、指令跳过、原生视觉调用、视觉失败后的 caption 回退、AstrBot 全局 caption provider、无视觉兜底、base64 图片引用、关键词/情绪表情包、冷却、防重复、图片缺失降级和回复截断。
