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

`prompt` 按字符串原样传给后端；服务端只校验非空和长度，不折叠、不 `splitlines`、不删除内部
`\n`/空行。API 会保留内部换行及首尾空白，AstrBot persona 和插件 `system_prompt` 使用同一
原始文本。前端提示词预览使用 `white-space: pre-wrap` 显示多行。

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
  "warning": "AstrBot unique_session 迁移未完成：无法解析 JSON object：…（文件为空或正在并发写入）",
  "warnings": ["AstrBot unique_session 迁移未完成：…"],
  "nonfatal": true,
  "nonfatal_warning": true,
  "nonfatal_errors": ["AstrBot unique_session 迁移未完成：…"],
  "message": "AstrBot 已热重载插件……非致命警告：AstrBot unique_session 迁移未完成……",
  "error": ""
}
```

`warning` / `warnings` / `nonfatal` 是新增字段，用于区分“模式已经保存”与“可选的
`unique_session` 迁移暂未完成”。只要增强插件本地配置已安全写入且 Studio mirror 已更新，
即使 `cmd_config.json` 在并发写入窗口暂时为空/半写，响应仍为 `ok:true` 和 `error:""`；
`unique_session` 可能为 `null`，`restart_required` 保留原语义（仍为布尔值）。

语义与迁移：

- `direct` 是既有安装的默认值。读取旧配置时**不会写入或覆盖**用户已经选择的 `direct`；只有缺失
  字段才按默认值补齐。
- `astrbot` 模式把回复交给 AstrBot 默认 Agent。AstrBot v4.29 的
  `platform_settings.unique_session` 必须为 `false`，群聊才会按
  `event.unified_msg_origin` 共享同一份会话记忆；私聊的 UMO 天然包含用户会话，因此按用户隔离。
- 保存 `astrbot` 时，服务端会以增量方式把 `cmd_config.json` 的
  `platform_settings.unique_session` 迁移为 `false`，保留其它配置。迁移需要 AstrBot 重启才完全生效，
  所以 `restart_required=true`；插件配置本身通过真实 `PUT /api/v1/plugins/config` 接口热重载。
- 后端读取 `cmd_config.json` 时会针对临时空文件/并发截断重试最多 3 次（约 100ms 间隔）。
  稳定损坏的 JSON 仍不会被当作正常配置；但此时如果增强插件配置和 Studio mirror 已经成功保存，
  保存响应仍为 `ok=true`，并通过 `warning/warnings/nonfatal` 说明 `unique_session` 迁移未完成，
  不会把已经切换的记忆模式误报成失败。`unique_session` 可为 `null`（未知而非伪造 false），
  `error` 为空字符串；以上字段均是 additive，不改变旧字段类型。
- AstrBot 未运行时可先把插件配置写到本地文件，`applied_live=false`、`restart_required=true`。
- `/reset` 清空**当前**会话历史并保留会话与人格；`/new` 切换到新会话，旧会话仍在 AstrBot SQLite
  数据卷中，可按会话管理功能查看。两命令都不提供跨群共享知识库；长期记忆边界是 AstrBot 的会话存储，
  不是无限期全局记忆。
- 兼容别名：`GET/POST /api/memory?instance=<id>`，POST 请求体相同。
- 插件配置写入会同时原子更新 `<qqbot_enhancer_config>.bak` 最近良好快照（0600）。
  主配置损坏时读取优先回退 backup；无 backup 时回退内存 cached 值或 Studio 当前
  `memory_mode` 镜像，并返回明确 `error/warning`，不会把默认 `direct` 写回损坏文件。
  只有显式写操作才允许修复主配置。

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
      "errors": [],
      "warnings": [],
      "partial": false,
      "permission_denied": false
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
  "warnings": [],
  "partial": false,
  "permission_denied": false,
  "error": "",
  "message": "资源统计已更新。"
}
```

- 全局和每个实例的数据目录占用使用标准库递归统计，不跟随符号链接；单文件/目录读取失败不会使整个响应失败。
- 遇到 `PermissionError` 时跳过不可读目录/文件，继续统计同一实例中可读的 core/其它目录，并在
  `warnings`/`errors` 中返回明确提示（例如“部分不可读（权限不足，已跳过）：…”）。
  此时 `ok` 仍为 `true`、`error` 为空，新增的 `partial=true`、`permission_denied=true`；
  实例条目和 `global` 也有对应的 `partial/warnings/permission_denied` 字段。
