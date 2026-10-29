"""Optional AI assistant B extension management.

The assistant is an independent repository and is deliberately *not* embedded
in this project.  This module only clones/checks out that repository into the
Studio data directory.  It never reads assistant configuration, API keys or
starts the assistant process.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ASSISTANT_REPO_URL = "https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git"
ASSISTANT_DIR_NAME = "ai-assistant"
ASSISTANT_ENTRY_FILE = "assistant.py"
ASSISTANT_README_FILE = "README.md"

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
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.extensions_dir = self.data_dir / "extensions"
        self.extension_dir = self.extensions_dir / ASSISTANT_DIR_NAME
        self.repo_url = str(repo_url or ASSISTANT_REPO_URL)
        self.clone_timeout = max(10, int(clone_timeout))
        self.git_timeout = max(5, int(git_timeout))
        self._runner = runner or _default_git_runner
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
        # Even the official HTTPS remote has no credentials, but a manually
        # configured origin could.  Never bubble authenticated URLs or
        # secret-shaped values into an API response.
        text = re.sub(r"(?i)(https?://)[^\s/@:]+(?::[^\s/@]*)?@", r"\1***@", text)
        text = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1***", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}\b", "sk-***", text)
        return text[-limit:]

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

    @staticmethod
    def _remove_temp(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError:
            pass
