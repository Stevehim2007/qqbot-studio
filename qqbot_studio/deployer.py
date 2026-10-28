"""Deployment orchestration for AstrBot + SnowLuma + DeepSeek."""

from __future__ import annotations

import glob
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .astrbot_api import AstrBotAPIError, AstrBotClient
from .configgen import generate_artifacts, image_names_for_prefix, mirror_prefixes, set_image_names
from .dockerctl import DockerController, DockerError
from .store import (
    DEFAULT_PROMPT,
    MODEL_NAME,
    Store,
    default_data_dir,
    generate_token,
    utc_now,
)

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
QQ_ACCOUNT_RE = re.compile(r"^\d{5,12}$")
PERSONA_ID = "qqbot-assistant"
MAX_PROMPT_LENGTH = 12_000


class DeploymentError(RuntimeError):
    """User-facing deployment error."""


@dataclass
class Job:
    job_id: str
    status: str = "running"
    step: str = "准备部署"
    progress: int = 0
    message: str = "正在准备部署任务…"
    logs: list[str] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "step": self.step,
            "progress": max(0, min(100, int(self.progress))),
            "message": self.message,
            "logs": list(self.logs),
            "error": self.error,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DeployManager:
    """Thread-safe facade used by the local web server."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        store: Store | None = None,
        docker: DockerController | None = None,
        api_client_factory: Callable[[str, str, str], AstrBotClient] | None = None,
    ) -> None:
        self.store = store or Store(data_dir or default_data_dir())
        self.data_dir = self.store.data_dir
        self.docker = docker or DockerController(self.data_dir)
        self._api_client_factory = api_client_factory or (
            lambda base_url, username, password: AstrBotClient(base_url, username, password)
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._job_order: list[str] = []
        self._active_job_id = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_qq(qq_account: str) -> str:
        value = str(qq_account or "").strip()
        if not QQ_ACCOUNT_RE.fullmatch(value):
            raise DeploymentError("QQ 号必须为 5–12 位数字。")
        return value

    @staticmethod
    def _validate_prompt(prompt: str) -> str:
        value = str(prompt or "").strip()
        if not value:
            raise DeploymentError("提示词不能为空。")
        if len(value) > MAX_PROMPT_LENGTH:
            raise DeploymentError(f"提示词不能超过 {MAX_PROMPT_LENGTH} 个字符。")
        return value

    @staticmethod
    def _now() -> str:
        return utc_now()

    def _append_log(self, job: Job, line: str) -> None:
        line = ANSI_RE.sub("", str(line)).rstrip()
        if not line:
            return
        with self._lock:
            job.logs.extend(line.splitlines())
            # Keep the in-memory job bounded; the UI only needs recent output.
            if len(job.logs) > 2500:
                job.logs = job.logs[-2500:]
            job.updated_at = self._now()

    def _set_job(self, job: Job, *, step: str | None = None, progress: int | None = None, message: str | None = None) -> None:
        with self._lock:
            if step is not None:
                job.step = step
            if progress is not None:
                job.progress = max(0, min(100, int(progress)))
            if message is not None:
                job.message = message
            job.updated_at = self._now()

    def _collect_sensitive_values(self) -> set[str]:
        """Collect locally stored credentials for log redaction.

        This includes values from AstrBot's generated ``cmd_config.json``
        because AstrBot can echo provider errors or configuration details into
        its container logs.
        """

        values: set[str] = set()
        try:
            secrets_data = self.store.load_secrets()
            for key, value in secrets_data.items():
                if not isinstance(value, str) or len(value) < 6:
                    continue
                if any(word in str(key).lower() for word in ("password", "token", "secret", "vnc")):
                    values.add(value)
        except Exception:
            pass

        config_path = self.data_dir / "data" / "astrbot" / "cmd_config.json"
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parsed = None
        if parsed is not None:
            def walk(node: Any, parent_key: str = "") -> None:
                if isinstance(node, dict):
                    for key, item in node.items():
                        walk(item, str(key))
                elif isinstance(node, list):
                    for item in node:
                        walk(item, parent_key)
                elif isinstance(node, str):
                    lowered = parent_key.lower()
                    if any(word in lowered for word in ("key", "password", "token", "secret")):
                        if len(node) >= 6:
                            values.add(node)
                    elif node.startswith("sk-") and len(node) >= 10:
                        values.add(node)

            walk(parsed)
        return {value for value in values if value and value not in {"admin", "astrbot"}}

    def redact_sensitive_text(self, text: str) -> str:
        clean = ANSI_RE.sub("", str(text))
        for value in sorted(self._collect_sensitive_values(), key=len, reverse=True):
            clean = clean.replace(value, "***")
        # Defence in depth: mask secret-shaped values even when the provider
        # config is unreadable because AstrBot rewrote it as root.
        clean = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}\b", "sk-***", clean)
        clean = re.sub(
            r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}",
            r"\1***",
            clean,
        )
        clean = re.sub(
            r"(?i)([?&](?:access_token|token)=)[^&\s]+",
            r"\1***",
            clean,
        )
        return clean

    def _redactor(self, api_key: str) -> Callable[[str], str]:
        # Always re-read persisted secrets when redacting, so credentials created
        # halfway through a deployment are covered too.
        if api_key:
            return lambda text: self.redact_sensitive_text(str(text).replace(api_key, "***"))
        return self.redact_sensitive_text

    # ------------------------------------------------------------------
    # DeepSeek API preflight
    # ------------------------------------------------------------------
    def probe_deepseek_key(self, api_key: str) -> tuple[bool, str, int]:
        """Return (ok, human message, HTTP status)."""

        key = str(api_key or "").strip()
        if not key:
            return False, "请填写 DeepSeek API Key。", 0
        request = urllib.request.Request(
            "https://api.deepseek.com/models",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "User-Agent": "QQBotStudio/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", "replace")
                status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            if status in (401, 403):
                return False, "DeepSeek API Key 无效或没有权限（HTTP 401/403）。", status
            if status == 429:
                return False, "DeepSeek 请求过于频繁（HTTP 429），请稍后重试。", status
            return False, f"DeepSeek API 返回 HTTP {status}，请检查 Key 与网络。", status
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            return False, f"无法连接 DeepSeek API：{reason}", 0

        if status != 200:
            return False, f"DeepSeek API 返回 HTTP {status}。", status
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return True, "DeepSeek API Key 可用（返回内容不是标准 JSON）。", status
        ids: list[str] = []
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
            for item in parsed["data"]:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ids.append(item["id"])
        if ids and MODEL_NAME not in ids:
            return True, f"Key 可用，但模型列表中未出现 {MODEL_NAME}；部署后请检查账户模型权限。", status
        return True, f"DeepSeek API Key 校验通过，模型 {MODEL_NAME} 可用。", status

    def test_deepseek_key(self, api_key: str) -> dict[str, Any]:
        ok, message, _ = self.probe_deepseek_key(api_key)
        return {"ok": ok, "message": message}

    # ------------------------------------------------------------------
    # Deployment jobs
    # ------------------------------------------------------------------
    def start_deploy(
        self,
        *,
        qq_account: str,
        deepseek_api_key: str,
        prompt: str,
        use_mirror: bool = False,
        accept_risk: bool = False,
    ) -> str:
        qq = self._validate_qq(qq_account)
        text_prompt = self._validate_prompt(prompt)
        api_key = str(deepseek_api_key or "").strip()
        if not api_key:
            raise DeploymentError("请填写 DeepSeek API Key。")
        if not accept_risk:
            raise DeploymentError("请先勾选风险确认。")

        with self._lock:
            if self._active_job_id:
                active = self._jobs.get(self._active_job_id)
                if active and active.status == "running":
                    raise DeploymentError("已有部署任务正在运行，请等待完成。")
            config = self.store.load_config()
            if config.get("deployed"):
                raise DeploymentError("当前已部署。请先在完成页使用服务管理，或删除数据目录后重新部署。")
            job_id = f"job-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{generate_token(4)}"
            job = Job(job_id=job_id)
            self._jobs[job_id] = job
            self._job_order.append(job_id)
            while len(self._job_order) > 25:
                old = self._job_order.pop(0)
                self._jobs.pop(old, None)
            self._active_job_id = job_id

        try:
            config = self.store.load_config()
            config["last_job_id"] = job_id
            config["qq_account"] = qq
            config["prompt"] = text_prompt
            config["use_mirror"] = bool(use_mirror)
            config["model"] = MODEL_NAME
            self.store.save_config(config)
        except Exception as exc:
            with self._lock:
                self._jobs.pop(job_id, None)
                if job_id in self._job_order:
                    self._job_order.remove(job_id)
                if self._active_job_id == job_id:
                    self._active_job_id = ""
            raise DeploymentError(f"保存部署配置失败：{exc}") from exc

        thread = threading.Thread(
            target=self._run_deploy,
            name=f"qqbot-deploy-{job_id}",
            daemon=True,
            args=(job_id, qq, api_key, text_prompt, bool(use_mirror)),
        )
        try:
            thread.start()
        except Exception as exc:
            with self._lock:
                self._jobs.pop(job_id, None)
                if job_id in self._job_order:
                    self._job_order.remove(job_id)
                if self._active_job_id == job_id:
                    self._active_job_id = ""
            raise DeploymentError(f"无法启动部署线程：{exc}") from exc
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            return job.to_dict() if job else None

    def _run_deploy(
        self,
        job_id: str,
        qq_account: str,
        api_key: str,
        prompt: str,
        use_mirror: bool,
    ) -> None:
        job = self._jobs[job_id]
        # Keep a redactor available even if secrets.json was corrupted or is
        # temporarily unreadable.  The full redactor re-reads secrets on each
        # call; this fallback still masks the API key in startup errors.
        redact: Callable[[str], str] = lambda text: str(text).replace(api_key, "***") if api_key else str(text)
        try:
            redact = self._redactor(api_key)
            self._deploy_impl(job, qq_account, api_key, prompt, use_mirror, redact)
        except Exception as exc:  # noqa: BLE001 - report all failures to the UI
            message = redact(str(exc) or exc.__class__.__name__)
            if not isinstance(exc, (DeploymentError, DockerError, AstrBotAPIError, ValueError)):
                message = f"部署过程中发生未预期错误：{message}"
            with self._lock:
                job.status = "error"
                job.error = message
                job.message = message
                job.step = job.step or "部署失败"
                job.updated_at = self._now()
                self._append_log(job, f"[错误] {message}")
        else:
            with self._lock:
                job.status = "success"
                job.progress = 100
                job.step = "部署完成"
                job.message = "AstrBot、SnowLuma 与 DeepSeek 已配置完成。下一步请打开 noVNC 扫码登录 QQ。"
                job.error = ""
                job.updated_at = self._now()
                self._append_log(job, "[完成] 部署成功。请打开 noVNC 扫码登录 QQ。")
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = ""

    def _deploy_impl(
        self,
        job: Job,
        qq_account: str,
        api_key: str,
        prompt: str,
        use_mirror: bool,
        redact: Callable[[str], str],
    ) -> None:
        add_log = lambda text: self._append_log(job, redact(text))  # noqa: E731

        self._set_job(job, step="检查 Docker 环境", progress=4, message="正在检查 Docker 与 Compose 是否可用…")
        add_log("正在检查 Docker 环境…")
        status = self.docker.require_ready()
        add_log(f"Docker 已就绪：{status.version or '版本未知'}" + ("（通过 sudo 调用）" if status.uses_sudo else ""))

        self._set_job(job, step="检测 DeepSeek API Key", progress=8, message=f"正在验证 DeepSeek Key 与 {MODEL_NAME} 模型…")
        ok, key_message, key_status = self.probe_deepseek_key(api_key)
        add_log(key_message)
        if not ok and key_status in (401, 403):
            raise DeploymentError(f"DeepSeek API Key 校验失败：{key_message}")
        if not ok:
            add_log("警告：DeepSeek 预检未能完成，将继续部署；请在部署后通过 AstrBot WebUI 检查模型配置。")

        self._set_job(job, step="生成本地配置", progress=14, message="正在生成 Docker Compose、OneBot 与模型配置…")
        secrets_data = self.store.ensure_secrets()
        artifacts = generate_artifacts(
            self.data_dir,
            api_key=api_key,
            prompt=prompt,
            qq_account=qq_account,
            use_mirror=use_mirror,
            secrets_data=secrets_data,
            ports=self.store.load_config().get("ports") or None,
        )
        ports = artifacts["ports"]
        assert isinstance(ports, dict)
        secrets_data["ports"] = ports
        self.store.save_secrets(secrets_data)
        config = self.store.load_config()
        config.update(
            {
                "qq_account": qq_account,
                "prompt": prompt,
                "use_mirror": use_mirror,
                "ports": ports,
                "model": MODEL_NAME,
                "deployed": False,
            }
        )
        self.store.save_config(config)
        add_log("本地配置已生成，包含随机 OneBot token 与 AstrBot 登录凭据。")
        candidate_prefixes = mirror_prefixes() if use_mirror else [""]
        if use_mirror:
            add_log("已启用国内镜像加速，候选源：" + "、".join(candidate_prefixes[:4]))

        self._set_job(job, step="拉取 Docker 镜像", progress=24, message="正在拉取 AstrBot 与 SnowLuma 镜像，首次运行可能需要数分钟…")
        pull_result = None
        last_pull_error: Exception | None = None
        for index, prefix in enumerate(candidate_prefixes):
            if index > 0:
                astrbot_image, snowluma_image = image_names_for_prefix(prefix)
                set_image_names(artifacts["env"], astrbot_image, snowluma_image)
                add_log(f"镜像源不可用，自动切换到：{prefix or 'Docker Hub'}")
            try:
                pull_result = self.docker.pull()
            except DockerError as exc:
                last_pull_error = exc
                add_log(f"镜像源拉取失败（{prefix or 'Docker Hub'}）：{exc}")
                continue
            if use_mirror:
                config["docker_mirror"] = prefix
                self.store.save_config(config)
                add_log(f"镜像拉取成功，当前镜像源：{prefix}")
            add_log(self.docker._tail(pull_result.output, 2500) or "镜像拉取完成。")
            break

        if pull_result is None:
            suggestion = (
                "请检查网络，稍后重试；也可以在环境变量 QQBOT_IMAGE_MIRROR_PREFIXES 中配置自己的可用镜像前缀。"
                if use_mirror
                else "如果位于中国大陆，请返回配置页启用“国内镜像加速”后重试。"
            )
            detail = f" 最后错误：{last_pull_error}" if last_pull_error else ""
            raise DeploymentError(f"拉取镜像失败，已尝试的镜像源均不可用。{suggestion}{detail}")

        self._set_job(job, step="启动容器", progress=46, message="正在创建并启动 AstrBot 与 SnowLuma 容器…")
        up_result = self.docker.up()
        add_log(self.docker._tail(up_result.output, 3000) or "容器启动命令已完成。")

        self._set_job(job, step="等待 AstrBot 启动", progress=58, message="正在等待 AstrBot WebUI 与 API 就绪…")
        astrbot_url = f"http://127.0.0.1:{ports['astrbot_webui']}"
        client = self._api_client_factory(
            astrbot_url,
            str(secrets_data["astrbot_username"]),
            str(secrets_data["astrbot_password"]),
        )
        client.wait_until_ready(timeout=180)
        add_log("AstrBot WebUI 已响应。")

        # SnowLuma's initial WebUI password is emitted exactly once on first
        # boot; capture it now while the logs are guaranteed to be available.
        self._set_job(job, step="初始化 SnowLuma", progress=68, message="正在读取 SnowLuma 初始化信息并配置人格…")
        self._capture_snowluma_password(secrets_data, add_log)

        self._set_job(job, step="写入机器人人格", progress=78, message="正在通过 AstrBot API 创建/更新机器人提示词…")
        client.upsert_persona(PERSONA_ID, prompt)
        add_log("机器人人格提示词已写入 AstrBot。")

        self._set_job(job, step="检查容器状态", progress=90, message="正在确认两个容器均处于运行状态…")
        state = self.docker.detect(force=True)
        astrbot_state = self.docker._docker(["inspect", "--format", "{{.State.Status}}", self.docker.ASTRBOT_CONTAINER], timeout=20)
        snowluma_state = self.docker._docker(["inspect", "--format", "{{.State.Status}}", self.docker.SNOWLUMA_CONTAINER], timeout=20)
        astrbot_running = astrbot_state.ok and astrbot_state.stdout.strip() == "running"
        snowluma_running = snowluma_state.ok and snowluma_state.stdout.strip() == "running"
        if not (astrbot_running and snowluma_running):
            raise DeploymentError(
                "容器启动后未保持运行。请查看向导中的 AstrBot / SnowLuma 日志。"
                + f" AstrBot={astrbot_state.stdout.strip() or 'unknown'}, SnowLuma={snowluma_state.stdout.strip() or 'unknown'}"
            )
        if not state.ready:
            raise DeploymentError(state.detail or "Docker 状态异常。")
        add_log("AstrBot 与 SnowLuma 容器均运行中。")

        config = self.store.load_config()
        config["deployed"] = True
        config["deployed_at"] = utc_now()
        self.store.save_config(config)
        self.store.save_state(
            {
                "deployed": True,
                "deployed_at": config["deployed_at"],
                "ports": ports,
                "containers": {
                    "astrbot": "running",
                    "snowluma": "running",
                },
            }
        )
        self._set_job(
            job,
            step="部署完成",
            progress=100,
            message="部署完成。请打开 noVNC，在 QQ 官方登录界面扫码并完成必要验证。",
        )

    # ------------------------------------------------------------------
    # Status and operations
    # ------------------------------------------------------------------
    def _capture_snowluma_password(
        self,
        secrets_data: dict[str, Any] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> str:
        secrets_data = secrets_data or self.store.load_secrets()
        existing = str(secrets_data.get("snowluma_password") or "").strip()
        if existing:
            return existing
        try:
            time.sleep(2)
            logs = self.docker.logs("snowluma", 800)
        except Exception as exc:  # noqa: BLE001 - optional display credential
            if log:
                log(f"暂时无法读取 SnowLuma 初始密码：{exc}")
            return ""
        clean = ANSI_RE.sub("", logs)
        patterns = (
            r"临时密码\s*[:：]\s*([A-Za-z0-9_@#%+\-]{4,128})",
            r"initial credentials:\s*user=\S+\s+password=([^\s]+)",
            r"password\s*[:：]\s*([A-Za-z0-9_@#%+\-]{4,128})",
        )
        found = ""
        for pattern in patterns:
            matches = re.findall(pattern, clean, flags=re.IGNORECASE)
            if matches:
                found = str(matches[-1]).strip()
                break
        if found:
            secrets_data["snowluma_password"] = found
            self.store.save_secrets(secrets_data)
            if log:
                log("已获取 SnowLuma WebUI 初始密码并保存到本地 secrets.json。")
        elif log:
            log("SnowLuma 初始密码尚未出现在日志中；可在完成页刷新状态，或查看 SnowLuma 日志。")
        return found

    def _detect_onebot_connection(self, lines: int = 400) -> bool | None:
        try:
            logs = self.docker.logs("astrbot", lines)
        except Exception:
            return None
        lower = ANSI_RE.sub("", logs).lower()
        connected_markers = (
            "aiocqhttp(onebot v11) 适配器已连接",
            "aiocqhttp 适配器已连接",
            "aiocqhttp adapter connected",
        )
        closed_markers = (
            "aiocqhttp 适配器已被关闭",
            "aiocqhttp adapter closed",
        )
        last_connected = max((lower.rfind(marker) for marker in connected_markers), default=-1)
        last_closed = max((lower.rfind(marker) for marker in closed_markers), default=-1)
        if last_connected < 0 and last_closed < 0:
            return None
        return last_connected > last_closed

    def _detect_qq_login(self, qq_account: str) -> bool:
        config_dir = self.data_dir / "data" / "snowluma" / "config"
        try:
            files = [
                Path(path)
                for path in glob.glob(str(config_dir / "onebot_*.json"))
                if re.fullmatch(r"onebot_\d+\.json", Path(path).name)
            ]
        except OSError:
            return False
        if not files:
            return False
        if not qq_account:
            return bool(files)
        expected = f"onebot_{qq_account}.json"
        return any(path.name == expected for path in files)

    @staticmethod
    def _read_os_name() -> str:
        try:
            with open("/etc/os-release", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "未知系统"

    def get_status(self) -> dict[str, Any]:
        config = self.store.load_config()
        secrets_data = self.store.load_secrets()
        state = self.store.load_state()
        docker_status = self.docker.detect()
        deployed = bool(config.get("deployed") or state.get("deployed"))
        ports = config.get("ports") or state.get("ports") or {}
        if not isinstance(ports, dict):
            ports = {}

        running: bool | None = None
        if deployed and docker_status.ready:
            try:
                astrbot_state = self.docker._docker(
                    ["inspect", "--format", "{{.State.Status}}", self.docker.ASTRBOT_CONTAINER],
                    timeout=15,
                )
                snowluma_state = self.docker._docker(
                    ["inspect", "--format", "{{.State.Status}}", self.docker.SNOWLUMA_CONTAINER],
                    timeout=15,
                )
                running = (
                    astrbot_state.ok
                    and astrbot_state.stdout.strip() == "running"
                    and snowluma_state.ok
                    and snowluma_state.stdout.strip() == "running"
                )
            except Exception:
                running = None

        urls = {
            "astrbot_webui": f"http://127.0.0.1:{ports['astrbot_webui']}" if ports.get("astrbot_webui") else "",
            "snowluma_webui": f"http://127.0.0.1:{ports['snowluma_webui']}" if ports.get("snowluma_webui") else "",
            "novnc": f"http://127.0.0.1:{ports['novnc']}" if ports.get("novnc") else "",
        }
        snowluma_password = str(secrets_data.get("snowluma_password") or "")
        if deployed and not snowluma_password and running:
            snowluma_password = self._capture_snowluma_password(secrets_data)
        onebot_connected = self._detect_onebot_connection() if deployed and running else None
        qq_logged_in = self._detect_qq_login(str(config.get("qq_account") or "")) if deployed else False

        if not docker_status.installed:
            notice = "未检测到 Docker。请先在终端运行 ./start.sh（或 ./install.sh）完成安装。"
        elif not docker_status.compose_installed:
            notice = "Docker Compose 插件不可用。请安装 docker-compose-plugin 后重试。"
        elif not docker_status.daemon_running:
            notice = "Docker 守护进程未运行。请启动 Docker 后点击“重新检测”。"
        elif not docker_status.permission_ok:
            notice = docker_status.detail or "当前用户没有 Docker 权限，请重新打开启动器以缓存 sudo 凭据。"
        elif deployed and running is False:
            notice = "部署已完成，但容器当前未运行。可在完成页启动服务。"
        elif deployed and not qq_logged_in:
            notice = "请打开 noVNC，使用手机 QQ 扫码登录；如出现设备/短信/滑块验证，请在 QQ 官方界面完成。"
        else:
            notice = ""

        return {
            "app_version": "1.0.0",
            "requirements": {
                "docker_installed": docker_status.installed,
                "docker_running": docker_status.daemon_running,
                "compose_installed": docker_status.compose_installed,
                "permission_ok": docker_status.permission_ok,
                "os": self._read_os_name(),
            },
            "deployment": {
                "deployed": deployed,
                "running": running,
                "qq_account": str(config.get("qq_account") or ""),
                "model": MODEL_NAME,
                "prompt": str(config.get("prompt") or DEFAULT_PROMPT),
                "urls": urls,
                "credentials": {
                    "astrbot_username": str(secrets_data.get("astrbot_username") or ""),
                    "astrbot_password": str(secrets_data.get("astrbot_password") or ""),
                    "snowluma_password": snowluma_password,
                    "vnc_password": str(secrets_data.get("vnc_password") or ""),
                },
                "onebot_connected": onebot_connected,
                "qq_logged_in": qq_logged_in,
            },
            "notice": notice,
        }

    def container_action(self, action: str) -> str:
        action = str(action or "").strip().lower()
        if action not in {"start", "stop", "restart"}:
            raise DeploymentError("不支持的服务操作，仅支持 start、stop、restart。")
        config = self.store.load_config()
        if not config.get("deployed"):
            raise DeploymentError("尚未完成部署，不能执行服务操作。")
        self.docker.require_ready()
        if action == "stop":
            self.docker.stop()
        elif action == "start":
            self.docker.up()
        else:
            self.docker.restart()
        labels = {"start": "启动", "stop": "停止", "restart": "重启"}
        return f"{labels[action]}指令已发送。"

    def update_persona(self, prompt: str) -> str:
        value = self._validate_prompt(prompt)
        config = self.store.load_config()
        if not config.get("deployed"):
            raise DeploymentError("尚未完成部署，不能更新提示词。")
        secrets_data = self.store.ensure_secrets()
        ports = config.get("ports") or {}
        if not isinstance(ports, dict) or not ports.get("astrbot_webui"):
            raise DeploymentError("缺少 AstrBot WebUI 端口配置。")
        client = self._api_client_factory(
            f"http://127.0.0.1:{ports['astrbot_webui']}",
            str(secrets_data["astrbot_username"]),
            str(secrets_data["astrbot_password"]),
        )
        client.wait_until_ready(timeout=30)
        client.upsert_persona(PERSONA_ID, value)
        config["prompt"] = value
        self.store.save_config(config)
        return "提示词已写入 AstrBot，并更新本机配置。"

    def get_logs(self, service: str, lines: int = 200) -> str:
        raw = self.docker.logs(service, lines)
        return self.redact_sensitive_text(raw)
