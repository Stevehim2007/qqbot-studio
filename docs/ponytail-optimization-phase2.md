# Ponytail full 第二阶段：低风险精简记录

> 基线：`ae10104`（`main`，v3.12.0，Linux 407 tests）。日期：2026-10-31。
> 规则：DSH Ponytail `full` + `ponytail-audit`；先追踪真实加载顺序/调用链，再做
> 低风险删除或复用。本文只记录复杂度和过度工程清理，不把正确性/安全/性能问题
> 当成“精简项”。

## 1. 结论

- 行为承载代码/测试：**15 个文件，+198/-393 行，净减 195 行**。
  - 源码：9 个文件（含新增 1 个标准库 helper），+125/-282，净减 157 行；
  - 测试：6 个文件，+73/-111，净减 38 行。
- 新增运行依赖 **0**；删除文件 **0**；新增源码文件 **1**（`gitutil.py`）。
- 未改 API 路由/字段、数据格式、实例目录、权限、token、Host/SSRF/Docker argv、
  错误状态语义、插件协议或 Windows release。
- 全量 unittest：**408 tests OK（28.159s）**；JS/Python syntax、JSON schema、
  Markdown 本地链接和 `git diff --check` 全部通过。

## 2. 已实施项

### 2.1 前端公共 helper 复用

复用已有的经典脚本 `window.QQBotStudio.services.api`，不再在四处分叉 API/数据 helper。

| 文件 | 改动 | 行为保持 |
|---|---|---|
| `services/api.js` | 导出 `asRecord`、`textValue`、`createBoolParser`、`safeHttpUrl`；client 增加可选 `connectionErrorMessage` | 默认连接文案、token 头、JSON body、`ApiError`、404/405 fallback 不变 |
| `app.js` | 使用共享 `createApiClient`/`ApiError`/数据 helper；实例 query 由 `fetchImpl` 包装 | `/api/*?instance=`、请求体、错误状态与提示不变 |
| `console.js` | 使用共享 helper；删除零调用 `syncPromptManagerBusyUi()` | 数据归一化、插件配置、modal 行为不变 |
| `services/plugin-config.js` | 复用共享对象/字符串/布尔 helper | schema、敏感字段过滤、GET/POST 契约不变 |
| `index.html` | 在 `app.js` 前加载同源 `/services/api.js` | 经典脚本，无 CDN/构建器 |
| `assistant.html` | 加载同一本地 API service；删除重复 `ApiError`/`apiRequest`/fallback | 保留该页原连接错误文案和 token/fallback 语义 |

未合并各页 `formatError()`：404/409/500 文案和兜底策略不同，会影响用户可见错误语义。
`makeElement`/`makeIcon`/`setText` 等 DOM 小函数也因页面上下文差异保留。

### 2.2 Python Git runner 合并

新增 [`qqbot_studio/gitutil.py`](../qqbot_studio/gitutil.py)，统一：

- `GIT_TERMINAL_PROMPT=0`、非 Windows 的 `GIT_ASKPASS=/bin/true`、
  `GIT_SSH_COMMAND` BatchMode/主机键参数；
- 额外环境变量、`cwd`、超时、stdout/stderr 捕获和 `shell=False`；
- updater 的三参数 fake runner 与 assistant extension 的四参数 fake runner 注入签名。

新增单测覆盖环境变量与子进程参数；未改 Git 命令、超时默认值、错误分类或凭据处理。

### 2.3 测试 fixture 合并

- `test_assistant_ext.py`：四处 `FakeProcess` 和语义相同的 `StuckProcess` 合并为
  模块级 `FakeProcess(pid=...)`，保留各测试 PID 和 terminate/wait/kill 语义。
- `test_deployer.py`：三处相同 `OfflineApi` 合并为 `OfflineApi(FakeApi)`。
- 两个 `FakeContext` **不合并**：一个是导入期 AstrBot seam，另一个承载
  provider/persona/conversation 行为；合并会制造万能 mock 并掩盖导入时序问题。

### 2.4 死代码/import 复核

- 删除 `console.js` 中全仓零调用的 `syncPromptManagerBusyUi()` 单行包装。
- 最终 AST 扫描未发现新的无引用 import/私有函数；`studio.py` 的
  `_directory_usage` 等兼容别名有现存测试显式断言，保留。
- 未删除“仓内暂时无调用”但带公共/协议/兼容性质的 API。

## 3. 实际行数

