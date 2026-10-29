> **v2 重大变更提示（2026-10-29）：** 本 QA 报告对应 v1 单实例版本。
> 之后项目已升级为 v2：默认绑定 0.0.0.0、多机器人实例、screen 后台管理、镜像节点测速/自定义、
> 部署任务持久化与恢复、控制台页面、Docker 容器自检修复、QQ 增强插件等。
> v2 改动范围远大于本报告覆盖范围，发布前必须重新执行完整测试与真实端到端验收。

> **版本说明（2026-10-28 后续更新）：** 本报告主体对应早期 Docker 单镜像源版本（commit `6066872`）。
> 之后已新增“国内镜像多源自动回退”、离线镜像说明、Docker 安装完成严格检查，以及
> `--port` / `QQBOT_STUDIO_PORT` 指定本地向导端口；相关新增单元测试目前通过，
> 但未重新执行完整 QA 与真实 Docker E2E。改动涉及 `configgen.py`、`deployer.py`、`server.py`、
> 启动脚本、前端开关文案、测试和 README。

# QQ Bot Studio 最终 QA 报告

- 审计时间：2026-10-28 22:15（Asia/Shanghai，UTC 14:15）
- 审计对象：`/home/stevehim/QQ Ai bot` 当前最终稳定快照。该目录不是 Git 仓库，无法做 commit 级 diff。
- 环境：Ubuntu 24.04.3 LTS（WSL2）、Python 3.12.3、Node v24.21.0、Bash 5.2.21、普通用户 uid=1000。
- 安全/成本约束：未启动 Docker 守护进程，未拉取或启动容器，未调用真实 DeepSeek 付费接口，未执行真实 QQ 扫码/验证。
- 最终结论：**条件通过（Conditional Pass）**。25/25 单测通过，JS/Shell/Python 语法检查通过，本地 HTTP 冒烟、安全边界检查、密钥落盘/权限和文档一致性检查通过；**没有开放的 P0/P1/P2 代码或文档缺陷**。由于本环境没有 Docker daemon，真实容器、AstrBot/SnowLuma、QQ 扫码和 DeepSeek 联网 E2E 仍未验收，生产发布前必须补做。

## 1. 发现与修复状态

### P0

无。

### P1

#### P1-01（已修复）Docker 安装脚本以 root 使用可预测 `/tmp` 文件名，存在本地提权/任意覆盖风险

- 修复前位置：`scripts/install-docker.sh`，使用固定 `/tmp/qqbot-docker.gpg`、`/tmp/get-docker.sh`，并在 root 下执行下载脚本。
- 影响：本机其他用户可预置 symlink，或在下载与执行之间替换可预测文件，导致 root 任意写/任意代码执行（CWE-377）。
- 复现：独立执行 `curl -fsSL file://... -o <symlink>` 返回 0，目标文件被覆盖，确认 curl 跟随输出 symlink。
- 修复：当前脚本使用 `umask 077`、`mktemp -d`、`trap` 清理；GPG key 改为 `curl | gpg` 管道；`get.docker.com` 下载到私有临时目录，并在执行前检查 `-f && ! -L && -s`、再 `chmod 700`。
- 复核：`bash -n scripts/install-docker.sh` 通过；固定 `/tmp/qqbot-docker.gpg`、`/tmp/get-docker.sh` 写入已不存在。未在本环境以 root 真实安装 Docker。

### P2

#### P2-01（已修复）运行日志 `/api/logs` 原样回传，未做敏感信息脱敏

- 修复前：部署任务日志经过 redactor，但运行日志通过 `/api/logs` 直接返回容器原始输出。
- 修复：增加 `DeployManager._collect_sensitive_values()` 和 `redact_sensitive_text()`；从 `secrets.json`、AstrBot `cmd_config.json` 动态 provider key 以及 `sk-*`、`Bearer`、`access_token=` 模式中收集/兜底脱敏；`get_logs()` 返回前调用 redactor。
- 复核：真实 `DeployManager` + HTTP `/api/logs` 合成测试返回：
  `{"ok": true, "logs": "provider=***\nonebot=***\nAuthorization: Bearer ***\nurl?access_token=***\n"}`；
  API key、OneBot token、Bearer token、query token 全部未出现。对应单测 `test_logs_api_redacts_keys_and_tokens` 通过。
