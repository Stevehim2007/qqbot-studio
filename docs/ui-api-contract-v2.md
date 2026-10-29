# QQ Bot Studio v2 前端 / 本地服务 API 契约

适用文件：

- `qqbot_studio/static/index.html`、`styles.css`、`app.js`：实例部署向导
- `qqbot_studio/static/console.html`、`console.css`、`console.js`：多实例控制台
- 服务端由 Lead 提供；本文档只记录前端实际调用与容错假设。

核心约束：

- 纯静态、无构建步骤、无第三方 CDN、无外部字体/图片。
- 所有动态内容通过 DOM API / `textContent` 写入，不使用动态 `innerHTML`。
- 不保存 API Key、访问令牌或 QQ 密码到 localStorage / sessionStorage。
- 服务端必须将两个 HTML 中的占位符替换为本次本地会话令牌。

## 1. 页面路由与令牌注入

### 路由

- `/wizard?instance=<id>`：服务端返回 `index.html`。前端从 URL 读取 `instance`。
- `/console.html`：多实例控制台静态页（如果服务端另外支持 `/console`，应返回同一页面）。
- `/wizard?instance=`、`/console.html` 之外的文件继续按静态资源提供：`styles.css`、`app.js`、`console.css`、`console.js`。

如果向导 URL 缺少 `instance`，前端仍会展示页面并提示从控制台打开；请求可能退化为不带 `instance` 的旧式默认实例，服务端应拒绝或使用明确默认实例，不应静默串用其他实例。

### 令牌占位符

`index.html` 和 `console.html` 末尾均包含：

```html
<script>window.QB_TOKEN = "{{TOKEN}}";</script>
```

服务端需要做 JavaScript 字符串安全转义，例如用 `json.dumps(token)` 的结果替换 `"{{TOKEN}}"`。

前端行为：

- 所有修改请求（POST / DELETE）都发送 `X-QB-Token`。
- 因本机服务 GET API 也校验令牌，前端 GET 请求同样会附加 `X-QB-Token`；服务端可继续统一校验。
- 所有请求的令牌都只保存在当前页面内存中。

## 2. 向导实例参数规则

向导 `app.js` 的 `apiRequest()` 集中处理实例参数。当页面 URL 为：

```text
/wizard?instance=inst_abc123
```

则所有向导 API 均自动变成：

```text
/api/state?instance=inst_abc123
/api/test-key?instance=inst_abc123
/api/deploy?instance=inst_abc123
/api/job/<job_id>?instance=inst_abc123
/api/action?instance=inst_abc123
/api/persona?instance=inst_abc123
/api/logs?service=astrbot&lines=200&instance=inst_abc123
/api/mirrors?instance=inst_abc123
/api/mirrors/test?instance=inst_abc123
/api/mirrors/select?instance=inst_abc123
/api/mirrors/<node_id>?instance=inst_abc123
```

前端不会自行决定端口或拼接 WebUI 地址；地址全部来自 `/api/state` / `/api/instances` 的 `urls`。

## 3. 向导接口

### GET `/api/state?instance=<id>`

返回原有 state，并增加部署任务恢复字段：

```json
{
  "app_version": "2.0.0",
  "requirements": {
    "docker_installed": true,
    "docker_running": true,
    "compose_installed": true,
    "permission_ok": true,
    "os": "Ubuntu 24.04"
  },
  "deployment": {
    "deployed": true,
    "running": true,
    "qq_account": "123456789",
    "model": "deepseek-flash",
    "prompt": "机器人提示词",
    "active_job_id": "job_abc",
    "active_job_status": "success",
    "urls": {
      "astrbot_webui": "http://127.0.0.1:6185",
      "snowluma_webui": "http://127.0.0.1:5099",
      "novnc": "http://127.0.0.1:6081"
    },
    "credentials": {
      "astrbot_username": "admin",
      "astrbot_password": "本地随机密码",
      "snowluma_username": "admin",
      "snowluma_password": "本地预置初始密码",
      "vnc_password": "本地 VNC 密码"
    },
    "onebot_connected": true,
    "qq_logged_in": true
  },
  "notice": "可选提示"
}
```

