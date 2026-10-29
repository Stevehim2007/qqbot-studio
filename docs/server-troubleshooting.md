# 服务器部署与实战排错指南

本文记录在云服务器 / 远程 Ubuntu 上部署 QQ Bot Studio 时常见的问题，包括：

- Git HTTPS 报 `Unsupported SSL backend 'openssl'`
- Docker 安装被取消或只装了一半
- Docker 已安装且 daemon 正常，但启动器仍误报“未检测到 Docker”
- Docker 服务启动失败、`Start request repeated too quickly`
- 旧版容器仍只监听 `127.0.0.1`，新版启动后应从服务器 IP 直接访问
- 卸载并重装 Docker
- 国内镜像拉取慢
- 服务器上运行后，如何从自己的电脑直接访问管理控制台 / WebUI / noVNC
- 腾讯云安全组 / 轻量应用服务器防火墙开放端口（新手逐步教程）
- Ubuntu 本机 ufw / firewalld
- 指定管理服务监听 IP 和端口
- screen 后台运行与多机器人实例

> 详细的日常部署步骤以项目根目录 `README.md` 为准；本文是服务器专项补充。

---

## 1. Git 报 Unsupported SSL backend 'openssl'

### 现象

```text
fatal: Unsupported SSL backend 'openssl'. Supported SSL backends:
        gnutls
fatal: remote helper 'https' aborted session
```

### 原因

你的 Git 被配置成使用 OpenSSL 后端，但当前 Git / libcurl 只编译了 GnuTLS 后端。
常见来源：

- 从 Windows 或其他机器复制了 `~/.gitconfig`
- 曾经执行过 `git config --global http.sslBackend openssl`
- shell 环境里有 `GIT_SSL_BACKEND=openssl`
- 项目仓库的 `.git/config` 或 `/etc/gitconfig` 也有这个配置

这不是 GitHub 宕机，也不是证书过期。

### 修复

先定位配置来源：

```bash
git config --show-origin --show-scope --get-all http.sslBackend
env | grep -i GIT_SSL_BACKEND
git --version --build-options
```

删除错误配置：

```bash
# 全局配置
git config --global --unset-all http.sslBackend 2>/dev/null || true

# 当前仓库配置
git config --local --unset-all http.sslBackend 2>/dev/null || true

# 系统配置（需要 sudo）
sudo git config --system --unset-all http.sslBackend 2>/dev/null || true

# 当前 shell 环境变量
unset GIT_SSL_BACKEND
```

确认不存在残余配置：

```bash
git config --show-origin --get-all http.sslBackend
env | grep -i GIT_SSL_BACKEND
```

然后重试：

```bash
git pull --ff-only
# 或
git push origin main
```

如果确认当前 Git 支持 GnuTLS，也可以临时指定：

```bash
git -c http.sslBackend=gnutls ls-remote https://github.com/Stevehim2007/qqbot-studio.git
```

如果仍失败，可以暂时改用 SSH remote：

```bash
git remote set-url origin git@github.com:Stevehim2007/qqbot-studio.git
ssh -T git@github.com
git push origin main
```

不要把 `http.sslVerify false` 当作长期修复方案；它会关闭证书校验。

---

## 2. Docker 安装被取消后，项目仍然启动

### 现象

安装 Docker 时取消或中断，随后启动项目，向导看起来仍然正常打开，但 Docker 并未安装完成。

### 原因

旧版启动脚本没有在启动向导前做最终的 Docker 状态检查。

### 当前版本的处理

新版启动器会严格检查：

```bash
docker --version
docker compose version
docker info
```

任何一项不可用都会直接退出，并打印修复命令，不会继续进入向导。

### 处理方法

先关闭旧向导进程：

```bash
pkill -f "python3 -m qqbot_studio" 2>/dev/null || true
```

更新代码：

```bash
cd "/你的项目路径/QQ Ai bot"
git pull --ff-only
```

重新运行：

```bash
./install.sh
```

如果 Docker 仍然没有安装成功，可以手动安装：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

注销并重新登录，然后验证：

