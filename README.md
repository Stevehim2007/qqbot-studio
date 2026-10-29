# QQ Bot Studio v2

在 Ubuntu 上部署、管理多个 QQ AI 机器人：

- AstrBot 聊天机器人框架
- SnowLuma / OneBot v11 协议端
- DeepSeek `deepseek-flash`
- 多机器人实例隔离
- Docker 镜像节点测速、切换和自定义
- 浏览器向导、控制台和多实例管理
- 实时部署日志和中断恢复
- Docker/容器启动检查与自动修复
- 控制台内置离线使用助手
- `/找图` / `/搜图` 网络图片搜索与本地表情包
- Windows 10/11 初始移植（Docker Desktop + WSL2，未完成真实 Windows E2E）

> QQ 密码不需要填写。现代 QQ 登录使用扫码；设备验证、短信验证、滑块等只能在
> noVNC 中的 QQ 官方登录界面完成，工具不会绕过验证。

---

## 1. 三个脚本

| 脚本 | 作用 |
|---|---|
| `install.sh` | 安装 Docker、Python、screen 等依赖，首次配置 IP/端口，然后自动调用 `start.sh` |
| `start.sh` | 新建一个机器人实例，启动后台管理服务并打印部署向导 URL；可重复运行创建多个机器人 |
| `console.sh` | 打开/启动 Web 管理控制台，查看所有已创建机器人、运行状态和 WebUI 链接 |

Windows 初始移植使用 Docker Desktop + WSL2，入口和限制见
[`windows/README.md`](windows/README.md)。当前没有完成真实 Windows/Docker Desktop 联调，
不能视为与 Ubuntu 版同等级的生产可用承诺。

### 安装并启动第一个机器人

```bash
cd "/你的项目路径/QQ Ai bot"
./install.sh
```

安装完成后会自动弹出/打印部署向导地址。

### 新建第二个机器人

```bash
./start.sh
```

每次执行 `./start.sh` 都会创建一个**全新的隔离实例**，拥有独立的：

- 数据目录
- QQ 号
- DeepSeek API Key
- 提示词
- Compose 项目名
- 容器名
- Docker 网络
- AstrBot / SnowLuma / noVNC 端口

> **QQ 登录提醒：** 每个新实例首次部署都必须在 noVNC 中扫码登录 QQ。
> 重复运行 `./start.sh` 会创建新实例，需要重新扫码；管理已有机器人请使用 `./console.sh`。
> 重启已有容器通常复用 SnowLuma 保存的登录态，但 QQ 登录过期或触发验证时仍需在
> noVNC 中重新完成官方验证。

### 打开多实例控制台

```bash
./console.sh
```

### 自动更新

控制台顶部提供 **检查更新 / 更新并重启** 按钮：

- 管理服务会通过 `git fetch` 检查当前仓库的 `origin` 分支；
- 只接受干净的 Git 工作区和 fast-forward 更新；
- 有部署任务运行时禁止更新；
- 点击“更新并重启”后，后台脚本会执行 `git pull --ff-only`，同步已有实例的 QQ 增强插件，重启 AstrBot 容器，再重启管理服务；
- 更新日志：

```text
~/.local/share/qqbot-studio/auto-update.log
```

如果服务器不能访问 GitHub，或 Git 工作区有本地修改，控制台会拒绝自动更新并显示原因。

控制台可查看每个实例的部署状态、容器状态、QQ 号、AstrBot / SnowLuma / noVNC 链接，
并执行启动、停止、重启和打开部署向导。

控制台右上角的 **使用助手** 是离线教程面板：不会上传问题、不消耗 DeepSeek token，
可查询安装、更新、端口、扫码、SnowLuma 密码、概率/记忆、Docker 和日志等问题。

---

## 2. 首次启动：IP、端口和参数

首次运行会提示确认：

```text
管理服务监听 IP（0.0.0.0 表示所有网卡）
管理控制台端口
对外访问 IP/域名（用于生成浏览器链接）
```

