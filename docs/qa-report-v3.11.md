# QQ Bot Studio v3.11.0 独立 QA 报告

> 审查对象：`3957d9a3537a`（tag `v3.11.0`，Linux 主仓库）。
> 审查方式：只读代码/文档/测试，不修改业务代码、不登录真实 QQ、不调用真实 DeepSeek。
> 审查日期：2026-10-31。
> 结论：**条件通过（Conditional Pass）**。自动化测试、静态检查、schema 检查通过；
> 发现 **0 个 P0**、多个需要后续修复的 P1/P2 功能/契约风险。真实 AstrBot/QQ/
> DeepSeek/Windows E2E 仍未验证，不能把本报告当成生产可用承诺。

## 1. 测试与静态检查

### 1.1 全量测试

```bash
python3 -m unittest discover -s tests -v
```

结果：**383 tests OK，Ran 383 tests in 27.940s**。

相关专项：

```bash
python3 -m unittest tests.test_enhancer_plugin -v
# 180 tests OK，0.631s
python3 -m unittest tests.test_prompt_profiles -v
# 5 tests OK，0.030s
```

全量测试中的 AstrBot/ConversationManager/PersonaManager 均是测试 fake；没有真实
AstrBot 实例、Docker 容器、QQ 协议端或 DeepSeek 计费请求。

### 1.2 静态/格式检查

```bash
for f in qqbot_studio/static/*.js qqbot_studio/static/services/*.js \
         qqbot_studio/static/components/*.js; do node --check "$f"; done
python3 -m py_compile qqbot_studio/*.py plugins/qqbot_enhancer/*.py tests/*.py
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
python3 -m json.tool plugins/qqbot_enhancer/stickers/sticker_rules.json >/dev/null
bash -n start.sh console.sh install.sh scripts/*.sh
python3 -m qqbot_studio --version
```

结果：全部通过；版本为 `qqbot-studio 3.11.0`。静态 HTML/JS 检查显示三个页面的 ID
唯一，未发现外部 CDN、`innerHTML` 或 localStorage/sessionStorage 使用。

## 2. 审查范围与功能地图

| 功能 | 主要路径 | 关键行号 |
|---|---|---|
| Studio prompt profiles/active | `qqbot_studio/deployer.py` | `2452-2775` |
| prompt HTTP API/token | `qqbot_studio/server.py` | `298-308`, `540-567`, `893-920` |
| prompt UI 二级 modal | `qqbot_studio/static/console.js` | `1935-2344`, `3068-3104` |
| prompt UI DOM/CSS | `qqbot_studio/static/console.html` | `249-291` |
| plugin prompt profile parse/resolve | `plugins/qqbot_enhancer/personas.py` | `170-250` |
| plugin direct/astrbot prompt priority | `plugins/qqbot_enhancer/main.py` | `3933-4125` |
| persona/conversation bridge | `plugins/qqbot_enhancer/persona_bridge.py` | `25-169` |
| `/会话列表` `/会话` `/新会话` | `plugins/qqbot_enhancer/main.py` | `1040-1322` |
| conversation parser | `plugins/qqbot_enhancer/conversations.py` | `1-59` |
| ambient cache/门控/@ 发言人 | `plugins/qqbot_enhancer/main.py` | `2240-2415`, `5053-5088` |
| ambient/memory hooks | `plugins/qqbot_enhancer/main.py` | `539-554`, `1686-1712`, `4050-4105` |
| schema/defaults | `plugins/qqbot_enhancer/config.py`, `_conf_schema.json` | `126-145`, `526-744` |

## 3. P0：立即阻断项

**未发现 P0。** 本轮没有发现会直接导致未授权访问、跨群 credential 泄漏、任意文件/命令
执行、明文密钥输出或默认开启高危网络能力的 v3.11 新问题。

保留的既有安全边界：

- `/api/instances/<id>/prompts` 与其他 `/api/*` 服务端一样要求 `X-QB-Token`；
  无 token 的 GET/POST 返回 403；Host 校验、静态文件路径限制和 2 MiB 请求体上限不变。