`deployment.active_job_id` 可为空字符串或缺失，`deployment.active_job_status` 可为 `null` 或缺失。

前端重进页面时的恢复逻辑：

1. `deployed === true` 或 `active_job_status` 为成功状态：进入完成页。
2. `active_job_id` 存在且 `active_job_status` 为 `running / pending / queued / null`：进入部署页，显示恢复提示并轮询 `/api/job/<id>`。
3. `active_job_id` 存在但状态为 `interrupted / cancelled / error / failed / lost / paused`：进入部署页，显示“部署任务已中断”提示，同时提供：
   - “继续获取进度”：重新轮询原 job；
   - “重试部署”：返回配置页重新提交。
4. 没有活动任务且未部署：进入环境检查页。

当 job 接口返回 404（例如服务重启导致任务记录丢失），前端立即显示中断提示，不会无限重试卡住。

### POST `/api/test-key?instance=<id>`

```json
{ "deepseek_api_key": "sk-..." }
```

```json
{ "ok": true, "message": "Key 可用" }
```

### POST `/api/deploy?instance=<id>`

v2 请求体不包含 `use_mirror`；镜像节点由服务端按已选节点决定：

```json
{
  "qq_account": "123456789",
  "deepseek_api_key": "sk-...",
  "prompt": "提示词文本",
  "accept_risk": true
}
```

```json
{ "ok": true, "job_id": "job_abc" }
```

或：

```json
{ "ok": false, "message": "失败原因" }
```

### GET `/api/job/<id>?instance=<id>`

```json
{
  "status": "running",
  "step": "拉取镜像",
  "progress": 42,
  "message": "当前步骤说明",
  "logs": ["日志行 1", "日志行 2"],
  "error": ""
}
```

状态兼容：

- 成功：`success / succeeded / complete / completed / done / ok`
- 失败/中断：`error / failed / interrupted / cancelled / canceled / aborted / paused / lost` 等常见同义状态
- 进行中：`running / pending / queued / starting / in_progress / processing`
- `progress` 按 0–100 解释
- `logs` 兼容字符串与字符串数组

前端大约每 1.2 秒轮询一次。连续三次网络失败时，会同时显示“继续获取进度”和“重试部署”，不会永久卡在等待进度状态。

### POST `/api/action?instance=<id>`

```json
{ "action": "start" }
```

`action` 仅取 `start / stop / restart`。响应 `{ "ok": true, "message": "..." }` 或 `{ "ok": false, "message": "..." }`。

### POST `/api/persona?instance=<id>`

```json
{ "prompt": "新提示词" }
```

响应 `{ "ok": true, "message": "..." }`。成功后前端重新读取该实例 state。

### GET `/api/logs?service=astrbot&lines=200&instance=<id>`

```json
{ "ok": true, "logs": "日志文本" }
```

`logs` 兼容字符串或数组。`service` 仅取 `astrbot / snowluma`。

## 4. 镜像节点接口与 UI

配置页的“Docker 镜像节点”卡片会自动执行：

1. `GET /api/mirrors?instance=<id>`
2. `POST /api/mirrors/test?instance=<id>`，请求体为 `{}`
3. 渲染节点名称、前缀、状态、延迟和选中状态。

### GET `/api/mirrors?instance=<id>`

```json
{
  "ok": true,
  "nodes": [
    {
      "id": "m1",
      "name": "DaoCloud",
      "prefix": "docker.m.daocloud.io",
      "custom": false,
      "selected": true,
      "latency_ms": 92,
      "status": "ok"
    }
  ],
  "selected_id": "m1"
}
```

### POST `/api/mirrors/test?instance=<id>`

请求：

```json
{ "ids": ["m1", "m2"] }
```

前端自动测速时发送空对象 `{}`，服务端可测全部节点。响应：