- `/console` 只显示可读部分的统计，并在实例 V3 状态/全局统计提示中展示“部分指标读取失败”；
  `warnings` 文本包含“部分不可读”，不会因为插件收藏表情目录不可读而把整份资源统计置为失败。
- Docker 容器 CPU/内存通过 `docker stats --no-stream --format '{{json .}}' <containers...>` 获取。
- 响应短时缓存约 5 秒，`cached=true` 表示命中缓存；不要求前端参与缓存。
- 兼容旧字段：没有移除 `/api/state` / `/api/instances` 中任何已有字段。
  `/api/state` 的 `deployment.memory_mode` 和顶层 `memory_mode` 是新增可选提示。

### 8.5 v2/v4 当前数据兼容

- V1 单实例根目录布局（`config.json`、`secrets.json`、`compose.yml`、`.env`、`jobs/`、`data/`）
  的自动导入/移动已移除；这些根目录旧文件不会被删除或改写。需要继续使用的用户应新建当前
  `instances/<id>` 实例后手动迁移业务数据。
- v2 实例目录缺少 `instance.json` 时，会从 `compose.yml`/`config.json` 推断并补写最小元数据；
  已有 `instance.json` 字段优先，不删除未知字段。
- `config.json` 的新增字段采用 additive migration：只补缺失值，不覆盖用户已有的
  `memory_mode=direct` 或其它调优值。`cmd_config.json` 的 `platform_settings` 其他键保持不变。
- 旧版本地插件配置缺少 `memory_mode` 时按 `direct` 处理；只有保存记忆模式时才写入明确值。
  旧版 `compose.yml`/`.env` 不需要为了升级后端而重写，更新镜像时仍可沿用。

---

## 9. 可选 AI 助手 B 安装与状态

AI 助手 B 是独立仓库中的可选生成式 AI 助手，主控制台通过独立 `/assistant` 页面管理；
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
- clone/网络失败：返回 `ok=false`，主项目、主控制台和已有实例不受影响。

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
  `$QQBOT_STUDIO_DATA_DIR/extensions/ai-assistant`，并说明主控制台/已有实例不受影响。
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
`/api/assistant/*` 时，控制台应按 404/405 降级为“当前后端不支持扩展安装”，主控制台仍可用。

---

## 10. AI 助手 B：配置与启动

这些接口只管理独立 AI 助手 B 的本机配置和进程，不改变主控制台其他功能，也不自动启动 B。
与第 9 节相同，全部需要 `X-QB-Token`；可选功能失败会返回 HTTP 200 + `ok:false`，
不影响主控制台、实例或 A。

B 的配置文件固定为：

```text
<QQBOT_STUDIO_DATA_DIR>/ai-assistant/config.json
```

文件在 Linux/POSIX 上以 0600 原子写入，目录优先 0700。服务端读取配置后只返回安全状态，
**不会返回 `api_key`，也不会把 Key 写进日志或进程参数**。

### 10.1 读取配置/运行状态

```text
GET /api/assistant/config
```

```json
{
  "ok": true,
  "installed": true,
  "configured": true,
  "running": false,
  "url": "http://127.0.0.1:8766/",
  "configure_url": "/api/assistant/config",
  "config_path": "/home/user/.local/share/qqbot-studio/ai-assistant/config.json",
  "key_present": true,
  "key_source": "independent",
  "instance_id": "",
  "port": 8766,
  "host": "127.0.0.1",
  "public_host": "",
  "health_url": "http://127.0.0.1:8766/",
  "pid": null,
  "log_path": "/home/user/.local/share/qqbot-studio/ai-assistant/assistant.log",
  "pid_path": "/home/user/.local/share/qqbot-studio/ai-assistant/assistant.pid",
  "access_note": "AI 助手 B 没有面向用户的访问认证，只允许监听本机回环地址……",
  "ssh_tunnel": "ssh -N -L 8766:127.0.0.1:8766 <用户名>@<服务器地址>",
  "message": "AI 助手 B 已配置但尚未启动。启动后仅可从本机或 SSH 隧道访问。",
  "error": ""
}
```

- 未安装扩展时 `installed=false`；未创建配置时 `configured=false`；两者都不返回 500。
- `configured` 仅在检测到可用 Key 时为 true：
  - `independent`：B 配置文件中存在非空 `api_key`；
  - `instance`：引用的实例目录存在且其 AstrBot `cmd_config.json` 中存在 provider Key。
