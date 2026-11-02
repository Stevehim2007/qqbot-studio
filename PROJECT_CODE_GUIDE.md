# QQ Bot Studio 全项目代码导读

> 版本：Linux `4.1.1` / `qqbot_enhancer 2.7.0`  
> 目标：用一份文档说明当前仓库每个主要代码模块“做什么、怎么实现、和谁集成”，方便对照学习。  
> 说明：Windows 是独立仓库，本文在最后给出独立仓库的模块地图；Linux 主仓库不包含 Windows EXE 源码。

---

## 1. 一句话架构

```text
QQ 客户端
  ↓
SnowLuma / OneBot v11 协议端
  ↓ 反向 WebSocket
AstrBot（机器人运行时：事件、LLM、会话、人格、插件）
  ↓
qqbot_enhancer（AstrBot 插件：概率回复、识图、表情、唱歌、food、网页工具）
  ↑
QQ Bot Studio（安装器、部署向导、多实例控制台、配置/日志/更新 API）
  ↓
Docker Compose（AstrBot + SnowLuma + noVNC）
```

分工：

- **SnowLuma**：QQ 登录、OneBot v11、消息收发。
- **AstrBot**：LLM provider、对话、人格、事件管线、插件宿主。
- **qqbot_enhancer**：产品级聊天增强与安全工具。
- **QQ Bot Studio**：把以上组件部署、管理、诊断、更新。
- **Windows 仓库**：同一套 core/plugin/static 的桌面化发行版。

---

## 2. 仓库目录总览

```text
QQ Ai bot/
├── install.sh                 # Ubuntu 安装入口
├── start.sh                   # 新建实例并打开向导
├── console.sh                 # 打开已有实例控制台
├── qqbot_studio/              # Linux 管理服务（Python + 原生 Web UI）
├── plugins/qqbot_enhancer/    # AstrBot 增强插件
├── tests/                     # 单元/契约/前端/E2E 测试
├── docs/                      # 架构、API、插件、排错、QA 文档
├── examples/                  # AstrBot / SnowLuma 配置样例
├── scripts/                   # 安装、更新、E2E、桌面入口脚本
├── windows/                   # Windows Docker 版 PowerShell 辅助脚本
└── assets/                    # 图标资源
```

核心原则：

1. **控制台不保存用户 API Key**：Key 写入实例 AstrBot 配置，控制台只做传输/状态检查。
2. **实例隔离**：每个实例有独立目录、端口块、Compose 项目、容器名、QQ 数据。
3. **失败降级**：AstrBot API、Docker 状态、资源统计失败时，控制台保留本地插件目录和其他功能。
4. **安全工具而非电脑控制**：模型只能输出白名单 marker，由插件路由到固定工具。
5. **固定第三方目标**：food、网页、图片源等都有 host/path/大小/超时校验。

---

## 3. Linux 管理服务 `qqbot_studio/`

### 3.1 `__init__.py` / `__main__.py`

- 定义 `__version__`。
- `python -m qqbot_studio` 入口。

### 3.2 `launcher.py` — 命令行启动器

负责：

- `install` / `start` / `console` / `deploy` 等 CLI；
- 生成/读取 Studio 配置；
- 启动管理 HTTP 服务；
- 检查端口、PID、健康状态；
- 用 `screen` 保持后台服务；
- 调用 Docker 检查与修复；
- 打开浏览器；
- 处理 `--no-open`、自定义 host/port。

启动链：

```text
start.sh / console.sh
  → qqbot_studio.launcher
  → StudioManager
  → screen 启动 qqbot_studio.server
  → 浏览器打开 /console 或 /wizard
```

### 3.3 `studio.py` — 产品级状态编排

`StudioManager` 是管理服务的高层门面，负责：

- Studio 配置默认值/迁移；
- 实例创建、读取、列表；
- 管理服务设置；
- 调用 `Store`、`DeployManager`、`DockerController`；
- 生成实例 ID / 数据目录；
- 安装/更新检查；
- 管理进程状态。

