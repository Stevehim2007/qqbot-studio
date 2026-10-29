"""Deployment orchestration for AstrBot + SnowLuma + DeepSeek."""

from __future__ import annotations

import copy
import glob
import hashlib
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote as urlquote
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .astrbot_api import AstrBotAPIError, AstrBotClient
from .configgen import generate_artifacts, image_names_for_prefix, mirror_prefixes, set_image_names
from .dockerctl import DockerController, DockerError
from .store import (
    DEFAULT_MEMORY_MODE,
    DEFAULT_PROMPT,
    MEMORY_MODES,
    MODEL_NAME,
    Store,
    chmod_best_effort,
    default_data_dir,
    generate_token,
    utc_now,
)

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
QQ_ACCOUNT_RE = re.compile(r"^\d{5,12}$")
PERSONA_ID = "qqbot-assistant"
ENHANCER_PLUGIN_ID = "qqbot_enhancer"
MAX_PROMPT_LENGTH = 12_000
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PLUGIN_CONFIG_MAX_FIELDS = 256
PLUGIN_CONFIG_MAX_REQUEST_BYTES = 512 * 1024
PLUGIN_CONFIG_MAX_STRING_LENGTH = 100_000
PLUGIN_CONFIG_MAX_LIST_ITEMS = 256


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


def _plugin_field_is_sensitive(field_name: Any, schema_item: Any = None) -> bool:
    """Return True for key/token/password/secret-shaped config fields."""

    if isinstance(schema_item, dict):
        if bool(schema_item.get("secret")) or bool(schema_item.get("sensitive")):
            return True
        item_type = str(schema_item.get("type") or "").strip().lower()
        if item_type in {"password", "secret", "token", "api_key", "apikey"}:
            return True
    compact = re.sub(r"[^a-z0-9]", "", str(field_name or "").lower())
    if compact in {"key", "token", "password", "secret", "apikey", "accesstoken"}:
        return True
    return any(
        token in compact
        for token in (
            "apikey",
            "accesstoken",
            "authtoken",
            "password",
            "passwd",
            "secret",
            "credential",
            "privatekey",
            "jwtsecret",
        )
    )


def _strip_sensitive_plugin_value(value: Any) -> Any:
    """Recursively copy a value while removing key/token/password fields."""

    if isinstance(value, dict):
        return {
            str(key): _strip_sensitive_plugin_value(item)
            for key, item in value.items()
            if not _plugin_field_is_sensitive(key, item if isinstance(item, dict) else None)
        }
    if isinstance(value, list):
        return [_strip_sensitive_plugin_value(item) for item in value]
    return copy.deepcopy(value)