- prompt/active 落盘仍通过 `Store.save_config` / 私有 JSON 原子写；不新增无鉴权接口，
  不返回 API Key。
- ambient 默认 `ambient_group_chat_enabled=false`；显式 @/私聊必回路径仍优先于 ambient。
- 本报告只提出修复建议，没有直接改业务代码。

## 4. P1：需要后续修复的功能/契约风险

### P1-1 超过 100 个旧 profile 的迁移会失败或静默丢 profile

**路径/行号：** `qqbot_studio/deployer.py:2562-2569`、`2610-2639`。

`_normalise_prompt_profiles()` 先按全部原始 profiles 计算 `active_id`，然后才执行
`profiles = profiles[:100]`：

- 若 active 是第 101 个 profile：active id 指向已被截断掉的项；随后
  `get_prompt_profiles()` 的 `next(item ... if id == active_id)` 抛 `StopIteration`，
  接口返回 `ok:false`，用户无法通过 UI 修复；
- 若 active 在前 100 项：接口 `ok:true`，但第 101 项及之后被静默丢弃，随后
  `store.save_config()` 把缩短后的列表写回 config，属于用户提示词数据丢失。

**临时 mock 复现（无真实网络）：**

```python
# 101 profiles，active 指向 p100
manager.get_prompt_profiles()
# 观察：ok=False，prompts=[]，错误信息无法定位到 active 越界

# 101 profiles，active=p0
manager.get_prompt_profiles()
# 观察：ok=True，返回 100 个；store 中的 prompt_profiles 也从 101 变 100
```

**风险：** 旧实例/手工修复过的配置在打开提示词面板时可能丢失最后一个 profile；active
在尾部时则形成无法自助修复的失败状态。正常通过 v3.11 API 创建时会在 100 个处拒绝，
但迁移/配置导入没有同等保护。

**建议（不在本任务修改）：** 在截断前先确定保留集合和 active；若 active 将被淘汰，
应明确拒绝迁移并返回可操作错误，不能静默删除；并增加 100/101 profile 边界测试。

### P1-2 迁移路径没有校验 prompt 最大长度，插件会丢弃该 profile

**路径/行号：** `qqbot_studio/deployer.py:2506`（迁移读取 raw prompt）、
`:2617-2625`、`plugins/qqbot_enhancer/personas.py:217-229`。

Studio 的 create/update 使用 `_prompt_profile_prompt()` 限制 12,000 字符
（`deployer.py:2460-2465`），但 `_normalise_prompt_profiles()` 对旧/外部 profile
只做 `str(raw.get("prompt"))`，没有长度上限。临时配置一个 13,000 字符 profile：

- Studio GET 返回 `ok:true`，并把 13,000 字符保存回 config/plugin；
- 插件 `parse_prompt_profiles()` 的 `_safe_text(max_chars=12000)` 返回 `None`，
  该 profile 被丢弃；若存在 group default，插件会选 group default，而不是 active profile；
- 于是 Studio 显示 active profile，实际 LLM 使用的是另一个 fallback prompt。

**风险：** prompt/active 显示与实际人格不一致，且没有 warning。

**建议：** 归一化迁移时应用与 create/update 相同的 12,000 字符校验；超限项应明确
拒绝或截断并返回 warning，另加 12,000/12,001 边界测试。

### P1-3 `GET /prompts` 不会重试同步已过期的插件状态

**路径/行号：** `qqbot_studio/deployer.py:2610-2635`，同步函数
`:2571-2608`。

`get_prompt_profiles()` 只在 `changed=True` 时调用 `_sync_prompt_profiles_to_plugin()`。
如果之前一次本地保存成功、但 AstrBot 在线写入失败（或实例重启后的 plugin config 仍是旧值），
Studio config 已经 normalized，下一次 GET 的 `changed` 为 False，不会重试同步。