- `key_present` 是布尔值，不暴露 Key 内容。
- `running` 通过探测 `health_url + /api/health` 判定；不会仅因 PID 文件存在就认为运行中。
  即使配置了公网 URL，健康检查也优先使用服务器本机回环地址，避免公网 NAT 回环/Hairpin 失败。
- `url` 是给浏览器使用的地址，绝不含监听占位地址 `0.0.0.0`：
  - host 为 `127.0.0.1`/`localhost`/`::1`：返回对应 loopback URL；
  - host 为 `0.0.0.0` 且 `public_host` 非空：返回 `http://<public_host>:<port>/`；
  - host 为 `0.0.0.0` 且 `public_host` 为空：`url=""`，前端应提示填写公网 IP/域名。
- `GET /api/assistant/status` 也会合并上述安全配置摘要，包含 `url`、`public_host`、
  `health_url`、`access_note`、`ssh_tunnel`，但仍不会包含 Key。
- 回环模式的 `access_note` 和 `ssh_tunnel` 说明远端用户应使用 SSH 隧道。
- 公网模式的 `access_note` 会警告 B 没有认证，必须用安全组/防火墙/VPN/认证反向代理限制来源；
  提供公网 URL 时 `ssh_tunnel` 为空，未提供 public_host 时 ssh_tunnel 作为安全替代建议。

### 10.2 保存配置

```text
POST /api/assistant/config
Content-Type: application/json
```

独立 Key 模式：

```json
{
  "key_source": "independent",
  "api_key": "sk-用户本地Key",
  "instance_id": "",
  "port": 8766,
  "host": "127.0.0.1"
}
```

复用实例 Key 模式：

```json
{
  "key_source": "instance",
  "instance_id": "bot-1",
  "port": 8767,
  "host": "127.0.0.1"
}
```

显式公网/所有网卡模式（高风险）：

```json
{
  "key_source": "independent",
  "api_key": "sk-用户本地Key",
  "port": 8768,
  "host": "0.0.0.0",
  "public_host": "bot.example.com"
}
```

规则：

- `key_source` 只能是 `independent` 或 `instance`。
- `api_key` 是**只写字段**，成功响应、错误响应、GET 配置响应和白名单日志均不包含它。
  独立模式下省略或发送空字符串会保留已有 Key，便于只修改端口/界面；切换到 `instance`
  模式时会清空 B 配置中的 `api_key`，绝不把实例 Key 复制到 B 配置。
- `instance_id` 仅用于 `instance` 模式，必填且只允许字母、数字、点、下划线和连字符；
  不安全字符或路径穿越会被拒绝。实例不存在时仍允许保存，但 `configured/key_present`
  会是 false，界面应提示先部署实例或补齐 Key。
- `host` 默认 `127.0.0.1`；允许 `127.0.0.1`、`localhost`、`::1`，以及**显式高风险值**
  `0.0.0.0`。默认保持回环安全模式；传入 `0.0.0.0` 表示所有网卡，必须同时评估安全组、
  防火墙和 B 无用户认证的风险。
- `public_host` 可选，只用于给 `host=0.0.0.0` 生成浏览器 URL；值是公网 IPv4、IPv6 或域名，
  不能包含 `http://`/`https://`、路径、查询、用户信息、端口或空格。
  端口使用独立 `port` 字段。`public_host` 不能是 `0.0.0.0`/`::` 等未指定地址。
  IPv6 可传裸地址或方括号形式，响应 URL 会自动加方括号。
- 服务端在 `host=0.0.0.0` 时仍使用 `127.0.0.1` 探测端口和健康状态；不把 `0.0.0.0`
  写进浏览器 URL，也不使用公网 URL 做本机健康检查。
- `port` 范围为 5000–10000（含边界）。服务端会避开管理服务监听端口和所有实例的
  AstrBot/SnowLuma/noVNC 端口。未指定端口时优先 8766，再选择范围内可用端口。
- 写入成功后返回与 GET 配置相同的安全字段，并带 `action:"configured"`。
- 配置缺失、损坏、权限写入失败、端口冲突等返回 `ok:false` 和可操作中文错误。

### 10.3 启动 B

```text
POST /api/assistant/start
Content-Type: application/json
body: {}   # 也接受空请求体
```

要求已安装扩展且配置文件存在。服务端：

1. 先探测 `GET <health_url>/api/health`；若已在运行则幂等返回 `action:"already_running"`，不重复启动。
2. 使用当前 Python 解释器执行：
   `python3 <extensions/ai-assistant>/assistant.py --config <data>/ai-assistant/config.json`
