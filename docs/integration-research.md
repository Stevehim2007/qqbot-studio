> **v2 实现备注：** v2 默认将管理服务及机器人宿主机端口绑定到 `0.0.0.0`，
> 每个机器人实例使用独立的 Compose 项目、容器名、网络和端口。
> SnowLuma ↔ AstrBot 仍通过 `ws://astrbot:6199/ws` 连接，但只存在于各自实例的 Docker 网络内。

# 集成调研结论（AstrBot v4.29.x + SnowLuma v2.2.x）

调研时间：2026-10-28。以下结论用于本仓库的自动部署生成器；实际字段以对应版本源码为准。
本项目不修改或重新分发 AstrBot / SnowLuma 的镜像或二进制文件，只调用官方发布物。

## 1. AstrBot

### 1.1 Docker 与端口

- 官方镜像：`soulter/astrbot:latest`
- 数据目录：容器内 `/AstrBot/data`，宿主机必须持久化。
- AstrBot WebUI：容器端口 `6185`。
- OneBot v11 反向 WebSocket：AstrBot 作为服务端，默认配置端口 `6199`，连接路径 `/ws`。
- 参考：`compose.yml`、`Dockerfile` 与 `astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py`。

### 1.2 `data/cmd_config.json` 的 aiocqhttp 配置

`platform` 是列表；OneBot v11 适配器的关键字段：

```json
{
  "id": "qqbot-onebot",
  "type": "aiocqhttp",
  "enable": true,
  "ws_reverse_host": "0.0.0.0",
  "ws_reverse_port": 6199,
  "ws_reverse_token": "<RANDOM_TOKEN>"
}
```

适配器使用 `aiocqhttp.CQHttp(use_ws_reverse=True)`，因此协议端应作为 WebSocket 客户端连接
`ws://astrbot:6199/ws`。在 SnowLuma 与 AstrBot 同处一个 Compose 网络时，主机名必须是服务名
`astrbot`，不能是 `127.0.0.1`。

### 1.3 DeepSeek / OpenAI-compatible provider

AstrBot 的 `provider` 列表支持 DeepSeek 的 OpenAI Chat Completions 实现。本次固定使用 DeepSeek
当前官方模型名 `deepseek-flash`：

```json
{
  "id": "deepseek-flash",
  "provider": "deepseek",
  "type": "openai_chat_completion",
  "provider_type": "chat_completion",
  "enable": true,
  "key": ["<DEEPSEEK_API_KEY>"],
  "api_base": "https://api.deepseek.com/v1",
  "model": "deepseek-flash",
  "timeout": 120,
  "proxy": "",
  "custom_headers": {}
}
```

- `key` 是列表。
- AstrBot v4.29 的 `provider_settings` 默认配置中没有 `default_provider_id`；配置完整性检查会移除这个未知键。
  当前版本应以 `agent_runner.config.model.provider_id = "deepseek-flash"` 为准。
- DeepSeek 官方模型列表 API：`GET https://api.deepseek.com/models`，使用
  `Authorization: Bearer <API_KEY>`。官方当前文档列出的模型包含 `deepseek-flash` 和 `deepseek-v4-pro`。
- 来源：
  - <https://raw.githubusercontent.com/AstrBotDevs/AstrBot/master/astrbot/core/config/default.py>
  - <https://api-docs.deepseek.com/quick_start/pricing>
  - <https://api-docs.deepseek.com/api/list-models>

### 1.4 人格 / 提示词

AstrBot v4 的人格提示词存储在数据库中，不直接写 `provider_settings.prompt_prefix`（后者是用户
输入前缀）。生成器采用以下方式：

1. 在 `agent_runner.config.persona.persona_id` 中写死人格 ID，例如 `qqbot-assistant`；
2. AstrBot 启动后调用 Dashboard API 创建或更新该人格的 `system_prompt`。

`agent_runner.config` 关键结构（其余压缩/工具字段由 AstrBot 默认值补齐）：

```json
{
  "runner_type": "local",
  "config": {
    "model": {
      "provider_id": "deepseek-flash",
      "fallback_provider_ids": [],
      "request_max_retries": 5
    },
    "persona": {
      "persona_id": "qqbot-assistant",
      "safety_mode": true,
      "safety_mode_strategy": "system_prompt"
    }
  }
}
```

### 1.5 Dashboard 初始密码

AstrBot 4.29 在配置文件没有可用密码时会生成随机强密码，并只在启动日志打印一次；如果检测到
缺失的 `pbkdf2_password` / `password`，会覆盖配置。自动部署生成器可以预置以下字段，避免随机密码：