- 残余边界：脱敏是已知值/模式匹配，第三方日志若输出编码或变形后的密钥无法保证 100% 覆盖；README 已明确“日志展示不应被当作绝对无敏感信息的保证”。

#### P2-02（已修复）API 错误状态/响应格式不符合契约

- 修复前：Docker 不可用时 `/api/logs` 返回 500；`OPTIONS`/不支持的 HTTP 方法返回 stdlib HTML 501；500 可能回传内部异常文本。
- 修复：`DockerError -> 503`、`AstrBotAPIError -> 502`；`OPTIONS/PUT/PATCH/DELETE/TRACE -> 405 JSON`，带 `Allow: GET, HEAD, POST` 且无 CORS 头；意外异常仅记录终端 traceback，响应为通用 JSON 500。
- 复核：真实 HTTP 冒烟得到 `/api/logs`（Docker 不可用）= **503 JSON**；`OPTIONS /api/state` = **405 JSON**，`Allow: GET, HEAD, POST`，无 `Access-Control-Allow-Origin`；`PUT` = 405 JSON；合成 `DockerError`/`AstrBotAPIError` 分别得到 503/502；合成内部异常返回通用 500，body 不含内部 marker。

#### P2-03（已修复）部署线程启动早期异常会永久卡在 running

- 修复前：`secrets.json` 损坏等早期异常发生在 `try` 之外，任务保持 running，`_active_job_id` 不清理，后续部署被永久拒绝。
- 修复：redactor 初始化移入 `try`，提供 API key 兜底 redactor；`finally` 统一清理 active job；配置落盘失败/线程启动失败也会回收 job 和 active 状态。
- 复核：新增 `test_early_failure_marks_job_error_and_clears_active_lock`；损坏 `secrets.json` 场景任务转为 error 且 active job 清空。25/25 单测通过。

#### P2-04（已修复）安装脚本错误提示与失败分支不完善

- `install.sh` 现在只在需要安装 Docker 时请求 sudo，失败时输出中文错误；Docker CLI 与 daemon 状态分开提示；桌面启动项安装失败会告警。
- `scripts/install-docker.sh` 的 convenience script 非零退出已改为 warning + 继续走统一失败提示，不再被 `set -e` 直接打断；普通用户直接运行会输出 `[docker-install] 错误：请通过 sudo 运行本脚本。`
- 实测：`./start.sh --version` 无 sudo 时退出码 1，输出明确的“未能获取管理员权限，Docker 未安装”；`./install.sh` 无 sudo 时退出码 1，输出“安装 Docker 需要管理员权限；请在有 sudo 权限的账户下重试。”；`bash scripts/install-docker.sh` 普通用户退出码 1，提示清晰。
- `install-desktop-entry.sh` 在含空格、单引号、非 ASCII 的路径下生成的 `Exec` 经 `shlex` 解析为目标脚本；desktop 文件权限 `755`。GTK 探测已识别 launch 目标，仅因本环境无终端/GUI 停在 “Unable to find terminal required”。

#### P2-05（已修复）README/API 契约/集成调研与实现不一致

修复并复核通过：