```json
{
  "ok": true,
  "nodes": [
    {
      "id": "m1",
      "name": "DaoCloud",
      "prefix": "docker.m.daocloud.io",
      "custom": false,
      "selected": true,
      "latency_ms": 88,
      "status": "ok"
    }
  ],
  "results": [
    { "id": "m1", "latency_ms": 88, "status": "ok", "error": "" }
  ],
  "selected_id": "m1"
}
```

前端也兼容只返回 `results` 的响应：此时保留已有节点并合并测速结果。

节点状态展示兼容：

- 可用：`ok / online / available / healthy / success / reachable / connected`
- 不可用：`error / failed / unreachable / unavailable / timeout / offline`
- 需留意：`warn / warning / degraded / partial`
- 测速中：`testing / running / pending / queued`
- 缺失状态：显示“待测速”

### POST `/api/mirrors/select?instance=<id>`

```json
{ "id": "m1" }
```

响应：

```json
{ "ok": true, "selected_id": "m1" }
```

前端在单选框变更后立即调用并保存；失败时回滚到之前选择。也兼容没有 `selected_id`、仅有 `ok: true` 的响应。

### POST `/api/mirrors?instance=<id>`

```json
{ "name": "我的节点", "prefix": "registry.example.com" }
```

响应：

```json
{
  "ok": true,
  "node": {
    "id": "custom_1",
    "name": "我的节点",
    "prefix": "registry.example.com",
    "custom": true,
    "selected": false,
    "latency_ms": null,
    "status": "pending"
  }
}
```

前端校验：名称和前缀必填；前缀不能含空格；不能以 `http://` 或 `https://` 开头。添加成功后自动刷新并重新测速。

### DELETE `/api/mirrors/<node_id>?instance=<id>`

响应：

```json
{ "ok": true }
```

请求携带 `X-QB-Token`。前端只对 `custom: true` 节点显示删除入口，删除前有内联二次确认，不使用 `alert()` / `confirm()`。

## 5. 多实例控制台

### GET `/api/settings`

```json
{
  "ok": true,
  "bind_host": "127.0.0.1",
  "bind_port": 8765,
  "public_host": "",
  "first_run_complete": true,
  "mirror_nodes": [],
  "selected_mirror_id": "m1"
}
```

控制台用该接口显示本地服务监听地址、初始化状态和镜像节点数量。该接口失败不会阻塞实例列表。

### GET `/api/instances`

```json
{
  "ok": true,
  "instances": [
    {
      "id": "inst_abc123",
      "name": "群聊助手一号",
      "created_at": "2026-01-01T12:00:00Z",
      "deployed": true,
      "running": true,
      "qq_account": "123456789",
      "active_job_id": "job_abc",
      "urls": {
        "astrbot_webui": "http://127.0.0.1:6185",
        "snowluma_webui": "http://127.0.0.1:5099",
        "novnc": "http://127.0.0.1:6081"
      }
    }
  ]
}
```

控制台拿到列表后，会对每个实例额外请求：

```text
GET /api/state?instance=<id>
```

并用 state 中的 `deployment.deployed/running/active_job_id/active_job_status/urls` 覆盖/补充列表数据，确保任务状态和临时地址最新。某个实例 state 失败时只在对应卡片显示错误，不影响其他卡片。

### POST `/api/instances`

```json
{ "name": "群聊助手一号" }
```

```json
{ "ok": true, "instance": { "id": "inst_abc123", "name": "群聊助手一号" } }
```

成功后控制台执行：

```js
window.location.assign('/wizard?instance=' + encodeURIComponent(instance.id));
```

### DELETE `/api/instances/<id>`

删除一个实例及其容器和数据目录。需要 `X-QB-Token`。有活动部署任务时返回 400。
响应：

```json
{
  "ok": true,
  "instance": { "id": "bot-1", "name": "群聊助手一号" }
}
```

前端必须做二次点击确认，不能使用 `confirm()`/`alert()`。

### 控制台实例操作
### 控制台实例操作

对指定实例执行启动 / 停止 / 重启：

```text
POST /api/action?instance=<id>
```

```json
{ "action": "start" }
```