```bash
docker --version
docker compose version
docker info
```

---

## 3. Docker 已安装，但启动器仍提示“未检测到 Docker”

### 现象

Docker 明明已经安装并正常运行：

```bash
docker --version
docker compose version
docker info
systemctl is-active docker
```

以上命令全部正常，但运行 `./install.sh` / `./start.sh` 时出现：

```text
install.sh：Docker、Docker Compose 与守护进程均已就绪。
start.sh：错误：未检测到 Docker。请先运行 ./install.sh 安装 Docker，或手动安装后重试。
```

### 原因（v2.0.0 的启动器 bug）

v2.0.0 的启动器在检查 Docker 时，使用了一个不存在的临时目录作为
子进程工作目录：

```text
~/.local/share/qqbot-studio/doctor
```

Python 的 `subprocess.run(cwd=...)` 在目录不存在时会抛出
`FileNotFoundError`；程序把这个错误误判成“找不到 docker 命令”，于是即使
Docker、Compose 和 daemon 全部正常，也会报告“未检测到 Docker”。

### 永久修复

升级到 **v2.0.1 或更高版本**。该版本会使用已存在的数据目录，并且
`DockerController` 会自行创建所需工作目录。

```bash
cd "/你的项目路径/QQ Ai bot"
git pull --ff-only
git log -1 --oneline
```

确认当前提交包含或晚于：

```text
2f7eb49 fix: detect Docker from an existing working directory
```

验证版本：

```bash
python3 -m qqbot_studio --version
```

然后停掉旧管理服务并重新启动：

```bash
screen -S qqbot-studio-server -X quit 2>/dev/null || true
pkill -f "python3 -m qqbot_studio" 2>/dev/null || true

./start.sh --ip 0.0.0.0 --port 8765 --public-host 你的服务器公网IP或域名
```

### 临时绕过（无法立即更新时）

如果暂时不能 `git pull`，可以手动创建 v2.0.0 缺失的工作目录。

使用默认数据目录时：

```bash
mkdir -p "$HOME/.local/share/qqbot-studio/doctor"
./start.sh --ip 0.0.0.0 --port 8765
```

使用了 `--data-dir` 时，目录要对应那个数据目录：

```bash
# 假设数据目录是 /data/qqbot-studio
mkdir -p /data/qqbot-studio/doctor
./start.sh --data-dir /data/qqbot-studio --ip 0.0.0.0 --port 8765
```

这只是绕过旧版的误判，长期仍应升级到 v2.0.1。

### 如何确认 Docker 本身是否真的正常

```bash
command -v docker
docker --version
docker compose version
docker info
systemctl is-active docker
```

- 如果 `docker info` 报 `permission denied`，是 Docker 用户组权限问题，
  不是本节的目录 bug；
- 如果 `docker --version` 找不到命令，检查 PATH、Snap 安装路径或重新安装 Docker；
- 如果以上全部正常，但旧版仍报“未检测到 Docker”，优先按本文升级或创建
  `doctor` 目录。

### 旧版容器仍只监听 127.0.0.1

旧版 v1 部署可能留下：

```text
qqbot-astrbot
qqbot-snowluma
```

它们即使在新版启动后仍可能监听：

```text
127.0.0.1:5099
127.0.0.1:6081
127.0.0.1:6185
```

v2 会创建自己独立的 `qqbot-bot-N-astrbot` / `qqbot-bot-N-snowluma`
容器，并把端口绑定到 `0.0.0.0`。旧容器不会被 v2 控制台管理。

查看当前容器：

```bash
docker ps --format 'table {{.Names}}	{{.Ports}}'
```

确认它们确实是旧版容器且不需要保留后，可以删除：

```bash
docker rm -f qqbot-astrbot qqbot-snowluma
```

删除容器不会自动删除宿主机上的旧数据目录。若旧 QQ 登录状态还需要保留，
先备份对应数据目录再操作。

---

## 4. Docker 服务启动失败：Start request repeated too quickly

### 现象