- README 删除“跳过 DeepSeek 验证”复选框描述；UI 无该控件，文案改为“网络不可达时警告并继续，无跳过复选框”。
- 前后端默认提示词完全一致（`len=335`）；README 中的防刷屏、防 API Key/系统提示词泄露、不冒充官方等条目均由实际默认提示词覆盖。
- README 环境检查描述改为 Docker/Compose/权限，并注明端口冲突在生成配置时规避。
- UI API 契约改为：所有 `/api/*` GET 同样强制 token；默认用户名为 `admin`；不支持的 HTTP 方法返回 405 JSON。
- 集成调研改为：AstrBot v4.29 `provider_settings` 无 `default_provider_id`，以 `agent_runner.config.model.provider_id = "deepseek-flash"` 为准；`jwt_secret` 可为 URL-safe 随机字符串，不要求 hex。
- `.env` 镜像覆盖已由 Compose 模板中的 `${ASTRBOT_IMAGE}` / `${SNOWLUMA_IMAGE}` 实现。
- 生成配置复核：SnowLuma `ws://astrbot:6199/ws`；AstrBot `deepseek-flash` / `https://api.deepseek.com/v1`；三个 host 端口均绑定 `127.0.0.1`；默认镜像及国内镜像前缀一致。

#### P2-06（已修复）`--host ::1` 的 CLI 允许值与实际监听能力不一致

- 修复前：CLI 允许 `::1`，但服务器为 IPv4 address family，实际启动报 `Address family for hostname not supported`。
- 修复：CLI 现在只允许 `127.0.0.1` / `localhost`；`::1` 和 `0.0.0.0` 均以 rc=2 拒绝，并输出“管理界面只允许监听 127.0.0.1 或 localhost”的中文提示。
- 复核：两种非法 host 均已实测为 rc=2。

### 已文档化的残余风险（非开放缺陷）

1. **访问令牌不是多用户隔离边界**：`GET /` 无认证返回含 token 的页面，同机任意可访问 loopback 的用户/进程可取得 token。设计用途是防 CSRF/误访问；README 已明确多人共用主机应使用独立系统账户、防火墙和容器权限限制。单用户桌面场景可接受。
2. **日志脱敏是 best-effort**：README 已明确日志展示不应被视为绝对无敏感信息；API Key 只写入 AstrBot 配置 `0600`，状态接口不返回 API Key。
3. **根目录 desktop 启动器使用当前离线绝对路径**：当前路径已被验证有效；README 已说明移动目录后重新运行 `scripts/install-desktop-entry.sh "$PWD"` 安装应用菜单入口。

## 2. 最终执行命令与结果

### 2.1 自动化检查

| 命令 | 结果 |
|---|---|
| `python3 -m unittest discover -s tests -v` | **25/25 通过**，`OK`，6.967s |
| `node --check qqbot_studio/static/app.js` | 通过，rc=0 |
| `bash -n start.sh install.sh scripts/*.sh` | 通过，rc=0 |
| `python3 -m compileall -q qqbot_studio tests` | 通过，rc=0 |
| `python3 -m qqbot_studio --version` | `qqbot-studio 1.0.0` |
| `python3 -m json.tool examples/*.json` | 两个示例 JSON 均可解析 |
| Python AST 导入扫描 | 无第三方运行依赖，全部标准库 |
| `subprocess` AST 扫描 | 仅 `dockerctl.py` 1 处 `subprocess.run`，参数为 argv list，`SHELL_TRUE=[]` |
| 全仓 `shell=True` / `os.system` / `os.popen` / `eval` / `exec` 扫描 | 源码中未发现 |

### 2.2 本地 HTTP 冒烟

启动：临时 `QQBOT_STUDIO_DATA_DIR` + `python3 -m qqbot_studio --no-browser --port <随机端口>`，未执行真实部署。