默认监听：

```text
0.0.0.0:8765
```

配置保存到：

```text
~/.local/share/qqbot-studio/studio.json
```

以后再次运行 `./start.sh` 或 `./console.sh` 会直接使用保存的 IP/端口，不再重复询问。

### 参数修改

```bash
./start.sh --ip 0.0.0.0 --port 8765
./console.sh --ip 0.0.0.0 --port 8765
```

也可以指定对外访问地址：

```bash
./start.sh --ip 0.0.0.0 --port 8765 --public-host bot.example.com
```

首次运行参数会写入 `studio.json`。如果管理服务已经运行，修改监听 IP/端口会重启后台
管理服务；正在进行的部署任务会被标记为中断，可重新部署。

### 机器人服务端口

AstrBot WebUI、SnowLuma WebUI、noVNC 的宿主机端口仍由程序自动选择空闲端口，
并在实例的 `config.json` 中保存。多实例之间不会共用端口。

---

## 3. 默认 0.0.0.0 的安全风险（重要）

v2 默认将管理服务以及机器人 WebUI 端口绑定到 `0.0.0.0`，方便在服务器上直接用电脑或手机访问。

这会把以下内容暴露到网络：

- 管理控制台和部署向导
- AstrBot WebUI
- SnowLuma WebUI
- noVNC 远程桌面（包含 QQ 扫码窗口）
- 部分页面会展示 AstrBot、SnowLuma、VNC 登录凭据

腾讯云（CVM 安全组 / 轻量应用服务器防火墙）从零开放端口的逐步教程见：
[腾讯云开放端口 / 防火墙教程](docs/server-troubleshooting.md)。

请务必至少做到：

1. 使用云服务器安全组/防火墙限制来源 IP；
2. 不要把 `8765`、`6081`、`5900` 等管理/桌面端口直接暴露到公网；
3. 优先在可信内网、VPN、Tailscale/WireGuard 或 SSH 隧道中使用；
4. 不要在多人共用服务器上把本工具当作多租户安全隔离方案；
5. 使用 QQ 小号并遵守 QQ 用户协议。

如果只需要本机或 SSH 隧道访问，可以用：

```bash
./start.sh --ip 127.0.0.1 --port 8765
```

然后在本机建立 SSH 隧道：

```bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器IP
```

本机浏览器访问 `http://127.0.0.1:8765/console`。

---

## 4. 镜像节点测速和管理

部署向导配置页包含“Docker 镜像节点”卡片。进入页面后会自动：

1. 读取已保存的镜像节点；
2. 对所有节点自动测速；
3. 显示延迟和可用状态；
4. 默认使用当前选中的节点；
5. 允许手动切换节点。

默认节点：

1. DaoCloud
2. 1ms.run
3. 轩辕镜像
4. RatHub

### 添加节点

在 UI 中填写：

```text
节点名称
镜像前缀
```

例如：

```text
registry.example.com/
docker.1ms.run/
m.daocloud.io/docker.io/
```

要求前缀支持拼接：

```text
<prefix>/soulter/astrbot:latest
<prefix>/motricseven7/snowluma:latest
```

不要填写 `http://` 或 `https://`，只在 UI 中填写主机和路径。

### 切换和删除

- 单选后立即保存到 `studio.json`；
- 删除只对自定义节点开放；
- 部署时会先使用选中的节点，失败后自动尝试其他节点；
- 实际成功的节点记录在实例 `config.json` 的 `docker_mirror`。

### 离线镜像

如果已有本地镜像，程序会检测并跳过重复拉取：

```bash
docker load -i qqbot-images.tar
```

程序会先检查本地镜像，已存在时跳过重复拉取；镜像节点选择仍然保留。

---

## 5. 下载日志、部署恢复与鲁棒性

### 更详细的下载日志

镜像拉取现在按镜像逐个执行，并将 Docker 输出实时写入部署任务日志：