**临时 mock 复现：**

1. 首次 `manager.get_prompt_profiles()` 生成本地/插件状态；
2. 手工把 enhancer config 中 `prompt_profiles` 改为 `"[]"`、
   `prompt_profile_active_id` 改为空、`system_prompt` 改为 `STALE LEGACY`；
3. 再次 `manager.get_prompt_profiles()`：返回 `ok:true`，
   但插件配置仍是 `prompt_profile_active_id=""`、`system_prompt="STALE LEGACY"`。

**风险：** 控制台显示正常列表，下一轮消息实际使用旧人格/旧 prompt。

**建议：** GET 也做一次性同步（或显式返回 `plugin_sync_required`/warning），
并增加“本地 state 已规范化但插件 state 不是最新”的回归测试。

### P1-4 Studio 保存的首尾空白与插件实际使用的 prompt 不一致

**路径/行号：** `plugins/qqbot_enhancer/personas.py:30-38`（`_safe_text` 调用
`.strip()`）、`:217-229`；API 契约在 `docs/ui-api-contract-v2.md:1373` 声称
prompt 保留首尾换行/空白。

临时解析一个 `prompt="第一行\n\n第四行  \n"` 的 profile：

```text
Studio/API 保存值: "第一行\n\n第四行  \n"
插件实际使用值:   "第一行\n\n第四行"
```

内部换行保留，但尾部空格/换行被剥离。对 strict prompt 模板、固定结尾格式或
token 对齐可能造成可观察差异；至少与当前 API 契约不一致。

**建议：** 插件的 profile 解析只校验长度和非法控制字符，不改变 prompt 原文；
或把契约改成“保留内部换行，首尾空白会规范化”。

### P1-5 AstrBot profile 切换只对 `profile` source 新建会话，group/default 变更会复用旧历史

**路径/行号：** `plugins/qqbot_enhancer/main.py:4014-4028`、
`:4030-4048`；`plugins/qqbot_enhancer/persona_bridge.py:102-146`。

`new_conversation_on_persona_change` 只在目标 source 为 `profile` 时传 True。
如果把 active profile 改成 group 精确人格，或修改同一群的 group_personas prompt：

- 目标 persona id 改变（或同一 group persona prompt 被 update）；
- `bind_conversation()` 走 `update_conversation(..., persona_id=...)`，
  不调用 `new_conversation()`；
- 旧 conversation/history 仍保留，可能在新人格下继续被读取。

临时 mock 结果：group persona A -> group persona B，当前 conversation id 仍相同，
`new_conversation` 计数不变。真实 AstrBot 历史结构未在本环境验证，但代码路径确定
没有隔离旧历史。

**建议：** 任意 persona id/prompt 变化都按产品策略决定是否新建会话；若“改群人格保留
历史”是刻意行为，应在 UI/契约中明确，不应只对 profile 做保护。

### P1-6 AstrBot memory 模式的 ambient 没有把插件缓存送入 Agent 请求

**路径/行号：** `plugins/qqbot_enhancer/main.py:539-554`（ambient 在 astrbot
模式只 set extra 后 return）、`:1686-1712`、`:4078-4105`、`:2417-2495`。

direct 模式会把 `_chat_identity_history()` 作为 `chat_history` 传入 `_build_prompt()`；
astrbot memory 模式的 `on_llm_request` 只注入“当前发送者身份”（`inject_agent_current_sender_identity`），
没有注入插件缓存的最近全群消息。ambient 在 memory 模式下依赖 AstrBot 自己的会话历史，
而插件缓存的 `ambient_group_cache_limit/TTL` 不会变成 Agent 上下文。

**风险：** 文档宣称 ambient 缓存全群消息，但 memory 模式可能只能看到 AstrBot 已持久化的
wake 历史；低概率参与时的“读全群”语义没有兑现。

**建议：** 要么把受限的最近缓存通过 `on_llm_request` 注入（必须限条数/长度/不泄漏 ID），
要么明确 ambient 全群上下文仅适用于 direct 模式；补 fake hook 回归测试。