```json
"dashboard": {
  "enable": true,
  "username": "admin",
  "password": "<MD5_OF_RAW_PASSWORD>",
  "pbkdf2_password": "pbkdf2_sha256$600000$<SALT_HEX>$<DIGEST_HEX>",
  "password_storage_upgraded": true,
  "password_change_required": false,
  "jwt_secret": "<RANDOM_SECRET_STRING>",
  "host": "0.0.0.0",
  "port": 6185
}
```

语义（来自 `astrbot/core/config/astrbot_config.py`、`auth_service.py`、`auth_password.py`）：

- `password` 是 MD5(明文) 的兼容字段；登录校验仍要求输入明文。
- `pbkdf2_password` 是新的 PBKDF2-HMAC-SHA256 存储格式；存在时优先使用。
- `password_storage_upgraded=true` 表示已使用 PBKDF2 存储。
- `password_change_required=false` 可避免首次登录强制改密。
- `jwt_secret` 为空时 AstrBot 会自行生成；预置可保证自动登录稳定。本项目使用 URL-safe 随机字符串，不要求必须为 hex。

### 1.6 Dashboard API（用于自动写入人格）

- 登录：
  - `POST /api/v1/auth/login`
  - 旧版兜底：`POST /api/auth/login`
  - JSON：`{"username":"admin","password":"<raw>"}`
  - 成功响应 `data.token` 即 Bearer JWT。
- 创建人格：
  - `POST /api/v1/personas`
  - JSON：`{"persona_id":"qqbot-assistant","system_prompt":"..."}`
  - 已存在时服务端返回 400（`Persona ... already exists`）。
- 更新人格：
  - `PUT /api/v1/personas/by-id`
  - JSON：`{"persona_id":"qqbot-assistant","system_prompt":"..."}`
  - 旧版兜底：`POST /api/persona/create`、`POST /api/persona/update`。
- 请求头：`Authorization: Bearer <JWT>`。
- 来源：
  - <https://raw.githubusercontent.com/AstrBotDevs/AstrBot/master/astrbot/dashboard/api/auth.py>
  - <https://raw.githubusercontent.com/AstrBotDevs/AstrBot/master/astrbot/dashboard/api/personas.py>
  - <https://raw.githubusercontent.com/AstrBotDevs/AstrBot/master/astrbot/dashboard/services/persona_service.py>

## 2. SnowLuma

### 2.1 Docker 运行参数

- 镜像：`motricseven7/snowluma:latest`（官方文档当前 tag 示例；本项目可在 `.env` 覆盖）。
- 端口：
  - `6081`：noVNC（浏览器远程桌面，用于 QQ 扫码）
  - `5099`：SnowLuma WebUI
  - `3000`：OneBot HTTP（默认）
  - `3001`：OneBot WebSocket（默认）
- 必需容器参数：
  - `--cap-add=SYS_PTRACE`
  - `--security-opt seccomp=unconfined`
  - `--shm-size=1g`
- 其他重要环境变量：
  - `VNC_PASSWD`：远程桌面密码；留空则首次启动随机生成并只在日志打印一次。
  - `SNOWLUMA_UID` / `SNOWLUMA_GID`：容器运行用户，应与宿主机目录属主匹配。
  - `SNOWLUMA_WEBUI_HOST=0.0.0.0`
  - `SNOWLUMA_HOOK_AUTOLOAD=1`
- 持久化目录（官方文档）：
  - `/app/data`：SnowLuma 配置、数据库等；`config/onebot.json` 与 `config/onebot_<uin>.json` 均在此。
  - `/app/.config`：QQ 客户端配置。
  - `/app/.local/share`：QQ 登录态与缓存。
- 来源：
  - <https://snowluma.github.io/zh/docs/guide/deploy/docker>
  - <https://raw.githubusercontent.com/SnowLuma/SnowLuma/main/packages/onebot/src/config.ts>
  - <https://raw.githubusercontent.com/SnowLuma/SnowLuma/main/packages/onebot/src/types.ts>

### 2.2 OneBot v2 配置与反向 WebSocket

SnowLuma 当前配置分层在 `networks` 下，包含 `httpServers`、`httpClients`、`wsServers`、`wsClients`。
连接 AstrBot 使用 `wsClients`（SnowLuma 主动连出去）：

```json
{
  "networks": {
    "httpServers": [],
    "httpClients": [],
    "wsServers": [],
    "wsClients": [
      {
        "name": "astrbot",
        "enabled": true,
        "url": "ws://astrbot:6199/ws",
        "role": "Universal",
        "accessToken": "<SAME_TOKEN_AS_ASTRBOT_WS_REVERSE_TOKEN>",
        "messageFormat": "array",
        "reportSelfMessage": false,
        "reconnectIntervalMs": 5000
      }
    ]
  },
  "statusCommand": {
    "enabled": true,
    "swallow": false,
    "cooldownSeconds": 5,
    "trigger": "#sl"
  },
  "historySync": {"enabled": false}
}
```

