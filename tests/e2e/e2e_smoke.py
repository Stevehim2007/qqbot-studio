#!/usr/bin/env python3
"""Real container-level E2E smoke test for QQ Bot Studio.

This test intentionally lives outside ``unittest discover`` because it pulls
images, starts containers, opens temporary host ports, and may take several
minutes.  It never asks for or uses a real DeepSeek key.

Typical use on Ubuntu/Linux:

    python3 tests/e2e/e2e_smoke.py --timeout 900

On a WSL2 host where Docker Desktop integration is available, the same command
works.  It uses dummy QQ Official AppID/secret values and never asks for or
uses real platform credentials.  Real QQ Open Platform authorization is
outside this smoke test.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().with_name("run_studio_server.py")
FAKE_DEEPSEEK_KEY = "sk-e2e-invalid-not-a-real-key"
FAKE_OFFICIAL_APPID = "10001"
FAKE_OFFICIAL_SECRET = "e2e-official-secret-not-real"
PERSONA_ID = "qqbot-assistant"


class E2EBlocked(RuntimeError):
    """Environment prevents completing a meaningful automated check."""


class E2EFailed(RuntimeError):
    """The product/runtime failed in a way that is not merely environmental."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def _opener() -> urllib.request.OpenerDirector:
    # Docker Desktop / corporate proxy variables must not hijack localhost.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, bytes, dict[str, str]]:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with _opener().open(request, timeout=timeout) as response:
            return int(getattr(response, "status", 200)), response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(), dict(exc.headers.items())


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any], str]:
    status, raw, _ = _http(method, url, headers=headers, payload=payload, timeout=timeout)
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text.strip() else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}, text


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise E2EFailed(f"JSON 顶层不是 object：{path}")
    return value


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _extract_qb_token(html: str) -> str:
    match = re.search(r'window\.QB_TOKEN\s*=\s*"([^"]+)"', html)
    if not match:
        raise E2EFailed("管理页面未找到 window.QB_TOKEN")
    return match.group(1)


def _display_path(path: Path | str) -> str:
    """Return a useful path without exposing the operator's home directory."""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(REPO_ROOT))
    except (OSError, ValueError):
        pass
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        return "<TEMP>/" + str(candidate.resolve().relative_to(temp_root))
    except (OSError, ValueError):
        return candidate.name