| 检查 | 结果 |
|---|---|
| `/healthz` | 200，`{"ok": true, "version": "1.0.0"}` |
| 首页 token 注入 | 200；提取 43 字符 token；精确占位符已替换；`Cache-Control: no-store` |
| `/styles.css` | 200，39806 bytes，`text/css` |
| `/app.js` | 200，59145 bytes，`text/javascript` |
| `/favicon.ico` / `/assets/qqbot-studio.svg` | 200，1345 bytes，`image/svg+xml` |
| `/api/state` + 正确 token | 200；`model=deepseek-flash` |
| `/api/state` 无 token / 错 token | 均 403 JSON `ok:false` |
| `/api/job/unknown` | 404 JSON |
| `/api/test-key` 空 key | 200 JSON `{"ok":false,"message":"请填写 DeepSeek API Key。"}` |
| `/api/logs?service=bogus` | 400 JSON |
| `/api/logs?service=astrbot`，Docker 不可用 | **503 JSON**，明确提示先运行 start.sh/install.sh |
| `OPTIONS /api/state` | **405 JSON**；`Allow: GET, HEAD, POST`；无 CORS 头 |
| `PUT /api/state` | **405 JSON**；`Allow: GET, HEAD, POST` |
| 非法 `Host: evil.example` | 400 JSON |
| `HEAD /healthz` | 200，无 body |
| 畸形 JSON / 非 object / 错误 Content-Type | 400 / 400 / 415，均 JSON |
| 路径遍历 4 组（`..`、编码 `%2e%2e`、`/static/..`、`/api/..`） | 全部 404，未返回文件内容 |
| 安全头 | `nosniff`、`X-Frame-Options: DENY`、CSP、`Referrer-Policy: no-referrer` 均在 |
| `Server` banner | `QQBotStudio/1.0`，不暴露 Python 版本 |
| 内部异常 `RuntimeError` 合成测试 | 500 JSON 通用文案；body 不含内部 secret marker；traceback 仅在终端 |
| `DockerError` / `AstrBotAPIError` 合成 POST | 503 JSON / 502 JSON |
| 远程绑定 `--host 0.0.0.0` | rc=2 + 中文回环限制提示 |
| 远程绑定 `--host ::1` | rc=2 + 中文回环限制提示 |
| 全新临时数据目录权限 | `0700` |

### 2.3 生成器、密钥与文件权限

| 检查 | 结果 |
|---|---|
| Compose YAML 解析 | 通过；services=`astrbot`、`snowluma` |
| Compose 镜像 env 引用 | `${ASTRBOT_IMAGE}` / `${SNOWLUMA_IMAGE}` 均由 `.env` 定义 |
| 生成 `.env` / `cmd_config.json` / `onebot.json` 写入方式 | `_atomic_write_text()`：`mkstemp` 后 `fchmod`，写完 `os.replace`；无敏感临时副本残留 |
| `.env` / AstrBot cmd_config / SnowLuma onebot.json | 权限 `0600` |
| `config.json` / `secrets.json` | 权限 `0600` |
| 根目录 / data 子目录 | 权限 `0700` |
| 脚本权限 | `start.sh`、`install.sh`、`scripts/*.sh` 均 `755` |
| setuid/setgid/world-writable | 未发现 |
| API Key 标记 | 只出现在按设计需要的 AstrBot `cmd_config.json`（1 次，`0600`）；未出现在 config/secrets/.env/compose/SnowLuma 配置/状态/job 日志/API 响应 |
| QQ 密码标记 | 未出现在 config/secrets/生成配置/UI；payload 中额外 `qq_password` 字段不会进入部署接口 |
| 部署日志脱敏 | API Key、OneBot token、AstrBot 密码、VNC 密码均替换为 `***` |
| 运行日志脱敏 | `/api/logs` 合成测试中 DeepSeek key、OneBot token、Bearer token、access_token 均替换为 `***` |
| 前端持久化 | 无 `localStorage`；`sessionStorage` 仅保存 job id；令牌/API Key 不写入浏览器存储 |
| 静态 DOM 契约 | app.js 中 78 个 DOM id 引用全部能在 index.html 找到 |
| 前端危险 sink | 未发现 `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`eval`/`new Function` |

### 2.4 前端/API 契约与文档一致性

| 检查 | 结果 |
|---|---|
| 前端请求路径 ↔ server 路由 | 7 个接口全部匹配 |
| `state/test-key/deploy/job/action/persona/logs` 字段 | 与前端读取字段一致 |
| README 模型 / API Base / 镜像 / 镜像前缀 / 端口 | 与生成器一致 |
| SnowLuma 生成的 OneBot URL | `ws://astrbot:6199/ws` |
| AstrBot 生成 provider | `deepseek-flash` + `https://api.deepseek.com/v1` |
| README 默认提示词 ↔ store ↔ app.js | 文本完全一致，长度 335 |
| README/Docs “跳过验证” | UI 无该控件；文档已改为“无跳过复选框” |
| UI 契约 GET token / 默认用户名 / 405 JSON | `admin`、GET 强制 token、405 JSON 均与实现一致 |
| 集成调研 `default_provider_id` / `jwt_secret` | 已与当前生成配置及 URL-safe random 说明一致 |
| README 安全边界/日志脱敏/Token 用途 | 已明确记录单用户边界与脱敏 best-effort |
| README 当前目录 desktop 启动器 | 文件存在且权限 755；`Exec` 指向存在且可执行的 install.sh；Path/Icon 目标均存在 |
| `.env` 镜像覆盖 | Compose 模板实际引用 env，符合调研文档 |
| 静态资源外部依赖 | 无 CDN/远程字体/外部图片 |

