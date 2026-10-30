# Windows 双发行版可行性调研

> 调研日期：2026-10-30  
> 范围：只做架构与官方资料核实，不写部署代码、不构建 EXE、不发布 Release。  
> 结论中的“可行”仅表示官方能力/技术路径存在；本环境没有 Windows 真机、桌面版 NTQQ、Docker Desktop 或真实扫码验证，因此**不能把本文当作已联调或生产可用承诺**。

## 0. 结论先行

| 目标 | 结论 | 推荐程度 | 关键前提 |
|---|---|---|---|
| A. 纯 Windows 原生版，**不使用 Docker/WSL/Linux** | **可行**，但属于高工作量、较高维护风险的新后端 | 中低 | Windows 10/11 x64；AstrBot 可用 Python 3.12+ 原生运行；SnowLuma 使用官方 `win-x64` 包；用户自行安装并登录 Windows 桌面版 NTQQ |
| A 继续使用 noVNC | **技术上可做，但不是 SnowLuma 官方原生能力**；必须额外部署 Windows VNC Server + WebSocket↔TCP 代理 + noVNC 静态前端 | 低（可选） | VNC Server 运行在同一桌面会话；第三方软件许可、安全、桌面共享和多实例限制必须接受 |
| B. Docker 版，不强制绝对无 Linux/WSL | **可行且应作为首选**：沿用当前 Docker Desktop + WSL2 + Linux containers | 高 | Docker Desktop、虚拟化、8GB 内存；首次扫码仍需 noVNC |
| B 的“无 WSL”版本 | **有条件可行**：Docker Desktop 全用户安装 + Hyper-V 后端，仍运行 Linux VM/容器；Hyper-V 仅见 Windows Pro/Edu/Enterprise 官方要求 | 中（仅限合适合规机器） | 管理员安装、Hyper-V；不是 Windows containers |
| B 的“完全无 Linux”版本 | **当前不建议/不可行** | 不建议 | AstrBot/SnowLuma 无官方 Windows 容器镜像；SnowLuma 依赖 Windows 交互式 QQ 注入，标准 Windows 容器无法可靠承载扫码 GUI |
| Release 中同时提供两个 EXE | **可行**，建议作为同一版本线的两个资产 | 高（流程建议） | 共享 UI/大部分核心代码；两个运行时后端；双 EXE 同步构建、同步版本号、分别做 Windows E2E |

一句话建议：

1. **B 先发布并稳定**：直接复用现有 Windows Docker Desktop + WSL2 方案，最省改造、风险最低。
2. **A 做限量单实例 POC**：先验证“原生 AstrBot + 原生 SnowLuma + 本机 NTQQ 扫码 + OneBot 连接”，再决定是否产品化。
3. **noVNC 不删掉，但降级为可选增强**：官方 SnowLuma Windows 路径是直接看桌面 QQ 窗口；若用户坚持浏览器扫码，需要额外 VNC Server + websockify/noVNC，且只能共享 Windows 当前交互桌面。
4. **两个 EXE 必须同版本、同 Release、CI 同批构建**；不要维护两套手工分叉前端。

## 0.1 已落地的同步与双 EXE 发布规则

Windows 独立仓库已按本文结论接入以下流程（流程文件已存在；真实 GitHub Actions 跨仓库
自动运行仍以实际 workflow run 为准）：

- 同一 Windows 仓库、同一版本线、同一 `v*` GitHub Release 同时发布：
  - `QQBotStudioWindows.exe` = Docker Desktop + WSL2 稳定 edition；
  - `QQBotStudioWindowsNative.exe` = Native bootstrap/guide 占位，不冒充完整 Native
    产品，不宣称 AstrBot/SnowLuma/NTQQ E2E。
- `.github/workflows/sync-upstream.yml`：
  - 支持 `workflow_dispatch` 和每日 schedule，checkout 本仓库公开 `main`；
  - allowlist 同步 `qqbot_studio/`（含 static）、`plugins/qqbot_enhancer/`、`assets/`
    和必要 docs；
  - 保留 Windows launcher、PowerShell、build/spec、`VERSION`、release workflow 和
    Windows 专属 README/FAQ；
  - 写入 `.upstream-commit` 并更新 `UPSTREAM.md` 的 Linux commit、bundled core 版本和
    plugin 版本；同步后跑测试，有变化才自动 commit/push，无变化不空提交；
  - 不覆盖 Windows 版本号，版本升级和 tag 仍由发布任务显式处理。
