# QQ Bot Studio 真实容器 E2E 验证记录

> 验证日期：2026-10-31（UTC 2026-10-30 至 2026-10-31）  
> 范围：Docker / Docker Compose / AstrBot / SnowLuma / OneBot v11 / 管理 API / 配置产物。  
> QQ 扫码、设备验证、短信验证、滑块等只能人工完成；本次**没有**自动绕过、代填或伪造登录成功。

## 1. 结论

| 项目 | 结果 |
|---|---|
| 自动 E2E 总状态 | `PASS_WITH_ENV_BLOCKED_STEPS` |
| Docker 容器级运行 | **通过** |
| AstrBot 容器 | **running + healthy** |
| SnowLuma 容器 | **running** |
| 部署 job | **success** |
| AstrBot / SnowLuma / noVNC 宿主端口 | **HTTP 200** |
| AstrBot 登录 + Persona API | **通过（HTTP 200）** |
| QQ 增强插件 schema / 资源统计 / 实例 / state API | **通过（HTTP 200）** |
| OneBot WebSocket `ws://127.0.0.1:6199/ws` | **容器内握手成功** |
| 生成配置 JSON、端口分配、`BIND_ADDRESS=0.0.0.0` | **通过** |
| QQ 扫码与设备验证 | `ENV_BLOCKED:QQ_SCAN_REQUIRED`（人工步骤，未执行） |
| 敏感信息检查 | 本轮 artifact 中未发现测试 DeepSeek Key、JWT/token/密码泄漏；敏感值已脱敏 |

唯一未能自动完成的是 **QQ 官方扫码/验证**。这不是产品代码失败，也不应被报告为“QQ 登录通过”。

## 2. 交付的 E2E 脚手架

| 文件 | 作用 |
|---|---|
| `tests/e2e/e2e_smoke.py` | 真实容器 E2E 主脚本；不在普通 `unittest discover` 中运行 |
| `tests/e2e/run_studio_server.py` | E2E 专用管理服务入口；仅 stub 外部 DeepSeek 付费预检，另有仅调试用的 `docker exec` API 桥 |
| `scripts/e2e-smoke.sh` | Linux 便捷入口，转调 `tests/e2e/e2e_smoke.py` |
| `tests/e2e/.gitignore` | 忽略生成的 artifacts、`__pycache__` 和临时 driver log |

普通部署/业务代码未为 E2E 改动。`run_studio_server.py` 只用于测试：
- 永远不调用真实 DeepSeek API、不要求用户提供真实 Key；
- 使用固定非真实 Key `sk-e2e-invalid-not-a-real-key`，仅写入临时容器配置；
- `QQBOT_E2E_EXEC_ASTRBOT=1` 仅在 Docker Desktop 宿主端口不可达时，通过 `docker exec` 转发 AstrBot API；默认不启用，不改变产品行为。

## 3. 运行命令

### 3.1 Ubuntu/Linux 正常路径

```bash
cd '/path/to/QQ Ai bot'

# 先确认 Docker + Compose v2 可用
docker info --format '{{.ServerVersion}}'
docker compose version

# 运行真实容器 E2E；默认不在普通单测中执行
python3 tests/e2e/e2e_smoke.py --timeout 900

# 或使用便捷脚本
./scripts/e2e-smoke.sh --timeout 900
```

脚本会自动：
1. 创建临时数据目录和随机管理端口；
2. 用一个随机高序号实例 ID 避免与用户已有 `bot-1`、Compose 项目、容器名或网络重名；
3. 启动临时管理服务，提取本地 `X-QB-Token`；
4. 通过管理 API 创建临时实例并发起部署；
5. 拉取/启动 AstrBot 与 SnowLuma 容器；
6. 校验配置、端口、容器、HTTP、AstrBot API、插件 schema、统计、state、OneBot WS；
7. 最后只清理**本轮随机实例**的 Compose 项目、容器、网络和临时数据目录；不使用全局通配删除。

退出码约定：

| 退出码 | 含义 |
|---|---|
| `0` | 自动检查通过；结果可能带 `ENV_BLOCKED:QQ_SCAN_REQUIRED` 人工步骤 |
| `1` | 产品/运行时/断言失败，或未预期异常 |
| `2` | 环境阻塞：Docker CLI/daemon/Compose 不可用、镜像拉取网络失败等 |