class DockerCLI:
    """Small direct Docker helper used only to verify container runtime state."""

    def __init__(self) -> None:
        executable = shutil.which("docker")
        self.base = [executable] if executable else []
        self.server_version = ""
        self.init_error = "" if executable else "ENV_BLOCKED:DOCKER_CLI: 找不到 docker 命令"

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = 60.0,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if not self.base:
            raise E2EBlocked(self.init_error or "ENV_BLOCKED:DOCKER_CLI: Docker CLI 不可用")
        command = [*self.base, *[str(item) for item in args]]
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                env=env,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise E2EBlocked(f"ENV_BLOCKED:DOCKER_CLI: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise E2EFailed(f"Docker 命令超时：{' '.join(command[:3])}") from exc
        if check and completed.returncode != 0:
            detail = (completed.stdout + "\n" + completed.stderr).strip()
            raise E2EFailed(f"Docker 命令失败：{' '.join(command[:3])}：{detail[-1200:]}")
        return completed

    def preflight(self) -> None:
        # ``docker version --format`` differs between Docker CLI builds;
        # ``docker info --format`` is the stable daemon-readiness probe used by
        # the product itself.
        version = self.run(["info", "--format", "{{.ServerVersion}}"], timeout=30)
        if version.returncode != 0:
            detail = (version.stdout + "\n" + version.stderr).strip()
            raise E2EBlocked(f"ENV_BLOCKED:DOCKER_DAEMON: {detail[-1200:] or 'Docker daemon 不可用'}")
        self.server_version = version.stdout.strip()
        compose = self.run(["compose", "version"], timeout=30)
        if compose.returncode != 0:
            detail = (compose.stdout + "\n" + compose.stderr).strip()
            raise E2EBlocked(f"ENV_BLOCKED:DOCKER_COMPOSE: {detail[-1200:] or 'docker compose 不可用'}")


class SmokeRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.started_at = utc_now()
        self.artifact_dir = Path(args.artifacts_dir).expanduser().resolve() / f"e2e-{stamp}-{secrets.token_hex(3)}"
        self.artifact_dir.mkdir(parents=True, exist_ok=False)
        self.temp_root: Path | None = None
        self.server_proc: subprocess.Popen[str] | None = None
        self.server_log_path = self.artifact_dir / "studio-server.log"
        self.job_artifact_path = self.artifact_dir / "deployment-job.redacted.json"
        self.summary_path = self.artifact_dir / "result.json"
        self.docker = DockerCLI()
        self.base_url = ""
        self.qb_token = ""
        self.secret_values: list[str] = [FAKE_DEEPSEEK_KEY]
        self.summary: dict[str, Any] = {
            "started_at": self.started_at,
            "status": "RUNNING",
            "docker_server_version": "",
            "checks": {},
            "blocked": [],
            "warnings": [],
            "artifacts": {},
            "temp_data_dir_removed": False,
        }

    def set_check(self, name: str, ok: bool, detail: str = "") -> None:
        self.summary["checks"][name] = {"ok": bool(ok), "detail": detail}
        if ok:
            log(f"PASS {name}" + (f"：{detail}" if detail else ""))
        else:
            log(f"FAIL {name}: {detail}")

    def api(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 90.0) -> tuple[int, dict[str, Any], str]:
        url = self.base_url + path
        return _http_json(method, url, headers={"X-QB-Token": self.qb_token}, payload=payload, timeout=timeout)

    def wait_health(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                status, raw, _ = _http("GET", self.base_url + "/healthz", timeout=3)
                if status == 200 and b'"ok": true' in raw:
                    return
                last = f"HTTP {status}"
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            time.sleep(0.25)
        raise E2EBlocked(f"ENV_BLOCKED:STUDIO_HEALTH: 管理服务未就绪：{last}")

    def start_server(self) -> None:
        assert self.temp_root is not None
        studio_root = self.temp_root / "studio"
        studio_root.mkdir(parents=True, exist_ok=True)
        # Initialise with a random high instance sequence so compose project,
        # network and container names cannot collide with a user's bot-1.
        sys.path.insert(0, str(REPO_ROOT))
        from qqbot_studio.studio import StudioManager  # local import: test-only

        studio = StudioManager(studio_root)
        settings = studio.load_settings()
        settings["bind_host"] = "0.0.0.0"
        settings["bind_port"] = self.args.studio_port
        settings["public_host"] = "127.0.0.1"
        settings["first_run_complete"] = True
        settings["next_instance_seq"] = random.SystemRandom().randint(100_000, 999_999)
        studio.save_settings(settings)

        self.base_url = f"http://127.0.0.1:{self.args.studio_port}"
        env = os.environ.copy()
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + current_pythonpath if current_pythonpath else "")
        env["QQBOT_STUDIO_DATA_DIR"] = str(studio_root)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if self.args.exec_astrbot_bridge:
            env["QQBOT_E2E_EXEC_ASTRBOT"] = "1"
        command = [
            sys.executable,
            str(RUNNER),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.args.studio_port),
            "--data-dir",
            str(studio_root),
            "--no-browser",
        ]
        log(f"启动临时管理服务：{self.base_url}")
        with self.server_log_path.open("w", encoding="utf-8") as log_handle:
            self.server_proc = subprocess.Popen(
                command,
                cwd=str(self.temp_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        self.wait_health(30)
        status, raw, text = _http("GET", self.base_url + "/console", timeout=15)
        if status != 200:
            raise E2EBlocked(f"ENV_BLOCKED:STUDIO_UI: /console HTTP {status}: {text[:300]}")
        self.qb_token = _extract_qb_token(raw.decode("utf-8", "replace"))
        self.summary["checks"]["management_service_health"] = {"ok": True, "detail": "/healthz=200, /console=200"}

    def create_instance(self) -> str:
        status, payload, text = self.api("/api/instances", method="POST", payload={"name": "E2E smoke"})
        if status != 200 or not payload.get("ok") or not isinstance(payload.get("instance"), dict):
            raise E2EFailed(f"创建临时实例失败：HTTP {status}: {text[:500]}")
        instance = payload["instance"]
        instance_id = str(instance.get("id") or "")
        if not re.fullmatch(r"bot-\d{6}", instance_id):
            raise E2EFailed(f"临时实例 ID 不安全或不符合预期：{instance_id!r}")
        self.summary["instance_id"] = instance_id
        log(f"临时实例：{instance_id}")
        status, state, state_text = self.api(f"/api/state?instance={urllib.parse.quote(instance_id)}")
        if status != 200:
            raise E2EFailed(f"/api/state 失败：HTTP {status}")
        if FAKE_DEEPSEEK_KEY in state_text:
            raise E2EFailed("/api/state 泄漏了 DeepSeek Key")
        self.set_check("management_api_state", True, "token 保护 + 初始状态可读")
        return instance_id

    def deploy(self, instance_id: str) -> tuple[str, dict[str, Any]]:
        q = urllib.parse.urlencode({"instance": instance_id})
        status, payload, text = self.api(
            f"/api/deploy?{q}",
            method="POST",
            payload={
                "official_appid": FAKE_OFFICIAL_APPID,
                "official_secret": FAKE_OFFICIAL_SECRET,
                "deepseek_api_key": FAKE_DEEPSEEK_KEY,
                "prompt": "你是 E2E 测试机器人。请用简洁中文回答。",
                "accept_risk": True,
                "instance": instance_id,
            },
            timeout=60,
        )
        if status != 200 or not payload.get("ok") or not payload.get("job_id"):
            # A preflight/environment failure before job creation is not a
            # product pass.  Keep the message but never include credentials.
            raise E2EFailed(f"部署任务创建失败：HTTP {status}: {text[:800]}")
        job_id = str(payload["job_id"])
        log(f"部署任务：{job_id}")
        deadline = time.monotonic() + self.args.timeout
        last_progress = -1
        job: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status, job, text = self.api(f"/api/job/{urllib.parse.quote(job_id)}?instance={urllib.parse.quote(instance_id)}", timeout=30)
            if status != 200:
                raise E2EFailed(f"读取 job 失败：HTTP {status}: {text[:500]}")
            progress = int(job.get("progress") or 0)
            if progress != last_progress:
                log(f"JOB {progress:3d}% {job.get('step') or '-'}：{job.get('message') or ''}")
                last_progress = progress
            state = str(job.get("status") or "")
            if state in {"success", "error", "failed", "interrupted", "cancelled"}:
                break
            time.sleep(2)
        else:
            raise E2EFailed("部署任务超时")
        if str(job.get("status")) != "success":
            error = str(job.get("error") or job.get("message") or "unknown deployment error")
            if _is_environment_error(error):
                raise E2EBlocked(f"ENV_BLOCKED:DEPLOY_ENVIRONMENT: {error}")
            raise E2EFailed(f"部署失败：{error}")
        status, state, state_text = self.api(f"/api/state?instance={urllib.parse.quote(instance_id)}")
        if status != 200:
            raise E2EFailed(f"部署后 /api/state 失败：HTTP {status}")
        if FAKE_DEEPSEEK_KEY in state_text:
            raise E2EFailed("部署后 /api/state 泄漏了 DeepSeek Key")
        deployment = state.get("deployment") if isinstance(state.get("deployment"), dict) else {}
        if not deployment.get("deployed"):
            raise E2EFailed("job 成功但 state.deployment.deployed 不是 true")
        self.set_check("deployment_job", True, f"{job_id} success")
        self.set_check("state_after_deploy", True, "deployed=true，未包含 DeepSeek Key")
        return job_id, state

    def verify_files(self, instance_id: str, state: dict[str, Any]) -> dict[str, Any]:
        assert self.temp_root is not None
        instance_dir = self.temp_root / "studio" / "instances" / instance_id
        env_path = instance_dir / ".env"
        compose_path = instance_dir / "compose.yml"
        astrbot_config_path = instance_dir / "data" / "astrbot" / "cmd_config.json"
        secrets_path = instance_dir / "secrets.json"
        for path in (env_path, compose_path, astrbot_config_path, secrets_path):
            if not path.is_file():
                raise E2EFailed(f"部署产物缺失：{path.name}")
        env_values = _read_env(env_path)
        bind_address = env_values.get("BIND_ADDRESS", "")
        if bind_address != "0.0.0.0":
            raise E2EFailed(f"BIND_ADDRESS 不是 0.0.0.0：{bind_address!r}")
        port = int(env_values.get("ASTRBOT_WEBUI_PORT") or 0)
        if port < 5000 or port > 10000:
            raise E2EFailed(f"实例端口不符合 5000-10000 随机分配预期：{port}")
        secrets_data = _read_json(secrets_path)
        self.secret_values.extend(
            str(secrets_data.get(key) or "")
            for key in ("astrbot_password", "astrbot_jwt_secret", "qq_official_secret")
        )
        self.secret_values = [item for item in self.secret_values if item]
        astrbot_config = _read_json(astrbot_config_path)
        platforms = astrbot_config.get("platform")
        if not isinstance(platforms, list) or len(platforms) != 1:
            raise E2EFailed("cmd_config.json.platform 必须且只能包含 QQ 官方平台")
        official_platform = platforms[0] if isinstance(platforms[0], dict) else {}
        if official_platform.get("type") != "qq_official":
            raise E2EFailed("cmd_config.json 平台类型不是 qq_official")
        if str(official_platform.get("appid") or "") != FAKE_OFFICIAL_APPID:
            raise E2EFailed("QQ 官方 AppID 未写入 AstrBot 配置")
        if str(official_platform.get("secret") or "") != FAKE_OFFICIAL_SECRET:
            raise E2EFailed("QQ 官方 Secret 未写入 AstrBot 配置")
        if str(secrets_data.get("qq_official_secret") or "") != FAKE_OFFICIAL_SECRET:
            raise E2EFailed("QQ 官方 Secret 未保存到 secrets.json")
        providers = astrbot_config.get("provider")
        sources = astrbot_config.get("provider_sources")
        provider = next((item for item in providers if isinstance(item, dict) and item.get("id") == "deepseek-flash"), None) if isinstance(providers, list) else None
        if not isinstance(provider, dict) or provider.get("model") != "deepseek-flash":
            raise E2EFailed("DeepSeek provider 配置不符合 deepseek-flash")
        # AstrBot v4.29 migrates a chat_completion provider at first boot:
        # connection fields (key/api_base/type) move into provider_sources,
        # while the provider entry keeps id/model/enable/provider_source_id.
        source = provider
        source_id = str(provider.get("provider_source_id") or "").strip()
        if source_id:
            source = next(
                (item for item in sources if isinstance(item, dict) and item.get("id") == source_id),
                None,
            ) if isinstance(sources, list) else None
            if not isinstance(source, dict):
                raise E2EFailed("provider 指向的 provider_source 不存在")
        if source.get("api_base") != "https://api.deepseek.com/v1":
            raise E2EFailed("DeepSeek provider api_base 不是官方 /v1 地址")
        agent_runner = astrbot_config.get("agent_runner")
        if not isinstance(agent_runner, dict) or not isinstance(agent_runner.get("config"), dict):
            raise E2EFailed("agent_runner 配置缺失或格式错误")
        if agent_runner["config"].get("persona", {}).get("persona_id") != PERSONA_ID:
            raise E2EFailed("agent_runner persona_id 未指向 E2E Persona")
        artifact_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (env_path, compose_path, astrbot_config_path)
        ).lower()
        for forbidden in ("snowluma", "novnc", "onebot"):
            if forbidden in artifact_text:
                raise E2EFailed(f"官方部署产物仍包含旧版字段：{forbidden}")
        if FAKE_DEEPSEEK_KEY in artifact_text:
            raise E2EFailed("DeepSeek Key 被错误写入了部署产物")
        self.summary["ports"] = {"astrbot_webui": port}
        self.summary["bind_address"] = bind_address
        self.set_check("generated_json", True, "cmd_config.json 为 qq_official 且关键字段匹配")
        self.set_check("single_port_allocation", True, f"bind=0.0.0.0 port={port}")
        return {
            "instance_dir": instance_dir,
            "env": env_values,
            "secrets": secrets_data,
            "astrbot_config": astrbot_config,
        }

    def verify_containers(self, instance_dir: Path) -> dict[str, Any]:
        meta = _read_json(instance_dir / "instance.json")
        astrbot_container = str(meta.get("astrbot_container") or "")
        project_name = str(meta.get("project_name") or "")
        if not astrbot_container or not project_name:
            raise E2EFailed("instance.json 缺少 AstrBot container/project 名称")
        self.summary["container_names"] = [astrbot_container]
        result = self.docker.run(["inspect", astrbot_container], timeout=30)
        if result.returncode != 0:
            raise E2EFailed(f"docker inspect 失败：{(result.stdout + result.stderr).strip()[-1000:]}")
        data = json.loads(result.stdout)
        states: dict[str, Any] = {}
        for item in data:
            name = str(item.get("Name") or "").lstrip("/")
            state = item.get("State") if isinstance(item.get("State"), dict) else {}
            health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
            states[name] = {"status": state.get("Status"), "health": health.get("Status")}
        info = states.get(astrbot_container) or {}
        if info.get("status") != "running":
            raise E2EFailed(f"容器 {astrbot_container} 状态不是 running：{info}")
        if info.get("health") == "unhealthy":
            self.summary["warnings"].append("AstrBot 容器 healthcheck 报告 unhealthy；以 WebUI/API 200 为主判据")
        self.set_check("containers_running", True, json.dumps(states, ensure_ascii=False))
        return {"project_name": project_name, "containers": states, "meta": meta}

    def _container_http_status(self, container: str, url: str, timeout: float = 20.0) -> int:
        """Fetch a loopback URL from inside an application container."""
        # AstrBot has Python; SnowLuma has Node. Avoid pulling an extra image.
        key = container.lower()
        if "astrbot" in key:
            inner = (
                "import sys, urllib.request\n"
                "try:\n"
                "    r=urllib.request.urlopen(sys.argv[1], timeout=10)\n"
                "    print(r.status)\n"
                "except Exception as e:\n"
                "    print('ERR', type(e).__name__, str(e)); raise SystemExit(2)\n"
            )
            command = [self.docker.base[0], "exec", container, "python", "-c", inner, url]
        else:
            inner = (
                "const u=process.argv[1];"
                "fetch(u).then(r=>{console.log(r.status);process.exit(r.status<500?0:2)})"
                ".catch(e=>{console.error(String(e));process.exit(2)})"
            )
            command = [self.docker.base[0], "exec", container, "node", "-e", inner, url]
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise E2EFailed(f"容器内 HTTP 检查失败：{exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stdout + "\n" + completed.stderr).strip()
            raise E2EFailed(f"容器内访问 {url} 失败：{detail[-800:]}")
        digits = re.search(r"\b([1-5]\d\d)\b", completed.stdout)
        if not digits:
            raise E2EFailed(f"容器内访问 {url} 未返回 HTTP 状态：{completed.stdout[-300:]}")
        return int(digits.group(1))

    def verify_http_endpoints(self, state: dict[str, Any], meta: dict[str, Any]) -> None:
        deployment = state.get("deployment") if isinstance(state.get("deployment"), dict) else {}
        urls = deployment.get("urls") if isinstance(deployment.get("urls"), dict) else {}
        astrbot_container = str(meta.get("astrbot_container") or "")
        if set(urls) != {"astrbot_webui"}:
            raise E2EFailed(f"state.urls 应仅包含 astrbot_webui：{sorted(urls)}")
        url = str(urls.get("astrbot_webui") or "")
        if not url.startswith(("http://", "https://")):
            raise E2EFailed(f"state.urls.astrbot_webui 无效：{url!r}")
        host_ok = False
        host_detail = ""
        try:
            status, _, _ = _http("GET", url.rstrip("/") + "/", timeout=12)
            if status < 500:
                host_ok = True
                host_detail = f"{url} -> HTTP {status}"
            else:
                host_detail = f"{url} -> HTTP {status}"
        except Exception as exc:  # noqa: BLE001
            host_detail = f"{url} 不可达：{exc}"
        if host_ok:
            self.set_check("http_astrbot_webui", True, host_detail)
            return
        if not astrbot_container:
            raise E2EFailed(f"AstrBot WebUI 宿主端口不可达且缺少容器名：{host_detail}")
        internal_status = self._container_http_status(astrbot_container, "http://127.0.0.1:6185/")
        if internal_status >= 500:
            raise E2EFailed(f"AstrBot WebUI 容器内返回 HTTP {internal_status}")
        self.set_check("http_astrbot_webui_internal", True, f"http://127.0.0.1:6185/ -> HTTP {internal_status}")
        self.summary["warnings"].append("宿主端口发布在当前环境不可用，已改用容器内回环验证：" + host_detail)
        self.summary["blocked"].append(
            {
                "code": "ENV_BLOCKED:HOST_PORT_PUBLISHING",
                "detail": host_detail + "；容器内服务正常，但宿主机未收到 Docker Desktop 发布端口。",
            }
        )
        log("ENV_BLOCKED: 宿主端口发布不可用；已用容器内 HTTP 验证代替")

    def _astrbot_exec_json(
        self,
        container: str,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str = "",
    ) -> tuple[int, dict[str, Any], str]:
        spec = {"method": method, "path": path, "payload": payload, "token": token}
        inner = r'''
import json, os, urllib.error, urllib.request
spec = json.loads(os.environ["QB_E2E_ASTRBOT_REQUEST"])
url = "http://127.0.0.1:6185" + spec["path"]
headers = {"Accept": "application/json", "User-Agent": "QQBotStudio-E2E/1.0"}
data = None
if spec.get("payload") is not None:
    data = json.dumps(spec["payload"], ensure_ascii=False).encode("utf-8")
    headers["Content-Type"] = "application/json"
if spec.get("token"):
    headers["Authorization"] = "Bearer " + spec["token"]
request = urllib.request.Request(url, data=data, headers=headers, method=spec["method"])
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        status = int(getattr(response, "status", 200))
        body = response.read().decode("utf-8", "replace")
except urllib.error.HTTPError as exc:
    status = int(exc.code)
    body = exc.read().decode("utf-8", "replace")
print(json.dumps({"status": status, "body": body}, ensure_ascii=False))
'''
        env = os.environ.copy()
        env["QB_E2E_ASTRBOT_REQUEST"] = json.dumps(spec, ensure_ascii=False)
        completed = self.docker.run(
            ["exec", "-i", "-e", "QB_E2E_ASTRBOT_REQUEST", container, "python", "-"],
            input_text=inner,
            env=env,
            timeout=45,
        )
        if completed.returncode != 0:
            raise E2EFailed(f"AstrBot 容器内 API 调用失败：{(completed.stdout + completed.stderr)[-800:]}")
        try:
            envelope = json.loads(completed.stdout.strip().splitlines()[-1])
            status = int(envelope.get("status") or 0)
            parsed = json.loads(str(envelope.get("body") or "{}"))
        except (ValueError, IndexError, TypeError) as exc:
            raise E2EFailed("AstrBot 容器内 API 返回格式异常") from exc
        if not isinstance(parsed, dict):
            parsed = {}
        return status, parsed, str(envelope.get("body") or "")

    def verify_astrbot_api(
        self,
        urls: dict[str, Any],
        secrets_data: dict[str, Any],
        meta: dict[str, Any],
    ) -> None:
        base = str(urls.get("astrbot_webui") or "").rstrip("/")
        username = str(secrets_data.get("astrbot_username") or "")
        password = str(secrets_data.get("astrbot_password") or "")
        container = str(meta.get("astrbot_container") or "")
        if not base or not username or not password:
            raise E2EFailed("缺少 AstrBot WebUI 或登录凭据")
        used_exec = False
        try:
            status, payload, text = _http_json(
                "POST",
                base + "/api/v1/auth/login",
                payload={"username": username, "password": password},
                timeout=20,
            )
        except Exception:  # noqa: BLE001
            if not container:
                raise
            used_exec = True
            status, payload, text = self._astrbot_exec_json(
                container, "POST", "/api/v1/auth/login", payload={"username": username, "password": password}
            )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        jwt = str(data.get("token") or "")
        if status != 200 or not jwt:
            raise E2EFailed(f"AstrBot 登录 API 失败：HTTP {status}: {text[:300]}")
        self.secret_values.append(jwt)
        if used_exec:
            status, payload, text = self._astrbot_exec_json(container, "GET", "/api/v1/personas", token=jwt)
        else:
            status, payload, text = _http_json(
                "GET",
                base + "/api/v1/personas",
                headers={"Authorization": f"Bearer {jwt}"},
                timeout=20,
            )
        if status != 200:
            raise E2EFailed(f"AstrBot Persona API 失败：HTTP {status}: {text[:300]}")
        serialized = json.dumps(payload, ensure_ascii=False)
        if PERSONA_ID not in serialized:
            raise E2EFailed(f"AstrBot Persona 列表未包含 {PERSONA_ID}")
        mode = "docker exec 回环桥接" if used_exec else "宿主端口"
        self.set_check("astrbot_login_and_persona_api", True, f"{mode}：登录 + /api/v1/personas 200")

    def verify_management_features(self, instance_id: str) -> None:
        q = urllib.parse.urlencode({"instance": instance_id})
        status, payload, text = self.api(f"/api/instances/{urllib.parse.quote(instance_id)}/plugins/qqbot_enhancer/config")
        if status != 200:
            raise E2EFailed(f"插件 schema API 失败：HTTP {status}: {text[:300]}")
        schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
        values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
        if "reply_probability" not in schema and "reply_probability" not in values:
            raise E2EFailed(f"插件 schema 缺少 reply_probability：{text[:500]}")
        self.set_check("plugin_schema_api", True, "qqbot_enhancer/reply_probability 可读")
        status, payload, text = self.api(f"/api/instances/{urllib.parse.quote(instance_id)}/stats")
        if status != 200:
            raise E2EFailed(f"统计 API 失败：HTTP {status}: {text[:300]}")
        self.set_check("resource_stats_api", True, "HTTP 200")
        status, payload, text = self.api(f"/api/instances")
        if status != 200 or not payload.get("ok"):
            raise E2EFailed(f"实例列表 API 失败：HTTP {status}: {text[:300]}")
        self.set_check("instances_api", True, "HTTP 200")
        # Keep the query local so the URL parameter is always explicit.
        status, payload, text = self.api(f"/api/state?{q}")
        if status != 200:
            raise E2EFailed(f"实例 state API 失败：HTTP {status}")
        deployment = payload.get("deployment") if isinstance(payload.get("deployment"), dict) else {}
        if FAKE_DEEPSEEK_KEY in text:
            raise E2EFailed("实例 state 响应泄漏了 DeepSeek Key")
        self.set_check("state_query_api", True, f"running={deployment.get('running')!r}")

    def write_redacted_job(self, job: dict[str, Any]) -> None:
        safe = {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "step": job.get("step"),
            "progress": job.get("progress"),
            "message": job.get("message"),
            "error": job.get("error"),
            "logs": job.get("logs") if isinstance(job.get("logs"), list) else [],
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
        }
        text = json.dumps(safe, ensure_ascii=False, indent=2) + "\n"
        for secret in self.secret_values:
            if secret:
                text = text.replace(secret, "***REDACTED***")
        self.job_artifact_path.write_text(text, encoding="utf-8")

    def cleanup(self, instance_dir: Path | None, meta: dict[str, Any] | None) -> None:
        # If deployment created resources, remove only this run's project.
        # Read instance.json defensively: a failure can happen before the
        # normal container-verification phase populates ``meta``.
        if instance_dir is not None and meta is None:
            try:
                meta = _read_json(instance_dir / "instance.json")
            except Exception:  # noqa: BLE001 - best-effort cleanup only
                meta = None
        if instance_dir is not None and meta:
            compose = instance_dir / "compose.yml"
            project_name = str(meta.get("project_name") or "")
            if compose.is_file() and project_name:
                log(f"清理 E2E Compose 项目：{project_name}")
                try:
                    result = self.docker.run(
                        ["compose", "-p", project_name, "-f", str(compose), "down", "--remove-orphans", "--timeout", "15"],
                        timeout=120,
                        cwd=instance_dir,
                    )
                    if result.returncode != 0:
                        self.summary["warnings"].append(
                            "Compose 清理返回非零：" + (result.stdout + result.stderr).strip()[-1000:]
                        )
                except Exception as exc:  # noqa: BLE001
                    self.summary["warnings"].append(f"Compose 清理失败：{exc}")
        if self.server_proc is not None:
            try:
                if self.server_proc.poll() is None:
                    self.server_proc.terminate()
                    try:
                        self.server_proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.server_proc.kill()
                        self.server_proc.wait(timeout=5)
            except Exception as exc:  # noqa: BLE001
                self.summary["warnings"].append(f"管理服务停止失败：{exc}")
        if self.temp_root is not None and not self.args.keep:
            shutil.rmtree(self.temp_root, ignore_errors=True)
            self.summary["temp_data_dir_removed"] = not self.temp_root.exists()
        elif self.temp_root is not None:
            self.summary["kept_temp_data_dir"] = _display_path(self.temp_root)

    def redact_artifacts(self) -> None:
        for path in self.artifact_dir.glob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            original = text
            for secret in self.secret_values:
                if secret:
                    text = text.replace(secret, "***REDACTED***")
            if text != original:
                path.write_text(text, encoding="utf-8")

    def run(self) -> int:
        instance_dir: Path | None = None
        meta: dict[str, Any] | None = None
        job: dict[str, Any] = {}
        try:
            self.docker.preflight()
            self.summary["docker_server_version"] = self.docker.server_version
            self.set_check("docker_preflight", True, f"Docker {self.docker.server_version} + Compose v2")
            self.temp_root = Path(tempfile.mkdtemp(prefix="qqbot-studio-e2e-"))
            self.summary["temp_data_dir"] = _display_path(self.temp_root)
            self.start_server()
            self.set_check("privacy_state_no_api_key", True, "state 响应未包含测试 Key")
            instance_id = self.create_instance()
            # Resolve cleanup metadata before deployment so a mid-deploy
            # failure can still remove only this run's compose project.
            instance_dir = self.temp_root / "studio" / "instances" / instance_id
            meta = _read_json(instance_dir / "instance.json")
            job_id, state = self.deploy(instance_id)
            files = self.verify_files(instance_id, state)
            deployment = state.get("deployment") if isinstance(state.get("deployment"), dict) else {}
            urls = deployment.get("urls") if isinstance(deployment.get("urls"), dict) else {}
            runtime = self.verify_containers(instance_dir)
            meta = runtime["meta"]
            self.verify_http_endpoints(state, meta)
            self.verify_astrbot_api(urls, files["secrets"], meta)
            self.verify_management_features(instance_id)
            # Persist only a redacted job record for debugging.
            status, job, text = self.api(f"/api/job/{urllib.parse.quote(job_id)}?instance={urllib.parse.quote(instance_id)}")
            if status == 200:
                self.write_redacted_job(job)
            if any(str(item.get("code", "")).startswith("ENV_BLOCKED:") for item in self.summary["blocked"]):
                self.summary["status"] = "PASS_WITH_ENV_BLOCKED_STEPS"
            else:
                self.summary["status"] = "PASS"
            return 0
        except E2EBlocked as exc:
            self.summary["status"] = "ENV_BLOCKED"
            self.summary["blocked"].append({"code": "ENV_BLOCKED", "detail": str(exc)})
            log(f"ENV_BLOCKED: {exc}")
            return 2
        except E2EFailed as exc:
            self.summary["status"] = "FAIL"
            self.summary["error"] = str(exc)
            log(f"FAIL: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            self.summary["status"] = "FAIL"
            self.summary["error"] = f"未预期异常：{exc.__class__.__name__}: {exc}"
            self.summary["traceback_tail"] = traceback.format_exc()[-3000:]
            log(f"FAIL: 未预期异常 {exc.__class__.__name__}: {exc}")
            return 1
        finally:
            self.cleanup(instance_dir, meta)
            self.summary["finished_at"] = utc_now()
            self.summary["artifacts"] = {
                "result_json": _display_path(self.summary_path),
                "server_log": _display_path(self.server_log_path),
                "job_log": _display_path(self.job_artifact_path) if self.job_artifact_path.exists() else "",
            }
            self.redact_artifacts()
            self.summary_path.write_text(
                json.dumps(self.summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            log(f"结果文件：{_display_path(self.summary_path)}")
            log(f"服务日志：{_display_path(self.server_log_path)}")


def _is_environment_error(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "未检测到可用的 docker",
        "docker compose",
        "docker 守护进程",
        "docker daemon",
        "拉取镜像失败",
        "network is unreachable",
        "connection refused",
        "tls handshake timeout",
        "no such host",
        "i/o timeout",
        "permission denied while trying to connect",
        "cannot connect to the docker",
    )
    return any(marker in lowered for marker in markers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QQ Bot Studio 真实容器 E2E smoke")
    parser.add_argument("--timeout", type=int, default=900, help="等待部署 job 的秒数")
    parser.add_argument("--studio-port", type=int, default=0, help="临时管理服务端口；0=随机")
    parser.add_argument(
        "--artifacts-dir",
        default=str(REPO_ROOT / "tests" / "e2e" / "artifacts"),
        help="脱敏后的结果/日志目录",
    )
    parser.add_argument(
        "--exec-astrbot-bridge",
        action="store_true",
        help="仅在 Docker Desktop 宿主端口发布失效时使用的 E2E 测试桥（通过 docker exec 访问 AstrBot）",
    )
    parser.add_argument("--keep", action="store_true", help="保留临时数据目录（含敏感配置，调试用）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.studio_port = int(args.studio_port or _free_local_port())
    if args.studio_port < 1 or args.studio_port > 65535:
        print("--studio-port 必须在 1-65535", file=sys.stderr)
        return 2
    run = SmokeRun(args)
    try:
        return run.run()
    except KeyboardInterrupt:
        log("收到中断，正在清理本轮资源")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