```text
docker.service: Start request repeated too quickly.
docker.service: Failed with result 'exit-code'.
Failed to start docker.service - Docker Application Container Engine.
```

### 先理解这个提示

`Start request repeated too quickly` 只是 systemd 在 Docker 连续失败后触发限流，
不是根因。真正原因在更早的 `journalctl` 日志中。

### 第一步：查看真正错误

```bash
echo '===== docker ====='
sudo journalctl -xeu docker.service -b --no-pager -n 200

echo '===== containerd ====='
sudo systemctl status containerd.service --no-pager -l
sudo journalctl -u containerd.service -b --no-pager -n 100
```

重点查找：

- `failed to start daemon`
- `failed to initialize`
- `containerd`
- `iptables` / `nftables`
- `overlay2`
- `permission denied`
- `no space left`

也可以在前台启动查看完整错误：

```bash
sudo dockerd --debug
```

看到错误后按 `Ctrl+C`。

### 常见原因一：daemon.json 非法或镜像地址无效

这是很常见的原因，尤其是配置镜像加速时写入了占位符或非法 URL。

检查：

```bash
sudo cat /etc/docker/daemon.json 2>/dev/null || echo '没有 daemon.json'
sudo python3 -m json.tool /etc/docker/daemon.json 2>/dev/null
```

如果 JSON 不合法，或里面还是：

```json
{
  "registry-mirrors": ["https://<你的专属镜像加速地址>"]
}
```

先移除：

```bash
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak 2>/dev/null || true
sudo rm -f /etc/docker/daemon.json
sudo systemctl daemon-reload
sudo systemctl reset-failed docker.service docker.socket
sudo systemctl restart containerd
sudo systemctl start docker
sudo systemctl status docker --no-pager -l
```

如果 Docker 能正常启动，说明是配置文件问题。之后只使用真实可用的镜像地址，
并先用 `python3 -m json.tool` 校验 JSON。

### 常见原因二：containerd 没运行

```bash
sudo systemctl enable --now containerd
sudo systemctl reset-failed containerd.service
sudo systemctl restart containerd
sudo systemctl start docker
```

如果 containerd 也失败，查看：

```bash
sudo journalctl -u containerd.service -b --no-pager -n 100
```

### 常见原因三：旧 PID / socket

```bash
sudo systemctl stop docker.socket docker.service 2>/dev/null || true
sudo rm -f /var/run/docker.pid
sudo systemctl reset-failed docker.socket docker.service containerd.service
sudo systemctl start containerd
sudo systemctl start docker
```

### 常见原因四：iptables / nftables 冲突

如果日志出现 `iptables failed`、`nftables` 或默认网桥创建失败：

```bash
sudo apt install -y iptables
iptables -V
update-alternatives --display iptables
```

只有在确认系统使用 legacy iptables 且确实存在冲突时，才考虑：

```bash
sudo update-alternatives --set iptables /usr/sbin/iptables-legacy
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
sudo systemctl restart containerd
sudo systemctl restart docker
```

不要一开始就关闭 Docker 的 iptables 功能，这会破坏容器网络隔离。

### 常见原因五：磁盘满或 Docker 数据损坏

```bash
df -h
df -i
```

如果明确是 `/var/lib/docker` 损坏，先备份并移走旧数据：

```bash
sudo systemctl stop docker containerd
sudo mv /var/lib/docker /var/lib/docker.bak.$(date +%s)
sudo mkdir -p /var/lib/docker
sudo systemctl start containerd
sudo systemctl start docker
```

注意：这会丢失所有 Docker 镜像、容器和 Docker 卷。
QQ Bot Studio 的机器人数据通常在 `~/.local/share/qqbot-studio/data`，
该目录是宿主机 bind mount，不受 `/var/lib/docker` 清理影响。

### 修复后的验证

```bash
sudo systemctl daemon-reload
sudo systemctl reset-failed docker.service docker.socket
sudo systemctl start docker
sudo systemctl status docker --no-pager -l
docker --version
docker compose version
docker info
```

---

## 5. 卸载并重装 Docker

### 4.1 先停止容器并备份机器人数据

