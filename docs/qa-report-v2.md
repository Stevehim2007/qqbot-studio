# QQ Bot Studio v2 QA 报告

日期：2026-10-29
结论：**条件通过（Conditional Pass）**。自动化、静态检查、模拟运行和本地 HTTP 冒烟测试通过；
真实 Docker 镜像拉取、AstrBot/SnowLuma 启动、QQ 扫码登录、noVNC 和 DeepSeek 实际对话尚未在本环境执行。

## 1. 测试环境

- Linux 沙箱，Python 3.12
- Node.js（用于静态 JS 语法检查）
- 无可用 Docker daemon，无 root，无法启动真实容器
- 无图形界面，无法打开 noVNC 或扫码登录 QQ
- 无真实 DeepSeek API Key，无法验证实际模型回复
- screen 已安装，但沙箱 `/run/screen` 只读；已验证自动降级到 nohup 后台模式

## 2. 通过项

### 2.1 自动化测试

```bash
python3 -m unittest discover -s tests -v
```

结果：**119 tests OK**。

覆盖：

- 设置/密钥存储和权限
- 多实例创建与实例隔离
- Compose 生成、项目名/容器名/网络名隔离
- `0.0.0.0` 端口映射及 AstrBot/SnowLuma WebUI 监听配置
- 镜像源候选顺序、测速、增删选
- 部署流程、镜像拉取回退、详细日志流式回调
- 部署任务持久化、中断恢复、active job 状态
- Docker 容器检查与修复
- 服务器 API、token 鉴权、Host 校验、多实例接口、镜像接口
- 首次运行 IP/端口配置持久化
- Docker 检测使用已存在的工作目录；真实 subprocess 回归测试可避免把 `cwd` 缺失误判为 Docker 未安装
- SnowLuma WebUI 使用稳定 bootstrap 密码环境变量，并兼容日志密码轮换后的同步
- SnowLuma 用户名 `admin` 与“每个新实例首次部署需要扫码登录 QQ”的 UI/README 提醒
- QQ 增强插件 `memory_mode=direct/astrbot`：轻量直连与 AstrBot 默认 Agent 长期记忆模式切换
- SnowLuma WebUI 一键重置密码：备份 webui.json、scrypt 哈希重建、重启容器和日志回退
- Git 自动更新检查、干净工作区/fast-forward 保护、后台更新脚本和更新后服务重启
- 实例状态读取异常时返回可诊断的 `status_error`，避免控制台只显示无上下文的失败
- QQ 增强插件 `/找图`/`/搜图` Bing Images 搜索、安全过滤、冷却与 8 张本地表情包
- 控制台离线“使用助手”知识库，覆盖安装、端口、扫码、SnowLuma、概率、记忆、更新、Windows
- Windows 初始移植的 PowerShell 入口、后台进程管理和静态/跨平台测试（真实 Windows E2E 未验证）
- 实例创建时预留宿主机端口，控制台删除实例时清理容器和实例目录
- `start.sh` 部署模式和 `console.sh` 控制台模式的后台 URL 行为
- QQ 增强插件 20 个 mock 用例（@ 必回、概率回复、图片、表情包、默认 LLM 阻断）

### 2.2 静态检查

```bash
node --check qqbot_studio/static/app.js
node --check qqbot_studio/static/console.js
bash -n start.sh console.sh install.sh scripts/*.sh
python3 -m py_compile qqbot_studio/*.py plugins/qqbot_enhancer/*.py
```

全部通过。

HTML/CSS 静态检查：

- `index.html` 127 个唯一 id，`console.html` 41 个唯一 id
- JS 引用的普通 id 全部存在
- 无外部 CDN/字体/图片引用
- 无动态 `innerHTML`
- 无 `alert()` / `confirm()`
- 无 localStorage/sessionStorage 持久化
- 两个页面均保留 `window.QB_TOKEN = "{{TOKEN}}"` 占位符

### 2.3 HTTP 冒烟测试

以 `0.0.0.0` 启动真实 `StudioManager` 服务，使用外部 Host 头访问，验证：

- `/console`、`/wizard` 200
- 样式和 JS 静态资源 200
- token 注入成功
- `/api/state` 无 token 返回 403，有 token 返回 200
- `POST /api/instances` 创建多实例成功
- `GET /api/instances` 返回所有实例
- 镜像节点列表、添加、选择、删除成功
- 非回环 Host 校验在 `0.0.0.0` 模式下放行

结果：**SMOKE_OK**。

### 2.4 镜像测速

对真实网络执行节点测速：

- DaoCloud：HTTP 401，预期可用，约 197 ms
- 1ms.run：HTTP 401，预期可用，约 484 ms

说明：镜像仓库 `/v2/` 返回 401 表示端点可达且需要认证，是正常的 Docker Registry 响应。

### 2.5 启动器/后台服务

- 首次运行默认 `bind_host=0.0.0.0`、`bind_port=8765`，写入 `studio.json`
- `--ip` / `--port` / `--public-host` 参数覆盖并持久化
- `start.sh` 每次创建新实例
- `console.sh` 打开多实例控制台
- 管理服务启动后 health check 通过
- 沙箱屏幕会话失败时自动降级 nohup，仍能正常提供 HTTP 服务