### P1-7 ambient 轮次门控在 `round_interval > cache_limit` 时永远不会触发

**路径/行号：** `plugins/qqbot_enhancer/main.py:2391-2395`、
`:2327-2336`、`:2240-2243`。

实现用缓存列表长度判断轮次：

```python
if round_interval > 0 and len(history) % round_interval != 0:
    return False
```

但 `history` 已被 `ambient_group_cache_limit`（与 `group_identity_history_limit` 取较大值）
截断。临时参数 `group_identity_history_limit=0, ambient_group_cache_limit=5,
ambient_group_round_interval=10`，连续发送 15 条消息：缓存始终为 5，
`llm_calls=0`，即永远不参与。

**风险：** UI 允许把轮次设为 1–100、缓存设为 1–100；常见组合（轮次大于缓存）会静默失效。

**建议：** 使用独立单调消息计数器/每群轮次状态，而不是缓存长度；或校验
`round_interval <= cache_limit` 并明确提示。

### P1-8 ambient 绕过 `min_message_chars` 和 `ignore_pure_image`/`ignore_pure_face`

**路径/行号：** `plugins/qqbot_enhancer/main.py:2369-2415` 与普通群聊过滤
`:1952-1968`。

`_ambient_group_should_participate()` 只要求 `message_text.strip()` 或 `has_image`，
没有调用 `_group_random_eligible()`，因此：

- `ignore_pure_image=true` 时，非 @ 纯图片仍可被 ambient 选中；
- `ignore_pure_face=true` 时，纯表情仍可被选中；
- `min_message_chars=3` 时，只有 2 个字符的消息仍可被选中。

临时 mock 用 `ambient_group_reply_probability=1.0`、`ignore_pure_image=true`：

```text
纯图片消息 -> 1 次 LLM 调用
短文本 hi  -> 1 次 LLM 调用
```

**风险：** ambient 与旧非 @ 概率路径的过滤行为不一致，可能增加误参与和 token 成本。

**建议：** 在 ambient 概率前复用现有 `_group_random_eligible()`，并明确是否确实要对
纯图片/纯表情突破旧过滤；增加对应测试。

## 5. P2：局部 UI/文档/可维护性风险

1. **删除提示词没有二次确认。** `console.js:2116-2120` 的删除按钮直接调用
   `mutatePrompt('delete')`，而实例删除使用两次点击确认。提示词内容可能较长且删除后
   无撤销；建议复用实例删除的 armed-confirm 交互，或在 P1 中优先处理。
2. **后端上限与 UI 输入上限不一致。** HTML 名称 `maxlength=200`（`console.html:274`），
   后端最多 80 字符（`deployer.py:2453-2456`）；textarea 没有 12,000 字符客户端限制。
   用户可输入后在提交时才看到错误；建议 HTML/JS 与后端上限对齐并做即时提示。
3. **busy guard 无超时/取消入口。** `setPromptManagerBusy(true)` 会禁用关闭、表单控件
   （`console.js:2022-2034`），而 fetch 没有前端超时；服务端卡顿时用户可能被锁在 modal。
   建议保留“取消/关闭”或设置请求超时；同时给 modal 增加 `aria-busy`。
4. **prompt manager 作为二级 modal 时，父实例设置 modal 仍保持打开。**
   `openPromptManager`（`console.js:2176-2206`）只打开第二层 overlay；
   `instanceSettings` 的 `isBlocked` 已包含 promptManager，但父 modal 的 aria/焦点管理
   没有显式暂停。当前静态测试覆盖 DOM/ID，没有浏览器焦点/Escape 顺序 E2E。
5. **`conversation_list_limit` 只有下限保护。** `main.py:1194-1197` 使用
   `max(1, cfg_int(...))`，没有上限；schema slider 上限 50，但手工配置可返回更长列表。
   建议服务端也 clamp 到 50。
