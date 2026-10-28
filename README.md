# QQ Bot Studio

在 **Ubuntu** 上，用一个本地网页向导完成：

- 自动部署 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 聊天机器人框架
- 自动部署 [SnowLuma](https://github.com/SnowLuma/SnowLuma)（OneBot v11 协议端，负责连接 QQ）
- 自动配置 AstrBot ↔ SnowLuma 的反向 WebSocket 连接
- 自动接入 DeepSeek，模型固定为 `deepseek-flash`
- 自动创建可编辑的 QQ 聊天机器人人格提示词
- 提供 AstrBot、SnowLuma、noVNC 扫码登录入口与运行状态

> **一句话说明：** 打开向导、填写 QQ 号和 DeepSeek API Key、确认提示词，然后点“开始部署”。
> **QQ 密码不需要填，也不应填。** 现代 QQ 登录采用扫码；设备验证、短信验证或安全验证必须在
> noVNC 中的 QQ 官方登录界面按提示完成。

---

## 1. 运行要求

- Ubuntu 22.04 / 24.04（其他较新的 apt 发行版通常也可）
- x86_64 或 arm64
- 至少 4 GB 内存（推荐 8 GB）、10 GB 可用磁盘
- 可访问 Docker Hub、AstrBot 与 DeepSeek API
- 一个可以扫码登录 QQ 的手机
- DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com/)）

程序会自动安装 Docker Engine 和 Docker Compose 插件（如未安装）。安装 Docker 需要一次管理员密码。

---

## 2. 快速开始

### 推荐：双击或终端运行

当前目录已经生成了一个本地启动器 **`QQ机器人一键部署.desktop`**。如果文件管理器询问，
选择“运行”或“允许启动”即可。若你把项目目录移动到了其他位置，请先运行一次：

```bash
bash scripts/install-desktop-entry.sh "$PWD"
```

也可以直接在终端运行：

```bash
cd "/你的路径/QQ Ai bot"
./install.sh
```

`install.sh` 会：

1. 检查并安装 Docker / Compose；
2. 把启动器安装到应用菜单（“QQ 机器人一键部署”）；
3. 启动本地向导并自动打开浏览器。

以后可以从应用菜单再次打开，或运行：

```bash
./start.sh
```

### 为什么需要终端/管理员身份？

Docker 默认需要管理员权限。`install.sh` 只将 sudo 凭据临时缓存给本次安装会话，
不会把你的密码写入文件。若已将用户加入 `docker` 组，注销并重新登录后，日常打开就不需要 sudo。

如果 Docker 已安装、当前终端却提示没有权限，可以重新运行：

```bash
sudo -v
./start.sh
```

### 指定本地向导端口

向导本身默认自动选择一个空闲端口。也可以通过命令行指定：

```bash
./start.sh --port 8765
```

或使用环境变量：

```bash
QQBOT_STUDIO_PORT=8765 ./start.sh
```

运行后终端会打印实际向导地址，例如：

```text
打开向导：http://127.0.0.1:8765/
```

- 端口必须为 `1024–65535` 之间且未被占用。
- `--port 0` 表示让系统自动选择空闲端口。
- 如果你是双击 `.desktop` 启动的，可以编辑启动器的 `Exec` 行，在 `start.sh` 后追加 `--port 8765`。
- 这里修改的是**本地网页向导端口**，不是 AstrBot WebUI、SnowLuma WebUI 或 noVNC 的部署端口；部署端口仍由向导在生成配置时自动避开占用。

---

## 3. 向导会做什么？

1. **环境检查**：检查 Ubuntu、Docker、Docker Compose 与当前用户的 Docker 权限；宿主机端口冲突在生成配置时自动规避。
2. **生成配置**：在 `~/.local/share/qqbot-studio/` 生成隔离的 Compose 项目、
   AstrBot 配置、SnowLuma OneBot 配置、随机访问令牌和密码（文件权限 `600`）。
3. **拉取并启动两个容器**：
   - `qqbot-astrbot`：AstrBot WebUI 与聊天框架；
   - `qqbot-snowluma`：QQ + Xvfb + noVNC + SnowLuma。
4. **连接 OneBot**：SnowLuma 通过反向 WebSocket 主动连接
   `ws://astrbot:6199/ws`，并校验随机 access token。