- 当前镜像名
- 当前镜像节点
- 层下载/解压信息
- 失败原因
- 自动回退到下一个节点

部署页每约 1.2 秒刷新任务日志。

### 浏览器退出后继续部署

管理服务默认运行在后台 `screen` 会话中：

```text
qqbot-studio-server
```

关闭浏览器、关闭启动终端或 SSH 断开，只要 screen 服务还在，部署会继续进行。

重新进入时：

- 如果任务仍在运行，向导自动跳到部署页并继续显示进度；
- 如果已部署成功，自动跳到完成页；
- 如果任务被中断，会显示“继续获取进度 / 重试部署”，不会永久卡死。

### 服务重启后的任务

部署任务状态会持久化到：

```text
instances/<instance>/jobs/
```

如果管理服务重启，正在运行的任务会被标记为“中断”，可以重新点击部署。
Docker Compose 操作是幂等的，重新部署会复用已完成的镜像和容器配置。

---

## 6. Docker 检查与容器修复

`start.sh` / `console.sh` 启动时会：

1. 检查 Docker CLI、Docker Compose 和 Docker daemon；
2. 检查每个已部署实例的 AstrBot 和 SnowLuma 容器；
3. 容器停止、缺失、重启中或健康检查异常时：
   - 执行 Compose `up -d --remove-orphans`；
   - 对 unhealthy 容器执行 restart；
   - 再检查修复结果；
4. 修复失败时控制台启动继续，但会在终端打印失败实例和原因；控制台卡片也会显示异常状态。

如果 Docker 不可用，启动会直接失败并提示修复命令，不会带着错误状态启动网页。

---

## 7. QQ 登录与验证

1. 部署完成后在向导中打开 noVNC；
2. 输入 VNC 密码；
3. 在 QQ 官方登录界面扫码；
4. 如出现设备验证、短信/邮箱验证、滑块或安全验证，必须在 noVNC 中完成；
5. 登录态保存在实例的 SnowLuma 数据目录中。

本工具不会：

- 收集、保存或代填 QQ 密码
- 绕过验证码、滑块、设备验证或风控
- 提供 QQ 登录自动化破解

如果 QQ 客户端提供密码登录，只能在 noVNC 的官方界面中自行输入；工具不会读取该密码。

---

## 8. DeepSeek 与人格提示词

- API Base：`https://api.deepseek.com/v1`
- 模型：`deepseek-flash`
- Provider：OpenAI 兼容 `openai_chat_completion`
- API Key 只写入实例的 `data/astrbot/cmd_config.json`，权限 `600`
- 部署前会调用 DeepSeek `/models` 预检：
  - HTTP 401/403 会阻止部署；
  - 网络异常会记录警告并继续
- 人格提示词部署前可编辑；部署后可在完成页修改。

---

## 9. QQ 增强插件

项目自动安装 `qqbot_enhancer` 插件，目标能力：

- 被 @ 或私聊必定回复
- 非 @ 群消息按概率回复
- 图片消息理解并回复
- 本地表情包发送（8 张自绘 PNG）
- `/找图 关键词` / `/搜图 关键词` 网络图片搜索并发送
- 搜索冷却、安全搜索级别、结果数量和失败降级
- 冷却、过滤和防刷屏

插件文档：

- [`docs/plugin-qqbot-enhancer.md`](docs/plugin-qqbot-enhancer.md)

### 设置群聊主动回复概率

打开 AstrBot WebUI → 插件 → **QQ Bot 增强回复** → 配置：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `memory_mode` | `direct` | `direct`=省 Token 直连；`astrbot`=使用 AstrBot 会话记忆 |
| `reply_probability` | `0.12` | 非 @ 群消息回复概率，0.12 = 12% |
| `group_cooldown_seconds` | `30` | 同一群两次概率回复的最小间隔 |
| `user_cooldown_seconds` | `90` | 同一用户两次概率回复的最小间隔 |
| `min_message_chars` | `3` | 太短的消息不参与概率回复 |
| `ignore_pure_image` | `true` | 非 @ 纯图片不参与概率回复 |
| `sticker_probability` | `0.35` | 已回复时附加表情包的概率 |

