"""Safe Git-based updater for QQ Bot Studio."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from .store import default_data_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GitRunner = Callable[[Sequence[str], Path, int], subprocess.CompletedProcess[str]]


class UpdateError(RuntimeError):
    """User-facing update failure."""


def _default_git_runner(
    argv: Sequence[str],
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if os.name != "nt":
        env.setdefault("GIT_ASKPASS", "/bin/true")
        # Avoid an unattended management server hanging on an SSH host-key
        # prompt.  The Windows Git port does not use /bin/true; leave its
        # configured credential helper and SSH agent untouched.
        env.setdefault(
            "GIT_SSH_COMMAND",
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            "-o ConnectTimeout=15 -o ControlMaster=no -o ControlPath=none",
        )
    return subprocess.run(
        [str(item) for item in argv],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git(
    root: Path,
    *args: str,
    timeout: int = 60,
    runner: GitRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    return (runner or _default_git_runner)(
        ["git", "-C", str(root), *args],
        root,
        timeout,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    text = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return text[-1500:]


def check_update(
    root: Path | str | None = None,
    *,
    runner: GitRunner | None = None,
) -> dict:
    """Fetch the configured remote and report whether a fast-forward is available."""

    repo = Path(root or PROJECT_ROOT).expanduser().resolve()
    if not (repo / ".git").exists():
        return {
            "ok": False,
            "can_update": False,
            "message": f"当前目录不是 Git 仓库：{repo}",
        }

    dirty = _git(repo, "status", "--porcelain", runner=runner)
    if not dirty.returncode == 0:
        return {"ok": False, "can_update": False, "message": _output(dirty) or "无法检查 Git 工作区。"}
    if dirty.stdout.strip():
        return {
            "ok": False,
            "can_update": False,
            "message": "代码目录有未提交的修改；自动更新已停止，请先在服务器处理这些修改。",
        }

    fetch = _git(repo, "fetch", "origin", "--prune", timeout=120, runner=runner)
    if fetch.returncode != 0:
        return {
            "ok": False,
            "can_update": False,
            "message": "无法从 origin 获取更新："
            + (_output(fetch) or "请检查服务器是否能访问 GitHub，以及 Git 凭据是否有效。"),
        }

    local = _git(repo, "rev-parse", "--short", "HEAD", runner=runner)
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", runner=runner)
    if local.returncode != 0 or branch.returncode != 0:
        return {"ok": False, "can_update": False, "message": _output(local) or "无法读取当前 Git 版本。"}

    branch_name = branch.stdout.strip() or "main"
    upstream = f"origin/{branch_name}"
    remote = _git(repo, "rev-parse", "--short", upstream, runner=runner)
    if remote.returncode != 0:
        return {
            "ok": False,
            "can_update": False,
            "message": f"找不到远端分支 {upstream}；请确认 origin 和当前分支。",
        }

    counts = _git(
        repo,
        "rev-list",
        "--left-right",
        "--count",
        f"HEAD...{upstream}",
        runner=runner,
    )
    if counts.returncode != 0:
        return {"ok": False, "can_update": False, "message": _output(counts) or "无法比较版本。"}
    parts = counts.stdout.strip().split()
    ahead = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else 0
    behind = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    can_update = behind > 0 and ahead == 0
    if behind == 0:
        message = "当前已经是最新版本。"
    elif ahead > 0:
        message = f"本地有 {ahead} 个提交领先、远端有 {behind} 个提交，无法自动快进。"
    else:
        message = f"发现 {behind} 个新提交，可以自动更新。"
    return {
        "ok": True,
        "can_update": can_update,
        "branch": branch_name,
        "upstream": upstream,
        "current": local.stdout.strip(),
        "remote": remote.stdout.strip(),
        "ahead": ahead,
        "behind": behind,
        "message": message,
    }


def start_update(
    root: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    runner: GitRunner | None = None,
) -> dict:
    """Validate and launch the detached update/restart script."""

    if os.name == "nt":
        raise UpdateError(
            "Windows 初始移植版暂不支持网页内一键更新；请在 PowerShell 中手动 git pull，"
            "然后运行 windows\\console.ps1 -RestartService。"
        )
    repo = Path(root or PROJECT_ROOT).expanduser().resolve()
    info = check_update(repo, runner=runner)
    if not info.get("ok"):
        raise UpdateError(str(info.get("message") or "自动更新不可用。"))
    if not info.get("can_update"):
        raise UpdateError(str(info.get("message") or "当前没有可自动应用的更新。"))
    script = repo / "scripts" / "auto-update.sh"
    if not script.is_file():
        raise UpdateError("找不到 scripts/auto-update.sh。")
    data = Path(data_dir).expanduser().resolve() if data_dir else default_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["QQBOT_AUTO_UPDATE_ROOT"] = str(repo)
    try:
        subprocess.Popen(
            ["bash", str(script), str(data)],
            cwd=str(repo),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise UpdateError(f"无法启动自动更新脚本：{exc}") from exc
    return {
        "ok": True,
        "restarting": True,
        "message": "整项目更新脚本已启动，管理服务会在几秒后重启。请稍后刷新页面。",
        "current": info.get("current"),
        "remote": info.get("remote"),
        "behind": info.get("behind"),
    }