### 3.2 仅当 Docker Desktop 宿主端口发布不可用时

例如 WSL Integration 关闭、但 Windows Docker Desktop Linux engine 仍可由 `docker.exe` 使用：

```powershell
python.exe -X utf8 tests\e2e\e2e_smoke.py --timeout 900 --exec-astrbot-bridge
```

这个参数只是测试专用 API 桥，用于让部署流程能通过容器内回环完成 AstrBot readiness/Persona；脚本仍会优先验证宿主端口。若宿主端口可用，AstrBot API 断言仍走正常宿主 HTTP。

### 3.3 保留临时数据（危险）

```bash
python3 tests/e2e/e2e_smoke.py --timeout 900 --keep
```

`--keep` 会保留含随机凭据和假 DeepSeek Key 的临时目录，仅供本地调试；不要提交、上传或放到共享目录。

## 4. 实际执行结果

### 4.1 成功验收轮

| 项 | 值 |
|---|---|
| 结果 JSON | `tests/e2e/artifacts/e2e-20261031-080600-d8695d/result.json` |
| 状态 | `PASS_WITH_ENV_BLOCKED_STEPS` |
| Docker daemon | `29.8.1`（Linux containers；Docker Desktop） |
| Compose | v2 可用 |
| 临时实例 | `bot-876635` |
| Compose 项目 | `qqbot-studio-bot-876635` |
| 镜像 | DaoCloud 镜像源的 AstrBot / SnowLuma 官方镜像 |
| AstrBot | v4.29.3，容器 `running`，health `healthy` |
| SnowLuma | 2.2.1，容器 `running` |
| 随机宿主端口 | AstrBot `5006`、SnowLuma WebUI `5007`、noVNC `5008` |
| `BIND_ADDRESS` | `0.0.0.0` |
| 部署 job | `success`，100% |
| 清理 | 临时数据目录已删除；上一轮 Compose 项目/容器/网络已清理 |

实际通过项：

```text
PASS docker_preflight：Docker 29.8.1 + Compose v2
PASS management_service_health：/healthz=200, /console=200
PASS privacy_state_no_api_key
PASS management_api_state
PASS deployment_job
PASS state_after_deploy
PASS generated_json：cmd_config.json / onebot.json 可解析且关键字段匹配
PASS three_port_allocation：bind=0.0.0.0 ports=[5006,5007,5008]
PASS containers_running：[AstrBot running+healthy, SnowLuma running]
PASS http_astrbot_webui：HTTP 200
PASS http_snowluma_webui：HTTP 200
PASS http_novnc：HTTP 200
PASS astrbot_login_and_persona_api：宿主端口登录 + /api/v1/personas 200
PASS plugin_schema_api：qqbot_enhancer/reply_probability 可读
PASS resource_stats_api：HTTP 200
PASS instances_api：HTTP 200
PASS state_query_api：running=True
PASS onebot_websocket：容器内 ws://127.0.0.1:6199/ws 握手成功
ENV_BLOCKED QQ 扫码/设备验证需要用户在 noVNC 手动完成
```

关键配置断言：
- AstrBot `cmd_config.json` 与 SnowLuma `config/onebot.json` 均可被 BOM 容错的 JSON 解析器读取；
- AstrBot `provider` 中的 DeepSeek provider `id=deepseek-flash`、`model=deepseek-flash`；
- AstrBot 首次启动会把 chat-completion 连接字段迁移到 `provider_sources`；脚本兼容迁移前后两种结构并验证 `api_base=https://api.deepseek.com/v1`；
- SnowLuma `networks.wsClients[0].url == ws://astrbot:6199/ws`；
- AstrBot `ws_reverse_token` 与 SnowLuma `wsClients[0].accessToken` 一致；
- OneBot 端口 `6199`、三个宿主端口互不相同且在 `5000-10000`；
- 测试 DeepSeek Key 只应出现在临时 AstrBot 配置中；未出现在 `/api/state`、SnowLuma 配置或写入的脱敏日志中。

### 4.2 补充纯宿主端口运行

另执行了一轮**不带** `--exec-astrbot-bridge` 的运行。以下自动检查在控制台均已通过：

