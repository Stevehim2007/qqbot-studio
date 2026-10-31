# QQ Bot Studio v3.12.0 复验 QA 报告

> 目标版本：v3.12.0（当前工作树仍显示 core `3.11.0`，见 P2-1；本报告针对本次
> task-76/77/78/80/81/82 修复快照）。
> 基线：v3.11.0 tag `3957d9a` + 当前未提交修复。
> 方式：只读业务代码，运行测试/静态检查，并用临时 fake manager/event 做边界 mock；
> **没有登录真实 QQ、没有调用真实 DeepSeek、没有启动真实 Docker/AstrBot/SnowLuma。**
> 日期：2026-10-31。
>
> **复验结论：P0=0，P1=0，原 8 项 P1 及后续 3 项 P1 全部复验通过；条件通过。**
> P2 仍有版本/文档、无浏览器和真实端到端未验证项，详见第 4、5 节。

## 1. 测试与静态检查

### 1.1 全量测试

```bash
python3 -m unittest discover -s tests -v
```

结果：**407 tests OK，Ran 407 tests in 27.720s**。

专项结果：

```bash
python3 -m unittest tests.test_prompt_profiles -v
# 17 tests OK，0.155s
python3 -m unittest tests.test_enhancer_plugin -v
# 191 tests OK，0.724s
python3 -m unittest tests.test_control_assistant_static -v
# 16 tests OK，0.244s
```

全量测试中的 AstrBot/ConversationManager/PersonaManager 仍是 mock/fake；未做真实
AstrBot 实例、QQ 协议端或 DeepSeek E2E。

### 1.2 静态/格式检查

```bash
for f in qqbot_studio/static/*.js qqbot_studio/static/services/*.js \
         qqbot_studio/static/components/*.js; do node --check "$f"; done
python3 -m py_compile qqbot_studio/*.py plugins/qqbot_enhancer/*.py tests/*.py
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
python3 -m json.tool plugins/qqbot_enhancer/stickers/sticker_rules.json >/dev/null
bash -n start.sh console.sh install.sh scripts/*.sh
git diff --check
```

结果：全部通过。当前 `python3 -m qqbot_studio --version` 输出
`qqbot-studio 3.11.0`；详见 P2-1。

## 2. 原 8 项 P1 逐项复验

| # | 原问题 | 复验结果 | 证据 |
|---|---|---|---|
| P1-1 | >100 profile 的 active 头/尾不截断、不丢失 | **通过** | 101 个 profile、active=`p000` 与 `p100` 均返回 `ok:false` + `limit_exceeded=true`；返回/保存仍为 101 项，未静默截断。 |
| P1-2 | 12000/12001 迁移/API/插件一致 | **通过** | 12000 字符保存/GET 成功；12001 返回失败，store 仍保留 12001 原文；插件 parser 与 Studio 使用同一 12000/100 上限。 |
| P1-3 | GET 修复 stale 插件状态 | **通过** | `deployed=true`、AstrBot API 不可用时，连续两次 GET 均返回 `restart_required=true`、`prompt_plugin_sync_pending=true`；`prompt_plugin_sync_pending_at` 持久化。 |
| P1-4 | prompt 首尾空白 round-trip | **通过** | `"  第一行\n第二行  \n"` 经 parser 保持首尾空白；direct 实际 system prompt 也保留该值。 |
| P1-5 | persona 变化新建 conversation / 无法保证安全拒绝 | **通过（含 task-80）** | group persona v1→v2、profile→group 均新建 conversation；`/会话 名称` 目标 persona 相同才 switch；None/不同时明确拒绝，current 不变、无隐藏新会话。 |
| P1-6 | astrbot memory ambient 全群上下文注入/降级 | **通过（含 task-80）** | 群聊 `on_llm_request` 注入同 UMO 的 `# Ambient Group Context`；私聊不注入；UMO 隔离；异常保留原 prompt 并设降级标记。 |
| P1-7 | `round_interval > cache_limit` | **通过** | 临时 mock `cache_limit=3, round_interval=5`，第 5 条消息触发；缓存仍只保留 3 条，计数独立于缓存深度。 |
| P1-8 | ambient 过滤 `min_message_chars`/`ignore_pure_image`/`ignore_pure_face` | **通过** | 纯图片、2 字符短文本、纯 Face 在默认过滤下均无回复、无 LLM 调用；使用 `_group_random_eligible()`。 |