它是 UI Server 和底层存储/部署之间的中间层。

### 3.4 `store.py` — 本地持久化与秘密

负责：

- `~/.local/share/qqbot-studio` 数据目录；
- 实例索引、Studio 配置、secrets；
- 敏感 key 脱敏；
- 生成 token、VNC 密码、管理员密码；
- 文件权限收敛；
- 原子写入。

注意：AstrBot 的 DeepSeek Key 不进入 Studio 实例索引；QQ 登录密码不保存。

### 3.5 `server.py` — 管理 HTTP 服务

用标准库 `ThreadingHTTPServer` 实现，避免引入 Web 框架。

主要职责：

- 静态文件：`index.html`、`console.html`、`assistant.html`、JS/CSS；
- 部署向导 API；
- 实例 CRUD；
- 部署任务状态；
- Docker/容器状态；
- 插件列表、插件配置读取/保存；
- 提示词模板 API；
- 日志读取；
- 存储/内存统计；
- 更新状态/触发更新；
- AI 助手 B 管理；
- 管理 token 校验。

请求处理流程：

```text
HTTP Request
  → token 校验
  → 路径分发
  → AppContext / StudioManager / DeployManager / DockerController
  → JSON 响应
```

### 3.6 `deployer.py` — 部署/插件/配置/人格桥接

最重要的后端模块之一。

`DeployManager` 负责：

- 创建实例目录与 Compose 文件；
- 部署任务 Job 状态机；
- 启动/停止/重建容器；
- 生成 AstrBot/SnowLuma 配置；
- 通过 AstrBot Dashboard API 登录；
- 插件列表、插件配置读取/保存；
- 人格创建/更新；
- 提示词模板持久化；
- 配置损坏恢复链；
- 本地插件目录 fallback；
- 版本、端口、健康检查。

插件配置数据流：

```text
Studio UI
  → /api/instances/<id>/plugins/<plugin>/config
  → DeployManager
  → AstrBotClient
  → AstrBot Dashboard API
  → 插件 _conf_schema.json + 实例配置
```

### 3.7 `astrbot_api.py` — AstrBot HTTP 客户端

负责：

- AstrBot Dashboard 登录；
- token 缓存；
- setup-status 等待；
- v1/legacy 登录接口回退；
- persona create/update；
- 瞬时断连重试（Broken pipe、Connection reset）；
- 401/403 不误重试；
- 本地 loopback 绕过 HTTP 代理。

当前重试策略：

```text
Broken pipe / reset / timeout
  → 最多 3 次，0.3s/0.8s 退避
  → 重建 urllib opener
  → 强制登录时先清 token
```

### 3.8 `configgen.py` — 配置/Compose 生成器

负责：

- 选择空闲端口；
- 分配实例端口块；
- 生成 `docker-compose.yml`；
- 生成 `.env`；
- 生成 AstrBot `cmd_config.json`；
- 生成 SnowLuma OneBot 配置；
- 生成密码 hash / token；
- 镜像节点前缀；
- OneBot WebSocket 地址与鉴权。

端口约定：

```text
base     = AstrBot WebUI
base + 1 = SnowLuma WebUI
base + 2 = noVNC
```

### 3.9 `dockerctl.py` — Docker 控制

封装：

- `docker --version` / `docker compose version`；
- Docker daemon 检测；
- Compose up/down；
- 容器 restart；
- 容器状态/健康状态；
- stats（CPU/内存）；
- 错误信息清理。

所有子进程调用集中在这里，便于测试和替换。

### 3.10 `resource_stats.py` — 资源统计

- 实例目录占用；
- 磁盘；
- 宿主机内存；
- Docker stats 解析；
- 目录扫描警告；
- 失败返回“未知”，不抛整页错误。

### 3.11 `updater.py` — 项目更新

- 检查 Git 远端更新；
- 防止脏工作区覆盖；
- 执行 fast-forward；
- 同步内置插件到已有实例；
- 重启 AstrBot/管理服务；
- 依赖 `gitutil.py` 的安全 git 调用。