- 每次 Linux major/minor 更新必须在同一发布周期同步 Windows 两个 edition；tag
  Release 前校验 `VERSION`，构建和 smoke 两个 EXE，上传 Docker/Native EXE +
  `SHA256SUMS.txt` + README/VALIDATION。未同步、测试失败或任一 smoke 失败时阻止发布。
- Native noVNC 继续作为可选高级增强：Windows VNC Server + websockify，共享整个
  交互式桌面，不承诺每实例独立屏；官方推荐路径仍是本机 NTQQ 窗口扫码。

本地预演（在 Windows 仓库）：

```powershell
python scripts\sync_upstream.py --upstream-dir ..\qqbot-studio --target-dir . --dry-run
python -m unittest discover -s tests -v
```

---

## 1. A：纯 Windows 原生版

### 1.1 AstrBot 原生 Windows 可行性：已核实，官方支持

官方确认：

- AstrBot 官方“脚本部署”页明确支持 **Windows**，安装脚本为 PowerShell `install.ps1`。
- 官方源码部署页给出了 Windows 下创建 venv、激活 `venv\Scripts\activate`、安装依赖并运行 `python main.py` 的步骤。
- 官方还提供 AstrBot 桌面客户端（`.exe`），但它是独立 GUI 客户端；本项目若需要精确管理配置、端口和启动顺序，优先使用官方 CLI/Python 运行时，不建议把桌面客户端当作被管理的后端。
- v4.29.3 的 `pyproject.toml` 声明 `requires-python = ">=3.12"`；官方安装脚本实际使用 `uv tool install --python 3.12 astrbot`，随后执行 `astrbot init`、`astrbot run`。
- 原生路径不需要 Docker/WSL。AstrBot 数据根目录可由 `ASTRBOT_ROOT` 指定，源码中数据目录为 `<ASTRBOT_ROOT>/data`；因此可以为每个实例指定独立数据目录，避免多个实例的 `cmd_config.json`、数据库和插件互相污染。
- 默认端口仍与 Linux 版一致：WebUI `6185`，OneBot v11 反向 WS 默认 `6199`。

对项目的含义：

- A 版仍可复用现有 AstrBot 配置生成逻辑，重点是改为原生进程生命周期管理，而不是 `docker compose`。
- Windows 原生版不能继续把 OneBot URL 写成 `ws://astrbot:6199/ws`；应改为 `ws://127.0.0.1:<每实例端口>/ws`。
- 推荐默认只绑定回环地址（AstrBot `ws_reverse_host=127.0.0.1`，SnowLuma WebUI 也优先 `127.0.0.1`），避免 Windows 局域网/公网直接暴露 OneBot 控制面。
- 需要随 EXE 提供或首次下载 Python 3.12 运行时、AstrBot 包、Dashboard 静态资源。AstrBot 依赖数量较多，**不建议承诺单文件 PyInstaller 完全内嵌**；更现实的是一个管理 EXE + 首次安装/修复依赖。

风险：

- 首次安装需要联网下载 Python/依赖/AstrBot，失败重试与国内镜像需要单独设计。
- AstrBot 的依赖包含原生/较重包（如 faiss、pandas、Pillow 等），打包体积和 Windows 兼容性必须真机验证。
- AstrBot AGPL-3.0-or-later；如果重新分发，需要按许可证履行源码/许可义务。推荐下载官方未修改发行物，或由用户安装，而不是随意内嵌修改版。

### 1.2 SnowLuma 原生 Windows 可行性：已核实，官方支持

官方 Windows 手动部署页确认：

- 原生 Windows **不需要 Docker、不需要 WSL2**。
- 运行方式是安装 Windows 桌面版 QQ（NTQQ），下载 SnowLuma 官方 `win-x64` 发行包，解压后运行 `launcher.bat`；SnowLuma 会检测 `QQ.exe` 并注入 hook。
- 完整版包内置 Node.js；lite 版需要用户安装 Node.js（官方文档写 Node ≥22，建议 Node 24 LTS）。
- 官方 Release v2.2.1 的实际 Windows 资产是：
  - `SnowLuma-v2.2.1-win-x64.zip`
  - `SnowLuma-v2.2.1-win-x64-lite.zip`
