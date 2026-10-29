"""Optional AI assistant B extension management.

The assistant is an independent repository and is deliberately *not* embedded
in this project.  This module only clones/checks out that repository into the
Studio data directory.  It never reads assistant configuration, API keys or
starts the assistant process.
"""

from __future__ import annotations

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

ASSISTANT_REPO_URL = "https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git"
ASSISTANT_DIR_NAME = "ai-assistant"
ASSISTANT_ENTRY_FILE = "assistant.py"
ASSISTANT_README_FILE = "README.md"

ASSISTANT_CONFIG_VERSION = 1
ASSISTANT_DEFAULT_HOST = "127.0.0.1"
ASSISTANT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")
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
            "message": "AI 助手 B 尚未安装。A 离线助手不受影响。",
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
                    "下载 AI 助手 B 失败；主项目和助手 A 不受影响。可检查网络后重试。",
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
        value = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)\S+", r"\1***", value)
        return value

    @staticmethod
    def _service_url(host: str, port: int) -> str:
        normalized = str(host or ASSISTANT_DEFAULT_HOST).strip().lower()
        if normalized == "::1":
            return f"http://[::1]:{int(port)}/"
        if normalized == "localhost":
            return f"http://localhost:{int(port)}/"
        return f"http://127.0.0.1:{int(port)}/"

    @staticmethod
    def _access_note() -> str:
        return (
            "AI 助手 B 没有面向用户的访问认证，只允许监听本机回环地址。"
            "如果从远端电脑访问，请使用 SSH 隧道或在本机浏览器打开，"
            "不要把 B 的端口直接暴露到公网。"
        )

    @staticmethod
    def _ssh_tunnel(port: int) -> str:
        return f"ssh -N -L {int(port)}:127.0.0.1:{int(port)} <用户名>@<服务器地址>"

    def _config_base_state(self, *, installed: bool) -> dict[str, Any]:
        port = ASSISTANT_DEFAULT_PORT
        return {
            "ok": True,
            "installed": bool(installed),
            "configured": False,
            "running": False,
            "url": "",
            "configure_url": "/api/assistant/config",
            "config_path": str(self.config_path),
            "key_present": False,
            "key_source": "independent",
            "instance_id": "",
            "port": port,
            "host": ASSISTANT_DEFAULT_HOST,
            "pid": None,
            "log_path": str(self.log_path),
            "pid_path": str(self.pid_path),
            "access_note": self._access_note(),
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
        if host == "0.0.0.0":
            raise ValueError(
                "出于安全原因，主项目不允许把 AI 助手 B 绑定到 0.0.0.0；"
                "B 只支持回环地址。远端访问请使用 SSH 隧道。"
            )
        if host not in ASSISTANT_ALLOWED_HOSTS:
            raise ValueError("host 只能是 127.0.0.1、localhost 或 ::1。")
        return host

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
        key_present = False
        if source == "independent":
            key = raw.get("api_key")
            key_present = isinstance(key, str) and bool(key.strip())
        else:
            key_present = bool(instance_id) and self._instance_key_present(instance_id)
        identity = {
            "host": host,
            "port": port,
            "url": self._service_url(host, port),
            "key_source": source,
            "instance_id": instance_id,
            "key_present": key_present,
            "configured": bool(key_present and not error),
            "ssh_tunnel": self._ssh_tunnel(port),
            "access_note": self._access_note(),
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
                payload["url"] = self._service_url(ASSISTANT_DEFAULT_HOST, suggested_port)
                payload["ssh_tunnel"] = self._ssh_tunnel(suggested_port)
                payload["message"] = (
                    "AI 助手 B 尚未配置。安装后请设置独立 API Key 或复用已有实例，"
                    "再启动 B。A 离线助手不受影响。"
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

            running, pid = self._runtime_state(str(identity["url"]))
            payload["running"] = running
            payload["pid"] = pid
            if running:
                payload["message"] = (
                    f"AI 助手 B 正在运行：{identity['url']}。"
                    "该服务仅监听回环地址；远端请使用 SSH 隧道。"
                )
            elif key_present:
                payload["message"] = (
                    "AI 助手 B 已配置但尚未启动。启动后仅可从本机或 SSH 隧道访问。"
                )
            else:
                payload["message"] = (
                    "AI 助手 B 配置已存在，但未检测到可用 API Key；"
                    "可启动查看界面，但聊天前需要补齐 Key。"
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
            if state.get("running"):
                already = dict(state)
                already["action"] = "already_running"
                already["ok"] = True
                already["running"] = True
                already["message"] = "AI 助手 B 已在运行，未重复启动。"
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
                return self._service_failure(
                    f"AI 助手 B 端口 {port} 已被占用，请修改端口后重试。",
                    installed=True,
                    state=state,
                )

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
                if self._health_ok(url, timeout=1.0):
                    result = self.get_config()
                    result["ok"] = True
                    result["action"] = "started"
                    result["running"] = True
                    result["pid"] = pid
                    result["message"] = (
                        f"AI 助手 B 已启动：{url}。"
                        "服务仅监听回环地址；远端访问请使用 SSH 隧道，不要暴露到公网。"
                    )
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
            return self._service_failure(
                "AI 助手 B 启动失败或未就绪；主项目和 A 离线助手不受影响。",
                installed=True,
                detail=detail,
                state=state,
            )

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

            deadline = time.monotonic() + self.stop_timeout
            while time.monotonic() < deadline:
                if not self._health_ok(str(state.get("url") or ""), timeout=0.8):
                    break
                time.sleep(min(self.poll_interval, max(0.01, deadline - time.monotonic())))

            if self._health_ok(str(state.get("url") or ""), timeout=0.8):
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
            final["message"] = "AI 助手 B 已停止；主项目和 A 离线助手不受影响。"
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