### 3.12 `assistant_ext.py` — AI 助手 B

- clone/更新独立助手仓库；
- 读取其配置；
- 启动/停止助手进程；
- API Key 不经过 Studio 持久化；
- 管理助手服务状态。

### 3.13 `contracts.py` — 类型/接口约定

用 `Protocol` 定义：

- ConfigStore；
- StateProvider；
- RuntimeBackend；
- DeploymentProvider；
- ExtensionManager；
- ResourceStats；
- AstrBotClient；
- UIComponentProvider。

作用：让 server/deployer/UI 依赖接口，便于 mock 和单元测试。

---

### 3.14 `gitutil.py` / `__init__.py`

- `gitutil.py`：统一、安全地执行 git 子进程，供 updater 使用。
- `__init__.py`：版本号；`__main__.py`：模块入口。

## 4. 前端 `qqbot_studio/static/`

### 4.1 `index.html` / `app.js` / `styles.css`

部署向导：

- 环境检查；
- 镜像节点；
- QQ / API Key；
- 提示词；
- 容器部署；
- 扫码/noVNC；
- 完成页；
- 任务轮询/错误提示；
- 部署完成后锁定环境/配置步骤。

### 4.2 `console.html` / `console.js` / `console.css`

多实例控制台：

- 实例卡片；
- 部署/启动/停止/删除；
- AstrBot / SnowLuma / noVNC 入口；
- 插件、记忆、资源面板；
- 插件参数 modal；
- 提示词模板库；
- 日志查看；
- AI 助手入口；
- 更新/状态提示。

插件参数面板的数据流：

```text
console.js
  → services/plugin-config.js
  → /api/instances/:id/plugins/:plugin/config
  → AstrBot API
  → schema + values
  → 渲染 bool/int/float/string/select/list 控件
```

当前面板已经有搜索、默认值、slider、保存/取消/错误状态。  
分组导航、只看已修改、高级折叠属于下一阶段 UI 增强。

### 4.3 `services/api.js`

- 统一 fetch；
- APIError；
- 布尔解析；
- 文本/记录读取；
- token header；
- 401/网络错误处理。

### 4.4 `services/plugin-config.js`

- schema 原始格式归一化；
- 支持 array / fields / properties / schema 嵌套；
- 敏感字段过滤；
- 字段类型归一化；
- 默认值/options/slider/min/max；
- 插件配置读取/保存 service。

### 4.5 `components/instance-settings.js`

可复用实例设置组件：状态、插件、记忆、资源等渲染逻辑。

### 4.6 `assistant.html`

AI 助手 B 的独立管理页面：状态、启动/停止、打开链接。

---

## 5. QQ 增强插件总览

`plugins/qqbot_enhancer/` 是一个 AstrBot 插件，不是独立进程。

它依赖：

- AstrBot 事件管线；
- `AstrMessageEvent`；
- `Context` / provider / conversation / persona manager；
- `Comp.Plain` / `Comp.Image` / `Comp.Record`；
- OneBot v11 消息段；
- Python 标准库 HTTP / SQLite / 本地文件。

### 5.1 `main.py` — 插件主类与事件编排

`QQBotEnhancerPlugin(Star)` 是核心。

负责：

- `@`/私聊/概率回复决策；
- 群冷却/用户冷却/最短长度；
- 图片理解；
- 图片搜索；
- 表情包反应；
- 收藏/分类/发表情；
- 本地歌曲；
- 会话命令；
- 人格/提示词选择；
- 安全工具 catalog 注入；
- `on_llm_request` 上下文注入；
- `on_decorating_result` 结果后处理；
- food 查询/提交；
- web fetch / 自动网页阅读；
- 限流、日志、错误回复。

主要事件流：

```text
消息事件
  → 是否命令
  → 是否 @/私聊/概率命中
  → 图片处理
  → 安全工具意图 / food / web
  → prompt 构建
  → provider 调用
  → on_decorating_result
  → 文本/图片/语音/表情 marker
  → OneBot 发送
```

