# QQ Bot Studio Windows 10/11 初始移植

> **状态：初始移植，未完成真实 Windows / Docker Desktop / WSL2 联调。**
>
> 本目录中的 PowerShell 脚本只在本仓库的 Ubuntu 开发环境中完成了文本检查、
> Python 辅助脚本编译和 Linux 回归测试；当前构建环境没有 Windows、Docker
> Desktop、WSL2、winget 或 PowerShell（pwsh），因此没有声称已完成真实联调。
> 不能把本文档理解为 Windows 生产可用承诺。详细未验证项见文末。

Windows 版尽量复用现有的：

- `qqbot_studio/` Python 标准库后端
- `qqbot_studio/static/` 同一套向导和控制台 UI
- Docker Compose 生成逻辑、镜像节点、部署任务和多实例数据模型

Windows 专有部分只放在 `windows/`：PowerShell 安装、启动、控制台和后台进程
管理。后台管理服务使用 `Start-Process` 隐藏窗口 + PID/日志 + HTTP health
check，不依赖 Linux 的 `screen`、systemd 或 `sudo`。

---

## 1. 文件与脚本

| 文件 | 作用 |
|---|---|
| `windows\install.ps1` | 检查 Windows/Python/WSL2/Docker Desktop/Compose/daemon，创建数据目录，首次配置并持久化 `studio.json`，可选项安装依赖，然后启动第一个实例 |
| `windows\start.ps1` | 等效 Linux `start.sh`：检查 Docker，确保隐藏后台管理服务运行，创建一个全新实例并打印部署向导 URL |
| `windows\console.ps1` | 等效 Linux `console.sh`：启动/复用后台管理服务，修复已有容器，打开多实例控制台，不创建新实例 |
| `windows\common.ps1` | 三个入口共享的 PowerShell 函数（进程、日志、health check、配置提示等） |
| `windows\studio_helper.py` | 极小的 JSON 桥，复用 `qqbot_studio` 的配置/建实例/容器修复逻辑；不负责启动后台服务 |

PowerShell 脚本用 Windows PowerShell 5.1 兼容语法编写，输出保持 ASCII，避免旧版
PowerShell 读取无 BOM UTF-8 脚本时的中文编码问题；中文说明以本 Markdown 为准。

---

## 2. 系统要求与首次安装

推荐：

- Windows 10 22H2 x64 或 Windows 11
- BIOS/UEFI 中开启 CPU 虚拟化（VT-x/AMD-V）
- WSL2
- Docker Desktop 4.x，必须使用 **Linux containers**
- Python 3.10+ x64（3.12 推荐）
- Windows PowerShell 5.1 或 PowerShell 7
- 至少 8 GB 内存；多实例/镜像构建建议 16 GB
- 普通桌面用户运行，不要长期在管理员 PowerShell 中运维；管理员只用于 WSL/防火墙等一次性操作

先确认 WSL2：

```powershell
wsl --status
wsl --update
wsl -l -v
```

如果没有 WSL，可在**管理员** PowerShell 中执行：

```powershell
wsl --install --no-distribution
wsl --update
```

按 Windows 提示重启。Docker Desktop 的“Use the WSL 2 based engine”应启用。
不需要，也不建议把 QQ Bot Studio 安装在单独的 WSL Linux 发行版里手工运行；
Windows 版是在 Windows 宿主上启动 Python，再调用 Docker Desktop 的 Linux 引擎。

手工安装 Python 和 Docker Desktop 的命令示例：

```powershell
winget install --id Python.Python.3.12 -e --source winget
winget install --id Docker.DockerDesktop -e --source winget
```

安装后启动一次 Docker Desktop，完成首次登录/许可/教程，确认托盘图标正常，并确认
它处于 Linux containers 模式：

```powershell
python --version
docker version
docker compose version
docker info
```

`install.ps1` 可以检查缺失项并在确认后用 `winget` 尝试安装 Python 3.12 和 Docker
Desktop；WSL2 涉及管理员权限和重启，脚本只检查并打印命令，不自动重启系统。
Docker Desktop 的许可协议由用户自行确认。

### 运行安装脚本

在仓库根目录打开普通 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\windows\install.ps1
```

如果系统策略阻止脚本，可使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\install.ps1
```

常用参数：

```powershell
.\windows\install.ps1 `
  -DataDir "$env:LOCALAPPDATA\QQBotStudio\data" `
  -Ip 0.0.0.0 `
  -Port 8765 `
  -PublicHost 192.168.1.20
```