3. 独立进程、`start_new_session`/Windows 新进程组启动；stdout/stderr 追加到
   `data_dir/ai-assistant/assistant.log`，PID 写入 `data_dir/ai-assistant/assistant.pid`（0600）。
4. 启动时只注入 `QQBOT_STUDIO_HOME=<主数据目录>` 等非敏感环境变量，**不通过 argv/env 传 Key**。
5. 轮询 B 的 `/api/health`；失败或提前退出会清理 PID、终止残留子进程，并返回有限长度的脱敏日志摘要。
6. 健康检查成功后，服务端还会读取实际 TCP listener（Linux `/proc/net/tcp`/`tcp6`，
   Windows `netstat -ano`，测试可注入 `listener_probe`）。`host=0.0.0.0` 时必须看到
   `0.0.0.0`/`::` 通配监听；若只看到 `127.0.0.1`/`::1`，会终止本次刚启动的错误进程并返回
   `ok:false, action:"listener_mismatch", error_code:"listener_loopback_only"`。
   不会把“health 能通”误报成“公网模式启动成功”。

成功响应包含与 GET 配置相同的字段，并增加：

```json
{
  "ok": true,
  "action": "started",
  "running": true,
  "pid": 12345,
  "url": "http://127.0.0.1:8766/",
  "public_host": "",
  "health_url": "http://127.0.0.1:8766/",
  "message": "AI 助手 B 已启动：http://127.0.0.1:8766/。服务仅监听回环地址；远端访问请使用 SSH 隧道。",
  "error": ""
}
```

公网模式成功时 `url` 形如 `http://bot.example.com:8768/`，`health_url` 仍是
`http://127.0.0.1:8768/`，并在 `access_note` 中返回高风险提示。若没有 `public_host`，
`url` 为空字符串，不会返回 `http://0.0.0.0:...`。
公网模式响应还会带 `listener`、`listener_match`、`listener_ok`、`error_code`、
`suggestions/recommendations`：只有实际 listener 为 `0.0.0.0`/`::` 才算公网绑定成功；
若仍只有回环，返回 `ok:false` 且建议更新 B 后“停止→启动”，不会静默报告已成功打开公网。

主项目要求已有可用 Key 才启动 B：`independent` 模式必须有已保存 Key，`instance` 模式必须能读取所选实例的 AstrBot Key；否则返回 `ok:false` 和中文配置提示。
主项目不会自动启动 B，也不会调用 B 的聊天 API 或读取聊天内容。

### 10.4 停止 B（可选能力，已实现）

```text
POST /api/assistant/stop
Content-Type: application/json
body: {}   # 也接受空请求体
```

- 未运行/只有过期 PID 文件时幂等返回 `running:false` 并清理过期 PID。
- 仅在 B 健康响应正常且 PID 文件受本机保护时，才发送 `SIGTERM`；POSIX 下若 `/proc/<pid>/cmdline`
  可用，会校验其中包含 `assistant.py`，避免误杀其他进程。
- 超时仍运行、无 PID 或 PID 异常时返回 `ok:false`，不会盲目强杀。
- 成功返回 `action:"stopped"`, `running:false`，不含 Key 或日志内容。

### 10.5 公网访问诊断

```text
GET /api/assistant/diagnose
```

需要 `X-QB-Token`。此接口只读，不会启动/停止 B，也不会修改云安全组、NAT 或主机防火墙。
它用于把“公网拒绝连接”拆成可验证的层次，返回安全字段：

```json
{
  "ok": true,
  "healthy": false,
  "installed": true,
  "configured": true,
  "running": true,
  "pid": 12345,
  "host": "0.0.0.0",
  "port": 8766,
  "public_host": "bot.example.com",
  "url": "http://bot.example.com:8766/",
  "health_url": "http://127.0.0.1:8766/",
  "listener": {
    "supported": true,
    "listening": true,
    "bind_addresses": ["127.0.0.1", "::1"],
    "wildcard": false,
    "loopback_only": true,
    "source": "proc/net/tcp"
  },
  "listener_match": "loopback_only",
  "listener_ok": false,
  "local_health": {"ok": true, "url": "http://127.0.0.1:8766/", "detail": "本机 health 成功", "error": ""},
  "public_probe": {
    "checked": true,
    "skipped": false,
    "status": "unreachable",
    "tcp_connected": false,
    "http_ok": null,
    "inconclusive": true,
    "remote_verification_required": true,
    "detail": "服务器侧无法回环公网地址；可能 NAT hairpin/安全组/防火墙"
  },
  "public_access_verified": false,
  "old_b_suspected": true,
  "b_entry_supports_wildcard": false,
  "log_tail": "最近的脱敏日志摘要",
  "error_code": "listener_loopback_only",
  "suggestions": [
    "先在“安装状态”点击“更新 AI 助手 B”。",
    "再执行一次“停止 → 启动”，让新版本和 host=0.0.0.0 生效。",
    "从外部网络验证 URL，并检查云安全组/主机防火墙。"
  ],
  "message": "B 实际只监听回环地址，公网模式未生效。",
  "error": ""
}
```