```bash
cd ~/.local/share/qqbot-studio 2>/dev/null && \
  docker compose -p qqbot-studio down 2>/dev/null || true

tar czf ~/qqbot-studio-data-backup.tar.gz \
  -C ~/.local/share/qqbot-studio data 2>/dev/null || true

sudo systemctl stop docker.socket docker.service containerd.service 2>/dev/null || true
```

### 4.2 判断 Docker 包来源

```bash
dpkg -l | awk '/^ii/ && ($2 ~ /docker|containerd/) {print $2}'
```

#### Docker 官方源安装的包

通常包含 `docker-ce`：

```bash
sudo apt-get purge -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin \
  docker-ce-rootless-extras

sudo apt-get autoremove --purge -y
```

#### Ubuntu 源安装的包

通常包含 `docker.io`：

```bash
sudo apt-get purge -y \
  docker.io docker-compose-v2 docker-compose \
  docker-doc podman-docker

sudo apt-get autoremove --purge -y
```

### 4.3 清理 Docker 源（可选）

```bash
sudo rm -f /etc/apt/sources.list.d/docker.list
sudo rm -f /etc/apt/keyrings/docker.gpg /etc/apt/keyrings/docker.asc
sudo apt-get update
```

### 4.4 是否删除 Docker 数据

只修复安装问题时，建议保留 `/var/lib/docker`，这样已拉取的镜像不用重新下载。

如果要做彻底干净重装，并且不介意重新拉镜像：

```bash
sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker
```

### 4.5 重新安装

方式 A：在项目目录中运行安装器：

```bash
cd "/你的项目路径/QQ Ai bot"
./install.sh
```

方式 B：Ubuntu 软件包（国内通常更快）：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

注销并重新登录后验证：

```bash
docker --version
docker compose version
docker info
```

---

## 6. 国内镜像加速

向导现在默认勾选“使用国内镜像加速（自动多源）”，会依次尝试：

1. `m.daocloud.io/docker.io/`
2. `docker.1ms.run/`
3. `docker.xuanyuan.me/`
4. `hub.rat.dev/`

拉取失败会自动切换下一个源。成功源记录在对应机器人实例的：

```text
~/.local/share/qqbot-studio/instances/<instance>/config.json
```

v2 的节点测速、切换、添加和删除都在部署向导 UI 中完成，不需要设置环境变量。
节点列表保存在：

```text
~/.local/share/qqbot-studio/studio.json
```

要求自定义前缀支持：

```text
<prefix>/soulter/astrbot:latest
<prefix>/motricseven7/snowluma:latest
```

### Docker daemon 镜像加速器

可选，不是必需。若配置 `/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": ["https://你的真实镜像加速地址"]
}
```

必须使用真实地址，不能使用占位符。校验 JSON：

```bash
sudo python3 -m json.tool /etc/docker/daemon.json
```

再重启：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

如果 Docker 因此无法启动，先移除 daemon.json，确认 Docker 正常后再重新配置。

### 离线导入镜像

```bash
docker pull soulter/astrbot:latest
docker pull motricseven7/snowluma:latest
docker save -o qqbot-images.tar soulter/astrbot:latest motricseven7/snowluma:latest
```

拷贝到目标服务器后：

```bash
docker load -i qqbot-images.tar
```

程序会先检查本地镜像，已存在时跳过重复拉取。

---

## 7. 服务器上运行，自己的电脑如何访问

### v2 默认：直接使用服务器 IP

v2 默认监听：

```text
0.0.0.0:8765
```

服务器上运行：

```bash
cd "/你的项目路径/QQ Ai bot"
./install.sh
```

安装器完成后会自动调用 `start.sh`，创建第一个机器人实例并打印地址，例如：

```text
请用电脑/手机浏览器打开部署向导：
http://服务器IP:8765/wizard?instance=bot-1

管理控制台：
http://服务器IP:8765/console
```

在自己电脑浏览器打开对应地址即可。AstrBot、SnowLuma、noVNC 的宿主机端口也会
绑定到 `0.0.0.0`，具体 URL 会显示在控制台/完成页中。

