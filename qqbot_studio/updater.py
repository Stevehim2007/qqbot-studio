"""Safe Git-based updater: update channels, same-major rollback, history.

Channels
--------
``stable``
    Follow formal GitHub releases on ``origin/main`` (fast-forward only).
``test``
    Automatically discover the newest prerelease tag (``vX.Y.Z-alpha.N``,
    ``vX.Y.Z-beta.N`` or ``vX.Y.Z-rc.N``). Arbitrary branches, commits or URLs
    are never accepted.

Rollback
--------
Any formal tag inside the *current major* version (``v4.x`` -> any ``v4.*``
tag), not just the previous stable. Cross-major rollback is refused. Only exact
tag names survive validation, so a browser cannot inject a ref, commit hash or
URL.

Safety
------
A clean worktree and an exclusive lock are mandatory. Before switching, the
current code (git bundle) and the instance data/config/secrets files are backed
up. Data directories are never written by the updater. A failed switch restores
the original ref, and every attempt is recorded in a sanitized history file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from .gitutil import run_git
from .store import default_data_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GitRunner = Callable[[Sequence[str], Path, int], subprocess.CompletedProcess[str]]

CHANNEL_LABELS = {"stable": "稳定版", "test": "测试版"}
CHANNELS = tuple(CHANNEL_LABELS)
FORMAL_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Test channel is tag-driven: every prerelease tag matching this strict shape
# is discoverable, and the newest one is selected automatically.
TEST_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-(alpha|beta|rc)\.(\d+)$")
TEST_PRERELEASE_RANK = {"alpha": 0, "beta": 1, "rc": 2}
HISTORY_LIMIT = 50
LOCK_STALE_SECONDS = 30 * 60
BACKUP_KEEP = 8
CONFIG_BACKUP_FILES = ("studio.json", "config.json", "secrets.json", "deploy-state.json", ".env")
INSTANCE_BACKUP_FILES = ("instance.json", "config.json", "secrets.json", ".env", "compose.yml")
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|access[_-]?key)=([^&\s\"']+)"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{16,}|xox[abprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"),
)


class UpdateError(RuntimeError):
    """User-facing update failure."""


# ----------------------------------------------------------------------
# Git plumbing
# ----------------------------------------------------------------------
def _git(
    root: Path,
    *args: str,
    timeout: int = 60,
    runner: GitRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    return (runner or run_git)(["git", "-C", str(root), *args], root, timeout)


def _output(result: subprocess.CompletedProcess[str]) -> str:
    text = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return text[-1500:]


def sanitize_message(text: str) -> str:
    """Strip credentials and tokens from anything shown or persisted."""

    cleaned = str(text or "")
    cleaned = SECRET_PATTERNS[1].sub("***", cleaned)
    cleaned = SECRET_PATTERNS[2].sub(r"\1***@", cleaned)
    cleaned = SECRET_PATTERNS[0].sub(r"\1=***", cleaned)
    return cleaned[:1500]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _repo(root: Path | str | None) -> Path:
    repo = Path(root or PROJECT_ROOT).expanduser().resolve()
    if not (repo / ".git").exists():
        raise UpdateError(f"当前目录不是 Git 仓库：{repo}")
    return repo


def _data_dir(data_dir: Path | str | None) -> Path:
    return Path(data_dir).expanduser().resolve() if data_dir else default_data_dir()


def _parse_formal_tag(name: str) -> tuple[int, int, int] | None:
    match = FORMAL_TAG_RE.match(str(name or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def _parse_test_tag(name: str) -> tuple[int, int, int, str, int] | None:
    match = TEST_TAG_RE.match(str(name or "").strip())
    if not match:
        return None
    major, minor, patch, stage, number = match.groups()
    return (int(major), int(minor), int(patch), stage, int(number))


def _resolve_commit(repo: Path, rev: str, runner: GitRunner | None) -> str:
    result = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}", runner=runner)
    return result.stdout.strip() if result.returncode == 0 else ""


def _fetch(repo: Path, runner: GitRunner | None) -> tuple[bool, str]:
    result = _git(repo, "fetch", "--tags", "--prune", "origin", timeout=120, runner=runner)
    if result.returncode == 0:
        return True, ""
    return False, sanitize_message(_output(result) or "无法从 origin 获取更新。")


def _status_text(repo: Path, runner: GitRunner | None) -> tuple[str | None, str]:
    result = _git(repo, "status", "--porcelain", runner=runner)
    if result.returncode != 0:
        return None, sanitize_message(_output(result) or "无法检查 Git 工作区。")
    return result.stdout, ""


def _require_clean(repo: Path, runner: GitRunner | None) -> None:
    status, error = _status_text(repo, runner)
    if status is None:
        raise UpdateError(error)
    if status.strip():
        raise UpdateError(
            "代码目录有未提交的修改；自动更新/回滚已停止，请先在服务器处理这些修改。"
        )


def _head_info(repo: Path, runner: GitRunner | None) -> dict[str, Any]:
    commit = _git(repo, "rev-parse", "--short", "HEAD", runner=runner)
    full = _git(repo, "rev-parse", "HEAD", runner=runner)
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", runner=runner)
    if commit.returncode != 0 or full.returncode != 0 or branch.returncode != 0:
        raise UpdateError(sanitize_message(_output(commit) or "无法读取当前 Git 版本。"))

    exact = _git(repo, "describe", "--tags", "--exact-match", "HEAD", runner=runner)
    exact_tag = exact.stdout.strip() if exact.returncode == 0 else ""
    if exact_tag and not (_parse_formal_tag(exact_tag) or _parse_test_tag(exact_tag)):
        exact_tag = ""
    nearest = _git(repo, "describe", "--tags", "--abbrev=0", runner=runner)
    nearest_tag = nearest.stdout.strip() if nearest.returncode == 0 else ""

    test_tag = exact_tag if _parse_test_tag(exact_tag) else ""
    if not test_tag and _parse_test_tag(nearest_tag):
        test_tag = nearest_tag
    version_tuple = _parse_formal_tag(exact_tag) or _parse_formal_tag(nearest_tag)
    if test_tag:
        parsed_test = _parse_test_tag(test_tag)
        version = test_tag.lstrip("v")
        major = parsed_test[0] if parsed_test else 0
    elif version_tuple:
        version = ".".join(str(part) for part in version_tuple)
        major = version_tuple[0]
    else:
        version = __version__
        try:
            major = int(str(__version__).split(".")[0])
        except ValueError:
            major = 0

    branch_name = branch.stdout.strip()
    detached = branch_name in ("", "HEAD")
    channel = "test" if exact_tag and _parse_test_tag(exact_tag) else "stable"

    return {
        "version": version,
        "major": major,
        "tag": exact_tag,
        "nearest_tag": nearest_tag,
        "commit": commit.stdout.strip(),
        "commit_full": full.stdout.strip(),
        "branch": "" if detached else branch_name,
        "detached": detached,
        "channel": channel,
    }


# ----------------------------------------------------------------------
# Locks / history / backups
# ----------------------------------------------------------------------
def _lock_path(data_dir: Path) -> Path:
    return data_dir / "update.lock"


def lock_active(data_dir: Path | str | None) -> bool:
    path = _lock_path(_data_dir(data_dir))
    try:
        return path.exists() and (time.time() - path.stat().st_mtime) < LOCK_STALE_SECONDS
    except OSError:
        return False


def _acquire_lock(data_dir: Path) -> Path:
    path = _lock_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and time.time() - path.stat().st_mtime >= LOCK_STALE_SECONDS:
            path.unlink()
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise UpdateError("已有更新或回滚任务正在运行，请等待它完成后再试。") from exc
    except OSError as exc:
        raise UpdateError(f"无法创建更新锁：{sanitize_message(str(exc))}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"pid={os.getpid()} at={_now_iso()}\n")
    return path


def _release_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def history_path(data_dir: Path | str | None) -> Path:
    return _data_dir(data_dir) / "update-history.json"


def load_history(data_dir: Path | str | None) -> list[dict[str, Any]]:
    path = history_path(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    records = raw if isinstance(raw, list) else raw.get("records") if isinstance(raw, dict) else []
    return [item for item in records if isinstance(item, dict)][:HISTORY_LIMIT]


def _append_history(data_dir: Path, record: dict[str, Any]) -> None:
    path_target = history_path(data_dir)
    records = load_history(data_dir)
    record = dict(record)
    record["message"] = sanitize_message(str(record.get("message") or ""))
    records.insert(0, record)
    del records[HISTORY_LIMIT:]
    path_target.parent.mkdir(parents=True, exist_ok=True)
    temporary = path_target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path_target)


def _backup(repo: Path, data_dir: Path, label: str, runner: GitRunner | None) -> dict[str, Any]:
    """Back up code (git bundle) plus instance config/data/secrets files."""

    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label)[:60] or "backup"
    destination = data_dir / "backups" / f"{stamp}-{safe_label}"
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o700)

    bundle = destination / "code.bundle"
    result = _git(repo, "bundle", "create", str(bundle), "--all", timeout=300, runner=runner)
    if result.returncode != 0:
        raise UpdateError(
            "代码备份失败，已停止切换：" + sanitize_message(_output(result) or "git bundle 失败。")
        )

    copied: list[str] = []
    data_snapshot = destination / "data"
    for name in CONFIG_BACKUP_FILES:
        source = data_dir / name
        if source.is_file():
            target = data_snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(f"data/{name}")

    inventory: dict[str, dict[str, Any]] = {}
    instances = data_dir / "instances"
    if instances.is_dir():
        for instance in sorted(instances.iterdir()):
            if not instance.is_dir():
                continue
            inventory[instance.name] = _inventory(instance)
            for name in INSTANCE_BACKUP_FILES:
                source = instance / name
                if not source.is_file():
                    continue
                target = data_snapshot / "instances" / instance.name / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(f"data/instances/{instance.name}/{name}")

    manifest = {
        "created_at": _now_iso(),
        "label": label,
        "code_bundle": str(bundle),
        "files": copied,
        # Instance data directories are never written by the updater, so only
        # their size is recorded; copying gigabytes during an HTTP request would
        # be pointless. Config/secrets files above are the restorable state.
        "instance_data": inventory,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _prune_backups(data_dir)
    return {"path": str(destination), "files": copied}


def _inventory(root: Path, limit: int = 20000) -> dict[str, Any]:
    """Bounded size/count snapshot of an instance data directory."""

    total = 0
    files = 0
    for base, _directories, names in os.walk(root):
        for name in names:
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                continue
            files += 1
            if files >= limit:
                return {"files": files, "bytes": total, "truncated": True}
    return {"files": files, "bytes": total, "truncated": False}


def _prune_backups(data_dir: Path) -> None:
    backups = data_dir / "backups"
    if not backups.is_dir():
        return
    try:
        entries = sorted(
            (entry for entry in backups.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name,
        )
    except OSError:
        return
    for stale in entries[:-BACKUP_KEEP]:
        shutil.rmtree(stale, ignore_errors=True)


# ----------------------------------------------------------------------
# Schema compatibility
# ----------------------------------------------------------------------
def _data_schema_versions(data_dir: Path) -> dict[str, int]:
    found: dict[str, int] = {}

    def read(path: Path, kind: str) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        try:
            found[kind] = max(found.get(kind, 0), int(data.get("schema_version") or 0))
        except (TypeError, ValueError):
            return

    read(data_dir / "studio.json", "studio")
    read(data_dir / "config.json", "config")
    instances = data_dir / "instances"
    if instances.is_dir():
        for instance in sorted(instances.iterdir()):
            if instance.is_dir():
                read(instance / "config.json", "config")
    return found


def _declared_schema_versions(repo: Path, rev: str, runner: GitRunner | None) -> dict[str, int]:
    declared: dict[str, int] = {}
    sources = (
        ("qqbot_studio/store.py", "CONFIG_SCHEMA_VERSION", "config"),
        ("qqbot_studio/studio.py", "STUDIO_SCHEMA_VERSION", "studio"),
    )
    for relpath, attribute, kind in sources:
        result = _git(repo, "show", f"{rev}:{relpath}", runner=runner)
        if result.returncode != 0:
            continue
        match = re.search(rf"^{attribute}\s*=\s*(\d+)", result.stdout, re.MULTILINE)
        if match:
            declared[kind] = int(match.group(1))
    return declared


def _schema_conflicts(repo: Path, data_dir: Path, rev: str, runner: GitRunner | None) -> list[dict[str, int]]:
    stored = _data_schema_versions(data_dir)
    declared = _declared_schema_versions(repo, rev, runner)
    conflicts = []
    for kind, stored_version in stored.items():
        target_version = declared.get(kind)
        if target_version is not None and stored_version > target_version:
            conflicts.append(
                {"kind": kind, "stored": stored_version, "target": target_version}
            )
    return conflicts


# ----------------------------------------------------------------------
# Public: status / listing
# ----------------------------------------------------------------------
def check_update(
    root: Path | str | None = None,
    *,
    channel: str = "",
    runner: GitRunner | None = None,
) -> dict:
    """Fetch origin and report whether the selected channel has a newer version."""

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
    selected = str(channel or "").strip()
    if not selected:
        try:
            selected = _head_info(repo, runner)["channel"]
        except UpdateError:
            selected = "stable"
    if selected not in CHANNELS:
        return {"ok": False, "can_update": False, "message": "更新通道只能是 stable 或 test。"}

    if selected == "test":
        test_tag = _latest_test_tag(repo, runner)
        if not test_tag:
            return {
                "ok": True,
                "can_update": False,
                "channel": "test",
                "current": local.stdout.strip(),
                "message": "当前没有可用的测试版预发布标签（v*-alpha.N / v*-beta.N / v*-rc.N）。",
            }
        target = _resolve_commit(repo, f"refs/tags/{test_tag}", runner)
        if not target:
            return {
                "ok": False,
                "can_update": False,
                "channel": "test",
                "message": f"找不到测试版标签 {test_tag}；请检查远端标签。",
            }
        counts = _git(
            repo,
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...{target}",
            runner=runner,
        )
        if counts.returncode != 0:
            return {"ok": False, "can_update": False, "message": _output(counts) or "无法比较测试版。"}
        parts = counts.stdout.strip().split()
        ahead = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else 0
        behind = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
        current_commit = _resolve_commit(repo, "HEAD", runner)
        can_update = current_commit != target and behind > 0 and ahead == 0
        if current_commit == target:
            message = f"当前已经是测试版 {test_tag}。"
        elif ahead == 0:
            message = f"发现测试版 {test_tag}（{behind} 个新提交），可以自动更新。"
        elif behind == 0:
            message = f"当前已包含测试版 {test_tag} 的全部提交。"
        else:
            message = f"本地测试版与 {test_tag} 已分叉（领先 {ahead}、落后 {behind}），无法自动快进；可在测试版列表手动切换。"
        return {
            "ok": True,
            "can_update": can_update,
            "channel": "test",
            "branch": branch_name,
            "target": test_tag,
            "current": local.stdout.strip(),
            "remote": target[:7],
            "ahead": ahead,
            "behind": behind,
            "message": message,
        }

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
        "channel": "stable",
        "branch": branch_name,
        "upstream": upstream,
        "current": local.stdout.strip(),
        "remote": remote.stdout.strip(),
        "ahead": ahead,
        "behind": behind,
        "message": message,
    }


def update_status(
    root: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    runner: GitRunner | None = None,
) -> dict:
    """Report current version/ref, worktree cleanliness, lock and channels."""

    repo = _repo(root)
    data = _data_dir(data_dir)
    head = _head_info(repo, runner)
    status_text, error = _status_text(repo, runner)
    dirty = bool(status_text and status_text.strip())
    return {
        "ok": True,
        "current": head,
        "version": head["version"],
        "major": head["major"],
        "dirty": dirty,
        "dirty_message": error or ("代码目录有未提交的修改。" if dirty else ""),
        "busy": lock_active(data),
        "schema_versions": _data_schema_versions(data),
        "channels": {
            "stable": {
                "label": CHANNEL_LABELS["stable"],
                "track": "origin/main",
                "description": "跟随 GitHub 正式发布（main 快进更新）。",
            },
            "test": {
                "label": CHANNEL_LABELS["test"],
                "description": "自动发现并选择最新的 alpha/beta/rc 预发布标签。",
                "refs": ["refs/tags/v*-alpha.N / v*-beta.N / v*-rc.N"],
            },
        },
    }


def _tag_entries(repo: Path, runner: GitRunner | None) -> list[dict[str, Any]]:
    result = _git(
        repo,
        "for-each-ref",
        "--sort=-version:refname",
        "--format=%(refname:short)%09%(creatordate:short)%09%(subject)%09%(objectname:short)%09%(*objectname:short)",
        "refs/tags",
        runner=runner,
    )
    entries: list[dict[str, Any]] = []
    if result.returncode != 0:
        return entries
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        tag = parts[0].strip()
        version = _parse_formal_tag(tag)
        if not version:
            continue
        # Annotated tags expose the peeled commit in ``*objectname``; lightweight
        # tags leave it empty and the ref itself points at the commit.
        peeled = parts[4].strip() if len(parts) > 4 else ""
        commit = peeled or (parts[3].strip() if len(parts) > 3 else "")
        entries.append(
            {
                "tag": tag,
                "version": ".".join(str(part) for part in version),
                "major": version[0],
                "commit": commit,
                "date": parts[1].strip() if len(parts) > 1 else "",
                "subject": parts[2].strip() if len(parts) > 2 else "",
            }
        )
    entries.sort(key=lambda item: tuple(int(part) for part in item["version"].split(".")), reverse=True)
    return entries


def _test_tag_entries(repo: Path, runner: GitRunner | None) -> list[dict[str, Any]]:
    """Return every strict prerelease tag, newest semantic version first."""

    result = _git(
        repo,
        "for-each-ref",
        "--sort=-version:refname",
        "--format=%(refname:short)%09%(creatordate:short)%09%(subject)%09%(objectname:short)%09%(*objectname:short)",
        "refs/tags",
        runner=runner,
    )
    entries: list[dict[str, Any]] = []
    if result.returncode != 0:
        return entries
    for line in result.stdout.splitlines():
        parts = line.split("	")
        if len(parts) < 2:
            continue
        tag = parts[0].strip()
        version = _parse_test_tag(tag)
        if not version:
            continue
        peeled = parts[4].strip() if len(parts) > 4 else ""
        commit = peeled or (parts[3].strip() if len(parts) > 3 else "")
        entries.append(
            {
                "tag": tag,
                "ref": f"refs/tags/{tag}",
                "kind": "tag",
                "version": tag.lstrip("v"),
                "major": version[0],
                "commit": commit,
                "date": parts[1].strip() if len(parts) > 1 else "",
                "subject": parts[2].strip() if len(parts) > 2 else "",
            }
        )
    entries.sort(key=lambda item: _parse_test_tag(item["tag"]) or (0, 0, 0, "", 0), reverse=True)
    return entries


def _latest_test_tag(repo: Path, runner: GitRunner | None) -> str:
    entries = _test_tag_entries(repo, runner)
    return entries[0]["tag"] if entries else ""


def list_versions(
    channel: str = "stable",
    root: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    runner: GitRunner | None = None,
) -> dict:
    """List selectable versions for a channel.

    ``stable`` returns every formal tag with a ``same_major`` flag so the UI can
    offer any same-major rollback and grey out cross-major entries. ``test``
    returns every strict prerelease tag, newest first, discovered from origin.
    """

    if channel not in CHANNELS:
        raise UpdateError("更新通道只能是 stable 或 test。")
    repo = _repo(root)
    data = _data_dir(data_dir)
    head = _head_info(repo, runner)
    fetched, fetch_message = _fetch(repo, runner)

    versions: list[dict[str, Any]] = []
    if channel == "stable":
        for entry in _tag_entries(repo, runner):
            entry["same_major"] = entry["major"] == head["major"]
            entry["current"] = entry["commit"] == head["commit"]
            versions.append(entry)
    else:
        for entry in _test_tag_entries(repo, runner):
            commit_full = _resolve_commit(repo, f"refs/tags/{entry['tag']}", runner)
            entry["same_major"] = entry["major"] == head["major"]
            entry["current"] = bool(commit_full) and commit_full == head["commit_full"]
            versions.append(entry)

    message = fetch_message
    if not versions and not message:
        message = "没有可选的版本。"
    return {
        "ok": True,
        "channel": channel,
        "channels": CHANNEL_LABELS,
        "current": head,
        "current_major": head["major"],
        "versions": versions,
        "same_major_count": sum(1 for item in versions if item["same_major"]),
        "fetch_ok": fetched,
        "message": message,
    }


# ----------------------------------------------------------------------
# Switching / rollback
# ----------------------------------------------------------------------
def _restore_original(repo: Path, head: dict[str, Any], runner: GitRunner | None) -> tuple[bool, str]:
    candidates = []
    if head.get("branch"):
        candidates.append([head["branch"]])
    candidates.append(["--detach", head["commit_full"]])
    for args in candidates:
        result = _git(repo, "checkout", *args, timeout=120, runner=runner)
        if result.returncode == 0:
            return True, ""
    return False, sanitize_message(_output(result))


def _switch_code(
    repo: Path,
    data_dir: Path,
    rev: str,
    *,
    action: str,
    channel: str,
    head: dict[str, Any],
    runner: GitRunner | None,
    restart: bool,
    allow_schema_downgrade: bool,
) -> dict[str, Any]:
    target_commit = _resolve_commit(repo, rev, runner)
    if not target_commit:
        raise UpdateError(f"目标版本不存在：{rev}")

    conflicts = _schema_conflicts(repo, data_dir, rev, runner)
    if conflicts and not allow_schema_downgrade:
        detail = "、".join(
            f"{item['kind']} schema {item['stored']} -> {item['target']}" for item in conflicts
        )
        raise UpdateError(
            f"目标版本的数据/配置 schema 与本地不兼容（{detail}）。这不属于代码回滚范围，"
            "已拒绝静默降级；如确认已手动备份并接受，请显式勾选“允许 schema 降级”后重试。"
        )

    backup = _backup(repo, data_dir, f"before-{action}-{rev}", runner)
    started_at = _now_iso()
    switch = _git(repo, "checkout", "--detach", rev, timeout=120, runner=runner)
    if switch.returncode == 0:
        switch = _git(repo, "rev-parse", "HEAD", runner=runner)
    if switch.returncode != 0 or switch.stdout.strip() != target_commit:
        restored, restore_error = _restore_original(repo, head, runner)
        message = sanitize_message(
            _output(switch) or f"无法切换到 {rev}。"
        ) + ("；已恢复原版本。" if restored else f"；恢复原版本失败：{restore_error}")
        _append_history(
            data_dir,
            {
                "at": started_at,
                "action": action,
                "channel": channel,
                "from": _from_snapshot(head),
                "to": {"version": rev.lstrip("v"), "tag": rev, "commit": target_commit[:7]},
                "result": "failed" if not restored else "restored",
                "backup": backup["path"],
                "message": message,
            },
        )
        raise UpdateError(f"切换到 {rev} 失败：{message}")

    restart_state = "skipped"
    restart_message = ""
    if restart:
        try:
            _launch_restart(repo, data_dir)
            restart_state = "started"
        except UpdateError as exc:
            restart_state = "failed"
            restart_message = sanitize_message(str(exc))

    record = {
        "at": started_at,
        "action": action,
        "channel": channel,
        "from": _from_snapshot(head),
        "to": {"version": rev.lstrip("v"), "tag": rev, "commit": target_commit[:7]},
        "result": "success",
        "backup": backup["path"],
        "message": restart_message,
    }
    _append_history(data_dir, record)
    if restart_state == "failed":
        raise UpdateError(
            f"代码已切换到 {rev}，但自动重启失败：{restart_message}。请手动重启管理服务。"
        )
    return {
        "ok": True,
        "action": action,
        "channel": channel,
        "from": record["from"],
        "to": record["to"],
        "backup": backup["path"],
        "backup_files": backup["files"],
        "restarting": restart_state == "started",
        "restart": restart_state,
        "message": f"已切换到 {rev}，管理服务将在几秒后重启。请稍后刷新页面。",
    }


def _from_snapshot(head: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": head.get("version"),
        "commit": head.get("commit"),
        "ref": head.get("branch") or ("detached:" + str(head.get("commit"))),
        "tag": head.get("tag") or "",
    }


def _launch_restart(repo: Path, data_dir: Path) -> None:
    if os.name == "nt":
        raise UpdateError("Windows 版请按 windows 目录说明手动重启管理服务。")
    script = repo / "scripts" / "auto-update.sh"
    if not script.is_file():
        raise UpdateError("找不到 scripts/auto-update.sh。")
    env = os.environ.copy()
    env["QQBOT_AUTO_UPDATE_ROOT"] = str(repo)
    try:
        subprocess.Popen(
            ["bash", str(script), str(data_dir), "restart"],
            cwd=str(repo),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise UpdateError(f"无法启动重启脚本：{sanitize_message(str(exc))}") from exc


def rollback(
    target: str,
    root: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    restart: bool = True,
    allow_schema_downgrade: bool = False,
    runner: GitRunner | None = None,
) -> dict:
    """Roll back to any formal tag of the current major version."""

    name = str(target or "").strip()
    version = _parse_formal_tag(name)
    if not version:
        raise UpdateError(
            "只能回滚到正式发布标签（例如 v4.1.0）；不接受分支名、提交号或链接。"
        )
    repo = _repo(root)
    data = _data_dir(data_dir)
    lock = _acquire_lock(data)
    try:
        _fetch(repo, runner)
        head = _head_info(repo, runner)
        if version[0] != head["major"]:
            raise UpdateError(
                f"跨大版本回滚已拒绝：当前是 v{head['major']}.x，目标 {name} 属于 v{version[0]}.x。"
            )
        if not _resolve_commit(repo, f"refs/tags/{name}", runner):
            raise UpdateError(f"本地找不到正式标签 {name}；请先检查网络或标签是否已发布。")
        _require_clean(repo, runner)
        return _switch_code(
            repo,
            data,
            name,
            action="rollback",
            channel="rollback",
            head=head,
            runner=runner,
            restart=restart,
            allow_schema_downgrade=allow_schema_downgrade,
        )
    finally:
        _release_lock(lock)


def _resolve_test_ref(repo: Path, ref: str, runner: GitRunner | None) -> str:
    ref = str(ref or "").strip()
    if not _parse_test_tag(ref):
        raise UpdateError("测试通道只允许 vX.Y.Z-alpha.N / beta.N / rc.N 形式的预发布标签。")
    candidates = [f"refs/tags/{ref}", ref]
    for candidate in candidates:
        if _resolve_commit(repo, candidate, runner):
            return candidate
    raise UpdateError(f"测试通道引用的 {ref} 不存在；请先同步远端或检查登记名。")


def apply_update(
    channel: str = "stable",
    root: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    test_ref: str = "",
    restart: bool = True,
    allow_schema_downgrade: bool = False,
    runner: GitRunner | None = None,
) -> dict:
    """Apply the stable channel (main fast-forward) or an allowlisted test ref."""

    if channel not in CHANNELS:
        raise UpdateError("更新通道只能是 stable 或 test。")
    repo = _repo(root)
    data = _data_dir(data_dir)
    lock = _acquire_lock(data)
    try:
        _require_clean(repo, runner)
        head = _head_info(repo, runner)
        fetched, fetch_message = _fetch(repo, runner)
        if channel == "test":
            ref = str(test_ref or "").strip() or _latest_test_tag(repo, runner)
            if not ref:
                raise UpdateError("当前没有可用的测试版预发布标签。")
            resolved = _resolve_test_ref(repo, ref, runner)
            return _switch_code(
                repo,
                data,
                resolved,
                action="update",
                channel="test",
                head=head,
                runner=runner,
                restart=restart,
                allow_schema_downgrade=allow_schema_downgrade,
            )

        target = _resolve_commit(repo, "origin/main", runner)
        if not target:
            raise UpdateError(
                "无法解析 origin/main" + (f"：{fetch_message}" if fetch_message else "；请检查 origin 与网络。")
            )
        current = _resolve_commit(repo, "HEAD", runner)
        if current == target:
            return {
                "ok": True,
                "can_update": False,
                "restarting": False,
                "message": "当前已经是最新稳定版。",
                "current": head["commit"],
                "remote": target[:7],
            }
        ancestor = _git(repo, "merge-base", "--is-ancestor", "HEAD", "origin/main", runner=runner)
        if ancestor.returncode != 0:
            raise UpdateError(
                "本地提交不在 origin/main 的历史上，无法快进更新。请先处理分支/提交后再试。"
            )
        backup = _backup(repo, data, "before-update-stable", runner)
        started_at = _now_iso()
        merge = _git(repo, "merge", "--ff-only", "origin/main", timeout=120, runner=runner)
        if merge.returncode != 0:
            message = sanitize_message(_output(merge) or "快进合并失败。")
            _append_history(
                data,
                {
                    "at": started_at,
                    "action": "update",
                    "channel": "stable",
                    "from": _from_snapshot(head),
                    "to": {"version": "", "commit": target[:7]},
                    "result": "failed",
                    "backup": backup["path"],
                    "message": message,
                },
            )
            raise UpdateError(f"稳定版更新失败：{message}")
        restart_state = "skipped"
        restart_message = ""
        if restart:
            try:
                _launch_restart(repo, data)
                restart_state = "started"
            except UpdateError as exc:
                restart_state = "failed"
                restart_message = sanitize_message(str(exc))
        _append_history(
            data,
            {
                "at": started_at,
                "action": "update",
                "channel": "stable",
                "from": _from_snapshot(head),
                "to": {"version": "", "commit": target[:7]},
                "result": "success",
                "backup": backup["path"],
                "message": restart_message,
            },
        )
        if restart_state == "failed":
            raise UpdateError(
                f"稳定版代码已更新，但自动重启失败：{restart_message}。请手动重启管理服务。"
            )
        return {
            "ok": True,
            "can_update": True,
            "restarting": restart_state == "started",
            "restart": restart_state,
            "current": target[:7],
            "backup": backup["path"],
            "backup_files": backup["files"],
            "message": "稳定版更新已应用，管理服务将在几秒后重启。请稍后刷新页面。",
        }
    finally:
        _release_lock(lock)


def start_update(
    channel: str = "stable",
    root: Path | str | None = None,
    data_dir: Path | str | None = None,
    *,
    test_ref: str = "",
    runner: GitRunner | None = None,
) -> dict:
    """Backwards-compatible entry point used by the management console."""

    return apply_update(
        channel=channel,
        root=root,
        data_dir=data_dir,
        test_ref=test_ref,
        restart=True,
        runner=runner,
    )