### 5.2 `config.py` + `_conf_schema.json`

- `ConfigReader` 读取 AstrBot 配置；
- 提供 bool/int/float/str/list 类型安全读取；
- `_conf_schema.json` 定义 256+ 字段的默认值、类型、说明、slider、options；
- 配置恢复链：schema defaults → 实例文件 → `.bak` → memory cache → Studio mirror。

### 5.3 `conversations.py`

对话辅助：

- 标题归一化；
- conversation id/title/persona；
- 按标题查找；
- 创建时间；
- 短 ID。

### 5.4 `personas.py`

人格与提示词模板：

- `Persona` / `PersonaConfig`；
- `group_personas` JSON 解析；
- `resolve_persona`；
- `PromptProfile` / `PromptProfileConfig`；
- 提示词模板解析、默认模板、按标题查找。

提示词模板是“模板库”，不是全局自动切换：

- 新建会话时可按标题绑定；
- 置顶只作为新建会话默认；
- 已有会话保持自己的 title/persona/memory。

### 5.5 `persona_bridge.py`

AstrBot memory 模式的适配层：

- `AgentPersonaBridge`；
- 发送者身份上下文；
- 群聊最近消息；
- 表情工具 catalog；
- web fetch catalog；
- 自动网页阅读上下文；
- food 提交 catalog；
- 所有注入都应幂等，不重复叠加。

### 5.6 `interfaces.py`

纯协议/数据类：

- `MediaPayload`；
- `StickerRecord`；
- `ImageCandidate`；
- `MediaDecoder`；
- `StickerRepository`；
- `ImageSearchProvider`；
- `SongRepository`；
- `LLMService`。

作用是隔离媒体/表情/搜索/歌曲/LLM 的接口，方便测试和替换。

### 5.7 `media.py`

媒体处理：

- 图片 magic/MIME 检测；
- PNG/JPEG/GIF/WebP 尺寸；
- base64 解码；
- 本地媒体路径校验；
- HTTP URL 清洗；
- 防止任意本地文件读取。

### 5.8 `image_search.py`

图片搜索候选排序：

- 关键词变体；
- 标题/描述的文本相关性；
- 过滤低质量/不安全候选；
- 供 `/找图` 和自然语言找图使用。

### 5.9 `sticker_store.py` / `sticker_intent.py` / `sticker_reclassify.py` / `stickers/`

表情系统：

- `stickers/*.png` 内置 8 张表情；
- `sticker_rules.json` 本地语义/分类规则；
- `CollectedStickerStore` 收藏文件、SHA-256 去重、index；
- 解析“发表情/收藏表情/分类”等自然语言；
- 本地规则 + 可选 AI 语义分类；
- 安全候选匹配，不访问任意文件。

### 5.10 `songs.py` / `songs/`

本地点歌：

- `resolve_song_file` 防目录穿越；
- 扫描授权本地音频；
- 支持 mp3/wav/ogg/flac/m4a/aac；
- `LocalSongRepository` 提供候选；
- 不调用 LLM/TTS，不下载版权音乐。

### 5.11 `food.py`

food.dlsz.top 的全部纯业务逻辑：

- 固定 host `food.dlsz.top`；
- 查询 URL 构造；
- 查询结果格式化；
- 自然语言提交字段校验；
- 分数 0.1–5；
- 标签拆分；
- 完整地址拆分：
  ```text
  中国牛逼市
    → 中国 / 牛逼市
  ```
- 位置路径匹配；
- 固定 POST `/api/upload`；
- `new_location_name` / `new_location_parent_id`；
- 错误码/中文错误兼容；
- 搜索排序：
  - total/taste/value/reviews/name/latest；
- 结果格式化。

`food.py` 不直接依赖 AstrBot；AstrBot 相关调用在 `main.py` 中。

### 5.12 `web_fetch.py`

安全网页读取：

