# QQ Bot Studio v3.12.1 最终 QA 报告

> 审查对象：`017ec94`（`main`，v3.12.1 候选；当前未打 v3.12.1 tag，按 Lead 流程在 QA 后打 tag）。
> Ponytail 第二阶段基线：`ae10104`（v3.12.0）；本轮只读复核 `1a6b61c` 及发布提交 `017ec94`。
> 审查方式：只读业务代码、运行全量/专项测试、静态检查、临时 mock 和 Windows 仓库
> sync dry-run；未登录真实 QQ、未调用真实 DeepSeek、未启动真实 Docker/AstrBot/SnowLuma。
> 日期：2026-10-31。
>
> **结论：P0=0，P1=0；Ponytail 第二阶段未发现行为/API/数据/安全/权限回归。
> 最终候选为代码级条件通过（Conditional Pass），发布前仍需真实 Windows/QQ/Docker/
> DeepSeek E2E 和文档收口。**
> 本任务没有修改业务代码、测试或其它文档，只新增本报告。

## 1. 测试与静态检查

### 1.1 全量测试

```bash
python3 -m unittest discover -s tests -v
```

结果：**408 tests OK，Ran 408 tests in 28.103s**。

专项：

```bash
python3 -m unittest tests.test_enhancer_plugin -v
# 191 tests OK，0.688s
python3 -m unittest tests.test_prompt_profiles -v
# 17 tests OK，0.151s
python3 -m unittest tests.test_control_assistant_static -v
# 16 tests OK，0.244s
python3 -m unittest tests.test_server -v
# 37 tests OK，19.240s
python3 -m unittest tests.test_frontend_components -v
# 4 tests OK，0.110s
python3 -m unittest tests.test_updater -v
# 6 tests OK，0.003s
python3 -m unittest tests.test_assistant_ext -v
# 37 tests OK，0.201s
```

### 1.2 静态/格式检查

```bash
for f in qqbot_studio/static/*.js qqbot_studio/static/services/*.js \
         qqbot_studio/static/components/*.js; do node --check "$f"; done
python3 -m py_compile qqbot_studio/*.py plugins/qqbot_enhancer/*.py tests/*.py tests/e2e/*.py
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
python3 -m json.tool plugins/qqbot_enhancer/stickers/sticker_rules.json >/dev/null
bash -n start.sh console.sh install.sh scripts/*.sh
python3 -m qqbot_studio --version
git diff --check
```

结果：全部通过；版本为 **`qqbot-studio 3.12.1`**。工作树 clean，当前候选为
`v3.12.0-2-g017ec94`，v3.12.1 tag 尚未创建（按发布流程在 QA 后创建）。

## 2. Ponytail 第二阶段改动复核

### 2.1 变更范围

`1a6b61c` 的行为承载源码/测试为 **15 个文件、+198/-393、净减 195 行**：

- 源码 9 个文件：`+125/-282`，净 **-157 行**；新增 1 个标准库 helper
  [`gitutil.py`](../qqbot_studio/gitutil.py)，新增运行依赖 **0**；
- 测试 6 个文件：`+73/-111`，净 **-38 行**；
- 另加本阶段要求的 docs/architecture 记录；没有改动 server/deployer/plugin 的 API 路由、
  数据格式、实例目录、权限、token/Host/SSRF/Docker argv 或插件功能正文。

逐文件核对的证据：

| 改动 | 复核 |
|---|---|
| `static/services/api.js` 导出 `asRecord`/`textValue`/`createBoolParser`/`safeHttpUrl`，增加可选 `connectionErrorMessage` | `app.js:31-43`、`console.js:14-27`、`plugin-config.js:9-13`、`assistant.html:518-532` 使用共享 service；JSON body、X-QB-Token、ApiError、404/405 fallback 的契约测试通过。 |
| `index.html:596`、`console.html:298-301`、`assistant.html:470` 加载同源 `/services/api.js` | 静态脚本顺序测试通过；`/services/*.js` 路由在 `tests.test_server` 中覆盖。 |
| `gitutil.py` 合并 Git runner | `assistant_ext.py:30`、`updater.py:10` 使用共享 `run_git`；仍设置 `GIT_TERMINAL_PROMPT=0`、非 Windows `GIT_ASKPASS`/`GIT_SSH_COMMAND`，`shell=False`，保留 stdout/stderr、cwd、超时和 extra_env；`test_updater` 6 tests 通过。 |
| 测试 fixture 合并 | `test_assistant_ext.py` 的 FakeProcess 与 `test_deployer.py` 的 OfflineApi 仅测试内复用；全量测试无行为回归。 |
| 前端删除重复 helper | `console.js` 删除零调用 `syncPromptManagerBusyUi()`；prompt/session/ambient UI 静态契约 16 tests 通过。 |