### 2.5 脚本普通用户路径实测

| 场景 | 结果 |
|---|---|
| `./start.sh --version`（无 sudo） | rc=1；中文安装说明 + `错误：未能获取管理员权限，Docker 未安装。` |
| `./install.sh`（无 sudo） | rc=1；中文 `错误：安装 Docker 需要管理员权限；请在有 sudo 权限的账户下重试。` |
| `bash scripts/install-docker.sh`（普通用户） | rc=1；`[docker-install] 错误：请通过 sudo 运行本脚本。` |
| desktop 脚本含空格/引号/Unicode 路径 | 生成成功；`Exec` 经 shlex 解析等于目标 `start.sh`；文件 755 |

## 3. 未能在本环境验证的事项

以下不能视为已验收：

1. Docker daemon、真实 `docker compose config`、Windows/WSL Docker 集成、镜像拉取和容器启动。当前 Docker CLI 在 WSL2 中不可用。
2. AstrBot v4.29.x 真实 schema、Dashboard 登录/persona API、健康检查、OneBot 反向连接。
3. SnowLuma v2.2.x 配置加载、WebUI 临时密码日志格式、noVNC、真实 QQ 扫码及设备/短信/滑块验证。
4. DeepSeek 真实 Key `/models` 响应或聊天调用；本次只测空 Key 和 mock，未消耗额度。
5. 真实浏览器视觉、响应式、剪贴板、密码管理器行为；只做 Node 语法、DOM 引用和 HTTP 静态资源验证。
6. 真实 Ubuntu 桌面/终端中的 sudo 交互、root 安装 Docker、应用菜单双击启动。本环境 sudo 被容器策略阻断且无 GUI/终端。
7. Python 3.10 实际运行；本机只有 3.12.3，仅做静态兼容审查。
8. `shellcheck`、`desktop-file-validate` 不可用；Shell 仅做 `bash -n` + 人工审阅，desktop 使用 `shlex`、临时路径和 GTK 探测辅助验证。

## 4. 发布建议

- 当前代码适合进入真实 Ubuntu 24.04 主机/虚拟机做最终集成验收；本地单测、本地 HTTP 服务、生成器、安全检查和文档一致性均通过。
- 最终上线前必须执行一次真实 E2E：安装 Docker、`docker compose config`、拉取镜像、启动 AstrBot + SnowLuma、打开 noVNC 扫码/验证、确认 OneBot 状态、使用真实 DeepSeek Key 做一次最小聊天调用并核对 AstrBot 日志与完成页。
- 权限提示：SnowLuma 按上游要求使用 `SYS_PTRACE` 与 `seccomp=unconfined`，且宿主机端口只应保持 loopback 绑定；不要在多人共用主机上把他人的 loopback 访问视为可信。
- 当前没有需要立即阻断交接的开放 P0/P1/P2 缺陷；真实 E2E 通过后才能给生产发布最终签字。

## 5. 最终快照指纹