### 2.1 补充复验（task-82 容量 P1）

容量上限已统一为：

- `PROMPT_PROFILE_MAX_COUNT=100`；
- `PROMPT_PROFILE_MAX_PROMPT_CHARS=12000`；
- `PROMPT_PROFILES_MAX_TOTAL_PROMPT_CHARS=1,200,000`；
- `PROMPT_PROFILES_MAX_JSON_CHARS=2,600,000`；
- `PROMPT_PROFILES_MAX_REQUEST_BYTES=8 MiB`；
- `_conf_schema.json` 的 `prompt_profiles.max_length=2600000`；
- 插件 `personas.py` 使用同一 JSON/总数/单条上限。

临时 mock 结果：

```text
9×12000 API 创建：成功；插件字段 JSON 109,909；插件 parser 10 个（含默认 profile）
100×12000 GET：成功；JSON 1,214,789；插件 parser 100 个，errors=0
Studio 通用 plugin schema validator：通过
100×12000 的 manager/plugin 配置同步：通过（未访问真实 AstrBot）
```

超过总 JSON 上限由 `tests/test_prompt_profiles.py` 的 over-total 测试拒绝；server 的
`/prompts` 与含 `prompt_profiles` 的插件配置 POST 使用 8 MiB 专用 body 上限，其它
普通 API 仍保持 2 MiB。**该容量 P1 已关闭。**

## 3. UI P2 复验

现状：`node --check`、静态 ID/契约测试、`tests/test_control_assistant_static.py` 16 tests
全部通过；**没有可用浏览器/jsdom，因此行为级只做代码/静态复验，不做真实焦点断言。**

| UI P2 | 代码证据 | 复验判断 |
|---|---|---|
| 删除二次确认 | `console.js:2252-2274` 的 `requestPromptDelete()`；`deleteArmedId`、5 秒确认超时、确认按钮文案 `console.js:2232-2242` | 通过代码路径检查；第二次点击才调用 delete，打开/关闭/操作后会清理确认状态。 |
| busy 超时/取消 | `PROMPT_REQUEST_TIMEOUT_MS=15000`、`withPromptTimeout()`（`console.js:1937-1962`）、`cancelPromptOperation()`（`console.js:2108-2117`）、`prompt-manager-cancel-busy`（`console.html:290`） | 通过代码/静态检查；请求超时拒绝并释放控件；取消递增 `actionSeq`，不会让迟到响应更新界面。 |
| focus trap / aria | `trapPromptModalFocus()`（`console.js:2066-2106`）、`aria-busy` 同步（`:2038-2041`、`:2362`、`:2396`）、标题 `tabindex=-1`（`console.html:254`） | 通过代码/静态检查；打开聚焦标题、Tab 循环、Esc 关闭、关闭返回触发按钮。真实浏览器焦点未验证。 |
| 80/12000 长度 | `console.html:274` 名称 `maxlength=80`、`:279` prompt `maxlength=12000`；`console.js:1937-1938`、`:2416-2432` 字段级校验 | 通过；前后端均为 80/12000。HTML `maxlength` 与 Python/JS 代码点计数对极端 emoji/代理对可能有极小差异，常规中文无影响。 |

## 4. P2 发现与剩余债务

### P2-1 版本/README 仍是 3.11.0

**路径：** `qqbot_studio/__init__.py:4`、`README.md:5`、`docs/README.md:24`。

当前工作树版本仍为 `qqbot-studio 3.11.0`，而本报告目标为 v3.12.0；插件 metadata 已为
1.21.0，core/README 未同步。若本次要正式发布 v3.12.0，必须先 bump core version、
README、`/healthz` 版本断言和 Windows sync 的 upstream 版本；这不是行为 P1，但属于
发布门禁。

### P2-2 文档对“非法旧列表”的自动迁移描述与实现不完全一致