诊断字段与解释：

- `listener.supported/listening/bind_addresses/wildcard/loopback_only`：实际 TCP 监听结果，
  不依赖 `host` 配置字符串；Linux 读取 `/proc/net/tcp`、`tcp6`，Windows 调用无 shell 的
  `netstat -ano -p tcp`。平台不支持时 `supported=false`，不会伪报成功。
- `listener_match`：`wildcard`（0.0.0.0 正常）、`loopback_only`（只监听回环）、
  `missing`（无 listener）、`unknown`（平台无法探测）、`ok`（回环模式正常）。
- `local_health`：服务器本机回环 `/api/health` 的真实结果；公网 URL 不用于本机健康检查。
- `public_probe`：服务器侧可选 TCP+HTTP 探测。`status=unreachable/tcp_only` 时
  `inconclusive=true`，因为服务器访问自己的公网 IP 可能受 NAT hairpin 影响；必须从外部网络复核，
  不能把它直接当作外网一定不可达。`public_access_verified` 为 `true`（探测成功）、
  `false`（未确认/失败）或 `null`（回环模式或已跳过）。为避免 SSRF，探测只连接解析后的全局公网地址；
  私网/回环/元数据地址返回 `non_public_target` 并跳过。
- `error_code`：例如 `not_installed`、`not_configured`、`not_running`、
  `listener_loopback_only`、`listener_missing`、`public_probe_inconclusive`、
  `public_probe_dns_error`、`ok_loopback`、`ok`。
- `suggestions` / `recommendations`：中文可操作建议。公网探测失败会建议检查：
  1) 从外部网络访问；2) 云安全组放行 TCP 端口；3) UFW/firewalld；4) public_host/端口/NAT 映射；
  5) 更新 B 后“停止→启动”。
- `old_b_suspected`：配置为 `0.0.0.0` 且实际只监听回环，同时入口文件未出现
  `0.0.0.0` 支持标记时为 true；这是“旧版 B/未重启”的强提示，不是唯一判据。
- `log_tail`：脱敏后的最近日志摘要；不返回 Key、token、password、secret。

主项目**不会**自动打开云安全组或主机防火墙；`/assistant` 页面的“诊断公网访问”只显示结果和
建议。公网模式没有用户认证，页面会继续显示高风险警告，并建议安全组/防火墙/VPN/认证反代限制来源。

### 10.6 安全与兼容边界

- `configure_url` 固定为 `/api/assistant/config`，供前端识别配置入口。
- B 服务自身**没有用户认证**，默认 host 为 `127.0.0.1`。`0.0.0.0` 是显式高风险选项：
  公网使用时必须使用云安全组、UFW/nftables、VPN 或带认证的反向代理限制来源；
  禁止直接裸奔公网。无法安全限制来源时，优先回环模式 + SSH 隧道。
- `api_key` 只在写配置请求体中出现；服务端不打印请求体、不把 Key 放进程环境/命令行、
  不在 GET 配置或错误信息中回显。错误只返回脱敏后的摘要。
- 新接口只新增，不改变第 1–9 节已有 API 字段；旧实例目录不迁移、不删除。
- 旧后端返回 404/405 时，前端应只把 B 卡片显示为“当前后端不支持 B 配置/启动”，主控制台仍可用。

---

## 11. 插件参数读取与编辑

这些接口让控制台读取/编辑某个实例的单个插件参数，重点是 `qqbot_enhancer` 的
`reply_probability`。服务端以插件目录中的 `_conf_schema.json` 为字段白名单，不修改 schema，
也不会读取/返回其它插件的配置。

### 11.1 读取插件参数

```text
GET /api/instances/<instance_id>/plugins/<plugin_id>/config
```

兼容 query 别名：

```text
GET /api/plugins/config?instance=<instance_id>&plugin_id=<plugin_id>
GET /api/plugin-config?instance=<instance_id>&plugin_id=<plugin_id>
```

需要 `X-QB-Token`。成功响应：