### 多机器人实例

```bash
# 每次运行创建一个新实例
./start.sh

# 查看和控制所有实例
./console.sh
```

控制台会显示每个实例的 QQ、容器状态、AstrBot/SnowLuma/noVNC 链接以及部署任务。

### 管理服务 IP / 端口

首次运行会询问并保存到：

```text
~/.local/share/qqbot-studio/studio.json
```

以后可通过参数修改：

```bash
./start.sh --ip 0.0.0.0 --port 8765
./console.sh --ip 0.0.0.0 --port 8765
```

只允许本机访问时：

```bash
./start.sh --ip 127.0.0.1 --port 8765
```

### 安全风险（必须阅读）

v2 默认 `0.0.0.0` 是为了方便直接从电脑/手机访问，但这会把：

- 管理控制台
- AstrBot WebUI
- SnowLuma WebUI
- noVNC 远程桌面
- 会显示登录凭据的部分页面

暴露到服务器网络。必须使用云安全组、防火墙或 VPN 限制来源 IP；
不要把 noVNC/VNC 端口直接开放给公网。多人共用主机时，不应把本工具当作多租户
安全隔离方案。

### 可选：SSH 隧道

如果只想通过 SSH 安全访问，先让管理服务只监听回环：

```bash
./start.sh --ip 127.0.0.1 --port 8765
```

自己电脑执行：

```bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器IP
```

然后访问：

```text
http://127.0.0.1:8765/console
```

`-N` 表示只建立隧道、不执行远程命令；`-L` 表示本地端口转发。
需要访问 AstrBot/noVNC 时，可以在命令中追加多个 `-L`：

```bash
ssh -N \
  -L 8765:127.0.0.1:8765 \
  -L 6185:127.0.0.1:6185 \
  -L 5099:127.0.0.1:5099 \
  -L 6081:127.0.0.1:6081 \
  用户名@服务器IP
```

实际端口查看：

```bash
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
```

---

## 8. 腾讯云开放端口 / 防火墙教程（新手）

很多人以为程序启动了就能从自己电脑访问，实际上需要同时确认两件事：

1. **云服务器防火墙/安全组**是否放行了端口；
2. **程序本身**是否监听在 `0.0.0.0`，而不是只监听 `127.0.0.1`。

只完成其中一项都可能打不开页面。

### 8.1 需要开放哪些端口

先开放管理控制台端口：

| 用途 | 默认端口 | 协议 |
|---|---:|---|
| QQ Bot Studio 管理控制台/部署向导 | `8765` | TCP |

部署完成后，控制台/完成页会显示每个机器人的实际链接。默认服务端口通常是：

| 用途 | 常见默认端口 | 协议 |
|---|---:|---|
| AstrBot WebUI | `6185` | TCP |
| SnowLuma WebUI | `5099` | TCP |
| noVNC（QQ 扫码/验证） | `6081` | TCP |

> 这些机器人端口是自动分配的，端口被占用时会变化。不要只照着默认端口开，
> 要以控制台显示的实际 URL/端口为准。

查看当前实际端口：

```bash
docker ps --format 'table {{.Names}}	{{.Ports}}'
```

或者查看某个实例的配置：

```bash
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
```

每新建一个机器人实例，都可能使用新的端口，需要把新实例的实际端口也加入防火墙。

如果服务器出站规则被改成只允许白名单，还需要允许出站 `TCP 443` 访问 Docker Hub、
镜像站和 `api.deepseek.com`。腾讯云默认出站通常是允许全部，一般不用额外配置。

### 8.2 腾讯云 CVM（云服务器）安全组操作

1. 登录腾讯云控制台：<https://console.cloud.tencent.com/cvm>
2. 进入 **云服务器 → 实例**，确认服务器地域正确，找到你的服务器。
3. 点击服务器名称或右侧操作，进入 **安全组** / **配置安全组**。
4. 找到该实例绑定的安全组，点击 **修改规则** / **编辑规则**。
5. 选择 **入站规则**，点击 **添加规则**。
6. 新增一条规则，例如管理控制台：

   ```text
   类型：自定义
   来源：你的电脑公网 IP/32（更安全）或 0.0.0.0/0（临时测试）
   协议端口：TCP:8765
   策略：允许
   备注：QQ Bot Studio 管理控制台
   ```