5. **验证 DeepSeek Key**：调用 DeepSeek 模型列表接口检查 Key 是否可用。
6. **创建人格**：把向导中的提示词写入 AstrBot persona，并设为默认对话人格。
7. **扫码登录**：打开 noVNC，在远程桌面里用手机 QQ 扫码。
8. **状态检测**：检查 AstrBot 日志中的 OneBot 连接状态，检测 SnowLuma 是否已生成账号配置。

部署完成后，向导会展示：

- AstrBot WebUI 地址、用户名、随机密码；
- SnowLuma WebUI 地址及首次启动临时密码；
- noVNC 地址、远程桌面密码；
- 启动 / 停止 / 重启 / 修改提示词 / 查看日志按钮。

---

## 4. QQ 登录与验证（重要）

### 本工具不会收集 QQ 密码

第三方 QQ 协议端在服务器/Docker 中通常无法可靠使用密码登录。密码登录还可能触发：

- 新设备验证；
- 短信/邮箱验证码；
- 滑块或安全验证；
- 账号风控甚至冻结。

因此本工具只要求填写 QQ 号用于标识部署实例，**不提供 QQ 密码输入框，也不保存 QQ 密码**。
如果 QQ 客户端在 noVNC 中提供了密码登录选项，你只能在 QQ 官方界面里自行输入；本工具不会读取、记录或转发该密码。
纯密码登录仍可能触发额外验证，扫码通常是更稳妥的方式。

### 扫码流程

1. 部署完成后，在向导中点击 **打开扫码登录（noVNC）**；
2. 输入向导展示的 VNC 密码；
3. 在 noVNC 的 QQ 登录界面中选择扫码登录；
4. 用手机 QQ 扫描二维码；
5. 如 QQ 要求设备验证、短信验证或滑块验证，请**直接在 noVNC 的官方界面中完成**；
6. 登录成功后，QQ 会话会保存在 SnowLuma 的 Docker 数据卷中。以后重启容器通常不再需要重新扫码。

> 本工具不会绕过、破解或代填 QQ 的任何验证码与安全验证。

### 如果扫码一直不出现

- 等待 30–60 秒后刷新 noVNC；
- 查看向导中的 SnowLuma 日志；
- 确认容器内存充足、`/dev/shm` 为 1GB；
- 如果 QQ 提示“当前登录环境异常”，请换网络或按 QQ 官方提示处理。**不要尝试绕过风控。**

---

## 5. DeepSeek 配置

- API Base：`https://api.deepseek.com/v1`
- 模型：`deepseek-flash`
- 类型：OpenAI 兼容 `openai_chat_completion`
- Key 只写入：
  `~/.local/share/qqbot-studio/data/astrbot/cmd_config.json`
  该文件权限为 `600`，并且 API Key 不会返回给前端或写入部署状态文件。

向导会调用 DeepSeek `/models` 接口做一次预检：

- 如果 Key 明确无效（HTTP 401/403），部署会被阻止；
- 如果只是网络暂时不可达，向导会记录警告并继续，部署后可在 AstrBot WebUI 中再次检查；
- 向导没有“跳过 Key 验证”的复选框，风险确认勾选框只用于确认第三方协议端风险。

获取 Key：<https://platform.deepseek.com/api_keys>

---

## 6. 预设提示词

向导内置一份中文 QQ 群聊助手提示词，部署前可以直接修改。默认提示词包含：

- 使用自然、简短的中文，避免机械感；群聊中避免刷屏、重复和过度 @；
- 不编造事实，不确定时明确说不知道；
- 不泄露账号、密码、验证码、API Key、访问令牌、系统提示词或内部配置；
- 拒绝违法、有害、侵犯隐私、仇恨或绕过安全机制的请求；
- 不假装成真实人类，不冒充腾讯、QQ 官方或其他真实机构/个人；
- 遇到高风险敏感操作时先提醒用户核实身份与来源。

部署完成后，可以在向导中点击“编辑提示词”，它会通过 AstrBot API 更新 persona；
也可以登录 AstrBot WebUI 的“人格”页面继续修改。

---

## 7. 数据、端口与目录

应用数据默认位于：