### 2.2 行为/API/安全检查

- 前后端 API 路径、请求字段、响应字段和错误状态未改变；
- 前端仍无 CDN、无构建器、无 `innerHTML`/localStorage/sessionStorage；新增的 `/services/api.js`
  是同源本地静态资源；
- `safeHttpUrl` 仍拒绝非 HTTP(S)，token 仍通过 `X-QB-Token`，schema 敏感字段过滤与
  plugin-config fallback 测试通过；
- Git runner 使用列表 argv、`shell=False`，不新增 shell 拼接或凭据日志；
- Windows 独立仓库不被本任务修改；sync 只读 dry-run 见第 3.6 节。

## 3. 高风险点复验

### 3.1 Prompt profiles / active / pending / 容量

**通过。**

- `tests.test_prompt_profiles` 17/17：旧 prompt 迁移、唯一 pinned、删除最后保护、
  12000/12001 边界、边缘空白 round-trip、stale plugin 连续 GET pending、API 恢复清 pending、
  未部署不误报、写失败不伪造同步、mutation pending。
- 临时 mock 边界：
  - 101 profile、active 在头/尾均返回可操作 `ok:false`，store 仍保留 101 项；
  - 12000 通过、12001 拒绝且不丢原文；
  - 9×12000 创建成功，插件字段 JSON 109,909；
  - 100×12000 GET 成功，插件 parser 100 项、JSON 1,214,789；
  - Studio schema/通用 validator 接受 2,600,000 字符上限；
  - server 对 `/prompts` 使用 8 MiB 专用 body 上限，普通 API 仍使用 2 MiB。
- 未做真实 AstrBot 热重载；因此“API 接受但真实插件热重载”只能在 mock 路径确认，真实 E2E 留待发布前。

### 3.2 AstrBot persona / conversation

**通过（fake manager 层）。**

- group persona v1→v2、profile→group 均新建 conversation；
- `/会话 名称` 目标 persona 与当前群 persona 一致时真正 switch，不新建；
- 目标 persona 为空/不同时明确拒绝，current conversation 不变，没有隐藏新会话；
- `on_llm_request` 无法保证 persona/conversation 绑定时安全降级，不把新人格注入旧历史；
- 真实 AstrBot v4.29.3 ConversationManager/PersonaManager API 未验证。

### 3.3 Ambient 群聊 / 私聊 / @

**通过。**

- ambient 默认关闭；
- 显式 @ 在 ambient 开/关两种配置下均必回；
- 群聊 `on_llm_request` 注入同 UMO 的 `# Ambient Group Context`，私聊不注入 group 文案；
- UMO 隔离、`round_interval > cache_limit`（默认 cache=3、round=5 的第 5 条触发）、
  `min_message_chars`/`ignore_pure_image`/`ignore_pure_face` 过滤测试通过；
- 真实 QQ `Comp.At` 显示、AstrBot memory 真实上下文消耗未验证。

### 3.4 统一 tool marker / 多分类 / 语义表情

**通过（插件 191 tests）。**

- legacy `[[sticker:...]]` 与统一 `[[tool:...]]` 不同时执行；
- 非法/多 marker 只回退文字或最多一个附件；
- 多分类、语义 tags/meaning/tone、全局收藏和本地收藏回归通过；
- 单测不访问真实网络，未验证真实 AstrBot 工具链/QQ 图片展示。

### 3.5 服务端 token / 路径 / 请求体上限

**通过。**

- `tests.test_server` 37/37：token 403、Host、API 字段、静态资源路径和 prompt body
  上限测试通过；
- 路径解码后仍限制在 static roots；插件 ID/instance ID 的既有校验未改；
- 8 MiB 只用于 `/prompts` 和含 `prompt_profiles` 的插件配置路径；非 prompt 插件配置
  仍走约 512 KiB 校验，普通 API 仍 2 MiB；