成功后控制台重新读取实例列表与每个实例 state。每个实例卡片包含：

- 打开向导：`/wizard?instance=<id>`
- AstrBot WebUI、SnowLuma WebUI、noVNC 链接（仅绝对 http/https 地址可用）
- 启动 / 停止 / 重启
- 当前部署任务状态与“查看进度”链接

## 6. 契约偏差 / 前端补充约定

核心路径和字段按 v2 任务契约实现，没有新增必需的后端接口。以下为前端容错和非破坏性补充：

1. 向导所有 API 默认追加 `instance` 查询参数；缺少实例时仅显示提示并保留旧式默认调用，不伪造实例。
2. GET 也携带 `X-QB-Token`，因为本地服务当前对所有 `/api/` 即做令牌校验；服务端可忽略 GET 上的该头。
3. 自动镜像测速的 `POST /api/mirrors/test` 请求体为 `{}`；如服务端需要指定节点，可接受 `ids` 数组。
4. `/api/mirrors/test` 兼容 `nodes + results`、仅 `nodes`、仅 `results` 三种响应。
5. `/api/job` 和 `/api/logs` 的 `logs` 兼容字符串或数组。
6. `active_job_status` 兼容 `null`、缺失和常见同义状态。中断/404/连续连接失败都会提供可见的继续或重试入口。
7. 控制台 `/api/settings` 失败只降级设置信息，不阻塞实例列表；单个 state 失败只影响对应卡片。
8. 前端不会构造端口；URL 仅接受绝对 `http://` / `https://`，不安全协议不生成可点击链接。
9. 前端不使用 localStorage / sessionStorage；刷新恢复完全依赖 `/api/state.deployment.active_job_id`。

## 6.1 SnowLuma 密码重置与自动更新

### POST `/api/snowluma-reset?instance=<id>`

需要 `X-QB-Token`。该操作会备份并重建 SnowLuma WebUI 的 `webui.json`，
保留 QQ/OneBot 数据，然后重启 SnowLuma 容器。成功响应：

```json
{
  "ok": true,
  "username": "admin",
  "password": "新的初始密码",
  "message": "SnowLuma WebUI 密码已重置，请用新密码登录。"
}
```

前端应在收到新密码后更新 SnowLuma 卡片，并提示用户在登录后自行修改并保存新密码。

### GET `/api/update/check`

需要 `X-QB-Token`。返回：

```json
{
  "ok": true,
  "can_update": true,
  "current": "abc1234",
  "remote": "def5678",
  "behind": 2,
  "ahead": 0,
  "message": "发现 2 个新提交，可以自动更新。"
}
```

如果工作区有未提交修改、不是 Git 仓库、无法访问 origin 或分支分叉，返回 `ok: false`
和可见的 `message`。

### POST `/api/update/apply`

需要 `X-QB-Token`，请求体为 `{}`。后台启动 `scripts/auto-update.sh`，执行
`git pull --ff-only`、同步已有实例插件、重启 AstrBot 容器并重启管理服务。响应：

```json
{
  "ok": true,
  "restarting": true,
  "message": "更新脚本已启动，管理服务会在几秒后重启。请稍后刷新页面。"
}
```

---

## 7. 安全文案边界

v2 默认将管理服务、AstrBot WebUI、SnowLuma WebUI 和 noVNC 的宿主机监听地址都设为
`0.0.0.0`，以便从用户自己的电脑直接访问服务器 IP。部署时必须使用防火墙/安全组/VPN
限制来源，不要把管理端口和 noVNC/VNC 端口直接暴露到公网。

向导和控制台均保留以下安全要求：

- 不收集、不保存 QQ 密码；现代 QQ 需要扫码登录。
- 如果扫码后要求设备验证、短信验证、滑块或安全验证，必须在 noVNC 里按 QQ 官方提示操作；工具不能绕过验证。
- 第三方协议端存在账号风控/封禁风险，使用小号，遵守 QQ 协议与当地法律。
- DeepSeek API Key 仅写入本机 AstrBot 配置，不在浏览器持久存储中保存，也不在控制台展示。