- 设为 `0.05` 就是约 5%，设为 `0` 则只有 @ 和私聊回复；
- @ 机器人和私聊不受概率、群冷却、用户冷却限制；
- 保存后一般立即生效，不生效时重启对应 AstrBot 容器。

### 图片搜索与表情包

在 QQ 群里使用：

```text
/找图 橘猫
/搜图 风景
```

机器人会从 Bing Images 搜索结果页解析真实图片直链并发送 1–5 张（可配置）。
该功能不需要额外 API Key，但依赖 Bing HTML 页面；改版/反爬时可能暂时无结果。

AstrBot WebUI → 插件 → QQ Bot 增强回复 → 配置中可调整：

- `image_search_enabled`：是否启用；
- `image_search_result_count`：单次最多发送几张；
- `image_search_safe_search`：`strict` / `moderate` / `off`；
- `image_search_group_cooldown_seconds`、`image_search_user_cooldown_seconds`：冷却；
- `image_search_timeout_seconds`、`image_search_max_response_bytes`：网络边界。

本地表情包共 8 张，按关键词/情绪/随机选择发送；`sticker_probability` 只控制表情包，
不会额外消耗 LLM token。

### 长期对话记忆模式

插件默认 `memory_mode=direct`：只发送 system prompt + 当前消息，不保存跨轮上下文。

如果希望机器人记住之前的对话，在 AstrBot WebUI → 插件 → QQ Bot 增强回复 → 配置中把：

```text
memory_mode = astrbot
```

切换后，@、私聊和概率命中的非 @ 消息会交给 AstrBot 默认 Agent 处理，使用 AstrBot
的会话管理器、人格和持久化历史。每组/每个私聊会话拥有自己的上下文；可以用
`/reset` 清空当前会话，用 `/new` 开启新会话。

记忆模式的代价是历史越长 token 越多，详见下一节。

### Token 说明

DeepSeek 的聊天 API 是无状态的：每次请求都会重新携带 system prompt 和本轮输入。
本项目插件在 `direct` 模式下采用轻量直连方式，不携带完整的 AstrBot 会话历史，
因此单次输入通常比“每轮带完整历史”的 Agent 模式小。

DeepSeek 会自动启用 Context Caching：稳定不变的系统提示词前缀命中缓存时，
会以更便宜的缓存 token 计费。不要每轮把时间、随机数等动态内容写入 system prompt，
否则会破坏缓存前缀。

进一步省 token 可以：

- 降低 `reply_probability`，或增大群/用户冷却；
- 增大 `min_message_chars`；
- 纯图片不参与概率回复时保持 `ignore_pure_image=true`；
- 把 `max_images_per_message` 调小（图片 token 很贵）；
- 保持系统提示词简洁且稳定；
- 表情包不会额外调用 LLM，`sticker_probability` 不增加模型 token。

> 图片理解需要可用的多模态模型；DeepSeek Flash 本身是否支持图片输入以实际 API 为准，
> 不支持时需要额外配置视觉模型/图片描述 provider。

---

## 10. 数据目录

默认根目录：

```text
~/.local/share/qqbot-studio/
```

结构：

```text
~/.local/share/qqbot-studio/
├── studio.json                # 全局 IP/端口/镜像节点
├── server.pid
├── server-runtime.json
├── studio-server.log
└── instances/
    ├── bot-1/
    │   ├── instance.json
    │   ├── config.json
    │   ├── secrets.json      # 权限 600
    │   ├── compose.yml
    │   ├── .env              # 权限 600
    │   ├── jobs/
    │   └── data/
    │       ├── astrbot/
    │       └── snowluma/
    └── bot-2/
```

自定义数据目录：

