# QQ Bot Studio v3.9.0 QA 报告（含 v3 控制台功能）

日期：2026-10-29
结论：**条件通过（Conditional Pass）**。自动化测试、静态检查、本地 HTTP 冒烟和管理 API 冒烟通过；真实 Docker 容器、AstrBot/SnowLuma、QQ 扫码/noVNC、DeepSeek 实际对话和 Windows Docker Desktop E2E 仍未在本环境执行。

先读文档：[主 README](../README.md) · [文档索引](README.md) · [服务器排错](server-troubleshooting.md)。

## 1. 测试环境

- Ubuntu 24.04.3 LTS（WSL2），普通用户 uid=1000
- Python 3.12.3
- Node.js v24.21.0
- 无可用 Docker daemon；Docker CLI/WSL Integration 不可用
- 无 GUI/noVNC 环境，无法扫码登录 QQ
- 无真实 DeepSeek API Key，未调用真实计费接口
- 目录为 Git 工作区，但测试在未提交的开发快照上执行（本报告不把工作区状态当作发行 commit）

## 2. 通过项

### 2.1 全量自动化测试

```bash
python3 -m unittest discover -s tests -v
```

结果：**236 tests OK，Ran 236 tests in 18.453s**。

覆盖重点：

- 设置/密钥存储和权限、旧版 v1 根目录迁移
- 多实例创建、实例隔离、实例删除
- 每实例 `5000–10000` 内互不重叠的三端口块分配
- Compose 生成、项目名/容器名/网络名隔离、Windows 兼容分支
- 首次运行 IP/端口/public_host 持久化
- 镜像源候选顺序、测速、添加、选择、删除
- 部署任务、逐镜像拉取日志回退、任务持久化/中断恢复、active job
- Docker 自检/容器修复、Docker CLI/daemon 权限降级
- 服务器 API、token 鉴权、Host 校验、多实例 API、镜像 API
- AstrBot/SnowLuma 配置字段、SnowLuma WebUI 密码 bootstrap/重置
- QQ 增强插件：@必回、概率/冷却、图片理解、双源找图、自然语言找图、8 张本地表情包、本地点歌、`/option`
- `memory_mode=direct/astrbot`、群统一会话/私聊隔离、`/reset`/`/new` 契约
- 控制台插件列表/启停、记忆模式 API、存储/内存/Docker 统计降级
- Git 自动更新检查、干净工作区/fast-forward 保护、更新后重启脚本
- 可选 AI 助手 B 的状态/安装/更新隔离和密钥不回显
- Windows 独立移植的静态/跨平台测试（不是真实 Windows E2E）

### 2.2 静态检查

```bash
node --check qqbot_studio/static/app.js
node --check qqbot_studio/static/console.js
bash -n start.sh console.sh install.sh scripts/*.sh
python3 -m py_compile qqbot_studio/*.py plugins/qqbot_enhancer/*.py
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
python3 -m qqbot_studio --version
```

结果：全部通过；版本为 `qqbot-studio 3.9.0`。

静态 UI 检查：

- `index.html`：129 个唯一 id；`console.html`：86 个唯一 id
- 两页均保留精确占位符 `window.QB_TOKEN = "{{TOKEN}}";`
- JS 引用的普通 DOM id 均在对应页面存在
- 无外部 CDN/字体/图片依赖
- 无 `innerHTML` 动态注入
- 无 `localStorage` / `sessionStorage` 持久化
- 无 `alert()` / `confirm()` 弹窗；操作使用页面内联提示

### 2.3 本地 HTTP 冒烟

使用临时 `QQBOT_STUDIO_DATA_DIR` 启动真实管理服务，未启动 Docker、未执行真实部署：