- 只允许 http/https；
- 公网 IP 校验；
- localhost/private/link-local/metadata 阻止；
- DNS 解析后固定 IP，防 DNS rebinding；
- 逐跳重定向校验；
- 最大 URL 长度；
- 最大响应体；
- 允许的 content-type；
- gzip 拒绝；
- HTML parser 去 script/style；
- visible text 提取；
- `[[tool:` 中和；
- URL 提取/读取意图识别；
- 截断；
- 自动网页阅读的页面结果结构。

### 5.13 `safe_tools.py`

安全工具 marker 协议：

```text
[[tool:send_sticker category="可爱的猫猫"]]
[[tool:fetch_webpage url="https://..." max_chars=4000]]
[[tool:search_food_restaurants name="..." tag="火锅" location="南关区" sort="total"]]
[[tool:submit_food_review restaurant="..." location="..." taste="4.5" value="3.5" comment="..."]] 
```

负责：

- 解析 marker；
- 参数白名单；
- URL/路径/危险引用拒绝；
- 单次 tool 限制；
- enabled tools；
- prompt catalog 文案；
- 不允许任意命令、文件、系统工具。

### 5.14 `llm.py`

LLM 辅助纯函数：

- 提取 AI 表情 marker；
- 解析模型的表情选择 JSON；
- 生成安全的表情选择 system instruction。

---

## 6. 配置、会话与记忆模式

### 6.1 两种 memory mode

- `direct`：
  - 插件自己调用 provider；
  - 只发系统提示 + 当前消息（默认不带跨轮历史）；
  - 省钱。
- `astrbot`：
  - 交给 AstrBot Agent；
  - 使用 conversation、persona、历史和 Agent 能力；
  - 历史越长 token 越多。

长期记忆建议作为 **AstrBot 核心 sidecar**：

```text
AstrBot conversation（短期）
+ memory.sqlite3（长期事实/偏好/经历）
```

不要用长期记忆替代 conversation history。

### 6.2 每会话绑定

当前产品语义：

```text
一个 AstrBot conversation
= 一个提示词模板
+ 一个 persona
+ 一段独立记忆
```

命令：

```text
/新会话 名称 [提示词标题]  创建并立即切换
/会话 名称               只切换已有会话
/会话列表
/删除会话 名称|ID
```

---

## 7. 测试地图 `tests/`

| 文件 | 覆盖 |
|---|---|
| `test_enhancer_plugin.py` | 增强插件：回复决策、表情、歌曲、food、web、工具 marker、配置 |
| `test_astrbot_api.py` | 登录、API 错误、Broken pipe 重试 |
| `test_deployer.py` | 部署、插件 API、配置保存/恢复、人格 |
| `test_store_configgen.py` | Store、端口、Compose、AstrBot/SnowLuma 配置 |
| `test_dockerctl.py` | Docker 命令与状态解析 |
| `test_server.py` | HTTP 路由、鉴权、错误 |
| `test_launcher.py` | CLI、screen、PID、健康 |
| `test_contracts_resource_stats.py` | Protocol 契约、资源统计 |
| `test_prompt_profiles.py` | 提示词模板 |
| `test_memory_stats_resilience.py` | 记忆/统计降级 |
| `test_frontend_components.py` | 前端组件静态断言 |
| `test_wizard_platform_static.py` | 向导锁步骤/平台 |
| `test_windows_port_static.py` | Windows 端口/静态兼容 |
| `test_control_assistant_static.py` | AI 助手页面 |
| `test_task56_regressions.py` | 历史回归 |
| `test_e2e.py` / `tests/e2e/` | 真实 loopback 服务/部署冒烟 |
| `tests/e2e/e2e_smoke.py` | 端到端 smoke 驱动 |

运行：

```bash
python3 -m unittest discover -s tests -q
```

当前目标：**568 tests OK**（Linux）。

---

## 8. 安全边界

必须保持：