```bash
./start.sh --data-dir /data/qqbot-studio
```

注意：改数据目录会创建独立的实例集合。

---

## 11. screen 使用教程

`install.sh` 会安装 screen。后台管理服务使用：

```bash
screen -S qqbot-studio-server
```

启动脚本会自动创建这个会话，因此正常使用时不需要手动进入 screen。

常用操作：

```bash
# 查看所有会话
screen -ls

# 重新连接管理服务（排查日志时使用）
screen -r qqbot-studio-server

# 脱离 screen（保持服务运行）
# 先按 Ctrl+A，再按 D

# 结束 screen 会话
screen -S qqbot-studio-server -X quit
```

### 手动使用 screen 调试

```bash
screen -S qqbot-debug
# 在 screen 中运行
./start.sh
# Ctrl+A 然后 D 脱离
```

### 查看后台服务日志

```bash
tail -f ~/.local/share/qqbot-studio/studio-server.log
```

---

## 12. 常用命令

```bash
# 安装并自动启动第一个实例
./install.sh

# 新建一个机器人实例并打开部署向导
./start.sh

# 打开多实例控制台
./console.sh

# 修改监听参数（会导致管理服务重启）
./start.sh --ip 127.0.0.1 --port 8765
./console.sh --ip 0.0.0.0 --port 9000

# 查看 screen
screen -ls
screen -r qqbot-studio-server

# 查看管理服务日志
tail -f ~/.local/share/qqbot-studio/studio-server.log

# 查看全局配置和候选镜像节点
cat ~/.local/share/qqbot-studio/studio.json

# 查看某个实例状态和端口
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
```

---

## 13. 常见问题

### Docker 安装失败或被取消

新版 `start.sh` 在启动前强制检查 Docker、Compose 和 daemon，不会假装成功。

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

### Docker 服务启动失败

```bash
sudo journalctl -xeu docker.service -b --no-pager -n 200
sudo python3 -m json.tool /etc/docker/daemon.json 2>/dev/null
```

最常见原因是 `/etc/docker/daemon.json` 中镜像加速地址写错。先备份并移除：

```bash
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak 2>/dev/null || true
sudo rm -f /etc/docker/daemon.json
sudo systemctl daemon-reload
sudo systemctl reset-failed docker.service docker.socket
sudo systemctl restart containerd
sudo systemctl start docker
```

完整排查见：「[服务器排错指南](docs/server-troubleshooting.md)」。

### Git 报 Unsupported SSL backend 'openssl'

```bash
git config --show-origin --show-scope --get-all http.sslBackend
git config --global --unset-all http.sslBackend 2>/dev/null || true
git config --local --unset-all http.sslBackend 2>/dev/null || true
unset GIT_SSL_BACKEND
```

然后重试 `git pull --ff-only`。详情见服务器排错指南。

### 管理服务启动了但页面打不开

1. 看管理服务日志：
   ```bash
   tail -n 100 ~/.local/share/qqbot-studio/studio-server.log
   ```
2. 检查 IP/端口：
   ```bash
   cat ~/.local/share/qqbot-studio/studio.json
   ss -ltnp | grep 8765
   ```
3. 云服务器安全组是否放行该端口；
4. 如果绑定的是内网地址，确保电脑/手机与该地址网络互通。

### 浏览器打不开服务器 IP 上的页面

如果服务器本机 `curl http://127.0.0.1:8765/healthz` 正常，但自己的电脑打不开，
优先检查腾讯云安全组/轻量防火墙是否放行 `8765`，以及程序是否监听 `0.0.0.0`。
完整步骤见：[腾讯云开放端口 / 防火墙教程](docs/server-troubleshooting.md)。

### SnowLuma WebUI 登录不上 / 密码错误

SnowLuma WebUI 的默认用户名是：

```text
admin
```

密码有两种情况：