- 注意：官方 Windows 文档中出现了 `.tar.gz` 的写法，但 v2.2.1 Release 实际提供的是 `.zip`。实现时应从 GitHub Release API/固定资产名读取，并做 `.zip` 为主、错误提示清晰的回退，不要硬编码文档中的 `.tar.gz`。
- 原生版默认端口：WebUI `5099`、OneBot HTTP `3000`、OneBot WS `3001`。
- 登录**只支持扫码**：在桌面版 QQ 窗口中用手机 QQ 扫码。原生 Windows 上官方明确说“不需要 VNC/noVNC；那是 Linux 无头环境才用的”。
- SnowLuma 与 QQ 应在**同一 Windows 用户、相同权限级别**运行；一个管理员一个非管理员可能导致注入失败。
- QQ 版本必须和 SnowLuma native hook 匹配；QQ 自动升级后 hook 可能失效，需要等待 SnowLuma 更新或回退兼容 QQ 版本。
- Release 说明写的是 Windows x64；未看到 Windows ARM64 正式资产。

数据目录与多实例的不确定性：

- 官方 Windows 文档没有给出固定的绝对数据目录，只说首次运行会在数据目录下生成 `config/onebot.json`、`config/onebot_<uin>.json`、`config/runtime.json`，具体位置“见 WebUI / README”。
- 源码中 OneBot 配置路径是相对路径 `config/`，说明进程工作目录会影响配置落点。原生管理器应为每个 SnowLuma 实例指定独立工作目录（例如 `<实例目录>\snowluma`），但**必须真机验证**启动后配置确实写入该目录，且升级/重启不丢登录态。
- 官方 Windows 文档没有把“多开多个原生 QQ/SnowLuma 实例”写成受支持流程。Native hook、单个 QQ 桌面进程、单交互桌面和 WebUI 端口都可能冲突。第一阶段应明确只支持**单机器人实例**；多实例必须另行设计并验证。

Windows 原生 OneBot 对接建议：

```text
AstrBot 原生进程（每实例独立 ASTRBOT_ROOT、端口）
    ws_reverse_host = 127.0.0.1
    ws_reverse_port = <实例端口>
    ws_reverse_token = <随机 token>

SnowLuma 原生进程（每实例独立工作目录、端口）
    networks.wsClients[0].url = ws://127.0.0.1:<实例端口>/ws
    networks.wsClients[0].accessToken = <同一个 token>
```

这与 Docker 版的关键差异只有网络地址：原生版不能用 Docker 服务名 `astrbot`，必须用 `127.0.0.1` 或明确配置的宿主地址。

### 1.3 A 版继续使用 noVNC：能做，但不是“官方原生能力”

#### 事实边界

- noVNC 本身只是 **HTML5 VNC 客户端**，不负责截屏，也不提供 Windows 上的虚拟桌面。
- noVNC 需要连接一个已存在的 VNC Server；对普通 VNC Server 还需要 `websockify` 把 WebSocket 流量转换成 VNC 的 TCP 流量。官方 noVNC README 明确给出 `novnc_proxy --vnc localhost:5901` 的架构。
- SnowLuma 官方 Windows 文档只支持在桌面 QQ 窗口扫码；**没有找到官方文档支持“在 SnowLuma WebUI 里显示 QQ 登录二维码”**。SnowLuma WebUI 的二维码功能是 TOTP 二次验证二维码，不是 QQ 登录二维码。
- 因此，A 版若继续提供 noVNC 链接，路径不是“SnowLuma 自带二维码”，而是必须部署 Windows VNC Server，把当前 Windows 桌面共享给 noVNC。官方推荐路径仍然是让用户直接看本机 QQ 窗口。

#### 可实施方案（可选增强）

1. 用户安装/启用 Windows VNC Server（例如 TightVNC/UltraVNC/TigerVNC，具体选型需单独做许可与安全评估）。
2. VNC Server **只监听 `127.0.0.1:5900`**，设置强随机密码；不要直接暴露 5900。
3. 管理器启动 `websockify`，只监听 `127.0.0.1:6081`，把 WebSocket 连接到 `127.0.0.1:5900`，同时托管 noVNC 静态文件。
4. 控制台只在受 token 保护的本地面板中展示 noVNC 链接；默认不使用公网 `0.0.0.0` 绑定。
5. 用户仍通过 QQ 官方窗口扫码；设备验证、短信、滑块等也只能在官方 UI 内完成。
6. 若 VNC Server 未安装或启动失败，界面必须优雅降级为“打开本机 QQ 窗口/手动扫码”，不能把 noVNC 当成唯一入口。