1. QQ 密码、API Key 不进入浏览器 localStorage、不写日志、不回显。
2. noVNC 不公开到公网；默认 0.0.0.0 需要防火墙/安全组。
3. 网页/记忆/图片/用户消息都是不可信输入。
4. 网页中的 `[[tool:` 不得被执行。
5. 模型只能调用白名单 marker，不得执行 shell/任意文件/任意 URL。
6. food 只允许固定 host/path，密码默认发送者 QQ，不进 prompt/日志/回复。
7. 记忆数据库损坏/锁定必须 fail-open，不能阻断表情、food、聊天。
8. 删除实例/会话前明确确认，数据库和备份敏感。

---

## 9. 启动与数据流

### 9.1 安装/部署

```text
install.sh
  → 安装 Docker/Compose/Python/screen/git
  → 启动 Studio
  → 浏览器打开向导
  → DeployManager 生成实例目录/Compose/AstrBot/SnowLuma 配置
  → DockerController 启动容器
  → AstrBotClient 等待/登录/配置人格
  → noVNC 扫码
```

### 9.2 聊天

```text
QQ → SnowLuma → OneBot → AstrBot
  → qqbot_enhancer 决策/上下文/工具
  → DeepSeek
  → AstrBot 输出
  → OneBot → SnowLuma → QQ
```

### 9.3 控制台读插件配置

```text
console.js
  → plugin-config.js
  → server.py
  → deployer.py
  → astrbot_api.py
  → AstrBot Dashboard API
```

---

## 10. Windows 独立仓库

当前 Linux 仓库包含 `windows/` 的 PowerShell 辅助脚本，但正式 Windows 桌面版在独立仓库：

```text
qqbot-studio-windows
├── windows/app_shell.py        # WebView2 Docker 桌面壳
├── windows/native_app.py       # Native 桌面壳
├── windows/native_setup.py     # Native 依赖下载/校验/安装
├── windows/native_runtime.py   # AstrBot/SnowLuma/QQ 进程管理
├── windows/common.ps1
├── windows/start.ps1
├── windows/console.ps1
├── windows/install.ps1
└── windows/studio_helper.py
```

桌面壳职责：

- 启动/停止本地或 Docker 服务；
- 左栏控制台/向导；
- 向导步骤锁定；
- 明亮主题；
- 诊断日志/复制/打开目录；
- Native 下载校验官方来源；
- 不保存 QQ 密码/API Key。

---

## 11. 推荐学习顺序

1. `README.md`：产品怎么用。
2. `qqbot_studio/configgen.py`：实例怎么生成。
3. `qqbot_studio/dockerctl.py`：容器怎么起。
4. `qqbot_studio/deployer.py`：部署/插件/人格。
5. `qqbot_studio/server.py`：控制台 API。
6. `qqbot_studio/static/console.js` + `services/plugin-config.js`：前端怎么接。
7. `plugins/qqbot_enhancer/main.py`：插件事件总入口。
8. `safe_tools.py`：安全工具协议。
9. `food.py` / `web_fetch.py`：业务模块。
10. `tests/test_enhancer_plugin.py`：行为契约。
11. `docs/plugin-qqbot-enhancer.md`：配置与限制。
12. Windows 仓库 app_shell/native_app。

---

## 12. 当前限制与下一步

已实现：

- 多实例部署、AstrBot/SnowLuma/noVNC、插件参数、提示词模板、会话；
- 表情、图片理解/搜索、唱歌、food、网页访问/自动阅读、AstrBot Broken pipe 重试；
- Windows v1.2.0 桌面发行。

未完成/人工验证：

- QQ 真实扫码/设备验证；
- SnowLuma EULA/隐私；
- 真实 QQ 消息和 DeepSeek 生成；
- food 后台管理员浏览器点击验证；
- Native 完整下载安装 E2E；
- GitHub Actions sync-upstream（无 token）。

下一步建议：

```text
AstrBot 源码级长期记忆
+ Studio 插件参数分组导航
+ 记忆管理 UI
+ 跨平台同步
```

长期记忆的正确边界：

```text
AstrBot core: memory service / hooks / conversation / persona
qqbot_enhancer: food / web / stickers / songs / tools
QQ Bot Studio: config UI / deployment / memory admin
```