6. **同名会话已有重复时只取第一个。** `conversations.py:54-58` 精确 casefold 取首个；
   `/新会话` 会拒绝未来重复，但数据库已有的重复标题没有歧义提示。建议列表显示短 id，
   切换结果提示命中的 id，或在多个同名时拒绝并列出候选。
7. **文档与实现有陈旧/冲突描述。**
   - `plugins/qqbot_enhancer/README.md:306` 仍写 astrbot 模式“不修改全局人格/
     只会安全降级”，但 v3.11 实际会为 profile/群人格创建专用 Persona 并绑定 conversation；
     应更新为当前行为（专用 persona ≠ 全局人格）。
   - `docs/qa-report-v2.md:26` 记录 `383 tests OK / 26.257s`，本环境本轮实测为
     `383 tests OK / 27.940s`（前一次为 28.336s）；计数一致，但耗时会随机器变化，
     建议写成“本次运行”并避免把固定秒数当契约。
   - `docs/ui-api-contract-v2.md:1373` 与插件首尾空白处理不一致（见 P1-4）。
8. **API 容错覆盖仍以 happy-path 为主。** 有 `items/profiles`、`profile_id` 等前端
   字段容错测试，但缺以下 mock 回归：
   - 101 profile 边界；
   - 12,001 字符 profile migration；
   - Studio config normalized 但 plugin config 过期时 GET 重同步；
   - group persona source 变更时是否复用 conversation；
   - `round_interval > cache_limit`；
   - `ignore_pure_image/min_message_chars` 与 ambient 交互；
   - 真实 ConversationManager/PersonaManager 的 API 签名与返回值。
9. **真实 E2E 未验证（保持条件通过）。** 未执行：
   - AstrBot v4.29.3 真实 `ConversationManager.get_conversations/switch_conversation/
     new_conversation/update_conversation` 和 `PersonaManager` API；
   - QQ 群聊显式 @、会话切换后的历史/人格隔离、ambient 的官方 At 显示；
   - DeepSeek 实际 token 消耗、图片/工具/表情链；
   - Docker/Ubuntu 真实部署、noVNC 扫码；
   - 浏览器中的二级 modal、键盘焦点/Escape、30 分钟刷新与 busy guard；
   - Windows 独立仓库（本任务只读审计，不修改 release）。

## 6. 未发现/通过的关键检查

- prompt profiles 的正常迁移、内部换行、唯一 pinned、删除 active、删除最后一个保护
  均有单元测试并通过。
- prompt GET/POST 使用 token；无 token 403；返回 JSON 字段包含 `prompts`/`active_id`/
  `conversation_created`/`conversation_pending`/`restart_required`。
- direct 模式 active profile 优先于 group default/legacy；group 精确映射优先于 active；
  测试覆盖通过。
- `/会话列表`、`/会话`、`/新会话` 的正常路径、同名拒绝、自动创建、私聊提示、
  API 缺失/异常、跨群 UMO 隔离测试通过。
- ambient 默认关闭、概率参与、显式 @ 必回、官方 At 与昵称降级、冷却/TTL/跨群隔离
  happy-path 测试通过。
- 未发现新增无鉴权 API、任意文件/命令能力或 API Key 回显。

## 7. 结论与建议

**结论：v3.11.0 提示词 profiles/active、按名称会话、ambient 主路径实现完整且自动化
回归通过，但仍有多个 P1 级边界/契约风险，不能直接视为生产完成；应保持条件通过。**
建议按以下顺序处理后再做真实 E2E：

1. 修复 P1-1/P1-2/P1-3/P1-4 的 prompt 数据一致性边界；
2. 修复 P1-5 的 persona 历史隔离；
3. 修复 P1-6/P1-7/P1-8 的 ambient 语义/门控；
4. 补浏览器二级 modal 与 AstrBot/QQ/DeepSeek E2E。

本报告只记录审查结果，未修改业务代码；所有测试均未访问真实 QQ 或 DeepSeek。