说明：

- `role` 合法值为 `Api`、`Event`、`Universal`。
- `messageFormat` 为 `array` 或 `string`；AstrBot 适配 OneBot v11 阵列消息，使用 `array`。
- `accessToken` 必须与 AstrBot `ws_reverse_token` 一致；OneBot 鉴权为
  `Authorization: Bearer <token>` 或 `?access_token=<token>`。
- SnowLuma 首次运行会自动生成 `config/onebot.json` 和账号级 `onebot_<uin>.json`。账号级文件在 QQ 登录后生成；
  自动部署器可在 QQ 登录前预写全局 `onebot.json`，SnowLuma 会将它作为全局默认继承。
- 本项目只使用反向 WebSocket；生成的全局配置会把未使用的 HTTP/WS 正向监听器设为 `enabled:false`，
  并把唯一启用的 `wsClients` 指向 `ws://astrbot:6199/ws`。
- 来源：<https://snowluma.github.io/zh/docs/guide/connect-bot>、
  <https://snowluma.github.io/zh/docs/guide/configuration>

### 2.3 首次扫码登录与验证

SnowLuma 在 Linux Docker 中运行 Linux QQ + Xvfb + noVNC。

1. 打开 `http://127.0.0.1:6081/`，输入 `VNC_PASSWD`（或日志中一次性生成的远程桌面密码）。
2. 在远程桌面的 QQ 官方登录界面选择扫码。
3. 用手机 QQ 扫码。
4. 若 QQ 要求设备验证、短信/邮箱验证、滑块或安全验证，必须在 noVNC 中的 QQ 官方界面完成。
5. 登录态保存在 `/app/.local/share` 对应的持久化目录中。

SnowLuma WebUI 首次启动会在容器日志打印一次性临时密码，常见格式：

```text
临时密码: xxxxxxxx
initial credentials: user=admin password=xxxxxxxx
```

本项目用 `docker compose logs snowluma` 解析类似文本并保存到本地 `secrets.json`。若日志中尚未出现，
可在完成页刷新状态或查看 SnowLuma 日志。

**限制（重要）**：QQ 密码登录、验证码代填、滑块破解、设备验证绕过均不在本项目实现范围内。
现代 QQ 在服务器/容器环境通常要求扫码或额外验证；本工具只负责将官方客户端呈现给用户，由用户完成官方验证。
若出现风控/封禁提示，应停止自动化尝试并遵守 QQ 用户协议。

## 3. 与本项目实现的对应关系

| 组件 | 本项目生成/操作 |
|---|---|
| Compose | 生成 `compose.yml` + `.env`，固定项目名 `qqbot-studio` |
| AstrBot | 写入 `data/astrbot/cmd_config.json`，预置 dashboard 登录、provider、platform、agent_runner |
| SnowLuma | 写入 `data/snowluma/config/onebot.json`，配置 `wsClients` 指向 AstrBot |
| OneBot token | 随机生成并同时写入 AstrBot `ws_reverse_token` 与 SnowLuma `wsClients[].accessToken` |
| DeepSeek | 调用 `/models` 预检 Key；Key 仅存入 AstrBot 配置（文件权限 600） |
| 人格 | AstrBot 启动后通过 Dashboard API 创建/更新 `qqbot-assistant` 的 system_prompt |
| QQ 登录 | 不收集密码；打开 noVNC，由用户扫码并完成官方验证 |

## 4. 已知不确定性与后续验证建议

- Docker 镜像 `latest` 可能随 AstrBot/SnowLuma 升级发生变化；本项目生成字段基于上述版本源码，若升级后失败，
  应优先查看 dashboard API 响应和 SnowLuma 配置加载报错。
- SnowLuma WebUI 临时密码的具体日志格式可能随版本微调；解析使用中英文多模式并做容错，但首次部署后仍建议
  人工确认日志。
- AstrBot WebUI 首次启动如果无法下载 WebUI 静态资源，需要网络；本工具未内置 Dashboard 静态文件，只使用官方镜像。
- 机器上其他容器若占用名称 `qqbot-astrbot` / `qqbot-snowluma` 或固定内网端口，可能需要先清理。
- 本项目不绕过 QQ 安全验证，也不支持自动输入 QQ 密码；这是产品和安全边界，不是未完成缺陷。