7. 保存/应用规则。
8. 首次部署完成后，再按控制台显示的实际端口，新增 AstrBot、SnowLuma、noVNC 的 TCP 入站规则。

### 8.3 来源填什么：先方便还是先安全

**推荐：来源填你自己的公网 IP/32。**

例如你的电脑公网 IP 是 `203.0.113.25`，就填：

```text
203.0.113.25/32
```

腾讯云安全组页面通常有“我的 IP”快捷选项，可以直接使用。

如果你是动态公网 IP、不知道自己的 IP，或者刚开始配置，可以先临时使用：

```text
0.0.0.0/0
```

这表示允许所有 IPv4 来源访问该端口。**临时测试完成后应尽快收紧到自己的 IP**。
尤其不要把 noVNC/VNC 和管理控制台长期暴露给整个公网。

如果你使用家庭宽带，公网 IP 经常变化，可以：

- 每次变化时更新安全组来源；或
- 只允许公司/家庭固定 IP；或
- 使用 VPN、Tailscale、WireGuard、SSH 隧道（更推荐）。

### 8.4 腾讯云轻量应用服务器（Lighthouse）防火墙

轻量应用服务器不一定叫“安全组”，通常叫“防火墙”。

1. 登录轻量应用服务器控制台：<https://console.cloud.tencent.com/lighthouse>
2. 进入 **轻量应用服务器 → 服务器**，选择你的实例。
3. 找到 **防火墙** 标签页。
4. 点击 **添加规则**。
5. 配置：

   ```text
   应用类型：自定义
   协议：TCP
   端口：8765
   来源：你的公网 IP/32，或临时 0.0.0.0/0
   策略：允许
   备注：QQ Bot Studio
   ```

6. 保存后按同样方式添加部署完成后的实际服务端口。

轻量服务器和 CVM 的安全组路径不同，但最终目的都是放行入站 TCP 端口。

### 8.5 Ubuntu 本机 ufw 防火墙

云安全组之外，Ubuntu 自己也可能启用了 ufw。

查看状态：

```bash
sudo ufw status verbose
```

如果显示 `inactive`，本机 ufw 没有拦截；云安全组是主要边界。

如果显示 `active`，先确保 SSH 已放行，再开放管理端口（不要在远程 SSH 中把自己锁在外面）：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8765/tcp
sudo ufw status numbered
```

部署后拿到实际服务端口，再按需开放，例如：

```bash
sudo ufw allow 6185/tcp
sudo ufw allow 5099/tcp
sudo ufw allow 6081/tcp
```

> 重要：Docker 发布端口时会写入自己的 iptables NAT 规则，部分场景下会绕过 ufw。
> 因此不要只依赖 ufw，**腾讯云安全组/轻量防火墙才是最终公网边界**。

### 8.6 Ubuntu firewalld

如果系统使用 firewalld：

```bash
sudo firewall-cmd --state
sudo firewall-cmd --permanent --add-port=8765/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

部署后再添加实际端口：

```bash
sudo firewall-cmd --permanent --add-port=6185/tcp
sudo firewall-cmd --permanent --add-port=5099/tcp
sudo firewall-cmd --permanent --add-port=6081/tcp
sudo firewall-cmd --reload
```

### 8.7 检查程序是否监听 0.0.0.0

开放安全组后，还要确认程序监听地址正确。

```bash
ss -ltnp | grep -E '8765|6185|5099|6081'
```

正确的管理服务应该类似：

```text
LISTEN 0 128 0.0.0.0:8765 ...
```

如果看到：

```text
LISTEN 0 128 127.0.0.1:8765 ...
```

那么只有服务器本机能访问，安全组开得再宽也没用。检查：

```bash
cat ~/.local/share/qqbot-studio/studio.json
```

