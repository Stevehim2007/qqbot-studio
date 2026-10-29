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