#### noVNC 方案的限制

- **共享整个交互式 Windows 桌面**，不是每个机器人独立虚拟屏幕；多个原生 QQ/机器人会出现在同一桌面里，无法提供当前 Linux 容器那种“一实例一 noVNC”的隔离。
- 用户可能通过 noVNC 看到 Windows 上的其他窗口和隐私内容；绝不能默认对公网开放。
- UAC 安全桌面、锁屏、切换用户等场景不一定能被普通用户态 VNC Server 捕获；遇到时仍需用户在本机完成。
- VNC Server 多为第三方软件，许可、安装器、杀毒软件误报、服务权限和自启动都是额外风险；不建议未经用户明确同意就捆绑安装。
- websockify/noVNC 也有各自许可证/署名义务；如果要捆绑，需要做许可证清单和源码/许可分发。
- 如果用户只需要“在同一台 Windows 上首次扫码”，直接看桌面 QQ 窗口最简单；noVNC 主要价值是让另一台设备通过浏览器远程看到 Windows 桌面，而不是 SnowLuma 专属登录页。

#### 建议的产品形态

A 版完成页同时给两个入口，按可用性显示：

- **打开 QQ 登录窗口（官方、默认）**：调用/聚焦桌面 QQ；说明用手机扫码。
- **高级：浏览器远程扫码（可选）**：仅当检测到 VNC Server + websockify 已配置时显示；链接必须受管理器 token 和回环绑定保护，并显示“会共享整个 Windows 桌面”的显著警告。

不应默认假设 noVNC 能显示“每个实例的独立二维码”。这一点需要用户确认是否接受。

### 1.4 A 版推荐架构与工作量

建议的单实例最小架构：

```text
QQBotStudio-Windows-Native.exe（管理器 + 本地 Web UI）
  ├─ AstrBot 原生进程（ASTRBOT_ROOT=<数据目录>\instances\<id>\astrbot）
  ├─ SnowLuma 原生进程（cwd=<数据目录>\instances\<id>\snowluma）
  ├─ 本机 NTQQ 桌面进程（用户自行安装/登录）
  └─ 可选：Windows VNC Server + websockify/noVNC
```

最低依赖：

- Windows 10/11 x64（官方 SnowLuma 正式资产为 win-x64）。
- AstrBot Python 3.12+；推荐由管理器下载固定版本 uv/Python 并安装官方 AstrBot 包。
- SnowLuma 完整版 win-x64 包（内置 Node.js；若用 lite 包则要求 Node 22/24）。
- 官方 Windows NTQQ 客户端；需要用户先安装、可正常启动。
- 浏览器（Windows 自带 Edge 即可）。

工作量初估（工程估算，未经真机验证）：

| 阶段 | 内容 | 相对工作量 |
|---|---|---|
| POC | 原生启动 AstrBot + SnowLuma，固定端口，手工扫码，确认 OneBot | 中 |
| 单实例产品化 | 依赖引导、进程守护、日志/健康检查、配置生成、端口分配、失败恢复 | 高 |
| noVNC 可选增强 | VNC Server 检测/启动、websockify、noVNC 静态资源、权限/许可/安全 | 高 |
| 多实例 | QQ 多开、桌面隔离、SnowLuma 配置隔离、端口与升级策略 | 很高/当前不建议承诺 |

结论：A 的“单实例纯原生”真实可行；A 的“与 Linux 版等价的完整多实例 + 每实例 noVNC”当前不具备官方能力支撑，必须先做真机 POC 和用户取舍，不能按 Docker 版经验直接重写。

---

## 2. B：Docker 版

### 2.1 推荐方案：保留 Docker Desktop + WSL2 + Linux containers

这是**最稳定、改动最小**的方案，也是当前 Windows 初版已经采用的方向：