```json
{
  "ok": true,
  "plugin_id": "qqbot_enhancer",
  "schema": {
    "reply_probability": {
      "description": "群聊非 @ 消息的回复概率",
      "type": "float",
      "default": 0.12,
      "slider": {"min": 0, "max": 1, "step": 0.01}
    },
    "image_search_enabled": {
      "description": "启用图片搜索",
      "type": "bool",
      "default": true
    }
  },
  "values": {
    "reply_probability": 0.12,
    "image_search_enabled": true
  },
  "schema_source": "bundled",
  "schema_path": "/opt/qqbot-studio/plugins/qqbot_enhancer/_conf_schema.json",
  "schema_warning": "",
  "config_path": "/home/user/.local/share/qqbot-studio/instances/bot-1/data/astrbot/config/qqbot_enhancer_config.json",
  "source": "astrbot",
  "api_available": true,
  "restart_required": false,
  "message": "已从 AstrBot 在线接口读取插件配置，并合并本地值。",
  "error": ""
}
```

读取规则：

- 内置 `qqbot_enhancer` **优先**读取主项目 `plugins/qqbot_enhancer/_conf_schema.json`（bundled，版本固定）；
  实例 `<astrbot_data>/plugins/qqbot_enhancer/_conf_schema.json` 仅作备用，不复制、不覆盖。
- 其它插件保持实例 schema 优先；实例 schema 不可用时回退 bundled。
- 对 missing、PermissionError/OSError、半写 JSON、损坏 JSON、空 object/无字段 schema 做最多 3 次短重试；
  本地 schema 失败但 bundled 成功时仍返回 HTTP 200 + `ok:true`，UI 可正常编辑。
- 响应字段：
  - `schema_source`: `bundled` / `local`
  - `schema_path`: 实际读取路径
  - `schema_warning`: 本地 schema 失败但已回退时的安全说明
  - 只有 bundled 和实例 schema 都不可用时才返回 `ok:false`，错误列出实例路径与主项目内置路径（脱敏、无 Key）。
- 合并本地 `<astrbot_data>/config/<plugin_id>_config.json`；
- AstrBot 在线时再以真实 `GET /api/v1/plugins/config` 返回的当前配置为准；
- AstrBot 离线时仍返回 HTTP 200 + `ok:true`，`api_available:false`、`source:"local"` 或
  `source:"schema"`，不返回 500、不阻塞控制台；
- 本地插件配置读取会对临时空文件/半写 JSON 做最多 3 次重试；稳定损坏时按下一节恢复链
  返回 ok:true + recovered/warning，而不是阻塞阅读。读取不会覆盖坏文件。
- `source` 为 `astrbot` / `local` / `schema`；
- `schema` 和 `values` 会过滤掉字段名或 schema 标记中带 `api_key`、`token`、`password`、
  `secret`、`credential` 等敏感语义的 Key 类字段。
- 本地插件配置坏 JSON 时不再返回整体 `ok:false` 阻塞 AI 工具权限。服务端会保留原文件及
  `.corrupt-<UTC时间>` 快照，按 `.bak` → 内存 cached → Studio `memory_mode`/镜像字段 →
  schema defaults 顺序恢复，返回 `ok:true`、可用 schema/values，以及：
  - `recovered: true`
  - `recovery_source: backup|cache|studio|schema`
  - `warning` / `warnings`
  - `corrupt_snapshot`
  消息明确提示“已恢复，请检查并保存”。读取路径不删除、不覆盖损坏主文件；只有用户显式保存
  才通过原子写修复主文件（并刷新 `.bak`）。
- 记忆模式遇到相同的坏 JSON 也走恢复链，不默认回落 `direct`；确实没有任何历史值时返回
  `mode="unknown"` + 明确 error，而不是伪造 direct。

### 11.2 保存插件参数

```text
POST /api/instances/<instance_id>/plugins/<plugin_id>/config
Content-Type: application/json
```

```json
{
  "values": {
    "reply_probability": 0.5,
    "image_search_enabled": false,
    "image_search_timeout_seconds": 12
  },
  "restart": false
}
```

键 `values` 必须是 JSON object；`restart` 为可选布尔值。服务端只更新请求中出现的字段，
其它已保存字段、未知用户字段和其它插件配置保持不变。

成功时响应与 GET 相同的安全结构，并反映保存后的值。`source/api_available/restart_required`：

- AstrBot 在线且真实保存/热重载成功：
  `ok:true, api_available:true, source:"astrbot", restart_required:false`；