---

## 8. v3 可选运维接口（向后兼容）

所有接口仍要求 `X-QB-Token`。以下字段均为**新增可选字段**；v2 的 `/api/state`、`/api/instances`、
`/api/job/<id>`、`/api/action`、`/api/persona`、`/api/logs`、镜像和删除接口保持不变。
前端应把未知字段忽略，并在 404/405/网络失败时只降级对应卡片，不要阻断整页。

### 8.1 实例插件列表

```text
GET /api/instances/<instance_id>/plugins
```

成功时使用 AstrBot v4.29 的 `GET /api/v1/plugins`（兼容旧版 `GET /api/plugin/get`）。
插件记录来自 AstrBot，不伪造安装状态。

```json
{
  "ok": true,
  "plugins": [
    {
      "id": "qqbot_enhancer",
      "plugin_id": "qqbot_enhancer",
      "name": "QQ Bot 增强回复",
      "enabled": true,
      "version": "1.4.1",
      "description": "QQ 群 @/私聊必回、概率回复等增强。",
      "source": "installed",
      "reserved": false,
      "root_dir_name": "qqbot_enhancer",
      "restart_required": false,
      "error": ""
    }
  ],
  "items": [],
  "failed": "",
  "api_available": true,
  "cached": false,
  "restart_required": false,
  "message": "已从 AstrBot v4.29 插件接口读取插件状态。",
  "error": ""
}
```

- `source` 为 `installed` 或 `builtin`；`reserved=true` 表示 AstrBot 内置插件。
- `enabled` 来自 AstrBot 的 `activated` 字段；AstrBot 不可达时降级读取本地插件目录和
  `data_v4.db` 的禁用列表，此时 `enabled` 可能为 `null`，且 `api_available=false`。
- 降级响应仍是 HTTP 200 JSON，但 `ok=false`，前端据此禁用启停开关。Docker 不可用不能导致 500。

### 8.2 实例插件启停

```text
POST /api/instances/<instance_id>/plugins
POST /api/instances/<instance_id>/plugins/toggle   # 兼容别名
body: {"plugin_id": "qqbot_enhancer", "enabled": false}
```

服务端使用 AstrBot v4.29 真实接口
`PATCH /api/v1/plugins/enabled`；旧版 AstrBot 回退到
`POST /api/plugin/off` / `POST /api/plugin/on`。两者都会实际卸载/加载或更新运行时激活状态，
不会通过改仓库文件伪装成功。

```json
{
  "ok": true,
  "plugin": {
    "id": "qqbot_enhancer",
    "name": "QQ Bot 增强回复",
    "enabled": false,
    "version": "1.4.1",
    "description": "QQ 群 @/私聊必回、概率回复等增强。",
    "source": "installed",
    "restart_required": false,
    "error": ""
  },
  "restart_required": false,
  "message": "停用成功。",
  "error": ""
}
```

AstrBot v4.29 的启停接口会热更新插件；`restart_required=false`。如果 AstrBot 或接口不可达，
返回可操作的中文错误，不会把插件标记为已修改。至少支持管理内置 `qqbot_enhancer` 及
AstrBot 内置（reserved）插件。

兼容别名：`GET /api/plugins?instance=<id>`、`POST /api/plugins?instance=<id>`，请求体相同。

### 8.3 记忆模式

```text
GET  /api/instances/<instance_id>/memory
POST /api/instances/<instance_id>/memory
body: {"mode": "direct"}  或  {"mode": "astrbot"}
```

读取响应：