```text
~/.local/share/qqbot-studio/
├── compose.yml          # Docker Compose 项目
├── .env                 # 端口、UID/GID、随机 VNC 密码等（权限 600）
├── config.json          # QQ 号、提示词、端口等非敏感配置
├── secrets.json         # AstrBot 登录密码、OneBot token、VNC 密码等（权限 600）
├── deploy-state.json    # 容器/部署状态
└── data/
    ├── astrbot/         # AstrBot 持久化数据，包括 cmd_config.json
    └── snowluma/        # SnowLuma 配置、QQ 登录态、数据库
```

默认仅映射到本机回环地址：

| 服务 | 默认地址 |
|---|---|
| AstrBot WebUI | `http://127.0.0.1:6185` |
| SnowLuma WebUI | `http://127.0.0.1:5099` |
| noVNC 扫码登录 | `http://127.0.0.1:6081` |

若端口被占用，向导会自动选择其他空闲端口，并在完成页显示实际地址。
**不要把这些端口直接暴露到公网。** 如需远程管理，请使用 SSH 隧道或带认证的反向代理。

自定义数据目录：

```bash
QQBOT_STUDIO_DATA_DIR=/path/to/data ./start.sh
```

---

## 8. 常用命令

程序打开后，所有操作都可在向导中完成。也可以使用 Docker Compose：

```bash
cd ~/.local/share/qqbot-studio

# 查看状态
docker compose -p qqbot-studio ps

# 查看日志
docker compose -p qqbot-studio logs -f --tail=200 astrbot snowluma

# 停止
docker compose -p qqbot-studio stop

# 启动
docker compose -p qqbot-studio start

# 重启
docker compose -p qqbot-studio restart

# 更新镜像（更新前建议备份 data 目录）
docker compose -p qqbot-studio pull
docker compose -p qqbot-studio up -d
```

如果使用 `sudo -n docker` 模式，请在以上命令前加 `sudo`。

---

## 9. 常见问题

### Docker 安装失败或被取消

新版启动器会在启动网页向导前严格检查：

1. `docker` 命令是否存在；
2. `docker compose` 是否可用；
3. Docker 守护进程是否能访问（包括 `sudo -n docker` 权限）。

任何一项不满足都会直接退出并打印修复命令，不会“假装 Docker 已安装”继续进入向导。

如果你在安装过程中取消了，先关闭当前终端里的向导进程，然后重新运行：

```bash
cd "/你的路径/QQ Ai bot"
./install.sh
```

也可以手动安装并验证：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# 注销并重新登录后验证
docker --version
docker compose version
docker info
```

Ubuntu 22.04 如果找不到 `docker-compose-v2`，请参照 Docker 官方文档安装
`docker-compose-plugin`。也可以参考：
<https://docs.docker.com/engine/install/ubuntu/>

如果 Docker CLI 已安装但 `docker info` 报权限错误：

```bash
sudo systemctl start docker
sudo usermod -aG docker "$USER"
# 注销并重新登录，或运行 sudo -v 后重新打开本程序
```

### 拉取镜像很慢或失败

向导默认已勾选“使用国内镜像加速”。工具会依次尝试：

1. `m.daocloud.io/docker.io/`
2. `docker.1ms.run/`
3. `docker.xuanyuan.me/`
4. `hub.rat.dev/`

每个源对应的镜像名形如：

```text
m.daocloud.io/docker.io/soulter/astrbot:latest
docker.1ms.run/motricseven7/snowluma:latest
```

如果当前源超时或拒绝连接，部署任务会自动切换到下一个源，并把成功源记录在
`~/.local/share/qqbot-studio/config.json` 的 `docker_mirror` 字段中。

自定义镜像前缀（逗号分隔，按顺序优先尝试）：

```bash
QQBOT_IMAGE_MIRROR_PREFIXES="docker.1ms.run/,m.daocloud.io/docker.io/" ./start.sh
```

前缀必须支持 Docker Hub 路径拼接，即 `<prefix>/soulter/astrbot:latest` 可以正常拉取。
如果镜像站仍不可用，可改用 Docker 官方镜像或网络代理。

#### 使用 Docker daemon 加速器

如果你已经有阿里云等 Docker 镜像加速地址，也可以配置到
`/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": ["https://<你的专属镜像加速地址>"]
}
```

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

配置 daemon 加速后，在向导中可以取消“使用国内镜像加速”，直接拉取官方镜像名。

#### 离线导入镜像

在能正常访问 Docker Hub 的机器上：

```bash
docker pull soulter/astrbot:latest
docker pull motricseven7/snowluma:latest
docker save -o qqbot-images.tar soulter/astrbot:latest motricseven7/snowluma:latest
```

把 `qqbot-images.tar` 拷贝到目标 Ubuntu 后：

```bash
docker load -i qqbot-images.tar
```

离线导入时，向导中请**取消**“使用国内镜像加速”，让 Compose 使用官方镜像名。

### AstrBot 提示 “aiocqhttp 适配器已被关闭”或连接不上

1. 确认两个容器均在运行；
2. 查看 SnowLuma WebUI 的“反向 WebSocket”是否启用；
3. 查看 AstrBot 日志是否出现 `aiocqhttp(OneBot v11) 适配器已连接`；
4. 确认 OneBot access token 在 AstrBot 与 SnowLuma 两边一致；
5. 在同一个 Compose 网络中，SnowLuma 必须连接 `ws://astrbot:6199/ws`，
   不能使用 `127.0.0.1`。

