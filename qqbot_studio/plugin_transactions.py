"""Crash-safe migration state machine for one Studio instance.

The module implements the owner migration transaction described by the
v2/v3 migration contract.  It is intentionally independent from Docker and
plugin runtime code: callers inject step handlers/probes, which makes the
journal, backup, CAS, resume and rollback behaviour testable without a real
container.

Forward state machine::

    inventoried -> planned -> verified -> quiesced -> drained -> backed_up
                -> staged -> switching -> starting -> checking
                -> committing -> active

Failure/rollback states: ``failed``, ``rolling_back``, ``rolled_back`` and
``manual_recovery_required``.

Every step writes an intent journal record *before* running its side effect and
a result record afterwards.  A crash leaves ``pending_state`` on disk; a later
``resume_operation()`` asks the handler's ``recover`` hook whether the effect
already happened.  Unknown outcomes become ``manual_recovery_required`` rather
than being retried blindly or reported as success.

No business data is deleted by this module.  Backups contain code/config/
metadata only; rollback overwrites only files recorded in the backup manifest
and first copies any newer current file into a ``preserved`` directory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping

try:  # POSIX file lock; Windows has a small fallback below.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None  # type: ignore
try:  # pragma: no cover - exercised on Windows
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None  # type: ignore

from .plugin_state import (
    OWNER_KEYS,
    PluginOwnerState,
    PluginRevisionConflict,
    PluginStateCorruptError,
    PluginStateError,
    PluginStatePathError,
    PluginStateStore,
    validate_owner_state,
)

INITIAL_STATE = "inventoried"
FORWARD_STATES: tuple[str, ...] = (
    "inventoried",
    "planned",
    "verified",
    "quiesced",
    "drained",
    "backed_up",
    "staged",
    "switching",
    "starting",
    "checking",
    "committing",
    "active",
)
TRANSITION_STEPS: tuple[str, ...] = FORWARD_STATES[1:]
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_MANUAL = "manual_recovery_required"
STATUS_ROLLING_BACK = "rolling_back"
STATUS_ROLLED_BACK = "rolled_back"

MIGRATION_STATES = (*FORWARD_STATES, "failed", "rolling_back", "rolled_back", "manual_recovery_required")
BACKUP_KINDS = frozenset({"code", "config", "metadata"})
PREVIEW_TTL_SECONDS = 15 * 60
MAX_PLAN_BYTES = 512 * 1024
MAX_OPERATION_BYTES = 512 * 1024
MAX_IDEMPOTENCY_ENTRIES = 1000
MAX_BACKUP_FILES = 5000
MAX_BACKUP_BYTES = 256 * 1024 * 1024
MAX_JOURNAL_ENTRIES = 5000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|"
    r"secret|credential|token|private[_-]?key|cookie)"
)
_FORBIDDEN_DATA_ROOTS = frozenset(
    {
        "data",
        "data/astrbot",
        "data/astrbot/data",
        "data/astrbot/plugin_data",
    }
)


class MigrationError(RuntimeError):
    http_status = 400
    code = "migration_error"

    def __init__(self, message: str, *, detail: str = "", reason: str = "") -> None:
        super().__init__(str(message))
        self.message = str(message)
        self.detail = str(detail or "")
        self.reason = str(reason or "")

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        if self.reason:
            payload["reason"] = self.reason
        return payload


class MigrationNotFound(MigrationError):
    http_status = 404
    code = "migration_not_found"


class MigrationPlanError(MigrationError):
    code = "invalid_migration_plan"


class MigrationConflict(MigrationError):
    http_status = 409
    code = "migration_conflict"


class MigrationStateError(MigrationError):
    http_status = 503
    code = "migration_state_corrupt"


class MigrationIdempotencyConflict(MigrationError):
    http_status = 409
    code = "idempotency_conflict"


class MigrationStepError(MigrationError):
    http_status = 500
    code = "migration_step_failed"


class MigrationBridgeUnavailable(MigrationStepError):
    http_status = 503
    code = "bridge_unavailable"


class MigrationManualRecovery(MigrationError):
    http_status = 409
    code = "manual_recovery_required"


class MigrationCrash(RuntimeError):
    """Raised by tests/handlers to simulate process death between journal and result."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _epoch() -> float:
    return time.time()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_copy(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    raise MigrationPlanError("迁移数据必须是 JSON 兼容类型。")


_BENIGN_CONTROL_FIELDS = frozenset({"preview_token", "confirm_token"})


def _reject_sensitive_keys(value: Any, *, where: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text not in _BENIGN_CONTROL_FIELDS and _SECRET_KEY_RE.search(key_text):
                raise MigrationPlanError(f"{where} 不允许包含秘密字段，已拒绝。")
            _reject_sensitive_keys(item, where=where)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_keys(item, where=where)


def _bounded_json(value: Any, *, max_bytes: int = MAX_PLAN_BYTES, where: str = "payload") -> Any:
    counter = [0]

    def walk(item: Any, depth: int) -> Any:
        if depth > 12:
            raise MigrationPlanError(f"{where} 嵌套过深。")
        counter[0] += 1
        if counter[0] > 5000:
            raise MigrationPlanError(f"{where} 条目过多。")
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise MigrationPlanError(f"{where} 包含非法数值。")
            return item
        if isinstance(item, str):
            if len(item) > 65536 or _CONTROL_RE.search(item):
                raise MigrationPlanError(f"{where} 字符串过长或包含控制字符。")
            return item
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 256 or _CONTROL_RE.search(key):
                    raise MigrationPlanError(f"{where} 的键不合法。")
                result[key] = walk(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            if len(item) > 1000:
                raise MigrationPlanError(f"{where} 数组过大。")
            return [walk(child, depth + 1) for child in item]
        raise MigrationPlanError(f"{where} 必须是 JSON 兼容类型。")

    copied = walk(value, 0)
    if len(_canonical_json(copied).encode("utf-8")) > max_bytes:
        raise MigrationPlanError(f"{where} 超过大小限制。")
    return copied


def _safe_token(value: Any, name: str, *, max_len: int = 128) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_len or _CONTROL_RE.search(text):
        raise MigrationPlanError(f"{name} 不合法。")
    if "/" in text or "\\" in text or ".." in text or "://" in text:
        raise MigrationPlanError(f"{name} 不能是路径或 URL。")
    if not _SAFE_TOKEN_RE.fullmatch(text):
        raise MigrationPlanError(f"{name} 不合法。")
    return text


def _safe_version(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 100 or _CONTROL_RE.search(text) or "/" in text or "\\" in text:
        raise MigrationPlanError("version 不合法。")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}", text):
        raise MigrationPlanError("version 不合法。")
    return text


def _normalise_entry_list(value: Any, *, field_name: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise MigrationPlanError(f"{field_name} 必须是数组。")
    if len(value) > MAX_BACKUP_FILES:
        raise MigrationPlanError(f"{field_name} 条目过多。")
    entries: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            path = item
            kind = "metadata"
        elif isinstance(item, Mapping):
            path = str(item.get("path") or "").strip()
            kind = str(item.get("kind") or "metadata").strip().lower()
        else:
            raise MigrationPlanError(f"{field_name} 条目必须是路径或对象。")
        if kind not in BACKUP_KINDS:
            raise MigrationPlanError(f"{field_name} 只允许 code/config/metadata，不能备份业务数据目录。")
        path = _safe_relative_path(path)
        if path in _FORBIDDEN_DATA_ROOTS:
            raise MigrationPlanError(f"{field_name} 不能指向业务数据根目录。")
        entry = {"path": path, "kind": kind}
        if entry not in entries:
            entries.append(entry)
    return entries


def _safe_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > 512 or _CONTROL_RE.search(text):
        raise MigrationPlanError("路径不合法。")
    if text.startswith("/") or "://" in text or re.match(r"^[A-Za-z]:", text):
        raise MigrationPlanError("路径必须是实例内相对路径，已拒绝。")
    parts = PurePosixPath(text).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise MigrationPlanError("路径不能包含路径穿越段，已拒绝。")
    normalized = "/".join(parts)
    if normalized in _FORBIDDEN_DATA_ROOTS:
        raise MigrationPlanError("不允许把业务数据目录作为代码/配置备份项。")
    return normalized


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _sha256_file(path: Path, *, limit: int = MAX_BACKUP_BYTES) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise MigrationStepError("备份文件超过大小限制。")
            digest.update(chunk)
    return digest.hexdigest(), total


def _atomic_write_private_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    payload = json.dumps(_json_copy(dict(value)), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _read_private_json(path: Path, *, max_bytes: int, what: str) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink():
        raise MigrationStateError(f"{what} 不能是符号链接。")
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise MigrationNotFound(f"{what}不存在。") from exc
    except OSError as exc:
        raise MigrationStateError(f"无法读取{what}。", detail=exc.__class__.__name__) from exc
    if size <= 0 or size > max_bytes:
        raise MigrationStateError(f"{what}大小异常，已停止读取。")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise MigrationStateError(f"{what}损坏，已停止读取。", detail=exc.__class__.__name__) from exc
    if not isinstance(raw, dict):
        raise MigrationStateError(f"{what}顶层必须是 JSON object。")
    return raw


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Any = None

    def __enter__(self) -> "_FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._handle = open(self.path, "a+b")
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_LOCK, 1)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise MigrationStateError("无法获取迁移跨进程锁。") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


@dataclass
class StepResult:
    outcome: str = "succeeded"
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in {"succeeded", "already_applied", "skipped", "unknown"}:
            raise MigrationStepError("步骤结果状态不合法。")
        if not isinstance(self.evidence, dict):
            raise MigrationStepError("步骤 evidence 必须是 JSON object。")
        if self.outcome == "unknown" and not str(self.reason or "").strip():
            self.reason = "步骤结果无法确认，已要求人工恢复。"

    @classmethod
    def unknown(cls, reason: str) -> "StepResult":
        return cls(outcome="unknown", reason=str(reason or "步骤结果无法确认。"))

    @classmethod
    def applied(cls, evidence: Mapping[str, Any] | None = None) -> "StepResult":
        return cls(outcome="already_applied", evidence=dict(evidence or {}))


@dataclass
class StepContext:
    operation_id: str
    plan_id: str
    instance_id: str
    state: str
    attempt: int
    resume: bool
    instance_dir: Path
    operation_dir: Path
    backup_dir: Path
    staging_dir: Path
    plan: dict[str, Any]
    operation: dict[str, Any]


@dataclass
class MigrationStepHandlers:
    """Optional handler overrides for the state machine.

    A handler may be a callable ``(StepContext) -> StepResult|Mapping|None`` or
    an object with an ``execute(context)`` method.  It may additionally expose
    ``recover(context)`` returning ``"applied"``, ``"not_applied"`` or
    ``"unknown"``.  Callables are assumed idempotent when no recover hook is
    present.
    """

    handlers: Mapping[str, Any] = field(default_factory=dict)

    def execute(self, state: str) -> Any | None:
        return self.handlers.get(state)


@dataclass
class MigrationPlan:
    plan_id: str
    instance_id: str
    stage: str
    source_version: str
    target_version: str
    expected_revision: int
    owners_before: dict[str, str]
    owners_after: dict[str, str]
    owner_epochs_before: dict[str, int]
    owner_epochs_after: dict[str, int]
    phase_before: str
    phase_after: str
    legacy_load_allowed_before: bool
    legacy_load_allowed_after: bool
    legacy_runtime_dependencies_before: list[str]
    legacy_runtime_dependencies_after: list[str]
    backup_paths: list[dict[str, str]]
    stage_paths: list[dict[str, str]]
    entrypoint_closure: list[str]
    request_hash: str
    preview_token_hash: str
    created_at: str
    expires_at: float
    status: str = "planned"
    consumed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "migration_plan",
            "plan_id": self.plan_id,
            "instance_id": self.instance_id,
            "stage": self.stage,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "expected_revision": self.expected_revision,
            "owners_before": dict(self.owners_before),
            "owners_after": dict(self.owners_after),
            "owner_epochs_before": dict(self.owner_epochs_before),
            "owner_epochs_after": dict(self.owner_epochs_after),
            "phase_before": self.phase_before,
            "phase_after": self.phase_after,
            "legacy_load_allowed_before": self.legacy_load_allowed_before,
            "legacy_load_allowed_after": self.legacy_load_allowed_after,
            "legacy_runtime_dependencies_before": list(self.legacy_runtime_dependencies_before),
            "legacy_runtime_dependencies_after": list(self.legacy_runtime_dependencies_after),
            "backup_paths": [dict(item) for item in self.backup_paths],
            "stage_paths": [dict(item) for item in self.stage_paths],
            "entrypoint_closure": list(self.entrypoint_closure),
            "request_hash": self.request_hash,
            "preview_token_hash": self.preview_token_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "consumed_at": self.consumed_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MigrationPlan":
        if raw.get("schema_version") != 1 or raw.get("kind") != "migration_plan":
            raise MigrationStateError("迁移计划格式不受支持，已停止读取。")
        try:
            owners_before = {key: str((raw.get("owners_before") or {}).get(key) or "") for key in OWNER_KEYS}
            owners_after = {key: str((raw.get("owners_after") or {}).get(key) or "") for key in OWNER_KEYS}
            epochs_before = {key: int((raw.get("owner_epochs_before") or {}).get(key) or 0) for key in OWNER_KEYS}
            epochs_after = {key: int((raw.get("owner_epochs_after") or {}).get(key) or 0) for key in OWNER_KEYS}
            return cls(
                plan_id=str(raw.get("plan_id") or ""),
                instance_id=str(raw.get("instance_id") or ""),
                stage=str(raw.get("stage") or ""),
                source_version=str(raw.get("source_version") or ""),
                target_version=str(raw.get("target_version") or ""),
                expected_revision=int(raw.get("expected_revision") or 0),
                owners_before=owners_before,
                owners_after=owners_after,
                owner_epochs_before=epochs_before,
                owner_epochs_after=epochs_after,
                phase_before=str(raw.get("phase_before") or ""),
                phase_after=str(raw.get("phase_after") or ""),
                legacy_load_allowed_before=bool(raw.get("legacy_load_allowed_before")),
                legacy_load_allowed_after=bool(raw.get("legacy_load_allowed_after")),
                legacy_runtime_dependencies_before=[
                    str(item) for item in raw.get("legacy_runtime_dependencies_before") or []
                ],
                legacy_runtime_dependencies_after=[
                    str(item) for item in raw.get("legacy_runtime_dependencies_after") or []
                ],
                backup_paths=[
                    {"path": str(item.get("path") or ""), "kind": str(item.get("kind") or "")}
                    for item in raw.get("backup_paths") or []
                    if isinstance(item, Mapping)
                ],
                stage_paths=[
                    {"path": str(item.get("path") or ""), "kind": str(item.get("kind") or "")}
                    for item in raw.get("stage_paths") or []
                    if isinstance(item, Mapping)
                ],
                entrypoint_closure=[str(item) for item in raw.get("entrypoint_closure") or []],
                request_hash=str(raw.get("request_hash") or ""),
                preview_token_hash=str(raw.get("preview_token_hash") or ""),
                created_at=str(raw.get("created_at") or ""),
                expires_at=float(raw.get("expires_at") or 0),
                status=str(raw.get("status") or "planned"),
                consumed_at=str(raw.get("consumed_at") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise MigrationStateError("迁移计划字段损坏，已停止读取。") from exc

    def validate(self) -> "MigrationPlan":
        if not _ID_RE.fullmatch(self.plan_id):
            raise MigrationStateError("迁移计划 ID 不合法。")
        if self.status not in {"planned", "consumed", "expired"}:
            raise MigrationStateError("迁移计划状态不合法。")
        _safe_token(self.stage, "stage")
        _safe_version(self.source_version)
        _safe_version(self.target_version)
        if self.expected_revision < 0:
            raise MigrationStateError("迁移计划 revision 不合法。")
        for owners in (self.owners_before, self.owners_after):
            if set(owners) != set(OWNER_KEYS):
                raise MigrationStateError("迁移计划 owner 域不完整。")
        preview = validate_owner_state(
            {
                "schema_version": 2,
                "revision": len(self.owners_after),
                "phase": self.phase_after,
                "owners": self.owners_after,
                "owner_epochs": {
                    key: max(1, int(self.owner_epochs_after.get(key) or 0))
                    for key in OWNER_KEYS
                },
                "legacy_load_allowed": self.legacy_load_allowed_after,
                "legacy_runtime_dependencies": self.legacy_runtime_dependencies_after,
                "created_at": self.created_at,
                "updated_at": self.created_at,
            }
        )
        del preview
        _normalise_entry_list(self.backup_paths, field_name="backup_paths")
        _normalise_entry_list(self.stage_paths, field_name="stage_paths")
        if len(self.entrypoint_closure) > 5000:
            raise MigrationStateError("entrypoint closure 条目过多。")
        for item in self.entrypoint_closure:
            _safe_token(str(item), "entrypoint_closure", max_len=256)
        return self


@dataclass
class MigrationOperation:
    operation_id: str
    instance_id: str
    kind: str
    plan_id: str
    plan_hash: str
    status: str
    current_state: str
    pending_state: str
    attempt: int
    created_at: str
    updated_at: str
    actor: str
    idempotency_key: str
    request_hash: str
    history: list[dict[str, Any]] = field(default_factory=list)
    last_error: dict[str, Any] = field(default_factory=dict)
    manual_recovery_reason: str = ""
    backup_manifest_path: str = ""
    rollback_plan_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "migration_operation",
            "operation_id": self.operation_id,
            "instance_id": self.instance_id,
            "operation_kind": self.kind,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "status": self.status,
            "current_state": self.current_state,
            "pending_state": self.pending_state,
            "attempt": self.attempt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "actor": self.actor,
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "history": [dict(item) for item in self.history],
            "last_error": dict(self.last_error),
            "manual_recovery_reason": self.manual_recovery_reason,
            "backup_manifest_path": self.backup_manifest_path,
            "rollback_plan_id": self.rollback_plan_id,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MigrationOperation":
        if raw.get("schema_version") != 1 or raw.get("kind") != "migration_operation":
            raise MigrationStateError("迁移操作格式不受支持，已停止读取。")
        try:
            operation = cls(
                operation_id=str(raw.get("operation_id") or ""),
                instance_id=str(raw.get("instance_id") or ""),
                kind=str(raw.get("operation_kind") or ""),
                plan_id=str(raw.get("plan_id") or ""),
                plan_hash=str(raw.get("plan_hash") or ""),
                status=str(raw.get("status") or ""),
                current_state=str(raw.get("current_state") or ""),
                pending_state=str(raw.get("pending_state") or ""),
                attempt=int(raw.get("attempt") or 0),
                created_at=str(raw.get("created_at") or ""),
                updated_at=str(raw.get("updated_at") or ""),
                actor=str(raw.get("actor") or ""),
                idempotency_key=str(raw.get("idempotency_key") or ""),
                request_hash=str(raw.get("request_hash") or ""),
                history=[dict(item) for item in raw.get("history") or [] if isinstance(item, Mapping)],
                last_error=dict(raw.get("last_error") or {}),
                manual_recovery_reason=str(raw.get("manual_recovery_reason") or ""),
                backup_manifest_path=str(raw.get("backup_manifest_path") or ""),
                rollback_plan_id=str(raw.get("rollback_plan_id") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise MigrationStateError("迁移操作字段损坏，已停止读取。") from exc
        operation.validate()
        return operation

    def validate(self) -> "MigrationOperation":
        if not _ID_RE.fullmatch(self.operation_id):
            raise MigrationStateError("迁移操作 ID 不合法。")
        if self.kind not in {"migration", "rollback"}:
            raise MigrationStateError("迁移操作类型不合法。")
        if self.status not in {
            STATUS_RUNNING,
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_MANUAL,
            STATUS_ROLLING_BACK,
            STATUS_ROLLED_BACK,
        }:
            raise MigrationStateError("迁移操作状态不合法。")
        if self.kind == "migration":
            if self.current_state not in MIGRATION_STATES:
                raise MigrationStateError("迁移操作状态机字段不合法。")
        else:
            if self.current_state not in {INITIAL_STATE, "rolling_back", "rolled_back", "failed", STATUS_MANUAL}:
                raise MigrationStateError("回滚操作状态机字段不合法。")
        if self.pending_state and not _ID_RE.fullmatch(self.pending_state):
            # State names contain underscores, so use the explicit set instead.
            if self.pending_state not in TRANSITION_STEPS and self.pending_state not in {"rolling_back", "rolled_back"}:
                raise MigrationStateError("迁移操作 pending_state 不合法。")
        if len(self.history) > MAX_JOURNAL_ENTRIES:
            raise MigrationStateError("迁移操作历史过多。")
        return self


class PluginMigrationService:
    """Crash-safe migration orchestration for one instance."""

    def __init__(
        self,
        instance_dir: Path | str,
        *,
        instance_id: str = "",
        state_store: PluginStateStore | None = None,
        lock: threading.RLock | None = None,
        clock: Callable[[], str] | None = None,
        epoch: Callable[[], float] | None = None,
        handlers: Mapping[str, Any] | MigrationStepHandlers | None = None,
        quiescence_check: Callable[[StepContext], bool] | None = None,
        drain_check: Callable[[StepContext], bool] | None = None,
        start_probe: Callable[[StepContext], bool] | None = None,
        check_probe: Callable[[StepContext], bool] | None = None,
        commit_probe: Callable[[StepContext], bool] | None = None,
        new_data_probe: Callable[[], Mapping[str, Any]] | None = None,
        redact: Callable[[str], str] | None = None,
        max_backup_files: int = MAX_BACKUP_FILES,
        max_backup_bytes: int = MAX_BACKUP_BYTES,
    ) -> None:
        instance = Path(instance_dir).expanduser().resolve()
        if not instance.is_dir():
            raise MigrationPlanError("实例目录不存在，无法创建迁移服务。")
        self.instance_dir = instance
        self.instance_id = str(instance_id or instance.name).strip()
        if not _SAFE_TOKEN_RE.fullmatch(self.instance_id) or ".." in self.instance_id:
            raise MigrationPlanError("实例 ID 不合法。")
        self.root = instance / "studio-plugins"
        self.migrations_dir = self.root / "migrations"
        self.plans_dir = self.migrations_dir / "plans"
        self.operations_dir = self.migrations_dir / "operations"
        self.idempotency_path = self.migrations_dir / "idempotency.json"
        self.lock_path = self.migrations_dir / "migration.lock"
        self.state_store = state_store or PluginStateStore(instance)
        self.clock = clock or _now
        self.epoch = epoch or _epoch
        self.redact = redact or (lambda value: str(value))
        self._thread_lock = lock or threading.RLock()
        self._handlers = handlers
        self.quiescence_check = quiescence_check
        self.drain_check = drain_check
        self.start_probe = start_probe
        self.check_probe = check_probe
        self.commit_probe = commit_probe
        self.new_data_probe = new_data_probe
        self.max_backup_files = max(1, int(max_backup_files))
        self.max_backup_bytes = max(1024, int(max_backup_bytes))

    # ------------------------------------------------------------------
    # storage/path helpers
    # ------------------------------------------------------------------
    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            self.migrations_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.root, 0o700)
                os.chmod(self.migrations_dir, 0o700)
            except OSError:
                pass
            with _FileLock(self.lock_path):
                yield

    def _plan_path(self, plan_id: str) -> Path:
        plan_id = str(plan_id or "").strip()
        if not _ID_RE.fullmatch(plan_id):
            raise MigrationNotFound("迁移计划不存在。")
        path = self.plans_dir / f"{plan_id}.json"
        if not _is_within(path, self.migrations_dir):
            raise MigrationStateError("迁移计划路径越界，已拒绝。")
        return path

    def _operation_dir(self, operation_id: str) -> Path:
        operation_id = str(operation_id or "").strip()
        if not _ID_RE.fullmatch(operation_id):
            raise MigrationNotFound("迁移操作不存在。")
        path = self.operations_dir / operation_id
        if not _is_within(path, self.migrations_dir):
            raise MigrationStateError("迁移操作路径越界，已拒绝。")
        return path

    def _operation_path(self, operation_id: str) -> Path:
        return self._operation_dir(operation_id) / "operation.json"

    def _journal_path(self, operation_id: str) -> Path:
        return self._operation_dir(operation_id) / "journal.jsonl"

    def _public_plan(self, plan: MigrationPlan) -> dict[str, Any]:
        after_state = validate_owner_state(
            {
                "schema_version": 2,
                "revision": 1,
                "phase": plan.phase_after,
                "owners": plan.owners_after,
                "owner_epochs": {
                    key: max(1, int(plan.owner_epochs_after.get(key) or 0))
                    for key in OWNER_KEYS
                },
                "legacy_load_allowed": plan.legacy_load_allowed_after,
                "legacy_runtime_dependencies": plan.legacy_runtime_dependencies_after,
                "created_at": plan.created_at,
                "updated_at": plan.created_at,
            }
        )
        del after_state
        owner_changes = []
        for domain in OWNER_KEYS:
            before = plan.owners_before.get(domain, "")
            after = plan.owners_after.get(domain, "")
            if before != after:
                owner_changes.append(
                    {
                        "domain": domain,
                        "before": before,
                        "after": after,
                        "epoch_before": int(plan.owner_epochs_before.get(domain) or 0),
                        "epoch_after": int(plan.owner_epochs_after.get(domain) or 0),
                    }
                )
        return {
            "plan_id": plan.plan_id,
            "instance_id": plan.instance_id,
            "stage": plan.stage,
            "source_version": plan.source_version,
            "target_version": plan.target_version,
            "expected_revision": plan.expected_revision,
            "owner_state_revision": plan.expected_revision,
            "owners_before": dict(plan.owners_before),
            "owners_after": dict(plan.owners_after),
            "owner_changes": owner_changes,
            "phase_before": plan.phase_before,
            "phase_after": plan.phase_after,
            "legacy_load_allowed_before": plan.legacy_load_allowed_before,
            "legacy_load_allowed_after": plan.legacy_load_allowed_after,
            "legacy_runtime_dependencies_before": list(plan.legacy_runtime_dependencies_before),
            "legacy_runtime_dependencies_after": list(plan.legacy_runtime_dependencies_after),
            "configuration_preview": {
                "owner_fields_changed": len(owner_changes),
                "secrets_included": False,
            },
            "data_preview": {
                "policy": "preserve_never_delete",
                "backup_entries": len(plan.backup_paths),
                "stage_entries": len(plan.stage_paths),
                "business_data_touched": False,
            },
            "entrypoint_closure": list(plan.entrypoint_closure),
            "entrypoint_closure_count": len(plan.entrypoint_closure),
            "dependencies": list(plan.legacy_runtime_dependencies_after),
            "downtime": {
                "requires_quiesce": True,
                "requires_drain": True,
                "description": "需要暂停消息/管理写并排空旧 writer 后才能切换 owner。",
            },
            "requires_confirmation": True,
            "status": plan.status,
            "created_at": plan.created_at,
            "expires_at": plan.expires_at,
            "warnings": [],
        }

    def _public_operation(self, op: MigrationOperation, *, journal: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload = {
            "id": op.operation_id,
            "operation_id": op.operation_id,
            "instance_id": op.instance_id,
            "kind": op.kind,
            "operation": op.kind,
            "plan_id": op.plan_id,
            "plan_hash": op.plan_hash,
            "status": op.status,
            "current_state": op.current_state,
            "pending_state": op.pending_state,
            "attempt": op.attempt,
            "created_at": op.created_at,
            "updated_at": op.updated_at,
            "actor": op.actor,
            "history": [dict(item) for item in op.history],
            "last_error": dict(op.last_error),
            "manual_recovery_reason": op.manual_recovery_reason,
            "backup_manifest_path": op.backup_manifest_path,
            "rollback_plan_id": op.rollback_plan_id,
        }
        if journal is not None:
            payload["journal"] = list(journal[-100:])
        return payload

    def _append_journal(self, operation_id: str, record: Mapping[str, Any]) -> None:
        path = self._journal_path(operation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_record = _bounded_json(dict(record), max_bytes=64 * 1024, where="journal")
        line = json.dumps(safe_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            try:
                if path.exists():
                    os.chmod(path, 0o600)
            except OSError:
                pass

    def _read_journal(self, operation_id: str, *, limit: int = MAX_JOURNAL_ENTRIES) -> list[dict[str, Any]]:
        path = self._journal_path(operation_id)
        if not path.exists():
            return []
        if path.is_symlink():
            raise MigrationStateError("迁移 journal 不能是符号链接。")
        entries: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if len(entries) >= limit:
                        raise MigrationStateError("迁移 journal 条目过多，已停止读取。")
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise MigrationStateError("迁移 journal 记录格式损坏。")
                    entries.append(item)
        except MigrationError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise MigrationStateError(
                "迁移 journal 损坏，已停止读取。", detail=exc.__class__.__name__
            ) from exc
        return entries

    def _load_plan_unlocked(self, plan_id: str) -> MigrationPlan:
        raw = _read_private_json(self._plan_path(plan_id), max_bytes=MAX_PLAN_BYTES, what="迁移计划")
        plan = MigrationPlan.from_dict(raw)
        plan.validate()
        return plan

    def _save_plan_unlocked(self, plan: MigrationPlan) -> None:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_private_json(self._plan_path(plan.plan_id), plan.to_dict())

    def _load_operation_unlocked(self, operation_id: str) -> MigrationOperation:
        raw = _read_private_json(
            self._operation_path(operation_id), max_bytes=MAX_OPERATION_BYTES, what="迁移操作"
        )
        return MigrationOperation.from_dict(raw)

    def _save_operation_unlocked(self, operation: MigrationOperation) -> None:
        operation.updated_at = self.clock()
        operation.validate()
        self._operation_dir(operation.operation_id).mkdir(parents=True, exist_ok=True)
        _atomic_write_private_json(self._operation_path(operation.operation_id), operation.to_dict())

    # ------------------------------------------------------------------
    # idempotency
    # ------------------------------------------------------------------
    def _load_idempotency(self) -> dict[str, Any]:
        if not self.idempotency_path.exists():
            return {"schema_version": 1, "entries": {}}
        raw = _read_private_json(
            self.idempotency_path, max_bytes=4 * 1024 * 1024, what="迁移幂等账本"
        )
        if raw.get("schema_version") != 1 or not isinstance(raw.get("entries"), dict):
            raise MigrationStateError("迁移幂等账本格式损坏，已停止读取。")
        return raw

    def _save_idempotency(self, data: Mapping[str, Any]) -> None:
        entries = data.get("entries")
        if not isinstance(entries, dict):
            raise MigrationStateError("迁移幂等账本格式损坏。")
        if len(entries) > MAX_IDEMPOTENCY_ENTRIES:
            ordered = sorted(entries.items(), key=lambda pair: str(pair[1].get("created_at") or ""))
            entries = dict(ordered[-MAX_IDEMPOTENCY_ENTRIES:])
        _atomic_write_private_json(
            self.idempotency_path,
            {"schema_version": 1, "entries": entries},
        )

    def _check_idempotency(
        self, key: str, request_hash: str, *, kind: str
    ) -> dict[str, Any] | None:
        data = self._load_idempotency()
        existing = data["entries"].get(key)
        if not isinstance(existing, Mapping):
            return None
        if not hmac.compare_digest(str(existing.get("request_hash") or ""), request_hash):
            raise MigrationIdempotencyConflict(
                "同一幂等键对应不同请求，已拒绝重放。"
            )
        operation_id = str(existing.get("operation_id") or "")
        if not operation_id:
            raise MigrationStateError("迁移幂等账本缺少操作 ID。")
        operation = self._load_operation_unlocked(operation_id)
        journal = self._read_journal(operation_id)
        return {
            "ok": True,
            "idempotent": True,
            "operation": self._public_operation(operation, journal=journal),
            "operation_id": operation.operation_id,
        }

    def _store_idempotency(self, key: str, request_hash: str, operation_id: str, *, kind: str) -> None:
        data = self._load_idempotency()
        entries = dict(data.get("entries") or {})
        entries[key] = {
            "request_hash": request_hash,
            "operation_id": operation_id,
            "kind": kind,
            "created_at": self.clock(),
        }
        self._save_idempotency({"schema_version": 1, "entries": entries})

    # ------------------------------------------------------------------
    # plan creation
    # ------------------------------------------------------------------
    def create_plan(self, payload: Mapping[str, Any], *, actor: str = "") -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise MigrationPlanError("迁移计划请求必须是 JSON object。")
        safe_payload = _bounded_json(dict(payload), max_bytes=MAX_PLAN_BYTES, where="迁移计划")
        _reject_sensitive_keys(safe_payload, where="迁移计划")
        expected_revision = safe_payload.get("expected_revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise MigrationPlanError("expected_revision 必须是非负整数。")
        stage = _safe_token(safe_payload.get("stage") or "manual", "stage", max_len=64)
        source_version = _safe_version(safe_payload.get("source_version"))
        target_version = _safe_version(safe_payload.get("target_version"))
        backup_paths = _normalise_entry_list(safe_payload.get("backup_paths"), field_name="backup_paths")
        stage_paths = _normalise_entry_list(safe_payload.get("stage_paths"), field_name="stage_paths")
        raw_closure = safe_payload.get("entrypoint_closure") or []
        if not isinstance(raw_closure, (list, tuple)) or len(raw_closure) > 5000:
            raise MigrationPlanError("entrypoint_closure 必须是有界数组。")
        entrypoint_closure = [_safe_token(item, "entrypoint", max_len=256) for item in raw_closure]

        with self._locked():
            try:
                current = self.state_store.load()
                current_exists = self.state_store.exists()
            except PluginStateError:
                raise
            current_revision = int(current.revision) if current_exists else 0
            if expected_revision != current_revision:
                raise MigrationConflict("owner state revision 已变化，请重新读取后生成计划。")
            target_owners = safe_payload.get("owners_after")
            if target_owners is None:
                target_owners = safe_payload.get("owners")
            if target_owners is None:
                target_owners = dict(current.owners)
            if not isinstance(target_owners, Mapping):
                raise MigrationPlanError("owners_after 必须是 JSON object。")
            unknown = {str(key) for key in target_owners} - set(OWNER_KEYS)
            if unknown:
                raise MigrationPlanError("owners_after 包含未知 owner 域。")
            owners_after = {
                key: str(target_owners.get(key, current.owners.get(key, "")) or "")
                for key in OWNER_KEYS
            }
            phase_after = str(safe_payload.get("phase") or current.phase).strip()
            legacy_load_allowed_after = safe_payload.get(
                "legacy_load_allowed", current.legacy_load_allowed
            )
            if not isinstance(legacy_load_allowed_after, bool):
                raise MigrationPlanError("legacy_load_allowed 必须是布尔值。")
            dependencies_after = safe_payload.get(
                "legacy_runtime_dependencies", list(current.legacy_runtime_dependencies)
            )
            if not isinstance(dependencies_after, (list, tuple)):
                raise MigrationPlanError("legacy_runtime_dependencies 必须是数组。")

            # Validate the target shape through the durable-state validator.
            # This rejects duplicate new owners and an unjustified final phase.
            epochs_after = {key: int(current.owner_epochs.get(key) or 0) for key in OWNER_KEYS}
            for key in OWNER_KEYS:
                if owners_after.get(key) != current.owners.get(key) or bool(
                    safe_payload.get("bump_all_epochs")
                ):
                    epochs_after[key] = max(1, epochs_after[key] + 1)
            preview_state = validate_owner_state(
                {
                    "schema_version": 2,
                    "revision": current_revision,
                    "phase": phase_after,
                    "owners": owners_after,
                    "owner_epochs": {key: max(1, epochs_after[key]) for key in OWNER_KEYS},
                    "legacy_load_allowed": legacy_load_allowed_after,
                    "legacy_runtime_dependencies": [
                        str(item) for item in dependencies_after
                    ],
                    "created_at": current.created_at,
                    "updated_at": self.clock(),
                }
            )
            del preview_state

            now = self.clock()
            plan_id = f"plan-{secrets.token_hex(10)}"
            token = f"mplan-{secrets.token_urlsafe(24)}"
            request_hash = _fingerprint(
                {
                    "instance_id": self.instance_id,
                    "stage": stage,
                    "source_version": source_version,
                    "target_version": target_version,
                    "expected_revision": expected_revision,
                    "owners_after": owners_after,
                    "phase_after": phase_after,
                    "legacy_load_allowed_after": legacy_load_allowed_after,
                    "legacy_runtime_dependencies_after": [str(item) for item in dependencies_after],
                    "backup_paths": backup_paths,
                    "stage_paths": stage_paths,
                    "entrypoint_closure": entrypoint_closure,
                }
            )
            plan = MigrationPlan(
                plan_id=plan_id,
                instance_id=self.instance_id,
                stage=stage,
                source_version=source_version,
                target_version=target_version,
                expected_revision=expected_revision,
                owners_before=dict(current.owners),
                owners_after=owners_after,
                owner_epochs_before={key: int(current.owner_epochs.get(key) or 0) for key in OWNER_KEYS},
                owner_epochs_after=epochs_after,
                phase_before=current.phase,
                phase_after=phase_after,
                legacy_load_allowed_before=bool(current.legacy_load_allowed),
                legacy_load_allowed_after=bool(legacy_load_allowed_after),
                legacy_runtime_dependencies_before=list(current.legacy_runtime_dependencies),
                legacy_runtime_dependencies_after=[str(item) for item in dependencies_after],
                backup_paths=backup_paths,
                stage_paths=stage_paths,
                entrypoint_closure=entrypoint_closure,
                request_hash=request_hash,
                preview_token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                created_at=now,
                expires_at=self.epoch() + PREVIEW_TTL_SECONDS,
            )
            plan.validate()
            self._save_plan_unlocked(plan)
            public = self._public_plan(plan)
            public["preview_token"] = token
            public["message"] = "迁移计划已生成；确认后才会执行 owner/代码/配置切换。"
            return {"ok": True, "plan": public, "preview_token": token, "requires_confirmation": True}

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self._locked():
            plan = self._load_plan_unlocked(plan_id)
            public = self._public_plan(plan)
            if plan.expires_at and plan.expires_at < self.epoch() and plan.status == "planned":
                public["status"] = "expired"
                public["expired"] = True
            return {"ok": True, "plan": public}

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------
    def _validate_idempotency_key(self, value: Any) -> str:
        key = str(value or "").strip()
        if not key or len(key) > 160 or _CONTROL_RE.search(key):
            raise MigrationPlanError("idempotency_key 不合法。")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", key):
            raise MigrationPlanError("idempotency_key 不合法。")
        return key

    def _consume_plan_locked(self, plan: MigrationPlan, token: str) -> None:
        if plan.status == "consumed":
            raise MigrationConflict("迁移计划已被使用，不能重复执行。")
        if plan.expires_at and plan.expires_at < self.epoch():
            raise MigrationPlanError("迁移计划已过期，请重新生成计划。")
        provided_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
        if not hmac.compare_digest(provided_hash, plan.preview_token_hash):
            raise MigrationPlanError("确认 token 不匹配，已拒绝执行。")
        plan.status = "consumed"
        plan.consumed_at = self.clock()
        self._save_plan_unlocked(plan)

    def create_operation(self, payload: Mapping[str, Any], *, actor: str = "") -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise MigrationPlanError("迁移操作请求必须是 JSON object。")
        safe_payload = _bounded_json(dict(payload), max_bytes=MAX_PLAN_BYTES, where="迁移操作")
        _reject_sensitive_keys(safe_payload, where="迁移操作")
        plan_id = str(safe_payload.get("plan_id") or "").strip()
        if not _ID_RE.fullmatch(plan_id):
            raise MigrationPlanError("plan_id 不合法。")
        token = str(safe_payload.get("preview_token") or safe_payload.get("confirm_token") or "").strip()
        if not token:
            raise MigrationPlanError("缺少确认 token，已拒绝执行。")
        if safe_payload.get("confirm") is not True:
            raise MigrationPlanError("必须显式 confirm=true 才能执行迁移。")
        idempotency_key = self._validate_idempotency_key(safe_payload.get("idempotency_key"))
        expected_revision = safe_payload.get("expected_revision")
        request_hash = _fingerprint(
            {
                "kind": "migration",
                "plan_id": plan_id,
                "expected_revision": expected_revision,
            }
        )
        with self._locked():
            existing = self._check_idempotency(idempotency_key, request_hash, kind="migration")
            if existing is not None:
                return existing
            plan = self._load_plan_unlocked(plan_id)
            if expected_revision is not None:
                if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                    raise MigrationPlanError("expected_revision 必须是非负整数。")
                if expected_revision != plan.expected_revision:
                    raise MigrationConflict("计划 revision 与请求不一致，请重新生成计划。")
            self._consume_plan_locked(plan, token)
            operation_id = f"op-{secrets.token_hex(12)}"
            now = self.clock()
            operation = MigrationOperation(
                operation_id=operation_id,
                instance_id=self.instance_id,
                kind="migration",
                plan_id=plan.plan_id,
                plan_hash=plan.request_hash,
                status=STATUS_RUNNING,
                current_state=INITIAL_STATE,
                pending_state="",
                attempt=0,
                created_at=now,
                updated_at=now,
                actor=str(actor or ""),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            self._operation_dir(operation_id).mkdir(parents=True, exist_ok=True)
            self._save_operation_unlocked(operation)
            self._append_journal(
                operation_id,
                {
                    "event": "operation_created",
                    "kind": "migration",
                    "plan_id": plan.plan_id,
                    "at": now,
                },
            )
            self._store_idempotency(idempotency_key, request_hash, operation_id, kind="migration")
            public = self._public_operation(operation, journal=self._read_journal(operation_id))
            return {
                "ok": True,
                "idempotent": False,
                "operation": public,
                "operation_id": operation_id,
                "status": operation.status,
            }

    def start_operation(self, payload: Mapping[str, Any], *, actor: str = "", run_async: bool = True) -> dict[str, Any]:
        """Create and (by default asynchronously) run one migration operation."""

        result = self.create_operation(payload, actor=actor)
        if result.get("idempotent"):
            return result
        operation_id = str(result.get("operation_id") or "")
        if run_async:
            thread = threading.Thread(
                target=self._run_async,
                args=(operation_id,),
                name=f"migration-{operation_id}",
                daemon=True,
            )
            thread.start()
        else:
            final = self.run_operation(operation_id)
            if isinstance(final, dict) and isinstance(final.get("operation"), dict):
                result["operation"] = final["operation"]
                result["status"] = final["operation"].get("status")
        return result

    def _run_async(self, operation_id: str) -> None:
        try:
            self.run_operation(operation_id)
        except Exception:
            # The step-level journal already persisted the pending intent; keep
            # the operation resumable instead of converting an unknown crash to
            # a fake failure or success.
            return

    def _get_operation_unlocked(self, operation_id: str) -> dict[str, Any]:
        operation = self._load_operation_unlocked(operation_id)
        journal = self._read_journal(operation_id)
        return {"ok": True, "operation": self._public_operation(operation, journal=journal)}

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        with self._locked():
            return self._get_operation_unlocked(operation_id)

    def _handler_for(self, state: str) -> Any | None:
        if isinstance(self._handlers, MigrationStepHandlers):
            return self._handlers.execute(state)
        if isinstance(self._handlers, Mapping):
            return self._handlers.get(state)
        return None

    def _handler_callable(self, state: str) -> Callable[[StepContext], Any]:
        handler = self._handler_for(state)
        if handler is None:
            builtin = getattr(self, f"_step_{state}", None)
            if builtin is None:
                raise MigrationStepError(f"迁移步骤 {state} 没有处理器。")
            return builtin  # type: ignore[return-value]
        execute = getattr(handler, "execute", None)
        if callable(execute):
            return execute
        if callable(handler):
            return handler
        raise MigrationStepError(f"迁移步骤 {state} 处理器不合法。")

    def _handler_recover(self, state: str, context: StepContext) -> str:
        handler = self._handler_for(state)
        recover = getattr(handler, "recover", None)
        if callable(recover):
            try:
                result = str(recover(context) or "").strip().lower()
            except Exception as exc:  # noqa: BLE001 - unknown recovery is fail-closed
                raise MigrationManualRecovery(
                    "迁移步骤恢复检查失败，必须人工确认。",
                    reason=exc.__class__.__name__,
                ) from exc
            if result in {"applied", "already_applied", "succeeded"}:
                return "applied"
            if result in {"not_applied", "missing", "retry"}:
                return "not_applied"
            return "unknown"
        builtin_recover = getattr(self, f"_recover_{state}", None)
        if callable(builtin_recover):
            try:
                result = str(builtin_recover(context) or "").strip().lower()
            except Exception:
                return "unknown"
            if result in {"applied", "already_applied"}:
                return "applied"
            if result in {"not_applied"}:
                return "not_applied"
            return "unknown"
        # Step handlers are contractually idempotent; re-running is allowed
        # only when the handler did not explicitly declare an unknown outcome.
        return "not_applied"

    def _normalise_step_result(self, result: Any) -> StepResult:
        if result is None:
            return StepResult()
        if isinstance(result, StepResult):
            return result
        if isinstance(result, Mapping):
            if "outcome" in result or "status" in result:
                outcome = str(result.get("outcome") or result.get("status") or "succeeded").lower()
                evidence = result.get("evidence") if isinstance(result.get("evidence"), Mapping) else {}
                reason = str(result.get("reason") or result.get("error") or "")
                return StepResult(outcome=outcome, evidence=dict(evidence), reason=reason)
            return StepResult(evidence=_bounded_json(dict(result), where="step evidence"))
        raise MigrationStepError("迁移步骤处理器返回类型不合法。")

    def _step_context(
        self,
        operation: MigrationOperation,
        plan: MigrationPlan,
        state: str,
        *,
        resume: bool,
    ) -> StepContext:
        operation_dir = self._operation_dir(operation.operation_id)
        return StepContext(
            operation_id=operation.operation_id,
            plan_id=operation.plan_id,
            instance_id=operation.instance_id,
            state=state,
            attempt=int(operation.attempt),
            resume=bool(resume),
            instance_dir=self.instance_dir,
            operation_dir=operation_dir,
            backup_dir=operation_dir / "backup",
            staging_dir=operation_dir / "staging",
            plan=self._public_plan(plan),
            operation=self._public_operation(operation),
        )

    def _next_state(self, current: str) -> str:
        try:
            index = FORWARD_STATES.index(current)
        except ValueError as exc:
            raise MigrationStateError("迁移操作当前状态非法。") from exc
        if index + 1 >= len(FORWARD_STATES):
            return "active"
        return FORWARD_STATES[index + 1]

    def advance_operation(self, operation_id: str) -> dict[str, Any]:
        """Execute at most one forward step and persist its journal result."""

        with self._locked():
            operation = self._load_operation_unlocked(operation_id)
            if operation.kind != "migration":
                raise MigrationError("该操作不是前向迁移操作。")
            if operation.status in {STATUS_COMPLETED, STATUS_MANUAL, STATUS_ROLLED_BACK}:
                return self._get_operation_unlocked(operation_id)
            plan = self._load_plan_unlocked(operation.plan_id)
            target_state = ""
            resumed = False
            if operation.pending_state:
                target_state = operation.pending_state
                if target_state not in TRANSITION_STEPS:
                    raise MigrationStateError("迁移 pending_state 不合法。")
                context = self._step_context(operation, plan, target_state, resume=True)
                recovery = self._handler_recover(target_state, context)
                self._append_journal(
                    operation_id,
                    {
                        "event": "recovery_check",
                        "state": target_state,
                        "outcome": recovery,
                        "at": self.clock(),
                    },
                )
                if recovery == "applied":
                    operation.current_state = target_state
                    operation.pending_state = ""
                    operation.status = STATUS_RUNNING
                    operation.history.append(
                        {
                            "state": target_state,
                            "outcome": "recovered_already_applied",
                            "at": self.clock(),
                        }
                    )
                    if target_state == "active":
                        operation.status = STATUS_COMPLETED
                    self._save_operation_unlocked(operation)
                    return self._get_operation_unlocked(operation_id)
                if recovery == "unknown":
                    operation.status = STATUS_MANUAL
                    operation.manual_recovery_reason = (
                        f"步骤 {target_state} 的结果无法确认，必须人工恢复。"
                    )
                    operation.last_error = {
                        "code": "outcome_unknown",
                        "message": operation.manual_recovery_reason,
                    }
                    self._append_journal(
                        operation_id,
                        {
                            "event": "manual_recovery_required",
                            "state": target_state,
                            "reason": operation.manual_recovery_reason,
                            "at": self.clock(),
                        },
                    )
                    self._save_operation_unlocked(operation)
                    return self._get_operation_unlocked(operation_id)
                resumed = True
            else:
                if operation.current_state == "active":
                    operation.status = STATUS_COMPLETED
                    self._save_operation_unlocked(operation)
                    return self._get_operation_unlocked(operation_id)
                target_state = self._next_state(operation.current_state)

            operation.attempt = int(operation.attempt or 0) + 1
            operation.pending_state = target_state
            operation.status = STATUS_RUNNING
            self._append_journal(
                operation_id,
                {
                    "event": "intent",
                    "state": target_state,
                    "attempt": operation.attempt,
                    "resume": resumed,
                    "plan_hash": operation.plan_hash,
                    "at": self.clock(),
                },
            )
            # Persist the intent before invoking the handler.  A process crash
            # after this point leaves pending_state for recover/resume.
            self._save_operation_unlocked(operation)
            context = self._step_context(operation, plan, target_state, resume=resumed)
            handler = self._handler_callable(target_state)
            try:
                raw_result = handler(context)
            except MigrationManualRecovery as exc:
                operation.status = STATUS_MANUAL
                operation.manual_recovery_reason = str(exc.reason or exc.message)
                operation.last_error = {
                    "code": exc.code,
                    "message": exc.message,
                    "reason": exc.reason,
                }
                self._append_journal(
                    operation_id,
                    {
                        "event": "manual_recovery_required",
                        "state": target_state,
                        "reason": operation.manual_recovery_reason,
                        "at": self.clock(),
                    },
                )
                self._save_operation_unlocked(operation)
                return self._get_operation_unlocked(operation_id)
            except MigrationStepError as exc:
                operation.status = STATUS_FAILED
                operation.pending_state = ""
                operation.last_error = {
                    "code": exc.code,
                    "message": exc.message,
                    "reason": exc.reason,
                }
                operation.history.append(
                    {
                        "state": target_state,
                        "outcome": "failed",
                        "code": exc.code,
                        "at": self.clock(),
                    }
                )
                self._append_journal(
                    operation_id,
                    {
                        "event": "step_failed",
                        "state": target_state,
                        "code": exc.code,
                        "message": exc.message,
                        "at": self.clock(),
                    },
                )
                self._save_operation_unlocked(operation)
                return self._get_operation_unlocked(operation_id)
            except Exception:
                # Unknown exception == crash.  Keep pending_state and re-raise
                # so a caller/test can explicitly resume.
                self._append_journal(
                    operation_id,
                    {
                        "event": "crash",
                        "state": target_state,
                        "attempt": operation.attempt,
                        "at": self.clock(),
                    },
                )
                self._save_operation_unlocked(operation)
                raise

            result = self._normalise_step_result(raw_result)
            evidence = _bounded_json(dict(result.evidence), max_bytes=64 * 1024, where="evidence")
            self._append_journal(
                operation_id,
                {
                    "event": "result",
                    "state": target_state,
                    "outcome": result.outcome,
                    "evidence": evidence,
                    "reason": result.reason,
                    "at": self.clock(),
                },
            )
            if result.outcome == "unknown":
                operation.status = STATUS_MANUAL
                operation.manual_recovery_reason = (
                    result.reason or f"步骤 {target_state} 的结果无法确认，必须人工恢复。"
                )
                operation.last_error = {
                    "code": "outcome_unknown",
                    "message": operation.manual_recovery_reason,
                }
                self._save_operation_unlocked(operation)
                return self._get_operation_unlocked(operation_id)
            operation.current_state = target_state
            operation.pending_state = ""
            operation.status = STATUS_COMPLETED if target_state == "active" else STATUS_RUNNING
            operation.history.append(
                {
                    "state": target_state,
                    "outcome": result.outcome,
                    "at": self.clock(),
                }
            )
            if result.evidence:
                operation.history[-1]["evidence"] = evidence
            if target_state == "backed_up" and isinstance(result.evidence.get("path"), str):
                operation.backup_manifest_path = result.evidence["path"]
            self._save_operation_unlocked(operation)
            return self._get_operation_unlocked(operation_id)

    def run_operation(self, operation_id: str, *, max_steps: int = 32) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for _ in range(max(1, int(max_steps))):
            result = self.advance_operation(operation_id)
            operation = result.get("operation") if isinstance(result, dict) else None
            if not isinstance(operation, Mapping):
                break
            if operation.get("status") in {
                STATUS_COMPLETED,
                STATUS_FAILED,
                STATUS_MANUAL,
                STATUS_ROLLED_BACK,
            }:
                break
            if operation.get("current_state") == "active":
                break
        return result

    def resume_operation(self, operation_id: str, *, force_manual: bool = False) -> dict[str, Any]:
        kind = ""
        with self._locked():
            operation = self._load_operation_unlocked(operation_id)
            kind = operation.kind
            if operation.status == STATUS_MANUAL and not force_manual:
                raise MigrationManualRecovery(
                    "迁移操作需要人工恢复，不能自动重试。",
                    reason=operation.manual_recovery_reason,
                )
            if operation.status == STATUS_ROLLED_BACK:
                return self._get_operation_unlocked(operation_id)
            if operation.status in {STATUS_FAILED, STATUS_MANUAL}:
                operation.status = STATUS_RUNNING
                operation.last_error = {}
                operation.manual_recovery_reason = ""
                self._save_operation_unlocked(operation)
                self._append_journal(
                    operation_id,
                    {"event": "resume_requested", "state": operation.current_state, "at": self.clock()},
                )
        if kind == "rollback":
            return self._run_rollback(operation_id)
        return self.run_operation(operation_id)

    def cancel_operation(self, operation_id: str) -> dict[str, Any]:
        with self._locked():
            operation = self._load_operation_unlocked(operation_id)
            if operation.status in {STATUS_COMPLETED, STATUS_ROLLED_BACK}:
                raise MigrationConflict("已完成的迁移操作不能取消。")
            operation.status = STATUS_FAILED
            operation.last_error = {"code": "cancelled", "message": "操作已取消，可稍后重新计划。"}
            operation.pending_state = ""
            self._save_operation_unlocked(operation)
            self._append_journal(operation_id, {"event": "cancelled", "at": self.clock()})
            return self._get_operation_unlocked(operation_id)

    # ------------------------------------------------------------------
    # built-in step handlers (fail closed without an injected runtime probe)
    # ------------------------------------------------------------------
    def _step_planned(self, context: StepContext) -> StepResult:
        plan = self._load_plan_unlocked(context.plan_id)
        if plan.plan_id != context.plan_id:
            raise MigrationStepError("迁移计划 ID 不一致，已停止。")
        return StepResult(evidence={"plan_id": plan.plan_id, "stage": plan.stage})

    def _step_verified(self, context: StepContext) -> StepResult:
        plan = self._load_plan_unlocked(context.plan_id)
        try:
            current = self.state_store.load()
            exists = self.state_store.exists()
        except PluginStateError as exc:
            raise MigrationStepError(exc.message, reason=exc.code) from exc
        current_revision = int(current.revision) if exists else 0
        if current_revision != plan.expected_revision:
            raise MigrationConflict("owner state revision 在计划后发生变化，请重新生成计划。")
        if exists and current.owners != plan.owners_before:
            raise MigrationConflict("owner state 在计划后发生变化，请重新生成计划。")
        return StepResult(evidence={"revision": current_revision, "verified": True})

    def _require_probe(self, callback: Callable[[StepContext], bool] | None, name: str) -> bool:
        if callback is None:
            raise MigrationBridgeUnavailable(
                f"{name} 探测未配置，迁移按失败关闭，未声称成功。"
            )
        try:
            return bool(callback)
        except TypeError:
            raise

    def _step_quiesced(self, context: StepContext) -> StepResult:
        if self.quiescence_check is None:
            raise MigrationBridgeUnavailable("运行时停写探测未配置，不能进入 quiesced。")
        if not bool(self.quiescence_check(context)):
            raise MigrationStepError("旧 writer/消息入口停写未得到确认，迁移已停止。")
        return StepResult(evidence={"quiesced": True})

    def _step_drained(self, context: StepContext) -> StepResult:
        if self.drain_check is None:
            raise MigrationBridgeUnavailable("运行时排空探测未配置，不能进入 drained。")
        if not bool(self.drain_check(context)):
            raise MigrationStepError("在途任务/工具调用未排空，迁移已停止。")
        return StepResult(evidence={"drained": True})

    def _step_backed_up(self, context: StepContext) -> StepResult:
        evidence = self._create_backup(context)
        evidence["path"] = "backup/manifest.json"
        return StepResult(evidence=evidence)

    def _step_staged(self, context: StepContext) -> StepResult:
        evidence = self._create_staging(context)
        evidence["path"] = "staging/manifest.json"
        return StepResult(evidence=evidence)

    def _step_switching(self, context: StepContext) -> StepResult:
        plan = self._load_plan_unlocked(context.plan_id)
        try:
            updated = self.state_store.apply_owner_plan(
                expected_revision=plan.expected_revision,
                owners=plan.owners_after,
                phase=plan.phase_after,
                legacy_load_allowed=plan.legacy_load_allowed_after,
                legacy_runtime_dependencies=plan.legacy_runtime_dependencies_after,
                bump_all_epochs=True,
                actor="migration",
            )
        except PluginRevisionConflict as exc:
            raise MigrationConflict("owner state revision 在切换前发生变化，迁移已停止。") from exc
        except PluginStateError as exc:
            raise MigrationStepError(exc.message, reason=exc.code) from exc
        return StepResult(
            evidence={
                "revision": updated.revision,
                "owners": dict(updated.owners),
                "epochs": dict(updated.owner_epochs),
            }
        )

    def _step_starting(self, context: StepContext) -> StepResult:
        if self.start_probe is None:
            raise MigrationBridgeUnavailable("新组件启动探测未配置，不能声称 starting 成功。")
        if not bool(self.start_probe(context)):
            raise MigrationStepError("新组件未通过启动探测，迁移已停止。")
        return StepResult(evidence={"started": True})

    def _step_checking(self, context: StepContext) -> StepResult:
        plan = self._load_plan_unlocked(context.plan_id)
        try:
            current = self.state_store.load()
        except PluginStateError as exc:
            raise MigrationStepError(exc.message, reason=exc.code) from exc
        if current.owners != plan.owners_after:
            raise MigrationStepError("owner state 与计划目标不一致。")
        if any(int(current.owner_epochs.get(key) or 0) <= int(plan.owner_epochs_before.get(key) or 0) for key in OWNER_KEYS):
            # At least one epoch must advance; bump_all_epochs in switching
            # guarantees this, but never accept a stale owner state.
            raise MigrationStepError("owner epoch 未推进，切换不能被确认。")
        if self.check_probe is not None and not bool(self.check_probe(context)):
            raise MigrationStepError("新组件健康/单 writer 检查未通过。")
        return StepResult(evidence={"owners_match": True, "check_probe": self.check_probe is not None})

    def _step_committing(self, context: StepContext) -> StepResult:
        if self.commit_probe is None:
            raise MigrationBridgeUnavailable("提交探测未配置，不能声称 committing 成功。")
        if not bool(self.commit_probe(context)):
            raise MigrationStepError("提交探测未通过，迁移已停止。")
        return StepResult(evidence={"committed": True})

    def _step_active(self, context: StepContext) -> StepResult:
        plan = self._load_plan_unlocked(context.plan_id)
        try:
            current = self.state_store.load()
        except PluginStateError as exc:
            raise MigrationStepError(exc.message, reason=exc.code) from exc
        if current.owners != plan.owners_after:
            raise MigrationStepError("最终 owner state 与计划不一致，拒绝声明 active。")
        if plan.phase_after == "final" and not current.is_final_ready():
            raise MigrationStepError("最终阶段门禁未满足，拒绝声明 active。")
        return StepResult(outcome="succeeded", evidence={"active": True, "revision": current.revision})

    # Recovery checks for built-in side-effecting steps.
    def _recover_backed_up(self, context: StepContext) -> str:
        manifest_path = context.operation_dir / "backup" / "manifest.json"
        if manifest_path.exists() and not manifest_path.is_symlink():
            try:
                raw = _read_private_json(manifest_path, max_bytes=8 * 1024 * 1024, what="备份清单")
            except MigrationError:
                return "unknown"
            return "applied" if raw.get("operation_id") == context.operation_id else "unknown"
        return "not_applied"

    def _recover_staged(self, context: StepContext) -> str:
        manifest_path = context.operation_dir / "staging" / "manifest.json"
        return "applied" if manifest_path.exists() and not manifest_path.is_symlink() else "not_applied"

    def _recover_switching(self, context: StepContext) -> str:
        plan = self._load_plan_unlocked(context.plan_id)
        try:
            current = self.state_store.load()
            exists = self.state_store.exists()
        except PluginStateError:
            return "unknown"
        if not exists:
            return "not_applied"
        if current.owners != plan.owners_after:
            return "not_applied"
        if int(current.revision) <= int(plan.expected_revision):
            return "not_applied"
        return "applied"

    def _recover_active(self, context: StepContext) -> str:
        plan = self._load_plan_unlocked(context.plan_id)
        try:
            current = self.state_store.load()
        except PluginStateError:
            return "unknown"
        return "applied" if current.owners == plan.owners_after else "not_applied"

    # ------------------------------------------------------------------
    # backup/staging
    # ------------------------------------------------------------------
    def _assert_source_path(self, relative: str) -> Path:
        safe = _safe_relative_path(relative)
        source = self.instance_dir / PurePosixPath(safe)
        if not _is_within(source, self.instance_dir):
            raise MigrationPlanError("备份路径越界，已拒绝。")
        # Refuse symlinked ancestors and files.
        current = self.instance_dir
        for part in PurePosixPath(safe).parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            except OSError as exc:
                raise MigrationStepError("无法检查备份路径。", detail=exc.__class__.__name__) from exc
            if stat_is_symlink(info.st_mode):
                raise MigrationPlanError("备份路径不能包含符号链接，已拒绝。")
        return source

    def _iter_backup_files(self, source: Path, relative: str) -> list[tuple[Path, str]]:
        if not source.exists():
            return []
        if source.is_file():
            return [(source, relative)]
        if not source.is_dir():
            raise MigrationPlanError("备份路径必须是普通文件或目录。")
        files: list[tuple[Path, str]] = []
        for path in sorted(source.rglob("*")):
            try:
                info = path.lstat()
            except OSError as exc:
                raise MigrationStepError("无法读取备份目录。", detail=exc.__class__.__name__) from exc
            if stat_is_symlink(info.st_mode):
                raise MigrationPlanError("备份目录不能包含符号链接，已拒绝。")
            if path.is_dir():
                continue
            if not path.is_file():
                raise MigrationPlanError("备份目录不能包含特殊文件。")
            rel = str(PurePosixPath(relative) / path.relative_to(source))
            files.append((path, rel))
            if len(files) > self.max_backup_files:
                raise MigrationStepError("备份文件数量超过限制。")
        return files

    def _create_backup(self, context: StepContext) -> dict[str, Any]:
        plan = self._load_plan_unlocked(context.plan_id)
        backup_dir = context.operation_dir / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        total_bytes = 0
        for entry in plan.backup_paths:
            relative = entry["path"]
            kind = entry["kind"]
            source = self._assert_source_path(relative)
            files = self._iter_backup_files(source, relative)
            if not files:
                entries.append(
                    {"path": relative, "kind": kind, "exists": False, "files": []}
                )
                continue
            entry_files = []
            for source_file, file_relative in files:
                digest, size = _sha256_file(source_file)
                total_bytes += size
                if total_bytes > self.max_backup_bytes:
                    raise MigrationStepError("备份总大小超过限制。")
                safe_file_relative = _safe_relative_path(file_relative)
                target = backup_dir / "files" / kind / PurePosixPath(safe_file_relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.parent / f".{target.name}.{secrets.token_hex(6)}.tmp"
                try:
                    shutil.copyfile(source_file, tmp)
                    os.replace(tmp, target)
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
                finally:
                    try:
                        if tmp.exists():
                            tmp.unlink()
                    except OSError:
                        pass
                entry_files.append(
                    {
                        "path": safe_file_relative,
                        "backup": str(target.relative_to(backup_dir)),
                        "sha256": digest,
                        "bytes": size,
                        "mode": oct(stat_mode(source_file)),
                    }
                )
            entries.append(
                {"path": relative, "kind": kind, "exists": True, "files": entry_files}
            )
        # Snapshot owner state as metadata; this is a config-state backup, not
        # business data.  If it is missing, record that explicitly.
        state_entries = []
        if self.state_store.state_path.exists():
            digest, size = _sha256_file(self.state_store.state_path)
            target = backup_dir / "files" / "metadata" / "owner-state.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.state_store.state_path, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            state_entries.append(
                {
                    "path": "studio-plugins/owner-state.json",
                    "backup": str(target.relative_to(backup_dir)),
                    "sha256": digest,
                    "bytes": size,
                    "mode": oct(stat_mode(self.state_store.state_path)),
                }
            )
        manifest = {
            "schema_version": 1,
            "kind": "migration_backup",
            "operation_id": context.operation_id,
            "instance_id": self.instance_id,
            "created_at": self.clock(),
            "data_policy": "preserve_never_delete",
            "business_data_backed_up": False,
            "entries": entries,
            "owner_state_entries": state_entries,
        }
        _atomic_write_private_json(backup_dir / "manifest.json", manifest)
        return {
            "entries": len(entries),
            "files": sum(len(item.get("files") or []) for item in entries),
            "bytes": total_bytes,
            "manifest": "backup/manifest.json",
        }

    def _create_staging(self, context: StepContext) -> dict[str, Any]:
        plan = self._load_plan_unlocked(context.plan_id)
        staging_dir = context.operation_dir / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for entry in plan.stage_paths:
            source = self._assert_source_path(entry["path"])
            entries.append(
                {
                    "path": entry["path"],
                    "kind": entry["kind"],
                    "exists": source.exists(),
                }
            )
        manifest = {
            "schema_version": 1,
            "kind": "migration_staging",
            "operation_id": context.operation_id,
            "target_version": plan.target_version,
            "target_owners": dict(plan.owners_after),
            "entries": entries,
            "data_policy": "preserve_never_delete",
            "created_at": self.clock(),
        }
        _atomic_write_private_json(staging_dir / "manifest.json", manifest)
        return {"entries": len(entries), "manifest": "staging/manifest.json"}

    # ------------------------------------------------------------------
    # rollback
    # ------------------------------------------------------------------
    def _read_backup_manifest(self, operation_id: str) -> dict[str, Any]:
        operation = self._load_operation_unlocked(operation_id)
        path = self._operation_dir(operation_id) / "backup" / "manifest.json"
        if not path.exists():
            raise MigrationNotFound("该操作没有可用的备份清单。")
        raw = _read_private_json(path, max_bytes=16 * 1024 * 1024, what="备份清单")
        if raw.get("schema_version") != 1 or raw.get("kind") != "migration_backup":
            raise MigrationStateError("备份清单格式不受支持。")
        return raw

    def _new_data_readable(self) -> dict[str, Any]:
        problems: list[str] = []
        if self.new_data_probe is not None:
            try:
                result = self.new_data_probe()
            except Exception as exc:  # noqa: BLE001 - an unreadable probe blocks
                return {"readable": False, "problems": [f"probe_failed:{exc.__class__.__name__}"]}
            if isinstance(result, Mapping):
                return {
                    "readable": bool(result.get("readable", False)),
                    "problems": [str(item) for item in result.get("problems") or []],
                }
            return {"readable": bool(result), "problems": [] if result else ["probe_unavailable"]}
        roots = [
            self.instance_dir / "data",
            self.instance_dir / "data" / "astrbot" / "data",
            self.instance_dir / "data" / "astrbot" / "plugin_data",
        ]
        checked = 0
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_dir() or path.is_symlink():
                    continue
                checked += 1
                if checked > 5000:
                    break
                try:
                    with path.open("rb") as handle:
                        handle.read(1)
                except OSError as exc:
                    problems.append(f"{path.relative_to(self.instance_dir)}:{exc.__class__.__name__}")
        return {"readable": not problems, "problems": problems[:50]}

    def create_rollback_plan(self, payload: Mapping[str, Any], *, actor: str = "") -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise MigrationPlanError("回滚计划请求必须是 JSON object。")
        safe_payload = _bounded_json(dict(payload), max_bytes=MAX_PLAN_BYTES, where="回滚计划")
        _reject_sensitive_keys(safe_payload, where="回滚计划")
        target_operation_id = str(
            safe_payload.get("target_operation_id") or safe_payload.get("operation_id") or ""
        ).strip()
        if not _ID_RE.fullmatch(target_operation_id):
            raise MigrationPlanError("target_operation_id 不合法。")
        target_snapshot = str(safe_payload.get("target_snapshot") or "").strip()
        target_version = _safe_version(safe_payload.get("target_version"))
        with self._locked():
            target_operation = self._load_operation_unlocked(target_operation_id)
            if target_operation.kind != "migration":
                raise MigrationPlanError("只能以前向迁移操作作为回滚目标。")
            if target_operation.status not in {STATUS_COMPLETED, STATUS_FAILED, STATUS_MANUAL}:
                raise MigrationConflict("目标迁移操作尚未完成，不能生成回滚计划。")
            backup = self._read_backup_manifest(target_operation_id)
            target_plan = self._load_plan_unlocked(target_operation.plan_id)
            current = self.state_store.load()
            current_revision = int(current.revision) if self.state_store.exists() else 0
            if safe_payload.get("expected_revision") is not None:
                expected = safe_payload.get("expected_revision")
                if isinstance(expected, bool) or not isinstance(expected, int) or expected != current_revision:
                    raise MigrationConflict("owner state revision 已变化，请重新生成回滚计划。")
            readability = self._new_data_readable()
            rollback_plan_id = f"rbplan-{secrets.token_hex(10)}"
            token = f"rbplan-{secrets.token_urlsafe(24)}"
            now = self.clock()
            record = {
                "schema_version": 1,
                "kind": "rollback_plan",
                "rollback_plan_id": rollback_plan_id,
                "instance_id": self.instance_id,
                "target_operation_id": target_operation_id,
                "target_plan_id": target_plan.plan_id,
                "target_snapshot": target_snapshot,
                "target_version": target_version,
                "current_revision": current_revision,
                "target_owners": dict(target_plan.owners_before),
                "target_phase": target_plan.phase_before,
                "target_legacy_load_allowed": target_plan.legacy_load_allowed_before,
                "target_legacy_runtime_dependencies": list(target_plan.legacy_runtime_dependencies_before),
                "backup_manifest": f"operations/{target_operation_id}/backup/manifest.json",
                "backup_entries": len(backup.get("entries") or []),
                "new_data_readable": bool(readability.get("readable")),
                "new_data_problems": list(readability.get("problems") or []),
                "ready": bool(readability.get("readable")),
                "request_hash": _fingerprint(
                    {
                        "instance_id": self.instance_id,
                        "target_operation_id": target_operation_id,
                        "current_revision": current_revision,
                        "target_snapshot": target_snapshot,
                        "target_version": target_version,
                    }
                ),
                "preview_token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "created_at": now,
                "expires_at": self.epoch() + PREVIEW_TTL_SECONDS,
                "status": "planned",
                "consumed_at": "",
            }
            self.plans_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_private_json(self.plans_dir / f"{rollback_plan_id}.json", record)
            public = {
                "rollback_plan_id": rollback_plan_id,
                "instance_id": self.instance_id,
                "target_operation_id": target_operation_id,
                "target_plan_id": target_plan.plan_id,
                "target_snapshot": target_snapshot,
                "target_version": target_version,
                "current_revision": current_revision,
                "target_owners": dict(target_plan.owners_before),
                "target_phase": target_plan.phase_before,
                "target_legacy_load_allowed": target_plan.legacy_load_allowed_before,
                "backup_entries": len(backup.get("entries") or []),
                "data_policy": "preserve_never_delete",
                "new_data_readable": bool(readability.get("readable")),
                "new_data_problems": list(readability.get("problems") or []),
                "ready": bool(readability.get("readable")),
                "requires_confirmation": True,
                "preview_token": token,
                "expires_at": record["expires_at"],
                "warnings": (
                    []
                    if readability.get("readable")
                    else ["新业务数据不可读，回滚执行会被阻塞，必须先人工恢复。"]
                ),
            }
            return {"ok": True, "plan": public, "preview_token": token, "requires_confirmation": True}

    def get_rollback_plan(self, rollback_plan_id: str) -> dict[str, Any]:
        with self._locked():
            path = self.plans_dir / f"{rollback_plan_id}.json"
            if not _ID_RE.fullmatch(str(rollback_plan_id or "")) or not path.exists():
                raise MigrationNotFound("回滚计划不存在。")
            raw = _read_private_json(path, max_bytes=MAX_PLAN_BYTES, what="回滚计划")
            if raw.get("kind") != "rollback_plan":
                raise MigrationStateError("回滚计划格式不受支持。")
            raw.pop("preview_token_hash", None)
            raw.pop("request_hash", None)
            return {"ok": True, "plan": raw}

    def _load_rollback_plan(self, rollback_plan_id: str) -> dict[str, Any]:
        path = self.plans_dir / f"{rollback_plan_id}.json"
        if not _ID_RE.fullmatch(str(rollback_plan_id or "")) or not path.exists():
            raise MigrationNotFound("回滚计划不存在。")
        raw = _read_private_json(path, max_bytes=MAX_PLAN_BYTES, what="回滚计划")
        if raw.get("kind") != "rollback_plan":
            raise MigrationStateError("回滚计划格式不受支持。")
        return raw

    def _preserve_current_file(self, operation_dir: Path, relative: str) -> str:
        source = self._assert_source_path(relative)
        if not source.exists() or not source.is_file():
            return ""
        preserve_dir = operation_dir / "preserved"
        target = preserve_dir / PurePosixPath(_safe_relative_path(relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return str(target.relative_to(operation_dir))

    def start_rollback(self, payload: Mapping[str, Any], *, actor: str = "", run_async: bool = True) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise MigrationPlanError("回滚操作请求必须是 JSON object。")
        safe_payload = _bounded_json(dict(payload), max_bytes=MAX_PLAN_BYTES, where="回滚操作")
        _reject_sensitive_keys(safe_payload, where="回滚操作")
        rollback_plan_id = str(
            safe_payload.get("rollback_plan_id") or safe_payload.get("plan_id") or ""
        ).strip()
        if not _ID_RE.fullmatch(rollback_plan_id):
            raise MigrationPlanError("rollback_plan_id 不合法。")
        token = str(safe_payload.get("preview_token") or safe_payload.get("confirm_token") or "").strip()
        if not token:
            raise MigrationPlanError("缺少确认 token，已拒绝执行。")
        if safe_payload.get("confirm") is not True:
            raise MigrationPlanError("必须显式 confirm=true 才能执行回滚。")
        idempotency_key = self._validate_idempotency_key(safe_payload.get("idempotency_key"))
        request_hash = _fingerprint({"kind": "rollback", "rollback_plan_id": rollback_plan_id})
        with self._locked():
            existing = self._check_idempotency(idempotency_key, request_hash, kind="rollback")
            if existing is not None:
                return existing
            plan = self._load_rollback_plan(rollback_plan_id)
            if plan.get("status") == "consumed":
                raise MigrationConflict("回滚计划已被使用，不能重复执行。")
            if float(plan.get("expires_at") or 0) < self.epoch():
                raise MigrationPlanError("回滚计划已过期，请重新生成。")
            provided_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(provided_hash, str(plan.get("preview_token_hash") or "")):
                raise MigrationPlanError("确认 token 不匹配，已拒绝执行。")
            if not bool(plan.get("new_data_readable")):
                raise MigrationStepError("新业务数据不可读，回滚已被阻塞，禁止用旧快照覆盖。")
            plan["status"] = "consumed"
            plan["consumed_at"] = self.clock()
            _atomic_write_private_json(self.plans_dir / f"{rollback_plan_id}.json", plan)
            operation_id = f"rb-{secrets.token_hex(12)}"
            now = self.clock()
            operation = MigrationOperation(
                operation_id=operation_id,
                instance_id=self.instance_id,
                kind="rollback",
                plan_id=rollback_plan_id,
                plan_hash=str(plan.get("request_hash") or ""),
                status=STATUS_RUNNING,
                current_state="rolling_back",
                pending_state="",
                attempt=0,
                created_at=now,
                updated_at=now,
                actor=str(actor or ""),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                rollback_plan_id=rollback_plan_id,
            )
            self._operation_dir(operation_id).mkdir(parents=True, exist_ok=True)
            self._save_operation_unlocked(operation)
            self._append_journal(
                operation_id,
                {"event": "rollback_created", "rollback_plan_id": rollback_plan_id, "at": now},
            )
            self._store_idempotency(idempotency_key, request_hash, operation_id, kind="rollback")
            result = {
                "ok": True,
                "idempotent": False,
                "operation": self._public_operation(operation, journal=self._read_journal(operation_id)),
                "operation_id": operation_id,
                "status": operation.status,
            }
        if run_async:
            thread = threading.Thread(
                target=self._run_rollback_async,
                args=(operation_id,),
                name=f"rollback-{operation_id}",
                daemon=True,
            )
            thread.start()
        else:
            final = self._run_rollback(operation_id)
            if isinstance(final, dict) and isinstance(final.get("operation"), dict):
                result["operation"] = final["operation"]
                result["status"] = final["operation"].get("status")
        return result

    def _run_rollback_async(self, operation_id: str) -> None:
        try:
            self._run_rollback(operation_id)
        except Exception:
            return

    def _run_rollback(self, operation_id: str) -> dict[str, Any]:
        with self._locked():
            operation = self._load_operation_unlocked(operation_id)
            if operation.kind != "rollback":
                raise MigrationError("该操作不是回滚操作。")
            if operation.status in {STATUS_ROLLED_BACK, STATUS_MANUAL}:
                return self._get_operation_unlocked(operation_id)
            plan = self._load_rollback_plan(operation.rollback_plan_id or operation.plan_id)
            try:
                target_operation = self._load_operation_unlocked(str(plan.get("target_operation_id") or ""))
                target_plan = self._load_plan_unlocked(target_operation.plan_id)
                backup = self._read_backup_manifest(target_operation.operation_id)
                operation_dir = self._operation_dir(operation_id)
                self._append_journal(
                    operation_id,
                    {"event": "rollback_intent", "stage": "restore", "at": self.clock()},
                )
                restored_files = 0
                preserved_files: list[str] = []
                for entry in backup.get("entries") or []:
                    if not isinstance(entry, Mapping):
                        raise MigrationStateError("备份清单条目损坏。")
                    if not bool(entry.get("exists")):
                        continue
                    kind = str(entry.get("kind") or "")
                    if kind not in BACKUP_KINDS:
                        raise MigrationStateError("备份清单包含不允许恢复的类型。")
                    for file_entry in entry.get("files") or []:
                        if not isinstance(file_entry, Mapping):
                            raise MigrationStateError("备份文件条目损坏。")
                        relative = _safe_relative_path(file_entry.get("path"))
                        backup_relative = _safe_relative_path(file_entry.get("backup"))
                        target_operation_dir = self._operation_dir(target_operation.operation_id)
                        backup_file = target_operation_dir / "backup" / backup_relative
                        if not _is_within(backup_file, target_operation_dir):
                            raise MigrationStateError("备份文件路径越界，已拒绝。")
                        if not backup_file.is_file() or backup_file.is_symlink():
                            raise MigrationManualRecovery(
                                "备份文件缺失或为符号链接，必须人工恢复。",
                                reason=relative,
                            )
                        expected_sha = str(file_entry.get("sha256") or "").lower()
                        actual_sha, _ = _sha256_file(backup_file)
                        if not hmac.compare_digest(expected_sha, actual_sha):
                            raise MigrationManualRecovery(
                                "备份文件哈希不匹配，必须人工恢复。",
                                reason=relative,
                            )
                        # Preserve the current file (new business/config state)
                        # before overwriting it with the snapshot.
                        preserved = self._preserve_current_file(operation_dir, relative)
                        if preserved:
                            preserved_files.append(preserved)
                        target = self._assert_source_path(relative)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        tmp = target.parent / f".{target.name}.{secrets.token_hex(6)}.restore"
                        try:
                            shutil.copyfile(backup_file, tmp)
                            os.replace(tmp, target)
                            try:
                                os.chmod(target, 0o600)
                            except OSError:
                                pass
                        finally:
                            try:
                                if tmp.exists():
                                    tmp.unlink()
                            except OSError:
                                pass
                        restored_files += 1
                        actual_restored, _ = _sha256_file(target)
                        if not hmac.compare_digest(expected_sha, actual_restored):
                            raise MigrationManualRecovery(
                                "恢复后的文件哈希不匹配，必须人工恢复。",
                                reason=relative,
                            )
                self._append_journal(
                    operation_id,
                    {
                        "event": "rollback_result",
                        "stage": "restore",
                        "files": restored_files,
                        "preserved": len(preserved_files),
                        "at": self.clock(),
                    },
                )
                # Revert ownership but always bump every epoch so clients that
                # hold an epoch from the failed generation cannot continue.
                current_revision = int(self.state_store.load().revision) if self.state_store.exists() else 0
                state = self.state_store.apply_owner_plan(
                    expected_revision=current_revision,
                    owners=plan.get("target_owners") or {},
                    phase=str(plan.get("target_phase") or "legacy"),
                    legacy_load_allowed=bool(plan.get("target_legacy_load_allowed", True)),
                    legacy_runtime_dependencies=list(plan.get("target_legacy_runtime_dependencies") or []),
                    bump_all_epochs=True,
                    actor="rollback",
                )
                operation.status = STATUS_ROLLED_BACK
                operation.current_state = "rolled_back"
                operation.pending_state = ""
                operation.history.append(
                    {
                        "state": "rolled_back",
                        "outcome": "succeeded",
                        "at": self.clock(),
                        "restored_files": restored_files,
                        "preserved_files": len(preserved_files),
                    }
                )
                operation.last_error = {}
                self._save_operation_unlocked(operation)
                self._append_journal(
                    operation_id,
                    {
                        "event": "rollback_completed",
                        "owner_state_revision": state.revision,
                        "owner_epochs": dict(state.owner_epochs),
                        "data_policy": "preserve_never_delete",
                        "at": self.clock(),
                    },
                )
                return self._get_operation_unlocked(operation_id)
            except MigrationManualRecovery as exc:
                operation.status = STATUS_MANUAL
                operation.pending_state = "rolling_back"
                operation.manual_recovery_reason = str(exc.reason or exc.message)
                operation.last_error = {"code": exc.code, "message": exc.message, "reason": exc.reason}
                self._append_journal(
                    operation_id,
                    {
                        "event": "rollback_manual_recovery_required",
                        "reason": operation.manual_recovery_reason,
                        "at": self.clock(),
                    },
                )
                self._save_operation_unlocked(operation)
                return self._get_operation_unlocked(operation_id)
            except MigrationError as exc:
                operation.status = STATUS_FAILED
                operation.pending_state = ""
                operation.last_error = {"code": exc.code, "message": exc.message, "reason": exc.reason}
                operation.history.append(
                    {"state": "rolling_back", "outcome": "failed", "code": exc.code, "at": self.clock()}
                )
                self._append_journal(
                    operation_id,
                    {"event": "rollback_failed", "code": exc.code, "message": exc.message, "at": self.clock()},
                )
                self._save_operation_unlocked(operation)
                return self._get_operation_unlocked(operation_id)

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def status(self, *, bridge_status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._locked():
            try:
                snapshot = self.state_store.snapshot()
            except PluginStateError as exc:
                return {
                    "ok": False,
                    "code": exc.code,
                    "message": exc.message,
                    "fail_closed": True,
                    "owner_state": None,
                }
            active_operation = None
            try:
                if self.operations_dir.exists():
                    operations = []
                    for path in self.operations_dir.iterdir():
                        if path.is_dir() and _ID_RE.fullmatch(path.name):
                            try:
                                op = self._load_operation_unlocked(path.name)
                            except MigrationError:
                                continue
                            operations.append(op)
                    operations.sort(key=lambda item: item.updated_at, reverse=True)
                    if operations:
                        active_operation = self._public_operation(operations[0])
            except OSError:
                active_operation = None
            owner_state = {
                key: snapshot.get(key)
                for key in (
                    "schema_version",
                    "revision",
                    "phase",
                    "owners",
                    "owner_epochs",
                    "legacy_load_allowed",
                    "legacy_runtime_dependencies",
                    "exists",
                )
            }
            writer_readiness = {}
            for domain in OWNER_KEYS:
                owner = str((owner_state.get("owners") or {}).get(domain) or "")
                epoch = int((owner_state.get("owner_epochs") or {}).get(domain) or 0)
                writer_readiness[domain] = {
                    "owner": owner,
                    "epoch": epoch,
                    "ready": bool(owner) and bool(epoch),
                }
            bridge = dict(bridge_status or {
                "ok": True,
                "enabled": False,
                "configured": False,
                "ready": False,
                "reachable": None,
                "reason": "bridge_disabled",
            })
            return {
                "ok": True,
                "instance_id": self.instance_id,
                "owner_state": owner_state,
                "abi": {
                    "control_protocol_version": 1,
                    "state_schema_version": 2,
                    "bridge_ready": bool(bridge.get("ready")),
                },
                "legacy_entrypoints": {
                    "closure_required": True,
                    "closed": [],
                    "total": 0,
                    "message": "旧入口闭合进度由 S0 盘点/S3-S7 交接任务写入；S2 不伪造闭合。",
                },
                "writer_readiness": writer_readiness,
                "bridge": bridge,
                "active_operation": active_operation,
                "fail_closed": not bool(snapshot.get("exists")),
            }


def stat_mode(path: Path) -> int:  # pragma: no cover - tiny wrapper
    return path.stat().st_mode & 0o777


def stat_is_symlink(mode: int) -> bool:
    return (mode & 0o170000) == 0o120000


# Friendly aliases for server/integration callers.
MigrationService = PluginMigrationService
MigrationState = MigrationOperation
StepHandlers = MigrationStepHandlers

__all__ = [
    "BACKUP_KINDS",
    "FORWARD_STATES",
    "INITIAL_STATE",
    "MIGRATION_STATES",
    "MigrationBridgeUnavailable",
    "MigrationConflict",
    "MigrationCrash",
    "MigrationError",
    "MigrationIdempotencyConflict",
    "MigrationManualRecovery",
    "MigrationNotFound",
    "MigrationOperation",
    "MigrationPlan",
    "MigrationPlanError",
    "MigrationService",
    "MigrationState",
    "MigrationStateError",
    "MigrationStepError",
    "MigrationStepHandlers",
    "PluginMigrationService",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_MANUAL",
    "STATUS_ROLLED_BACK",
    "STATUS_ROLLING_BACK",
    "STATUS_RUNNING",
    "StepContext",
    "StepHandlers",
    "StepResult",
    "TRANSITION_STEPS",
]