```json
{
  "ok": true,
  "mode": "astrbot",
  "memory_mode": "astrbot",
  "options": ["direct", "astrbot"],
  "memory_scope": "session",
  "group_session_key": "event.unified_msg_origin",
  "private_session_key": "event.unified_msg_origin",
  "unique_session": false,
  "group_memory_shared": true,
  "restart_required": false,
  "config_path": "…/data/astrbot/config/qqbot_enhancer_config.json",
  "astrbot_config_path": "…/data/astrbot/cmd_config.json",
  "behavior": {
    "group": "同一群的消息共享 event.unified_msg_origin 对应的会话。",
    "private": "私聊按用户各自的 event.unified_msg_origin 会话保存。",
    "reset": "/reset 清空当前会话历史，保留会话与人格。",
    "new": "/new 切换到一个新会话，旧历史仍保存在 AstrBot 数据库中。"
  },
  "message": "astrbot 模式使用 AstrBot 当前会话……",
  "error": ""
}
```

保存成功响应额外包含 `applied_live`：

```json
{
  "ok": true,
  "mode": "astrbot",
  "memory_mode": "astrbot",
  "memory_scope": "session",
  "applied_live": true,
  "restart_required": true,
  "message": "AstrBot 已热重载插件……重启 AstrBot 后共享会话迁移生效。",
  "error": ""
}
```

语义与迁移：

- `direct` 是既有安装的默认值。读取旧配置时**不会写入或覆盖**用户已经选择的 `direct`；只有缺失
  字段才按默认值补齐。
- `astrbot` 模式把回复交给 AstrBot 默认 Agent。AstrBot v4.29 的
  `platform_settings.unique_session` 必须为 `false`，群聊才会按
  `event.unified_msg_origin` 共享同一份会话记忆；私聊的 UMO 天然包含用户会话，因此按用户隔离。
- 保存 `astrbot` 时，服务端会以增量方式把 `cmd_config.json` 的
  `platform_settings.unique_session` 迁移为 `false`，保留其它配置。迁移需要 AstrBot 重启才完全生效，
  所以 `restart_required=true`；插件配置本身通过真实 `PUT /api/v1/plugins/config` 接口热重载。
- AstrBot 未运行时可先把插件配置写到本地文件，`applied_live=false`、`restart_required=true`。
  AstrBot 无法写入 `cmd_config.json` 等无法保证共享会话的情况返回 `ok=false` 和中文原因。
- `/reset` 清空**当前**会话历史并保留会话与人格；`/new` 切换到新会话，旧会话仍在 AstrBot SQLite
  数据卷中，可按会话管理功能查看。两命令都不提供跨群共享知识库；长期记忆边界是 AstrBot 的会话存储，
  不是无限期全局记忆。
- 兼容别名：`GET/POST /api/memory?instance=<id>`，POST 请求体相同。

### 8.4 资源统计

```text
GET /api/stats?instance=<instance_id>
GET /api/instances/<instance_id>/stats
```

省略 `instance` 时返回全局视图（`scope=global`）。Docker、`/proc` 或 Windows 内存 API 不可用时，
接口仍返回 HTTP 200 JSON，`ok=true`，并在 `errors`、`host_memory.error` 或对应 Docker 条目的
`error` 中说明原因。

```json
{
  "ok": true,
  "scope": "instance",
  "instance_id": "bot-1",
  "generated_at": "2026-01-01T12:00:00+00:00",
  "cached": false,
  "host_memory": {
    "total_bytes": 17179869184,
    "available_bytes": 8589934592,
    "used_bytes": 8589934592,
    "used_percent": 50.0,
    "source": "/proc/meminfo",
    "error": ""
  },
  "instances": [
    {
      "instance_id": "bot-1",
      "name": "群聊助手一号",
      "directory": "/home/user/.local/share/qqbot-studio/instances/bot-1",
      "data_dir_size_bytes": 1234567,
      "data_size_bytes": 1234567,
      "file_count": 120,
      "rollup": [
        {"path": "data", "bytes": 1200000, "file_count": 100, "errors": []}
      ],
      "errors": []
    }
  ],
  "docker": [
    {
      "instance_id": "bot-1",
      "container": "qqbot-bot-1-astrbot",
      "service": "astrbot",
      "status": "available",
      "cpu_percent": 1.25,
      "memory_usage_bytes": 104857600,
      "memory_limit_bytes": 1073741824,
      "memory_percent": 9.77,
      "error": ""
    }
  ],
  "global": {
    "data_dir": "/home/user/.local/share/qqbot-studio",
    "data_dir_size_bytes": 5000000,
    "file_count": 400,
    "instances_total_bytes": 4000000,
    "disk": {
      "total_bytes": 100000000000,
      "used_bytes": 40000000000,
      "free_bytes": 60000000000,
      "used_percent": 40.0,
      "error": ""
    }
  },
  "errors": [],
  "error": "",
  "message": "资源统计已更新。"
}
```