- 新版部署会在 `.env` 中预置 `SNOWLUMA_WEBUI_PASSWORD`，完成页会显示这个初始密码；
- 如果你在 SnowLuma WebUI 首次登录后修改过密码，请使用你修改后的密码，完成页不会自动更新。

如果旧实例的密码已经失效（例如未修改就被重启导致 SnowLuma 轮换了一次性密码），优先使用
完成页 SnowLuma 卡片里的 **重置密码** 按钮：第一次点击进入确认状态，第二次点击执行重置。
成功后卡片会显示新密码，用户名始终是 `admin`。也可以在服务器上手动操作：

可以从日志读取当前密码：

```bash
# 先确认 SnowLuma 容器名
docker ps --format 'table {{.Names}}	{{.Ports}}'

# 以实际容器名为准
docker logs --tail 200 qqbot-bot-1-snowluma 2>&1 | grep -i -A3 -B3 "initial credentials\|初始登录"
```

日志中会显示：

```text
initial credentials: user=admin password=xxxxxxxxxxxxxxxx
```

如果日志里已经找不到一次性密码，可以重置 SnowLuma **WebUI 登录密码**：

```bash
# 找到实例的 webui.json
find ~/.local/share/qqbot-studio/instances -path '*/data/snowluma/config/webui.json' -print

# 假设找到的是 bot-1，先备份再删除
cp ~/.local/share/qqbot-studio/instances/bot-1/data/snowluma/config/webui.json \
   ~/.local/share/qqbot-studio/instances/bot-1/data/snowluma/config/webui.json.bak

rm ~/.local/share/qqbot-studio/instances/bot-1/data/snowluma/config/webui.json

# 重启 SnowLuma 容器
docker restart qqbot-bot-1-snowluma

# 等待几秒后读取新一次性密码
sleep 5
docker logs --tail 200 qqbot-bot-1-snowluma 2>&1 | grep -i -A3 -B3 "initial credentials"
```

用日志中的 `password`、用户名 `admin` 登录，并按 SnowLuma 要求设置自己的新密码。
删除 `webui.json` 只会重置 SnowLuma WebUI 登录，不会删除 QQ 数据目录；
QQ 登录态是否会保留取决于 SnowLuma 版本，必要时仍需在 noVNC 中重新验证。

### 重新进入没有显示部署进度

确认后台 screen 还在运行：

```bash
screen -ls
```

以及 `studio-server.log` 是否有异常。v2 页面会通过 `active_job_id` 自动恢复部署页；
如果任务被标记为中断，会显示重试入口。

### 容器状态异常

```bash
screen -r qqbot-studio-server
# 观察启动时的容器修复输出
./start.sh
```

或者在控制台点击对应实例的“重启”。

---

## 14. 开发与测试

```bash
python3 -m unittest discover -s tests -v
node --check qqbot_studio/static/app.js
node --check qqbot_studio/static/console.js
bash -n start.sh console.sh install.sh scripts/*.sh
```

相关文档：

- [v2 QA 报告与真实 E2E 待测清单](docs/qa-report-v2.md)
- [前端/API 契约 v2](docs/ui-api-contract-v2.md)
- [QQ 增强插件说明](docs/plugin-qqbot-enhancer.md)
- [服务器排错指南](docs/server-troubleshooting.md)
- [AstrBot / SnowLuma / DeepSeek 集成调研](docs/integration-research.md)
- [Windows 初始移植说明](windows/README.md)

---

## 15. 许可与第三方项目

- 本项目代码：MIT License
- AstrBot：<https://github.com/AstrBotDevs/AstrBot>
- SnowLuma：<https://github.com/SnowLuma/SnowLuma>
- OneBot v11：<https://github.com/botuniverse/onebot-11>
- DeepSeek API：<https://api-docs.deepseek.com/>

AstrBot、SnowLuma、QQ、DeepSeek 分别适用其自身许可协议和服务条款。
使用第三方 QQ 协议端存在账号风控/封禁风险，请使用小号并遵守当地法律。