**路径：** `docs/ui-api-contract-v2.md:1389` 仍写“不存在/空/非法旧列表会自动迁移为默认 profile”；
当前 `DeployManager._normalise_prompt_profiles()`（`qqbot_studio/deployer.py:2531-2600`）
对非 list/空 list 会迁移默认 profile，但对 list 中非法条目返回 `ok:false` 并保留原数据
（不会自动迁移）。临时 mock：`[{"id":"","prompt":"bad"}]` 返回失败，未覆盖旧配置。

这是安全的“拒绝而不丢数据”，但 UI 无自动修复入口，用户需手工修正 config；建议把文档
改成“非法条目返回可操作错误，不截断/不删除”，或给 UI 增加修复/删除非法项入口。

### P2-3 文档/测试计数陈旧

- `docs/qa-report-v2.md:26` 仍记录 383 tests / 26.257s；本轮实际为 407 tests / 27.720s。
- `docs/plugin-qqbot-enhancer.md:1068` 仍记录 403 tests；实际当前为 407 tests。
- 插件专项实际为 191 tests（文档其它处已更新）。这些不影响功能，但会让发布验收读数
  不一致，建议发布前统一到本次实测值或以 CI 输出为准。

### P2-4 ambient memory 上下文的默认最小长度边界

**路径：** `plugins/qqbot_enhancer/_conf_schema.json:747-756`、
`plugins/qqbot_enhancer/persona_bridge.py:228-288`。

`ambient_group_context_max_chars` 的 slider 最小值为 100，但注入 header 固定约 150+ 字符；
当用户配置为 100 时，`inject_ambient_group_context()` 会因无消息行可放入而返回 false，
即“启用但永远不注入”。这是配置语义的边界体验问题，不影响默认值 1500；建议提高最小值
或按 header 长度单独计算余量。

### P2-5 大量 body 的 8 MiB 专用上限路径较宽

**路径：** `qqbot_studio/server.py:793-814`。

当前只要路径以 `/prompts` 结尾或 `/plugins/.../config` 匹配，就允许 8 MiB body；实际
业务校验仍会拒绝非 prompt_profiles 的超大字段。这是 token 保护下的本地服务，不是 P0/P1，
但建议把 8 MiB 只授予包含 `prompt_profiles` 的具体 API/实例，减少不必要的内存读取面。

### P2-6 无浏览器/真实 AstrBot E2E

本环境没有 Chromium/jsdom，无法真实验证 Tab 循环、Esc 关闭、aria-busy 可访问树、
删除确认计时器和二级 modal 的屏幕阅读器行为。真实 AstrBot v4.29.3 的
`ConversationManager/PersonaManager` API 签名、conversation history/persona 隔离、QQ
官方 At 展示、DeepSeek provider 调用也仍未验证。建议在发布前补真实浏览器和 Ubuntu +
Docker + AstrBot + QQ 测试账号 E2E。

## 5. 未验证的真实 E2E

- AstrBot v4.29.3 `get_conversations/switch_conversation/new_conversation/
  update_conversation/get_conversation` 的真实返回结构与异常行为；
- AstrBot `PersonaManager` 真实创建/更新 persona，及 conversation persona 绑定后的
  history 隔离；
- QQ 群聊官方 `Comp.At`、私聊、ambient 回复的实际展示/频控；
- DeepSeek 实际视觉、文本、图片搜索和 token 消耗；
- Docker/Ubuntu 真实部署、noVNC 扫码和云安全组/防火墙；
- Windows 独立仓库（本轮只读，不同步 release）；
- 真实浏览器焦点/aria/取消请求行为。

## 6. 复验结论

- **P0：0。**
- **P1：0。** 原 8 项 P1、task-80 的命名会话/私聊 ambient 两项、task-81 的
  `restart_required` 持久化、task-82 的容量一致性均已通过代码、测试和临时 mock 复验。
- **P2：** 以上版本/文档/默认最小长度/UI 浏览器未验证等，不阻断本次代码级复验，但应在
  v3.12.0 正式发布前处理或明确接受。
- 当前报告仅新增 `docs/qa-report-v3.12.md`，未修改业务代码、测试或其它文档。