- 全局和每个实例的数据目录占用使用标准库递归统计，不跟随符号链接；单文件/目录读取失败不会使整个响应失败。
- Docker 容器 CPU/内存通过 `docker stats --no-stream --format '{{json .}}' <containers...>` 获取。
- 响应短时缓存约 5 秒，`cached=true` 表示命中缓存；不要求前端参与缓存。
- 兼容旧字段：没有移除 `/api/state` / `/api/instances` 中任何已有字段。
  `/api/state` 的 `deployment.memory_mode` 和顶层 `memory_mode` 是新增可选提示。

### 8.5 v1/v2 旧数据兼容与迁移

- v1 单实例根目录布局（`config.json`、`secrets.json`、`compose.yml`、`.env`、`jobs/`、`data/`）
  会在首次启动多实例服务时迁移到 `instances/bot-1`；原 compose、`.env` 和 `cmd_config.json`
  内容保持不变，旧容器名/网络名会写入实例元数据以便继续管理。
- v2 实例目录缺少 `instance.json` 时，会从 `compose.yml`/`config.json` 推断并补写最小元数据；
  已有 `instance.json` 字段优先，不删除未知字段。
- `config.json` 的新增字段采用 additive migration：只补缺失值，不覆盖用户已有的
  `memory_mode=direct` 或其它调优值。`cmd_config.json` 的 `platform_settings` 其他键保持不变。
- 旧版本地插件配置缺少 `memory_mode` 时按 `direct` 处理；只有保存记忆模式时才写入明确值。
  旧版 `compose.yml`/`.env` 不需要为了升级后端而重写，更新镜像时仍可沿用。

---

## 9. 可选 AI 助手 B 安装与状态

AI 助手 B 是独立仓库中的可选生成式 AI 助手，不是主项目内置的离线助手 A。A 始终保留；
B 默认不安装、不自动启动，主项目也不会读取或传递 B 的 API Key。

固定仓库（服务端不接受请求体传入 URL 或路径）：

```text
https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git
```

固定安装目录：

```text
<QQBOT_STUDIO_DATA_DIR>/extensions/ai-assistant
```

Linux 默认即 `~/.local/share/qqbot-studio/extensions/ai-assistant`。服务端只 clone/更新该独立
仓库，不执行该仓库内的任意脚本。B 的依赖安装、密钥配置和启动必须由用户按 B 的 README 手动完成。

### 9.1 状态

```text
GET /api/assistant/status
```

需要 `X-QB-Token`。未安装、扩展目录缺失、目录不完整或 Git 不可用时仍返回 HTTP 200 JSON，不返回
500；`installed=false` 是正常状态。检查失败时 `ok=false` 且 `error/message` 说明原因。

```json
{
  "ok": true,
  "installed": true,
  "path": "/home/user/.local/share/qqbot-studio/extensions/ai-assistant",
  "repo": "https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git",
  "commit": "c15fb5b",
  "entry": "/home/user/.local/share/qqbot-studio/extensions/ai-assistant/assistant.py",
  "readme": "/home/user/.local/share/qqbot-studio/extensions/ai-assistant/README.md",
  "entry_file": "assistant.py",
  "readme_file": "README.md",
  "entry_exists": true,
  "readme_exists": true,
  "repo_ok": true,
  "directory_exists": true,
  "branch": "main",
  "dirty": false,
  "action": "",
  "message": "AI 助手 B 已安装。主项目不会自动启动它。",
  "error": ""
}
```

字段说明：

