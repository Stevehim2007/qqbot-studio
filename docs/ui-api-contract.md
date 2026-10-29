> **历史文档：** 本文件对应 v1 单实例界面。v2 的多实例、镜像节点、任务恢复和控制台接口见 [`ui-api-contract-v2.md`](ui-api-contract-v2.md)。

# QQ Bot Studio 前端 / 本地服务 API 契约

本文档记录 `qqbot_studio/static/index.html`、`styles.css`、`app.js` 与 Lead 提供的 Python 标准库本地服务之间的接口约定。前端为纯静态文件，无构建步骤、无第三方 CDN。

## 1. 访问令牌注入（HTML → 前端）

服务端必须在返回 `index.html` 时，把下面的精确占位符替换为本次本地会话令牌：

```html
<script>window.QB_TOKEN = "{{TOKEN}}";</script>
```

要求：

- 请对令牌做 JavaScript 字符串转义（例如使用 `json.dumps(token)` 的结果替换整个 `"{{TOKEN}}"`），避免引号导致脚本损坏。
- 前端的所有修改请求 (`POST`) 都发送 `X-QB-Token: <token>`。
- 前端在存在令牌时也会给 GET 请求附加该请求头；服务端对所有 `/api/*` GET 接口同样校验令牌，缺失或错误返回 403。
- 前端不把令牌写入 localStorage、sessionStorage 或页面可见文本。
- 若服务端没有替换占位符，前端会以空令牌发送 POST，服务端应返回 `401/403`，前端会显示“访问令牌无效或已过期”。

## 2. 接口清单

所有 API 响应均为 JSON；不支持的 HTTP 方法返回 `405` JSON，而非 HTML。前端不会自行决定或拼接端口，所有 WebUI / noVNC 地址都由 `/api/state` 返回。

### GET `/api/state`

```json
{
  "app_version": "1.0.0",
  "requirements": {
    "docker_installed": true,
    "docker_running": true,
    "compose_installed": true,
    "permission_ok": true,
    "os": "Ubuntu 24.04"
  },
  "deployment": {
    "deployed": false,
    "running": false,
    "qq_account": "123456789",
    "model": "deepseek-flash",
    "prompt": "机器人提示词",
    "urls": {
      "astrbot_webui": "http://127.0.0.1:6185",
      "snowluma_webui": "http://127.0.0.1:5099",
      "novnc": "http://127.0.0.1:6081"
    },
    "credentials": {
      "astrbot_username": "admin",
      "astrbot_password": "本地随机密码",
      "snowluma_password": "本地临时密码",
      "vnc_password": "本地 VNC 密码"
    },
    "onebot_connected": false,
    "qq_logged_in": false
  },
  "notice": "可选提示文本"
}
```

前端处理规则：

- 页面加载和用户点击“重新检测 / 刷新连接状态”时都会自动 `GET /api/state`（携带令牌），从而恢复已部署状态和完成页。
- 字段缺失、对象缺失、值为 `null` 时不会导致页面崩溃；缺失的部署状态显示为未知或未部署，缺失的凭据显示为“未提供，请刷新状态”。
- 布尔字段建议返回 JSON boolean。前端同时容忍 `1/0`、`"true"/"false"`、`"running"/"stopped"` 等字符串形式。
- `urls.*` 必须是绝对 `http://` 或 `https://` 地址；否则文案仍会显示，但“一键打开”按钮会禁用，避免打开不安全协议。
- `notice` 可选，为字符串；仅作普通文本显示，不解释 HTML。

### POST `/api/test-key`

请求：

```json
{ "deepseek_api_key": "sk-..." }
```

响应：

```json
{ "ok": true, "message": "Key 可用" }
```

`ok` 不为 `true` 时，前端显示服务端 `message` 为校验失败。前端不保存该 Key。

### POST `/api/deploy`

请求体（JSON，非表单编码）：

```json
{
  "qq_account": "123456789",
  "deepseek_api_key": "sk-...",
  "prompt": "提示词文本",
  "use_mirror": true,
  "accept_risk": true
}
```

成功响应：

```json
{ "ok": true, "job_id": "job-123" }
```

失败响应：

```json
{ "ok": false, "message": "失败原因" }
```

`job_id` 可以是字符串或数字，前端会统一转成字符串。若 `ok: true` 但没有 `job_id`，前端视为错误，不进入进度页。

### GET `/api/job/<id>`

```json
{
  "status": "running",
  "step": "正在拉取镜像",
  "progress": 42,
  "message": "当前步骤说明",
  "logs": ["日志第一行", "日志第二行"],
  "error": ""
}
```

兼容规则：

- `status` 识别 `success/succeeded/complete/completed/done` 为成功；`error/failed/failure/cancelled/canceled` 为失败；`running/pending/queued/in_progress/processing` 为进行中。
- `progress` 按 `0–100` 百分比解释；缺失按 `0` 处理。
- `logs` 可以是字符串，也可以是字符串数组；数组元素若为对象，会优先读取 `message` / `text` / `line`。
- 前端每 1.2 秒轮询一次；连续 3 次网络失败后显示“继续获取进度”，不会自动重复创建部署。
- 为支持刷新后继续观察同一任务，前端仅把 **job id** 保存到 `sessionStorage` 的 `qb_active_job_id`；不使用 localStorage，不保存 API Key、密码或令牌。

### POST `/api/action`

```json
{ "action": "start" }
```

`action` 仅使用 `start`、`stop`、`restart`。响应：

```json
{ "ok": true, "message": "启动指令已发送" }
```

`ok: false` 时显示服务端 `message`。操作完成后前端会重新读取 `/api/state`。

### POST `/api/persona`

```json
{ "prompt": "新的提示词" }
```

响应：

```json
{ "ok": true, "message": "提示词已保存" }
```

成功后前端刷新 `/api/state`，并更新完成页中的提示词预览。

### GET `/api/logs?service=astrbot&lines=200`

`service` 仅取 `astrbot` 或 `snowluma`。

```json
{ "ok": true, "logs": "经本地敏感值脱敏后的日志文本" }
```

`logs` 同样兼容字符串或数组。前端只显示文本，不执行日志内容。

## 3. 契约偏差与前端补充约定

核心 API 路径、字段、POST JSON 和 `X-QB-Token` 请求头均按任务契约实现，没有新增必需接口。以下是前端为容错而做出的非破坏性补充：

1. **刷新后继续任务**：使用 `sessionStorage` 保存 `job_id`。这是前端本地行为，不属于后端 API 变更。
2. **GET 也携带令牌**：服务端对 `/api/*` GET 也强制校验令牌；POST 必须携带。
3. **未知字段容错**：状态与任务响应缺字段时使用占位文案并保持页面可用，不会抛错中断。
4. **日志类型容错**：`logs` 接受字符串或数组。
5. **任务状态容错**：除 `running/success/error` 外，额外识别常见同义词。
6. **不安全 URL 禁用**：前端不会给非 `http/https` 地址生成可点击链接，也不会自己补端口。
7. **API Key 生命周期**：仅保存在当前输入框内存中；部署成功后会主动清空输入框。重新加载页面不会恢复该密钥。

## 4. 安全边界

- 前端没有 QQ 密码输入框，也不会收集、保存或提交 QQ 密码。
- 前端不包含二维码识别、验证码代填、滑块破解或任何绕过 QQ 安全验证的逻辑。
- 设备验证、短信验证、滑块或安全验证必须由用户在 noVNC 中的 QQ 官方界面完成。
- 第三方协议端账号风控/封禁风险由使用者自行判断并承担；UI 要求使用小号并遵守 QQ 协议与当地法律。