| 参数 | 说明 |
|---|---|
| `-DataDir` | 数据目录；默认 `%LOCALAPPDATA%\QQBotStudio\data`。也可设置环境变量 `QQBOT_STUDIO_DATA_DIR` |
| `-Ip` | 管理服务监听 IP，默认 `0.0.0.0` |
| `-Port` | 管理服务端口，默认 `8765` |
| `-PublicHost` | 打印/打开链接时使用的 IP 或域名，不要带 `http://`、路径或端口 |
| `-InstallMissing` | 允许脚本不询问地用 winget 安装缺失的 Python/Docker Desktop；WSL 仍需手动处理 |
| `-AddFirewallRule` | 使用管理员权限创建管理端口的 Windows 防火墙入站规则（Private、LocalSubnet） |
| `-NoStart` | 只做检查和配置，不创建第一个实例 |
| `-NoOpen` | 不自动打开浏览器 |
| `-NonInteractive` | 首次运行不提问，使用参数/默认值。适合自动化；日常首次安装不建议 |

`install.ps1` 不会自动放开所有机器人 WebUI/noVNC 端口，避免默认扩大攻击面。
端口与防火墙见第 6 节。

---

## 3. 新建实例、控制台和首次配置

安装完成后会提示输入：

```text
管理服务监听 IP（默认 0.0.0.0）
管理控制台端口（默认 8765）
对外访问 IP/域名（用于打印浏览器链接）
```

这三项写入：

```text
%LOCALAPPDATA%\QQBotStudio\data\studio.json
```

字段包括 `bind_host`、`bind_port`、`public_host`、`first_run_complete` 和镜像节点。
之后再次运行启动脚本会复用保存值，不会重复询问。使用参数显式传入新值时，更新配置并
按需重启管理服务。

新建一个机器人实例：

```powershell
.\windows\start.ps1
```

指定名称：

```powershell
.\windows\start.ps1 -Name "我的第一个 QQ 机器人"
```

打开已有实例控制台：

```powershell
.\windows\console.ps1
```

只重启后台管理服务（不会创建新实例）：

```powershell
.\windows\console.ps1 -RestartService
```

> **重要：** 每次执行 `start.ps1` 都会创建一个**全新的隔离实例**，包括独立数据目录、
> Compose 项目名、容器名、网络和端口。新实例首次部署必须在 noVNC 中扫码登录 QQ。
> 管理已有机器人请使用 `console.ps1`。重启已有容器通常可复用 SnowLuma 保存的登录态，
> 但 QQ 登录过期、风控或触发验证时仍必须重新在 noVNC 中完成 QQ 官方验证。

修改监听参数会让后台管理服务在下次启动时检测到 host/port/runtime 文件不一致，
并重启服务；显式使用 `-RestartService` 可强制重启。正在运行的管理服务重启会中断
正在运行的部署任务；任务会标记为中断，通常可以从向导重试。

### 自定义数据目录

```powershell
$env:QQBOT_STUDIO_DATA_DIR = 'D:\QQBotStudioData'
.\windows\start.ps1
```

或：

```powershell
.\windows\start.ps1 -DataDir 'D:\QQBotStudioData'
```

不同数据目录会创建完全独立的实例集合。不要把数据目录放在只读网络共享上；OneDrive
同步目录也可能导致 Docker bind mount、SQLite/文件锁和登录态异常。优先使用本机
`%LOCALAPPDATA%` 或 `C:`/`D:` 的本地 NTFS 目录。

---

## 4. Windows 后台管理服务

Linux 版使用 `screen`/nohup；Windows 版不用它们，核心机制是：

1. `start.ps1` / `console.ps1` 检查配置和 Docker；
2. `Start-Process` 以隐藏窗口启动 `python -m qqbot_studio --host ... --port ... --data-dir ... --no-browser`；
3. 记录 PID 到 `server.pid`；
4. 标准输出与错误分别写入日志；
5. 轮询 `http://127.0.0.1:<port>/healthz`（若监听特定网卡则使用该网卡地址）；
6. 再次运行时读取 PID、校验进程命令行、读取 `server-runtime.json`，健康且配置一致则复用，
   否则安全停止本公司管理的旧进程并重启。

默认文件位置（以默认数据目录为例）：

```text
%LOCALAPPDATA%\QQBotStudio\data\
├── studio.json
├── server.pid
├── server-runtime.json
├── studio-server.log
├── studio-server.err.log
└── instances\
```