def _public_plugin_schema(schema: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key, item in schema.items():
        if not isinstance(item, dict) or _plugin_field_is_sensitive(key, item):
            continue
        definition = _strip_sensitive_plugin_value(item)
        # Keep the raw nested slider object for API compatibility while also
        # exposing common flat fields consumed by generic dashboard clients.
        slider = item.get("slider")
        if isinstance(slider, dict):
            for name in ("min", "max", "step"):
                if name in slider and name not in definition:
                    definition[name] = copy.deepcopy(slider[name])
        public[str(key)] = definition
    return public


def _public_plugin_values(values: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _strip_sensitive_plugin_value(values[key])
        for key, item in schema.items()
        if key in values and isinstance(item, dict) and not _plugin_field_is_sensitive(key, item)
    }


def secrets_token_fragment(length: int = 6) -> str:
    import secrets as _secrets

    return _secrets.token_hex(length).upper()


class DeployManager:
    """Thread-safe facade used by the local web server."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        store: Store | None = None,
        docker: DockerController | None = None,
        api_client_factory: Callable[[str, str, str], AstrBotClient] | None = None,
        public_host: str = "",
        bind_address: str = "127.0.0.1",
        instance_id: str = "",
        project_name: str = "qqbot-studio",
        astrbot_container: str = "qqbot-astrbot",
        snowluma_container: str = "qqbot-snowluma",
        network_name: str = "qqbot-studio-net",
    ) -> None:
        self.store = store or Store(data_dir or default_data_dir())
        # Additive migration: old config.json files gain optional fields while
        # existing values (especially memory_mode) are never overwritten.
        try:
            self.store.migrate_config()
        except (OSError, ValueError):
            pass
        self.data_dir = self.store.data_dir
        try:
            self._migrate_enhancer_config_defaults()
        except (OSError, ValueError):
            pass
        self.public_host = str(public_host or "").strip()
        self.bind_address = str(bind_address or "127.0.0.1").strip()
        self.instance_id = str(instance_id or "").strip()
        self.project_name = str(project_name or "qqbot-studio")
        self.astrbot_container = str(astrbot_container or "qqbot-astrbot")
        self.snowluma_container = str(snowluma_container or "qqbot-snowluma")
        self.network_name = str(network_name or "qqbot-studio-net")
        self.docker = docker or DockerController(
            self.data_dir,
            project_name=self.project_name,
            astrbot_container=self.astrbot_container,
            snowluma_container=self.snowluma_container,
        )
        self._api_client_factory = api_client_factory or (
            lambda base_url, username, password: AstrBotClient(base_url, username, password)
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._job_order: list[str] = []
        self._active_job_id = ""
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        try:
            chmod_best_effort(self.jobs_dir, 0o700)
        except OSError:
            pass
        self._last_persist_at: dict[str, float] = {}
        self._recover_interrupted_jobs()

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

    def _job_path(self, job_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id))
        return self.jobs_dir / f"{safe}.json"

    def _persist_job(self, job: Job, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last_persist_at.get(job.job_id, 0.0)
            if not force and now - last < 0.75:
                return
            self._last_persist_at[job.job_id] = now
            snapshot = job.to_dict()
        path = self._job_path(job.job_id)
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(f".{path.name}.{secrets_token_fragment()}")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            chmod_best_effort(path, 0o600)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _load_persisted_job(self, job_id: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._job_path(job_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _recover_interrupted_jobs(self) -> None:
        """Mark jobs left running by a previous server process as interrupted."""

        try:
            paths = sorted(self.jobs_dir.glob("job-*.json"))
        except OSError:
            return
        for path in paths[-20:]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("status") != "running":
                continue
            data["status"] = "error"
            data["error"] = "管理服务重启，上一轮部署被中断。请重新点击部署，程序会基于现有配置和容器继续修复。"
            data["message"] = data["error"]
            data["updated_at"] = utc_now()
            logs = data.get("logs")
            if isinstance(logs, list):
                logs.append("[中断] " + data["error"])
                data["logs"] = logs[-2500:]
            try:
                tmp = path.with_name(f".{path.name}.recover")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                os.replace(tmp, path)
                chmod_best_effort(path, 0o600)
            except OSError:
                pass

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
            self._persist_job(job)

    def _set_job(self, job: Job, *, step: str | None = None, progress: int | None = None, message: str | None = None) -> None:
        with self._lock:
            if step is not None:
                job.step = step
            if progress is not None:
                job.progress = max(0, min(100, int(progress)))
            if message is not None:
                job.message = message
            job.updated_at = self._now()
            self._persist_job(job)

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
        mirror_candidates: list[str] | None = None,
    ) -> str:
        qq = self._validate_qq(qq_account)
        text_prompt = self._validate_prompt(prompt)
        api_key = str(deepseek_api_key or "").strip()
        if not api_key:
            raise DeploymentError("请填写 DeepSeek API Key。")
        if not accept_risk:
            raise DeploymentError("请先勾选风险确认。")

        if mirror_candidates is None:
            # Backward-compatible path used by older callers/tests.
            mirror_candidates = mirror_prefixes() if use_mirror else []
        configured_prefixes = [str(item).strip() for item in mirror_candidates if str(item).strip()]
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
            self._persist_job(job, force=True)

        try:
            config = self.store.load_config()
            config["last_job_id"] = job_id
            config["qq_account"] = qq
            config["prompt"] = text_prompt
            config["use_mirror"] = bool(configured_prefixes)
            config["mirror_candidates"] = configured_prefixes
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
            args=(job_id, qq, api_key, text_prompt, configured_prefixes),
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
            if job is not None:
                return job.to_dict()
        persisted = self._load_persisted_job(str(job_id))
        return persisted

    def _run_deploy(
        self,
        job_id: str,
        qq_account: str,
        api_key: str,
        prompt: str,
        mirror_candidates: list[str],
    ) -> None:
        job = self._jobs[job_id]
        # Keep a redactor available even if secrets.json was corrupted or is
        # temporarily unreadable.  The full redactor re-reads secrets on each
        # call; this fallback still masks the API key in startup errors.
        redact: Callable[[str], str] = lambda text: str(text).replace(api_key, "***") if api_key else str(text)
        try:
            redact = self._redactor(api_key)
            self._deploy_impl(job, qq_account, api_key, prompt, mirror_candidates, redact)
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
                self._persist_job(job, force=True)
        else:
            with self._lock:
                job.status = "success"
                job.progress = 100
                job.step = "部署完成"
                job.message = "AstrBot、SnowLuma 与 DeepSeek 已配置完成。下一步请打开 noVNC 扫码登录 QQ。"
                job.error = ""
                job.updated_at = self._now()
                self._append_log(job, "[完成] 部署成功。请打开 noVNC 扫码登录 QQ。")
                self._persist_job(job, force=True)
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
        mirror_candidates: list[str],
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
            use_mirror=bool(mirror_candidates),
            secrets_data=secrets_data,
            ports=self.store.load_config().get("ports") or None,
            mirror_prefix=(mirror_candidates[0] if mirror_candidates else ""),
            bind_address=self.bind_address,
            project_name=self.project_name,
            astrbot_container=self.astrbot_container,
            snowluma_container=self.snowluma_container,
            network_name=self.network_name,
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
                "use_mirror": bool(mirror_candidates),
                "mirror_candidates": list(mirror_candidates),
                "ports": ports,
                "model": MODEL_NAME,
                "deployed": False,
            }
        )
        self.store.save_config(config)
        self._install_bundled_plugin(Path(artifacts["astrbot_data"]), prompt, add_log)
        _, unique_default_error = self._ensure_unique_session_default()
        if unique_default_error:
            add_log("警告：" + unique_default_error)
        else:
            add_log("AstrBot 群聊会话将按 event.unified_msg_origin 共享（已确保 unique_session 默认关闭）。")
        add_log("本地配置已生成，包含随机 OneBot token 与 AstrBot 登录凭据。")
        candidate_prefixes = list(mirror_candidates) or [""]
        if mirror_candidates:
            add_log("镜像候选源：" + "、".join(candidate_prefixes[:6]))

        self._set_job(job, step="拉取 Docker 镜像", progress=24, message="正在拉取 AstrBot 与 SnowLuma 镜像，首次运行可能需要数分钟…")
        pull_result = None
        last_pull_error: Exception | None = None
        for index, prefix in enumerate(candidate_prefixes):
            if index > 0:
                astrbot_image, snowluma_image = image_names_for_prefix(prefix)
                set_image_names(artifacts["env"], astrbot_image, snowluma_image)
                add_log(f"镜像源不可用，自动切换到：{prefix or 'Docker Hub'}")
            try:
                add_log(f"开始从 {prefix or 'Docker Hub'} 拉取镜像…")
                pull_images = list(image_names_for_prefix(prefix))
                pull_result = self.docker.pull(on_line=add_log, images=pull_images)
            except DockerError as exc:
                last_pull_error = exc
                add_log(f"镜像源拉取失败（{prefix or 'Docker Hub'}）：{exc}")
                continue
            if mirror_candidates:
                config["docker_mirror"] = prefix
                self.store.save_config(config)
                add_log(f"镜像拉取成功，当前镜像源：{prefix}")
            add_log(self.docker._tail(pull_result.output, 2500) or "镜像拉取完成。")
            break

        if pull_result is None:
            suggestion = (
                "请检查网络，或在控制台配置其他 Docker 镜像节点后重试。"
                if mirror_candidates
                else "如果位于中国大陆，请在控制台配置并选择 Docker 镜像节点后重试。"
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
    @staticmethod
    def _snowluma_passwords_from_logs(logs: str) -> list[str]:
        clean = ANSI_RE.sub("", logs or "")
        patterns = (
            r"临时密码\s*[:：]\s*([A-Za-z0-9_@#%+\-]{4,128})",
            r"initial credentials:\s*user=\S+\s+password=([^\s]+)",
            r"password\s*[:：]\s*([A-Za-z0-9_@#%+\-]{4,128})",
        )
        found: list[str] = []
        for pattern in patterns:
            found.extend(
                str(item).strip()
                for item in re.findall(pattern, clean, flags=re.IGNORECASE)
                if str(item).strip()
            )
        deduped: list[str] = []
        for item in found:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def reset_snowluma_password(self) -> dict[str, Any]:
        """Reset SnowLuma WebUI login while preserving QQ/OneBot data."""

        config = self.store.load_config()
        if not config.get("deployed"):
            raise DeploymentError("尚未完成部署，不能重置 SnowLuma 密码。")
        secrets_data = self.store.load_secrets()

        before_passwords: set[str] = set()
        try:
            before_passwords = set(
                self._snowluma_passwords_from_logs(self.docker.logs("snowluma", 800))
            )
        except Exception:
            pass

        snowluma_root = self.data_dir / "data" / "snowluma"
        config_files: list[Path] = []
        if snowluma_root.exists():
            try:
                config_files = list(snowluma_root.rglob("webui.json"))
            except OSError:
                config_files = []
        expected = snowluma_root / "config" / "webui.json"
        if expected not in config_files:
            config_files.append(expected)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        for path in config_files:
            if path.exists():
                try:
                    path.replace(path.with_name(f"{path.name}.bak.{stamp}"))
                except OSError as exc:
                    raise DeploymentError(f"备份 SnowLuma webui.json 失败：{exc}") from exc

        # SnowLuma v2.x stores a scrypt hash (N=16384, r=8, p=1, 64-byte key).
        # Writing this file avoids depending on a one-time password that rotates
        # when the bootstrap password was never changed.
        password = f"Aa1!{generate_token(18)}"
        salt = os.urandom(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=16384,
            r=8,
            p=1,
            dklen=64,
        )
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        auth_state = {
            "passwordHash": digest.hex(),
            "passwordSalt": salt.hex(),
            "mustChangePassword": False,
            "generatedAt": now,
            "updatedAt": now,
        }
        expected.parent.mkdir(parents=True, exist_ok=True)
        try:
            chmod_best_effort(expected.parent, 0o700)
            payload = json.dumps(auth_state, ensure_ascii=False, indent=2) + "\n"
            fd = os.open(expected, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            chmod_best_effort(expected, 0o600)
        except OSError as exc:
            raise DeploymentError(f"写入 SnowLuma webui.json 失败：{exc}") from exc

        restart = self.docker._docker(
            ["restart", self.docker.SNOWLUMA_CONTAINER],
            timeout=120,
        )
        if not restart.ok:
            raise DockerError(
                "SnowLuma 重置密码后重启失败："
                + (self.docker._tail(restart.output, 1200) or f"退出码 {restart.returncode}")
            )

        # Some SnowLuma versions may reject the generated schema and regenerate
        # a one-time password. Only accept a credentials line that did not exist
        # before this reset, so an old log line cannot overwrite the new hash.
        time.sleep(3)
        actual_password = password
        try:
            after_passwords = self._snowluma_passwords_from_logs(
                self.docker.logs("snowluma", 400)
            )
            new_passwords = [item for item in after_passwords if item not in before_passwords]
            if new_passwords:
                actual_password = new_passwords[-1]
        except Exception:
            pass

        secrets_data["snowluma_password"] = actual_password
        secrets_data["snowluma_username"] = "admin"
        self.store.save_secrets(secrets_data)
        return {
            "ok": True,
            "username": "admin",
            "password": actual_password,
            "message": "SnowLuma WebUI 密码已重置，请用新密码登录。",
        }

    def _capture_snowluma_password(
        self,
        secrets_data: dict[str, Any] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> str:
        secrets_data = secrets_data or self.store.load_secrets()
        existing = str(secrets_data.get("snowluma_password") or "").strip()
        try:
            time.sleep(2)
            logs = self.docker.logs("snowluma", 800)
        except Exception as exc:  # noqa: BLE001 - optional display credential
            if log:
                log(f"暂时无法读取 SnowLuma 初始密码：{exc}")
            return ""
        passwords = self._snowluma_passwords_from_logs(logs)
        found = passwords[-1] if passwords else ""
        if found:
            if found != existing:
                secrets_data["snowluma_password"] = found
                self.store.save_secrets(secrets_data)
                if log:
                    log("已从最新日志同步 SnowLuma WebUI 密码到本地 secrets.json。")
            return found
        if existing:
            return existing
        if log:
            log("SnowLuma 初始密码尚未出现在日志中；可在完成页刷新状态，或查看 SnowLuma 日志。")
        return ""

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
        if os.name == "nt":
            try:
                return platform.platform()
            except OSError:
                return "Windows"
        try:
            with open("/etc/os-release", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("PRETTY_NAME="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
        return "未知系统"

    @staticmethod
    def _install_bundled_plugin(
        astrbot_data: Path,
        prompt: str,
        log: Callable[[str], None],
    ) -> None:
        """Copy the bundled QQ enhancer and seed its persona prompt."""

        source = Path(__file__).resolve().parent.parent / "plugins" / "qqbot_enhancer"
        if not source.is_dir():
            log("未找到内置 QQ 增强插件目录，跳过插件安装。")
            return
        target = astrbot_data / "plugins" / "qqbot_enhancer"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)
            DeployManager._write_enhancer_config(astrbot_data, prompt)
            log("已安装 QQ 增强插件：@必回 / 概率回复 / 图片理解 / 表情包。")
        except OSError as exc:
            log(f"安装 QQ 增强插件失败：{exc}")

    @staticmethod
    def _enhancer_schema_defaults() -> dict[str, Any]:
        """Read the bundled plugin schema defaults without third-party YAML."""

        schema_path = (
            Path(__file__).resolve().parent.parent
            / "plugins"
            / ENHANCER_PLUGIN_ID
            / "_conf_schema.json"
        )
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            schema = {}
        if not isinstance(schema, dict):
            return {}
        defaults: dict[str, Any] = {}
        for key, item in schema.items():
            if isinstance(item, dict) and "default" in item:
                defaults[str(key)] = item["default"]
        return defaults

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_private_json(path: Path, value: dict[str, Any]) -> None:
        """Atomically write a private JSON file (AstrBot may run as root)."""

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            chmod_best_effort(path.parent, 0o700)
        except OSError:
            pass
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(f".{path.name}.{secrets_token_fragment()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            chmod_best_effort(path, 0o600)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    @classmethod
    def _merged_enhancer_config(cls, loaded: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return schema defaults plus user values, preserving every user key."""

        merged = cls._enhancer_schema_defaults()
        if isinstance(loaded, dict):
            merged.update(loaded)
        # The schema default is authoritative only when the setting is absent.
        merged.setdefault("memory_mode", DEFAULT_MEMORY_MODE)
        return merged

    @staticmethod
    def _write_enhancer_config(astrbot_data: Path, prompt: str) -> None:
        """Write/update the plugin config without resetting user-tuned options."""

        config_dir = astrbot_data / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        target = config_dir / f"{ENHANCER_PLUGIN_ID}_config.json"
        loaded = DeployManager._read_json_object(target)
        config = DeployManager._merged_enhancer_config(loaded)
        config["system_prompt"] = str(prompt or "").strip()
        DeployManager._write_private_json(target, config)

    # ------------------------------------------------------------------
    # Memory mode and plugin selection
    # ------------------------------------------------------------------
    @property
    def astrbot_data_dir(self) -> Path:
        return self.data_dir / "data" / "astrbot"

    @property
    def enhancer_config_path(self) -> Path:
        return self.astrbot_data_dir / "config" / f"{ENHANCER_PLUGIN_ID}_config.json"

    @property
    def astrbot_cmd_config_path(self) -> Path:
        return self.astrbot_data_dir / "cmd_config.json"

    @staticmethod
    def _normalise_memory_mode(value: Any) -> str:
        mode = str(value or "").strip().lower()
        return mode if mode in MEMORY_MODES else DEFAULT_MEMORY_MODE

    def _migrate_enhancer_config_defaults(self) -> bool:
        """Create/fill enhancer config defaults without replacing user values.

        This runs when an old instance is first managed after an update.  The
        config is only created when the plugin is actually installed, avoiding
        stray files for instances that never used qqbot_enhancer.
        """

        plugin_dir = self.astrbot_data_dir / "plugins" / ENHANCER_PLUGIN_ID
        config_path = self.enhancer_config_path
        if not plugin_dir.is_dir() and not config_path.exists():
            return False
        loaded = self._read_json_object(config_path)
        merged = self._merged_enhancer_config(loaded)
        if not loaded:
            prompt = str(self.store.load_config().get("prompt") or "").strip()
            if prompt:
                merged["system_prompt"] = prompt
        if loaded != merged:
            self._write_private_json(config_path, merged)
            return True
        return False

    def _read_enhancer_config(self) -> dict[str, Any]:
        return self._merged_enhancer_config(self._read_json_object(self.enhancer_config_path))

    def _effective_memory_mode(self) -> tuple[str, str]:
        """Return (mode, error). The plugin config wins over the Studio mirror."""

        config = self._read_enhancer_config()
        raw = config.get("memory_mode")
        error = ""
        if raw is None:
            raw = self.store.load_config().get("memory_mode", DEFAULT_MEMORY_MODE)
        normalised = self._normalise_memory_mode(raw)
        if str(raw or "").strip().lower() not in MEMORY_MODES:
            error = f"memory_mode 值无效：{raw!r}；已按 direct 显示，不会自动覆盖原值。"
        return normalised, error

    def _unique_session_state(self) -> tuple[bool | None, str]:
        """Return AstrBot platform_settings.unique_session and a read error.

        AstrBot treats a missing value as ``False`` (one conversation per
        unified message origin).  Returning False for a missing config file is
        therefore the effective default, not a fabricated UI value.
        """

        data = self._read_json_object(self.astrbot_cmd_config_path)
        if not data:
            return False, ""
        platform_settings = data.get("platform_settings")
        if not isinstance(platform_settings, dict):
            return False, ""
        value = platform_settings.get("unique_session")
        if value is None:
            return False, ""
        if isinstance(value, bool):
            return value, ""
        return None, f"cmd_config.json 中 platform_settings.unique_session 不是布尔值：{value!r}"

    def _ensure_unique_session_default(self) -> tuple[bool, str]:
        """Add the v4.29 group-session default without changing an existing value."""

        path = self.astrbot_cmd_config_path
        if not path.exists():
            return False, ""
        data = self._read_json_object(path)
        if not data:
            return False, f"AstrBot 配置无法解析：{path}"
        platform_settings = data.get("platform_settings")
        if not isinstance(platform_settings, dict):
            platform_settings = {}
            data["platform_settings"] = platform_settings
        if "unique_session" in platform_settings:
            return False, ""
        platform_settings["unique_session"] = False
        try:
            self._write_private_json(path, data)
        except OSError as exc:
            return False, f"无法写入 AstrBot 共享会话默认配置：{exc}"
        return True, ""

    def _migrate_unique_session_for_shared_memory(self) -> tuple[bool, str]:
        """Persist unique_session=false for group-wide AstrBot memory.

        AstrBot v4.29 uses ``unique_session`` to split group context per
        member.  The memory mode requested by this product is one conversation
        per ``event.unified_msg_origin``: groups share one conversation and
        private chats remain per-user because their UMO differs.  Other
        platform settings are preserved byte-for-byte at the JSON value level.
        """

        path = self.astrbot_cmd_config_path
        if not path.exists():
            # Not an error before the first deployment: the generated
            # cmd_config will add unique_session=false at deploy time.
            return False, ""
        data = self._read_json_object(path)
        if not data:
            return False, f"AstrBot 配置无法解析：{path}"
        platform_settings = data.get("platform_settings")
        if not isinstance(platform_settings, dict):
            platform_settings = {}
            data["platform_settings"] = platform_settings
        if platform_settings.get("unique_session") is False:
            return False, ""
        platform_settings["unique_session"] = False
        try:
            self._write_private_json(path, data)
        except OSError as exc:
            return False, f"无法写入 AstrBot 共享会话配置：{exc}"
        return True, ""

    def _memory_api_client(
        self,
        *,
        timeout: float = 8.0,
        wait_for_ready: bool = True,
    ) -> AstrBotClient | None:
        config = self.store.load_config()
        if not config.get("deployed"):
            return None
        ports = config.get("ports") or {}
        if not isinstance(ports, dict) or not ports.get("astrbot_webui"):
            return None
        secrets_data = self.store.ensure_secrets()
        client = self._api_client_factory(
            f"http://127.0.0.1:{int(ports['astrbot_webui'])}",
            str(secrets_data.get("astrbot_username") or ""),
            str(secrets_data.get("astrbot_password") or ""),
        )
        if wait_for_ready:
            waiter = getattr(client, "wait_until_ready", None)
            if callable(waiter):
                waiter(timeout=timeout)
        return client

    @staticmethod
    def _api_request(
        client: AstrBotClient,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Call a verified AstrBot v4.29 dashboard route through its client.

        ``AstrBotClient`` intentionally exposes only the persona helper
        methods, so plugin operations are implemented here using the same
        authenticated request primitive.  No fake/private AstrBot Python API
        is invoked: these are the documented dashboard HTTP routes.
        """

        login = getattr(client, "login", None)
        request = getattr(client, "_request", None)
        if not callable(login) or not callable(request):
            raise RuntimeError("当前 AstrBot 客户端不支持认证请求")
        token = login()
        response = request(method, path, payload=payload, token=token, timeout=timeout)
        if not isinstance(response, dict):
            raise RuntimeError("AstrBot 返回格式异常")
        return response

    @staticmethod
    def _api_response_data(response: dict[str, Any]) -> Any:
        return response.get("data", response)

    def _list_plugins_via_api(self, client: AstrBotClient) -> dict[str, Any]:
        """Call the real v4.29 plugin list route, with legacy fallback."""

        last_error: Exception | None = None
        for path in ("/api/v1/plugins", "/api/plugin/get"):
            try:
                response = self._api_request(client, "GET", path, timeout=20)
            except Exception as exc:  # noqa: BLE001 - route/version fallback
                last_error = exc
                if int(getattr(exc, "status_code", 0) or 0) in (401, 403):
                    break
                continue
            data = self._api_response_data(response)
            failed: Any = response.get("message") or ""
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                candidate = data.get("plugins", data.get("items"))
                if isinstance(candidate, list):
                    records = candidate
                elif candidate is None:
                    records = []
                    for plugin_id, value in data.items():
                        if isinstance(value, dict):
                            records.append({"name": plugin_id, **value})
                        elif isinstance(value, bool):
                            records.append({"name": plugin_id, "activated": value})
                else:
                    records = []
                if not failed:
                    failed = data.get("failed_plugins") or data.get("failed") or ""
            else:
                records = []
            return {
                "plugins": [dict(item) for item in records if isinstance(item, dict)],
                "failed": failed,
                "route": path,
                "message": str(response.get("message") or ""),
            }
        if last_error is not None:
            raise RuntimeError(f"AstrBot 插件列表读取失败：{last_error}") from last_error
        raise RuntimeError("AstrBot 插件列表读取失败：没有可用的插件接口")

    def _get_plugin_config_via_api(self, client: AstrBotClient, plugin_id: str) -> dict[str, Any]:
        """Read a plugin's complete effective config from AstrBot's API."""

        quoted = urlquote(plugin_id, safe="")
        last_error: Exception | None = None
        for path in (
            f"/api/v1/plugins/config?plugin_id={quoted}",
            f"/api/v1/plugins/{quoted}/config/schema",
        ):
            try:
                response = self._api_request(client, "GET", path, timeout=20)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if int(getattr(exc, "status_code", 0) or 0) in (401, 403):
                    break
                continue
            data = self._api_response_data(response)
            if isinstance(data, dict):
                config = data.get("config")
                if isinstance(config, dict):
                    return dict(config)
                if data and not {"metadata", "plugin_name", "log_level", "i18n"}.intersection(data):
                    return dict(data)
            last_error = RuntimeError("AstrBot 返回中没有可用的插件配置")
        if last_error is not None:
            raise RuntimeError(f"AstrBot 插件配置读取失败：{last_error}") from last_error
        raise RuntimeError("AstrBot 插件配置读取失败")

    def _save_plugin_config_via_api(
        self,
        client: AstrBotClient,
        plugin_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Save plugin config using AstrBot's hot-reload endpoint."""

        quoted = urlquote(plugin_id, safe="")
        attempts = (
            (
                "/api/v1/plugins/config",
                {"plugin_id": plugin_id, "config": config},
            ),
            (
                f"/api/v1/plugins/{quoted}/config",
                {"config": config},
            ),
        )
        last_error: Exception | None = None
        for path, payload in attempts:
            try:
                response = self._api_request(
                    client,
                    "PUT",
                    path,
                    payload=payload,
                    timeout=30,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if int(getattr(exc, "status_code", 0) or 0) in (401, 403):
                    break
                continue
            return {
                "plugin_id": plugin_id,
                "message": str(
                    response.get("message")
                    or "插件配置已保存，AstrBot 正在热重载插件。"
                ),
                "route": path,
            }
        if last_error is not None:
            raise RuntimeError(f"AstrBot 插件配置保存失败：{last_error}") from last_error
        raise RuntimeError("AstrBot 插件配置保存失败：没有可用的接口")

    def _set_plugin_enabled_via_api(
        self,
        client: AstrBotClient,
        plugin_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        """Toggle a plugin using v4.29 PATCH or legacy on/off routes."""

        desired = bool(enabled)
        attempts = (
            (
                "PATCH",
                "/api/v1/plugins/enabled",
                {"plugin_id": plugin_id, "enabled": desired},
            ),
            (
                "POST",
                "/api/plugin/on" if desired else "/api/plugin/off",
                {"name": plugin_id},
            ),
        )
        last_error: Exception | None = None
        for method, path, payload in attempts:
            try:
                response = self._api_request(
                    client,
                    method,
                    path,
                    payload=payload,
                    timeout=30,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if int(getattr(exc, "status_code", 0) or 0) in (401, 403):
                    break
                # A 400/409 is an operation error from the real endpoint.
                if int(getattr(exc, "status_code", 0) or 0) not in (404, 405, 0):
                    break
                continue
            return {
                "plugin_id": plugin_id,
                "enabled": desired,
                "message": str(
                    response.get("message")
                    or ("启用成功。" if desired else "停用成功。")
                ),
                "data": response.get("data", {}),
                "route": path,
            }
        if last_error is not None:
            raise RuntimeError(
                f"AstrBot 插件{'启用' if desired else '禁用'}失败：{last_error}"
            ) from last_error
        raise RuntimeError("AstrBot 插件状态修改失败：没有可用的插件接口")

    def get_memory_mode(self) -> dict[str, Any]:
        """Return the effective per-instance memory mode and its boundaries."""

        mode, read_error = self._effective_memory_mode()
        unique_session, unique_error = self._unique_session_state()
        scope = "session" if mode == "astrbot" else "none"
        if mode == "astrbot":
            message = (
                "astrbot 模式使用 AstrBot 当前会话：同一群共享一条会话记忆，"
                "私聊按用户会话隔离；/reset 清空当前会话历史，/new 切换到新会话。"
            )
        else:
            message = (
                "direct 模式不携带跨轮会话历史；/reset 与 /new 不会改变 direct 的单轮行为。"
            )
        if mode == "astrbot" and unique_session is True:
            message += " 注意：AstrBot 当前启用了 unique_session，群成员记忆仍会按成员隔离；保存一次 astrbot 模式会迁移为群共享。"
        errors = [item for item in (read_error, unique_error) if item]
        return {
            "ok": True,
            "mode": mode,
            "memory_mode": mode,
            "options": list(MEMORY_MODES),
            "memory_scope": scope,
            "group_session_key": "event.unified_msg_origin" if mode == "astrbot" else "",
            "private_session_key": "event.unified_msg_origin" if mode == "astrbot" else "",
            "unique_session": unique_session,
            "group_memory_shared": (unique_session is False) if mode == "astrbot" else None,
            "restart_required": False,
            "config_path": str(self.enhancer_config_path),
            "astrbot_config_path": str(self.astrbot_cmd_config_path),
            "behavior": {
                "direct": "只发送系统提示词和当前消息，不读取 AstrBot 历史。",
                "astrbot": "交给 AstrBot 默认 Agent，使用当前统一消息来源的会话历史、人格和 Agent 能力。",
                "group": "同一群的消息共享 event.unified_msg_origin 对应的会话。",
                "private": "私聊按用户各自的 event.unified_msg_origin 会话保存。",
                "reset": "/reset 清空当前会话历史，保留会话与人格。",
                "new": "/new 切换到一个新会话，旧历史仍保存在 AstrBot 数据库中。",
                "persistence": "AstrBot 历史存储在其本机 SQLite 数据卷中，跨 AstrBot 重启保留；它不是跨群共享的永久知识库。",
            },
            "message": message,
            "error": "；".join(errors),
        }

    def set_memory_mode(self, mode: str) -> dict[str, Any]:
        """Persist and (when possible) hot-reload the enhancer memory mode."""

        value = str(mode or "").strip().lower()
        if value not in MEMORY_MODES:
            raise DeploymentError("记忆模式只能是 direct 或 astrbot。")

        loaded = self._read_json_object(self.enhancer_config_path)
        merged = self._merged_enhancer_config(loaded)
        # Preserve every user-tuned value.  The explicit mode selected here is
        # the only field intentionally overwritten.
        merged["memory_mode"] = value

        applied_live = False
        api_error = ""
        api_message = ""
        if self.store.load_config().get("deployed"):
            try:
                client = self._memory_api_client(timeout=8)
                if client is not None:
                    live_config = self._get_plugin_config_via_api(
                        client, ENHANCER_PLUGIN_ID
                    )
                    if isinstance(live_config, dict):
                        # AstrBot's live config is authoritative while it is
                        # running; merge it without dropping local defaults.
                        merged = {**merged, **live_config}
                    merged["memory_mode"] = value
                    result = self._save_plugin_config_via_api(
                        client, ENHANCER_PLUGIN_ID, merged
                    )
                    applied_live = True
                    if isinstance(result, dict):
                        api_message = str(result.get("message") or "")
            except Exception as exc:  # noqa: BLE001 - degrade to offline write
                api_error = self.redact_sensitive_text(str(exc))

        try:
            self._write_private_json(self.enhancer_config_path, merged)
        except OSError as exc:
            raise DeploymentError(f"无法保存 QQ 增强插件配置：{exc}") from exc

        config = self.store.load_config()
        # Additive migration for existing instance configs: only this mirror is
        # updated, and other values are retained.
        config["memory_mode"] = value
        config["memory_mode_updated_at"] = utc_now()
        self.store.save_config(config)

        unique_changed = False
        unique_error = ""
        if value == "astrbot":
            unique_changed, unique_error = self._migrate_unique_session_for_shared_memory()

        restart_required = bool(unique_changed or not applied_live)
        messages: list[str] = []
        if applied_live:
            messages.append(api_message or "AstrBot 已热重载插件，记忆模式立即生效。")
        else:
            messages.append("记忆模式已保存到本地配置；AstrBot 运行后会自动使用，建议重启 AstrBot 使其立即生效。")
        if unique_changed:
            messages.append("已关闭 AstrBot 的 unique_session，使同一群共享会话；重启 AstrBot 后生效。")
        if value == "astrbot" and not self.astrbot_cmd_config_path.exists():
            messages.append("尚未生成 AstrBot cmd_config.json；部署时会补上共享会话默认配置。")
        if api_error:
            messages.append(f"AstrBot 在线配置接口暂不可用：{api_error}")
        if unique_error:
            messages.append(unique_error)
        errors = [item for item in (api_error, unique_error) if item]
        # If we could not ensure the required group-session migration, report a
        # non-fatal API error so the console can display the security boundary
        # without hiding the fact that the mode file itself was saved.
        ok = not bool(unique_error)
        return {
            "ok": ok,
            "mode": value,
            "memory_mode": value,
            "options": list(MEMORY_MODES),
            "memory_scope": "session" if value == "astrbot" else "none",
            "unique_session": self._unique_session_state()[0],
            "group_memory_shared": (self._unique_session_state()[0] is False)
            if value == "astrbot"
            else None,
            "applied_live": applied_live,
            "restart_required": restart_required,
            "config_path": str(self.enhancer_config_path),
            "astrbot_config_path": str(self.astrbot_cmd_config_path),
            "behavior": self.get_memory_mode().get("behavior", {}),
            "message": " ".join(item for item in messages if item),
            "error": "；".join(errors),
        }

    @staticmethod
    def _parse_metadata_yaml(text: str) -> dict[str, str]:
        """Parse the tiny scalar subset used by AstrBot metadata.yaml."""

        result: dict[str, str] = {}
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                result[key] = value
        return result

    def _inactivated_plugin_paths(self) -> tuple[set[str], str]:
        """Read AstrBot's persisted disabled-plugin list without its library."""

        db_path = self.astrbot_data_dir / "data_v4.db"
        if not db_path.exists():
            return set(), ""
        try:
            connection = sqlite3.connect(str(db_path), timeout=3)
            try:
                row = connection.execute(
                    "SELECT value FROM preferences WHERE scope = ? AND scope_id = ? AND key = ?",
                    ("global", "global", "inactivated_plugins"),
                ).fetchone()
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            return set(), f"无法读取 AstrBot 插件禁用状态：{exc}"
        if not row:
            return set(), ""
        raw = row[0]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return set(), "AstrBot 插件禁用状态不是有效 JSON"
        if isinstance(value, dict):
            value = value.get("val", [])
        if not isinstance(value, list):
            return set(), ""
        return {str(item) for item in value if item}, ""

    def _list_local_plugins(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Filesystem fallback when AstrBot is stopped or unreachable."""

        plugin_root = self.astrbot_data_dir / "plugins"
        inactive, inactive_error = self._inactivated_plugin_paths()
        errors = [inactive_error] if inactive_error else []
        plugins: list[dict[str, Any]] = []
        if not plugin_root.is_dir():
            return plugins, errors
        try:
            entries = sorted(plugin_root.iterdir(), key=lambda item: item.name.lower())
        except OSError as exc:
            return plugins, [*errors, f"无法读取插件目录：{exc}"]
        for path in entries:
            if not path.is_dir():
                continue
            try:
                metadata_text = ""
                for filename in ("metadata.yaml", "metadata.yml"):
                    candidate = path / filename
                    if candidate.exists():
                        metadata_text = candidate.read_text(encoding="utf-8", errors="replace")
                        break
                metadata = self._parse_metadata_yaml(metadata_text)
            except OSError as exc:
                plugins.append(
                    {
                        "id": path.name,
                        "name": path.name,
                        "enabled": None,
                        "version": "",
                        "description": "",
                        "source": "installed",
                        "restart_required": False,
                        "error": f"读取插件元数据失败：{exc}",
                    }
                )
                continue
            module_prefix = f"data.plugins.{path.name}."
            enabled: bool | None = None
            enabled_source = "unknown"
            if not inactive_error and (plugin_root.parent / "data_v4.db").exists():
                enabled = not any(item.startswith(module_prefix) for item in inactive)
                enabled_source = "shared_preferences"
            plugins.append(
                {
                    "id": str(metadata.get("name") or path.name),
                    "name": str(metadata.get("display_name") or metadata.get("name") or path.name),
                    "enabled": enabled,
                    "version": str(metadata.get("version") or ""),
                    "description": str(metadata.get("desc") or metadata.get("description") or ""),
                    "source": "installed",
                    "reserved": False,
                    "root_dir_name": path.name,
                    "restart_required": False,
                    "enabled_source": enabled_source,
                    "error": "",
                }
            )
        return plugins, errors

    @staticmethod
    def _normalise_plugin_record(record: dict[str, Any]) -> dict[str, Any]:
        plugin_id = str(record.get("id") or record.get("plugin_id") or record.get("name") or "").strip()
        display_name = str(record.get("display_name") or record.get("name") or plugin_id).strip()
        if "activated" in record:
            enabled = bool(record.get("activated"))
        elif "enabled" in record:
            enabled = bool(record.get("enabled"))
        else:
            enabled = None
        reserved = bool(record.get("reserved"))
        return {
            "id": plugin_id,
            "plugin_id": plugin_id,
            "name": display_name or plugin_id,
            "enabled": enabled,
            "version": str(record.get("version") or ""),
            "description": str(record.get("desc") or record.get("description") or ""),
            "source": "builtin" if reserved else "installed",
            "reserved": reserved,
            "root_dir_name": str(record.get("root_dir_name") or ""),
            "restart_required": False,
            "error": str(record.get("error") or ""),
        }

    def get_plugins(self) -> dict[str, Any]:
        """List plugins using AstrBot's API, with a safe filesystem fallback."""

        local_plugins, local_errors = self._list_local_plugins()
        config = self.store.load_config()
        if not config.get("deployed"):
            return {
                "ok": False,
                "plugins": local_plugins,
                "items": local_plugins,
                "api_available": False,
                "cached": False,
                "restart_required": False,
                "message": "实例尚未部署，仅显示本地插件目录；AstrBot 在线启停暂不可用。",
                "error": "；".join(local_errors),
            }
        api_error = ""
        failed: Any = ""
        route = ""
        try:
            client = self._memory_api_client(timeout=8, wait_for_ready=False)
            if client is None:
                raise RuntimeError("AstrBot 尚未启动")
            payload = self._list_plugins_via_api(client)
            if not isinstance(payload, dict):
                raise RuntimeError("AstrBot 插件列表返回格式异常")
            raw_plugins = payload.get("plugins")
            if not isinstance(raw_plugins, list):
                raise RuntimeError("AstrBot 插件列表缺少 plugins 数组")
            plugins = [
                self._normalise_plugin_record(item)
                for item in raw_plugins
                if isinstance(item, dict)
            ]
            failed = payload.get("failed") or ""
            route = str(payload.get("route") or "")
            api_message = str(payload.get("message") or "")
            message = "已从 AstrBot v4.29 插件接口读取插件状态。"
            if api_message and str(failed).strip() and str(failed) != api_message:
                message += " " + api_message
            return {
                "ok": True,
                "plugins": plugins,
                "items": plugins,
                "failed": failed,
                "api_available": True,
                "route": route,
                "cached": False,
                "restart_required": False,
                "message": message,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001 - still return useful local data
            api_error = self.redact_sensitive_text(str(exc) or exc.__class__.__name__)

        errors = [*local_errors]
        if api_error:
            errors.append(f"AstrBot 插件接口不可用：{api_error}")
        return {
            "ok": False,
            "plugins": local_plugins,
            "items": local_plugins,
            "failed": failed,
            "api_available": False,
            "route": route,
            "cached": False,
            "restart_required": False,
            "message": "无法连接 AstrBot，已降级显示本地插件目录；启动实例后可实时启停插件。",
            "error": "；".join(errors),
        }

    # Conventional alias for callers that prefer the noun.
    list_installed_plugins = get_plugins

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable one plugin via AstrBot's real runtime API."""

        value = str(plugin_id or "").strip()
        if not value:
            raise DeploymentError("插件 ID 不能为空。")
        config = self.store.load_config()
        if not config.get("deployed"):
            raise DeploymentError("实例尚未部署，不能启停插件。")
        try:
            client = self._memory_api_client(timeout=10)
            if client is None:
                raise DeploymentError("AstrBot WebUI 端口尚未配置，不能启停插件。")
            result = self._set_plugin_enabled_via_api(client, value, bool(enabled))
        except DeploymentError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate to an actionable error
            detail = self.redact_sensitive_text(str(exc) or exc.__class__.__name__)
            raise DeploymentError(
                f"插件{'启用' if enabled else '禁用'}失败：{detail}。请确认 AstrBot 正在运行，"
                "并在其 WebUI 插件页检查插件是否加载成功。"
            ) from exc
        plugin = self._normalise_plugin_record(
            {
                "id": value,
                "enabled": bool(enabled),
                "activated": bool(enabled),
            }
        )
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or "")
            # No disk cache exists; the authoritative state was changed live.
        return {
            "ok": True,
            "plugin": plugin,
            "plugins": [plugin],
            "api_available": True,
            "restart_required": False,
            "message": message or ("插件已启用。" if enabled else "插件已禁用。"),
            "error": "",
        }

    # ------------------------------------------------------------------
    # Plugin parameter get/set
    # ------------------------------------------------------------------
    def _plugin_schema_path(self, plugin_id: str) -> Path:
        return self.astrbot_data_dir / "plugins" / plugin_id / "_conf_schema.json"

    def _plugin_config_path(self, plugin_id: str) -> Path:
        return self.astrbot_data_dir / "config" / f"{plugin_id}_config.json"

    @staticmethod
    def _plugin_config_failure(
        plugin_id: str,
        message: str,
        *,
        detail: str = "",
        schema: dict[str, Any] | None = None,
        values: dict[str, Any] | None = None,
        config_path: str = "",
        source: str = "none",
        api_available: bool = False,
        restart_required: bool = False,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "plugin_id": str(plugin_id or ""),
            "schema": schema or {},
            "values": values or {},
            "config_path": config_path,
            "source": source,
            "api_available": bool(api_available),
            "restart_required": bool(restart_required),
            "message": str(message or "插件配置操作失败。"),
            "error": str(detail or message or "插件配置操作失败。"),
        }

    def _load_plugin_schema(self, plugin_id: str) -> tuple[dict[str, Any], str]:
        """Load a plugin's local _conf_schema.json without third-party YAML."""

        if not PLUGIN_ID_RE.fullmatch(str(plugin_id or "")):
            return {}, "plugin_id 无效，只允许字母、数字、点、下划线和连字符。"
        plugin_dir = self.astrbot_data_dir / "plugins" / plugin_id
        if plugin_dir.is_symlink():
            return {}, "插件目录不能是符号链接。"
        schema_path = self._plugin_schema_path(plugin_id)
        try:
            parsed = json.loads(schema_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, f"找不到插件 {plugin_id} 的 _conf_schema.json。"
        except OSError as exc:
            return {}, f"无法读取插件 {plugin_id} 的配置 schema：{exc}"
        except ValueError as exc:
            return {}, f"插件 {plugin_id} 的配置 schema 不是有效 JSON：{exc}"
        if not isinstance(parsed, dict) or not parsed:
            return {}, f"插件 {plugin_id} 的配置 schema 必须是非空 JSON object。"
        schema = {
            str(key): dict(item)
            for key, item in parsed.items()
            if isinstance(item, dict)
        }
        if not schema:
            return {}, f"插件 {plugin_id} 的配置 schema 没有可用字段。"
        return schema, ""

    def _read_local_plugin_config(self, plugin_id: str) -> tuple[dict[str, Any], str, bool]:
        path = self._plugin_config_path(plugin_id)
        if not path.exists():
            return {}, "", False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            return {}, f"无法读取本地插件配置：{exc}", True
        except ValueError as exc:
            return {}, f"本地插件配置不是有效 JSON：{exc}", True
        if not isinstance(raw, dict):
            return {}, "本地插件配置顶层必须是 JSON object。", True
        return raw, "", True

    @staticmethod
    def _plugin_defaults(schema: dict[str, Any]) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for key, item in schema.items():
            if isinstance(item, dict) and "default" in item:
                defaults[str(key)] = copy.deepcopy(item["default"])
        return defaults

    @staticmethod
    def _plugin_numeric_limits(item: dict[str, Any]) -> tuple[float | None, float | None]:
        slider = item.get("slider")
        minimum: Any = None
        maximum: Any = None
        if isinstance(slider, dict):
            minimum = slider.get("min", slider.get("minimum"))
            maximum = slider.get("max", slider.get("maximum"))
        if minimum is None:
            minimum = item.get("min", item.get("minimum"))
        if maximum is None:
            maximum = item.get("max", item.get("maximum"))
        try:
            min_value = float(minimum) if minimum is not None else None
        except (TypeError, ValueError):
            min_value = None
        try:
            max_value = float(maximum) if maximum is not None else None
        except (TypeError, ValueError):
            max_value = None
        return min_value, max_value

    def _validate_plugin_config_value(
        self,
        field: str,
        value: Any,
        item: dict[str, Any],
    ) -> Any:
        """Validate one value against the AstrBot plugin schema subset."""

        if _plugin_field_is_sensitive(field, item):
            raise ValueError("请求包含 Key/token/password/secret 类敏感字段，已拒绝处理。")
        item_type = str(item.get("type") or "").strip().lower()
        if not item_type and item.get("options"):
            item_type = "string"
        normalized = value

        if item_type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"字段 {field} 必须是布尔值 true/false。")
        elif item_type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"字段 {field} 必须是整数。")
        elif item_type in {"float", "number"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"字段 {field} 必须是数字。")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValueError(f"字段 {field} 必须是有限数字。")
        elif item_type in {"string", "text"}:
            if not isinstance(value, str):
                raise ValueError(f"字段 {field} 必须是字符串。")
            max_length = item.get("max_length", PLUGIN_CONFIG_MAX_STRING_LENGTH)
            try:
                max_length = int(max_length)
            except (TypeError, ValueError):
                max_length = PLUGIN_CONFIG_MAX_STRING_LENGTH
            if len(value) > max_length:
                raise ValueError(f"字段 {field} 超过最大长度 {max_length}。")
        elif item_type == "list":
            if not isinstance(value, list):
                raise ValueError(f"字段 {field} 必须是数组。")
            if len(value) > PLUGIN_CONFIG_MAX_LIST_ITEMS:
                raise ValueError(f"字段 {field} 数组最多 {PLUGIN_CONFIG_MAX_LIST_ITEMS} 项。")
            item_schema = item.get("items")
            if isinstance(item_schema, dict):
                validated_items = [
                    self._validate_plugin_config_value(f"{field}[{index}]", entry, item_schema)
                    for index, entry in enumerate(value)
                ]
                normalized = validated_items
        else:
            raise ValueError(f"字段 {field} 的 schema 类型不受支持：{item_type or 'unknown'}。")

        options = item.get("options")
        if isinstance(options, list) and options:
            if normalized not in options:
                allowed = "、".join(str(option) for option in options)
                raise ValueError(f"字段 {field} 的值不在允许选项中：{allowed}。")

        if item_type in {"int", "float", "number"}:
            minimum, maximum = self._plugin_numeric_limits(item)
            numeric = float(normalized)
            if minimum is not None and numeric < minimum:
                raise ValueError(f"字段 {field} 不能小于 {minimum:g}。")
            if maximum is not None and numeric > maximum:
                raise ValueError(f"字段 {field} 不能大于 {maximum:g}。")
        return normalized

    def _live_plugin_config(self, plugin_id: str) -> tuple[dict[str, Any] | None, str, bool]:
        """Try the real AstrBot config endpoint, returning a safe warning."""

        config = self.store.load_config()
        if not config.get("deployed"):
            return None, "", False
        try:
            client = self._memory_api_client(timeout=6, wait_for_ready=False)
            if client is None:
                return None, "", False
            live = self._get_plugin_config_via_api(client, plugin_id)
            if not isinstance(live, dict):
                raise RuntimeError("AstrBot 插件配置返回格式异常")
            return live, "", True
        except Exception as exc:  # noqa: BLE001 - local fallback is intentional
            return None, self.redact_sensitive_text(str(exc) or exc.__class__.__name__), False

    def get_plugin_config(self, plugin_id: str) -> dict[str, Any]:
        """Read one plugin's schema and effective values safely."""

        requested_id = str(plugin_id or "").strip()
        with self._lock:
            try:
                schema, schema_error = self._load_plugin_schema(requested_id)
                if schema_error:
                    return self._plugin_config_failure(
                        requested_id,
                        "无法读取插件配置 schema。",
                        detail=schema_error,
                    )
                config_path = str(self._plugin_config_path(requested_id))
                local_config, local_error, local_exists = self._read_local_plugin_config(requested_id)
                if local_error:
                    return self._plugin_config_failure(
                        requested_id,
                        "本地插件配置损坏，已停止读取以避免覆盖。",
                        detail=local_error,
                        schema=_public_plugin_schema(schema),
                        config_path=config_path,
                    )

                merged = self._plugin_defaults(schema)
                merged.update(local_config)
                live_config, api_error, api_available = self._live_plugin_config(requested_id)
                if live_config is not None:
                    merged.update(live_config)
                public_schema = _public_plugin_schema(schema)
                public_values = _public_plugin_values(merged, schema)
                source = "astrbot" if api_available else ("local" if local_exists else "schema")
                message = (
                    "已从 AstrBot 在线接口读取插件配置，并合并本地值。"
                    if api_available
                    else "AstrBot 不在线；显示本地插件配置和 schema 默认值。"
                )
                return {
                    "ok": True,
                    "plugin_id": requested_id,
                    "schema": public_schema,
                    "values": public_values,
                    "config_path": config_path,
                    "source": source,
                    "api_available": bool(api_available),
                    "restart_required": False,
                    "message": message,
                    "error": "",
                    "api_error": api_error,
                }
            except Exception as exc:  # noqa: BLE001 - optional endpoint isolation
                detail = self.redact_sensitive_text(str(exc) or exc.__class__.__name__)
                return self._plugin_config_failure(
                    requested_id,
                    "读取插件配置失败；主项目和其它插件不受影响。",
                    detail=detail,
                )

    def set_plugin_config(
        self,
        plugin_id: str,
        values: Any,
        restart: bool = False,
    ) -> dict[str, Any]:
        """Validate and persist plugin values, preserving unknown user fields."""

        requested_id = str(plugin_id or "").strip()
        with self._lock:
            try:
                schema, schema_error = self._load_plugin_schema(requested_id)
                config_path_obj = self._plugin_config_path(requested_id)
                config_path = str(config_path_obj)
                public_schema = _public_plugin_schema(schema)
                if schema_error:
                    return self._plugin_config_failure(
                        requested_id,
                        "无法读取插件配置 schema。",
                        detail=schema_error,
                    )
                if not isinstance(values, dict):
                    return self._plugin_config_failure(
                        requested_id,
                        "values 必须是 JSON object。",
                        detail="请求体 values 必须是 JSON object。",
                        schema=public_schema,
                        config_path=config_path,
                    )
                if len(values) > PLUGIN_CONFIG_MAX_FIELDS:
                    return self._plugin_config_failure(
                        requested_id,
                        f"一次最多保存 {PLUGIN_CONFIG_MAX_FIELDS} 个插件字段。",
                        schema=public_schema,
                        config_path=config_path,
                    )
                try:
                    encoded_size = len(
                        json.dumps(values, ensure_ascii=False).encode("utf-8")
                    )
                except (TypeError, ValueError):
                    return self._plugin_config_failure(
                        requested_id,
                        "values 包含无法序列化的值。",
                        schema=public_schema,
                        config_path=config_path,
                    )
                if encoded_size > PLUGIN_CONFIG_MAX_REQUEST_BYTES:
                    return self._plugin_config_failure(
                        requested_id,
                        "插件配置请求体过大，请减少保存的字段或文本长度。",
                        schema=public_schema,
                        config_path=config_path,
                    )

                validated_values: dict[str, Any] = {}
                for field, value in values.items():
                    field_name = str(field)
                    item = schema.get(field_name)
                    if _plugin_field_is_sensitive(field_name, item if isinstance(item, dict) else None):
                        return self._plugin_config_failure(
                            requested_id,
                            "请求包含 Key/token/password/secret 类敏感字段，已拒绝保存。",
                            detail="敏感字段不允许通过插件参数接口读写。",
                            schema=public_schema,
                            config_path=config_path,
                        )
                    if not isinstance(item, dict):
                        return self._plugin_config_failure(
                            requested_id,
                            f"字段 {field_name} 不在插件 schema 中，拒绝保存。",
                            schema=public_schema,
                            config_path=config_path,
                        )
                    try:
                        validated_values[field_name] = self._validate_plugin_config_value(
                            field_name, value, item
                        )
                    except ValueError as exc:
                        return self._plugin_config_failure(
                            requested_id,
                            "插件配置校验失败，未写入任何修改。",
                            detail=self.redact_sensitive_text(str(exc)),
                            schema=public_schema,
                            config_path=config_path,
                        )

                local_config, local_error, _local_exists = self._read_local_plugin_config(requested_id)
                if local_error:
                    return self._plugin_config_failure(
                        requested_id,
                        "本地插件配置损坏，已拒绝写入以避免覆盖。",
                        detail=local_error,
                        schema=public_schema,
                        config_path=config_path,
                    )
                # Keep unknown user fields from the existing local config.
                local_merged = self._plugin_defaults(schema)
                local_merged.update(local_config)
                local_merged.update(validated_values)
                try:
                    self._write_private_json(config_path_obj, local_merged)
                except OSError as exc:
                    return self._plugin_config_failure(
                        requested_id,
                        "无法安全写入本地插件配置。",
                        detail=self.redact_sensitive_text(str(exc)),
                        schema=public_schema,
                        config_path=config_path,
                    )

                api_available = False
                api_error = ""
                api_message = ""
                final_config = local_merged
                if self.store.load_config().get("deployed"):
                    try:
                        client = self._memory_api_client(timeout=8, wait_for_ready=False)
                        if client is not None:
                            live = self._get_plugin_config_via_api(client, requested_id)
                            if not isinstance(live, dict):
                                raise RuntimeError("AstrBot 插件配置返回格式异常")
                            api_merged = self._plugin_defaults(schema)
                            api_merged.update(local_config)
                            api_merged.update(live)
                            api_merged.update(validated_values)
                            save_result = self._save_plugin_config_via_api(
                                client, requested_id, api_merged
                            )
                            api_available = True
                            final_config = api_merged
                            if isinstance(save_result, dict):
                                api_message = str(save_result.get("message") or "")
                    except Exception as exc:  # noqa: BLE001 - local save already succeeded
                        api_error = self.redact_sensitive_text(
                            str(exc) or exc.__class__.__name__
                        )

                public_values = _public_plugin_values(final_config, schema)
                restart_required = bool(not api_available or restart)
                source = "astrbot" if api_available else "local"
                if api_available:
                    message = (
                        api_message
                        or "插件配置已通过 AstrBot 真实接口保存并热重载，无需重启。"
                    )
                    if restart:
                        message += " 已请求保存后重启提示。"
                else:
                    message = (
                        "插件配置已保存到本地（0600）。AstrBot 当前不在线，"
                        "需重启 AstrBot 后生效。"
                    )
                    if api_error:
                        message += " 在线接口警告：" + api_error
                return {
                    "ok": True,
                    "plugin_id": requested_id,
                    "schema": public_schema,
                    "values": public_values,
                    "config_path": config_path,
                    "source": source,
                    "api_available": bool(api_available),
                    "restart_required": restart_required,
                    "message": message,
                    "error": "",
                    "api_error": api_error,
                }
            except Exception as exc:  # noqa: BLE001 - optional endpoint isolation
                detail = self.redact_sensitive_text(str(exc) or exc.__class__.__name__)
                return self._plugin_config_failure(
                    requested_id,
                    "保存插件配置失败；主项目和其它插件不受影响。",
                    detail=detail,
                )

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

        url_host = self.public_host or "127.0.0.1"
        if ":" in url_host and not url_host.startswith("["):
            url_host = f"[{url_host}]"
        urls = {
            "astrbot_webui": f"http://{url_host}:{ports['astrbot_webui']}" if ports.get("astrbot_webui") else "",
            "snowluma_webui": f"http://{url_host}:{ports['snowluma_webui']}" if ports.get("snowluma_webui") else "",
            "novnc": f"http://{url_host}:{ports['novnc']}" if ports.get("novnc") else "",
        }
        with self._lock:
            active_job_id = self._active_job_id
            active_job = self._jobs.get(active_job_id)
            active_job_status = active_job.status if active_job else ("running" if active_job_id else None)
        if not active_job_id and not deployed:
            # Surface the last failed/interrupted job so a returning user sees
            # the deploy page with a retry action instead of a blank form.
            last_job_id = str(config.get("last_job_id") or "")
            if last_job_id:
                last_job = self._load_persisted_job(last_job_id)
                if last_job and last_job.get("status") in {"error", "failed", "interrupted", "cancelled"}:
                    active_job_id = last_job_id
                    active_job_status = str(last_job.get("status"))
        snowluma_password = str(secrets_data.get("snowluma_password") or "")
        if deployed and not snowluma_password and running:
            snowluma_password = self._capture_snowluma_password(secrets_data)
        onebot_connected = self._detect_onebot_connection() if deployed and running else None
        qq_logged_in = self._detect_qq_login(str(config.get("qq_account") or "")) if deployed else False
        try:
            memory_mode = self._effective_memory_mode()[0]
        except Exception:
            memory_mode = str(config.get("memory_mode") or DEFAULT_MEMORY_MODE)

        if not docker_status.installed:
            notice = "未检测到可用的 Docker CLI。请先运行 ./install.sh 安装 Docker，或手动安装后重试。"
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
            "app_version": __version__,
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
                    "snowluma_username": "admin",
                    "snowluma_password": snowluma_password,
                    "vnc_password": str(secrets_data.get("vnc_password") or ""),
                },
                "onebot_connected": onebot_connected,
                "qq_logged_in": qq_logged_in,
                "active_job_id": active_job_id,
                "active_job_status": active_job_status,
                "memory_mode": memory_mode,
            },
            "memory_mode": memory_mode,
            "notice": notice,
        }

    def repair_containers(self) -> dict[str, Any]:
        """Inspect and repair this instance's containers if they are unhealthy."""

        config = self.store.load_config()
        result = {
            "instance_id": self.instance_id,
            "ok": True,
            "action": "skipped",
            "message": "",
        }
        if not config.get("deployed"):
            result["message"] = "尚未完成部署，跳过容器修复。"
            return result
        self.docker.require_ready()
        statuses = {
            self.astrbot_container: self.docker.container_status(self.astrbot_container),
            self.snowluma_container: self.docker.container_status(self.snowluma_container),
        }
        health = {
            name: self.docker.container_health(name)
            for name in statuses
            if statuses.get(name) == "running"
        }
        needs_up = any(state != "running" for state in statuses.values())
        unhealthy = any(value == "unhealthy" for value in health.values())
        if not needs_up and not unhealthy:
            result["action"] = "healthy"
            result["message"] = "容器状态正常。"
            return result
        try:
            if needs_up:
                self.docker.up()
            if unhealthy:
                self.docker.restart()
            # Re-check after repair.
            repaired = {
                self.astrbot_container: self.docker.container_status(self.astrbot_container),
                self.snowluma_container: self.docker.container_status(self.snowluma_container),
            }
            repaired_health = {
                name: self.docker.container_health(name)
                for name, state_value in repaired.items()
                if state_value == "running"
            }
            still_unhealthy = any(value == "unhealthy" for value in repaired_health.values())
            if all(state_value == "running" for state_value in repaired.values()) and not still_unhealthy:
                result["action"] = "repaired"
                result["message"] = "检测到异常容器并已修复。"
            else:
                result["ok"] = False
                result["action"] = "failed"
                result["message"] = f"容器修复后仍异常：{repaired} health={repaired_health}"
        except Exception as exc:
            result["ok"] = False
            result["action"] = "failed"
            result["message"] = f"容器修复失败：{exc}"
        return result

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
        try:
            self._write_enhancer_config(self.data_dir / "data" / "astrbot", value)
        except OSError:
            pass
        config["prompt"] = value
        self.store.save_config(config)
        return "提示词已写入 AstrBot，并更新本机配置。"

    def get_logs(self, service: str, lines: int = 200) -> str:
        raw = self.docker.logs(service, lines)
        return self.redact_sensitive_text(raw)
