"""Optional AI assistant B extension management.

The assistant is an independent repository and is deliberately *not* embedded
in this project.  This module only clones/checks out that repository into the
Studio data directory.  It never reads assistant configuration, API keys or
starts the assistant process.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

ASSISTANT_REPO_URL = "https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git"
ASSISTANT_DIR_NAME = "ai-assistant"
ASSISTANT_ENTRY_FILE = "assistant.py"
ASSISTANT_README_FILE = "README.md"

ASSISTANT_CONFIG_VERSION = 1
ASSISTANT_DEFAULT_HOST = "127.0.0.1"
ASSISTANT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")
ASSISTANT_DEFAULT_PORT = 8766
ASSISTANT_PORT_MIN = 5000
ASSISTANT_PORT_MAX = 10000
ASSISTANT_KEY_SOURCES = ("independent", "instance")
ASSISTANT_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ASSISTANT_MAX_KEY_LENGTH = 4096

GitRunner = Callable[
    [Sequence[str], Path, int, Mapping[str, str] | None],
    subprocess.CompletedProcess[str],
]


def _default_git_runner(
    argv: Sequence[str],
    cwd: Path,
    timeout: int,
    extra_env: Mapping[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Main server processes may be detached from a terminal.  Never let git
    # stop on a credential or host-key prompt.
    env["GIT_TERMINAL_PROMPT"] = "0"
    if os.name != "nt":
        env.setdefault("GIT_ASKPASS", "/bin/true")
        env.setdefault(
            "GIT_SSH_COMMAND",
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            "-o ConnectTimeout=15 -o ControlMaster=no -o ControlPath=none",
        )
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return subprocess.run(
        [str(item) for item in argv],
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        shell=False,
    )


class AssistantExtensionManager:
    """Check, clone and fast-forward the optional assistant B repository."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        repo_url: str = ASSISTANT_REPO_URL,
        runner: GitRunner | None = None,
        clone_timeout: int = 180,
        git_timeout: int = 60,
        popen_factory: Callable[..., Any] | None = None,
        health_checker: Callable[[str, float], bool] | None = None,
        listener_probe: Callable[[int], dict[str, Any]] | None = None,
        public_probe: Callable[[str, float], dict[str, Any]] | None = None,
        start_timeout: float = 12.0,
        stop_timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.extensions_dir = self.data_dir / "extensions"
        self.extension_dir = self.extensions_dir / ASSISTANT_DIR_NAME
        self.assistant_data_dir = self.data_dir / ASSISTANT_DIR_NAME
        self.config_path = self.assistant_data_dir / "config.json"
        self.log_path = self.assistant_data_dir / "assistant.log"
        self.pid_path = self.assistant_data_dir / "assistant.pid"
        self.repo_url = str(repo_url or ASSISTANT_REPO_URL)
        self.clone_timeout = max(10, int(clone_timeout))
        self.git_timeout = max(5, int(git_timeout))
        self.start_timeout = max(0.1, float(start_timeout))
        self.stop_timeout = max(0.1, float(stop_timeout))
        self.poll_interval = max(0.01, float(poll_interval))
        self._runner = runner or _default_git_runner
        self._popen_factory = popen_factory or subprocess.Popen
        self._health_checker = health_checker
        self._listener_probe = listener_probe
        self._public_probe = public_probe
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Process helpers
    # ------------------------------------------------------------------
    def _git(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = ["git", *[str(item) for item in args]]
        try:
            result = self._runner(
                argv,
                cwd,
                int(timeout or self.git_timeout),
                None,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                argv,
                127,
                "",
                "找不到 git 命令。请先运行：sudo apt-get install -y git",
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", "Git 操作超时。")
        except OSError as exc:
            return subprocess.CompletedProcess(argv, 126, "", str(exc))
        stdout = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode("utf-8", "replace")
        stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode("utf-8", "replace")
        return subprocess.CompletedProcess(
            argv,
            int(result.returncode),
            stdout or "",
            stderr or "",
        )

    @staticmethod
    def _tail_output(result: subprocess.CompletedProcess[str], limit: int = 800) -> str:
        text = "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part and part.strip()
        )
        return AssistantExtensionManager._sanitize_text(text)[-limit:]

    def _base_status(self) -> dict[str, Any]:
        path = self.extension_dir
        return {
            "ok": True,
            "installed": False,
            "path": str(path),
            "repo": self.repo_url,
            "commit": "",
            "entry": str(path / ASSISTANT_ENTRY_FILE),
            "readme": str(path / ASSISTANT_README_FILE),
            "entry_file": ASSISTANT_ENTRY_FILE,
            "readme_file": ASSISTANT_README_FILE,
            "entry_exists": False,
            "readme_exists": False,
            "repo_ok": False,
            "directory_exists": path.exists(),
            "branch": "",
            "dirty": None,
            "action": "",
            "message": "AI 助手 B 尚未安装。主控制台和其他实例不受影响。",
            "error": "",
        }

    def _failure(
        self,
        message: str,
        *,
        detail: str = "",
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = status if status is not None else self._base_status()
        payload = dict(payload)
        payload["ok"] = False
        payload["message"] = message
        payload["error"] = detail or message
        return payload

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Return a partial-data-friendly status object and never raise.

        The response intentionally contains no assistant config contents and no
        API key.  A missing extension is a normal state (HTTP 200 with
        ``installed=false``).
        """

        payload = self._base_status()
        path = self.extension_dir
        if path.is_symlink():
            payload.update(
                {
                    "ok": False,
                    "message": "扩展目录是符号链接，出于安全考虑拒绝读取或更新。",
                    "error": "AI 助手 B 扩展路径必须是普通目录，不能是符号链接。",
                }
            )
            return payload
        if not path.exists():
            return payload
        if not path.is_dir():
            payload.update(
                {
                    "ok": False,
                    "message": "扩展路径存在但不是目录。",
                    "error": f"不是目录：{path}",
                }
            )
            return payload

        entry = path / ASSISTANT_ENTRY_FILE
        readme = path / ASSISTANT_README_FILE
        payload["entry_exists"] = entry.is_file()
        payload["readme_exists"] = readme.is_file()
        git_dir = path / ".git"
        payload["repo_ok"] = git_dir.exists()

        commit = ""
        branch = ""
        dirty: bool | None = None
        git_errors: list[str] = []
        if payload["repo_ok"]:
            head = self._git(["rev-parse", "--short=12", "HEAD"], cwd=path)
            if head.returncode == 0 and head.stdout.strip():
                commit = head.stdout.strip()
            else:
                git_errors.append(self._tail_output(head) or "无法读取 git commit。")

            branch_result = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
            if branch_result.returncode == 0:
                branch = branch_result.stdout.strip()
            else:
                git_errors.append(self._tail_output(branch_result) or "无法读取 git 分支。")

            dirty_result = self._git(["status", "--porcelain"], cwd=path)
            if dirty_result.returncode == 0:
                dirty = bool(dirty_result.stdout.strip())
            else:
                git_errors.append(self._tail_output(dirty_result) or "无法读取 git 工作区状态。")

        payload["commit"] = commit
        payload["branch"] = branch
        payload["dirty"] = dirty
        installed = bool(
            payload["entry_exists"]
            and payload["readme_exists"]
            and payload["repo_ok"]
            and commit
        )
        payload["installed"] = installed
        if installed:
            payload["message"] = (
                "AI 助手 B 已安装。入口与 README 已就绪；主项目不会自动启动它，"
                "请按 README 在本机配置密钥并手动启动。"
            )
        else:
            missing: list[str] = []
            if not payload["entry_exists"]:
                missing.append(ASSISTANT_ENTRY_FILE)
            if not payload["readme_exists"]:
                missing.append(ASSISTANT_README_FILE)
            if not payload["repo_ok"]:
                missing.append(".git")
            if payload["repo_ok"] and not commit:
                missing.append("有效 Git commit")
            detail = "扩展目录不完整，缺少：" + "、".join(missing) if missing else "扩展目录不完整。"
            payload["ok"] = False
            payload["error"] = detail + ("；" + "；".join(git_errors) if git_errors else "")
            payload["message"] = detail
        return payload

    # ------------------------------------------------------------------
    # Install and update
    # ------------------------------------------------------------------
    def install(self) -> dict[str, Any]:
        """Clone the fixed assistant repository or update an existing clone."""

        with self._lock:
            current = self.status()
            if current.get("installed"):
                return self.update()
            path = self.extension_dir
            if path.is_symlink():
                return self._failure(
                    "扩展目录是符号链接，拒绝安全风险操作。",
                    detail="AI 助手 B 扩展路径不能是符号链接。",
                    status=current,
                )
            if path.exists():
                if not path.is_dir():
                    return self._failure(
                        "扩展路径存在但不是目录，未做任何删除。",
                        detail=f"不是目录：{path}",
                        status=current,
                    )
                try:
                    is_empty = not any(path.iterdir())
                except OSError as exc:
                    return self._failure(
                        "无法读取已有扩展目录，未做任何删除。",
                        detail=str(exc),
                        status=current,
                    )
                if not is_empty:
                    return self._failure(
                        "扩展目录已存在但不是完整的 AI 助手 B Git 仓库；为避免覆盖用户数据，未删除或改写。",
                        detail=current.get("error") or "目录不完整或不是 Git 仓库。",
                        status=current,
                    )
                try:
                    path.rmdir()
                except OSError as exc:
                    return self._failure(
                        "无法清理空的扩展目录。",
                        detail=str(exc),
                        status=current,
                    )

            try:
                self.extensions_dir.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(self.extensions_dir, 0o700)
                except OSError:
                    pass
            except OSError as exc:
                return self._failure(
                    "无法创建扩展安装目录。",
                    detail=str(exc),
                    status=current,
                )

            temp_name = f".{ASSISTANT_DIR_NAME}.install-{secrets.token_hex(6)}"
            temp_path = self.extensions_dir / temp_name
            if temp_path.exists():
                temp_path = self.extensions_dir / f"{temp_name}-{secrets.token_hex(3)}"
            clone = self._git(
                ["clone", "--depth", "1", self.repo_url, str(temp_path)],
                cwd=self.extensions_dir,
                timeout=self.clone_timeout,
            )
            if clone.returncode != 0:
                self._remove_temp(temp_path)
                detail = self._tail_output(clone) or f"Git clone 退出码 {clone.returncode}"
                return self._failure(
                    "下载 AI 助手 B 失败；主控制台和其他实例不受影响。可检查网络后重试。",
                    detail=detail,
                    status=self.status(),
                )

            if not (temp_path / ASSISTANT_ENTRY_FILE).is_file() or not (temp_path / ASSISTANT_README_FILE).is_file():
                self._remove_temp(temp_path)
                return self._failure(
                    "下载完成但仓库缺少 assistant.py 或 README.md，未安装；主项目不受影响。",
                    detail="AI 助手 B 仓库必要文件校验失败。",
                    status=self.status(),
                )
            try:
                if path.exists():
                    raise OSError("目标扩展目录在安装过程中已被其他操作创建")
                os.replace(temp_path, path)
            except OSError as exc:
                self._remove_temp(temp_path)
                return self._failure(
                    "无法将 AI 助手 B 安装到扩展目录。",
                    detail=str(exc),
                    status=self.status(),
                )

            final = self.status()
            final["action"] = "installed"
            final["updated"] = True
            if final.get("installed"):
                final["ok"] = True
                final["message"] = (
                    "AI 助手 B 已安装到本机扩展目录。主项目不会自动启动它；"
                    "请阅读 README 并运行扩展目录内的 install.sh 完成密钥与手动启动配置。"
                )
                final["error"] = ""
            return final

    def update(self) -> dict[str, Any]:
        """Fast-forward an existing extension checkout without merging."""

        with self._lock:
            current = self.status()
            if not current.get("directory_exists"):
                return self._failure(
                    "AI 助手 B 尚未安装，不能更新。",
                    status=current,
                )
            if current.get("ok") is False and not current.get("repo_ok"):
                return self._failure(
                    "AI 助手 B 扩展目录不是完整的 Git 仓库，拒绝更新。",
                    detail=current.get("error") or "缺少 .git 目录。",
                    status=current,
                )
            path = self.extension_dir
            if path.is_symlink():
                return self._failure(
                    "扩展目录是符号链接，拒绝更新。",
                    detail="AI 助手 B 扩展路径不能是符号链接。",
                    status=current,
                )

            status_result = self._git(["status", "--porcelain"], cwd=path)
            if status_result.returncode != 0:
                return self._failure(
                    "无法检查 AI 助手 B 工作区状态，未执行更新。",
                    detail=self._tail_output(status_result),
                    status=current,
                )
            if status_result.stdout.strip():
                return self._failure(
                    "AI 助手 B 工作区有未提交修改，已拒绝更新；请先手动提交或还原这些修改。",
                    detail=status_result.stdout.strip()[-800:],
                    status=current,
                )

            branch_result = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
            if not branch or branch == "HEAD":
                return self._failure(
                    "当前 AI 助手 B 处于 detached HEAD，无法安全快进更新。",
                    detail=self._tail_output(branch_result),
                    status=current,
                )

            fetch = self._git(
                ["fetch", "origin", "--prune"],
                cwd=path,
                timeout=self.clone_timeout,
            )
            if fetch.returncode != 0:
                return self._failure(
                    "检查 AI 助手 B 远端更新失败；主项目和当前已安装版本不受影响。",
                    detail=self._tail_output(fetch),
                    status=current,
                )

            remote_ref = f"origin/{branch}"
            ancestor = self._git(
                ["merge-base", "--is-ancestor", "HEAD", remote_ref],
                cwd=path,
            )
            if ancestor.returncode != 0:
                return self._failure(
                    "AI 助手 B 远端不是当前提交的快进更新，已拒绝；请手动处理分支或改动。",
                    detail=self._tail_output(ancestor)
                    or f"HEAD 不是 {remote_ref} 的祖先。",
                    status=current,
                )

            merge = self._git(["merge", "--ff-only", remote_ref], cwd=path)
            if merge.returncode != 0:
                return self._failure(
                    "AI 助手 B 快进更新失败，旧版本保持可用。",
                    detail=self._tail_output(merge),
                    status=current,
                )

            final = self.status()
            final["action"] = "updated"
            final["updated"] = final.get("commit") != current.get("commit")
            if final.get("installed"):
                final["ok"] = True
                final["message"] = (
                    "AI 助手 B 已检查并完成快进更新。主项目不会自动启动它。"
                    if final["updated"]
                    else "AI 助手 B 已是最新版本；主项目不会自动启动它。"
                )
                final["error"] = ""
            return final

    # ------------------------------------------------------------------
    # Safe local configuration
    # ------------------------------------------------------------------
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _sanitize_text(text: Any) -> str:
        value = str(text or "")
        value = re.sub(r"(?i)(https?://)[^\s/@:]+(?::[^\s/@]*)?@", r"\1***@", value)
        value = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1***", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}\b", "sk-***", value)
        # Remove key/token/password assignments in text/JSON logs entirely,
        # including the field name, so a returned log summary cannot echo a
        # credential or even its value-shaped field.
        value = re.sub(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret|credential)\b\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
            "[redacted-sensitive-field]",
            value,
        )
        return value

    @staticmethod
    def _service_url(host: str, port: int) -> str:
        """Return a browsable URL, never the unspecified address itself."""

        normalized = str(host or ASSISTANT_DEFAULT_HOST).strip().lower()
        if normalized in {"0.0.0.0", "::"}:
            return ""
        if ":" in normalized:
            return f"http://[{normalized}]:{int(port)}/"
        if normalized == "localhost":
            return f"http://localhost:{int(port)}/"
        return f"http://{normalized}:{int(port)}/"

    @staticmethod
    def _url_host(host: str) -> str:
        value = str(host or "").strip()
        if ":" in value and not value.startswith("["):
            return f"[{value}]"
        return value

    @classmethod
    def _public_url(cls, public_host: str, port: int) -> str:
        value = str(public_host or "").strip()
        if not value:
            return ""
        return f"http://{cls._url_host(value)}:{int(port)}/"

    @classmethod
    def _browser_url(cls, bind_host: str, port: int, public_host: str) -> str:
        normalized = str(bind_host or ASSISTANT_DEFAULT_HOST).strip().lower()
        if normalized == "0.0.0.0":
            return cls._public_url(public_host, port)
        return cls._service_url(normalized, port)

    @classmethod
    def _health_url(cls, bind_host: str, port: int) -> str:
        """Use loopback for wildcard binds; public URLs may not hairpin."""

        normalized = str(bind_host or ASSISTANT_DEFAULT_HOST).strip().lower()
        if normalized in {"0.0.0.0", "::"}:
            return f"http://127.0.0.1:{int(port)}/"
        return cls._service_url(normalized, port)

    @staticmethod
    def _access_note(bind_host: str, public_host: str = "", url: str = "") -> str:
        if str(bind_host or "").strip().lower() == "0.0.0.0":
            if public_host and url:
                return (
                    "高风险公网模式：AI 助手 B 绑定所有网卡，可通过 "
                    f"{url} 访问。B 没有用户认证，必须使用安全组、防火墙、VPN 或"
                    "带认证的反向代理限制来源，绝不能直接裸奔公网。"
                )
            return (
                "高风险公网模式：AI 助手 B 已绑定所有网卡，但尚未填写公网 IP/域名，"
                "因此没有可点击的浏览器 URL。请填写 public_host，或改用回环地址 + SSH 隧道。"
                "B 没有用户认证，必须使用安全组、防火墙或 VPN 限制来源。"
            )
        return (
            "AI 助手 B 没有面向用户的访问认证，当前仅监听本机回环地址。"
            "远端访问请使用 SSH 隧道或在本机浏览器打开；如改用 0.0.0.0，"
            "必须使用安全组/防火墙/VPN 限制来源。"
        )

    @staticmethod
    def _ssh_tunnel(port: int) -> str:
        return f"ssh -N -L {int(port)}:127.0.0.1:{int(port)} <用户名>@<服务器地址>"

    def _config_base_state(self, *, installed: bool) -> dict[str, Any]:
        port = ASSISTANT_DEFAULT_PORT
        loopback_url = self._service_url(ASSISTANT_DEFAULT_HOST, port)
        return {
            "ok": True,
            "installed": bool(installed),
            "configured": False,
            "running": False,
            "url": loopback_url,
            "configure_url": "/api/assistant/config",
            "config_path": str(self.config_path),
            "key_present": False,
            "key_source": "independent",
            "instance_id": "",
            "port": port,
            "host": ASSISTANT_DEFAULT_HOST,
            "public_host": "",
            "health_url": loopback_url,
            "pid": None,
            "log_path": str(self.log_path),
            "pid_path": str(self.pid_path),
            "access_note": self._access_note(ASSISTANT_DEFAULT_HOST),
            "ssh_tunnel": self._ssh_tunnel(port),
            "message": "AI 助手 B 尚未配置。",
            "error": "",
        }

    def _service_failure(
        self,
        message: str,
        *,
        installed: bool,
        detail: str = "",
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(state) if isinstance(state, dict) else self._config_base_state(installed=installed)
        payload["ok"] = False
        payload["installed"] = bool(installed)
        payload["running"] = bool(payload.get("running", False))
        payload["message"] = message
        payload["error"] = self._sanitize_text(detail or message)
        return payload

    def _ensure_private_dir(self) -> None:
        self.assistant_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.assistant_data_dir, 0o700)
        except OSError:
            pass

    def _write_private_json(self, path: Path, value: dict[str, Any]) -> None:
        self._ensure_private_dir()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        temp = Path(temp_name)
        try:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                try:
                    fchmod(fd, 0o600)
                except OSError:
                    pass
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    def _read_config_raw(self) -> tuple[dict[str, Any] | None, str]:
        path = self.config_path
        if not path.exists():
            return None, ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            return None, f"无法读取 AI 助手 B 配置文件：{exc}"
        except ValueError as exc:
            return None, f"AI 助手 B 配置文件不是有效 JSON：{exc}"
        if not isinstance(raw, dict):
            return None, "AI 助手 B 配置文件顶层必须是 JSON 对象。"
        return raw, ""

    def _default_config_record(self) -> dict[str, Any]:
        return {
            "version": ASSISTANT_CONFIG_VERSION,
            "host": ASSISTANT_DEFAULT_HOST,
            "port": ASSISTANT_DEFAULT_PORT,
            "key_source": "independent",
            "api_key": "",
            "instance_id": "",
            "public_host": "",
            "timeout_seconds": 60,
            "system_prompt": "",
        }

    def _instance_key_present(self, instance_id: str) -> bool:
        """Return whether a safe referenced instance has an AstrBot key.

        Only a boolean is computed.  Key contents are never returned or logged.
        """

        safe_id = str(instance_id or "").strip()
        if not ASSISTANT_INSTANCE_ID_RE.fullmatch(safe_id):
            return False
        instance_dir = self.data_dir / "instances" / safe_id
        if instance_dir.is_symlink():
            return False
        config_path = instance_dir / "data" / "astrbot" / "cmd_config.json"
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict):
            return False
        providers = raw.get("provider")
        if not isinstance(providers, list):
            return False
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            key_value = provider.get("key")
            if isinstance(key_value, list):
                if any(isinstance(item, str) and item.strip() for item in key_value):
                    return True
            elif isinstance(key_value, str) and key_value.strip():
                return True
            for field in ("api_key", "apiKey"):
                value = provider.get(field)
                if isinstance(value, str) and value.strip():
                    return True
        return False

    def _reserved_ports(self) -> set[int]:
        reserved: set[int] = set()
        settings_path = self.data_dir / "studio.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        if isinstance(settings, dict):
            try:
                management_port = int(settings.get("bind_port"))
                if 1 <= management_port <= 65535:
                    reserved.add(management_port)
            except (TypeError, ValueError):
                pass
        instances_root = self.data_dir / "instances"
        if not instances_root.is_dir():
            return reserved
        try:
            instance_dirs = list(instances_root.iterdir())
        except OSError:
            return reserved
        for instance_dir in instance_dirs:
            if instance_dir.is_symlink() or not instance_dir.is_dir():
                continue
            try:
                config = json.loads((instance_dir / "config.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                config = {}
            if isinstance(config, dict) and isinstance(config.get("ports"), dict):
                for value in config["ports"].values():
                    try:
                        port = int(value)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= port <= 65535:
                        reserved.add(port)
            try:
                env_text = (instance_dir / ".env").read_text(encoding="utf-8")
            except OSError:
                env_text = ""
            for line in env_text.splitlines():
                key, _, value = line.partition("=")
                if key in {"ASTRBOT_WEBUI_PORT", "SNOWLUMA_WEBUI_PORT_HOST", "NOVNC_PORT"}:
                    try:
                        reserved.add(int(value.strip()))
                    except ValueError:
                        pass
        return reserved

    @staticmethod
    def _probe_host(host: str) -> str:
        normalized = str(host or ASSISTANT_DEFAULT_HOST).strip().lower()
        return "::1" if normalized == "::1" else "127.0.0.1"

    def _port_is_free(self, host: str, port: int) -> bool:
        probe = self._probe_host(host)
        family = socket.AF_INET6 if ":" in probe else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.bind((probe, int(port)))
            return True
        except OSError:
            return False

    def _choose_port(self, existing: Any, host: str) -> int:
        reserved = self._reserved_ports()
        if existing is not None and not isinstance(existing, bool):
            try:
                current = int(existing)
            except (TypeError, ValueError):
                current = 0
            if (
                ASSISTANT_PORT_MIN <= current <= ASSISTANT_PORT_MAX
                and current not in reserved
            ):
                return current
        candidates = list(range(ASSISTANT_DEFAULT_PORT, ASSISTANT_PORT_MAX + 1))
        candidates.extend(range(ASSISTANT_PORT_MIN, ASSISTANT_DEFAULT_PORT))
        for candidate in candidates:
            if candidate in reserved:
                continue
            if self._port_is_free(host, candidate):
                return candidate
        raise ValueError(
            f"无法在 {ASSISTANT_PORT_MIN}-{ASSISTANT_PORT_MAX} 范围内找到可用的 AI 助手 B 端口。"
        )

    @staticmethod
    def _validate_host(value: Any) -> str:
        host = str(value or ASSISTANT_DEFAULT_HOST).strip().lower()
        if host not in ASSISTANT_ALLOWED_HOSTS:
            raise ValueError(
                "host 只能是 127.0.0.1、localhost、::1 或显式高风险值 0.0.0.0。"
            )
        return host

    @staticmethod
    def _validate_public_host(value: Any) -> str:
        """Validate a URL host only (no scheme, path, port or whitespace)."""

        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        if len(text) > 255:
            raise ValueError("public_host 过长，最多 255 个字符。")
        if any(ch.isspace() for ch in text):
            raise ValueError("public_host 不能包含空格或换行。")
        if any(token in text for token in ("://", "/", "\\", "?", "#", "@")):
            raise ValueError("public_host 只能是公网 IP 或域名，不能包含协议、路径或用户信息。")

        candidate = text
        if candidate.startswith("[") or candidate.endswith("]"):
            if not (candidate.startswith("[") and candidate.endswith("]")):
                raise ValueError("IPv6 public_host 方括号不完整。")
            candidate = candidate[1:-1]
            try:
                address = ipaddress.IPv6Address(candidate)
            except ValueError:
                raise ValueError("public_host 不是有效 IPv6 地址。") from None
            if address.is_unspecified:
                raise ValueError("public_host 不能是未指定地址。")
            return str(address)

        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            address = None
        if address is not None:
            if address.is_unspecified:
                raise ValueError("public_host 不能是 0.0.0.0、:: 等未指定地址。")
            return str(address)

        if ":" in candidate:
            raise ValueError("public_host 不能包含端口；端口请使用独立的 port 字段。")
        domain = candidate.rstrip(".").lower()
        if not domain or len(domain) > 253:
            raise ValueError("public_host 域名长度无效。")
        labels = domain.split(".")
        label_re = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
        if any(not label_re.fullmatch(label) for label in labels):
            raise ValueError("public_host 不是有效的公网 IP 或域名。")
        return domain

    @staticmethod
    def _validate_port(value: Any, reserved: set[int]) -> int:
        if isinstance(value, bool):
            raise ValueError("port 必须是整数。")
        try:
            port = int(value)
        except (TypeError, ValueError):
            raise ValueError("port 必须是整数。") from None
        if not (ASSISTANT_PORT_MIN <= port <= ASSISTANT_PORT_MAX):
            raise ValueError(
                f"port 必须在 {ASSISTANT_PORT_MIN}-{ASSISTANT_PORT_MAX} 之间。"
            )
        if port in reserved:
            raise ValueError(
                f"port {port} 已被管理服务或现有机器人实例占用，请换一个端口。"
            )
        return port

    def _config_identity(self, raw: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
        """Return safe public identity data plus key presence and an error."""

        error = ""
        host = str(raw.get("host") or ASSISTANT_DEFAULT_HOST).strip().lower()
        try:
            host = self._validate_host(host)
        except ValueError as exc:
            error = str(exc)
            host = ASSISTANT_DEFAULT_HOST
        try:
            if isinstance(raw.get("port"), bool):
                raise ValueError
            port = int(raw.get("port", ASSISTANT_DEFAULT_PORT))
            if not (ASSISTANT_PORT_MIN <= port <= ASSISTANT_PORT_MAX):
                raise ValueError
        except (TypeError, ValueError):
            port = ASSISTANT_DEFAULT_PORT
            error = error or f"配置中的 port 无效，应在 {ASSISTANT_PORT_MIN}-{ASSISTANT_PORT_MAX} 之间。"
        source = str(raw.get("key_source") or "independent").strip()
        if source not in ASSISTANT_KEY_SOURCES:
            source = "independent"
            error = error or "配置中的 key_source 无效，只能是 independent 或 instance。"
        instance_id = str(raw.get("instance_id") or "").strip()
        if instance_id and not ASSISTANT_INSTANCE_ID_RE.fullmatch(instance_id):
            instance_id = ""
            error = error or "配置中的 instance_id 格式无效。"
        public_host_raw = raw.get("public_host", "")
        try:
            public_host = self._validate_public_host(public_host_raw)
        except ValueError as exc:
            public_host = ""
            error = error or str(exc)
        key_present = False
        if source == "independent":
            key = raw.get("api_key")
            key_present = isinstance(key, str) and bool(key.strip())
        else:
            key_present = bool(instance_id) and self._instance_key_present(instance_id)
        url = self._browser_url(host, port, public_host)
        health_url = self._health_url(host, port)
        identity = {
            "host": host,
            "port": port,
            "public_host": public_host,
            "url": url,
            "health_url": health_url,
            "key_source": source,
            "instance_id": instance_id,
            "key_present": key_present,
            "configured": bool(key_present and not error),
            "ssh_tunnel": self._ssh_tunnel(port)
            if host != "0.0.0.0" or not public_host
            else "",
            "access_note": self._access_note(host, public_host, url),
        }
        return identity, key_present, error

    def _read_pid(self) -> int | None:
        try:
            raw = self.pid_path.read_text(encoding="utf-8").strip()
            pid = int(raw)
        except (OSError, ValueError):
            return None
        return pid if pid > 1 else None

    def _write_pid(self, pid: int) -> None:
        self._ensure_private_dir()
        payload = f"{int(pid)}\n"
        fd, temp_name = tempfile.mkstemp(prefix=".assistant.pid.", dir=str(self.assistant_data_dir))
        temp = Path(temp_name)
        try:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                try:
                    fchmod(fd, 0o600)
                except OSError:
                    pass
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.pid_path)
            try:
                os.chmod(self.pid_path, 0o600)
            except OSError:
                pass
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    def _remove_pid(self) -> None:
        try:
            self.pid_path.unlink()
        except OSError:
            pass

    def _read_log_tail(self, limit: int = 1200) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return self._sanitize_text(text[-limit:]).strip()

    def _health_ok(self, url: str, timeout: float = 1.5) -> bool:
        if callable(self._health_checker):
            try:
                return bool(self._health_checker(url, timeout))
            except Exception:
                return False
        request = urllib.request.Request(
            url.rstrip("/") + "/api/health",
            headers={"Accept": "application/json", "User-Agent": "QQBotStudio/3.0"},
            method="GET",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(64 * 1024).decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False
        try:
            payload = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return False
        app = str(payload.get("app") or "")
        return not app or app.startswith("qqbot-studio-ai-assistant")

    # ------------------------------------------------------------------
    # Listener, public-access and diagnostic helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalise_bind_address(value: Any) -> str:
        text = str(value or "").strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return text

    @staticmethod
    def _proc_ipv4_address(hex_address: str) -> str:
        value = int(hex_address, 16)
        return socket.inet_ntoa(value.to_bytes(4, "little"))

    @staticmethod
    def _proc_ipv6_address(hex_address: str) -> str:
        raw = bytes.fromhex(hex_address)
        if len(raw) != 16:
            raise ValueError("invalid IPv6 listener address")
        # /proc/net/tcp6 stores each 32-bit word in host (little-endian)
        # order; convert back to network order before formatting.
        network = b"".join(raw[index : index + 4][::-1] for index in range(0, 16, 4))
        return socket.inet_ntop(socket.AF_INET6, network)

    @classmethod
    def _normalise_listener_result(
        cls,
        raw: Any,
        port: int,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "supported": False,
            "listening": False,
            "addresses": [],
            "bind_addresses": [],
            "wildcard": False,
            "loopback_only": False,
            "port": int(port),
            "source": "unavailable",
            "error": "",
        }
        if not isinstance(raw, dict):
            result["error"] = "监听探测没有返回有效结果。"
            return result
        result["supported"] = bool(raw.get("supported", True))
        result["source"] = str(raw.get("source") or "custom")
        result["error"] = cls._sanitize_text(raw.get("error") or "")
        addresses: list[dict[str, Any]] = []
        raw_addresses = (
            raw.get("addresses")
            or raw.get("bind_addresses")
            or raw.get("address")
            or []
        )
        # Test/alternate adapters may expose only boolean bind flags.  Convert
        # them to concrete addresses so listener validation remains available.
        if not raw_addresses:
            if raw.get("wildcard") is True:
                raw_addresses = ["0.0.0.0"]
            elif raw.get("loopback_only") is True:
                raw_addresses = ["127.0.0.1"]
        if isinstance(raw_addresses, (str, dict)):
            raw_addresses = [raw_addresses]
        if isinstance(raw_addresses, list):
            for item in raw_addresses:
                if isinstance(item, str):
                    address = cls._normalise_bind_address(item)
                    entry = {"address": address, "port": int(port), "family": "unknown", "pid": None}
                elif isinstance(item, dict):
                    address = cls._normalise_bind_address(
                        item.get("address") or item.get("host") or item.get("bind")
                    )
                    if not address:
                        continue
                    entry = {
                        "address": address,
                        "port": int(item.get("port") or port),
                        "family": str(item.get("family") or "unknown"),
                        "pid": item.get("pid"),
                    }
                else:
                    continue
                addresses.append(entry)
        result["addresses"] = addresses
        bind_addresses: list[str] = []
        for item in addresses:
            address = str(item["address"])
            if address not in bind_addresses:
                bind_addresses.append(address)
        result["bind_addresses"] = bind_addresses
        result["listening"] = bool(raw.get("listening", bool(addresses))) and bool(addresses)
        result["wildcard"] = any(address in {"0.0.0.0", "::"} for address in bind_addresses)
        loopbacks = {"127.0.0.1", "::1", "localhost"}
        result["loopback_only"] = bool(bind_addresses) and all(
            address.lower() in loopbacks for address in bind_addresses
        )
        return result

    def _proc_listener_snapshot(self, port: int) -> dict[str, Any]:
        addresses: list[dict[str, Any]] = []
        supported = False
        errors: list[str] = []
        sources: list[str] = []
        for filename, family, decoder in (
            ("/proc/net/tcp", "ipv4", self._proc_ipv4_address),
            ("/proc/net/tcp6", "ipv6", self._proc_ipv6_address),
        ):
            path = Path(filename)
            if not path.exists():
                continue
            supported = True
            sources.append(path.name)
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                errors.append(f"无法读取 {path}：{exc}")
                continue
            for line in lines[1:]:
                fields = line.split()
                if len(fields) < 4 or fields[3] != "0A":
                    continue
                local = fields[1]
                address_hex, separator, port_hex = local.rpartition(":")
                if not separator:
                    continue
                try:
                    listener_port = int(port_hex, 16)
                except ValueError:
                    continue
                if listener_port != int(port):
                    continue
                try:
                    address = decoder(address_hex)
                except (ValueError, OSError):
                    continue
                addresses.append(
                    {
                        "address": self._normalise_bind_address(address),
                        "port": int(port),
                        "family": family,
                        "pid": None,
                    }
                )
        return self._normalise_listener_result(
            {
                "supported": supported,
                "addresses": addresses,
                "source": "+".join(sources) if sources else "unavailable",
                "error": "；".join(errors),
            },
            port,
        )

    @staticmethod
    def _windows_listener_snapshot(port: int) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "supported": False,
                "addresses": [],
                "source": "netstat",
                "error": f"无法执行 netstat：{exc}",
            }
        addresses: list[dict[str, Any]] = []
        for line in (completed.stdout or "").splitlines():
            fields = line.split()
            if len(fields) < 4 or fields[0].upper() != "TCP":
                continue
            if "LISTENING" not in {field.upper() for field in fields}:
                continue
            local = fields[1]
            host, separator, port_text = local.rpartition(":")
            if not separator:
                continue
            try:
                listener_port = int(port_text)
            except ValueError:
                continue
            if listener_port != int(port):
                continue
            try:
                pid = int(fields[-1])
            except (TypeError, ValueError):
                pid = None
            addresses.append(
                {
                    "address": AssistantExtensionManager._normalise_bind_address(host),
                    "port": int(port),
                    "family": "ipv6" if ":" in host else "ipv4",
                    "pid": pid,
                }
            )
        return AssistantExtensionManager._normalise_listener_result(
            {"supported": True, "addresses": addresses, "source": "netstat"},
            port,
        )

    def _listener_snapshot(self, port: int) -> dict[str, Any]:
        """Return actual TCP listeners for ``port`` without shell invocation."""

        if callable(self._listener_probe):
            try:
                return self._normalise_listener_result(self._listener_probe(int(port)), int(port))
            except Exception as exc:  # noqa: BLE001 - diagnostic fallback
                return self._normalise_listener_result(
                    {
                        "supported": False,
                        "source": "custom",
                        "error": f"监听探测失败：{exc}",
                    },
                    int(port),
                )
        if os.name == "nt":
            return self._windows_listener_snapshot(int(port))
        if Path("/proc/net/tcp").exists() or Path("/proc/net/tcp6").exists():
            return self._proc_listener_snapshot(int(port))
        return self._normalise_listener_result(
            {
                "supported": False,
                "source": "unavailable",
                "error": "当前平台没有可用的监听地址探测方式。",
            },
            int(port),
        )

    @staticmethod
    def _listener_match(host: str, port: int, listener: dict[str, Any]) -> str:
        if not listener.get("supported"):
            return "unknown"
        if not listener.get("listening"):
            return "missing"
        addresses = [str(item).lower() for item in listener.get("bind_addresses") or []]
        if host == "0.0.0.0":
            if any(item in {"0.0.0.0", "::"} for item in addresses):
                return "wildcard"
            if addresses and all(item in {"127.0.0.1", "::1", "localhost"} for item in addresses):
                return "loopback_only"
            return "unexpected"
        loopbacks = {"127.0.0.1", "::1", "localhost"}
        if host in {"127.0.0.1", "localhost", "::1"}:
            return "ok" if any(item in loopbacks for item in addresses) else "unexpected"
        return "ok"

    def _entry_supports_wildcard(self) -> bool | None:
        entry = self.extension_dir / ASSISTANT_ENTRY_FILE
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return "0.0.0.0" in text

    def _public_access_probe(self, url: str, timeout: float = 3.0) -> dict[str, Any]:
        """Best-effort TCP+HTTP probe from the server itself.

        Failure to loop back to a public address can be caused by NAT hairpin,
        so a failed probe is explicitly marked inconclusive rather than proving
        that external clients cannot connect.
        """

        result: dict[str, Any] = {
            "checked": False,
            "skipped": True,
            "url": self._sanitize_text(url),
            "host": "",
            "port": None,
            "tcp_connected": None,
            "http_ok": None,
            "http_status": None,
            "status": "skipped",
            "inconclusive": False,
            "remote_verification_required": False,
            "detail": "未配置公网 URL，跳过公网探测。",
            "error": "",
        }
        if not url:
            return result
        if callable(self._public_probe):
            try:
                raw = self._public_probe(str(url), float(timeout))
            except Exception as exc:  # noqa: BLE001 - optional diagnostic
                raw = {
                    "checked": True,
                    "skipped": False,
                    "status": "inconclusive",
                    "inconclusive": True,
                    "remote_verification_required": True,
                    "detail": f"公网探测异常：{exc}",
                    "error": str(exc),
                }
            if not isinstance(raw, dict):
                raw = {}
            for key in result:
                if key in raw:
                    result[key] = raw[key]
            result["url"] = str(url)
            result["detail"] = self._sanitize_text(result.get("detail") or "")
            result["error"] = self._sanitize_text(result.get("error") or "")
            result["checked"] = bool(result.get("checked", not result.get("skipped")))
            result["skipped"] = bool(result.get("skipped", False))
            result["inconclusive"] = bool(result.get("inconclusive", False))
            result["remote_verification_required"] = bool(
                result.get("remote_verification_required", False)
            )
            return result

        parsed = urlsplit(str(url))
        host = str(parsed.hostname or "").strip()
        try:
            port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        except (TypeError, ValueError):
            port = 0
        result.update(
            {
                "checked": True,
                "skipped": False,
                "host": host,
                "port": port,
                "detail": "",
            }
        )
        if not host or port <= 0 or parsed.scheme not in {"http", "https"}:
            result.update(
                {
                    "status": "invalid_url",
                    "error": "公网访问 URL 无效。",
                    "detail": "public_host 或端口配置无法生成有效的 HTTP URL。",
                }
            )
            return result

        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            result.update(
                {
                    "status": "dns_error",
                    "remote_verification_required": True,
                    "error": f"public_host 无法解析：{exc}",
                    "detail": "公网 IP/域名无法解析；请检查 public_host。",
                }
            )
            return result
        except OSError as exc:
            result.update(
                {
                    "status": "dns_error",
                    "remote_verification_required": True,
                    "error": str(exc),
                    "detail": "解析 public_host 失败。",
                }
            )
            return result

        # Avoid turning a tampered config or a public_host that resolves to a
        # private/metadata address into an SSRF probe.  Only global addresses
        # are considered for the optional public-access check.
        safe_infos: list[tuple[Any, ...]] = []
        non_public_addresses: list[str] = []
        for info in infos:
            family, socktype, proto, canonname, sockaddr = info
            try:
                address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
            except ValueError:
                non_public_addresses.append(str(sockaddr[0]))
                continue
            if address.is_global:
                safe_infos.append(info)
            else:
                non_public_addresses.append(str(address))
        if not safe_infos:
            result.update(
                {
                    "status": "non_public_target",
                    "inconclusive": True,
                    "remote_verification_required": True,
                    "error": "public_host 指向非公网地址，已跳过公网探测。",
                    "detail": (
                        "public_host 解析到私网/回环/保留地址，服务端不会连接该地址。"
                        "请填写真实公网 IP 或域名，或使用回环 + SSH 隧道。"
                    ),
                }
            )
            return result

        connected = False
        connect_errors: list[str] = []
        for family, socktype, proto, _canonname, sockaddr in safe_infos:
            try:
                with socket.socket(family, socktype, proto) as sock:
                    sock.settimeout(timeout)
                    sock.connect(sockaddr)
                connected = True
                break
            except OSError as exc:
                connect_errors.append(str(exc))
        if not connected:
            result.update(
                {
                    "tcp_connected": False,
                    "status": "unreachable",
                    "inconclusive": True,
                    "remote_verification_required": True,
                    "detail": (
                        "服务器侧 TCP 连接公网地址失败。可能原因包括 NAT hairpin、"
                        "云安全组/防火墙未放行，或公网 IP/端口不正确；这项失败本身"
                        "不能证明外部客户端一定不可达，请从外部网络复核。"
                    ),
                    "error": "；".join(connect_errors[-3:]),
                }
            )
            return result

        result["tcp_connected"] = True
        # Rebuild from parsed host/port so a tampered URL cannot inject
        # credentials, paths or a different host into the health request.
        url_host = f"[{host}]" if ":" in host else host
        safe_base = f"{parsed.scheme}://{url_host}"
        default_port = 443 if parsed.scheme == "https" else 80
        if port != default_port:
            safe_base += f":{port}"
        safe_base += "/"
        result["url"] = safe_base
        health_url = safe_base + "api/health"

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
                return None

        request = urllib.request.Request(
            health_url,
            headers={"Accept": "application/json", "User-Agent": "QQBotStudio/3.0-diagnose"},
            method="GET",
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                raw_body = response.read(64 * 1024).decode("utf-8", "replace")
                http_status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            result.update(
                {
                    "http_ok": False,
                    "http_status": int(exc.code),
                    "status": "http_error",
                    "inconclusive": False,
                    "remote_verification_required": True,
                    "detail": f"公网地址 TCP 可达，但 health HTTP 返回 {exc.code}。",
                    "error": self._sanitize_text(exc.reason or f"HTTP {exc.code}"),
                }
            )
            return result
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            result.update(
                {
                    "http_ok": False,
                    "status": "tcp_only",
                    "inconclusive": True,
                    "remote_verification_required": True,
                    "detail": "公网 TCP 端口可达，但 HTTP health 请求失败；可能被防火墙/反代拦截。",
                    "error": self._sanitize_text(exc),
                }
            )
            return result
        try:
            payload = json.loads(raw_body)
        except ValueError:
            payload = {}
        health_ok = isinstance(payload, dict) and payload.get("ok") is True
        result.update(
            {
                "http_ok": health_ok,
                "http_status": http_status,
                "status": "ok" if health_ok else "http_error",
                "inconclusive": False,
                "remote_verification_required": not health_ok,
                "detail": (
                    "公网 TCP 与 health 探测均成功；服务在服务器自身视角可访问。"
                    if health_ok
                    else "公网 HTTP 可达，但 health 响应未返回 ok=true。"
                ),
                "error": "" if health_ok else "health 响应异常",
            }
        )
        return result

    def diagnose(self) -> dict[str, Any]:
        """Return safe, actionable diagnosis for local and public access.

        The method never starts/stops the service and never returns an API Key.
        It distinguishes local listener binding, local health, and a best-effort
        public probe.  Public-probe failures are marked inconclusive where NAT
        hairpin can make a server unable to reach its own public address.
        """

        with self._lock:
            try:
                state = self.get_config()
                installed = bool(state.get("installed"))
                configured = bool(state.get("configured"))
                running = bool(state.get("running"))
                host = str(state.get("host") or ASSISTANT_DEFAULT_HOST)
                try:
                    port = int(state.get("port") or ASSISTANT_DEFAULT_PORT)
                except (TypeError, ValueError):
                    port = ASSISTANT_DEFAULT_PORT
                public_host = str(state.get("public_host") or "")
                url = self._sanitize_text(state.get("url"))
                health_url = self._sanitize_text(state.get("health_url"))
                pid = state.get("pid")
                config_present = self.config_path.exists()
                entry_supports = self._entry_supports_wildcard() if installed else None

                if config_present:
                    listener = self._listener_snapshot(port)
                else:
                    listener = self._normalise_listener_result(
                        {
                            "supported": False,
                            "listening": False,
                            "addresses": [],
                            "source": "skipped",
                            "error": "尚未创建 B 配置。",
                        },
                        port,
                    )
                listener_match = self._listener_match(host, port, listener)
                local_health_ok = running and bool(health_url)
                local_health = {
                    "ok": local_health_ok,
                    "url": health_url,
                    "detail": (
                        "本机回环 health 检查成功。"
                        if local_health_ok
                        else "本机回环 health 检查失败；B 未运行或尚未完成启动。"
                    ),
                    "error": "" if local_health_ok else "health_unreachable",
                }

                # Build the public probe default with its own stable shape.
                public_probe = {
                    "checked": False,
                    "skipped": True,
                    "url": "",
                    "host": public_host,
                    "port": port,
                    "tcp_connected": None,
                    "http_ok": None,
                    "http_status": None,
                    "status": "skipped",
                    "inconclusive": False,
                    "remote_verification_required": False,
                    "detail": "未进入公网探测。",
                    "error": "",
                }
                if installed and config_present and host == "0.0.0.0":
                    if not public_host:
                        public_probe.update(
                            {
                                "status": "public_host_missing",
                                "checked": False,
                                "skipped": False,
                                "detail": "已绑定所有网卡，但 public_host 为空，无法生成公网 URL。",
                                "error": "public_host_missing",
                                "remote_verification_required": True,
                            }
                        )
                    elif running:
                        public_probe = self._public_access_probe(url, timeout=3.0)

                suggestions: list[str] = []
                error_code = "ok"
                healthy = False
                message = "AI 助手 B 状态正常。"
                old_b_suspected = False

                if not installed:
                    error_code = "not_installed"
                    message = "AI 助手 B 尚未安装。"
                    suggestions.append("先点击“安装 AI 助手 B”，再配置 Key。")
                elif not config_present:
                    error_code = "not_configured"
                    message = "AI 助手 B 尚未创建配置。"
                    suggestions.append("填写 Key、端口和监听方式后保存。")
                elif state.get("ok") is False:
                    error_code = "config_invalid"
                    message = "AI 助手 B 配置无效，无法可靠启动或诊断。"
                    suggestions.append("修正配置后重试；不要直接编辑含 Key 的文件并粘贴到聊天中。")
                elif not configured:
                    error_code = "key_missing"
                    message = "AI 助手 B 已配置，但未检测到可用 API Key。"
                    suggestions.append("补充独立 Key，或选择可读取的实例 Key。")
                elif not running:
                    error_code = "not_running"
                    message = "AI 助手 B 已配置但进程未运行。"
                    suggestions.append("点击“启动 AI 助手 B”；若启动失败，先更新 B 再重试。")
                else:
                    healthy = True
                    if host == "0.0.0.0":
                        if listener_match == "loopback_only":
                            healthy = False
                            error_code = "listener_loopback_only"
                            old_b_suspected = entry_supports is False
                            message = (
                                "B 进程在运行且本机 health 正常，但实际监听地址只有 127.0.0.1/::1，"
                                "公网无法连接。常见原因是旧版 B 不支持 0.0.0.0、更新后未重启，"
                                "或启动时仍在使用旧配置。"
                            )
                            suggestions.extend(
                                [
                                    "先在“安装状态”点击“更新 AI 助手 B”。",
                                    "再执行一次“停止→启动”，让新版本和 host=0.0.0.0 生效。",
                                    "稍后点击“诊断公网访问”确认 listener 变为 0.0.0.0。",
                                ]
                            )
                            if old_b_suspected:
                                suggestions.insert(0, "检测到当前 assistant.py 可能不支持 0.0.0.0，请先更新 B。")
                        elif listener_match == "missing":
                            healthy = False
                            error_code = "listener_missing"
                            message = "配置为 0.0.0.0，但未检测到 TCP 端口监听。"
                            suggestions.extend(
                                [
                                    "检查端口是否被其他进程占用。",
                                    "查看最近日志并执行“停止→启动”。",
                                    "确认 AstrBot/B 没有因 Key 或旧版代码启动失败。",
                                ]
                            )
                        elif listener_match == "unexpected":
                            healthy = False
                            error_code = "listener_unexpected"
                            message = "B 的监听地址与 0.0.0.0 配置不一致。"
                            suggestions.append("更新 B 后执行“停止→启动”。")
                        elif listener_match == "unknown":
                            error_code = "listener_unknown"
                            message = "当前平台无法读取实际 listener；本机 health 正常但公网可达性无法确认。"
                            suggestions.append("从外部网络验证 URL，并检查安全组/防火墙。")
                        elif not local_health_ok:
                            healthy = False
                            error_code = "health_failed"
                            message = "端口有 listener，但本机 health 检查失败。"
                            suggestions.append("查看最近日志并重启 B。")
                        elif not public_host:
                            error_code = "public_host_missing"
                            message = "已绑定所有网卡，但缺少 public_host，无法生成公网 URL。"
                            suggestions.append("填写公网 IP/域名；如仅本机使用请改回 127.0.0.1。")
                        elif public_probe.get("status") == "ok":
                            error_code = "ok"
                            message = "本机 listener、health 和公网 health 探测均正常。"
                            suggestions.append("若外部浏览器仍失败，请用另一网络复核 DNS、代理和安全组。")
                        elif public_probe.get("status") == "public_host_missing":
                            error_code = "public_host_missing"
                            message = "缺少 public_host，无法进行公网 URL 探测。"
                            suggestions.append("填写公网 IP/域名后再诊断。")
                        else:
                            probe_status = str(public_probe.get("status") or "unknown")
                            error_code = (
                                "public_probe_dns_error"
                                if probe_status == "dns_error"
                                else "public_probe_inconclusive"
                            )
                            message = "本机已监听所有网卡，但从服务器侧探测公网地址未成功；这不等于外网一定不可达。"
                            suggestions.extend(
                                [
                                    "从一台外部电脑/手机网络访问该 URL；不要在服务器上只凭失败结果判断。",
                                    f"检查云厂商安全组是否放行 TCP {port}（主项目不能替你修改云安全组）。",
                                    f"检查主机防火墙：Ubuntu 可用 `sudo ufw status`，确认允许 TCP {port}。",
                                    "确认 public_host 和端口与实际公网入口一致；NAT 映射需要匹配。",
                                    "若刚更新 B 或改过 host，执行“停止→启动”后再诊断。",
                                ]
                            )
                    else:
                        if listener_match == "missing":
                            healthy = False
                            error_code = "listener_missing"
                            message = "B 配置为回环地址，但未检测到本机 listener。"
                            suggestions.append("查看日志并重新启动。")
                        elif not local_health_ok:
                            healthy = False
                            error_code = "health_failed"
                            message = "B 进程配置看似存在，但本机 health 检查失败。"
                            suggestions.append("查看最近日志并重启 B。")
                        else:
                            error_code = "ok_loopback"
                            message = "AI 助手 B 正在本机回环地址运行；远程访问请使用 SSH 隧道。"
                            if state.get("ssh_tunnel"):
                                suggestions.append("远程访问请使用 SSH 隧道：" + str(state["ssh_tunnel"]))
                            suggestions.append("如暴露公网，必须用安全组/防火墙/VPN/认证反代限制来源。")

                if host == "0.0.0.0" and public_host and running and not suggestions:
                    suggestions.append("公网模式无认证，必须用安全组/防火墙/VPN 限制来源。")
                if error_code != "ok" and host == "0.0.0.0" and not any(
                    "安全组" in item for item in suggestions
                ):
                    suggestions.append("公网模式风险高：请同时检查云安全组和主机防火墙；主项目不会自动开放端口。")

                log_tail = self._read_log_tail(2000) if installed else ""
                return {
                    "ok": True,
                    "healthy": bool(healthy),
                    "installed": installed,
                    "configured": configured,
                    "running": running,
                    "pid": pid,
                    "host": host,
                    "port": port,
                    "public_host": public_host,
                    "url": url,
                    "health_url": health_url,
                    "config_path": str(self.config_path),
                    "listener": listener,
                    "listener_match": listener_match,
                    "listener_ok": (
                        True
                        if listener_match in {"wildcard", "ok"}
                        else False
                        if listener_match in {"missing", "loopback_only", "unexpected"}
                        else None
                    ),
                    "local_health": local_health,
                    "public_probe": public_probe,
                    "public_access_verified": (
                        True
                        if public_probe.get("status") == "ok"
                        else None
                        if public_probe.get("skipped")
                        else False
                    ),
                    "old_b_suspected": bool(old_b_suspected),
                    "b_entry_supports_wildcard": entry_supports,
                    "log_tail": log_tail,
                    "error_code": error_code,
                    "suggestions": list(dict.fromkeys(suggestions)),
                    "recommendations": list(dict.fromkeys(suggestions)),
                    "access_note": state.get("access_note") or self._access_note(host, public_host, url),
                    "ssh_tunnel": state.get("ssh_tunnel") or "",
                    "message": message,
                    "error": "",
                }
            except Exception as exc:  # noqa: BLE001 - diagnostic endpoint isolation
                base = self._config_base_state(installed=False)
                base.update(
                    {
                        "ok": False,
                        "healthy": False,
                        "error_code": "diagnose_failed",
                        "listener": {},
                        "local_health": {"ok": False, "url": "", "detail": "", "error": "diagnose_failed"},
                        "public_probe": {"status": "skipped", "checked": False, "skipped": True},
                        "log_tail": "",
                        "suggestions": ["请查看管理服务终端日志后重试。"],
                        "recommendations": ["请查看管理服务终端日志后重试。"],
                        "message": "AI 助手 B 诊断失败；主控制台不受影响。",
                        "error": self._sanitize_text(exc),
                    }
                )
                return base

    def _runtime_state(self, url: str) -> tuple[bool, int | None]:
        running = self._health_ok(url, timeout=1.2)
        return running, (self._read_pid() if running else None)

    def get_config(self) -> dict[str, Any]:
        """Return safe B config/runtime state; never include the API key."""

        with self._lock:
            try:
                installed = bool(self.status().get("installed"))
            except Exception:
                installed = False
            payload = self._config_base_state(installed=installed)
            raw, read_error = self._read_config_raw()
            if read_error:
                return self._service_failure(
                    "AI 助手 B 配置读取失败。",
                    installed=installed,
                    detail=read_error,
                    state=payload,
                )
            if raw is None:
                try:
                    suggested_port = self._choose_port(None, ASSISTANT_DEFAULT_HOST)
                except Exception:
                    suggested_port = ASSISTANT_DEFAULT_PORT
                payload["port"] = suggested_port
                payload["public_host"] = ""
                payload["url"] = self._service_url(ASSISTANT_DEFAULT_HOST, suggested_port)
                payload["health_url"] = payload["url"]
                payload["access_note"] = self._access_note(ASSISTANT_DEFAULT_HOST)
                payload["ssh_tunnel"] = self._ssh_tunnel(suggested_port)
                payload["message"] = (
                    "AI 助手 B 尚未配置。安装后请设置独立 API Key 或复用已有实例，"
                    "再启动 B。主控制台和其他实例不受影响。"
                )
                return payload

            identity, key_present, identity_error = self._config_identity(raw)
            payload.update(identity)
            payload["key_present"] = key_present
            if identity_error:
                return self._service_failure(
                    "AI 助手 B 配置存在但不完整或无效，请修正后重试。",
                    installed=installed,
                    detail=identity_error,
                    state=payload,
                )

            running, pid = self._runtime_state(str(identity["health_url"]))
            payload["running"] = running
            payload["pid"] = pid
            access_note = str(identity.get("access_note") or "")
            if running:
                if identity.get("url"):
                    payload["message"] = f"AI 助手 B 正在运行：{identity['url']}。{access_note}"
                else:
                    payload["message"] = (
                        "AI 助手 B 正在运行并绑定所有网卡，但尚未填写 public_host，"
                        "没有可点击的浏览器 URL。" + access_note
                    )
            elif key_present:
                payload["message"] = "AI 助手 B 已配置但尚未启动。" + access_note
            else:
                payload["message"] = (
                    "AI 助手 B 配置已存在，但未检测到可用 API Key；"
                    "启动前需要补齐 Key。" + access_note
                )
            payload["ok"] = True
            payload["error"] = ""
            return payload

    # Common alias used by API layers.
    config_status = get_config

    def save_config(
        self,
        payload: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create/update B config safely, preserving hidden existing key values."""

        incoming = dict(payload) if isinstance(payload, Mapping) else {}
        if kwargs:
            incoming.update(kwargs)
        with self._lock:
            try:
                installed = bool(self.status().get("installed"))
            except Exception:
                installed = False
            if not installed:
                return self._service_failure(
                    "AI 助手 B 尚未安装，不能保存配置。请先安装扩展。",
                    installed=False,
                )
            raw, read_error = self._read_config_raw()
            if read_error:
                return self._service_failure(
                    "现有 AI 助手 B 配置损坏，已拒绝覆盖。请先手动备份并修复配置文件。",
                    installed=True,
                    detail=read_error,
                )
            current = dict(raw or {})
            config = {**self._default_config_record(), **current}

            try:
                source = str(incoming.get("key_source") or current.get("key_source") or "independent").strip()
                if source not in ASSISTANT_KEY_SOURCES:
                    raise ValueError("key_source 只能是 independent 或 instance。")

                host = self._validate_host(incoming.get("host", current.get("host", ASSISTANT_DEFAULT_HOST)))
                public_host = self._validate_public_host(
                    incoming.get("public_host", current.get("public_host", ""))
                )
                reserved = self._reserved_ports()
                if "port" in incoming and incoming.get("port") is not None:
                    port = self._validate_port(incoming.get("port"), reserved)
                else:
                    port = self._choose_port(current.get("port"), host)

                key_provided = "api_key" in incoming
                provided_key = ""
                if key_provided:
                    if incoming.get("api_key") is None:
                        key_provided = False
                    elif not isinstance(incoming.get("api_key"), str):
                        raise ValueError("api_key 必须是字符串。")
                    else:
                        provided_key = incoming["api_key"].strip()
                        if len(provided_key) > ASSISTANT_MAX_KEY_LENGTH or "\r" in provided_key or "\n" in provided_key:
                            raise ValueError("api_key 格式异常或过长。")

                config["host"] = host
                config["public_host"] = public_host
                config["port"] = port
                config["key_source"] = source
                config["version"] = int(config.get("version") or ASSISTANT_CONFIG_VERSION)
                if source == "instance":
                    instance_id = str(
                        incoming.get("instance_id", current.get("instance_id", ""))
                    ).strip()
                    if not instance_id:
                        raise ValueError("instance 模式必须提供 instance_id。")
                    if not ASSISTANT_INSTANCE_ID_RE.fullmatch(instance_id):
                        raise ValueError("instance_id 只能包含字母、数字、点、下划线和连字符，且不能以符号开头。")
                    config["instance_id"] = instance_id
                    # Reuse mode must never copy or persist the instance Key.
                    config["api_key"] = ""
                else:
                    config["instance_id"] = ""
                    if not isinstance(config.get("api_key"), str):
                        config["api_key"] = ""
                    if key_provided and provided_key:
                        config["api_key"] = provided_key
                    # An absent or empty api_key keeps the existing hidden value,
                    # so a port-only update does not erase the user's Key.

                self._write_private_json(self.config_path, config)
            except ValueError as exc:
                return self._service_failure(
                    "AI 助手 B 配置校验失败。",
                    installed=True,
                    detail=str(exc),
                    state=self.get_config() if self.config_path.exists() else None,
                )
            except OSError as exc:
                return self._service_failure(
                    "无法安全写入 AI 助手 B 配置。",
                    installed=True,
                    detail=str(exc),
                    state=self.get_config() if self.config_path.exists() else None,
                )

            result = self.get_config()
            result["action"] = "configured"
            if not result.get("configured"):
                result["message"] = (
                    str(result.get("message") or "")
                    + " 当前未检测到可用 Key；可稍后补充后重启 B。"
                ).strip()
            return result

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    def _terminate_process(process: Any) -> None:
        try:
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                terminate()
        except Exception:
            pass
        try:
            wait = getattr(process, "wait", None)
            if callable(wait):
                wait(timeout=2)
                return
        except Exception:
            pass
        try:
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()
        except Exception:
            pass

    def start(self) -> dict[str, Any]:
        """Start B as a detached local process, idempotently."""

        with self._lock:
            state = self.get_config()
            installed = bool(state.get("installed"))
            if not installed:
                return self._service_failure(
                    "AI 助手 B 尚未安装，不能启动。",
                    installed=False,
                    state=state,
                )
            if state.get("error"):
                return self._service_failure(
                    "AI 助手 B 配置无效，不能启动。",
                    installed=True,
                    detail=str(state.get("error")),
                    state=state,
                )
            if not self.config_path.exists():
                return self._service_failure(
                    "AI 助手 B 尚未配置，请先保存 Key、端口和 host。",
                    installed=True,
                    state=state,
                )

            url = str(state.get("url") or "")
            probe_url = str(state.get("health_url") or url)
            if state.get("running"):
                already = dict(state)
                already["action"] = "already_running"
                already["ok"] = True
                already["running"] = True
                already["message"] = "AI 助手 B 已在运行，未重复启动。"
                host = str(state.get("host") or ASSISTANT_DEFAULT_HOST)
                try:
                    port = int(state.get("port"))
                except (TypeError, ValueError):
                    port = ASSISTANT_DEFAULT_PORT
                if host == "0.0.0.0":
                    listener = self._listener_snapshot(port)
                    listener_match = self._listener_match(host, port, listener)
                    entry_supports = self._entry_supports_wildcard()
                    already["listener"] = listener
                    already["listener_match"] = listener_match
                    already["b_entry_supports_wildcard"] = entry_supports
                    if listener.get("supported") and listener_match in {
                        "loopback_only",
                        "unexpected",
                        "missing",
                    }:
                        already["ok"] = False
                        already["action"] = "listener_mismatch"
                        already["error_code"] = (
                            "listener_loopback_only"
                            if listener_match == "loopback_only"
                            else "listener_missing"
                            if listener_match == "missing"
                            else "listener_unexpected"
                        )
                        already["listener_ok"] = False
                        already["old_b_suspected"] = bool(
                            listener_match == "loopback_only"
                            and entry_supports is False
                        )
                        suggestions = [
                            "先在“安装状态”点击“更新 AI 助手 B”。",
                            "再执行“停止→启动”，让新版本和 host=0.0.0.0 生效。",
                            "点击“诊断公网访问”确认实际 listener 是否为 0.0.0.0。",
                            "检查云安全组和主机防火墙；主项目不会自动开放云端口。",
                        ]
                        already["suggestions"] = suggestions
                        already["recommendations"] = list(suggestions)
                        already["message"] = (
                            "AI 助手 B 虽在响应 health，但实际监听地址与 host=0.0.0.0 不一致；"
                            "当前进程可能是旧版 B 或未重启。请先更新，再停止→启动。"
                        )
                        already["error"] = already["message"]
                        return already
                    if not listener.get("supported"):
                        already["message"] += (
                            " 当前平台无法验证实际 listener；请使用“诊断公网访问”并从外部网络确认。"
                        )
                    else:
                        already["message"] += " 已确认实际监听所有网卡。"
                    already["error_code"] = "ok"
                    already["suggestions"] = []
                    already["recommendations"] = []
                return already
            if not state.get("configured"):
                return self._service_failure(
                    "AI 助手 B 尚未配置可用 API Key，不能启动。"
                    "请选择 independent 并输入 Key，或选择 instance 后确认实例 AstrBot Key 可读取。",
                    installed=True,
                    state=state,
                )

            entry = self.extension_dir / ASSISTANT_ENTRY_FILE
            if not entry.is_file():
                return self._service_failure(
                    "AI 助手 B 入口 assistant.py 缺失，不能启动。",
                    installed=True,
                    state=state,
                )

            host = str(state.get("host") or ASSISTANT_DEFAULT_HOST)
            try:
                port = int(state.get("port"))
            except (TypeError, ValueError):
                return self._service_failure(
                    "AI 助手 B 端口配置无效，不能启动。",
                    installed=True,
                    detail="配置中的 port 不是有效整数。",
                    state=state,
                )
            if not self._port_is_free(host, port):
                # A foreign process may already own the port.  The health check
                # above distinguishes the managed B; this branch is actionable.
                failure = self._service_failure(
                    f"AI 助手 B 端口 {port} 已被占用，请修改端口后重试。",
                    installed=True,
                    state=state,
                )
                failure["error_code"] = "port_occupied"
                failure["listener"] = self._listener_snapshot(port)
                failure["suggestions"] = [
                    f"端口 {port} 已被其他进程占用；请换一个 5000–10000 的未占用端口。",
                    "先停止旧的 B 进程，再重新“停止→启动”。",
                    "诊断接口会显示实际 listener，但不会替你修改安全组。",
                ]
                failure["recommendations"] = list(failure["suggestions"])
                return failure

            self._ensure_private_dir()
            self._remove_pid()
            log_handle = None
            process = None
            try:
                log_fd = os.open(
                    self.log_path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                log_handle = os.fdopen(log_fd, "ab", buffering=0)
                try:
                    os.chmod(self.log_path, 0o600)
                except OSError:
                    pass
                env = os.environ.copy()
                # B must locate the same instances directory when reusing an
                # instance Key.  No API key is passed via argv/env.
                env["QQBOT_STUDIO_HOME"] = str(self.data_dir)
                env["PYTHONUNBUFFERED"] = "1"
                command = [
                    str(sys.executable or "python3"),
                    str(entry),
                    "--config",
                    str(self.config_path),
                ]
                popen_kwargs: dict[str, Any] = {
                    "cwd": str(self.extension_dir),
                    "env": env,
                    "stdin": subprocess.DEVNULL,
                    "stdout": log_handle,
                    "stderr": subprocess.STDOUT,
                    "shell": False,
                    "close_fds": os.name != "nt",
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = getattr(
                        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                    )
                else:
                    popen_kwargs["start_new_session"] = True
                process = self._popen_factory(command, **popen_kwargs)
                pid = int(getattr(process, "pid", 0) or 0)
                if pid <= 1:
                    self._terminate_process(process)
                    return self._service_failure(
                        "AI 助手 B 启动失败：未获得有效进程 ID。",
                        installed=True,
                        state=state,
                    )
                self._write_pid(pid)
            except Exception as exc:  # noqa: BLE001 - process startup isolation
                if process is not None:
                    self._terminate_process(process)
                self._remove_pid()
                return self._service_failure(
                    "AI 助手 B 启动失败：无法创建本地进程或日志文件。",
                    installed=True,
                    detail=self._sanitize_text(exc),
                    state=state,
                )
            finally:
                if log_handle is not None:
                    try:
                        log_handle.close()
                    except OSError:
                        pass

            deadline = time.monotonic() + self.start_timeout
            last_error = ""
            while time.monotonic() < deadline:
                if self._health_ok(probe_url, timeout=1.0):
                    result = self.get_config()
                    result["ok"] = True
                    result["action"] = "started"
                    result["running"] = True
                    result["pid"] = pid
                    listener = self._listener_snapshot(port)
                    listener_match = self._listener_match(host, port, listener)
                    entry_supports_wildcard = self._entry_supports_wildcard()
                    result["listener"] = listener
                    result["listener_match"] = listener_match
                    result["b_entry_supports_wildcard"] = entry_supports_wildcard
                    result["local_health"] = {
                        "ok": True,
                        "url": probe_url,
                        "detail": "启动后本机 health 检查成功。",
                        "error": "",
                    }
                    if host == "0.0.0.0" and listener.get("supported") and listener_match in {
                        "loopback_only",
                        "missing",
                        "unexpected",
                    }:
                        # A public-mode process that still binds only loopback is
                        # not a successful start.  Stop the just-started process
                        # so the UI does not show a misleading running state.
                        self._terminate_process(process)
                        self._remove_pid()
                        result["ok"] = False
                        result["running"] = False
                        result["pid"] = None
                        result["action"] = "listener_mismatch"
                        result["error_code"] = (
                            "listener_loopback_only"
                            if listener_match == "loopback_only"
                            else "listener_missing"
                            if listener_match == "missing"
                            else "listener_unexpected"
                        )
                        result["old_b_suspected"] = bool(
                            listener_match == "loopback_only"
                            and entry_supports_wildcard is False
                        )
                        result["listener_ok"] = False
                        suggestions = [
                            "先在“安装状态”点击“更新 AI 助手 B”。",
                            "再执行“停止→启动”，确保新版本和 host=0.0.0.0 生效。",
                            "点击“诊断公网访问”确认实际 listener 是否为 0.0.0.0。",
                            "检查云安全组和主机防火墙；主项目不会自动开放云端口。",
                        ]
                        if listener_match == "loopback_only":
                            result["message"] = (
                                "B 健康检查成功但实际仍只监听 127.0.0.1/::1，host=0.0.0.0 "
                                "公网模式未生效。常见原因是旧版 B、更新后未重启，或运行中的仍是旧配置；"
                                "已停止该错误进程。"
                            )
                        elif listener_match == "missing":
                            result["message"] = (
                                "B 健康检查成功但未读取到端口 listener，公网绑定无法确认；已停止该进程。"
                            )
                        else:
                            result["message"] = (
                                "B 的实际监听地址与 host=0.0.0.0 不一致，公网模式未生效；已停止该进程。"
                            )
                        result["suggestions"] = suggestions
                        result["recommendations"] = list(suggestions)
                        result["error"] = result["message"]
                        return result
                    if result.get("url"):
                        result["message"] = (
                            f"AI 助手 B 已启动：{result['url']}。"
                            f"{result.get('access_note') or ''}"
                        )
                    else:
                        result["message"] = (
                            "AI 助手 B 已启动并绑定所有网卡，但未填写 public_host，"
                            "暂无可点击的浏览器 URL。"
                            f"{result.get('access_note') or ''}"
                        )
                    result["error_code"] = "ok"
                    result["suggestions"] = []
                    result["recommendations"] = []
                    if host == "0.0.0.0" and not listener.get("supported"):
                        result["message"] += (
                            " 当前平台无法验证实际 listener；请点击“诊断公网访问”并从外部网络确认。"
                        )
                    elif host == "0.0.0.0":
                        result["message"] += " 已确认实际监听所有网卡。"
                    result["error"] = ""
                    return result
                try:
                    return_code = process.poll()
                except Exception:
                    return_code = None
                if return_code is not None:
                    last_error = f"B 进程提前退出，退出码 {return_code}。"
                    break
                time.sleep(min(self.poll_interval, max(0.01, deadline - time.monotonic())))

            self._terminate_process(process)
            self._remove_pid()
            log_tail = self._read_log_tail(1200)
            detail = last_error or f"等待 AI 助手 B 健康检查超时（{self.start_timeout:g} 秒）。"
            if log_tail:
                detail += " 最近日志：" + log_tail
            failure = self._service_failure(
                "AI 助手 B 启动失败或未就绪；主控制台和其他实例不受影响。",
                installed=True,
                detail=detail,
                state=state,
            )
            failure["error_code"] = "start_failed"
            failure["suggestions"] = [
                "查看最近的脱敏日志摘要，确认 B 的依赖/Key/配置是否有效。",
                "旧版 B 请先更新，再执行“停止→启动”。",
                "公网模式仍需单独检查云安全组和主机防火墙。",
            ]
            failure["recommendations"] = list(failure["suggestions"])
            return failure

    def stop(self) -> dict[str, Any]:
        """Stop the tracked B process.  Missing/stale PIDs degrade safely."""

        with self._lock:
            state = self.get_config()
            if not state.get("running"):
                self._remove_pid()
                stopped = dict(state)
                stopped["ok"] = True
                stopped["running"] = False
                stopped["pid"] = None
                stopped["action"] = "stopped"
                stopped["message"] = "AI 助手 B 当前未在运行，无需停止。"
                return stopped

            pid = state.get("pid") or self._read_pid()
            if not pid:
                return self._service_failure(
                    "AI 助手 B 正在响应健康检查，但没有受主项目管理的 PID；"
                    "为避免误杀其他进程，请手动停止它。",
                    installed=bool(state.get("installed")),
                    state=state,
                )
            pid = int(pid)
            if pid <= 1 or pid == os.getpid():
                return self._service_failure(
                    "AI 助手 B PID 文件异常，拒绝停止，避免误杀其他进程。",
                    installed=bool(state.get("installed")),
                    state=state,
                )
            if os.name != "nt":
                proc_cmdline = Path("/proc") / str(pid) / "cmdline"
                try:
                    cmdline = proc_cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
                except OSError:
                    cmdline = ""
                if cmdline and "assistant.py" not in cmdline:
                    return self._service_failure(
                        "AI 助手 B PID 与进程命令行不匹配，拒绝停止，请手动检查。",
                        installed=bool(state.get("installed")),
                        state=state,
                    )

            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                self._remove_pid()
            except OSError as exc:
                return self._service_failure(
                    "无法向 AI 助手 B 发送停止信号。",
                    installed=bool(state.get("installed")),
                    detail=self._sanitize_text(exc),
                    state=state,
                )

            probe_url = str(state.get("health_url") or state.get("url") or "")
            deadline = time.monotonic() + self.stop_timeout
            while time.monotonic() < deadline:
                if not self._health_ok(probe_url, timeout=0.8):
                    break
                time.sleep(min(self.poll_interval, max(0.01, deadline - time.monotonic())))

            if self._health_ok(probe_url, timeout=0.8):
                return self._service_failure(
                    "AI 助手 B 停止超时，可能仍在运行；请查看日志并手动停止。",
                    installed=bool(state.get("installed")),
                    state=state,
                )

            self._remove_pid()
            final = self.get_config()
            final["ok"] = True
            final["running"] = False
            final["pid"] = None
            final["action"] = "stopped"
            final["message"] = "AI 助手 B 已停止；主控制台和其他实例不受影响。"
            final["error"] = ""
            return final

    @staticmethod
    def _remove_temp(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError:
            pass