- 复用现有 Python 标准库管理服务、静态向导/控制台、Docker Compose 生成器、镜像节点、实例和更新逻辑。
- 保留 AstrBot 官方 Linux 镜像 `soulter/astrbot:latest` 与 SnowLuma 官方 Linux 镜像 `motricseven7/snowluma:latest`。
- AstrBot WebUI `6185`，OneBot 反向 WS `6199`；SnowLuma WebUI `5099`、noVNC `6081`、OneBot `3000/3001`；容器内 SnowLuma 仍连 `ws://astrbot:6199/ws`。
- Docker Desktop 负责把 Linux 容器端口映射到 Windows 宿主；用户仍用 noVNC 完成初次 QQ 扫码和官方验证。
- 需要 Docker Desktop 使用 **Linux containers**，不是 Windows containers。
- WSL2 默认后端可以安装在一个普通用户环境；官方当前安装文档对 WSL2 系统要求包含 Windows 10/11 的虚拟化、SLAT、8GB 内存等，具体版本以安装时 Docker 官方页面为准。
- 这条路仍是“在 Windows 上跑 Linux 容器”，不满足“绝对没有 Linux 内核”的字面要求，但满足用户“越简单越好、稳定少改”的新边界。

### 2.2 如果用户要求“不依赖 WSL”：Hyper-V 或 Docker VMM

截至本次调研的 Docker 官方文档，Docker Desktop for Windows 支持三种 Linux containers 后端：WSL 2、Hyper-V、Docker VMM（Beta）。

- **Hyper-V 后端**
  - 只能使用 Docker Desktop 的 all-users 安装模式，需要管理员权限。
  - 官方系统要求面向 Windows 10/11 Pro、Enterprise、Education；Windows Home 不应承诺 Hyper-V 后端。
  - 仍然运行 Docker 的 **Linux VM**；不使用 WSL，但并没有消除 Linux。
  - 对 SnowLuma 容器而言仍是 Linux QQ + Xvfb + noVNC。
- **Docker VMM（Beta）**
  - 是 Docker 自己的虚拟机管理器，Windows 上作为 WSL 2 的替代，仍是“运行 Linux VM 的容器引擎”。
  - 本次未找到足够证据证明本项目所需端口、bind mount、SYS_PTRACE/seccomp、noVNC 在 Docker VMM 上全部等价可用；**不要默认承诺生产支持**。
- 结论：如果用户只是不想装 WSL，可在 Pro/Edu/Enterprise 机器上评估 Hyper-V；如果要求完全无 Linux，则此方案不满足。

### 2.3 Windows containers：当前不建议

真正的 Windows containers 与本项目官方镜像不兼容，原因包括：

1. AstrBot 官方镜像是 Linux 镜像；没有看到官方 Windows 镜像。自己构建 Windows 版 AstrBot 需要 Windows 基础镜像、全部依赖的 Windows wheels/原生库和新的 CI，属于独立大工程，且未经官方支持。
2. SnowLuma 官方 Windows 方案依赖对交互式桌面 `QQ.exe` 进行 native hook 注入。标准 Windows 容器通常是 Server Core/Nano Server 类无交互桌面环境，无法可靠运行官方 QQ GUI、显示扫码窗口、完成设备验证，也没有现成的容器内 noVNC 等价方案。
3. Docker Desktop 的 Windows containers 需要 Windows 10/11 Pro/Enterprise，且容器 OS 版本要与宿主匹配；这与普通用户的一键体验冲突。
4. 虽然 Linux 侧的 `SYS_PTRACE`/`seccomp=unconfined` 不是 Windows containers 的同一概念，但这不能反推 SnowLuma 能在 Windows 容器里注入 QQ。

因此，B 版应明确使用 Docker Desktop 的 Linux engine；不要把“切换到 Windows containers”作为卖点或回退路径。

### 2.4 远程 Linux Docker

可以连接远程 Linux Docker daemon，但这会重新引入 Linux 主机、网络安全、TLS/SSH、镜像与 volume 位置、noVNC 端口暴露等复杂度，不符合“Windows 本机一键、越简单越好”。仅作为高级用户选项记录，不建议放入 B 版默认流程。

### 2.5 B 版结论与工作量

- 推荐 B 版 = 现有 Docker Compose 功能 + 一个 Windows 管理 EXE。
- 工作量主要是：EXE 打包、Docker Desktop 检测/引导、Windows 防火墙提示、端口/路径和日志适配、Release CI；**不需要重写 AstrBot/SnowLuma 集成核心**。
- 风险显著低于 A 版；真实 Windows + Docker Desktop + 镜像拉取 + noVNC 扫码仍需最终 E2E。

---

## 3. 两个 EXE 的共享架构与发布流程

### 3.1 建议资产与命名

同一个 GitHub Release 中提供（名称可再定，但必须固定且可脚本校验）：