- AstrBot 不在线或在线接口不可用：配置先安全写入本地 0600 文件，
  返回 `ok:true, api_available:false, source:"local", restart_required:true`，
  信息中说明需重启 AstrBot；不会返回 500；
- 返回前会重新读取刚写入的本地配置以构造安全 values；短暂并发/半写读取会重试。
  若保存前发现稳定损坏 JSON，会先走 `.bak → cached → Studio mirror → schema defaults`
  恢复链，用户显式保存后用原子写修复主文件并保留 `.corrupt-*` 快照，不删除损坏证据。
- 校验失败：HTTP 200 + `ok:false`，`error` 给出中文原因，不写入任何字段。

字段校验（以 schema 为准）：

- 支持类型：`bool`、`int`、`float`（兼容 `number`）、`string`、`text`、`list`。
- `bool` 必须为 JSON true/false，不能用 0/1 代替。
- `int` 必须为 JSON 整数，bool 不算整数；`float` 接受 JSON 数字。
- `string` / `text` 必须为字符串，受 schema `max_length` 或服务端上限限制。
- `list` 必须为数组；若 schema 提供 `items`，逐项校验类型/选项，最多 256 项。
- schema 有 `options` 时，值必须属于允许选项。
- schema 有 `slider.min` / `slider.max`（或直接 min/max）时严格执行范围。
- schema 中不存在、或属于敏感 Key 类的字段会被拒绝。普通插件参数请求仍限制约 512 KiB；
  当 values 包含 `prompt_profiles` 时，允许受控上限 8 MiB（仅提示词 profiles/插件配置路径），
  其它 API 仍使用默认 2 MiB JSON 请求体上限，不存在全局无界请求。
- `plugin_id` 只允许字母、数字、点、下划线和连字符，防止路径穿越；一次只操作指定插件。

所有错误响应均为 HTTP 200 JSON（缺少本地访问令牌、JSON 语法错误等传输层鉴权/解析错误除外），
形如：

```json
{
  "ok": false,
  "plugin_id": "qqbot_enhancer",
  "schema": {},
  "values": {},
  "config_path": "…/qqbot_enhancer_config.json",
  "source": "none",
  "api_available": false,
  "restart_required": false,
  "message": "插件配置校验失败，未写入任何修改。",
  "error": "字段 reply_probability 不能大于 1。"
}
```

安全边界：API Key、token、password、secret 类字段不会出现在 `schema`、`values`、消息或错误中；
服务端不记录配置请求体。旧后端缺少这些端点时，前端应只降级该插件的参数编辑面板。

---

## 12. 多提示词 Profiles 与 active 切换

每个实例可以保存多个提示词 profile；同一时刻只有一个 `pinned/active`。旧实例没有该字段时，
由后端把现有 `prompt`（或插件 `system_prompt`）迁移成名为“默认提示词”的单 profile。

### 12.1 读取

```text
GET /api/instances/<instance_id>/prompts
```

需要 `X-QB-Token`。响应：

```json
{
  "ok": true,
  "prompts": [
    {
      "id": "default",
      "name": "默认提示词",
      "prompt": "第一行\n第二行",
      "pinned": true,
      "created_at": "2026-01-01T00:00:00+00:00",
      "updated_at": "2026-01-01T00:00:00+00:00"
    }
  ],
  "active_id": "default",
  "default_prompt_id": "default",
  "default_profile": {"id": "default", "name": "默认提示词", "prompt": "…", "pinned": true},
  "restart_required": false,
  "conversation_created": false,
  "conversation_pending": false,
  "plugin_sync_required": true,
  "prompt_plugin_sync_pending": true,
  "prompt_plugin_sync_pending_at": "2026-01-01T00:00:00+00:00",
  "message": "提示词列表已读取；本地插件状态已更新，但 AstrBot 热重载未确认，需重启后生效。",
  "error": ""
}
```

- profile prompt 保留内部/首尾换行和空白，长度上限 12000 字符；插件 parser 同样不改写 prompt 原文。
- profile 名称最长 80；最多 100 个 profile。恰好 12000/100 合法；12001/101 返回 `ok:false`，
  不截断、不删除、不保存，`error/validation_errors` 给出可操作原因。
- 总量统一常量：
  - 每 profile prompt 12000 字符；
  - 最多 100 个 profile；
  - prompt 总字符数最多 1,200,000；
  - 序列化 `prompt_profiles` JSON 最多 2,600,000 字符（含 id/name/时间戳/转义余量）；
  - Studio prompt API 和包含 `prompt_profiles` 的插件配置 API 请求体最多 8 MiB；
  - 其它插件配置请求仍约 512 KiB，其它普通 API 仍 2 MiB。
