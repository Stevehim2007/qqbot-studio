# 服务器部署与实战排错指南

面向 Ubuntu 云服务器/远程主机的专项排错文档。日常安装、使用和功能说明先看主项目 [`README.md`](../README.md)；本文只保留可复现的故障现象、原因和最短修复命令。

> 不要把 API Key、QQ 密码、短信验证码、SnowLuma/VNC 密码或含访问令牌的完整日志贴到公开 issue。本文命令默认项目目录为 `"/你的路径/QQ Ai bot"`，实际路径请替换。

## 目录

1. [快速定位表](#triage)
2. [Git 报 Unsupported SSL backend 'openssl'](#git-ssl)
3. [Docker 安装不完整/被取消](#docker-install-incomplete)
4. [Docker 已安装但报未检测到 Docker（v2.0.0 目录 bug）](#docker-false-negative)
5. [Docker daemon 启动失败](#docker-daemon-start)
6. [卸载并重装 Docker](#docker-reinstall)
7. [国内镜像拉取慢、镜像节点和离线镜像](#mirror)
8. [0.0.0.0、127.0.0.1 与 remote 访问](#remote-access)
9. [腾讯云安全组/轻量应用服务器防火墙](#tencent-cloud)
10. [Ubuntu ufw/firewalld 和 SSH 隧道](#firewall-ssh)
11. [旧版 v1 容器仍只监听 127.0.0.1](#old-v1-containers)
12. [多实例端口冲突、删除实例和残留端口](#port-conflict)
13. [控制台/API 状态读取失败](#console-status)
14. [SnowLuma 用户名 admin 密码错误/重置](#snowluma-password)
15. [QQ 增强插件、找图、点歌不工作](#plugin-issues)
16. [screen/后台管理服务和日志](#screen)
17. [Windows 与 Docker Desktop 未验证](#windows)
18. [最终验证清单](#final-checklist)
19. [AI 助手 B 公网拒绝连接](#assistant-public-diagnose)
20. [记忆模式误报与资源统计 Permission denied](#memory-stats-permission)

<a id="triage"></a>
## 1. 快速定位表

| 症状 | 先做 | 详细章节 |
|---|---|---|
| `git pull` 报 SSL backend | 清理 `http.sslBackend` / `GIT_SSL_BACKEND` | [Git SSL](#git-ssl) |
| 安装 Docker 取消后向导异常 | 确认 `docker compose version`、`docker info` | [Docker 安装不完整](#docker-install-incomplete) |
| Docker 命令都正常，启动器说没 Docker | 升级到 v2.0.1+，检查旧版 `doctor` 目录 | [Docker 误报](#docker-false-negative) |
| `Start request repeated too quickly` | 看 `journalctl -xeu docker.service` 的前置错误 | [Docker daemon](#docker-daemon-start) |
| 本机能 `curl`，外网访问不了 | 查监听地址、实际端口、云安全组、ufw/firewalld | [远程访问](#remote-access) / [腾讯云](#tencent-cloud) |
| 面板链接只到 `127.0.0.1` | 查旧容器和实例 `.env`/`config.json` | [旧容器](#old-v1-containers) / [端口冲突](#port-conflict) |
| `port is already allocated` | `docker ps` + `ss`，删除/重建实例 | [端口冲突](#port-conflict) |
| 控制台某个实例“状态读取失败” | `healthz`、管理日志、`docker info/ps` | [控制台状态](#console-status) |
| SnowLuma `admin` 密码错误 | 控制台重置密码，或手动重建 `webui.json` 后读新密码 | [SnowLuma](#snowluma-password) |
| 找图无结果、点歌没声音、概率不回复 | 查插件启用、provider、网络/音频文件 | [插件问题](#plugin-issues) |
| Windows EXE/ Docker Desktop 问题 | 去独立 Windows 仓库 | [Windows](#windows) |

<a id="git-ssl"></a>
## 2. Git 报 Unsupported SSL backend 'openssl'

### 症状

```text
fatal: Unsupported SSL backend 'openssl'. Supported SSL backends:
        gnutls
fatal: remote helper 'https' aborted session
```

### 原因

当前 Git/libcurl 只编译了 GnuTLS，但 Git 配置、环境变量或复制来的 `.gitconfig` 强制指定了 OpenSSL。这不是 GitHub 宕机或证书过期。

### 修复

```bash
git config --show-origin --show-scope --get-all http.sslBackend
env | grep -i GIT_SSL_BACKEND

git config --global --unset-all http.sslBackend 2>/dev/null || true
git config --local --unset-all http.sslBackend 2>/dev/null || true
sudo git config --system --unset-all http.sslBackend 2>/dev/null || true
unset GIT_SSL_BACKEND

git pull --ff-only
```

如果确认本机 Git 支持 GnuTLS，也可以临时指定：

```bash
git -c http.sslBackend=gnutls ls-remote https://github.com/Stevehim2007/qqbot-studio.git
```

仍失败时可临时改用 SSH remote：

```bash
git remote set-url origin git@github.com:Stevehim2007/qqbot-studio.git
ssh -T git@github.com
git push origin main
```

不要长期使用 `http.sslVerify=false`，它会关闭证书校验。

<a id="docker-install-incomplete"></a>
## 3. Docker 安装不完整/被取消

### 症状

安装 Docker 时取消、断网或只装了一半；向导看似能打开，但无法拉镜像或启动容器。

### 原因

旧版启动器没有在启动向导前完整检查 Docker CLI、Compose 和 daemon。当前版本会检查并以错误退出，不会假装成功。

### 修复

```bash
cd "/你的路径/QQ Ai bot"
./install.sh
```

如果安装器仍失败，手动安装后再验证：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# 注销/重新登录后
docker --version
docker compose version
docker info
```

`docker info` 报 `permission denied` 时，先注销重登录，或临时重新运行 `sudo -v` 后从终端启动项目。不要直接在网页里输入 sudo 密码。

<a id="docker-false-negative"></a>
## 4. Docker 已安装但报未检测到 Docker（v2.0.0 目录 bug）

### 症状

```text
install.sh：Docker、Docker Compose 与守护进程均已就绪。
start.sh：错误：未检测到 Docker。请先运行 ./install.sh 安装 Docker，或手动安装后重试。
```

但下面命令全部正常：

```bash
docker --version
docker compose version
docker info
systemctl is-active docker
```

### 原因

v2.0.0 使用不存在的目录 `~/.local/share/qqbot-studio/doctor` 作为子进程工作目录。Python `subprocess.run(cwd=...)` 在目录不存在时抛 `FileNotFoundError`，程序误判为“找不到 docker 命令”。

### 永久修复

升级到 v2.0.1 或更高版本，并重启管理服务：

```bash
cd "/你的路径/QQ Ai bot"
git pull --ff-only
git log -1 --oneline
python3 -m qqbot_studio --version
./console.sh --restart-service
```

目标修复提交包含或晚于：

```text
2f7eb49 fix: detect Docker from an existing working directory
```

### 旧版临时绕过

```bash
mkdir -p "$HOME/.local/share/qqbot-studio/doctor"
./start.sh --ip 0.0.0.0 --port 8765
```

使用自定义数据目录时，把 `doctor` 建在对应数据目录下，例如：

```bash
mkdir -p /data/qqbot-studio/doctor
./start.sh --data-dir /data/qqbot-studio --ip 0.0.0.0 --port 8765
```

如果真正的问题是 Docker 权限或 daemon，按下面章节处理，不要只反复创建目录。

<a id="docker-daemon-start"></a>
## 5. Docker daemon 启动失败

### 症状

```text
docker.service: Start request repeated too quickly.
docker.service: Failed with result 'exit-code'.
```

`Start request repeated too quickly` 只是 systemd 连续失败后的限流，不是根因。

### 第一步：看真正错误

```bash
echo '===== docker ====='
sudo journalctl -xeu docker.service -b --no-pager -n 200

echo '===== containerd ====='
sudo systemctl status containerd.service --no-pager -l
sudo journalctl -u containerd.service -b --no-pager -n 100

df -h
df -i
```

重点找：`failed to start daemon`、`containerd`、`iptables/nftables`、`overlay2`、`permission denied`、`no space left`。

### 5.1 `/etc/docker/daemon.json` 非法或含占位符

```bash
sudo cat /etc/docker/daemon.json 2>/dev/null || echo '没有 daemon.json'
sudo python3 -m json.tool /etc/docker/daemon.json 2>/dev/null
```

如果 JSON 不合法，或 `registry-mirrors` 仍是 `<你的专属镜像加速地址>` 这类占位符：

```bash
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak 2>/dev/null || true
sudo rm -f /etc/docker/daemon.json
sudo systemctl daemon-reload
sudo systemctl reset-failed docker.service docker.socket
sudo systemctl restart containerd
sudo systemctl start docker
sudo systemctl status docker --no-pager -l
```

Docker 恢复后，再只填真实可用的镜像地址，并先用 `python3 -m json.tool` 校验。

### 5.2 containerd 没运行

```bash
sudo systemctl enable --now containerd
sudo systemctl reset-failed containerd.service
sudo systemctl restart containerd
sudo systemctl start docker
```

containerd 仍失败时看：

```bash
sudo journalctl -u containerd.service -b --no-pager -n 100
```

### 5.3 旧 PID/socket

```bash
sudo systemctl stop docker.socket docker.service 2>/dev/null || true
sudo rm -f /var/run/docker.pid
sudo systemctl reset-failed docker.socket docker.service containerd.service
sudo systemctl start containerd
sudo systemctl start docker
```

### 5.4 iptables / nftables 冲突

只有日志明确出现 `iptables failed`、`nftables` 或 Docker 默认网桥创建失败时，才考虑切换 legacy 工具：

```bash
sudo apt install -y iptables
iptables -V
update-alternatives --display iptables
sudo update-alternatives --set iptables /usr/sbin/iptables-legacy
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
sudo systemctl restart containerd
sudo systemctl restart docker
```

不要一开始就关闭 Docker 的 iptables 功能，这会破坏容器网络隔离。

### 5.5 磁盘满或 Docker 数据损坏

```bash
df -h
df -i
```

如果明确是 `/var/lib/docker` 损坏，先备份，再移走旧数据（会丢失镜像、容器和 Docker 卷）：

```bash
sudo systemctl stop docker containerd
sudo mv /var/lib/docker /var/lib/docker.bak.$(date +%s)
sudo mkdir -p /var/lib/docker
sudo systemctl start containerd
sudo systemctl start docker
```

机器人实例数据通常是宿主机 bind mount，在 `~/.local/share/qqbot-studio/instances`，不随 `/var/lib/docker` 清理自动删除；仍建议先整体备份。

<a id="docker-reinstall"></a>
## 6. 卸载并重装 Docker

### 6.1 先停止容器并备份机器人数据

```bash
# 按需逐个停止实例容器；不要删除实例数据目录
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

tar czf ~/qqbot-studio-data-backup.tar.gz \
  -C ~/.local/share/qqbot-studio instances studio.json 2>/dev/null || true

sudo systemctl stop docker.socket docker.service containerd.service 2>/dev/null || true
```

### 6.2 判断包来源并卸载

```bash
dpkg -l | awk '/^ii/ && ($2 ~ /docker|containerd/) {print $2}'
```

Docker 官方源通常含 `docker-ce`：

```bash
sudo apt-get purge -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras
sudo apt-get autoremove --purge -y
```

Ubuntu 源通常含 `docker.io`：

```bash
sudo apt-get purge -y \
  docker.io docker-compose-v2 docker-compose docker-doc podman-docker
sudo apt-get autoremove --purge -y
```

清理 Docker apt 源（可选）：

```bash
sudo rm -f /etc/apt/sources.list.d/docker.list
sudo rm -f /etc/apt/keyrings/docker.gpg /etc/apt/keyrings/docker.asc
sudo apt-get update
```

只修复安装问题时建议保留 `/var/lib/docker`；彻底干净重装才执行：

```bash
sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker
```

### 6.3 重装并验证

```bash
cd "/你的路径/QQ Ai bot"
./install.sh

# 或 Ubuntu 软件包
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

注销重登录后验证：

```bash
docker --version
docker compose version
docker info
```

<a id="mirror"></a>
## 7. 国内镜像拉取慢、镜像节点和离线镜像

### 症状

拉 AstrBot/SnowLuma 镜像很慢、超时或拉取失败。

### 原因

Docker Hub/网络不稳定、镜像节点不可用，或节点可达但不包含目标 tag。

### 修复

1. 打开部署向导的 **Docker 镜像节点** 卡片，等待自动测速，优先选择低延迟且状态正常的节点。
2. 默认会依次尝试：DaoCloud、1ms.run、轩辕镜像、RatHub；选中节点失败后会回退到其他候选。
3. 自定义前缀只写主机和路径，不要写协议：

```text
registry.example.com/
```

4. 节点测速返回 HTTP 401 通常表示 Registry 端点可达且需要认证，不等于失败；最终以能否拉取目标镜像为准。
5. 镜像拉不下来时，可在能联网的机器保存再导入：

```bash
docker pull soulter/astrbot:latest
docker pull motricseven7/snowluma:latest
docker save -o qqbot-images.tar \
  soulter/astrbot:latest motricseven7/snowluma:latest

# 目标服务器
docker load -i qqbot-images.tar
```

6. 如果修改了 `/etc/docker/daemon.json` 的 `registry-mirrors`，必须使用真实地址、校验 JSON，并在 Docker 起不来时先移除该文件：

```bash
sudo python3 -m json.tool /etc/docker/daemon.json
sudo systemctl daemon-reload
sudo systemctl restart docker
```

回退日志和当前节点写在实例的 `config.json`，部署页也可查看逐镜像拉取日志。

<a id="remote-access"></a>
## 8. 0.0.0.0、127.0.0.1 与 remote 访问

### 先分清四件事

| 项目 | 作用 |
|---|---|
| 监听地址 `bind_host` | `0.0.0.0` 表示所有网卡；`127.0.0.1` 只允许本机 |
| 管理端口 `bind_port` | 默认 `8765`，不是机器人实例端口 |
| 实例三端口块 | AstrBot/SnowLuma/noVNC，程序在 `5000–10000` 内自动分配 |
| `public_host` | 只影响打印/打开的 URL，不改变监听地址 |

### 症状与修复

```bash
cd "/你的路径/QQ Ai bot"

# 只允许本机
./console.sh --ip 127.0.0.1 --port 8765

# 允许外网/局域网访问，必须配合安全组和防火墙
./console.sh --ip 0.0.0.0 --port 8765 --public-host 你的公网IP或域名
```

检查实际监听：

```bash
ss -ltnp 2>/dev/null | grep 8765
# 0.0.0.0:8765 或 *:8765 表示所有网卡；127.0.0.1:8765 只允许本机
```

服务器本机验证：

```bash
curl -I http://127.0.0.1:8765/healthz
```

如果本机正常、外部打不开，按顺序查：

1. `bind_host` 是否真的是 `0.0.0.0`；
2. 云安全组/轻量防火墙是否放行实际端口；
3. Ubuntu ufw/firewalld 和 Docker 发布规则；
4. `public_host` 是否填错（它只改链接，不改监听）；
5. 路由/NAT/公网 IP 是否正确。

实例的实际端口不要猜，查看：

```bash
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

`docker ps` 中若看到 `127.0.0.1:xxxx->...`，说明该容器只绑定回环。旧版 v1 容器不会被 v2 自动迁移，见 [旧容器](#old-v1-containers)。

<a id="tencent-cloud"></a>
## 9. 腾讯云安全组/轻量应用服务器防火墙

### 需要放行什么

| 用途 | 端口 |
|---|---|
| 管理控制台/部署向导 | 默认 `8765/tcp` |
| 每个实例 AstrBot/SnowLuma/noVNC | 以控制台和 `config.json` 中该实例的实际三端口为准，通常在 `5000–10000` |

不要再假设旧版的固定宿主机端口 `6185/5099/6081`；这些是容器内部端口，宿主机映射已按实例重新分配。

### CVM 安全组

1. 登录腾讯云控制台：<https://console.cloud.tencent.com/cvm>
2. 进入 **云服务器 → 实例**，确认地域和实例。
3. 打开 **安全组 → 修改规则/编辑规则**。
4. **入站规则 → 添加规则**：类型自定义，协议端口 `TCP:8765`，来源填自己的公网 IP/32（例如 `203.0.113.25/32`），策略允许。
5. 部署后按控制台显示的实际实例端口，仅按需添加对应 TCP 规则。
6. 临时测试才使用 `0.0.0.0/0`，测试后立即收紧。

### 轻量应用服务器 Lighthouse

1. 登录：<https://console.cloud.tencent.com/lighthouse>
2. 进入 **轻量应用服务器 → 服务器 → 防火墙**。
3. 添加规则：应用类型自定义、协议 TCP、端口 `8765`、来源固定 IP/32、策略允许。
4. 部署后按实际实例端口继续添加；不要一次开放整个端口段给公网。

如果服务器出站规则被改成白名单，还要允许 TCP 443 访问 Docker Hub、镜像站和 `api.deepseek.com`；腾讯云默认出站通常是允许全部。

<a id="firewall-ssh"></a>
## 10. Ubuntu ufw/firewalld 和 SSH 隧道

### ufw

```bash
sudo ufw status verbose
# 远程 SSH 中先放行 SSH，避免把自己锁在外面
sudo ufw allow OpenSSH
sudo ufw allow 8765/tcp
sudo ufw status numbered
```

部署后再按实例实际端口放行，例如：

```bash
sudo ufw allow 5000/tcp
sudo ufw allow 5001/tcp
sudo ufw allow 5002/tcp
```

**Docker 发布端口可能写入自己的 iptables NAT 规则，从而绕过部分 ufw 限制；云安全组/轻量防火墙才是最终公网边界。**

### firewalld

```bash
sudo firewall-cmd --state
sudo firewall-cmd --permanent --add-port=8765/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

### SSH 隧道（最安全）

服务器管理服务只绑定回环：

```bash
./console.sh --ip 127.0.0.1 --port 8765
```

自己电脑执行：

```bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器IP
```

本机打开 `http://127.0.0.1:8765/console`。要访问某个实例的 AstrBot/SnowLuma/noVNC，按 `config.json` 中该实例实际端口追加 `-L`。例如实例端口为 `5000/5001/5002`：

```bash
ssh -N \
  -L 8765:127.0.0.1:8765 \
  -L 5000:127.0.0.1:5000 \
  -L 5001:127.0.0.1:5001 \
  -L 5002:127.0.0.1:5002 \
  用户名@服务器IP
```

<a id="old-v1-containers"></a>
## 11. 旧版 v1 容器仍只监听 127.0.0.1

### 症状

旧容器 `qqbot-astrbot`、`qqbot-snowluma` 仍占用旧端口，`docker ps` 显示绑定 `127.0.0.1:5099/6081/6185`；v2 控制台不管理它们。

### 原因

v1 单实例部署使用固定容器名/端口；v2 改成每实例独立名称、网络和三端口块，不会自动接管旧容器。

### 修复

先确认旧实例是否还需要保留，备份宿主机数据：

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
ls -la ~/.local/share/qqbot-studio
# 备份旧数据目录后再操作
```

确认旧容器不再需要后删除容器（此命令不删除宿主机 bind mount 数据）：

```bash
docker rm -f qqbot-astrbot qqbot-snowluma 2>/dev/null || true
```

然后用当前版本重新创建实例、扫码并验证。不要为了刷新端口直接删除正在使用的数据目录。

<a id="port-conflict"></a>
## 12. 多实例端口冲突、删除实例和残留端口

### 症状

- 控制台提示端口已被占用；
- Docker 报 `port is already allocated`；
- 三个面板中某个打不开或指向了其他实例。

### 原因

旧实例容器、旧 v1 容器、手工进程、Docker Desktop 代理或上次异常退出仍占着端口。v2 新实例会在 `5000–10000` 分配三端口块，但旧实例不会自动迁移旧端口。

### 修复

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
ss -ltnp 2>/dev/null | grep -E ':(8765|5000|5001|5002)\b'
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
```

处理顺序：

1. 控制台删除不再需要的实例：第一次点击确认，第二次实际删除；删除会清理容器和实例数据，不可恢复。
2. 确认是旧 v1 容器时，按 [上一节](#old-v1-containers) 备份后删除。
3. Docker Desktop/残留代理导致端口短时间不释放时，重启 Docker Desktop 或 Docker daemon；Linux 服务器先确认不会影响其他容器。
4. 重新创建实例，让程序重新分配新的连续端口块。
5. 若只是想在保留数据的情况下修复旧实例端口，先在停机状态完整备份实例目录，再在维护窗口调整/重建；不要在实例运行中手改 `.env`/`compose.yml`。

管理服务默认 `8765` 由控制台预留，不应手动改实例配置去占用它。

<a id="console-status"></a>
## 13. 控制台/API 状态读取失败

### 症状

- 控制台实例卡片显示“实时状态读取失败”或状态未知；
- `/api/state` 返回 `status_error`；
- 整个控制台打不开。

### 原因

可能分别是管理服务未启动、Docker daemon 不可用、单个容器异常、实例目录/权限损坏、AstrBot API 未就绪，或浏览器打开了旧服务端口。

### 修复

```bash
# 管理服务
curl -I http://127.0.0.1:8765/healthz
tail -n 150 ~/.local/share/qqbot-studio/studio-server.log

# Docker 和实例容器
docker info
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# 实例元数据和端口
cat ~/.local/share/qqbot-studio/instances/bot-1/instance.json
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
```

管理服务自身异常时可安全重启管理服务：

```bash
./console.sh --restart-service
```

若只是某个实例状态失败：

- 看该实例容器日志和 `docker ps`；
- 确认实例端口和容器名没有和别的实例冲突；
- 刷新控制台，必要时重启该实例容器；
- 不要根据一个失败状态直接删除数据目录。先备份，再执行修复/删除。

`/api/*` 需要页面注入的访问令牌；不要用浏览器直接去掉/伪造 token 调接口。控制台打不开但 `/healthz` 正常时，查看反向代理、端口转发和浏览器地址。

<a id="snowluma-password"></a>
## 14. SnowLuma 用户名 admin 密码错误/重置

### 症状

SnowLuma WebUI 登录失败，完成页显示密码无效，日志中的一次性密码已过期。

### 原因

用户名固定是 `admin`；新版可能使用 bootstrap 密码，旧实例/首次登录后手工改密/容器重启轮换都可能导致完成页密码过期。

### 优先修复：控制台重置密码

在控制台对应实例的 SnowLuma 卡片点击 **重置密码**，第一次进入确认状态，第二次执行。成功后用户名仍是 `admin`，卡片会显示新密码。

### 命令行读取当前一次性密码

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs --tail 200 qqbot-bot-1-snowluma 2>&1 | grep -i -A3 -B3 "initial credentials\|初始登录"
```

常见格式：

```text
initial credentials: user=admin password=xxxxxxxxxxxxxxxx
```

### 手动重置 SnowLuma WebUI 登录

先备份再删除 `webui.json`，然后重启容器并读新一次性密码：

```bash
find ~/.local/share/qqbot-studio/instances -path '*/data/snowluma/config/webui.json' -print

# 以 bot-1 为例
cp ~/.local/share/qqbot-studio/instances/bot-1/data/snowluma/config/webui.json \
   ~/.local/share/qqbot-studio/instances/bot-1/data/snowluma/config/webui.json.bak
rm ~/.local/share/qqbot-studio/instances/bot-1/data/snowluma/config/webui.json
docker restart qqbot-bot-1-snowluma
sleep 5
docker logs --tail 200 qqbot-bot-1-snowluma 2>&1 | grep -i -A3 -B3 "initial credentials"
```

登录用户名始终是 `admin`。删除 `webui.json` 只重置 SnowLuma WebUI 登录，不是删除 QQ 数据目录；QQ 登录态是否保留取决于 SnowLuma 版本，必要时在 noVNC 重新验证。

<a id="plugin-issues"></a>
## 15. QQ 增强插件、找图、点歌不工作

### 先确认插件状态

在控制台实例展开 **插件、记忆与资源 → 插件管理**，或登录 AstrBot WebUI → 插件，确认 `qqbot_enhancer` 已启用。AstrBot 停止时控制台只能显示本地文件状态，无法线上启停。

### 非 @ 消息不回复

这是设计行为：非 @ 群消息按 `reply_probability`（默认 0.12）和群/用户冷却随机回复；改成 `0` 后只有 @ 和私聊必回。检查：

- 是否真的 @ 了机器人（@全体不算）；
- 是否在 AstrBot 的指令前缀/唤醒范围内；
- 消息是否太短、纯图片/纯表情被过滤；
- 是否刚触发过群/用户冷却；
- AstrBot provider 和 DeepSeek Key 是否可用。

### 找图无结果

```text
/找图 橘猫
/搜图 千早爱音
```

自然语言找图只在 @ 或私聊触发；非 @ 群聊即使概率命中也不会自动找图。找图依赖 Bing Images/百度图片页面结构，可能因地区、反爬或改版失败；图片下载失败会尝试候选/远程 URL 兜底。检查 `image_search_enabled`、网络出站 443 和插件日志。不要输入受版权/隐私限制或违法内容。

### 图片识别不回复或答非所问

图片理解依赖可用的多模态 provider 或图片转述 provider。`deepseek-flash` 是否支持图片输入以实际 API 为准；在 AstrBot WebUI 配置 `vision_provider_id`/转述 provider，并检查模型是否实际接收到图片。

### 点歌没有声音

本地点歌只读取实例插件目录直属 `songs/` 文件，不调用 TTS/音乐生成；仓库不附带歌曲。放入你有权使用的音频：

```bash
SONGS="$HOME/.local/share/qqbot-studio/instances/bot-1/data/astrbot/plugins/qqbot_enhancer/songs"
mkdir -p "$SONGS"
cp "/你的授权音频.wav" "$SONGS/"
```

支持 `.mp3/.wav/.ogg/.flac/.m4a/.aac`，默认单文件上限 20 MB；部分 QQ 适配器可能只可靠支持 WAV，真实播放需按 AstrBot/SnowLuma 日志确认。

`/option`、`/插件用法`、`/用法` 可显示本插件用法和已加载插件元数据，但不会猜测其他插件的子命令。完整配置见 [`plugin-qqbot-enhancer.md`](plugin-qqbot-enhancer.md)。

<a id="screen"></a>
## 16. screen/后台管理服务和日志

管理服务默认在 screen 会话 `qqbot-studio-server` 中运行；受限系统/沙箱会降级到 nohup。查看：

```bash
screen -ls
screen -r qqbot-studio-server
# screen 内脱离：Ctrl+A，然后按 D
tail -f ~/.local/share/qqbot-studio/studio-server.log
```

如果 screen 不可用或 `/run/screen` 只读，启动器可能打印警告并切到 nohup；此时以 `studio-server.log` 和 `server.pid` 为准。不要用 `pkill -f python3` 盲杀所有 Python 进程，先确认 PID/命令行属于本项目。

关闭浏览器不会停止后台部署；关闭管理服务会中断部署任务（任务会标记为中断，通常可以重试）。Docker 容器不会因为关闭管理服务自动停止。

<a id="windows"></a>
## 17. Windows 与 Docker Desktop 未验证

Windows 10/11 是**独立项目**，不是本仓库的 Linux 脚本包装，也不能共用 Linux 数据目录/自动更新：

- Windows 独立仓库：<https://github.com/Stevehim2007/qqbot-studio-windows>
- EXE 构建 workflow：<https://github.com/Stevehim2007/qqbot-studio-windows/actions/workflows/windows-build.yml>
- 主仓库 [`windows/README.md`](../windows/README.md) 是早期移植资料/开发记录，正式 Windows 入口以独立仓库为准。

Windows 排错先确认 Docker Desktop 已启动、使用 **Linux containers**、WSL2 backend 正常：

```powershell
wsl --status
docker context ls
docker version
docker compose version
docker info --format "{{.OSType}}"
```

当前没有完成真实 Windows 10/11 + Docker Desktop + EXE 的完整 E2E；不能把 Linux 主项目的结果外推为 Windows 生产可用。Windows 防火墙和云安全组是两层，具体步骤和未验证项以独立 Windows 仓库 README 为准。

<a id="final-checklist"></a>
## 18. 最终验证清单

```bash
cd "/你的路径/QQ Ai bot"

# 代码和版本
python3 -m qqbot_studio --version
git status --short --branch

# Docker
docker --version
docker compose version
docker info

# 管理服务
curl -I http://127.0.0.1:8765/healthz
screen -ls
tail -n 100 ~/.local/share/qqbot-studio/studio-server.log

# 多实例和实际端口
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

最后确认安全组/防火墙只放行实际需要的来源 IP 和端口；不要在公开网络暴露 noVNC。

<a id="assistant-public-diagnose"></a>
## 19. AI 助手 B 公网拒绝连接

### 症状

配置了 `host=0.0.0.0` 和 `public_host=192.144.206.201` 后，打开
`http://192.144.206.201:<port>/` 仍提示 `ERR_CONNECTION_REFUSED`。这可能是多层原因，
不能只归咎于云防火墙。

### 最短操作步骤

1. 打开主项目 `/assistant` 页面，确认已安装 B 并配置 Key。
2. 点击 **更新 AI 助手 B**，确保使用支持 `0.0.0.0` 的新版本（旧版可能强制绑定回环）。
3. 点击 **停止→启动**，不要只修改配置不重启；运行中的进程不会读取新 host。
4. 点击 **诊断公网访问**，按结果排查：
   - 实际 listener 只有 `127.0.0.1`/`::1`：旧版 B 或未重启，先更新再“停止→启动”。
   - 实际 listener 有 `0.0.0.0`/`::`，本机 health 正常，但公网探测失败：
     公网探测可能是 NAT hairpin，**不能直接判定外网不可达**；请从手机关闭 Wi-Fi 或另一台外部主机
     访问 `http://192.144.206.201:<port>/`。
   - 公网探测提示 DNS/URL 错误：检查 `public_host` 是不带协议/路径/端口的 IP 或域名，端口字段正确。
   - 页面明确提示端口占用/启动失败：查看诊断日志摘要，换一个 5000–10000 的端口。
5. 若外部网络也拒绝，依次检查：
   - 云厂商安全组/轻量应用服务器防火墙是否放行 TCP `<port>`（主项目不能替你改云安全组）；
   - Ubuntu 主机防火墙：`sudo ufw status`，必要时仅对可信来源放行；
   - NAT/端口映射是否把公网端口映射到服务器同一端口；
   - `public_host` 是否误填内网地址、错误公网 IP 或错误端口。
6. 确认后从外部浏览器再次打开 **打开 AI 助手 B** 的 URL。

### 安全提醒

AI 助手 B 自身没有用户认证。不要把 `0.0.0.0` 服务直接长期裸露公网；优先使用
云安全组/防火墙/VPN/带认证反向代理限制来源。仅自己使用时，使用回环模式 + SSH 隧道：
```bash
ssh -N -L <port>:127.0.0.1:<port> <用户名>@192.144.206.201
# 然后在本机打开 http://127.0.0.1:<port>/
```

诊断接口返回的本机 listener、health、公网探测、日志摘要和中文建议均不包含 API Key。
如果页面/接口显示“当前后端不支持诊断”，请更新主项目后重启管理服务再试。

<a id="memory-stats-permission"></a>
## 20. 记忆模式误报与资源统计 Permission denied

### 20.1 切换记忆模式后先报 cmd_config.json 无法解析

AstrBot 可能正在原地重写 `data/astrbot/cmd_config.json`。主项目现在会对临时空文件或半写
JSON 做最多 3 次、约 100ms 间隔的短重试；真正稳定损坏的 JSON 仍会报告。

如果增强插件配置已保存、记忆模式已切换，但 `unique_session` 迁移遇到并发写入窗口：

- HTTP 仍返回 `ok:true`，`error:""`；
- 查看 `warning`、`warnings`、`nonfatal` 字段；
- 页面刷新后模式已经生效是正常的；
- 可稍后再次保存一次 `astrbot` 模式，或重启 AstrBot 后再确认 `unique_session=false`。

不要手动删除或清空 `cmd_config.json`。如需检查：
```bash
python3 -m json.tool ~/.local/share/qqbot-studio/instances/bot-1/data/astrbot/cmd_config.json >/dev/null
```

### 20.2 资源统计报 collected_stickers Permission denied

资源统计现在会跳过不可读的目录，继续显示可读的 core/其它目录大小。API 响应仍是
`ok:true`，并新增：

- `partial: true`
- `permission_denied: true`
- `warnings` / `errors` 中包含“部分不可读（权限不足，已跳过）”；
- 实例和 `global` 子对象也有对应字段。

页面会显示“部分指标读取失败”，不再把整个实例统计变成失败。若需要让宿主机统计到收藏表情目录，
可检查容器 root 创建目录的权限，并以管理员确认后用类似命令修复（路径按实际实例调整）：
```bash
sudo chmod -R u+rwX,g+rX,o+rX \
  ~/.local/share/qqbot-studio/instances/bot-1/data/astrbot/plugin_data/qqbot_enhancer/collected_stickers
```
不要使用 `777`，不要删除收藏；AstrBot 容器与宿主机用户权限的插件侧彻底修复由插件任务负责。

### 20.3 修改插件参数后再次打开提示“本地插件配置损坏”

本地插件配置保存后会立即重读；如果恰逢另一个进程写入或读到临时半写文件，主项目会做
最多 3 次、约 100ms 间隔的重试。重试成功时页面不再误报损坏。

如果文件稳定损坏（例如手动编辑后 JSON 不完整），接口仍会返回 `ok:false` 并拒绝覆盖；
请先备份，再用以下命令确认并修复，不要直接删除含用户调优值的文件：
```bash
python3 -m json.tool \
  ~/.local/share/qqbot-studio/instances/bot-1/data/astrbot/config/qqbot_enhancer_config.json \
  >/dev/null
```