```text
QQBotStudio-Windows-Docker-<version>.exe
QQBotStudio-Windows-Native-<version>.exe
SHA256SUMS.txt
```

可选压缩包/安装器：

```text
QQBotStudio-Windows-Native-Setup-<version>.exe
QQBotStudio-Windows-Docker-Setup-<version>.exe
```

说明：“EXE”建议是管理器/启动器，不应承诺所有 Python/Node/AstrBot/SnowLuma 依赖都被无损塞入一个 PyInstaller 文件。首次运行下载固定版本官方组件是更可控的方案；如果要离线包，再单独发布体积较大的离线资产并做许可证审查。

### 3.2 代码复用边界

建议抽象一个运行时后端接口，UI 和大部分业务核心共用：

```text
共享：
  qqbot_studio/static/*（向导、控制台、助手页面）
  server.py / API 路由 / token 鉴权 / state 格式
  configgen 的数据模型、端口分配、提示词、AstrBot Persona API
  store / secrets 权限模型 / 日志和任务状态
  tests（平台无关部分）

Docker 后端：
  Docker CLI/Compose 检测、镜像、容器、volume、网络、noVNC URL
  可最大程度沿用当前 Linux/Windows Docker 版

Native 后端：
  AstrBot/SnowLuma/Node/Python 进程发现、启动、停止、重启、健康检查
  本机 NTQQ 检测/聚焦、VNC/noVNC 可选桥接
  每个实例独立工作目录和端口块
```

前端不能写死“Docker 地址”或“Ubuntu 文案”；`/api/state.requirements.os` 和 edition/capability 字段应让同一套页面切换文案和能力提示。能力差异（例如原生版暂不支持多实例、noVNC 可能不可用）应返回明确字段，不伪造成功。

### 3.3 数据隔离和端口

- Docker 版数据：`%LOCALAPPDATA%\QQBotStudio\Docker\data`（名称可调整）。
- Native 版数据：`%LOCALAPPDATA%\QQBotStudio\Native\data`（名称可调整）。
- 两者**不得共用** `studio.json`、实例目录、Docker volume 名、AstrBot 数据根或 SnowLuma 工作目录；升级/卸载也不得互相删除。
- 两版默认管理端口应不同（例如 Docker `8765`、Native `8766`），避免同时运行冲突；向导展示实际端口，不靠猜。
- Docker 版实例仍使用每实例三个端口块（AstrBot/noVNC/SnowLuma 相关端口）；Native 版需要单独端口规划，至少 AstrBot WebUI、AstrBot OneBot、SnowLuma WebUI、SnowLuma OneBot/可选 noVNC 各不相同，全部先做占用检测。
- 原生版 native OneBot 用 `127.0.0.1`；Docker 版 SnowLuma→AstrBot 用服务名 `astrbot`。这是两个 edition 必须区分的配置分支。

### 3.4 同一版本线与同步策略

用户要求“每次 Linux 更新同步 Windows”，当前“Linux 主仓库 + 独立 Windows 仓库手工 sync”容易漂移。建议二选一：

1. **推荐：单仓库双目标构建**
   - 共享 `qqbot_studio/` 和静态资源；
   - 增加 Windows Docker 入口和 Windows Native 入口；
   - 同一 tag 下 CI 同时产出 Linux、Windows Docker、Windows Native；
   - 从根上消除“Windows 落后 Linux 几个版本”。
2. 如果保留独立 Windows 仓库：
   - Linux 主仓库打 tag/发布成功后，用 repository dispatch 或定时 CI 把**同一个 commit 的共享核心**同步到 Windows 仓库；
   - Windows 仓库 CI 必须对比 shared-core 校验和/上游 commit；
   - Windows Release 里记录 `upstream_linux_commit`；
   - 若 Windows 专属后端暂时不支持某新功能，用 capability flag 降级，但版本号仍需一致，并在 release notes 标注。

Release 门禁建议：

- Linux 全量 unittest/py_compile、静态 JS 检查通过。
- Windows Docker EXE 构建通过；至少 `--help/--status` 和服务启动 smoke。
- Windows Native EXE 构建通过；至少 AstrBot/SnowLuma 依赖检查、进程启动/停止 smoke。
- 两 EXE 版本号、Git commit、上游 AstrBot/SnowLuma 版本一致。
- 打包 SHA256；有签名条件则做代码签名。
- 真实 Windows E2E 未通过时，Release notes 必须明确单列“未验证”，不能写“Windows 全平台稳定”。