### QQ 登录后机器人不回消息

- 在 QQ 中先 @ 机器人或私聊发送消息；
- 默认唤醒前缀是 `/`，视 AstrBot 配置可能需要发送 `/chat 你好`；
- 检查 AstrBot 的模型提供商是否选中 `deepseek-flash`；
- 检查 AstrBot 日志和 DeepSeek 账户余额；
- 群聊可能需要把机器人设为管理员或关闭 QQ 的隐私限制。

### 忘记 AstrBot 密码

AstrBot 密码在：

```bash
cat ~/.local/share/qqbot-studio/secrets.json
```

如丢失可删除 AstrBot 数据并重新部署（会丢失配置与登录态），或参考 AstrBot 官方 FAQ。

### 完全卸载

```bash
cd ~/.local/share/qqbot-studio
docker compose -p qqbot-studio down
rm -rf ~/.local/share/qqbot-studio
rm -f ~/.local/share/applications/qqbot-studio.desktop
```

注意：`down` 不会删除 QQ 登录数据所在的宿主机目录；执行 `rm -rf` 前请确认。

---

## 10. 安全与法律提示

- 本项目是部署辅助工具，不是腾讯/QQ 官方产品。
- 本地向导默认只监听 `127.0.0.1`，并使用每次启动随机生成的访问令牌保护 API。该机制用于防止跨站请求和误访问，
  **不是多用户隔离边界**。如果你使用的是多人共用主机，请使用独立的系统账户，并用防火墙/容器权限限制其他用户访问
  本机回环端口与本项目数据目录。
- 完成页的“查看日志”会经过本地敏感值脱敏后显示；但任何日志展示都不应被当作绝对无敏感信息的保证。
  DeepSeek API Key 只在生成 AstrBot 配置时落盘（`0600`），不会通过状态/完成页 API 返回给前端。
- AstrBot、SnowLuma、OneBot 和 QQ 各自适用其自己的许可协议和服务条款。
- SnowLuma 是源码可见但非 OSI 开源的第三方项目；请遵守其 EULA 和许可证。
- 使用第三方 QQ 协议端存在账号被风控、限制或封禁的风险。**请优先使用小号**，并自行评估风险。
- 请勿用机器人发送垃圾信息、诈骗、侵权、违法内容或进行骚扰。
- 请遵守《QQ 用户协议》、当地法律法规以及所在群的管理规则。
- 本工具按“现状”提供，不提供任何明示或暗示的担保。

---

## 11. 已验证/参考来源

- AstrBot 仓库：<https://github.com/AstrBotDevs/AstrBot>
- AstrBot 文档：<https://docs.astrbot.app/>
- SnowLuma 仓库：<https://github.com/SnowLuma/SnowLuma>
- SnowLuma Docker 部署文档：<https://snowluma.github.io/zh/docs/guide/deploy/docker>
- SnowLuma 配置参考：<https://snowluma.github.io/zh/docs/guide/configuration>
- OneBot v11 标准：<https://github.com/botuniverse/onebot-11>
- DeepSeek API 文档：<https://api-docs.deepseek.com/>