- `entry` / `readme` 为便于界面展示的**绝对路径**；`entry_file` / `readme_file` 为仓库内相对文件名。
- `commit` 为当前 `HEAD` 的短 commit；无 Git 仓库时为空字符串。
- `installed=true` 要求扩展目录、`.git`、`assistant.py`、`README.md` 和有效 commit 均可用。
- 响应不包含 B 的 `config.json`、DeepSeek API Key、访问令牌或任何配置内容。
- 扩展路径是符号链接、不是目录或必要文件不完整时，`ok=false`，但仍返回完整字段供界面降级展示。

### 9.2 安装 / 更新

```text
POST /api/assistant/install
POST /api/assistant/update
Content-Type: application/json
body: {}
```

需要 `X-QB-Token`。

`/api/assistant/install`：

- 未安装：clone 固定官方仓库到临时目录，校验 `assistant.py` 和 `README.md` 后原子移动到固定路径。
- 已安装：等价于执行安全快进更新。
- 已存在非空但不是有效 Git 仓库的目录：拒绝操作，绝不删除或覆盖用户目录。
- clone/网络失败：返回 `ok=false`，主项目、A 离线助手和已有实例不受影响。

`/api/assistant/update`：

- 先检查工作区必须干净；有未提交修改时拒绝。
- 执行 `git fetch origin --prune`，确认 `HEAD` 是远端分支的祖先，再执行 `git merge --ff-only`。
- 不支持 merge commit、rebase、强制重置或覆盖配置。

成功安装示例（更新时 `action` 为 `updated`，并可能带 `updated:true`）：

```json
{
  "ok": true,
  "installed": true,
  "path": "/home/user/.local/share/qqbot-studio/extensions/ai-assistant",
  "repo": "https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git",
  "commit": "c15fb5b",
  "entry": "/home/user/.local/share/qqbot-studio/extensions/ai-assistant/assistant.py",
  "readme": "/home/user/.local/share/qqbot-studio/extensions/ai-assistant/README.md",
  "action": "installed",
  "message": "AI 助手 B 已安装到本机扩展目录。主项目不会自动启动它；请阅读 README 并运行扩展目录内的 install.sh。",
  "error": ""
}
```

失败也使用 HTTP 200 + `ok=false`，便于控制台只降级 B 卡片。典型错误包括网络不可达、
未安装 Git、工作区不干净、非快进更新、扩展目录不是 Git 仓库；不会把 stdout/stderr 中的
敏感配置回显到前端，也不会为了重试删除旧安装。

### 9.3 `install.sh` 可选安装入口

主项目 `install.sh` 在依赖检查后、启动主服务前询问是否安装 B：

- 首次安装：默认回答为否；明确提示会联网 clone 到
  `$QQBOT_STUDIO_DATA_DIR/extensions/ai-assistant`，并说明 A 离线助手始终可用。
- 已有 B：询问是否执行快进更新；有未提交修改时跳过。
- 非交互环境默认跳过，不阻塞安装。
- 也可显式设置环境变量：
  - `QQBOT_INSTALL_AI_ASSISTANT=1` 安装/更新；
  - `QQBOT_INSTALL_AI_ASSISTANT=0` 跳过。
- 网络失败只打印警告和可重试命令，主安装继续。
- clone 成功后只提示用户手动执行 B 的安装器：
  `cd "$AI_ASSISTANT_DIR" && ./install.sh`，主项目**不自动启动 B**，不运行 B 的脚本，不传 Key。

手动重试示例：

```bash
AI_ASSISTANT_DIR="${QQBOT_STUDIO_DATA_DIR:-$HOME/.local/share/qqbot-studio}/extensions/ai-assistant"
git clone --depth 1 https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git "$AI_ASSISTANT_DIR"
cd "$AI_ASSISTANT_DIR"
./install.sh
```

这些接口只新增能力，不修改 v2 现有 API 字段、旧实例目录或容器配置。旧后端没有
`/api/assistant/*` 时，控制台应按 404/405 降级为“当前后端不支持扩展安装”，A 离线助手仍可用。