### 3.5 版本兼容矩阵

Native 版比 Docker 版更依赖上游版本：

- AstrBot：固定并记录验证过的版本区间（例如 v4.29.x）。
- SnowLuma：固定并记录验证过的 Windows Release 版本与兼容 QQ NT 版本。
- SnowLuma 自动更新 QQ 后 hook 失效，是 Native 版必须处理的现实问题；管理器应提供“检测到 QQ 版本不兼容→暂停并给出手动修复说明”，而不是无限重启。
- Docker 版只需记录镜像 tag（`latest` 实际 digest 最好锁定或记录）。

---

## 4. 必须让用户确认的取舍

| # | 决策点 | 选项与影响 |
|---|---|---|
| 1 | A 版 noVNC 怎么做 | **官方原生窗口（简单稳定，推荐）** vs **额外 Windows VNC Server + websockify/noVNC（浏览器可看整个 Windows 桌面，许可/安全/多实例隔离代价高）**；SnowLuma WebUI 并未提供可替代的 QQ 登录二维码 |
| 2 | A 版是否支持多实例 | 单实例 POC 先落地 vs 多实例完整承诺（当前证据不足，可能需每实例独立 Windows 会话/VM，工作量大幅增加） |
| 3 | A 版 noVNC 是否捆绑 | 用户自行安装/授权 VNC Server vs 项目捆绑第三方软件（需重新评估许可证、杀软误报和分发义务） |
| 4 | A 版组件获取方式 | 首次联网下载官方 AstrBot/SnowLuma（较小、许可证更清晰） vs 离线全内置（EXE/安装包大，AstrBot AGPL 与 SnowLuma 再分发条款需确认） |
| 5 | QQ 安装责任 | 用户自行从腾讯安装官方 NTQQ vs 项目尝试自动下载安装（不建议，版本/许可/安全不可控） |
| 6 | A 版是否接受真实风控风险 | 原生 native hook/进程注入可能触发 QQ 风控、杀软误报或版本升级失效；不能向用户承诺稳定不封号 |
| 7 | B 版 Docker 后端 | 默认 WSL2 Linux containers（最简稳定） vs Hyper-V 无 WSL（需 Pro/Edu/Enterprise、管理员、仍是 Linux VM） vs Docker VMM Beta（不承诺生产） |
| 8 | B 版是否支持 Windows Home | WSL2 路径需按 Docker 当前官方要求装机确认；Hyper-V 不承诺 Home；不要用 Windows containers |
| 9 | 两 EXE 的发布节奏 | 同一 Release、同一版本号、每次 Linux 更新同步构建（建议） vs 各自独立版本（会导致支持矩阵和文档复杂化） |
| 10 | 数据目录/端口/共存 | 建议 Native/Docker 两个独立数据根和默认管理端口；是否能同时运行需用户确认 |
| 11 | 签名与分发 | 是否购买 Windows 代码签名证书；未签名 EXE 会触发 SmartScreen，需要用户明确接受“更多信息→仍要运行”的引导 |
| 12 | 商业/再分发许可 | SnowLuma 为 source-available 非商业许可；商业使用/公开修改分发需事先书面授权。发布含 SnowLuma 的离线包前必须由用户确认许可策略 |

---

## 5. 明确的不确定性与未验证项

以下**不能**在未做 Windows 真机 E2E 前写成“已验证支持”：

1. AstrBot v4.29.x 在目标 Windows 10/11 版本上完整依赖安装、WebUI、插件、Persona API、OneBot 反向 WS 的真实运行。
2. SnowLuma v2.2.1 win-x64 在具体 QQ NT 版本上的 hook 注入、扫码、设备/短信验证、登录态保持。
3. SnowLuma 原生 Windows 的精确数据目录、升级后配置/登录态保留、多实例和多账号行为。
4. Windows VNC Server 方案对 UAC 安全桌面、锁屏、多显示器、QQ 扫码/验证窗口的实际可用性；也未验证 websockify/noVNC 在 Windows 上的自动启动与端口冲突。
5. Docker Desktop + WSL2 以及 Hyper-V/Docker VMM 下，`SYS_PTRACE`、`seccomp=unconfined`、bind mount、端口转发和 noVNC 的完整等价性。
6. Windows Defender/杀软对 SnowLuma hook、PyInstaller EXE、VNC 组件、websockify 的拦截行为。
7. Windows 代码签名、SmartScreen、防火墙规则、UAC 提权与普通用户运行权限的最终体验。
8. AstrBot/SnowLuma 官方 Release 资产名或文档在后续版本变化；实现时应做资产存在性检查，而不是凭文档猜测。