`bind_host` 应为：

```json
"bind_host": "0.0.0.0"
```

如果不是，用参数覆盖后重启管理服务：

```bash
./start.sh --ip 0.0.0.0 --port 8765 --public-host 你的服务器公网IP或域名
```

容器端口也应检查：

```bash
docker ps --format 'table {{.Names}}	{{.Ports}}'
```

应看到 `0.0.0.0:端口->...`。如果仍是 `127.0.0.1:端口->...`，通常是旧容器
或旧实例，按第 3 节旧容器清理说明处理。

### 8.8 从自己的电脑验证

在服务器本机测试：

```bash
curl -I http://127.0.0.1:8765/healthz
```

只能证明程序启动了，不能证明公网可访问。还要从自己的电脑测试。

Windows PowerShell：

```powershell
Test-NetConnection 服务器公网IP -Port 8765
```

macOS / Linux：

```bash
nc -vz 服务器公网IP 8765
# 或者
curl -v http://服务器公网IP:8765/healthz
```

浏览器直接打开：

```text
http://服务器公网IP:8765/console
```

如果 `curl 127.0.0.1` 正常、但自己电脑打不开，通常是：

- 腾讯云安全组/轻量防火墙没放行；
- 来源 IP 填错；
- 使用了内网 IP；
- 程序只监听 `127.0.0.1`；
- 服务器有多个安全组，规则冲突；
- 运营商/公司网络限制。

### 8.9 新手最容易犯的错误

1. **只开了 ufw，没开腾讯云安全组**：公网仍然进不来。
2. **只开了腾讯云安全组，程序监听 127.0.0.1**：公网仍然进不来。
3. **只开了 8765，忘记开 noVNC/服务端口**：控制台能打开，但点链接打不开。
4. **把 `ip addr` 里的 10.x / 172.x 内网 IP 当成公网 IP**：应该使用腾讯云控制台显示的“公网 IP”。
5. **来源填了错误 IP**：手机热点/宽带 IP 变化后需要更新。
6. **安全组存在更高优先级的拒绝规则**：检查规则列表并把允许规则放到正确优先级。
7. **用的是轻量应用服务器**：它叫“防火墙”，不是 CVM 安全组。
8. **开了 `0.0.0.0/0` 后一直不收紧**：管理台和 noVNC 暴露风险很高。

### 8.10 最安全的替代方案：SSH 隧道

如果只是自己电脑临时使用，SSH 隧道通常比开放大量公网端口更安全：

```bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器公网IP
```

然后本机访问：

```text
http://127.0.0.1:8765/console
```

这种方式只需要腾讯云安全组放行 SSH 端口（通常是 22），不需要把 8765、5099、
6081、6185 直接开放到公网。

---

## 9. screen 后台运行

安装器会安装 screen。`start.sh` / `console.sh` 启动的后台管理服务名为：

```text
qqbot-studio-server
```

常用命令：

```bash
screen -ls
screen -r qqbot-studio-server
tail -f ~/.local/share/qqbot-studio/studio-server.log
screen -S qqbot-studio-server -X quit
```

脱离 screen：先按 `Ctrl+A`，再按 `D`。

如果系统限制 screen（例如 `/run/screen` 只读），启动器会自动降级为 `nohup`
后台模式，仍可通过日志文件查看服务状态。

---

## 10. 最终验证清单

```bash
# Git
git --version
git config --show-origin --get-all http.sslBackend
# 无输出即可

# Docker
docker --version
docker compose version
docker info

# 项目
cd "/你的项目路径/QQ Ai bot"
./start.sh --ip 0.0.0.0 --port 8765
./console.sh --ip 0.0.0.0 --port 8765
screen -ls
```

浏览器打开脚本输出的 URL。

如果仍有问题，请提供以下输出（敏感信息请先打码）：

```bash
sudo journalctl -xeu docker.service -b --no-pager -n 100
git config --show-origin --get-all http.sslBackend
tail -n 100 ~/.local/share/qqbot-studio/studio-server.log
docker --version
docker compose version
docker info
```