- `/healthz` 200，返回 `ok:true` 和版本 `3.9.0`
- `/` 302 到 `/console`；控制台/向导静态资源 200
- 两页 token 注入成功；`/api/settings` 无 token 403、有 token 200
- `POST /api/instances` 创建实例成功；`GET /api/instances` 返回该实例
- `GET /api/state?instance=bot-1`、`GET /api/stats?instance=bot-1`、`GET /api/memory?instance=bot-1` 均 200
- `GET /api/plugins?instance=bot-1` 在 AstrBot 未部署时优雅降级为本地插件空列表，不阻塞控制台
- `GET /api/assistant/status` 200，返回 `installed:false`；主控制台不受影响
- `GET /api/mirrors` 200；镜像节点列表正常返回
- `/api/logs` 在 Docker 不可用时返回 503 JSON，并提示 Docker CLI/daemon 问题
- `OPTIONS /api/state` 返回 405 JSON、`Allow: GET, HEAD, POST`，无 CORS 头
- 控制台/实例 token 鉴权继续生效；未发现 API Key 或 QQ 密码进入响应
- 本轮没有调用 `/api/mirrors/test`、真实部署或真实 DeepSeek 接口

### 2.4 文档结构与链接

本轮同时重构了主文档：

- `README.md` 已按“目录 → 最简单安装 → 进阶 → 教程/FAQ → 项目关系/声明/致谢”四部分组织；
- 新建 `docs/README.md` 文档索引，保留 v2 API/插件/排错/QA 文档，并标注旧版 v1 文档仅作历史参考；
- `docs/server-troubleshooting.md` 保留 Git SSL、Docker 安装/daemon、0.0.0.0/127、腾讯云安全组、ufw/firewalld、SSH 隧道、SnowLuma 密码、端口冲突、控制台状态、Windows 等真实故障案例；
- 相对链接/A 标签检查：**113 个 Markdown 链接、11 个唯一外部 URL、0 个相对链接或页内锚点错误**。

旧版接口文档 `docs/ui-api-contract.md` 和旧 QA `docs/qa-report.md` 已标记为历史资料；新配置请使用 `docs/ui-api-contract-v2.md` 与主 README。

### 2.5 功能需求矩阵

| 需求 | 实现/测试 | 真实 E2E |
|---|---|---|
| 多实例隔离 | 每实例独立目录/项目/容器/网络/端口 | 容器未启动 |
| 三服务相邻端口块 | 分配器限制 `5000–10000`，每实例 3 个连续端口，单元测试通过 | 未用真实 Docker bind |
| 删除实例 | 删除容器并清理实例目录，单元测试通过 | 未在真实容器执行 |
| @ 必回/私聊必回 | 插件 mock 测试通过 | QQ 未验证 |
| 非 @ 概率回复 | 概率 + 群/用户冷却 + 过滤，mock 通过 | QQ 未验证 |
| 图片理解 | 原生视觉/转述降级路径、mock 测试 | 真实多模态 provider 未测 |
| `/找图`/自然语言找图 | 双搜索源、安全过滤、下载/发送/mock 通过 | 真实 QQ 协议端未测 |
| 本地表情包/唱歌 | 8 张表情包、歌曲目录/冷却/组件路径、mock 通过 | QQ 音频/图片未测 |
| `memory_mode=direct/astrbot` | 配置、群统一会话/私聊隔离 API、`/reset` `/new` 文档与测试 | AstrBot 真实会话未测 |
| 插件选择器 | AstrBot v4.29 API + 本地降级，控制台 UI/接口测试 | 真实插件热更新未测 |
| 存储/内存统计 | 磁盘、宿主机内存、Docker stats 降级，单元测试通过 | Docker stats 真机未测 |
| 整项目更新 | 干净工作区/fast-forward 检查、后台脚本、重启保护，单元测试通过 | GitHub 真实更新未执行 |
| 镜像节点测速/回退 | 节点 API、测速和候选顺序测试通过 | 镜像 tag/拉取未测 |
| Docker 修复 | 停止/缺失/unhealthy 容器修复测试通过 | 真实容器未测 |
| B 可选 AI 助手 | 固定 repo clone/快进更新/不读取 Key 的隔离逻辑测试通过 | 真实 clone、安装、启动和 DeepSeek 未测 |
| Windows 独立项目 | 独立仓库、EXE workflow、静态/跨平台测试 | 真实 Windows/Docker Desktop 未测 |

## 3. 未执行的真实端到端项目

必须在一台有 Docker、能访问 QQ/GitHub/DeepSeek 的真实 Ubuntu 服务器补测：