- 部署 job `success`；
- 三端口与 `BIND_ADDRESS=0.0.0.0`；
- AstrBot running+healthy、SnowLuma running；
- 三个宿主 HTTP 端口均 200；
- AstrBot 登录 + Persona API 200；
- 插件 schema、统计、实例、state 200；
- OneBot WS 容器内握手成功；
- QQ 扫码仍为 `ENV_BLOCKED`。

该轮在**清理阶段**遇到 Docker Desktop engine 崩溃（`Docker Desktop is unable to start`），因此 `result.json` 未能落盘。相关 stdout 和已写出的脱敏 server/job 日志保留在成功验收目录的 `pure-run-*` 文件中；不将其冒充为完整 JSON 验收。

### 4.3 ENV_BLOCKED 分类自检（无 Docker）

为确认“无 Docker”不会被误报为产品失败，使用受限 `PATH` 模拟缺少 Docker CLI：

```bash
env PATH=/usr/bin:/bin python3 tests/e2e/e2e_smoke.py --artifacts-dir /tmp/qqbot-e2e-nodocker
```

实际结果：
- 输出 `ENV_BLOCKED:DOCKER_CLI: 找不到 docker 命令`；
- 仍写出 `result.json`；
- 进程退出码为 `2`。

这说明环境阻塞路径不会启动管理服务、不会创建容器，也不会伪造通过。

### 4.4 日志与证据路径

目录：`tests/e2e/artifacts/e2e-20261031-080600-d8695d/`

| 文件 | 说明 |
|---|---|
| `result.json` | 成功轮脱敏结果汇总 |
| `studio-server.log` | 成功轮临时管理服务日志 |
| `deployment-job.redacted.json` | 成功轮 job 状态和脱敏日志 |
| `manual-qq-checklist.txt` | noVNC 人工扫码/验证检查清单（无密码） |
| `pure-run-driver.log` | 补充纯宿主端口运行的 stdout |
| `pure-run-studio-server.log` | 补充运行的管理服务日志 |
| `pure-run-deployment-job.redacted.json` | 补充运行的脱敏 job 日志 |
| `pure-run-note.txt` | 说明补充运行为何没有 `result.json` |
| `post-run-environment-note.txt` | 测试后 Docker Desktop 引擎失效、设置恢复和待核对清理项 |

**没有截图**：QQ 登录/设备验证没有人工执行，不能伪造二维码、登录成功页或验证成功页。需要人工验证时，应在本次 noVNC 临时链接仍存活时由本人在 QQ 官方界面完成并自行截图。

## 5. 人工 QQ 步骤（唯一 ENV_BLOCKED）

自动化完成后，`result.json` 会记录：

```json
{
  "code": "ENV_BLOCKED:QQ_SCAN_REQUIRED",
  "detail": "真实 QQ 扫码/设备验证必须由用户在 noVNC 官方界面完成；本轮未执行。"
}
```

人工步骤：

1. 打开管理完成页或本轮 artifact 中记录的 noVNC 地址；
2. 输入完成页/管理服务显示的 VNC 或 SnowLuma 凭据（这些值不得写入日志/工单）；
3. 在 noVNC 中的 QQ 官方登录窗口用手机 QQ 扫码；
4. 如设备/短信/滑块验证出现，按腾讯官方提示手动完成；
5. 回到控制台检查 QQ 登录状态和 OneBot 连接状态。

本工具不收集 QQ 密码、不代填验证码、不绕过设备验证或风控。

## 6. 清理步骤

正常成功/失败路径由脚本自动执行：

```text
docker compose -p qqbot-studio-bot-<随机ID> \
  -f <临时实例>/compose.yml \
  down --remove-orphans --timeout 15

# 然后删除 tempfile.mkdtemp 创建的临时数据根目录
```

脚本的清理只针对它自己创建的随机 Compose 项目，不会按 `qqbot-*` 通配符删除用户实例。

若测试进程被强杀、Docker Desktop 崩溃或网络中断，先人工查看：

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Labels}}' \
  | grep 'qqbot-studio-bot-<随机ID>'

docker network ls --format '{{.Name}}' \
  | grep 'qqbot-studio-bot-<随机ID>'