---

## 6. 参考来源（官方优先）

### AstrBot

- 官方脚本部署（明确支持 Windows PowerShell）：<https://docs.astrbot.app/deploy/astrbot/package.html>
- 官方源码部署（Windows venv、`python main.py`）：<https://docs.astrbot.app/deploy/astrbot/cli.html>
- 官方桌面客户端：<https://docs.astrbot.app/deploy/astrbot/desktop.html>
- 官方 Windows 安装脚本：<https://docs.astrbot.app/install.ps1>
- v4.29.3 `pyproject.toml`（Python >=3.12）：<https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/pyproject.toml>
- v4.29.3 路径实现（`ASTRBOT_ROOT`、`<root>/data`）：<https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/utils/astrbot_path.py>
- 官方 Docker 部署（Windows Docker Desktop 示例）：<https://docs.astrbot.app/deploy/astrbot/docker.html>

### SnowLuma

- 官方 Windows 手动部署（原生 Windows、NTQQ、无需 Docker/WSL、无需 VNC/noVNC）：<https://snowluma.github.io/zh/docs/guide/deploy/windows>
- 官方 Windows Docker Desktop 部署（Linux 镜像、WSL2、noVNC）：<https://snowluma.github.io/zh/docs/guide/deploy/windows-docker>
- 官方 v2.2.1 Release 与 Windows x64 资产：<https://github.com/SnowLuma/SnowLuma/releases/tag/v2.2.1>
- 官方 OneBot 对接说明（AstrBot `ws://astrbot:6199/ws`）：<https://snowluma.github.io/zh/docs/guide/connect-bot>
- 官方 OneBot 配置结构：<https://snowluma.github.io/zh/docs/guide/configuration>
- 官方 README/许可说明：<https://github.com/SnowLuma/SnowLuma/blob/v2.2.1/README.md>
- 源码路径：<https://github.com/SnowLuma/SnowLuma/blob/v2.2.1/packages/common/src/runtime.ts>
- 源码配置路径（相对 `config/`）：<https://github.com/SnowLuma/SnowLuma/blob/v2.2.1/packages/onebot/src/config.ts>

### Docker Desktop / Windows containers / noVNC

- Docker Desktop Windows 安装与后端要求：<https://docs.docker.com/desktop/setup/install/windows-install/>
- Docker Desktop WSL 2 后端：<https://docs.docker.com/desktop/features/wsl/>
- Docker Desktop Virtual Machine Manager（Hyper-V / Docker VMM）：<https://docs.docker.com/desktop/features/vmm/>
- Microsoft Windows containers 概览：<https://learn.microsoft.com/en-us/virtualization/windowscontainers/about/>
- noVNC README（HTML5 VNC 客户端、需 WebSocket 代理）：<https://github.com/novnc/noVNC>
- websockify README（WebSocket ↔ TCP 代理）：<https://github.com/novnc/websockify>
- TightVNC 许可说明（Windows VNC Server 许可/再分发需评估）：<https://www.tightvnc.com/licensing.php>

---

## 7. 给 Lead 的最后建议

1. **先让用户确认 A 版是“原生窗口扫码”还是“必须浏览器 noVNC”**。这不是纯 UI 选择：后者需要第三方 VNC Server + 桌面共享，且 SnowLuma WebUI 没有 QQ 登录二维码可替代。
2. **A 版先做单实例 POC，不承诺多实例/每实例 noVNC**；通过真实 Windows + NTQQ + SnowLuma 版本匹配测试后再定路线图。
3. **B 版按当前 Docker Desktop/WSL2 方案打包为第一个稳定 Windows EXE**；Hyper-V 仅作为“明确不要 WSL”的 Pro/Edu/Enterprise 可选后端，Docker VMM Beta 不作为默认。
4. **两个 EXE 同仓库、同 tag、同一 CI Release**；共享前端/核心，Docker 与 Native 用两个运行时后端。每次 Linux 更新必须触发 Windows 双版本同步构建与校验。
5. 所有发布说明明确：真实 Windows E2E、QQ 扫码、设备验证、原生 hook 兼容性未验证前，不得宣称生产可用。