1. 从零执行 `./install.sh`，确认 Docker/Compose/screen/git/curl 安装与首次 IP/端口配置；
2. `./start.sh` 创建实例，打开 `/wizard?instance=bot-1`，切换镜像节点并观察逐镜像下载日志；
3. 真实拉取 AstrBot/SnowLuma 镜像并 `docker compose up -d`；
4. AstrBot WebUI、SnowLuma WebUI、noVNC 三个实际端口可访问；
5. noVNC 中扫码登录 QQ，并实测设备/短信/滑块验证必须在官方界面完成；
6. 私聊和群聊 @ 验证必回；非 @ 多消息验证概率、冷却、过滤和默认 LLM 阻断；
7. 发送图片验证视觉/转述路径；执行 `/找图` 和自然语言找图验证真实图片发送；
8. 放入授权音频验证 `/唱歌`、`/点歌`，确认协议端播放；
9. 验证 `memory_mode` 切换、群统一记忆、私聊隔离、`/reset`、`/new` 和重启后持久化；
10. 控制台插件启停、记忆切换、存储/内存/Docker 统计、实例删除；
11. 关闭浏览器后续看进度、重启管理服务后中断提示和重试；
12. 停止/删除一个容器后执行 `./console.sh` 验证 Docker 修复；
13. 控制台检查更新并更新整项目，验证插件同步、AstrBot/管理服务重启和 `auto-update.log`；
14. 默认 `0.0.0.0` 下从外网按实际三端口块访问，并验证云安全组/本机防火墙边界；
15. 从控制台安装可选 AI 助手 B，阅读其 README、手动运行 B 的安装器，并独立验证 B 的 DeepSeek Key 边界。

## 4. 已知限制与风险

- Windows 是独立项目：真实 Windows 10/11 + Docker Desktop/WSL2 + EXE 尚未完成 E2E，不能把 Linux 测试结果当作 Windows 生产可用承诺。
- 本环境无 Docker daemon，Compose 真实端口映射、bind mount 权限、容器重启/健康检查未验证。
- 无 QQ/noVNC 图形环境，二维码、设备验证、短信验证和登录态复用未验证。
- 无真实 DeepSeek Key；图片理解、概率回复、自然语言找图、唱歌和插件真实响应均未在 QQ 中验证。
- `/找图` 依赖 Bing/百度页面/接口结构，可能因改版、反爬、地区差异失败；安全搜索不等于绝对内容安全。
- 本地表情/音频跨协议端发送依赖 AstrBot/SnowLuma 实现；部分适配器可能只可靠支持部分图片/音频格式。
- 镜像节点测速只代表 Registry 端点可达，不代表节点包含指定 tag 或拉取速度快。
- 默认 `0.0.0.0` 会扩大攻击面；必须使用云安全组、防火墙、VPN 或 SSH 隧道限制来源 IP。本项目不是多租户隔离方案。
- 访问令牌防止跨站误用，不构成多用户操作系统的访问控制；共享主机/共享浏览器不在安全承诺内。
- AI 助手 B 有自己的数据目录、配置、Key 和生命周期；主项目不会读取/显示其 Key，也不会自动启动它。
- `docs/qa-report.md` 是旧版单实例 QA 快照，不应作为 v2 当前实现说明。

## 5. 发布建议

当前快照可作为 **v3.9.0 条件通过** 交付文档和候选版本；正式生产发布前必须按第 3 节在真实 Ubuntu + Docker + QQ + DeepSeek 环境完成准入验证，并优先验证实际三端口、扫码、插件消息、记忆/统计、自动更新和默认 `0.0.0.0` 的防火墙边界。

建议发布说明明确：

- Ubuntu 主项目与 Windows 独立项目、AI 助手 B 的边界；
- 默认 `0.0.0.0` 的安全风险；
- 真实 Docker/QQ/DeepSeek E2E 尚未由本环境完成；
- 真实故障排查入口：[`server-troubleshooting.md`](server-troubleshooting.md)；
- Windows EXE 目前从独立仓库的 GitHub Actions 构建 artifact 获取，不冒充 Ubuntu 主项目发布物。
