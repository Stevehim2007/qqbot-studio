# QQ 官方版 E2E 验证说明

> 适用范围：当前仅支持 **QQ 官方机器人开放平台 + AstrBot + DeepSeek** 的部署路径。
> 本文描述自动化容器级 smoke test 覆盖范围；历史 SnowLuma/noVNC/OneBot 版本的 E2E
> 结果已不再适用于当前代码。

## 1. 测试边界

E2E 脚本位于 [`tests/e2e/e2e_smoke.py`](../tests/e2e/e2e_smoke.py)，不在
`unittest discover` 中运行。它会拉取镜像、创建临时容器、打开临时宿主端口，因此只应在
测试机执行。

脚本使用以下**假值**，不读取真实凭据：

- DeepSeek Key：`sk-e2e-invalid-not-a-real-key`
- QQ 官方 AppID：`10001`
- QQ 官方 AppSecret：`e2e-official-secret-not-real`

真实 QQ 开放平台授权、AppID/AppSecret 有效性和真实群消息收发不在本自动化的断言范围内。

## 2. 执行方式

```bash
python3 tests/e2e/e2e_smoke.py --timeout 900
```

常用参数：

- `--keep`：保留临时数据目录，便于排错；目录含敏感配置，调试后应删除。
- `--exec-astrbot-bridge`：Docker Desktop 宿主端口发布失效时，经 `docker exec` 访问 AstrBot。
- `--artifacts-dir`：指定脱敏结果目录。

脚本不会使用真实 QQ 账号、密码、短信/设备验证或扫码流程。

## 3. 自动检查项

| 检查 | 预期 |
|---|---|
| Docker / Compose | 预检通过 |
| 管理服务 | `/healthz` 与 `/console` 可访问，API 受 `X-QB-Token` 保护 |
| 部署任务 | `POST /api/deploy` 使用 `official_appid`/`official_secret`，job 成功 |
| 部署产物 | 仅一个 AstrBot 容器、一个 `ASTRBOT_WEBUI_PORT` |
| AstrBot 平台配置 | `cmd_config.json.platform` 仅包含 `qq_official`，AppID/Secret 正确 |
| 旧版字段 | 生成产物中不存在 `snowluma` / `novnc` / `onebot` 字段或文件 |
| 容器 | AstrBot 容器 running |
| HTTP | `astrbot_webui` 可访问；宿主端口不可用时改容器内 `127.0.0.1:6185` 验证 |
| AstrBot API | 登录成功，Persona 列表包含测试 Persona |
| 管理功能 | 插件 schema、资源统计、实例列表、state API 可用 |
| 隐私 | `/api/state` 和脱敏 job 记录不包含测试 Key/Secret |

端口限制在 `5000–10000`，每个实例只发布一个 AstrBot WebUI 宿主端口。

## 4. 结果与已知边界

- 当前代码任务明确禁止实际部署，因此本轮**未执行**真实容器 E2E，不能宣称 E2E PASS。
- 真实 QQ 官方平台联调需要有效的 AppID/AppSecret、正确的开放平台权限和测试群配置，
  必须由用户在独立测试环境完成。
- 不自动验证真实 QQ 群/私聊消息、图片/音频收发；这些依赖真实平台授权和频控。
- 容器健康检查若为 `unhealthy` 而 WebUI/API 返回 200，以 API 200 为主判据，并记录 warning。

## 5. 产物位置

结果默认写入 `tests/e2e/artifacts/e2e-<UTC>-<suffix>/`：

- `result.json`：检查项、端口、容器名、状态、告警和错误；
- `deployment-job.redacted.json`：脱敏后的部署任务；
- `server.log`：管理服务日志；
- `driver.log`：驱动日志。

脚本会对已知 secret 值做脱敏。不要提交含真实凭据的临时目录或日志。
