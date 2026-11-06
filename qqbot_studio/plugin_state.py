"""Durable plugin-owner state (schema v2) for one Studio instance.

This module is deliberately dependency-free: it only uses the Python standard
library and never imports plugin code.  The state file lives inside the
instance's private ``studio-plugins/`` directory and is written atomically with
mode ``0600``.  Reads are fail-closed: a damaged or unexpected file raises
instead of being silently reset, because resetting ownership state would let
two writers become active after a crash.

State shape (schema v2)::

    {
      "schema_version": 2,
      "revision": 4,
      "phase": "v2",
      "owners": {
        "reply": "qqbot_core_reply",
        "marker": "qqbot_safe_tools",
        ...
      },
      "owner_epochs": {"reply": 3, "marker": 2, ...},
      "legacy_load_allowed": false,
      "legacy_runtime_dependencies": [],
      "created_at": "...",
      "updated_at": "..."
    }

The file intentionally contains no user secrets.  Validation rejects sensitive
key names anywhere in the object, path-like dependency names, and unknown
top-level fields.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

try:  # POSIX file lock; Windows has a small fallback below.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None  # type: ignore
try:  # pragma: no cover - exercised on Windows
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None  # type: ignore

OWNER_STATE_SCHEMA_VERSION = 2
STATE_DIR_NAME = "studio-plugins"
STATE_FILE_NAME = "owner-state.json"
STATE_LOCK_NAME = "owner-state.lock"

# Ownership domains from the migration contract.  ``reply`` and ``marker`` are
# component domains; the remaining names match the seven final plugins.
OWNER_KEYS: tuple[str, ...] = (
    "reply",
    "marker",
    "web_reader",
    "image_search",
    "food",
    "stickers",
    "memory_commands",
)

# The owner state phase describes the deployed ownership generation.  A v2
# migration must never write ``final`` merely because a request asks for it.
OWNER_PHASES: tuple[str, ...] = ("legacy", "v2", "v3", "final")

MAX_STATE_BYTES = 512 * 1024
MAX_OWNER_LENGTH = 128
MAX_DEPENDENCIES = 64

_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_OWNER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|"
    r"secret|credential|token|private[_-]?key|cookie)"
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "revision",
        "phase",
        "owners",
        "owner_epochs",
        "legacy_load_allowed",
        "legacy_runtime_dependencies",
        "created_at",
        "updated_at",
    }
)


class PluginStateError(RuntimeError):
    """Base class for fail-closed owner-state errors."""

    http_status = 500
    code = "plugin_state_error"

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(str(message))
        self.message = str(message)
        self.detail = str(detail or "")

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


class PluginStateCorruptError(PluginStateError):
    http_status = 503
    code = "plugin_state_corrupt"


class PluginStatePathError(PluginStateError):
    http_status = 400
    code = "unsafe_plugin_state_path"


class PluginRevisionConflict(PluginStateError):
    http_status = 409
    code = "revision_conflict"


class DuplicateOwnerError(PluginStateError):
    http_status = 409
    code = "duplicate_owner"


class StaleOwnerEpochError(PluginStateError):
    http_status = 409
    code = "stale_owner_epoch"


class OwnerStateValidationError(PluginStateError):
    http_status = 400
    code = "invalid_owner_state"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_copy(value: Any) -> Any:
    """Return a JSON-compatible deep copy or raise ``OwnerStateValidationError``."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    raise OwnerStateValidationError("owner state 必须是 JSON 兼容类型。")


def _reject_sensitive_keys(value: Any, *, where: str = "state") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                raise OwnerStateValidationError(
                    f"{where} 不允许包含秘密字段，拒绝写入/读取。"
                )
            _reject_sensitive_keys(item, where=where)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_keys(item, where=where)