| 路径 | + | - | 净值 |
|---|---:|---:|---:|
| `static/app.js` | 14 | 94 | -80 |
| `static/assistant.html` | 16 | 62 | -46 |
| `static/console.js` | 5 | 37 | -32 |
| `static/services/api.js` | 32 | 2 | +30 |
| `static/services/plugin-config.js` | 9 | 20 | -11 |
| `static/index.html` | 1 | 0 | +1 |
| `assistant_ext.py` | 3 | 38 | -35 |
| `updater.py` | 2 | 29 | -27 |
| `gitutil.py`（新增） | 43 | 0 | +43 |
| **源码小计** | **125** | **282** | **-157** |
| `tests/test_assistant_ext.py` | 24 | 94 | -70 |
| `tests/test_deployer.py` | 4 | 12 | -8 |
| `tests/test_frontend_components.py` | 17 | 1 | +16 |
| `tests/test_updater.py` | 20 | 0 | +20 |
| `tests/test_control_assistant_static.py` | 5 | 4 | +1 |
| `tests/test_wizard_platform_static.py` | 3 | 0 | +3 |
| **测试小计** | **73** | **111** | **-38** |
| **合计** | **198** | **393** | **-195** |

新增 43 行 Git helper 和小型契约测试是安全/验证成本；没有为净减数字删除安全分支、
错误处理或旧兼容路径。本文件与架构文档是任务要求的说明成本，不在上述源码/测试口径内。

## 4. 验证

```bash
python3 -m unittest discover -s tests          # Ran 408 tests in 28.159s, OK
node --check <all static/*.js + assistant inline scripts>
python3 -m py_compile qqbot_studio/*.py plugins/qqbot_enhancer/*.py tests/*.py tests/e2e/*.py
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
bash -n start.sh console.sh install.sh scripts/*.sh
python3 -m qqbot_studio --version              # qqbot-studio 3.12.0
git diff --check
```

额外 Node bootstrap smoke 验证 `/services/api.js` 先加载后，`app.js` 与
`assistant.html` 页内脚本均可完成初始化注册，无缺失命名空间错误。未运行真实 QQ
登录/浏览器视觉回归；沿用 v3.12.0 的真实 Linux Docker E2E，QQ 扫码仍需人工完成。

## 5. 明确拒绝项

| 拒绝/暂缓项 | 原因 |
|---|---|
| 合并三页 `formatError()` | 404/409/500 文案与兜底不同，属于用户可见错误语义 |
| 合并 DOM builder/日期格式 helper | 页面上下文不同，跨页依赖收益小；控制台日期格式化仅单处调用 |
| 合并 `FakeContext` | 导入期 seam 与完整 provider fake 的职责/时序不同 |
| 删除 `studio.py` 资源统计兼容别名 | 有测试断言可导入，属于兼容 API |
| 抽取 plugin schema 前后端双校验 | 后端信任边界与前端输入体验职责不同，合并会扩大安全回归面 |
| 合并原子 JSON 写入 | 0600/0700、fsync、临时文件、replace 分支安全敏感，不为行数冒险 |
| 合并 sticker/tool marker | legacy sticker 与 unified tool 的 wire format/权限/回退不同 |
| 删除 Protocol/单实现 seam | 测试 mock、Windows runtime seam 和架构阶段要求保留 |
| 删除历史文档/修改 Windows release | 历史链接仍有价值；Windows repo 本轮明确只读 |

## 6. Windows 同步影响

本轮没有修改 Windows 仓库。下一次整体同步 Windows 必须包含：

- `services/api.js` 的新 helper/可选连接错误参数；
- `index.html`、`console.js`、`assistant.html`、`plugin-config.js` 的新加载依赖；
- `gitutil.py` 及 `assistant_ext.py`、`updater.py` 的 runner 复用；
- 对应前端静态测试和 Python 测试。

不要只同步 HTML/JS 而漏掉 `services/api.js`，否则向导/独立助手页会显式报缺少共享
命名空间。`/services/*.js` 路由已在 Linux 核心中；本轮未改 Windows launcher、EXE、
VERSION、数据目录或发布流程。

## 7. 剩余债务

- `console.js`、`app.js`、`assistant.html` 仍是经典脚本单体；仅数据/API helper 已复用。
- 各页错误文案、DOM builder、日期显示仍有少量重复，待文案契约和浏览器回归稳定后再评估。
- `server.py`、`deployer.py`、插件 `main.py` 仍是大聚合模块，属于架构阶段任务。
- 前端仍无 ES module/import map 或构建步骤。
- Windows 独立仓库需在下次发布周期整体同步本基线，不能用主仓库净减行数代替发布验证。

关联文档：[架构与阶段进度](architecture-modularization.md) ·
[第一阶段 Ponytail 审计](ponytail-audit.md) · [v3.12 QA 快照](qa-report-v3.12.md)。