检查服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/healthz
Get-Content "$env:LOCALAPPDATA\QQBotStudio\data\server.pid"
Get-Process -Id (Get-Content "$env:LOCALAPPDATA\QQBotStudio\data\server.pid")
```

查看日志：

```powershell
Get-Content "$env:LOCALAPPDATA\QQBotStudio\data\studio-server.log" -Encoding UTF8 -Wait -Tail 50
Get-Content "$env:LOCALAPPDATA\QQBotStudio\data\studio-server.err.log" -Encoding UTF8 -Wait -Tail 50
```

PowerShell 脚本会为 Python 子进程设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`，
因此即使 Windows 传统 ANSI 代码页不能表示中文，管理服务也不会因重定向日志中的中文
诊断而崩溃；读取日志时请使用 `-Encoding UTF8`。

停止管理服务（Docker 容器默认继续运行）：

```powershell
$pidFile = "$env:LOCALAPPDATA\QQBotStudio\data\server.pid"
$serverPid = [int](Get-Content $pidFile -Raw)
Stop-Process -Id $serverPid
Remove-Item $pidFile -Force
```

优先使用：

```powershell
.\windows\console.ps1 -RestartService
```

Windows 初始版没有注册“开机自启”或 Windows 服务。Docker Desktop 和管理服务都需要
用户登录后手动启动；未来可增加计划任务。

---

## 5. 首次部署、扫码和 QQ 验证

`start.ps1` 会打印并尝试打开类似地址：

```text
http://<public_host>:8765/wizard?instance=bot-1
```

在部署向导中填写：

- QQ 号（只保存账号，不保存密码）
- DeepSeek API Key
- 人格提示词
- 风险确认

点击部署后，等待镜像拉取、容器启动和健康检查。完成后：

1. 在完成页打开 **noVNC**；
2. 输入向导显示的 VNC 密码；
3. 看到 SnowLuma 容器中的 QQ 官方登录窗口；
4. 用手机 QQ 扫描窗口中的 QR 码；
5. 如出现设备验证、短信/邮箱验证、滑块或安全验证，只能在 noVNC 中按 QQ 官方提示手动完成；
6. 登录态保存在该实例的 SnowLuma 数据目录中。

**每个新实例首次必须扫码登录。** 工具不会收集、代填或保存 QQ 密码，也不会绕过验证码、
设备验证或 QQ 风控。请使用小号并遵守 QQ 用户协议。

无法打开 noVNC 时，先确认 Docker Desktop 正在运行，再在向导/控制台检查该实例的
`novnc` 端口和 VNC 密码；对应端口也必须被 Windows 防火墙和（云主机场景下）云安全组允许。

---

## 6. 端口、Windows 防火墙与云安全组

### 6.1 端口

- 管理控制台：默认 `0.0.0.0:8765`。`0.0.0.0` 表示监听 Windows 所有网卡。
- AstrBot WebUI：宿主端口从 `6185` 起自动选择空闲端口。
- SnowLuma WebUI：宿主端口从 `5099` 起自动选择空闲端口。
- noVNC：宿主端口从 `6081` 起自动选择空闲端口。
- 每个实例的机器人端口由程序自动避让，保存在该实例的配置/部署状态中，不应猜测。
- 机器人 Compose 端口使用相同的 `BIND_ADDRESS`（默认 `0.0.0.0`）。因此不仅是管理服务，
  机器人 WebUI 和 noVNC 默认也可能对局域网/公网可达。