def _int_field(value: Any, name: str, *, minimum: int = 0, maximum: int = 2**31 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OwnerStateValidationError(f"{name} 必须是非负整数。")
    if value < minimum or value > maximum:
        raise OwnerStateValidationError(f"{name} 超出允许范围。")
    return value


def _safe_owner(value: Any, *, allow_empty: bool = True) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise OwnerStateValidationError("owner 不能为空。")
    if len(text) > MAX_OWNER_LENGTH or _CONTROL_RE.search(text) or "/" in text or "\\" in text:
        raise OwnerStateValidationError("owner 标识不合法。")
    if ".." in text or not _SAFE_OWNER_RE.fullmatch(text):
        raise OwnerStateValidationError("owner 标识不合法。")
    return text


def _safe_dependency(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128 or _CONTROL_RE.search(text):
        raise OwnerStateValidationError("legacy 运行时依赖标识不合法。")
    if "/" in text or "\\" in text or ".." in text or "://" in text:
        raise OwnerStateValidationError("legacy 运行时依赖不能是路径或 URL。")
    if not _MODULE_RE.fullmatch(text):
        raise OwnerStateValidationError("legacy 运行时依赖标识不合法。")
    return text


def _validate_timestamp(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 64 or _CONTROL_RE.search(text):
        raise OwnerStateValidationError(f"{name} 时间戳不合法。")
    return text


@dataclass
class PluginOwnerState:
    """Validated, in-memory owner state."""

    schema_version: int = OWNER_STATE_SCHEMA_VERSION
    revision: int = 0
    phase: str = "legacy"
    owners: dict[str, str] = field(default_factory=lambda: {key: "" for key in OWNER_KEYS})
    owner_epochs: dict[str, int] = field(default_factory=lambda: {key: 0 for key in OWNER_KEYS})
    legacy_load_allowed: bool = True
    legacy_runtime_dependencies: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def copy(self) -> "PluginOwnerState":
        return PluginOwnerState.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "revision": int(self.revision),
            "phase": str(self.phase),
            "owners": {key: str(self.owners.get(key) or "") for key in OWNER_KEYS},
            "owner_epochs": {key: int(self.owner_epochs.get(key) or 0) for key in OWNER_KEYS},
            "legacy_load_allowed": bool(self.legacy_load_allowed),
            "legacy_runtime_dependencies": list(self.legacy_runtime_dependencies),
            "created_at": str(self.created_at),
            "updated_at": str(self.updated_at),
        }

    def public_dict(self) -> dict[str, Any]:
        """Public view for APIs/market status; contains no paths or secrets."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PluginOwnerState":
        state = validate_owner_state(raw)
        return state

    def owner_for(self, domain: str) -> str:
        if domain not in OWNER_KEYS:
            raise OwnerStateValidationError("未知的 owner 域。")
        return str(self.owners.get(domain) or "")

    def epoch_for(self, domain: str) -> int:
        if domain not in OWNER_KEYS:
            raise OwnerStateValidationError("未知的 owner 域。")
        return int(self.owner_epochs.get(domain) or 0)

    def is_final_ready(self) -> bool:
        return (
            self.phase == "final"
            and not self.legacy_load_allowed
            and not self.legacy_runtime_dependencies
            and all(self.owners.get(key) for key in OWNER_KEYS)
        )


def default_owner_state(*, clock: Callable[[], str] | None = None) -> PluginOwnerState:
    """Return a fresh legacy-generation state.

    ``legacy_load_allowed=True`` is the safe default for an instance that has
    not been migrated yet: it does not claim any new owner.  No final plugin is
    invented before a real migration commits.
    """

    now = (clock or _now)()
    return PluginOwnerState(
        schema_version=OWNER_STATE_SCHEMA_VERSION,
        revision=0,
        phase="legacy",
        owners={key: "" for key in OWNER_KEYS},
        owner_epochs={key: 0 for key in OWNER_KEYS},
        legacy_load_allowed=True,
        legacy_runtime_dependencies=[],
        created_at=now,
        updated_at=now,
    )


def validate_owner_state(raw: Mapping[str, Any]) -> PluginOwnerState:
    """Validate a schema-v2 owner state fail-closed."""

    if not isinstance(raw, Mapping):
        raise PluginStateCorruptError("owner state 根对象必须是 JSON object。")
    _reject_sensitive_keys(raw)
    unknown = {str(key) for key in raw.keys()} - _TOP_LEVEL_KEYS
    if unknown:
        raise PluginStateCorruptError("owner state 包含未知字段，已停止读取。")
    schema_version = raw.get("schema_version")
    if schema_version != OWNER_STATE_SCHEMA_VERSION:
        raise PluginStateCorruptError("owner state schema_version 不受支持，已停止读取。")
    revision = _int_field(raw.get("revision"), "revision")
    phase = str(raw.get("phase") or "").strip()
    if phase not in OWNER_PHASES:
        raise PluginStateCorruptError("owner state phase 不合法，已停止读取。")
    legacy_load_allowed = raw.get("legacy_load_allowed")
    if not isinstance(legacy_load_allowed, bool):
        raise PluginStateCorruptError("owner state legacy_load_allowed 必须是布尔值。")

    raw_owners = raw.get("owners")
    if not isinstance(raw_owners, Mapping):
        raise PluginStateCorruptError("owner state owners 必须是 JSON object。")
    extra_owners = {str(key) for key in raw_owners} - set(OWNER_KEYS)
    if extra_owners:
        raise PluginStateCorruptError("owner state 包含未知 owner 域，已停止读取。")
    owners = {key: _safe_owner(raw_owners.get(key, "")) for key in OWNER_KEYS}

    raw_epochs = raw.get("owner_epochs")
    if not isinstance(raw_epochs, Mapping):
        raise PluginStateCorruptError("owner state owner_epochs 必须是 JSON object。")
    extra_epochs = {str(key) for key in raw_epochs} - set(OWNER_KEYS)
    if extra_epochs:
        raise PluginStateCorruptError("owner state 包含未知 epoch 域，已停止读取。")
    epochs = {
        key: _int_field(raw_epochs.get(key, 0), f"owner_epochs.{key}")
        for key in OWNER_KEYS
    }

    raw_dependencies = raw.get("legacy_runtime_dependencies")
    if not isinstance(raw_dependencies, list):
        raise PluginStateCorruptError("owner state legacy_runtime_dependencies 必须是数组。")
    if len(raw_dependencies) > MAX_DEPENDENCIES:
        raise PluginStateCorruptError("owner state legacy_runtime_dependencies 数量超限。")
    dependencies: list[str] = []
    for item in raw_dependencies:
        dependency = _safe_dependency(item)
        if dependency not in dependencies:
            dependencies.append(dependency)

    # Duplicate ownership is normally refused.  Legacy package identifiers are
    # allowed to share a domain during transition (for example one old package
    # owning both reply and marker), but two *new* owners must never overlap.
    seen: dict[str, str] = {}
    for domain, owner in owners.items():
        if not owner or owner.startswith("legacy:"):
            continue
        if owner in seen:
            raise PluginStateCorruptError(
                "owner state 包含重复的新 owner，已停止读取。"
            )
        seen[owner] = domain

    state = PluginOwnerState(
        schema_version=OWNER_STATE_SCHEMA_VERSION,
        revision=revision,
        phase=phase,
        owners=owners,
        owner_epochs=epochs,
        legacy_load_allowed=legacy_load_allowed,
        legacy_runtime_dependencies=dependencies,
        created_at=_validate_timestamp(raw.get("created_at", ""), "created_at"),
        updated_at=_validate_timestamp(raw.get("updated_at", ""), "updated_at"),
    )
    if phase == "final":
        if state.legacy_load_allowed:
            raise PluginStateCorruptError("phase=final 时 legacy_load_allowed 必须为 false。")
        if state.legacy_runtime_dependencies:
            raise PluginStateCorruptError("phase=final 时 legacy 运行时依赖必须为空。")
        missing = [key for key in OWNER_KEYS if not state.owners.get(key)]
        if missing:
            raise PluginStateCorruptError(
                "phase=final 时七个 owner 必须全部已提交，已停止读取。"
            )
        if any(not state.owner_epochs.get(key) for key in OWNER_KEYS):
            raise PluginStateCorruptError("phase=final 时 owner epoch 必须全部大于 0。")
    return state


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _atomic_write_private_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    payload = json.dumps(_json_copy(dict(value)), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp = parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
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
            dir_fd = os.open(parent, os.O_RDONLY)
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


class _FileLock:
    """Small cross-process exclusive lock using stdlib primitives."""

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
        except OSError:
            self._handle.close()
            self._handle = None
            raise PluginStateError("无法获取 owner state 跨进程锁。")
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


class PluginStateStore:
    """Read/write one instance's durable owner state.

    All paths are confined to ``<instance_dir>/studio-plugins/``.  Constructing
    the store does not create or modify anything; the first successful write
    initializes the directory and file atomically.
    """

    def __init__(
        self,
        instance_dir: Path | str,
        *,
        state_dir_name: str = STATE_DIR_NAME,
        clock: Callable[[], str] | None = None,
        max_bytes: int = MAX_STATE_BYTES,
    ) -> None:
        if not isinstance(state_dir_name, str) or not _SAFE_COMPONENT_RE.fullmatch(state_dir_name):
            raise PluginStatePathError("owner state 目录名不合法。")
        instance = Path(instance_dir).expanduser()
        try:
            instance = instance.resolve(strict=True)
        except OSError as exc:
            raise PluginStatePathError("实例目录不存在，无法定位 owner state。") from exc
        if not instance.is_dir():
            raise PluginStatePathError("实例路径不是目录。")
        candidate = instance / state_dir_name
        if candidate.is_symlink():
            raise PluginStatePathError("owner state 目录不能是符号链接。")
        resolved = candidate.resolve(strict=False)
        if not _is_within(resolved, instance):
            raise PluginStatePathError("owner state 目录越界，已拒绝。")
        self.instance_dir = instance
        self.state_dir = resolved
        self.state_path = resolved / STATE_FILE_NAME
        self.lock_path = resolved / STATE_LOCK_NAME
        self.clock = clock or _now
        self.max_bytes = max(1024, int(max_bytes))
        self._thread_lock = threading.RLock()

    # -- path checks ---------------------------------------------------
    def _assert_state_path_safe(self) -> None:
        if self.state_path.is_symlink():
            raise PluginStatePathError("owner state 文件不能是符号链接。")
        if not _is_within(self.state_path, self.instance_dir):
            raise PluginStatePathError("owner state 文件路径越界，已拒绝。")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            self._assert_state_path_safe()
            with _FileLock(self.lock_path):
                self._assert_state_path_safe()
                yield

    # -- read ----------------------------------------------------------
    def exists(self) -> bool:
        return self.state_path.is_file() and not self.state_path.is_symlink()

    def _load_unlocked(self) -> tuple[PluginOwnerState, bool]:
        self._assert_state_path_safe()
        try:
            size = self.state_path.stat().st_size
        except FileNotFoundError:
            return default_owner_state(clock=self.clock), False
        except OSError as exc:
            raise PluginStateError("无法读取 owner state 文件。", detail=exc.__class__.__name__) from exc
        if size <= 0 or size > self.max_bytes:
            raise PluginStateCorruptError("owner state 文件大小异常，已停止读取。")
        try:
            raw_text = self.state_path.read_text(encoding="utf-8-sig")
            raw = json.loads(raw_text)
        except (OSError, UnicodeError, ValueError) as exc:
            raise PluginStateCorruptError(
                "owner state 文件损坏，已停止读取以保护现有数据。",
                detail=exc.__class__.__name__,
            ) from exc
        if not isinstance(raw, dict):
            raise PluginStateCorruptError("owner state 文件顶层必须是 JSON object。")
        return validate_owner_state(raw), True

    def load(self) -> PluginOwnerState:
        with self._locked():
            return self._load_unlocked()[0]

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            state, exists = self._load_unlocked()
        payload = state.public_dict()
        payload["exists"] = bool(exists)
        payload["store"] = f"{STATE_DIR_NAME}/{STATE_FILE_NAME}"
        return payload

    # -- write ---------------------------------------------------------
    def _write_unlocked(
        self,
        state: PluginOwnerState,
        *,
        expected_revision: int | None,
        actor: str = "",
    ) -> PluginOwnerState:
        current, exists = self._load_unlocked()
        if expected_revision is not None:
            expected = int(expected_revision)
            current_revision = int(current.revision) if exists else 0
            if expected != current_revision:
                raise PluginRevisionConflict(
                    "owner state revision 已变化，请重新读取后重试。"
                )
        now = self.clock()
        next_revision = (int(current.revision) if exists else 0) + 1
        updated = PluginOwnerState(
            schema_version=OWNER_STATE_SCHEMA_VERSION,
            revision=next_revision,
            phase=state.phase,
            owners={key: str(state.owners.get(key) or "") for key in OWNER_KEYS},
            owner_epochs={key: int(state.owner_epochs.get(key) or 0) for key in OWNER_KEYS},
            legacy_load_allowed=bool(state.legacy_load_allowed),
            legacy_runtime_dependencies=list(state.legacy_runtime_dependencies),
            created_at=state.created_at or current.created_at or now,
            updated_at=now,
        )
        # Validate before touching disk.  This also enforces the final gate.
        validated = validate_owner_state(updated.to_dict())
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.state_dir.is_symlink():
            raise PluginStatePathError("owner state 目录不能是符号链接。")
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        _atomic_write_private_json(self.state_path, validated.to_dict())
        return validated

    def update(
        self,
        mutator: Callable[[PluginOwnerState], PluginOwnerState | Mapping[str, Any]],
        *,
        expected_revision: int,
        actor: str = "",
    ) -> PluginOwnerState:
        """CAS update: apply ``mutator`` only when the on-disk revision matches."""

        with self._locked():
            current, exists = self._load_unlocked()
            current_revision = int(current.revision) if exists else 0
            if int(expected_revision) != current_revision:
                raise PluginRevisionConflict(
                    "owner state revision 已变化，请重新读取后重试。"
                )
            draft = current.copy()
            result = mutator(draft)
            if isinstance(result, Mapping):
                result = PluginOwnerState.from_dict(result)
            if not isinstance(result, PluginOwnerState):
                raise OwnerStateValidationError("owner state 更新函数返回类型不合法。")
            result.created_at = result.created_at or current.created_at
            return self._write_unlocked(
                result,
                expected_revision=expected_revision,
                actor=actor,
            )

    def ensure_initialized(self) -> PluginOwnerState:
        with self._locked():
            state, exists = self._load_unlocked()
            if exists:
                return state
            return self._write_unlocked(
                default_owner_state(clock=self.clock),
                expected_revision=0,
            )

    # -- owner/epoch operations ---------------------------------------
    def claim_owner(
        self,
        domain: str,
        owner: str,
        *,
        expected_revision: int,
        actor: str = "",
    ) -> PluginOwnerState:
        if domain not in OWNER_KEYS:
            raise OwnerStateValidationError("未知的 owner 域。")
        normalized = _safe_owner(owner)

        def mutate(state: PluginOwnerState) -> PluginOwnerState:
            for other_domain, other_owner in state.owners.items():
                if other_domain == domain or not other_owner:
                    continue
                if other_owner.startswith("legacy:"):
                    continue
                if normalized and normalized == other_owner:
                    raise DuplicateOwnerError(
                        "同一个新 owner 不能同时拥有两个域，已拒绝。"
                    )
            previous = str(state.owners.get(domain) or "")
            state.owners[domain] = normalized
            if previous != normalized:
                state.owner_epochs[domain] = max(1, int(state.owner_epochs.get(domain) or 0) + 1)
            state.updated_at = self.clock()
            return state

        return self.update(mutate, expected_revision=expected_revision, actor=actor)

    def apply_owner_plan(
        self,
        *,
        expected_revision: int,
        owners: Mapping[str, str],
        phase: str | None = None,
        legacy_load_allowed: bool | None = None,
        legacy_runtime_dependencies: list[str] | None = None,
        bump_all_epochs: bool = False,
        actor: str = "",
    ) -> PluginOwnerState:
        if not isinstance(owners, Mapping):
            raise OwnerStateValidationError("owners 必须是 JSON object。")
        unknown = {str(key) for key in owners} - set(OWNER_KEYS)
        if unknown:
            raise OwnerStateValidationError("owners 包含未知域。")
        normalized = {key: _safe_owner(owners.get(key, "")) for key in OWNER_KEYS}
        seen: dict[str, str] = {}
        for domain, owner in normalized.items():
            if not owner or owner.startswith("legacy:"):
                continue
            if owner in seen:
                raise DuplicateOwnerError("同一个新 owner 不能同时拥有两个域，已拒绝。")
            seen[owner] = domain
        if legacy_runtime_dependencies is not None:
            dependencies = []
            for item in legacy_runtime_dependencies:
                dependency = _safe_dependency(item)
                if dependency not in dependencies:
                    dependencies.append(dependency)
        else:
            dependencies = None

        def mutate(state: PluginOwnerState) -> PluginOwnerState:
            for domain in OWNER_KEYS:
                previous = str(state.owners.get(domain) or "")
                new_owner = normalized[domain]
                state.owners[domain] = new_owner
                if bump_all_epochs or previous != new_owner:
                    state.owner_epochs[domain] = max(
                        1, int(state.owner_epochs.get(domain) or 0) + 1
                    )
            if phase is not None:
                if phase not in OWNER_PHASES:
                    raise OwnerStateValidationError("phase 不合法。")
                state.phase = phase
            if legacy_load_allowed is not None:
                state.legacy_load_allowed = bool(legacy_load_allowed)
            if dependencies is not None:
                state.legacy_runtime_dependencies = list(dependencies)
            state.updated_at = self.clock()
            return state

        return self.update(mutate, expected_revision=expected_revision, actor=actor)

    def set_phase(
        self,
        phase: str,
        *,
        expected_revision: int,
        legacy_load_allowed: bool | None = None,
        legacy_runtime_dependencies: list[str] | None = None,
        actor: str = "",
    ) -> PluginOwnerState:
        if phase not in OWNER_PHASES:
            raise OwnerStateValidationError("phase 不合法。")

        def mutate(state: PluginOwnerState) -> PluginOwnerState:
            state.phase = phase
            if legacy_load_allowed is not None:
                state.legacy_load_allowed = bool(legacy_load_allowed)
            if legacy_runtime_dependencies is not None:
                state.legacy_runtime_dependencies = [
                    _safe_dependency(item) for item in legacy_runtime_dependencies
                ]
            state.updated_at = self.clock()
            if phase == "final":
                missing = [key for key in OWNER_KEYS if not state.owners.get(key)]
                if missing:
                    raise OwnerStateValidationError(
                        "v2 阶段不能把 phase 写成 final：owner 未全部提交。"
                    )
                if state.legacy_load_allowed:
                    raise OwnerStateValidationError(
                        "v2 阶段不能把 phase 写成 final：legacy_load_allowed 仍为 true。"
                    )
                if state.legacy_runtime_dependencies:
                    raise OwnerStateValidationError(
                        "v2 阶段不能把 phase 写成 final：仍存在 legacy 运行时依赖。"
                    )
                if any(not state.owner_epochs.get(key) for key in OWNER_KEYS):
                    for key in OWNER_KEYS:
                        state.owner_epochs[key] = max(1, int(state.owner_epochs.get(key) or 0))
            return state

        return self.update(mutate, expected_revision=expected_revision, actor=actor)

    def assert_epoch(self, domain: str, epoch: int) -> PluginOwnerState:
        """Reject stale *and* self-reported-newer client epochs.

        The client epoch is never trusted to advance state; it must exactly
        match the revision currently committed on disk.
        """

        if domain not in OWNER_KEYS:
            raise OwnerStateValidationError("未知的 owner 域。")
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise StaleOwnerEpochError("owner epoch 必须是服务端已提交的整数。")
        state = self.load()
        current = state.epoch_for(domain)
        if int(epoch) != current:
            raise StaleOwnerEpochError(
                "owner epoch 已过期或未被服务端提交，写请求已拒绝。"
            )
        return state


# Explicit aliases for consumers that prefer descriptive names.
OwnerState = PluginOwnerState
OwnerStateStore = PluginStateStore
RevisionConflict = PluginRevisionConflict
OwnerEpochStale = StaleOwnerEpochError

__all__ = [
    "OWNER_KEYS",
    "OWNER_PHASES",
    "OWNER_STATE_SCHEMA_VERSION",
    "STATE_DIR_NAME",
    "STATE_FILE_NAME",
    "DuplicateOwnerError",
    "OwnerEpochStale",
    "OwnerState",
    "OwnerStateStore",
    "OwnerStateValidationError",
    "PluginOwnerState",
    "PluginRevisionConflict",
    "PluginStateCorruptError",
    "PluginStateError",
    "PluginStatePathError",
    "PluginStateStore",
    "RevisionConflict",
    "StaleOwnerEpochError",
    "default_owner_state",
    "validate_owner_state",
]