- 9×12000 与 100×12000（在总字符/JSON 上限内）均应保存、GET、插件配置同步成功；
  超过总 JSON 上限返回明确 `ok:false`/schema 长度错误或被 server 413 拒绝，不会被 API 接受后热重载再失败。
- 不存在/空/非法旧列表会自动迁移为一个默认 profile；不会删除最后一个 profile。
- GET 也会检查插件 state 是否与 Studio 一致；发现 `prompt_profiles`/active/`system_prompt`
  过期时自动修复并返回 `plugin_sync_required`、`warnings` 等状态，避免下一轮仍用旧 prompt。
- 只要实例 `deployed=true`，本地写入插件文件但 AstrBot API 不可用/热重载未确认时，会持久化
  `prompt_plugin_sync_pending=true` 与 `prompt_plugin_sync_pending_at`；连续 GET 都返回
  `restart_required=true`、`api_available=false` 和“需重启/热重载”警告，不会因第二次 GET 本地文件已匹配而消失。
- 只有 AstrBot API 可用，且实际 live 配置已匹配，或热重载保存明确成功后才清除 pending。
  未部署实例不会误报 `restart_required`。
- 本地插件文件写入失败时不会设置 pending 为“已同步”，会返回明确同步 warning；Studio store 仍原子写 0600。
- prompt/active 同时镜像到插件配置字段 `prompt_profiles`（JSON 字符串）、
  `prompt_profile_active_id` 和旧字段 `system_prompt`。

### 12.2 创建 / 更新 / 删除 / 激活

```text
POST /api/instances/<instance_id>/prompts
Content-Type: application/json
X-QB-Token: <token>
```

```json
{"action":"create","name":"角色A","prompt":"你是A\n保持换行"}
{"action":"update","profile_id":"p_xxx","name":"角色A2","prompt":"新提示词"}
{"action":"delete","profile_id":"p_xxx"}
{"action":"activate","profile_id":"p_xxx"}
```

响应在 GET 字段基础上增加：

- `conversation_created`: 后端当前始终为 `false`，因为 AstrBot conversation 只能由插件在下一轮
  消息中创建；不伪造“已创建”。
- `conversation_pending`: 兼容字段，当前恒为 `false`；activate 不再触发待创建会话。
- `restart_required`: 插件配置未能在线热重载时的明确提示；本地状态仍已原子保存。
- `message` / `warning` / `warnings` / `error`。

行为：

- profile 是“新建会话模板库”；pinned profile 是新建会话默认模板库的唯一默认项。
- GET/POST 返回的 `prompts` 始终把 pinned profile 排在第一，其余 profile 保持稳定顺序；
  同一实例始终最多一个 `pinned=true`。`active_id` 保留为兼容字段，等价于 pinned/default id。
- `activate` 只把目标 profile 设为唯一 pinned，更新 `config.prompt` 镜像与插件 state；
  **不会**创建/切换/重置任何当前 AstrBot conversation。
- `conversation_created` 恒为 false；`conversation_pending` 保留为兼容字段但恒为 false，
  “激活后下一轮强制新会话”的旧语义已删除。插件/前端如需新建 conversation，由插件在实际
  新建会话时读取 pinned/default profile 信息执行，不由 backend API 伪造。
- API 额外返回 `default_prompt_id` 和 `default_profile`，供 conversation 创建/切换逻辑使用。
- AstrBot 不在线或插件配置 API 不可用时：返回 `restart_required=true`/warning，本地配置仍保存，不返回 500。
- 删除 active profile 时自动选择剩余的第一个 profile；删除最后一个返回 `ok:false`。
- 写入使用原子 JSON + 0600；不记录 prompt、Key、用户 ID 到日志。
- `/新会话` 同名检测和 `/会话` 切换不再静默取第一个同名会话；存在多个同名会话时明确拒绝自动切换，
  并显示短 ID 提示用户先改名。

提示词优先级（direct 与 astrbot 一致）：

1. `group_personas.groups[群号]` 精确群映射最高；
2. 当前 active prompt profile；
3. `group_personas.default`；
4. 旧 `system_prompt` fallback。

因此群专属人格不会被全局 active profile 覆盖；未知群没有 exact 映射时会使用 active profile；
只有未配置 active profile 时才使用 group default/legacy。