Docker Desktop 会把 Linux 容器的发布端口代理到 Windows 宿主。通常在本机访问
`http://127.0.0.1:<宿主端口>` 即可。检查监听：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
Invoke-RestMethod http://127.0.0.1:8765/healthz
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"
```

如果只想本机使用，建议首次配置时输入 `127.0.0.1` 而不是默认的 `0.0.0.0`：

```powershell
.\windows\console.ps1 -Ip 127.0.0.1 -Port 8765 -PublicHost 127.0.0.1 -RestartService
```

`public_host` 只决定打印/打开的 URL，不会改变监听地址。绑定回环地址时，应同时把
`public_host` 设为 `127.0.0.1`；否则链接可能指向一个未监听的局域网 IP。

此后可通过 VPN/Tailscale、Windows 端口转发或反向代理访问。

### 6.2 Windows Defender 防火墙和云安全组是两层

两者是不同层次，云服务器场景通常**两层都必须放行**：

1. **云平台安全组 / 轻量应用服务器防火墙**：在云厂商控制台配置，位于虚拟机之外。
   如果 Windows 是本地电脑，则没有这一层。
2. **Windows Defender 防火墙**：运行在 Windows 内部，过滤到达 Windows 宿主的入站流量。
   Docker Desktop 的 WSL2 后端通过 Windows 侧代理发布端口；只在 WSL 发行版里改 `ufw`
   不足以替代 Windows 防火墙规则。

安全建议：

- 默认不要新增“允许所有来源、所有端口”的规则；
- 管理端口优先只允许固定来源 IP、内网网段或 VPN 网段；
- Windows 防火墙规则使用 `Private` 配置文件，不要随意使用 `Public`；
- 云安全组来源不要填 `0.0.0.0/0`；
- noVNC 内有 QQ 登录界面和敏感操作，绝不能长期公开到互联网；
- 管理服务 token、AstrBot/SnowLuma 密码、VNC 密码不要发到公开聊天或截图；
- 本工具不是多租户安全边界。

`install.ps1 -AddFirewallRule` 只会为管理端口创建 Private + LocalSubnet 规则。等价的
管理员 PowerShell 命令：

```powershell
New-NetFirewallRule `
  -DisplayName 'QQ Bot Studio Management Console (TCP 8765)' `
  -Direction Inbound -Action Allow -Protocol TCP `
  -LocalPort 8765 -Profile Private -RemoteAddress LocalSubnet
```

删除规则：

```powershell
Remove-NetFirewallRule -DisplayName 'QQ Bot Studio Management Console (TCP 8765)'
```

部署完成后，从 `Get-NetTCPConnection` 或向导获得实际机器人宿主端口，例如 6185、
5099、6081 或其后续可用端口；只为实际需要且确认安全的端口建规则，例如：

```powershell
New-NetFirewallRule `
  -DisplayName 'QQ Bot Studio instance bot-1 web' `
  -Direction Inbound -Action Allow -Protocol TCP `
  -LocalPort 6185,5099,6081 -Profile Private -RemoteAddress 192.168.1.0/24