- 真实外网/真实实例路径未做，只有本地 fake server 测试。

### 3.6 Windows 同步 allowlist

只读 clone Windows 独立仓库并执行 sync dry-run（没有写入 Windows release）：

```text
MIRRORED_PATHS:
  qqbot_studio        -> qqbot_studio
  plugins/qqbot_enhancer -> plugins/qqbot_enhancer
  assets              -> assets
```

- 整个 `qqbot_studio/` 目录被镜像，因此新文件
  `qqbot_studio/gitutil.py`、`static/services/api.js` 的新 helper、`index.html`/
  `console.js`/`assistant.html` 的新加载依赖都会覆盖到；
- 整个 `plugins/qqbot_enhancer/` 目录被镜像，插件新增 schema/config 文件不遗漏；
- dry-run 识别 `017ec94` 的 core 3.12.1 变更，当前 Windows 快照仍是 v0.8.0 /
  upstream `ae10104`，因此 **task-84 必须整体 sync 到 `017ec94`**，不能只同步部分 HTML/JS；
- `scripts/e2e-smoke.sh`、`tests/e2e/` 和最终 QA 报告有意不在 Windows 的 runtime
  allowlist 中；Windows 有独立 launcher/测试，若需要同步 QA/E2E 文档需在 task-84 单独处理。

## 4. P2：非阻断的发布/文档/验证债务

1. **文档测试计数/版本仍有少量陈旧值（P2）：**
   - `docs/plugin-qqbot-enhancer.md:1068` 写 403 tests；实际 408 tests；
   - `docs/e2e-validation.md:281` 写 403 tests / 27.705s；是较早的 E2E 快照，当前全量 408/28.103s；
   - `docs/ponytail-optimization-phase2.md:97` 的示例命令仍写 `qqbot-studio 3.12.0`，
     当前候选是 3.12.1；该文基线 `3.12.0` 可保留为历史，但示例输出应标明是基线运行；
   - `docs/qa-report-v3.12.md` 是 v3.12.0 的 407 tests 历史快照，且仍含早期 3.11.0
     pending 描述；不应作为当前最终版本说明；
   - `docs/README.md` 尚未加入本报告和 `docs/qa-report-v3.12.md` 的链接；由发布整理处理。
2. **Windows sync 未执行（P2）：** 当前外部仓库仍为 v0.8.0/`ae10104`；task-84 必须同步
   `017ec94`、重跑 Windows tests、更新 `.upstream-commit`、`VERSION` 和双 EXE release
   门禁。allowlist 本身覆盖 runtime 目录，但 E2E/docs 未纳入 runtime mirror。
3. **真实浏览器 E2E 未执行（P2）：** 本环境无 Chromium/jsdom；共享 API service 的
   Node 契约、脚本顺序和 `node --check` 通过，但焦点陷阱、aria-busy、删除计时器、二级
   modal 的真实浏览器/屏幕阅读器行为仍需人工验证。
4. **真实 AstrBot/QQ/Docker/DeepSeek 未验证（P2）：** 未登录真实 QQ，未验证真实
   noVNC 扫码、OneBot WebSocket、AstrBot 真实 Persona/Conversation 历史隔离、
   DeepSeek 真计费、Docker bind mount 和 Windows Docker Desktop。
5. **发布可重复性：** `README.md`/`docs/README.md`/`docs/qa-report-v2.md` 已为 3.12.1，
   core `__version__` 为 3.12.1；但 v3.12.1 tag 目前尚未创建，这是 Lead 计划的发布动作，
   不是本 QA 的代码缺陷。

## 5. 结论

- **P0=0；P1=0。** Ponytail 第二阶段的共享前端 helper、Git runner 合并、测试 fixture
  合并和死代码删除没有发现行为、API、数据、安全或权限回归。
- 全量 408 tests、prompt 17、插件 191、UI static 16、server 37、frontend components 4、
  updater 6、assistant extension 37 均通过；静态检查全部通过。
- 当前候选可作为 v3.12.1 的 **代码级条件通过**；正式发布前必须处理 P2 文档计数/链接
  收口，并完成 Windows task-84、真实浏览器、真实 AstrBot/QQ/Docker/DeepSeek E2E。
- 本报告只新增 `docs/qa-report-final.md`，未修改业务代码或既有测试。
