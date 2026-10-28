# 服务器部署与实战排错指南

本文记录在云服务器 / 远程 Ubuntu 上部署 QQ Bot Studio 时常见的问题，包括：

- Git HTTPS 报 `Unsupported SSL backend 'openssl'`
- Docker 安装被取消或只装了一半
- Docker 服务启动失败、`Start request repeated too quickly`
- 卸载并重装 Docker
- 国内镜像拉取慢
- 服务器上运行后，如何从自己的电脑访问向导和 WebUI
- 指定本地向导端口

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

## 3. Docker 服务启动失败：Start request repeated too quickly

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

## 4. 卸载并重装 Docker

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

## 5. 国内镜像加速

向导现在默认勾选“使用国内镜像加速（自动多源）”，会依次尝试：

1. `m.daocloud.io/docker.io/`
2. `docker.1ms.run/`
3. `docker.xuanyuan.me/`
4. `hub.rat.dev/`

拉取失败会自动切换下一个源。成功源记录在：

```text
~/.local/share/qqbot-studio/config.json
```

### 自定义镜像前缀

```bash
QQBOT_IMAGE_MIRROR_PREFIXES="docker.1ms.run/,m.daocloud.io/docker.io/" ./start.sh
```

要求前缀支持：

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

离线导入时，在向导里取消“使用国内镜像加速”。

---

## 6. 服务器上运行，自己的电脑如何访问

### 推荐方案：SSH 隧道

不要让向导监听 `0.0.0.0` 暴露到公网。管理面板包含容器控制能力，
并且页面里会展示 AstrBot、SnowLuma、VNC 等敏感登录信息。

服务器上启动向导：

```bash
cd "/你的项目路径/QQ Ai bot"
./start.sh --port 8765
```

自己的电脑上建立 SSH 隧道：

```bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器IP
```

然后本地浏览器访问：

```text
http://127.0.0.1:8765/
```

### `-N` 和 `-L` 是什么意思

```text
ssh -N -L 本地端口:目标地址:目标端口 用户@服务器
```

- `-N`：不执行远程命令，只建立隧道；连接后终端没有提示符是正常的。
- `-L`：本地端口转发。
- 例如 `-L 8765:127.0.0.1:8765` 表示：
  你电脑的 `127.0.0.1:8765` → SSH 加密隧道 → 服务器的 `127.0.0.1:8765`。

关闭隧道：在 SSH 窗口按 `Ctrl+C`。

后台运行：

```bash
ssh -fN -L 8765:127.0.0.1:8765 用户名@服务器IP
```

### 同时转发 AstrBot / SnowLuma / noVNC

部署完成后查看服务器实际端口：

```bash
cat ~/.local/share/qqbot-studio/config.json
```

假设端口为 `6185`、`5099`、`6081`，本地电脑执行：

```bash
ssh -N \
  -L 8765:127.0.0.1:8765 \
  -L 6185:127.0.0.1:6185 \
  -L 5099:127.0.0.1:5099 \
  -L 6081:127.0.0.1:6081 \
  用户名@服务器IP
```

本地访问：

- 向导：`http://127.0.0.1:8765`
- AstrBot：`http://127.0.0.1:6185`
- SnowLuma：`http://127.0.0.1:5099`
- noVNC：`http://127.0.0.1:6081`

如果本地端口被占用，可以修改 `-L` 的第一个端口，例如：

```bash
ssh -N -L 18765:127.0.0.1:8765 用户名@服务器IP
```

然后访问 `http://127.0.0.1:18765`。

### 为什么不建议直接 `--host 0.0.0.0`

当前版本有意只允许 `127.0.0.1` / `localhost`：

```bash
./start.sh --host 0.0.0.0
# 会被拒绝
```

因为：

- 管理面板会返回明文 AstrBot、SnowLuma、VNC 密码；
- 管理 API 可以启动/停止容器；
- 默认是 HTTP，没有 TLS；
- 直接暴露到公网会被扫描和攻击。

### 指定向导端口

```bash
./start.sh --port 8765
```

或：

```bash
QQBOT_STUDIO_PORT=8765 ./start.sh
```

`--port 0` 表示自动选择空闲端口。

注意：这里修改的是**本地向导端口**，不是 AstrBot、SnowLuma 或 noVNC 的部署端口。

---

## 7. 最终验证清单

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
./start.sh --port 8765
```

服务器上看到：

```text
打开向导：http://127.0.0.1:8765/
```

自己电脑执行 SSH 隧道后，本地浏览器打开：

```text
http://127.0.0.1:8765/
```

如果仍有问题，请提供以下输出（敏感信息请先打码）：

```bash
sudo journalctl -xeu docker.service -b --no-pager -n 100
git config --show-origin --get-all http.sslBackend
docker --version
docker compose version
docker info
```