```

端口选择会跳过 Windows 上当时已被占用的端口。启动容器时仍可能出现 Docker 报
`port is already allocated`，尤其是 Docker Desktop 的代理监听与普通进程竞争时；
此时用 `Get-NetTCPConnection` 和 `docker ps` 排查，重启 Docker Desktop 或换端口。

### 6.3 云 Windows 主机额外注意

- 腾讯云 CVM/轻量、阿里云 ECS 等要先在安全组放行来源 IP；
- 还需要 Windows Defender 防火墙放行；
- 公网 IP 与 NAT 映射可能让 `public_host` 和实际绑定地址不同；用 `public_host` 只影响
  打印/打开的 URL，不改变监听地址；
- 云主机上不要把管理端口和 noVNC 对 `0.0.0.0/0` 开放。用完即关规则。

---

## 7. Docker Desktop + WSL2 的限制和注意事项

1. **只支持 Linux containers。** 本项目使用 Linux 镜像（AstrBot/SnowLuma），不能切到
   Windows containers。
2. Docker Desktop 的 Linux Engine 运行在自己的 WSL2 虚拟机中。退出 Docker Desktop 后
   容器会停止；`restart: unless-stopped` 只在 Docker Engine 存活时生效。
3. Docker Desktop 必须由当前 Windows 用户启动。初始版没有安装 Windows 服务、计划任务或
   Docker Desktop 自动启动。
4. WSL2/Docker Desktop 端口是发布到 Windows 宿主的，局域网访问入口是 Windows 主机
   IP + 发布端口；Windows Defender 防火墙在 Windows 侧生效。
5. bind mount 使用 Windows 路径。Docker Desktop 会把文件共享进 Linux VM，但 NTFS 的
   POSIX 权限/属主语义与 Linux 不同；脚本已做可写权限降级，真实文件所有权仍需实测。
6. Windows 没有 `os.getuid()`/`os.getgid()`。生成配置时回退为容器常用 UID/GID
   `1000:1000`；Docker Desktop 下的实际映射可能不同，SnowLuma 对挂载目录的写入权限
   属于未验证项。
7. Windows 上不再挂载 Linux `/etc/localtime`。Compose 已显式设置 `TZ=Asia/Shanghai`；
   这避免把 Docker Desktop VM 内的 `/etc/localtime` 误当 Windows 宿主时区文件。
8. `seccomp`、`cap_add`、`ulimits`、`shm_size` 在 Docker Desktop 上的实际效果可能因版本
   而异，未做 E2E 验证。
9. Windows ACL 不是 Linux 的 `0600`。默认数据目录位于当前用户 LocalAppData，通常只有
   该用户可访问，但本机管理员和具有相应权限的进程仍可能读取密钥文件。不要把本工具当
   多租户密钥保险箱。
10. WSL2 默认会占用较多内存。可在 `%UserProfile%\.wslconfig` 调整，但修改后需
    `wsl --shutdown`；注意这会停止 Docker Desktop 中的容器。
11. 不建议把数据目录放到 WSL 的 Linux 文件系统（`\\wsl$`）或跨文件系统 bind mount；
    优先本机 NTFS 路径。
12. Docker Desktop 的许可条款与免费使用范围由用户自行确认。

---

## 8. 已知限制与未验证项

本仓库当前构建环境是 Ubuntu，没有 Windows 主机，因此以下内容**没有真实联调通过**：

- `install.ps1` / `start.ps1` / `console.ps1` 在 Windows PowerShell 5.1 和 PowerShell 7
  下的实际语法/执行；
- winget 自动安装 Python/Docker Desktop；
- Docker Desktop 的 WSL2 backend、Linux engine、Compose v2 和端口发布；
- Windows Defender 防火墙规则创建与来源网段控制；
- `Start-Process` 隐藏窗口、重定向日志、PID 生命周期和 `server-runtime.json` 的竞态；
- `py.exe` 启动器与真实 `python.exe` PID 差异；
- 中文用户名/含空格路径、非系统盘、OneDrive/网络盘；
- noVNC 扫码、QQ 设备/短信验证、AstrBot/SnowLuma 实际登录；
- Windows 上的容器 bind mount 属主/写权限、seccomp/cap/ulimit 行为；
- 默认浏览器自动打开和托盘最小化体验；
- 真实 DeepSeek 部署、真实 QQ 消息收发和长时间稳定性。

此外，Windows 初始版仍有这些明确限制：

- **没有 Windows 一键自动更新。** 控制台的更新按钮在 Windows 后端会返回明确错误。
  手动更新命令见下一节。
- 没有系统服务/开机自启。
- 没有代码签名；脚本需用户自行检查。
- 没有自动为后续实例动态端口创建防火墙规则。
- 共享静态 UI 的“环境检查/安装命令”等帮助文案仍可能显示 Ubuntu/Linux 命令。
  本次移植没有修改 `qqbot_studio/static/`，避免与前端任务冲突；请以本 README 的
  Windows 命令为准。
- PowerShell 脚本只做静态/文本检查；未运行 PSScriptAnalyzer 或 Windows Pester。
- 当前没有真实 Docker Desktop 联调，所以不能宣称“已验证可在 Windows 运行”。

---

## 9. 手动更新

Windows 初始版没有运行 Linux `scripts/auto-update.sh` 的能力，也没有已验证的 Windows
自动重启脚本。建议手动：

```powershell
cd "C:\你的路径\QQ Ai bot"
git status
git pull --ff-only
.\windows\console.ps1 -RestartService
```

更新前后都不要删除数据目录。若控制台部分实例容器需要修复，`console.ps1` 启动时的
容器修复逻辑仍会复用现有 Python 后端执行。

---

## 10. 排错

### 10.1 PowerShell 禁止运行脚本

```powershell
Get-ExecutionPolicy -List
Set-ExecutionPolicy -Scope Process Bypass
```

只在当前 PowerShell 进程放行，或按组织策略使用签名脚本。

### 10.2 Python 未找到或版本过旧

```powershell
py -0p
where.exe python
python --version
```

安装 Python 3.10+ 后重新打开 PowerShell。安装时勾选 “Add python.exe to PATH”。
脚本也会检查 `%LOCALAPPDATA%\Programs\Python\Python3xx\python.exe` 和
`%ProgramFiles%\Python3xx\python.exe`。

### 10.3 Docker Desktop engine 未就绪

1. 启动 Docker Desktop，并等待托盘状态正常；
2. 完成登录、许可、首次教程；
3. 确认使用 Linux containers；
4. 在 PowerShell 中执行：

```powershell
docker context ls
docker version
docker compose version
docker info
```

若 WSL 异常：

```powershell
wsl --status
wsl --update
wsl --shutdown
```

`wsl --shutdown` 会停止 WSL 内的容器和 Docker Desktop 引擎，请在确认没有部署任务时执行，
随后重新启动 Docker Desktop。

### 10.4 管理页面打不开

1. 查看 health check：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/healthz
```

2. 查看 PID 和日志：

