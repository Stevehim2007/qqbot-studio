# QQ Bot Studio v5

在 Ubuntu 上一键部署和管理多个 QQ 机器人实例：AstrBot、QQ 官方机器人开放平台、DeepSeek AI 聊天。当前版本支持 QQ 官方机器人，也可选外部 OneBot v11 网关。

当前主项目版本：**5.0.0-beta.8（测试版）**。支持 QQ 官方机器人，或内置 NapCat / 外部 OneBot v11 网关；AstrBot 单容器运行；包含长期记忆与群策略、记忆卡片 v3、插件市场/分组、最小三插件默认集、七个核心 QQ 插件与定时/运势/天气三个工具插件、统一安全工具入口、运行时依赖检查、Food 地点解析与更新/回退功能。目标是 Ubuntu 22.04 / 24.04；Windows 是独立项目，见 [Windows 入口](#windows)。

> **升级提醒**：5.0.0 是 QQ 官方机器人接入后的测试线；升级前请备份实例 `data/`，并先在测试实例验证。长期记忆是独立 SQLite sidecar，启用前同样先备份。

> **安全提醒：** 管理服务和机器人 WebUI 默认按 `0.0.0.0` 暴露到局域网/公网可达的网卡。第一次使用请先读 [安全组、防火墙和 SSH](#security)，不要把管理端口或 AstrBot WebUI 直接公开到互联网。
> **文档维护约定：** 无论是正式版还是测试版，每次发布/合并都必须检查并同步：`README.md`、`CHANGELOG.md`、`docs/README.md`、相关功能文档、版本号和测试数量。没有文档变更的提交也必须确认“无需改文档”；禁止只改代码不改版本/文档。

> **版本规则：** 5.x 正式版使用 `v5.y.z`；测试版使用 `v5.y.z-beta.N`。测试版必须在 README 明确标注“测试版”，不得冒充正式版。

<a id="toc"></a>
## 目录

- [第一部分：最简单安装和使用](#part-1)
  - [Ubuntu 一键安装](#ubuntu-install)
  - [部署第一个 QQ 官方机器人](#first-bot)
  - [打开管理控制台](#console)
  - [Windows 10/11：独立项目](#windows)
  - [AI 助手独立页面](#assistants)
- [第二部分：进阶功能](#part-2)
  - [多实例、单端口、删除和端口冲突](#multi-instance)
  - [QQ 增强插件：概率、识图、找图、表情包、唱歌、/option](#enhancer)
  - [记忆模式、群聊/私聊记忆和 token](#memory)
  - [插件选择器、存储/内存统计](#plugin-selector-stats)
  - [整项目自动更新、镜像节点和 Docker 修复](#update-mirrors-docker)
  - [安全组、防火墙和 SSH 隧道](#security)
- [第三部分：教程和常见问题](#part-3)
  - [Git SSL backend 错误](#faq-git-ssl)
  - [Docker 已安装却提示未检测到 Docker](#faq-docker-false-negative)
  - [0.0.0.0 访问不了 / 面板只监听 127.0.0.1](#faq-0000-127)
  - [腾讯云安全组 / 轻量应用服务器防火墙](#faq-tencent-cloud)
  - [控制台状态读取失败](#faq-console-status)
  - [端口冲突、旧容器和旧实例](#faq-port-conflict)
  - [Windows 与 Docker Desktop 未验证](#faq-windows)
  - [Docker 安装不完整、Docker daemon 启不来](#faq-docker-install)
  - [部署进度、浏览器关闭和后台服务](#faq-deploy-progress)
- [第四部分：项目关系、声明与致谢](#part-4)
- [更新日志](#changelog)
- [文档索引](#docs-index)

---

<a id="part-1"></a>
## 第一部分：最简单安装和使用

### 先选择你的平台

| 场景 | 入口 |
|---|---|
| Ubuntu 22.04 / 24.04 服务器或桌面 | 本文；运行 `./install.sh` |
| Windows 10/11 | **不要运行 Linux 的 `install.sh`**；使用独立的 [QQ Bot Studio Windows](https://github.com/Stevehim2007/qqbot-studio-windows) |
| 只查使用说明，不下载扩展 | 打开 README 的教程/FAQ；控制台顶部 **AI 助手** 进入独立管理页 |

<a id="ubuntu-install"></a>
### 1.1 Ubuntu 一键安装

建议在普通用户下运行，不要直接 `sudo ./install.sh`。安装脚本需要时会请求一次管理员权限，用来安装 Docker/Compose/Python/screen/git/curl 等依赖。

```bash
# 已有项目目录
cd "/你的路径/QQ Ai bot"
./install.sh
```

从 GitHub 全新拉取：

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/Stevehim2007/qqbot-studio.git
cd qqbot-studio
./install.sh
```

首次启动会询问并保存：

```text
管理服务监听 IP（默认 0.0.0.0，表示所有网卡）
管理控制台端口（默认 8765）
对外访问 IP/域名（只用于生成浏览器链接）
```

- 只在本机使用：`./install.sh --ip 127.0.0.1 --port 8765`
- 云服务器需要从自己电脑访问：使用 `--ip 0.0.0.0 --port 8765 --public-host 你的公网IP或域名`，并严格限制安全组/防火墙来源。
- `public-host` 只影响打印和打开链接，不改变实际监听地址。
- 要真正部署 QQ 机器人并让它聊天，仍需在向导里填写 DeepSeek API Key。

安装完成后会打印并尝试打开部署向导地址，例如：

```text
http://服务器IP:8765/wizard?instance=bot-1
http://服务器IP:8765/console
```

<a id="first-bot"></a>
### 1.2 部署第一个机器人（QQ 官方机器人）

1. 浏览器打开安装完成时打印的 **部署向导**。
2. 接入方式选择 **QQ 官方机器人**，填写开放平台 AppID、AppSecret、DeepSeek API Key、人格提示词，勾选风险确认。
3. 镜像节点可在向导里选择；不确定时保留默认自动选择。
4. 点击 **开始部署**。首次拉镜像可能较慢，可关闭浏览器后稍后回来；后台任务会继续运行。
5. 部署完成后回到控制台查看 AstrBot 与 QQ 官方连接状态。官方接入无需扫码登录。
6. 在 QQ 开放平台把机器人加入测试群；群主需开启「获取群内全部消息」和「机器人主动在群聊内发言」才能收到非 @ 群消息。
7. 在 QQ 开放平台配置好回调/IP 白名单和所需权限，并确保 AppID/AppSecret 有效。

> **QQ 官方机器人不能绕过平台频控/权限。** 管理工具不会收集或保存 QQ 密码；真实平台授权和权限配置以 QQ 开放平台为准。

### 1.3 可选：OneBot v11 网关

在向导的接入方式中选择 **OneBot v11 网关**，再选择网关模式：

#### 内置 NapCat（推荐，扫码即用）

1. 填写机器人 **QQ 号**（5–12 位数字），网关选择 **内置 NapCat**。
2. 部署会同时启动 AstrBot 与 NapCat；反向 WebSocket、Token 和 `onebot11_<QQ>.json` 自动生成，无需手动配置。
3. 部署完成后，在完成页点击 **打开 NapCat 登录**，用手机 QQ 扫码登录。
4. 登录后 NapCat 会自动连接 AstrBot；NapCat WebUI 默认只绑定 `127.0.0.1`，不要直接暴露到公网。
5. QQ 新设备登录可能需要扫码、短信或滑块验证，这是 QQ 平台行为，无法绕过。

#### 外部 OneBot v11 网关

如果使用自己的 NapCat、LLOneBot、Lagrange 等网关：

1. 填写该网关登录的机器人 **QQ 号**。
2. 反向 WebSocket 端口可留空自动分配；Token 可留空自动生成。
3. 部署完成后，向导/控制台会显示 `ws://<主机>:<端口>/ws` 和 Token。
4. 在外部网关中填写该地址与 Token，让网关连接 AstrBot；不要同时启用官方机器人与 OneBot，避免重复回复。
5. OneBot 模式使用网关登录的 QQ 号和数字群号；官方模式使用 AppID/AppSecret 和 openid，两者不要混用。本项目不内置 SnowLuma，也不收集 QQ 密码。

<a id="console"></a>
### 1.4 打开管理控制台

```bash
./console.sh
```

控制台可以：

- 查看每个实例的 QQ 官方 AppID、部署状态、容器状态和 WebUI 链接；
- 打开向导和 AstrBot WebUI；
- 启动、停止、重启或删除实例；
- 展开 **插件、记忆与资源**，管理插件、记忆模式和资源统计；
- 从这里打开 **AI 助手** 独立页面，管理可选的 DeepSeek 助手 B。
- 控制台自动刷新为 **每 30 分钟一次**；需要立即更新时点右上角 **手动刷新**。

`start.sh` 和 `console.sh` 的区别：

| 命令 | 行为 |
|---|---|
| `./start.sh` | **每次创建一个全新实例**，并打开该实例的部署向导 |
| `./console.sh` | 打开已有实例的控制台，不创建新实例 |

<a id="windows"></a>
### 1.4 Windows 10/11：独立项目

Windows 版不是 Ubuntu 主项目的同一发行版、不是本仓库的自动更新分支，也不能与 Linux 共用数据目录或自动更新。

- Windows 独立仓库：<https://github.com/Stevehim2007/qqbot-studio-windows>
- Releases 页面：<https://github.com/Stevehim2007/qqbot-studio-windows/releases>
- 双发行版（同一版本线/同一 Release）：
  - **Docker edition（稳定）**：`QQBotStudioWindows.exe`，Docker Desktop + WSL2 + Linux containers；
  - **Native edition（POC/bootstrap）**：由 Windows 独立仓库维护；其支持范围和验证状态以该仓库为准。
- 主仓库内的 [`windows/README.md`](windows/README.md) 是早期移植资料/开发记录，正式 Windows 入口以上面的独立仓库为准。
- Windows 双发行版可行性见 [`docs/windows-editions-research.md`](docs/windows-editions-research.md)（历史研究资料）。

**每次 Linux 主项目更新都要同步 Windows 双版本**：Windows 仓库的
`.github/workflows/sync-upstream.yml` 支持 `workflow_dispatch` 和每日 schedule，从本仓库
公开 `main` 同步 allowlist（`qqbot_studio/`、`plugins/qqbot_enhancer/`、`assets/`、必要 docs），
记录 `.upstream-commit` 与 `UPSTREAM.md`，有变化才自动 commit/push；Windows launcher、
PowerShell、build/spec、`VERSION` 和 release workflow 保留在 Windows 仓库。Linux
major/minor 发布后必须在同一发布周期同步 Windows；`v*` Release 校验 tag/VERSION 并同批
构建、smoke、上传 Docker/Native 两个 EXE + SHA256 + README/VALIDATION。未同步、测试失败或
任一 EXE smoke 失败时不得发布。当前 Windows 正式 Release 的 EXE/API 真实验证与未验证项
以 Windows 仓库 README/VALIDATION 为准；真实 Windows AstrBot/QQ 官方平台 E2E 仍未验证，
不能把 Native bootstrap 当作生产可用版本。

<a id="assistants"></a>
### 1.5 AI 助手独立页面

主控制台不再内置离线“使用助手”面板。AI 助手统一放在独立页面：

```text
http://服务器IP:8765/assistant
```

也可以点击控制台顶部 **AI 助手** 链接进入。

这个页面负责可选的生成式 AI 助手 B：

- 安装/更新独立子项目 `qqbot-studio-ai-assistant`；
- 配置独立 DeepSeek Key，或复用某个已部署实例的 Key；
- 默认端口 `8766`；
- 默认只监听 `127.0.0.1`，远程服务器用 SSH 隧道：
  ```bash
  ssh -L 8766:127.0.0.1:8766 用户名@服务器IP
  ```
  然后打开 `http://127.0.0.1:8766/`；
- 也可以在页面明确选择 **公网/所有网卡（0.0.0.0，高风险）**，填写公网 IP/域名后直接从公网访问。公网模式没有内置登录、TLS 和限流，必须先用安全组/防火墙/VPN/认证反代限制来源，禁止裸奔。

B 是独立子项目，代码、配置和 API Key 都独立于主项目：

- 仓库：<https://github.com/Stevehim2007/qqbot-studio-ai-assistant>
- 安装位置：`<QQBOT_STUDIO_DATA_DIR>/extensions/ai-assistant`
- 配置文件：`<QQBOT_STUDIO_DATA_DIR>/ai-assistant/config.json`
- 主控制台只管理配置和启动，不读取或展示 Key。

如果公网 URL 显示“拒绝连接”，依次检查：

```bash
# 1. 服务是否真的在监听 0.0.0.0:8766
ss -ltnp | grep 8766

# 2. 服务器本机健康检查
curl -sS http://127.0.0.1:8766/api/health

# 3. 查看 B 日志
tail -n 100 "$HOME/.local/share/qqbot-studio/ai-assistant/assistant.log"
```

- 只显示 `127.0.0.1:8766`：在 `/assistant` 页面改为“公网/所有网卡”，保存后停止再启动 B；
- 本机 `curl` 失败：先点“更新 AI 助手 B”，旧版 B 可能不支持 `0.0.0.0`；
- 本机正常但外网拒绝：在腾讯云安全组/轻量应用服务器防火墙放行 TCP `8766`，并检查 `ufw`/`firewalld`；
- 公网模式没有认证，必须限制来源 IP，建议 VPN 或带认证的反向代理。

---

<a id="part-2"></a>
## 第二部分：进阶功能

<a id="multi-instance"></a>
### 2.1 多实例、单端口、删除和端口冲突

#### 新建实例

```bash
./start.sh
```

每次运行都会创建新的隔离实例，拥有独立的：

- 数据目录、QQ 官方 AppID、DeepSeek API Key、提示词；
- Compose 项目名、容器名、Docker 网络；
- AstrBot WebUI 宿主机端口。

新实例在向导填写 QQ 官方 AppID/AppSecret 并部署即可，不需要扫码。管理已有机器人请用 `./console.sh`，不要重复点 `start.sh` 来“刷新”已有机器人。

#### 每个实例一个 WebUI 端口

每个实例只发布一个 AstrBot WebUI 宿主机端口：

```text
AstrBot WebUI = base
```

所有实例端口限制在 `5000–10000`，且互不重叠。例如：

```text
bot-1: 5000
bot-2: 5001
bot-3: 5002
```

管理控制台默认使用 `8765`，并会从实例分配中预留。容器内部端口不变；变化的是宿主机映射端口。实际地址以控制台和实例的 `config.json` 为准：

```bash
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

#### 删除实例

控制台中每个实例都有 **删除实例** 按钮：第一次点击进入确认状态，第二次点击才会删除。删除会停止/移除该实例容器并删除实例目录，**不可恢复**，提示词和 API Key 都一起删除。只想修端口时先备份数据，不要直接删除。

#### 端口冲突

先查实际占用：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
ss -ltnp 2>/dev/null | grep -E ':(8765|5000)\b'
```

常见原因是旧实例、旧 v1 容器或 Docker Desktop/旧 Docker 代理仍占着端口。处理顺序：

1. 在控制台删除不再需要的实例（确认已备份）；
2. 如果是旧 v1 容器，见 [端口冲突 FAQ](#faq-port-conflict)；
3. Docker Desktop/旧代理残留可重启 Docker Desktop 或 Docker daemon；
4. 重新创建一个新实例，让程序重新分配可用端口。

<a id="enhancer"></a>
### 2.2 QQ 增强插件：概率、识图、找图、表情包、唱歌、/option

最终模块化版本保留七个核心插件：`qqbot_core_reply`（回复/人格/会话/点歌）、`qqbot_safe_tools`（唯一 marker/授权/确认/审计）、`qqbot_web_reader`、`qqbot_image_search`、`qqbot_food`、`qqbot_stickers`、`qqbot_memory_commands`；另有 `qqbot_scheduler`、`qqbot_fortune`、`qqbot_weather` 三个不调用大模型的工具插件。部署过程**不再**复制 `qqbot_enhancer` 或 `qqbot_core_full`；请在控制台的插件市场按依赖顺序安装这七个包。

插件开发与新增插件规范见 [docs/plugin-standard.md](docs/plugin-standard.md)。

> 旧实例仍保留 `qqbot_enhancer` 配置、`plugin_data`、会话与记忆，升级时通过迁移脚本复制配置并停用旧代码，不删除任何实例数据。新实例的回复职责只由 `qqbot_core_reply` 承担。

| 功能 | 行为/命令 | 关键配置 |
|---|---|---|
| @ 必回 | 群聊中明确 @ 机器人必回；@全体不算 | 不受概率和概率冷却限制 |
| 私聊必回 | 私聊消息必回 | 不受概率和概率冷却限制 |
| 非 @ 概率回复 | 群里未被 @ 时按概率参与 | `reply_probability=0.12`；群冷却 30 秒；用户冷却 90 秒；最短 3 字符 |
| 图片理解 | 对图片消息尝试原生视觉或图片转述 | `native_vision_first`、`vision_provider_id`、`auto_use_astrbot_caption_provider` |
| 网络找图 | `/找图 关键词`、`/搜图 关键词` | Bing Images + 百度图片第二源；安全搜索、冷却、下载大小/超时 |
| 自然语言找图 | @ 机器人或私聊说“帮我搜两张千早爱音图片” | `image_search_natural_language_enabled=true`；非 @ 群聊不触发 |
| 用户表情反应 | 用户 @/私聊发送纯图片/表情时优先自然短句回应，可按概率附表情 | `user_sticker_reaction_enabled`、`user_sticker_reaction_probability`，不调用 LLM |
| 收藏/分类/发表情 | `/收藏表情 [分类]`、`/表情分类`、`/表情列表 [分类]`、`/发表情 [分类]` | 支持引用图片或本会话最近媒体；默认不自动收藏普通图片；收藏到 `data/plugin_data/qqbot_enhancer/collected_stickers/`，SHA-256 去重 |
| AI 自主发表情 | 机器人按语境/情绪自行决定附带内置或收藏表情，无需用户命令 | `sticker_ai_select_enabled`、`sticker_ai_marker_enabled`、`sticker_ai_select_probability`；不启用电脑控制/终端/工具权限 |
| 本地表情包 | 回复时按概率附带内置 8 张 PNG 或用户收藏表情 | `sticker_probability=0.35`、`collected_sticker_use_probability`，不额外消耗 LLM token |
| 本地点歌/唱歌 | `/唱歌`、`/唱歌 关键词`、`/点歌 关键词` | 读取 `songs/` 本地授权音频；不调用 LLM/TTS；默认上限 20 MB |
| 插件用法 | `/option`、`/插件用法`、`/用法` | 显示本插件用法和已加载插件元数据 |
| 饭店查询 | `/food 火锅`、`/food 南关区 火锅`、`/吃什么` | 固定 food.dlsz.top 公开 API；最多5家；不调用 LLM |

配置入口（推荐）：

```text
QQ Bot Studio 控制台 → 实例 → 插件、记忆与资源 → 选择对应插件（如 QQ 核心回复 / 表情 / 美食）→ 编辑参数
```

在参数面板里搜索 `reply_probability`，修改数字或拖动滑杆后保存。AstrBot 在线时会尽量热重载；提示需要重启时，重启该实例的 AstrBot 容器后生效。

也可以从 AstrBot WebUI 修改：

```text
AstrBot WebUI → 插件 → QQ Bot 增强回复 → 配置
```

常用调整：

- 将 `reply_probability` 从 `0.12` 改为 `0.05` 即约 5%，改为 `0` 就只有 @ 和私聊会回复；
- 增大 `group_cooldown_seconds` / `user_cooldown_seconds` 可以减少刷屏和 token；
- 非 @ 群聊默认不会触发自然语言找图，避免普通聊天被批量扩展成搜索请求；
- `/找图` 默认发 1 张，可配置到 1–5 张；结果依赖 Bing/百度页面结构，页面改版或反爬时可能暂时无结果；
- 图片理解需要可用的多模态 provider。`deepseek-flash` 是否支持图片输入以实际 API 为准，不支持时请配置视觉 provider 或图片转述 provider；
- 表情包只发送本地文件，不会额外调用 LLM。

本地点歌不放任何带版权的歌曲。把自己的授权音频放进实例的插件目录（不要使用 `../` 路径）：

```bash
SONGS="$HOME/.local/share/qqbot-studio/instances/bot-1/data/astrbot/plugins/qqbot_core_reply/songs"
mkdir -p "$SONGS"
cp "/你的授权音频.wav" "$SONGS/"
```

支持 `.mp3`、`.wav`、`.ogg`、`.flac`、`.m4a`、`.aac`；实际播放能力以 AstrBot 和 QQ 官方平台支持为准。

<a id="memory"></a>
### 2.3 记忆模式、群聊/私聊记忆和 token

在控制台实例的 **插件、记忆与资源** 中可切换 `memory_mode`，也可从 AstrBot WebUI 的插件配置切换。

| `memory_mode` | 行为 | token 成本 |
|---|---|---|
| `direct`（默认） | 插件直接调用 provider，只发送系统提示词 + 当前消息，不携带跨轮 AstrBot 会话历史 | 较低，适合低概率陪聊 |
| `astrbot` | 交给 AstrBot 默认 Agent，使用会话管理器、人格、上下文历史和 Agent 能力 | 随历史增长；记忆越长越贵 |

`astrbot` 模式的内存边界：

- 同一个群的群聊共享对应 `event.unified_msg_origin` 的会话记忆；
- 私聊按用户各自的会话保存；
- `/reset` 清空当前会话历史，但保留会话和人格；
- `/new` 切换到新会话，旧历史仍保存在 AstrBot 的本机数据库；
- 这是每实例的会话记忆，不是跨群共享的永久知识库；AstrBot 数据卷删除/重置后历史也会丢失；
- 切换时如果控制台提示需要重启 AstrBot，按提示重启该实例的 AstrBot 容器；旧版本 `unique_session` 设置可能需要重启才迁移成群共享。

省 token 的思路：

- `direct` 模式本来就不携带跨轮历史；`astrbot` 模式历史越长成本越高；
- 降低 `reply_probability`，或增大群/用户冷却；
- 增大 `min_message_chars`，过滤短消息；
- 保持纯图片不参与非 @ 概率回复；
- 减小 `max_images_per_message`（图片 token 通常更贵）；
- 保持系统提示词简洁稳定，便于 DeepSeek Context Caching 命中；
- 表情包和本地点歌不调用 LLM，不消耗模型 token。

<a id="plugin-selector-stats"></a>
### 2.4 插件选择器、存储/内存统计

控制台每个实例展开 **插件、记忆与资源**：

- **插件管理**：显示 AstrBot 已安装/内置插件及其启用状态，可切换启用/禁用。插件接口需要 AstrBot 可访问；AstrBot 停止或接口不可达时会显示错误，不影响控制台其他功能。部分变更可能需要重启 AstrBot。
- **记忆模式**：选择 `direct` 或 `astrbot`，并为该实例保存。
- **存储与内存**：显示实例数据目录占用、宿主机内存、Docker 容器 CPU/内存。Docker 或某个指标不可用时显示“未知”，不会猜测或导致整个控制台失败。
- 控制台顶部还会显示全局数据目录占用、宿主机内存和磁盘可用空间。

<a id="update-mirrors-docker"></a>
### 2.5 整项目自动更新、镜像节点和 Docker 修复

#### 整项目自动更新：更新通道与版本回滚

控制台右上角 **检查更新** 打开「更新与回滚」面板，当前版本、提交号、大版本和工作区状态一目了然：

- **稳定版**：只跟随 GitHub 正式发布，`origin/main` 快进更新；不会自动合并分叉提交；
- **测试版**：自动发现最新 `alpha/beta/rc` 预发布标签，不接受任意分支、提交号或链接；
- **回滚**：可在**当前大版本**内选择任意正式小版本（例如 v4.2.0 → v4.0.0 或 v4.1.1），列表带日期、提交和说明；跨大版本回滚（v4.x → v3.x）直接拒绝；
- **安全**：要求干净 Git 工作区，同一时间只允许一个更新/回滚任务；切换前自动备份代码（git bundle）和实例配置/密钥文件到 `~/.local/share/qqbot-studio/backups/`；切换失败会恢复原版本；
- **数据**：只切换代码，从不覆盖实例数据目录、数据库和 `config.json`；若目标版本的数据/配置 schema 低于本地，默认拒绝，需显式勾选「允许 schema 降级」才继续；
- **版本更新脚本**：切换代码后、重启服务前自动执行新版本内的 `scripts/update.sh`；脚本必须幂等、只做本地迁移，超时 600 秒，失败会恢复原版本。输出写入 `~/.local/share/qqbot-studio/release-update.log`（0600，已脱敏）；
- **历史**：`~/.local/share/qqbot-studio/update-history.json` 记录 from/to 版本、提交、通道、时间、结果，错误信息会脱敏；完整安装日志仍在 `auto-update.log`。

按钮在工作区有改动、已有任务运行或部署进行中时会禁用并说明原因。Windows 独立项目没有同一套网页自动更新，请按 Windows 仓库说明手动更新。

#### 镜像节点

部署向导的 **Docker 镜像节点** 卡片会读取节点、自动测速并显示延迟/状态。默认节点包括：

```text
DaoCloud   m.daocloud.io/docker.io/
1ms.run    docker.1ms.run/
轩辕镜像   docker.xuanyuan.me/
RatHub     hub.rat.dev/
```

可以添加自定义镜像前缀，但不要填写 `http://` 或 `https://`，格式只填主机和路径，例如：

```text
registry.example.com/
```

部署时先使用选中的节点，失败后按候选顺序回退。节点测速只说明网络端点可达，不代表一定有所需镜像。已有镜像可离线导入：

```bash
docker load -i qqbot-images.tar
```

程序会优先检查本地镜像，已存在时跳过重复拉取。

#### Docker 检查和容器修复

`start.sh` / `console.sh` 启动管理服务时会检查 Docker CLI、Compose、daemon 和已有实例容器；容器停止、缺失、重启中或健康检查异常时，会尝试 Compose `up -d` 或 restart，并把失败原因打印到终端/控制台。Docker 本身不可用时，先在终端修复 Docker：

```bash
docker --version
docker compose version
docker info
```

如果服务启动失败，查看日志：

```bash
tail -n 100 ~/.local/share/qqbot-studio/studio-server.log
```

完整 Docker 排错见 [`docs/server-troubleshooting.md`](docs/server-troubleshooting.md)。

<a id="security"></a>
### 2.6 安全组、防火墙和 SSH 隧道

v2 默认管理服务监听 `0.0.0.0:8765`，新实例的 AstrBot WebUI 端口也会按默认 `BIND_ADDRESS=0.0.0.0` 发布。这样做方便从电脑/手机访问，但也意味着配置不当时会暴露到整个网络。

最低要求：

1. 用腾讯云安全组/轻量应用服务器防火墙限制来源 IP，不要长期使用 `0.0.0.0/0`；
2. 不要把管理端口和 AstrBot WebUI 长期公开到公网；
3. 优先使用可信内网、VPN、Tailscale/WireGuard 或 SSH 隧道；
4. 不要把本工具当作多租户安全隔离方案；
5. 使用 QQ 小号并遵守 QQ 用户协议。

只允许本机访问：

```bash
./install.sh --ip 127.0.0.1 --port 8765
# 已安装后：
./console.sh --ip 127.0.0.1 --port 8765
```

在自己电脑建立 SSH 隧道：

```bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器IP
```

然后本机打开 `http://127.0.0.1:8765/console`。要看实例 AstrBot WebUI，请按实例 `config.json` 中的实际端口再追加 `-L`，例如实例端口为 5000：

```bash
ssh -N \
  -L 8765:127.0.0.1:8765 \
  -L 5000:127.0.0.1:5000 \
  用户名@服务器IP
```

腾讯云安全组、CVM/轻量防火墙、Ubuntu ufw/firewalld 的逐步命令，以及 `0.0.0.0` 和 `127.0.0.1` 的排查，见 [§3.3](#faq-0000-127) 和 [§3.4](#faq-tencent-cloud)，完整版见 [`docs/server-troubleshooting.md`](docs/server-troubleshooting.md)。

---

<a id="part-3"></a>
## 第三部分：教程和常见问题

这一部分按“症状 → 原因 → 最短修复”给出用户真实踩过的坑。更长的 Docker、systemd、iptables 操作放在 [`docs/server-troubleshooting.md`](docs/server-troubleshooting.md)。

<a id="faq-git-ssl"></a>
### 3.9 Git 报 `Unsupported SSL backend 'openssl'`

**症状**

```text
fatal: Unsupported SSL backend 'openssl'. Supported SSL backends:
        gnutls
fatal: remote helper 'https' aborted session
```

**原因**：Git/libcurl 实际使用 GnuTLS，但当前 Git 配置或环境变量强制指定了 OpenSSL。常见于从其他机器复制 `~/.gitconfig`、执行过 `git config --global http.sslBackend openssl`，或 shell 里有 `GIT_SSL_BACKEND=openssl`。

**最短修复**

```bash
git config --show-origin --show-scope --get-all http.sslBackend
env | grep -i GIT_SSL_BACKEND

git config --global --unset-all http.sslBackend 2>/dev/null || true
git config --local --unset-all http.sslBackend 2>/dev/null || true
sudo git config --system --unset-all http.sslBackend 2>/dev/null || true
unset GIT_SSL_BACKEND

git pull --ff-only
```

不要用 `http.sslVerify=false` 作为长期修复。详细命令见 [排错指南](docs/server-troubleshooting.md#git-ssl)。

<a id="faq-docker-false-negative"></a>
### 3.2 Docker 已安装，但启动器仍提示“未检测到 Docker”

**症状**：`docker --version`、`docker compose version`、`docker info` 都正常，但 `./install.sh` 或 `./start.sh` 报“未检测到 Docker”。

**原因**：v2.0.0 曾把不存在的工作目录 `~/.local/share/qqbot-studio/doctor` 作为 `subprocess.run(cwd=...)`，目录缺失时被误判成找不到 Docker 命令。这是旧版 bug，不是 Docker 本身坏了。

**最短修复**：升级到 v2.0.1 或更高版本，然后重启管理服务：

```bash
cd "/你的路径/QQ Ai bot"
git pull --ff-only
./console.sh --restart-service

docker --version
docker compose version
docker info
```

不能立即更新时的旧版临时绕过：

```bash
mkdir -p "$HOME/.local/share/qqbot-studio/doctor"
./start.sh --ip 0.0.0.0 --port 8765
```

如果 `docker info` 本身报 `permission denied`，那是 Docker 用户组/权限问题，不是这个目录 bug：

```bash
sudo usermod -aG docker "$USER"
# 注销后重新登录，再运行 docker info
```

<a id="faq-0000-127"></a>
### 3.3 `0.0.0.0` 访问不了 / 面板只监听 `127.0.0.1`

**症状**

- 服务器本机 `curl http://127.0.0.1:8765/healthz` 正常，但自己电脑打不开；
- 或 `ss` 看到管理服务只监听 `127.0.0.1:8765`；
- 或 AstrBot WebUI 链接打不开。

**原因**：绑定地址、实际端口、云安全组、Ubuntu 防火墙、`public_host` 是四件不同的事。只改 `public_host` 不会改变监听地址。

**最短排查**

```bash
# 1. 服务是否监听 0.0.0.0
ss -ltnp 2>/dev/null | grep 8765
# 如果看到 127.0.0.1:8765，重新用 0.0.0.0 启动/保存配置
./console.sh --ip 0.0.0.0 --port 8765 --public-host 你的公网IP或域名

# 2. 实例实际 WebUI 端口
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# 3. 服务器本机检查
curl -I http://127.0.0.1:8765/healthz
```

如果已经是 `0.0.0.0:8765`，但外网仍打不开，继续检查 [腾讯云安全组/轻量防火墙](#faq-tencent-cloud) 和 Ubuntu ufw/firewalld。旧的 v1 容器只监听 `127.0.0.1`，不会被 v2 控制台自动迁移；备份后按 [旧容器处理](#faq-port-conflict) 删除并重新创建。

<a id="faq-tencent-cloud"></a>
### 3.4 腾讯云安全组 / 轻量应用服务器防火墙（Lighthouse）

**症状**：服务器本机可以访问，但自己的电脑访问 `http://服务器公网IP:8765/console` 超时或拒绝。

**原因**：腾讯云安全组/轻量防火墙在虚拟机外，Ubuntu ufw/firewalld 和 Docker 发布规则在虚拟机内；任何一层没放行都可能打不开。

**最短步骤**

1. 腾讯云 CVM：进入 **云服务器 → 实例 → 安全组 → 入站规则**；轻量应用服务器：进入 **服务器 → 防火墙**。
2. 添加 TCP `8765`，来源填你自己的公网 IP/32；临时测试才用 `0.0.0.0/0`，用完立即收紧。
3. 部署后从控制台/实例 `config.json`/`docker ps` 查看该实例的实际 WebUI 端口，再按需放行；每个实例只发布一个 AstrBot WebUI 端口。
4. 检查 Ubuntu 本机防火墙：

```bash
sudo ufw status verbose
sudo ufw allow OpenSSH
sudo ufw allow 8765/tcp
```

5. 最后在浏览器打开 `http://服务器公网IP:8765/console`；如果仍失败，看 `ss -ltnp | grep 8765` 和 `tail -n 100 ~/.local/share/qqbot-studio/studio-server.log`。

> Docker 发布端口可能绕过部分 ufw 规则；云安全组/轻量防火墙才是公网边界。不要把 AstrBot WebUI 端口长期开放给 `0.0.0.0/0`。

<a id="faq-console-status"></a>
### 3.5 控制台状态读取失败 / 实例状态异常

**症状**：控制台某个实例显示“实时状态读取失败”，或打开 `/api/state` 返回 `status_error`；其他实例可能仍正常。

**原因**：通常是 Docker daemon、单个容器、实例配置/权限或 AstrBot API 临时不可用；如果整个页面打不开，则是管理服务本身没启动。

**最短修复**

```bash
# 管理服务健康
curl -I http://127.0.0.1:8765/healthz
tail -n 150 ~/.local/share/qqbot-studio/studio-server.log

# Docker 和实例容器
docker info
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# 查看实例配置
cat ~/.local/share/qqbot-studio/instances/bot-1/instance.json
cat ~/.local/share/qqbot-studio/instances/bot-1/config.json
```

如果管理服务本身异常，先重启：

```bash
./console.sh --restart-service
```

不要因为某个实例状态读取失败就直接删除数据目录。Docker 修复和容器重启后再刷新控制台；具体容器错误看 [Docker/console 排查](docs/server-troubleshooting.md#console-status)。

<a id="faq-port-conflict"></a>
### 3.6 端口冲突、旧容器和旧实例

**症状**：控制台显示端口冲突、`port is already allocated`，或 AstrBot WebUI 链接打开后不是当前实例。

**原因**：旧版本/旧实例容器、Docker 残留代理、手工启动的进程，或旧 v1 容器仍绑定固定端口。

**最短修复**

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
ss -ltnp 2>/dev/null | grep -E ':(8765|5000|5001|5002)\b'
```

- 在控制台删除不再需要的实例；删除前备份实例目录，删除不可恢复。
- 旧 v1 容器（如 `qqbot-astrbot`）可能仍监听固定端口。确认不再需要后，用 `docker rm -f <容器名>` 删除；删除容器不会删除宿主机数据。
- Docker Desktop/旧代理在容器停止后仍可能短暂占用端口，可重启 Docker Desktop/Docker daemon；Linux 服务器请先确认不会影响其他容器。
- 新建实例会自动分配新的单端口；旧实例不会自动迁移端口。

<a id="faq-windows"></a>
### 3.7 Windows / Docker Desktop 未验证

**症状**：在 Windows 上运行 `./install.sh`、`screen` 或 `sudo` 失败；Docker Desktop 未启动或使用 Windows containers；EXE/端口/防火墙行为与 Linux 不同。

**原因**：Linux 主项目依赖 screen、systemd/sudo、Linux bind mount 和 Linux Docker 端口语义。Windows 版是独立发行项目；本项目没有 Windows 真机 E2E 结果。

**最短处理**

- 不要在主仓库用 Linux 的 `install.sh` 跑 Windows。
- 使用独立 Windows 仓库和 Windows 专用 README/EXE：<https://github.com/Stevehim2007/qqbot-studio-windows>
- 确认 Docker Desktop 使用 **Linux containers**、WSL2 backend，并已启动 Docker engine。
- 当前不承诺 Windows 生产可用；按独立仓库“已验证/未验证”章节逐项测试，并把 Windows 日志发到 Windows 项目仓库。

<a id="faq-docker-install"></a>
### 3.8 Docker 安装不完整、Docker daemon 启不来

**症状**：安装 Docker 时取消/断网，向导仍打开但无法部署；或 systemd 报 `Start request repeated too quickly`。

**原因**：Docker CLI/Compose/daemon 不完整；或 `daemon.json`、containerd、旧 PID、iptables、磁盘空间等导致 daemon 启动失败。

**最短修复**

```bash
docker --version
docker compose version
docker info
sudo journalctl -xeu docker.service -b --no-pager -n 200
sudo python3 -m json.tool /etc/docker/daemon.json 2>/dev/null
```

`Start request repeated too quickly` 只是 systemd 限流，真正错误在前面的 journal。若是 `daemon.json` 里还有 `<你的镜像地址>` 占位符或非法 URL，先备份并移除：

```bash
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak 2>/dev/null || true
sudo rm -f /etc/docker/daemon.json
sudo systemctl daemon-reload
sudo systemctl reset-failed docker.service docker.socket
sudo systemctl restart containerd
sudo systemctl start docker
```

也请检查 containerd、旧 `/var/run/docker.pid`、iptables/nftables 和 `df -h`。完整分步命令在 [`docs/server-troubleshooting.md`](docs/server-troubleshooting.md#docker-daemon-start)。

<a id="faq-deploy-progress"></a>
### 3.10 部署进度、浏览器关闭和后台服务

**症状**：关掉浏览器/终端后回来，看不到部署进度；或管理服务重启后任务不再运行。

**原因**：管理服务可能被系统/用户停止，实例任务被标记为中断；或者浏览器只是重新打开了控制台，没有进入该实例向导。

**最短处理**

```bash
screen -ls
screen -r qqbot-studio-server
tail -n 150 ~/.local/share/qqbot-studio/studio-server.log
```

- 部署页会按 `active_job_id` 恢复；任务被标记中断时使用页面上的重试入口。
- 重新打开时用控制台的 **打开向导** 进入对应实例，不要用 `start.sh` 创建新实例。
- 管理服务启动失败时先看日志；Docker daemon 没运行，部署任务不可能继续。

<a id="part-4"></a>
## 第四部分：项目关系、声明与致谢

### 4.1 项目关系

| 项目 | 关系 |
|---|---|
| **QQ Bot Studio（本仓库）** | Ubuntu/Linux 主项目：安装器、部署向导、多实例控制台、AstrBot + QQ 官方平台编排 |
| **QQ Bot Studio Windows** | **独立项目**：Windows 10/11 + Docker Desktop + WSL2 + EXE/PowerShell；版本文档、数据目录、升级和 issue 与 Linux 主项目分开 |
| **AI 助手 B** | **可选独立子项目**：生成式使用助手；主项目只负责可选 clone/更新、配置/启动管理，不读取/传递其 API Key |
| **AstrBot** | 上游聊天机器人框架，本项目不重分发其镜像/二进制 |
| **QQ / DeepSeek** | 第三方账号、协议和 API 服务；使用须遵守各自用户协议、许可、隐私政策和当地法律 |

相关链接：

- 主项目：<https://github.com/Stevehim2007/qqbot-studio>
- Windows 独立项目：<https://github.com/Stevehim2007/qqbot-studio-windows>
- AI 助手 B：<https://github.com/Stevehim2007/qqbot-studio-ai-assistant>
- AstrBot：<https://github.com/AstrBotDevs/AstrBot>
- DeepSeek API 文档：<https://api-docs.deepseek.com/>
- 本项目许可证：[MIT License](LICENSE)

### 4.2 声明

- 本项目不是腾讯/QQ 官方产品，与 AstrBot、DeepSeek 及腾讯无隶属或背书关系。
- 本项目不重分发 AstrBot、QQ 或 DeepSeek 的二进制/镜像/服务；部署时会从相应上游来源拉取镜像或调用 API。各第三方项目适用其自身许可证、EULA 和服务条款，请自行核对。
- QQ 官方机器人接入须遵守 QQ 开放平台规则、频控和权限要求；本工具不会绕过平台验证或风控。
- 本项目不收集、不保存 QQ 密码或 QQ 开放平台凭据明文；AppID/AppSecret 仅保存在本地实例配置和 secrets 中。
- 默认 `0.0.0.0` 只适用于你已配置安全组/防火墙/VPN 的场景。管理控制台、AstrBot WebUI 和凭据页面都有敏感性，请限制来源 IP。
- 本项目不是多租户安全隔离方案；共享主机、共享浏览器和共享数据目录不在安全承诺内。
- 软件按“现状”提供，不提供明示或暗示担保；使用第三方协议端、API、镜像和云服务产生的账号、数据、合规或费用风险由使用者自行评估和承担。

### 4.3 致谢

感谢 AstrBot、DeepSeek 及所有上游贡献者和社区用户。也感谢提交真实故障日志、复现步骤、文档修正和测试反馈的用户；这些踩坑记录被保留在 [排错指南](docs/server-troubleshooting.md) 和 [常见问题](#part-3) 中。

<a id="docs-index"></a>

<a id="changelog"></a>
### v4.0.0 重点更新

- 会话级提示词模板：一个 AstrBot conversation 绑定一个提示词、一个 persona 和一段独立记忆。
- 置顶模板只作为新建会话默认值；切换/置顶模板不再自动重建当前会话。
- `/新会话 名称 [提示词标题]` 创建并立即切换会话；`/会话 名称` 只切换已有会话。
- `/会话列表` 显示会话数量/上限；超过上限自动清理最老非当前会话；`/删除会话 名称|ID` 可手动删除。
- 向导中的机器人提示词改为只读，提示词统一在控制台模板库管理。
- 损坏的插件配置会自动从最近良好备份/缓存/Studio 镜像/schema 恢复，不再阻塞 AI 工具权限；记忆模式不会被错误重置为 `direct`。
- 新增 `/表情状态`、会话清理、`/option` 更新、详细配置恢复提示。
- 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 更多文档

完整文档索引见 [`docs/README.md`](docs/README.md)：

- [QQ Bot 增强插件详解](docs/plugin-qqbot-enhancer.md)
- [服务器部署与实战排错](docs/server-troubleshooting.md)
- [v2 控制台/API 契约](docs/ui-api-contract-v2.md)
- [AstrBot / QQ 官方平台 / DeepSeek 集成调研（历史资料）](docs/integration-research.md)
- [v2 QA 报告与真实 E2E 待测清单](docs/qa-report-v2.md)
- [Windows 主仓库历史移植资料](windows/README.md)