> 无 Git 历史，以下为本报告对应的最终 `sha256sum` 快照（27 个关键文件）。

```text
0e4f038a7af181b25b84aee05fb025c4db1f64b385c296bf8cbbb5726afe32f7  README.md
daae2ce433614c0b1b1a882138928835cdaa8deb0865b6aa66cd2f6cb2823bf1  start.sh
dcd0e603c5a03dd75495b6e1274b100d6393b7e5072d5121862fa8368b180e85  install.sh
adeb77e0acd287eeae9962e14b8bbe6bd9494a80ed0a39bc8bd1403e8f51deb2  scripts/install-docker.sh
fb6b1c8ddbd8d19a234eb8fdee1c3b003d70f853ff6029e401108098a935cc61  scripts/install-desktop-entry.sh
0aebb04c54737ec5febcf1ba6c91d979e5c6e26ad69381538e3e3fe10fc87fcc  qqbot_studio/__init__.py
9e374459014e54e9bd5a8accf08b39021648ff3f259f681ac359ac2d0e0f11db  qqbot_studio/__main__.py
5bf1ac5e1e53dcf5f0a722e486d7e2243769f1b68b9dd408389aec4100cdf68a  qqbot_studio/astrbot_api.py
ffff2a4d2f72322ce68e3f041cdc3933c5efc39f8a2e108fce9ae5d5b7b0b0b1  qqbot_studio/configgen.py
fb1406be397b36cc1f54ce316d6fef24a4366832c02e1a4e535fbdfb1da6dc5a  qqbot_studio/deployer.py
796ab0635266c65e04998d3f761fa00de405f98ac08a202ca782aa6f70d394e9  qqbot_studio/dockerctl.py
c817bcc29f1d0cfb34ce4911442c9d2b07b40a580fa11216a52089f08c241159  qqbot_studio/server.py
e1a5b8be0ffc24e95eb121e6264ff9dc42bf91c66bd19cb2f640b379912416c9  qqbot_studio/store.py
5e88e9ba336a0719427f949c5869597c15ec027b035be6119fda7896bb79dd94  qqbot_studio/static/index.html
3b8480d7e377ac46f61b10f1c8e3dc77ac83fe510d5a73a93da8c8eadb2c61d3  qqbot_studio/static/app.js
222aace2ddf668e00c3451577bb96fafa49d49a70592d9f59815b6569db1bc50  qqbot_studio/static/styles.css
95267947771802ec987a0c7c4ffd98512f96ee132fd3153466e8eef18cc23e38  docs/integration-research.md
4ab431a57a160e2b379aeeddbde1b6ba196973879beda02c61a6b017a939cc9f  docs/ui-api-contract.md
6d6779281faa83b13d60901a22242904c458152cae090a5223c66a3514bd614f  tests/test_astrbot_api.py
36496a8f6a1b866bbe38b0ea54d27f5a470ccf0ff398f1fea4ae0877b7007e63  tests/test_deployer.py
3d5e67f06c3c5ed5746bc684e53ba3233d3c4910d4b0e3fc5feb03bfd0118c7f  tests/test_dockerctl.py
9d14467456ef6ebdf8fbd05775290666377fe4a9b5a5ac29a0e8e085ec3ea434  tests/test_server.py
03f7057cf7d3eed2d179c03d8e7cb5adcf38246b369c66763d3453dbe1ef258f  tests/test_store_configgen.py
5e566438bb72ef58c1104d8a2df87be24b53da822799312fec9b708ab6bf0154  QQ机器人一键部署.desktop
fdd26a028f6523a3079344c13616976df32953f67edfe14d56e64cc9da08326f  examples/astrbot-cmd_config.min.json
e45fb7431922a5d0cc5e064c64e0a8734634899c9ca9eac6434e74ea4371cb5e  examples/snowluma-onebot.json
5c33c2c4fe5dc00c023be293e8583f18a3cdff669686bd34f998d3e11176676a  assets/qqbot-studio.svg
```