```powershell
$data = "$env:LOCALAPPDATA\QQBotStudio\data"
Get-Content "$data\server.pid"
Get-Content "$data\studio-server.log" -Encoding UTF8 -Tail 100
Get-Content "$data\studio-server.err.log" -Encoding UTF8 -Tail 100
```

3. 检查端口：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
```

4. 如果只听 `127.0.0.1`，其他设备无法访问；重新运行 `console.ps1 -Ip 0.0.0.0` 并检查
   Windows 防火墙。
5. 若 Windows 在云主机上，检查云安全组来源 IP。
6. 若 `public_host` 填错，重新运行：

```powershell
.\windows\console.ps1 -PublicHost 你的实际IP或域名 -RestartService
```

注意 `public_host` 只用于生成 URL，不等同于监听地址，也不会替代防火墙放行。

### 10.5 端口冲突 / 旧 PID

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
Get-Process -Id (Get-Content "$env:LOCALAPPDATA\QQBotStudio\data\server.pid")
.\windows\console.ps1 -RestartService
```

如果 PID 文件损坏或进程命令行无法验证为 QQ Bot Studio，`common.ps1` 会拒绝自动结束
该进程，而不会盲目杀 PID。此时根据 `Get-NetTCPConnection` 和
`Get-Process`/任务管理器人工确认。

### 10.6 Docker Compose 报端口已分配

```powershell
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"
Get-NetTCPConnection -State Listen | Sort-Object LocalPort
```

停止冲突实例/容器，或重新运行管理控制台让后端重新选择端口。Docker Desktop 的代理
有时在容器停止后短时间内仍占用端口，重启 Docker Desktop 可释放。

### 10.7 noVNC 能打开但没有 QQ 登录窗口

- 确认实例部署成功且 SnowLuma 容器为 running/healthy；
- 查看容器日志：

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
docker logs --tail 200 qqbot-bot-1-snowluma
```

实际容器名以 `docker ps` 为准（多实例为 `qqbot-bot-1-snowluma` 等）。
- 端口/防火墙错误时先修网络，不要反复重启容器掩盖问题；
- 扫码窗口在 noVNC Linux 桌面中，设备验证必须在该窗口完成；
- 工具不保存 QQ 密码，也不会绕过验证。

---

## 11. 开发与验证记录

在本 Ubuntu 开发环境中已运行：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile qqbot_studio/*.py
python3 -m py_compile windows/studio_helper.py
```

PowerShell 文件做了文本级检查：

- 三个入口均使用 `#requires -Version 5.1`；
- 后台服务使用 `Start-Process -WindowStyle Hidden`；
- 有 `server.pid`、`studio-server.log`、`studio-server.err.log` 和 `/healthz`；
- 默认管理绑定常量为 `0.0.0.0`，首次配置提示/持久化 IP、端口、public_host；
- 不依赖 `screen`、systemd 或 sudo；
- 所有 `.ps1` 保持 ASCII，降低 Windows PowerShell 5.1 编码风险。

当前环境没有 PowerShell，因此**没有执行** PowerShell AST 解析、PSScriptAnalyzer 或
Pester。第一次在 Windows 上运行时，应先阅读脚本并在测试账号/测试数据目录中验证。

下一步建议：

1. 在 Windows 11 VM 上以普通用户完整跑 `install.ps1 -> start.ps1 -> console.ps1`；
2. 用 Docker Desktop WSL2 backend 做一次真实部署和 noVNC 扫码；
3. 分别验证 `127.0.0.1` 与 `0.0.0.0`、Windows 防火墙规则和云安全组；
4. 验证 py.exe/真实 PID、重复启动、日志轮转、端口冲突和 `-RestartService`；
5. 增加 PowerShell Pester/PSScriptAnalyzer 测试；
6. 设计 Windows 专用安全自动更新脚本或隐藏更新按钮；
7. 增加计划任务/开机自启、ACL 加固和真实 QQ 长稳测试。

---

## 12. 安全免责声明

QQ Bot Studio 会运行第三方 QQ 协议端和 AI 服务，并可能暴露管理界面、WebUI、VNC 桌面
及其中凭据。默认 `0.0.0.0` 是为了方便局域网/云主机访问，也意味着不正确配置防火墙时会
增大攻击面。请使用 VPN/内网/固定来源 IP 限制访问，使用 QQ 小号，遵守腾讯 QQ 用户协议、
Docker Desktop 许可和当地法律。项目不收集或代填 QQ 密码，也不绕过 QQ 官方登录验证。