```

确认容器/网络名包含该轮随机 ID 后，再精确删除：

```bash
docker rm -f qqbot-bot-<随机ID>-snowluma qqbot-bot-<随机ID>-astrbot
docker network rm qqbot-studio-bot-<随机ID>-net
rm -rf /tmp/qqbot-studio-e2e-<随机后缀>
```

不要删除任何不属于该随机 ID 的容器、网络、volume 或用户数据目录。

## 7. 环境限制与未验证项

1. **QQ 扫码/设备验证**：人工步骤，未执行，状态 `ENV_BLOCKED`。
2. **管理服务宿主**：本次容器引擎是 Docker Desktop Linux engine；为了绕过当前 WSL Docker CLI Integration 未启用的问题，管理服务进程使用 Windows Python 调用 `docker.exe`。因此“Ubuntu 宿主 + 原生 `/usr/bin/docker` + 同一脚本”的路径未在本轮环境完成，脚本按 Linux `subprocess` 接口编写，但最终需在纯 Ubuntu 主机复跑。
3. **Docker Desktop engine 后续崩溃**：成功轮完成后，补充纯宿主端口运行在清理阶段遇到 `Docker Desktop is unable to start`；随后 `docker info` 也无法连接 Linux engine。已停止 Docker Desktop 进程，并移除为排查 WSL Integration 临时写入 `settings-store.json` 的键。由于 engine 不可用，未能最后一次核对补充运行是否残留临时容器/网络；成功轮自身已执行清理且当时查询无残留。下一次 Docker Desktop 恢复后，请按第 6 节用随机 ID `bot-843858` / `qqbot-studio-bot-843858` 检查并精确清理。因此本轮最终状态以已落盘的 `result.json` 为准；建议下一次在有稳定 Docker engine 的纯 Ubuntu 环境重跑。
4. **AstrBot healthcheck**：本轮 healthy；脚本仍以宿主 HTTP/API 200 作为主判据，避免把容器 healthcheck 工具缺失误判为产品失败。
5. **OneBot 事件/消息收发**：本轮验证了 WebSocket 握手与 token 鉴权路径，未登录 QQ，因此没有真实 QQ 消息收发、群聊/私聊事件或图片/音频端到端测试。
6. **DeepSeek 真实推理**：按任务要求未使用真实 Key、未产生付费调用；仅验证 AstrBot provider/Persona/API 配置路径。实际对话/Rate Limit/模型权限仍需用户用自己的 Key 验收。
7. **SnowLuma WebUI 密码与 QQ 登录态**：已启动容器并验证 WebUI HTTP 200；真实 UI 登录和扫码后状态未人工验收。
8. **镜像拉取**：本轮镜像已存在于本机镜像缓存，`docker pull` 显示 up-to-date；未验证全新机器上 GitHub/Docker Hub/DaoCloud 的完整下载速度与失败恢复。
9. **资源占用**：AstrBot + SnowLuma + noVNC 容器较重；本轮未做长期稳定性、内存泄漏或重启恢复测试。

## 8. 本次发现的非产品问题

本轮发现并修正的问题都在 **E2E 测试脚本**，未修改业务行为：

- Docker CLI 的 `version --format` 在不同客户端版本上不可靠，改用 `docker info --format '{{.ServerVersion}}'` 检查 daemon。
- AstrBot 会在启动后以 UTF-8 BOM 重写 `cmd_config.json`；测试脚本用 `utf-8-sig` 读取，避免把正常文件误判为损坏。
- AstrBot v4.29.3 首次启动会把 DeepSeek chat provider 的 `key/api_base/type` 迁移到 `provider_sources`；测试断言需同时兼容迁移前/后结构。
- Docker Desktop 端口发布在环境刚启动时曾短暂不可用；后续运行恢复正常。成功结果中的三个宿主端口 HTTP 200 是实际验证，不依赖内部桥接。
- 清理逻辑增加了“部署中途失败也能读取 instance.json 并 down 掉本轮项目”的兜底，避免强杀后留下临时容器。

截至交付，未确认需要修改 AstrBot/SnowLuma/QQ Bot Studio 业务代码的真实 bug。

## 9. 普通单测回归

在本轮 E2E 之外运行了常规单测：

```bash
python3 -m unittest discover -s tests -v
```

结果：`Ran 403 tests in 27.705s`，`OK`。E2E 脚本不纳入普通 `unittest discover`，不会在无 Docker 的 CI 中误触发容器。