## 3. 需求验证矩阵

| 需求 | 实现 | 自动化/冒烟 | 真实 E2E |
|---|---|---|---|
| @ 必回 | QQ 增强插件比较 `At.qq` 与 `self_id`，私聊也必回 | mock 通过 | 未验证 |
| 非 @ 概率回复 | 概率 + 群/用户冷却 + 长度/过滤，阻断默认 LLM | mock 通过 | 未验证 |
| 图片识别 | `context.llm_generate(image_urls=...)`，DeepSeek Flash 支持 image，失败可转述降级 | mock + API 文档核对 | 未验证 |
| 发表情包 | `Comp.Image.fromFileSystem` + 4 张本地 PNG + 冷却/防重复 | mock 通过 | 未验证 |
| 更详细下载日志 | 逐镜像 `docker pull` 流式输出到 job logs | 单元测试通过 | 未验证 |
| 自动镜像测速/节点 UI | `/api/mirrors/test` + 前端节点卡片 | 真实测速通过 | UI 真机未验证 |
| 部署中重新进入 | `active_job_id` 自动恢复、中断提示 | 后端 + 前端 mock 通过 | 未验证 |
| 中途退出不永久卡死 | screen 服务 + job 持久化 + 重启标记中断 + 重试入口 | 持久化测试通过 | 未验证 |
| 三脚本 | `install.sh` / `start.sh` / `console.sh` | 语法与启动器测试通过 | 未执行真实安装 |
| 多机器人隔离 | 每实例独立项目/容器/网络/目录/端口 | 单元测试 + API 冒烟通过 | 未启动真实容器 |
| 默认 0.0.0.0 | 管理服务和三面板端口映射全部使用 `BIND_ADDRESS=0.0.0.0` | 配置/HTTP 测试通过 | Docker 真机未验证 |
| Docker 自检/修复 | 启动检查 daemon；修复停止/缺失/unhealthy 容器 | 单元测试通过 | 未验证 |
| screen 教程 | README 第 11 节 + 排错指南 | 文档检查通过 | 未在正常 `/run/screen` 主机验证 |

## 4. 未执行的真实端到端项目

以下必须在一台有 Docker、能访问 QQ 和 DeepSeek 的真实 Ubuntu 服务器上补测：

1. `./install.sh` 从零安装 Docker/Compose/screen/Python；
2. `./start.sh` 创建实例，浏览器打开 `http://服务器IP:8765/wizard?instance=...`；
3. 在配置页测速、切换自定义镜像节点，观察逐镜像下载日志；
4. 部署完成后分别访问 AstrBot WebUI、SnowLuma WebUI、noVNC；
5. noVNC 内扫码登录 QQ，完成可能出现的设备/短信验证；
6. 私聊机器人验证必回；
7. 群里 @ 机器人验证必回；
8. 群里非 @ 发多条消息验证概率回复、冷却和不重复；
9. 发送图片验证识别回复；
10. 验证本地表情包实际发送；
11. 部署中途关闭浏览器，再重新进入验证自动切回部署页；
12. 部署中途重启管理服务，验证中断提示和重试；
13. 停止一个容器后运行 `./start.sh`，验证 Docker 修复；
14. 用 `./console.sh` 创建第二个机器人，验证端口、容器、数据完全隔离；
15. 验证默认 `0.0.0.0` 下从自己电脑直连管理控制台和三个面板。

## 5. 已知限制

- Windows 初始移植仅完成 PowerShell 静态检查、跨平台 Python 兼容和 Linux 回归；
  尚无 Windows 11 + Docker Desktop/WSL2 + PowerShell 的真实 E2E/Pester 结果。
- 本环境无 Docker daemon，容器相关行为只能单元测试和静态检查。
- 无 QQ/noVNC 图形环境，扫码与验证流程未执行。
- 无真实 DeepSeek Key，模型多模态能力依据官方模型列表文档与 mock。
- AstrBot OpenAI-compatible provider 在图片请求失败时可能内部降级为纯文本重试，
  插件无法完全确认模型是否实际看到了图片。
- QQ 增强插件直接调用 provider，不进入 AstrBot 默认 Agent 的完整人格/会话历史/工具链。
  部署时会将其 `system_prompt` 同步为用户填写的提示词，但会话历史能力和默认 Agent 不同。
- 本地表情文件跨协议端发送依赖 SnowLuma/AstrBot 文件服务，需真实环境验证。
- 镜像节点测速只代表网络端点可达，不代表该镜像一定包含所需 tag。

## 6. 发布建议

当前可发布为 **v2.1.1 条件通过**，但 README 和发布说明必须明确：

- 默认 `0.0.0.0` 的安全风险；
- 真实 Docker/QQ/DeepSeek E2E 尚未完成；
- 用户应在自己的 Ubuntu 服务器按第 4 节清单完成最终验收。
