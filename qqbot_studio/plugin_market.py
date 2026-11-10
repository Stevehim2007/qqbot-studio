"""Secure plugin market, installation, grouping, and operation state.

The module deliberately uses only the Python standard library.  It never
executes archive content: plugins are unpacked as data into the owning AstrBot
instance's ``plugins`` directory.  Runtime dependencies are declarative; the
installer checks them and may run ``pip`` only after explicit user consent.

Security boundaries implemented here:

* manifests require an exact SHA-256 and an explicit archive reference;
* remote sources are HTTPS-only and must be present in a trusted-host list;
* ZIP files must contain exactly one top-level directory whose name equals the
  manifest plugin id;
* absolute paths, ``..`` components, backslashes, duplicate names, symlinks,
  special files, encrypted entries, and suspicious compression ratios are
  rejected before extraction;
* installs and updates are staged and atomically moved into place;
* only directories registered by this market can be deleted;
* the bundled ``qqbot_enhancer`` plugin is reserved and can never be replaced or
  deleted by market operations;
* configuration, ``plugin_data``, AstrBot databases/sessions, and all other
  files outside the registered code directory are untouched.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # POSIX file lock; Windows has a small msvcrt fallback below.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None  # type: ignore
try:  # pragma: no cover - exercised on Windows
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None  # type: ignore

from .store import chmod_best_effort, utc_now

PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GROUP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
logger = logging.getLogger(__name__)

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
RESERVED_PLUGIN_IDS = frozenset({"qqbot_enhancer"})
MARKET_SCHEMA_VERSION = 1
DEFAULT_MARKET_SOURCE_URL = "https://raw.githubusercontent.com/Stevehim2007/qqbot-plugins/main/market/catalog.json"
DEFAULT_MARKET_TRUSTED_HOSTS = (
    "raw.githubusercontent.com",
    "github.com",
    "objects.githubusercontent.com",
)
MANIFEST_SCHEMA_VERSION = 2
MANIFEST_V1_MAX_SCHEMA_VERSION = 1
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({1, 2})
GROUP_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 5000
MAX_COMPRESSION_RATIO = 200
MAX_IDEMPOTENCY_KEY_LENGTH = 160
MAX_GROUP_NAME_LENGTH = 80
MAX_BATCH_ACTIONS = 100
PREVIEW_TTL_SECONDS = 15 * 60
OPERATION_REQUEST_KEYS = frozenset(
    {
        "operation",
        "action",
        "plugin_id",
        "manifest",
        "archive_path",
        "archive",
        "download",
        "download_url",
        "url",
        "sha256",
        "version",
        "confirm",
        "preview_token",
        "idempotency_key",
        "source",
        "entry_id",
    }
)

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|secret|credential|token)"
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{6,}|sk-[A-Za-z0-9_-]{6,}|"
    r"(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;}\]]+)"
)
_URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/@\s]+@")


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class PluginMarketError(RuntimeError):
    """Base class for a safe, user-facing plugin market error."""

    http_status = 400
    code = "plugin_market_error"

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(str(message or "插件市场操作失败。"))
        self.message = str(message or "插件市场操作失败。")
        self.detail = sanitize_error_text(detail) if detail else ""

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


class ManifestError(PluginMarketError):
    code = "invalid_manifest"


class ArchiveValidationError(PluginMarketError):
    code = "invalid_archive"


class UnsafePathError(ArchiveValidationError):
    code = "unsafe_path"


class PluginConflictError(PluginMarketError):
    http_status = 409
    code = "plugin_conflict"


class PluginNotFoundError(PluginMarketError):
    http_status = 404
    code = "plugin_not_found"


class PluginStateError(PluginMarketError):
    http_status = 503
    code = "plugin_state_unavailable"


class PluginDependencyError(PluginMarketError):
    """A required dependency is missing/disabled/not ready/incompatible."""

    http_status = 409
    code = "dependency_unavailable"

    def __init__(
        self,
        message: str,
        *,
        graph: Mapping[str, Any] | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message, detail=detail)
        self.graph = dict(graph or {})

    def to_public_dict(self) -> dict[str, Any]:
        payload = super().to_public_dict()
        if self.graph:
            payload["dependency_graph"] = {
                key: self.graph.get(key)
                for key in (
                    "plugin_id",
                    "status",
                    "missing_required",
                    "missing_optional",
                    "disabled_required",
                    "not_ready_required",
                    "incompatible",
                    "cycles",
                    "conflicting_installed",
                    "install_plan",
                )
            }
        return payload


class GroupError(PluginMarketError):
    code = "invalid_group_action"


class GroupNotFoundError(GroupError):
    http_status = 404
    code = "group_not_found"


class ConfirmationRequiredError(PluginMarketError):
    code = "confirmation_required"


class IdempotencyConflictError(PluginMarketError):
    http_status = 409
    code = "idempotency_conflict"


class PreviewExpiredError(PluginMarketError):
    code = "preview_expired"


class PluginOperationBusy(PluginMarketError):
    http_status = 409
    code = "operation_in_progress"


class PluginOperationFailed(PluginMarketError):
    http_status = 500
    code = "plugin_operation_failed"

    def __init__(
        self,
        message: str,
        *,
        operation: Mapping[str, Any] | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message, detail=detail)
        self.operation = dict(operation or {})

    def to_public_dict(self) -> dict[str, Any]:
        payload = super().to_public_dict()
        if self.operation:
            payload["operation"] = dict(self.operation)
        return payload


# Compatibility aliases for callers that prefer shorter names.
MarketError = PluginMarketError
GroupStore = None  # assigned after PluginGroupStore is defined


# ---------------------------------------------------------------------------
# Generic safety helpers
# ---------------------------------------------------------------------------


def sanitize_error_text(value: Any, *, limit: int = 500) -> str:
    """Return a compact error string with common credential shapes removed."""

    text = str(value or "")
    text = CONTROL_RE.sub(" ", text)
    text = _SENSITIVE_TEXT_RE.sub("[已脱敏]", text)
    text = _URL_USERINFO_RE.sub(r"\1[已脱敏]@", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max(40, int(limit)):
        text = text[: max(40, int(limit)) - 1].rstrip() + "…"
    return text


def _is_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key or "")))


def _iter_redacted(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _iter_redacted(item, str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_iter_redacted(item, parent_key) for item in value]
    if parent_key and _is_sensitive_key(parent_key):
        return "[已脱敏]"
    return value


def _redact_url_for_display(value: Any) -> str:
    text = str(value or "")
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return "[已脱敏]"
    if not parsed.scheme:
        return text
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    if parsed.scheme.lower() == "https":
        displayed = urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        return displayed
    # Local/opaque schemes are only useful internally.
    return "[已脱敏]"


def _public_catalog_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    public = _iter_redacted(dict(record))
    if "download" in public:
        public["download"] = _redact_url_for_display(public.get("download"))
    archive = public.get("archive")
    if isinstance(archive, Mapping):
        public["archive"] = {"kind": str(archive.get("kind") or "")}
    manifest = public.get("manifest")
    if isinstance(manifest, Mapping):
        public["manifest"] = _public_catalog_entry(manifest)
    return public


def _deepcopy_json(value: Any) -> Any:
    """Deep-copy only JSON-compatible data; reject tuples/sets/custom objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _deepcopy_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deepcopy_json(item) for item in value]
    raise PluginMarketError("数据必须是 JSON 兼容类型。", detail=type(value).__name__)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return utc_now()


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    chmod_best_effort(path.parent, 0o700)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        chmod_best_effort(path, mode)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _read_json_object(path: Path, *, max_bytes: int = MAX_MANIFEST_BYTES) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink():
        raise PluginStateError("拒绝读取符号链接形式的插件状态文件。")
    try:
        if path.stat().st_size > max_bytes:
            raise PluginStateError("插件状态或清单文件过大，已停止读取。")
        raw = path.read_text(encoding="utf-8-sig")
    except PluginMarketError:
        raise
    except OSError as exc:
        raise PluginStateError("无法读取插件状态文件。", detail=exc.__class__.__name__) from exc
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise PluginStateError(
            "插件状态或清单文件损坏，已停止操作以保护现有数据。",
            detail=exc.__class__.__name__,
        ) from exc
    if not isinstance(value, dict):
        raise PluginStateError("插件状态或清单文件的顶层必须是 JSON object。")
    return value


def _safe_relative_parts(name: str) -> tuple[str, ...]:
    """Validate one ZIP member name and return its POSIX components."""

    value = str(name or "")
    if not value or value in {".", "./"}:
        raise UnsafePathError("ZIP 中存在空路径。")
    if "\x00" in value or CONTROL_RE.search(value):
        raise UnsafePathError("ZIP 路径包含控制字符。")
    if "\\" in value:
        raise UnsafePathError("ZIP 路径包含反斜杠，拒绝跨平台不安全路径。")
    if value.startswith("/") or value.startswith("~"):
        raise UnsafePathError("ZIP 路径不能是绝对路径。")
    if re.match(r"^[A-Za-z]:", value):
        raise UnsafePathError("ZIP 路径不能包含 Windows 盘符。")
    if value.endswith("/"):
        value = value.rstrip("/")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise UnsafePathError("ZIP 路径包含空段、当前目录或路径穿越。")
    for part in parts:
        if len(part) > 200 or part.endswith(" ") or part.endswith("."):
            raise UnsafePathError("ZIP 路径包含不安全文件名。")
        if ":" in part:
            raise UnsafePathError("ZIP 路径包含冒号，拒绝跨平台不安全文件名。")
    return tuple(parts)


def _has_parent_path_segment(value: Any) -> bool:
    text = urllib.parse.unquote(str(value or "")).replace("\\", "/")
    return ".." in text.split("/")


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _existing_dir_no_symlink(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode) and not path.is_symlink()
    except OSError:
        return False


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(max(4096, int(chunk_size)))
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise PluginMarketError("无法读取插件安装包。", detail=exc.__class__.__name__) from exc
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> str:
    expected_clean = str(expected or "").strip().lower()
    if expected_clean.startswith("sha256:"):
        expected_clean = expected_clean.split(":", 1)[1].strip()
    if not SHA256_RE.fullmatch(expected_clean):
        raise ManifestError("manifest 的 sha256 必须是 64 位十六进制字符串。")
    actual = sha256_file(Path(path))
    if not secrets.compare_digest(actual, expected_clean):
        raise PluginConflictError("插件安装包 SHA256 校验失败，已拒绝安装。")
    return actual


# ---------------------------------------------------------------------------
# Manifest and ZIP validation
# ---------------------------------------------------------------------------


def _normalise_sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    if not SHA256_RE.fullmatch(text):
        raise ManifestError("manifest 必须提供合法的 64 位 sha256。")
    return text


def _normalise_text(value: Any, field: str, *, max_len: int = 2000, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ManifestError(f"manifest 缺少 {field}。")
    if len(text) > max_len or CONTROL_RE.search(text):
        raise ManifestError(f"manifest 的 {field} 不合法。")
    return text


def _normalise_plugin_id(value: Any) -> str:
    plugin_id = str(value or "").strip()
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise ManifestError("插件 ID 必须为 1–128 位字母、数字、点、下划线或短横线，且以字母或数字开头。")
    return plugin_id


def _normalise_version(value: Any) -> str:
    version = _normalise_text(value, "version", max_len=80, required=True)
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+!()-]{0,79}", version):
        raise ManifestError("manifest 的 version 格式不合法。")
    return version


def _normalise_url_text(value: Any, field: str) -> str:
    text = _normalise_text(value, field, max_len=4096, required=True)
    if any(ch.isspace() for ch in text):
        raise ManifestError(f"manifest 的 {field} 不能包含空白。")
    return text


def _normalise_string_list(value: Any, field: str, *, max_items: int = 100) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ManifestError(f"manifest 的 {field} 必须是数组。")
    result: list[str] = []
    for item in value:
        text = _normalise_text(item, field, max_len=200)
        if text and text not in result:
            result.append(text)
    if len(result) > max_items:
        raise ManifestError(f"manifest 的 {field} 条目过多。")
    return result


_RUNTIME_PY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,80}$")
_RUNTIME_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,80}$")
_RUNTIME_SPEC_RE = re.compile(r"^[<>=!~0-9A-Za-z.*+_ -]{0,80}$")
_RUNTIME_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:/@()-]{0,120}$")


def _normalise_runtime_requirements(value: Any) -> dict[str, Any]:
    """Normalise optional runtime dependency declarations.

    This never executes anything.  It only freezes the declarations that the
    installer may show to the user and, after explicit consent, install.
    """
    if value in (None, ""):
        return {"python": [], "system": []}
    if not isinstance(value, Mapping):
        raise ManifestError("manifest 的 runtime_requirements 必须是 JSON object。")
    result: dict[str, Any] = {"python": [], "system": []}
    python_items = value.get("python") or []
    system_items = value.get("system") or []
    if not isinstance(python_items, (list, tuple)) or not isinstance(system_items, (list, tuple)):
        raise ManifestError("runtime_requirements.python/system 必须是数组。")
    if len(python_items) > 20 or len(system_items) > 20:
        raise ManifestError("runtime_requirements 条目过多。")
    seen: set[str] = set()
    for item in python_items:
        if not isinstance(item, Mapping):
            raise ManifestError("runtime_requirements.python 条目必须是 object。")
        name = _normalise_text(item.get("name"), "runtime_requirements.python.name", max_len=81, required=True)
        if not _RUNTIME_PY_NAME_RE.fullmatch(name):
            raise ManifestError("runtime_requirements.python.name 不合法。")
        if name.lower() in seen:
            raise ManifestError("runtime_requirements.python 包含重复依赖。")
        seen.add(name.lower())
        spec = str(item.get("spec") or "").strip()
        if spec and not _RUNTIME_SPEC_RE.fullmatch(spec):
            raise ManifestError("runtime_requirements.python.spec 不合法。")
        module = str(item.get("module") or name.replace("-", "_")).strip()
        if not _RUNTIME_MODULE_RE.fullmatch(module):
            raise ManifestError("runtime_requirements.python.module 不合法。")
        result["python"].append({
            "name": name,
            "spec": spec,
            "module": module,
            "description": _normalise_text(item.get("description") or "", "runtime_requirements.python.description", max_len=300),
        })
    for item in system_items:
        if not isinstance(item, Mapping):
            raise ManifestError("runtime_requirements.system 条目必须是 object。")
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in {"font", "command", "package"}:
            raise ManifestError("runtime_requirements.system.kind 只支持 font/command/package。")
        target = _normalise_text(item.get("target"), "runtime_requirements.system.target", max_len=121, required=True)
        if not _RUNTIME_TARGET_RE.fullmatch(target):
            raise ManifestError("runtime_requirements.system.target 不合法。")
        if kind == "command" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+/-]{0,120}", target):
            raise ManifestError("runtime_requirements.system.command 只能是单个命令名。")
        result["system"].append({
            "kind": kind,
            "target": target,
            "description": _normalise_text(item.get("description") or "", "runtime_requirements.system.description", max_len=300),
        })
    return result


_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


def _normalise_schema_version(value: Any, *, max_schema_version: int = MANIFEST_SCHEMA_VERSION) -> int:
    if value is None or value == "":
        version = 1
    elif isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError("manifest 的 schema_version 必须是正整数。")
    else:
        version = int(value)
    if version < 1:
        raise ManifestError("manifest 的 schema_version 必须是正整数。")
    if version > int(max_schema_version):
        raise ManifestError(
            "manifest schema_version 高于当前客户端支持的版本，已拒绝而不是忽略新增字段。"
        )
    if version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        raise ManifestError("manifest schema_version 不受支持，已拒绝。")
    return version


def _normalise_abi_version(value: Any, field: str = "abi_version") -> int | str:
    if isinstance(value, bool) or value is None or value == "":
        raise ManifestError(f"manifest 的 {field} 必须提供。")
    if isinstance(value, int):
        if value < 1:
            raise ManifestError(f"manifest 的 {field} 必须是正整数。")
        return value
    text = _normalise_text(value, field, max_len=64, required=True)
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}", text):
        raise ManifestError(f"manifest 的 {field} 格式不合法。")
    return text


def _normalise_capability(value: Any, field: str = "capability") -> str:
    text = _normalise_text(value, field, max_len=128, required=True)
    if not _CAPABILITY_RE.fullmatch(text):
        raise ManifestError(f"manifest 的 {field} 格式不合法。")
    return text


def _normalise_provides(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ManifestError("manifest 的 provides 必须是数组。")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ManifestError("manifest 的 provides 条目必须是 JSON object。")
        capability = _normalise_capability(item.get("capability"))
        if capability in seen:
            raise ManifestError("manifest 的 provides 包含重复 capability。")
        seen.add(capability)
        abi_version = _normalise_abi_version(item.get("abi_version"), "provides.abi_version")
        result.append({"capability": capability, "abi_version": abi_version})
    if len(result) > 100:
        raise ManifestError("manifest 的 provides 条目过多。")
    return tuple(result)


def _normalise_version_range(value: Any) -> str:
    text = str(value or "*").strip()
    if not text:
        return "*"
    if len(text) > 120 or CONTROL_RE.search(text):
        raise ManifestError("manifest 的 version_range 不合法。")
    if not re.fullmatch(r"[<>=!~^0-9A-Za-z.*+_| ,-]+", text):
        raise ManifestError("manifest 的 version_range 只支持固定比较表达式。")
    return text


def _normalise_requires(value: Any, *, schema_version: int) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ManifestError("manifest 的 requires 必须是数组。")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            item = {"plugin_id": item}
        if not isinstance(item, Mapping):
            raise ManifestError("manifest 的 requires 条目必须是对象或插件 ID。")
        plugin_id = _normalise_plugin_id(item.get("plugin_id") or item.get("id"))
        if plugin_id in seen:
            raise ManifestError("manifest 的 requires 包含重复 plugin_id。")
        seen.add(plugin_id)
        version_range = _normalise_version_range(item.get("version_range") or item.get("version"))
        capability = ""
        if item.get("capability") not in {None, ""}:
            capability = _normalise_capability(item.get("capability"))
        abi_version = None
        if item.get("abi_version") not in {None, ""}:
            abi_version = _normalise_abi_version(item.get("abi_version"), "requires.abi_version")
        optional = item.get("optional", False)
        if not isinstance(optional, bool):
            raise ManifestError("manifest 的 requires.optional 必须是布尔值。")
        if capability and abi_version is None:
            raise ManifestError("声明 requires.capability 时必须同时声明 abi_version。")
        result.append(
            {
                "plugin_id": plugin_id,
                "version_range": version_range,
                "capability": capability,
                "abi_version": abi_version,
                "optional": optional,
            }
        )
    if len(result) > 100:
        raise ManifestError("manifest 的 requires 条目过多。")
    del schema_version
    return tuple(result)


def _normalise_conflicts(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ManifestError("manifest 的 conflicts 必须是数组。")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            item = {"plugin_id": item}
        if not isinstance(item, Mapping):
            raise ManifestError("manifest 的 conflicts 条目必须是对象或插件 ID。")
        plugin_id = _normalise_plugin_id(item.get("plugin_id") or item.get("id"))
        if plugin_id in seen:
            raise ManifestError("manifest 的 conflicts 包含重复 plugin_id。")
        seen.add(plugin_id)
        result.append(
            {
                "plugin_id": plugin_id,
                "version_range": _normalise_version_range(item.get("version_range") or "*"),
            }
        )
    if len(result) > 100:
        raise ManifestError("manifest 的 conflicts 条目过多。")
    return tuple(result)


def _normalise_schema_number(value: Any, field: str) -> int:
    if value is None or value == "":
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManifestError(f"manifest 的 {field} 必须是正整数。")
    return int(value)


def _version_parts(value: Any) -> tuple[Any, ...]:
    parts: list[Any] = []
    for item in re.split(r"[.+-]", str(value or "").strip().lower().lstrip("v")):
        if item.isdigit():
            parts.append((0, int(item)))
        elif item:
            parts.append((1, item))
    return tuple(parts)


def _version_compare(left: Any, right: Any) -> int:
    a = _version_parts(left)
    b = _version_parts(right)
    length = max(len(a), len(b))
    for index in range(length):
        av = a[index] if index < len(a) else (0, 0)
        bv = b[index] if index < len(b) else (0, 0)
        if av < bv:
            return -1
        if av > bv:
            return 1
    return 0


def version_satisfies(version: Any, version_range: Any) -> bool:
    """Return whether a plugin version satisfies a bounded semver-ish range.

    Supported syntax: ``*``/empty, exact ``1.2.3``, ``== >= <= > < !=``,
    comma/space separated conjunctions, ``^1.2.3`` and ``~1.2.3``.  No
    packaging dependency and no arbitrary code/expression evaluation.
    """

    current = str(version or "").strip()
    spec = str(version_range or "*").strip()
    if not spec or spec == "*":
        return True
    if not current:
        return False
    # Normalize commas to spaces, then split comparator clauses.  A bare
    # version is an equality clause.
    clauses = [part for part in re.split(r"[,\s]+", spec.replace("||", " ")) if part]
    for clause in clauses:
        if clause == "*":
            continue
        match = re.fullmatch(r"(>=|<=|==|!=|>|<|=|\^|~)?(.+)", clause)
        if not match:
            return False
        operator = match.group(1) or "="
        target = match.group(2)
        if operator == "^":
            if _version_compare(current, target) < 0:
                return False
            target_parts = [item for item in re.split(r"[.+-]", target.lstrip("v")) if item.isdigit()]
            major = int(target_parts[0]) if target_parts else 0
            if major > 0:
                upper = f"{major + 1}.0.0"
            else:
                minor = int(target_parts[1]) if len(target_parts) > 1 else 0
                upper = f"0.{minor + 1}.0"
            if _version_compare(current, upper) >= 0:
                return False
            continue
        if operator == "~":
            if _version_compare(current, target) < 0:
                return False
            target_parts = [item for item in re.split(r"[.+-]", target.lstrip("v")) if item.isdigit()]
            major = int(target_parts[0]) if target_parts else 0
            minor = int(target_parts[1]) if len(target_parts) > 1 else 0
            upper = f"{major}.{minor + 1}.0"
            if _version_compare(current, upper) >= 0:
                return False
            continue
        comparison = _version_compare(current, target)
        if operator in {"=", "=="} and comparison != 0:
            return False
        if operator == "!=" and comparison == 0:
            return False
        if operator == ">" and comparison <= 0:
            return False
        if operator == ">=" and comparison < 0:
            return False
        if operator == "<" and comparison >= 0:
            return False
        if operator == "<=" and comparison > 0:
            return False
    return True


def _manifest_requirements(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("requires")
    if isinstance(raw, (list, tuple)) and raw:
        result = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            result.append(
                {
                    "plugin_id": str(item.get("plugin_id") or ""),
                    "version_range": str(item.get("version_range") or "*"),
                    "capability": str(item.get("capability") or ""),
                    "abi_version": item.get("abi_version"),
                    "optional": bool(item.get("optional")),
                }
            )
        return result
    # Transitional v1 compatibility: dependencies are required plugin ids with
    # no capability/version constraint.
    result = []
    for item in manifest.get("dependencies") or []:
        plugin_id = str(item or "").strip()
        if plugin_id and PLUGIN_ID_RE.fullmatch(plugin_id):
            result.append(
                {
                    "plugin_id": plugin_id,
                    "version_range": "*",
                    "capability": "",
                    "abi_version": None,
                    "optional": False,
                }
            )
    return result


def _manifest_provides(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("provides")
    if isinstance(raw, (list, tuple)):
        return [
            {
                "capability": str(item.get("capability") or ""),
                "abi_version": item.get("abi_version"),
            }
            for item in raw
            if isinstance(item, Mapping)
        ]
    return []


def _manifest_version(manifest: Mapping[str, Any]) -> str:
    record_manifest = manifest.get("manifest")
    if isinstance(record_manifest, Mapping):
        return str(record_manifest.get("version") or manifest.get("version") or "")
    return str(manifest.get("version") or "")


def _manifest_for_record(record: Mapping[str, Any]) -> dict[str, Any]:
    nested = record.get("manifest")
    if isinstance(nested, Mapping):
        merged = dict(nested)
        for key in ("id", "plugin_id", "version", "provides", "requires", "conflicts", "dependencies", "runtime_requirements"):
            if key not in merged and key in record:
                merged[key] = record[key]
        return merged
    return dict(record)


def _state_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled", "ready"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def validate_dependency_graph(
    target: str | Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    installed: Iterable[str] | Mapping[str, Any] | None = None,
    enabled: Iterable[str] | Mapping[str, bool] | None = None,
    ready: Iterable[str] | Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Validate a plugin's v2 dependency graph.

    Required dependencies must be installed, enabled and ready.  Optional
    dependencies that are missing/unavailable are reported but do not block.
    Version ranges, capability/ABI declarations, conflicts and cycles are all
    validated.  The returned ``install_plan`` is a dependency-first topological
    order and never includes optional dependencies.
    """

    if isinstance(target, str):
        target_id = _normalise_plugin_id(target)
        target_manifest = dict(catalog.get(target_id) or {})
        if not target_manifest:
            raise ManifestError("依赖校验目标不在 catalog 中。")
    elif isinstance(target, Mapping):
        target_manifest = dict(target)
        target_id = _normalise_plugin_id(
            target_manifest.get("plugin_id") or target_manifest.get("id")
        )
    else:
        raise ManifestError("依赖校验目标不合法。")

    catalog_map: dict[str, dict[str, Any]] = {}
    for plugin_id, record in catalog.items():
        try:
            normalized_id = _normalise_plugin_id(plugin_id)
        except ManifestError:
            continue
        if isinstance(record, Mapping):
            catalog_map[normalized_id] = _manifest_for_record(record)
    catalog_map[target_id] = target_manifest

    installed_map: dict[str, dict[str, Any]] = {}
    installed_ids: set[str] = set()
    if isinstance(installed, Mapping):
        for plugin_id, record in installed.items():
            try:
                normalized_id = _normalise_plugin_id(plugin_id)
            except ManifestError:
                continue
            installed_ids.add(normalized_id)
            installed_map[normalized_id] = _manifest_for_record(record) if isinstance(record, Mapping) else {}
    elif installed is not None:
        installed_ids = {
            str(item) for item in installed if PLUGIN_ID_RE.fullmatch(str(item or ""))
        }

    enabled_map: dict[str, bool] = {}
    if isinstance(enabled, Mapping):
        enabled_map = {str(key): _state_bool(value) for key, value in enabled.items()}
    elif enabled is not None:
        enabled_map = {str(item): True for item in enabled}

    ready_map: dict[str, bool] = {}
    if isinstance(ready, Mapping):
        ready_map = {str(key): _state_bool(value) for key, value in ready.items()}
    elif ready is not None:
        ready_map = {str(item): True for item in ready}

    missing_required: list[dict[str, Any]] = []
    missing_optional: list[dict[str, Any]] = []
    disabled_required: list[dict[str, Any]] = []
    not_ready_required: list[dict[str, Any]] = []
    incompatible: list[dict[str, Any]] = []
    cycles: list[list[str]] = []
    conflicting_installed: list[dict[str, Any]] = []
    required_edges: dict[str, list[str]] = {}
    visited: set[str] = set()
    visiting: list[str] = []

    def record_requirements(plugin_id: str, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
        return _manifest_requirements(manifest)

    def visit(plugin_id: str, manifest: Mapping[str, Any], *, is_root: bool = False) -> None:
        if plugin_id in visiting:
            cycle = visiting[visiting.index(plugin_id) :] + [plugin_id]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        if plugin_id in visited:
            return
        visiting.append(plugin_id)
        dependencies = []
        for requirement in record_requirements(plugin_id, manifest):
            dep_id = requirement["plugin_id"]
            if not dep_id:
                incompatible.append({"plugin_id": plugin_id, "reason": "empty_requirement"})
                continue
            dependencies.append(dep_id)
            dep_manifest = catalog_map.get(dep_id)
            if dep_manifest is None and dep_id in installed_map:
                dep_manifest = installed_map[dep_id]
            if dep_manifest is None:
                entry = {
                    "plugin_id": dep_id,
                    "required_by": plugin_id,
                    "version_range": requirement["version_range"],
                    "available": False,
                }
                if requirement["optional"]:
                    missing_optional.append(entry)
                else:
                    missing_required.append(entry)
                continue
            dep_installed = dep_id in installed_ids or dep_id == target_id and False
            if not dep_installed:
                entry = {
                    "plugin_id": dep_id,
                    "required_by": plugin_id,
                    "version_range": requirement["version_range"],
                    "available": True,
                }
                if requirement["optional"]:
                    missing_optional.append(entry)
                else:
                    missing_required.append(entry)
                # Still traverse catalog metadata to detect cycles/plan order.
                visit(dep_id, dep_manifest)
                continue
            if not enabled_map.get(dep_id, False):
                entry = {
                    "plugin_id": dep_id,
                    "required_by": plugin_id,
                    "version_range": requirement["version_range"],
                }
                if requirement["optional"]:
                    missing_optional.append({**entry, "reason": "disabled"})
                else:
                    disabled_required.append(entry)
            if not ready_map.get(dep_id, False):
                entry = {
                    "plugin_id": dep_id,
                    "required_by": plugin_id,
                    "version_range": requirement["version_range"],
                }
                if requirement["optional"]:
                    missing_optional.append({**entry, "reason": "not_ready"})
                else:
                    not_ready_required.append(entry)
            installed_version = _manifest_version(installed_map.get(dep_id) or {}) or str(
                dep_manifest.get("version") or ""
            )
            if not version_satisfies(installed_version, requirement["version_range"]):
                incompatible.append(
                    {
                        "plugin_id": dep_id,
                        "required_by": plugin_id,
                        "reason": "version_mismatch",
                        "installed_version": installed_version,
                        "version_range": requirement["version_range"],
                    }
                )
            provided_capabilities = _manifest_provides(dep_manifest)
            if requirement["capability"]:
                matched = [
                    item
                    for item in provided_capabilities
                    if item.get("capability") == requirement["capability"]
                ]
                if not matched:
                    incompatible.append(
                        {
                            "plugin_id": dep_id,
                            "required_by": plugin_id,
                            "reason": "capability_missing",
                            "capability": requirement["capability"],
                        }
                    )
                elif requirement["abi_version"] not in {None, ""}:
                    if not any(
                        str(item.get("abi_version")) == str(requirement["abi_version"])
                        for item in matched
                    ):
                        incompatible.append(
                            {
                                "plugin_id": dep_id,
                                "required_by": plugin_id,
                                "reason": "abi_mismatch",
                                "capability": requirement["capability"],
                                "required_abi": requirement["abi_version"],
                                "provided_abi": [item.get("abi_version") for item in matched],
                            }
                        )
            visit(dep_id, dep_manifest)
        required_edges[plugin_id] = dependencies
        visiting.pop()
        visited.add(plugin_id)

    visit(target_id, target_manifest, is_root=True)

    for conflict in _normalise_conflicts(target_manifest.get("conflicts")):
        dep_id = conflict["plugin_id"]
        if dep_id in installed_ids:
            installed_version = _manifest_version(installed_map.get(dep_id) or {})
            if version_satisfies(installed_version, conflict["version_range"]):
                conflicting_installed.append(
                    {
                        "plugin_id": dep_id,
                        "version_range": conflict["version_range"],
                        "installed_version": installed_version,
                    }
                )

    # Topological install plan for required dependencies that are not yet
    # installed.  DFS post-order gives dependencies before consumers.
    plan: list[dict[str, Any]] = []
    planned: set[str] = set()

    def plan_visit(plugin_id: str, manifest: Mapping[str, Any]) -> None:
        if plugin_id in planned:
            return
        planned.add(plugin_id)
        for requirement in _manifest_requirements(manifest):
            if requirement["optional"]:
                continue
            dep_id = requirement["plugin_id"]
            dep_manifest = catalog_map.get(dep_id)
            if dep_manifest is None:
                continue
            plan_visit(dep_id, dep_manifest)
        already_installed = plugin_id in installed_ids
        if not already_installed:
            plan.append(
                {
                    "plugin_id": plugin_id,
                    "version": _manifest_version(manifest),
                    "optional": False,
                    "action": "install",
                }
            )

    plan_visit(target_id, target_manifest)

    ok = not (
        missing_required
        or disabled_required
        or not_ready_required
        or incompatible
        or cycles
        or conflicting_installed
    )
    result = {
        "ok": bool(ok),
        "ready": bool(ok),
        "status": "ready" if ok else "unavailable",
        "plugin_id": target_id,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "required": [
            item["plugin_id"] for item in _manifest_requirements(target_manifest)
        ],
        "optional": [
            item["plugin_id"] for item in _manifest_requirements(target_manifest) if item["optional"]
        ],
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "disabled_required": disabled_required,
        "not_ready_required": not_ready_required,
        "incompatible": incompatible,
        "cycles": cycles,
        "conflicting_installed": conflicting_installed,
        "install_plan": plan,
        "error": "",
    }
    return result


def plan_topological_install(
    target: str | Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    installed: Iterable[str] | Mapping[str, Any] | None = None,
    enabled: Iterable[str] | Mapping[str, bool] | None = None,
    ready: Iterable[str] | Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    graph = validate_dependency_graph(
        target,
        catalog,
        installed=installed,
        enabled=enabled,
        ready=ready,
    )
    return {
        "ok": graph["ok"],
        "ready": graph["ready"],
        "status": graph["status"],
        "plugin_id": graph["plugin_id"],
        "install_plan": graph["install_plan"],
        "missing_required": graph["missing_required"],
        "incompatible": graph["incompatible"],
        "cycles": graph["cycles"],
        "error": graph["error"],
    }


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    sha256: str
    download: str
    root: str
    schema_version: int = 1
    description: str = ""
    author: str = ""
    homepage: str = ""
    astrbot: str = ""
    dependencies: tuple[str, ...] = ()
    provides: tuple[dict[str, Any], ...] = ()
    requires: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    config_schema_version: int = 1
    data_schema_version: int = 1
    recommended_groups: tuple[str, ...] = ()
    runtime_requirements: dict[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] | None = None

    @property
    def id(self) -> str:
        return self.plugin_id

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": int(self.schema_version),
            "id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
            "download": self.download,
            "root": self.root,
            "description": self.description,
            "author": self.author,
            "homepage": self.homepage,
            "astrbot": self.astrbot,
            # Transitional key: v1 consumers still read dependencies.  v2
            # consumers use requires/provides and must reject schema_version>2.
            "dependencies": list(self.dependencies),
            "provides": [dict(item) for item in self.provides],
            "requires": [dict(item) for item in self.requires],
            "conflicts": [dict(item) for item in self.conflicts],
            "config_schema_version": int(self.config_schema_version),
            "data_schema_version": int(self.data_schema_version),
            "recommended_groups": list(self.recommended_groups),
            "runtime_requirements": _deepcopy_json(dict(self.runtime_requirements)),
        }
        if self.extra:
            payload["extra"] = _deepcopy_json(dict(self.extra))
        return payload


def _manifest_from_dict(
    raw: Mapping[str, Any],
    *,
    max_schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> PluginManifest:
    if not isinstance(raw, Mapping):
        raise ManifestError("manifest 必须为 JSON object。")
    schema_version = _normalise_schema_version(
        raw.get("schema_version"),
        max_schema_version=max_schema_version,
    )
    plugin_id = _normalise_plugin_id(raw.get("id") or raw.get("plugin_id"))
    name = _normalise_text(raw.get("name") or raw.get("display_name") or plugin_id, "name", max_len=160)
    version = _normalise_version(raw.get("version"))
    sha256 = _normalise_sha256(raw.get("sha256") or raw.get("checksum"))
    download = (
        raw.get("download")
        or raw.get("download_url")
        or raw.get("archive")
        or raw.get("archive_url")
        or raw.get("url")
        or raw.get("file")
    )
    download = _normalise_url_text(download, "download")
    parsed_download = urllib.parse.urlsplit(download)
    if parsed_download.scheme:
        scheme = parsed_download.scheme.lower()
        if scheme not in {"https", "file"}:
            raise ManifestError("manifest 的 download 只允许相对路径、file URL 或受信 HTTPS URL。")
        if scheme == "https":
            if not parsed_download.hostname:
                raise ManifestError("manifest 的 HTTPS download 缺少主机名。")
            if parsed_download.username or parsed_download.password:
                raise ManifestError("manifest 的 download 不能包含用户名或密码。")
        elif parsed_download.netloc not in {"", "localhost"}:
            raise ManifestError("manifest 的本地 file URL 不合法。")
    root = _normalise_text(raw.get("root") or raw.get("root_dir") or plugin_id, "root", max_len=128, required=True)
    if root != plugin_id:
        raise ManifestError("manifest 的 root 必须与插件 id 完全一致。")
    description = _normalise_text(raw.get("description") or raw.get("desc") or "", "description", max_len=4000)
    author = _normalise_text(raw.get("author") or "", "author", max_len=200)
    homepage = _normalise_text(raw.get("homepage") or raw.get("home") or "", "homepage", max_len=2048)
    astrbot = _normalise_text(
        raw.get("astrbot") or raw.get("astrbot_version") or raw.get("min_astrbot_version") or "",
        "astrbot",
        max_len=200,
    )
    dependencies = _normalise_string_list(raw.get("dependencies") or [], "dependencies")
    provides = _normalise_provides(raw.get("provides"))
    requires = _normalise_requires(raw.get("requires"), schema_version=schema_version)
    conflicts = _normalise_conflicts(raw.get("conflicts"))
    # v2 requires imply the transitional dependency list, so an old consumer
    # that somehow ignores schema_version still sees at least the required IDs.
    if schema_version >= 2 and requires:
        merged_dependencies = list(dependencies)
        for requirement in requires:
            if not requirement["optional"] and requirement["plugin_id"] not in merged_dependencies:
                merged_dependencies.append(requirement["plugin_id"])
        dependencies = merged_dependencies
    config_schema_version = _normalise_schema_number(
        raw.get("config_schema_version"), "config_schema_version"
    )
    data_schema_version = _normalise_schema_number(
        raw.get("data_schema_version"), "data_schema_version"
    )
    recommended_groups = _normalise_string_list(
        raw.get("recommended_groups") or raw.get("groups"), "recommended_groups", max_items=20
    )
    known = {
        "schema_version",
        "id", "plugin_id", "name", "display_name", "version", "sha256", "checksum",
        "download", "download_url", "archive", "archive_url", "url", "file",
        "root", "root_dir", "description", "desc", "author", "homepage", "home",
        "astrbot", "astrbot_version", "min_astrbot_version", "dependencies",
        "provides", "requires", "conflicts", "config_schema_version",
        "data_schema_version", "recommended_groups", "groups", "extra",
        "runtime_requirements",
    }
    extra = {str(key): _deepcopy_json(value) for key, value in raw.items() if str(key) not in known}
    if isinstance(raw.get("extra"), Mapping):
        extra.update(_deepcopy_json(dict(raw["extra"])))
    return PluginManifest(
        plugin_id=plugin_id,
        name=name,
        version=version,
        sha256=sha256,
        download=download,
        root=root,
        schema_version=schema_version,
        description=description,
        author=author,
        homepage=homepage,
        astrbot=astrbot,
        dependencies=tuple(dependencies),
        provides=provides,
        requires=requires,
        conflicts=conflicts,
        config_schema_version=config_schema_version,
        data_schema_version=data_schema_version,
        recommended_groups=tuple(recommended_groups),
        runtime_requirements=_normalise_runtime_requirements(raw.get("runtime_requirements")),
        extra=extra or None,
    )


def validate_manifest(
    raw: Mapping[str, Any] | PluginManifest,
    *,
    max_schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Validate and normalise one plugin manifest.

    ``max_schema_version`` lets a v1 consumer reject a v2 manifest instead of
    silently ignoring fields it does not understand.  Unknown non-schema
    manifest keys are preserved in ``extra``.  The returned ``download`` value
    is not fetched; URL trust is checked later by the market source resolver.
    """

    manifest = (
        raw
        if isinstance(raw, PluginManifest)
        else _manifest_from_dict(raw, max_schema_version=max_schema_version)
    )
    return manifest.to_dict()


def load_manifest(
    source: str | os.PathLike[str] | Mapping[str, Any],
    *,
    local_root: Path | str | None = None,
    trusted_hosts: Iterable[str] | None = None,
    max_bytes: int = MAX_MANIFEST_BYTES,
    max_schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Read and validate a manifest from a local fixed file, dict, or trusted URL."""

    if isinstance(source, Mapping):
        return validate_manifest(source, max_schema_version=max_schema_version)
    text = str(source or "").strip()
    if not text:
        raise ManifestError("manifest 来源不能为空。")
    if text.startswith(("http://", "https://")):
        body, final_url = _http_get_bytes(
            text,
            trusted_hosts=trusted_hosts or (),
            max_bytes=max_bytes,
            accept="application/json, application/*+json",
        )
        try:
            raw = json.loads(body.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ManifestError("远程 manifest 不是合法 JSON。", detail=exc.__class__.__name__) from exc
        if not isinstance(raw, Mapping):
            raise ManifestError("远程 manifest 顶层必须是 JSON object。")
        result = validate_manifest(raw, max_schema_version=max_schema_version)
        result["_source_url"] = final_url
        return result
    if _has_parent_path_segment(text):
        raise UnsafePathError("manifest 路径不能包含路径穿越段。")
    path = Path(text).expanduser()
    if local_root is not None:
        path = (Path(local_root).expanduser() / path) if not path.is_absolute() else path
        if not _is_within(path, Path(local_root)):
            raise UnsafePathError("manifest 路径必须位于固定市场目录内。")
    else:
        path = path.resolve()
    raw = _read_json_object(path, max_bytes=max_bytes)
    return validate_manifest(raw, max_schema_version=max_schema_version)


@dataclass(frozen=True)
class _ZipLimits:
    max_archive_bytes: int = MAX_ARCHIVE_BYTES
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES
    max_files: int = MAX_ARCHIVE_FILES
    max_compression_ratio: int = MAX_COMPRESSION_RATIO


@dataclass(frozen=True)
class ZipInspection:
    root_dir: str
    files: tuple[str, ...]
    directories: tuple[str, ...]
    file_count: int
    uncompressed_bytes: int
    compressed_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_dir": self.root_dir,
            "file_count": self.file_count,
            "uncompressed_bytes": self.uncompressed_bytes,
            "compressed_bytes": self.compressed_bytes,
            "files": list(self.files),
            "directories": list(self.directories),
        }


def inspect_plugin_zip(
    archive_path: Path | str,
    expected_id: str,
    *,
    limits: _ZipLimits | None = None,
) -> ZipInspection:
    """Validate a plugin ZIP without extracting it."""

    plugin_id = _normalise_plugin_id(expected_id)
    archive = Path(archive_path)
    if archive.is_symlink():
        raise UnsafePathError("拒绝符号链接形式的插件安装包。")
    try:
        archive_size = archive.stat().st_size
    except OSError as exc:
        raise ArchiveValidationError("插件安装包不存在或不可读。", detail=exc.__class__.__name__) from exc
    limit = limits or _ZipLimits()
    if archive_size <= 0:
        raise ArchiveValidationError("插件安装包为空。")
    if archive_size > limit.max_archive_bytes:
        raise ArchiveValidationError("插件安装包超过大小限制。")
    files: list[str] = []
    directories: list[str] = []
    seen: set[str] = set()
    folded: set[str] = set()
    total_uncompressed = 0
    root_names: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > limit.max_files:
                raise ArchiveValidationError("ZIP 条目数量超过限制。")
            for info in infos:
                name = str(info.filename or "")
                if not name:
                    raise UnsafePathError("ZIP 中存在空条目名。")
                if info.flag_bits & 0x1:
                    raise ArchiveValidationError("拒绝加密 ZIP 条目。")
                is_dir = name.endswith("/")
                parts = _safe_relative_parts(name)
                if not parts:
                    raise UnsafePathError("ZIP 中存在空路径。")
                root_names.add(parts[0])
                if len(parts) < 2 and not is_dir:
                    raise ArchiveValidationError("插件 ZIP 必须只有一个精确的根目录，不能直接放文件。")
                normalised = "/".join(parts)
                key = normalised.casefold()
                if normalised in seen or key in folded:
                    raise ArchiveValidationError("ZIP 中存在重复或大小写冲突的路径。")
                seen.add(normalised)
                folded.add(key)
                mode = (int(info.external_attr) >> 16) & 0xFFFF
                if mode:
                    file_type = stat.S_IFMT(mode)
                    if stat.S_ISLNK(mode):
                        raise UnsafePathError("ZIP 中包含符号链接，已拒绝。")
                    if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                        raise UnsafePathError("ZIP 中包含设备、管道等特殊文件，已拒绝。")
                if is_dir:
                    directories.append(normalised)
                    continue
                if info.is_dir():
                    directories.append(normalised)
                    continue
                total_uncompressed += int(info.file_size or 0)
                if total_uncompressed > limit.max_extracted_bytes:
                    raise ArchiveValidationError("ZIP 解压后大小超过限制。")
                if int(info.file_size or 0) > 0 and int(info.compress_size or 0) > 0:
                    ratio = int(info.file_size) / max(1, int(info.compress_size))
                    if ratio > limit.max_compression_ratio:
                        raise ArchiveValidationError("ZIP 压缩比异常，疑似压缩炸弹。")
                files.append(normalised)
    except zipfile.BadZipFile as exc:
        raise ArchiveValidationError("插件安装包不是有效 ZIP。", detail=exc.__class__.__name__) from exc
    except OSError as exc:
        raise ArchiveValidationError("无法读取插件 ZIP。", detail=exc.__class__.__name__) from exc
    if len(root_names) != 1:
        raise ArchiveValidationError("ZIP 必须且只能包含一个顶层根目录。")
    root_dir = next(iter(root_names))
    if root_dir != plugin_id:
        raise ArchiveValidationError("ZIP 根目录必须与 manifest 的插件 id 完全一致。")
    if not files:
        raise ArchiveValidationError("插件 ZIP 中没有文件。")
    # Every non-directory entry must have the root as its first component; this
    # is already enforced while parsing, but keep the explicit assertion clear.
    for name in files + directories:
        if name.split("/", 1)[0] != root_dir:
            raise ArchiveValidationError("ZIP 条目不在唯一根目录下。")
    return ZipInspection(
        root_dir=root_dir,
        files=tuple(files),
        directories=tuple(directories),
        file_count=len(files),
        uncompressed_bytes=total_uncompressed,
        compressed_bytes=archive_size,
    )


def extract_plugin_zip(
    archive_path: Path | str,
    destination: Path | str,
    expected_id: str,
    *,
    limits: _ZipLimits | None = None,
) -> Path:
    """Safely extract a validated plugin ZIP and return its root directory."""

    archive = Path(archive_path)
    destination = Path(destination)
    inspection = inspect_plugin_zip(archive, expected_id, limits=limits)
    if destination.exists():
        raise PluginConflictError("插件解压目标已存在，已拒绝覆盖。")
    destination.mkdir(parents=True, exist_ok=False)
    chmod_best_effort(destination, 0o700)
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = str(info.filename or "")
                is_dir = name.endswith("/") or info.is_dir()
                parts = _safe_relative_parts(name)
                target = destination.joinpath(*parts)
                if is_dir:
                    target.mkdir(parents=True, exist_ok=True)
                    chmod_best_effort(target, 0o755)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(target, flags, 0o644)
                try:
                    with zf.open(info, "r") as source, os.fdopen(fd, "wb") as sink:
                        remaining = MAX_EXTRACTED_BYTES
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise ArchiveValidationError("ZIP 解压内容超过限制。")
                            sink.write(chunk)
                        sink.flush()
                        os.fsync(sink.fileno())
                except Exception:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    raise
                chmod_best_effort(target, 0o644)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    root = destination / inspection.root_dir
    if not _existing_dir_no_symlink(root):
        shutil.rmtree(destination, ignore_errors=True)
        raise ArchiveValidationError("ZIP 解压后缺少有效根目录。")
    for path in root.rglob("*"):
        try:
            if path.is_symlink():
                raise UnsafePathError("ZIP 解压后检测到符号链接，已拒绝。")
        except OSError as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise ArchiveValidationError("无法校验解压结果。", detail=exc.__class__.__name__) from exc
    return root


# ---------------------------------------------------------------------------
# Trusted HTTP source helpers
# ---------------------------------------------------------------------------


def _normalise_trusted_hosts(hosts: Iterable[str] | None) -> frozenset[str]:
    result: set[str] = set()
    for item in hosts or ():
        text = str(item or "").strip().lower()
        if not text:
            continue
        if "://" in text or "/" in text or "@" in text:
            raise PluginMarketError("受信插件市场主机配置不合法。")
        if text.startswith("*."):
            suffix = text[2:].strip(".")
            if not suffix or not re.fullmatch(r"[a-z0-9.-]+", suffix):
                raise PluginMarketError("受信插件市场主机通配配置不合法。")
            result.add("*." + suffix)
        elif re.fullmatch(r"[a-z0-9.-]+", text):
            result.add(text.strip("."))
        else:
            raise PluginMarketError("受信插件市场主机配置不合法。")
    return frozenset(result)


def _host_is_trusted(hostname: str, trusted_hosts: Iterable[str] | None) -> bool:
    host = str(hostname or "").strip().lower().strip(".")
    hosts = trusted_hosts if isinstance(trusted_hosts, frozenset) else _normalise_trusted_hosts(trusted_hosts)
    if not host or not hosts:
        return False
    for pattern in hosts:
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if host == suffix or host.endswith("." + suffix):
                return True
        elif host == pattern:
            return True
    return False


def _validate_remote_url(url: str, trusted_hosts: Iterable[str] | None) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError as exc:
        raise UnsafePathError("插件市场 URL 不合法。", detail=exc.__class__.__name__) from exc
    if parsed.scheme.lower() != "https":
        raise UnsafePathError("远程插件市场只允许受信 HTTPS URL。")
    if not parsed.hostname:
        raise UnsafePathError("插件市场 URL 缺少主机名。")
    if parsed.username or parsed.password:
        raise UnsafePathError("插件市场 URL 不能包含用户名或密码。")
    if parsed.fragment:
        raise UnsafePathError("插件市场 URL 不能包含 fragment。")
    if ".." in urllib.parse.unquote(parsed.path).replace("\\", "/").split("/"):
        raise UnsafePathError("插件市场 URL 不能包含路径穿越段。")
    if not _host_is_trusted(parsed.hostname, trusted_hosts):
        raise UnsafePathError("插件市场 URL 主机不在受信列表中。")
    return parsed


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, trusted_hosts: Iterable[str]) -> None:
        super().__init__()
        self.trusted_hosts = tuple(trusted_hosts)

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _validate_remote_url(newurl, self.trusted_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_get_bytes(
    url: str,
    *,
    trusted_hosts: Iterable[str],
    max_bytes: int,
    accept: str = "application/octet-stream",
    timeout: float = 15.0,
) -> tuple[bytes, str]:
    starting_url = _validate_remote_url(url, trusted_hosts)
    request = urllib.request.Request(
        starting_url.geturl(),
        headers={
            "User-Agent": "QQBotStudio-PluginMarket/1.0",
            "Accept": accept,
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _TrustedRedirectHandler(tuple(trusted_hosts)),
    )
    try:
        with opener.open(request, timeout=max(1.0, float(timeout))) as response:
            final_url = str(response.geturl() or starting_url.geturl())
            _validate_remote_url(final_url, trusted_hosts)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PluginMarketError("远程插件市场响应超过大小限制。")
                chunks.append(chunk)
            return b"".join(chunks), final_url
    except PluginMarketError:
        raise
    except urllib.error.HTTPError as exc:
        raise PluginMarketError("远程插件市场返回 HTTP 错误。", detail=str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PluginMarketError("无法连接远程插件市场。", detail=exc.__class__.__name__) from exc


@dataclass(frozen=True)
class _SourceDocument:
    raw: Mapping[str, Any]
    kind: str
    name: str
    base_dir: Path | None = None
    base_url: str = ""
    trusted_hosts: tuple[str, ...] = ()


def _catalog_entries_from_document(document: Any) -> list[Mapping[str, Any]]:
    if isinstance(document, Mapping):
        if isinstance(document.get("plugins"), list):
            entries = document["plugins"]
        elif isinstance(document.get("entries"), list):
            entries = document["entries"]
        elif document.get("id") and (document.get("sha256") or document.get("checksum")):
            entries = [document]
        else:
            raise ManifestError("市场清单必须包含 plugins 数组或单个合法 manifest。")
    elif isinstance(document, list):
        entries = document
    else:
        raise ManifestError("市场清单顶层必须是 JSON object 或数组。")
    if not entries:
        return []
    result: list[Mapping[str, Any]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            raise ManifestError("市场清单中的每个插件条目必须是 JSON object。")
        merged = dict(item)
        nested = merged.get("manifest")
        if isinstance(nested, Mapping):
            nested_merged = dict(nested)
            # Wrapper fields such as download overrides are useful when one
            # catalog hosts signed archives separately from plugin metadata.
            for key, value in merged.items():
                if key != "manifest" and key not in nested_merged:
                    nested_merged[key] = value
            merged = nested_merged
        result.append(merged)
    return result


def _resolve_local_archive_ref(base_dir: Path, value: str, market_root: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ManifestError("manifest 未提供安装包路径。")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"", "file"}:
        raise ManifestError("本地市场清单不能引用非 HTTPS 远程下载地址。")
    if parsed.scheme.lower() == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise UnsafePathError("本地 file URL 的主机部分不合法。")
        path = Path(urllib.parse.unquote(parsed.path))
    else:
        candidate = Path(urllib.parse.unquote(text))
        path = candidate if candidate.is_absolute() else base_dir / candidate
    path = path.expanduser()
    if not _is_within(path, market_root):
        raise UnsafePathError("插件安装包必须位于固定市场目录内。")
    if path.is_symlink() or not path.is_file():
        raise UnsafePathError("插件安装包不存在、不是普通文件或为符号链接。")
    return path.resolve()


# ---------------------------------------------------------------------------
# SQLite state database and plugin groups
# ---------------------------------------------------------------------------


class _StudioStateDatabase:
    """Small connection factory with deliberate corruption protection."""

    def __init__(self, db_path: Path | str, *, lock: threading.RLock | None = None) -> None:
        self.db_path = Path(db_path)
        self.state_dir = self.db_path.parent
        self.lock = lock or threading.RLock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        with self.lock:
            try:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                chmod_best_effort(self.state_dir, 0o700)
            except OSError as exc:
                raise PluginStateError("无法创建插件状态目录。", detail=exc.__class__.__name__) from exc
            if self.db_path.is_symlink():
                raise PluginStateError("拒绝符号链接形式的插件状态数据库。")
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=20.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA busy_timeout = 20000")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
                self._ensure_schema(conn)
                chmod_best_effort(self.db_path, 0o600)
                return conn
            except sqlite3.DatabaseError as exc:
                try:
                    conn.close()  # type: ignore[possibly-undefined]
                except Exception:
                    pass
                raise PluginStateError(
                    "插件状态数据库损坏或不可读，已停止操作以保护现有数据。",
                    detail=exc.__class__.__name__,
                ) from exc
            except PluginMarketError:
                raise
            except OSError as exc:
                raise PluginStateError("无法打开插件状态数据库。", detail=exc.__class__.__name__) from exc

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        meta_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if meta_exists:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is not None:
                try:
                    existing_version = int(row[0])
                except (TypeError, ValueError) as exc:
                    raise PluginStateError(
                        "插件状态数据库版本记录损坏，已停止操作以保护现有数据。"
                    ) from exc
                if existing_version > GROUP_SCHEMA_VERSION:
                    raise PluginStateError(
                        "插件状态数据库来自更高版本，已停止操作以避免数据损坏。"
                    )
        schema = """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plugin_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plugin_group_members (
            group_id TEXT NOT NULL REFERENCES plugin_groups(id) ON DELETE CASCADE,
            plugin_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (group_id, plugin_id)
        );
        CREATE INDEX IF NOT EXISTS idx_plugin_group_members_plugin
            ON plugin_group_members(plugin_id);
        CREATE TABLE IF NOT EXISTS plugin_previews (
            token TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at REAL NOT NULL,
            used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS plugin_idempotency (
            key TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plugin_operations (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            plugin_id TEXT NOT NULL,
            status TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            request_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_plugin_operations_plugin
            ON plugin_operations(plugin_id, created_at);
        """
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(GROUP_SCHEMA_VERSION),),
        )
        conn.commit()
        self._schema_ready = True

    @staticmethod
    def _translate_database_error(exc: sqlite3.DatabaseError) -> PluginStateError:
        return PluginStateError(
            "插件状态数据库操作失败，已停止写入以保护现有数据。",
            detail=exc.__class__.__name__,
        )


class PluginGroupStore:
    """SQLite-backed many-to-many plugin groups.

    Group deletion touches only the SQLite rows.  It never calls filesystem
    removal helpers, so deleting a group can never delete a plugin directory or
    user data.
    """

    def __init__(
        self,
        state_path: Path | str,
        *,
        lock: threading.RLock | None = None,
    ) -> None:
        path = Path(state_path)
        if path.suffix == ".sqlite3" or path.name == "state.sqlite3":
            db_path = path
        else:
            db_path = path / "state.sqlite3"
        self._db = _StudioStateDatabase(db_path, lock=lock)
        self.db_path = db_path
        self.state_dir = db_path.parent
        self.lock = self._db.lock

    # -- helpers -------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        return self._db._connect()

    def _validate_group_name(self, name: Any) -> str:
        text = str(name or "").strip()
        if not text:
            raise GroupError("分组名称不能为空。")
        if len(text) > MAX_GROUP_NAME_LENGTH:
            raise GroupError(f"分组名称不能超过 {MAX_GROUP_NAME_LENGTH} 个字符。")
        if CONTROL_RE.search(text):
            raise GroupError("分组名称包含控制字符。")
        return text

    def _validate_group_id(self, group_id: Any, *, allow_empty: bool = False) -> str:
        text = str(group_id or "").strip()
        if not text and allow_empty:
            return ""
        if not GROUP_ID_RE.fullmatch(text):
            raise GroupError("分组 ID 格式不合法。")
        return text

    @staticmethod
    def _rows_to_groups(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in rows:
            group_id = str(row["id"])
            if group_id not in grouped:
                grouped[group_id] = {
                    "id": group_id,
                    "name": str(row["name"]),
                    "plugin_ids": [],
                    "plugins": [],
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                }
                order.append(group_id)
            plugin_id = row["plugin_id"]
            if plugin_id:
                pid = str(plugin_id)
                grouped[group_id]["plugin_ids"].append(pid)
                grouped[group_id]["plugins"].append({"id": pid, "plugin_id": pid})
        return [grouped[group_id] for group_id in order]

    def _list_groups_locked(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT g.id, g.name, g.created_at, g.updated_at,
                   m.plugin_id
              FROM plugin_groups g
              LEFT JOIN plugin_group_members m ON m.group_id = g.id
             ORDER BY lower(g.name), g.name, m.plugin_id
            """
        ).fetchall()
        return self._rows_to_groups(rows)

    def _get_group_locked(self, conn: sqlite3.Connection, group_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT id, name, created_at, updated_at FROM plugin_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        group = {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "plugin_ids": [],
            "plugins": [],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        members = conn.execute(
            "SELECT plugin_id FROM plugin_group_members WHERE group_id = ? ORDER BY plugin_id",
            (group_id,),
        ).fetchall()
        for member in members:
            pid = str(member["plugin_id"])
            group["plugin_ids"].append(pid)
            group["plugins"].append({"id": pid, "plugin_id": pid})
        return group

    # -- public read operations ---------------------------------------
    def list_groups(self) -> dict[str, Any]:
        with self.lock:
            try:
                conn = self._connect()
                try:
                    groups = self._list_groups_locked(conn)
                finally:
                    conn.close()
            except PluginMarketError:
                raise
            except sqlite3.DatabaseError as exc:
                raise self._db._translate_database_error(exc) from exc
        return {
            "ok": True,
            "schema_version": GROUP_SCHEMA_VERSION,
            "groups": groups,
            "items": groups,
            "count": len(groups),
            "message": "插件分组已读取。" if groups else "尚未创建插件分组。",
            "error": "",
        }

    def get_group(self, group_id: str) -> dict[str, Any]:
        valid = self._validate_group_id(group_id)
        with self.lock:
            conn = self._connect()
            try:
                group = self._get_group_locked(conn, valid)
            finally:
                conn.close()
        if group is None:
            raise GroupNotFoundError("插件分组不存在。")
        return {"ok": True, "group": group}

    def create_group(self, name: str, *, group_id: str | None = None) -> dict[str, Any]:
        action: dict[str, Any] = {"op": "create", "name": name}
        if group_id:
            action["group_id"] = group_id
        preview = self.preview([action])
        result = self.apply({"preview_token": preview["preview_token"], "confirm": True})
        plan = next((item for item in preview.get("plan", []) if item.get("op") == "create"), {})
        result["group"] = next(
            (group for group in result.get("groups", []) if group.get("id") == plan.get("group_id")),
            None,
        )
        return result

    def rename_group(self, group_id: str, name: str) -> dict[str, Any]:
        action = {"op": "rename", "group_id": group_id, "name": name}
        preview = self.preview([action])
        result = self.apply({"preview_token": preview["preview_token"], "confirm": True})
        result["group"] = next(
            (group for group in result.get("groups", []) if group.get("id") == group_id),
            None,
        )
        return result

    def delete_group(self, group_id: str) -> dict[str, Any]:
        action = {"op": "delete", "group_id": group_id}
        preview = self.preview([action])
        result = self.apply({"preview_token": preview["preview_token"], "confirm": True})
        result["deleted"] = not any(
            group.get("id") == group_id for group in result.get("groups", [])
        )
        return result

    def add_plugin(self, group_id: str, plugin_id: str) -> dict[str, Any]:
        action = {"op": "add", "group_id": group_id, "plugin_id": plugin_id}
        preview = self.preview([action])
        result = self.apply({"preview_token": preview["preview_token"], "confirm": True})
        result["group"] = next(
            (group for group in result.get("groups", []) if group.get("id") == group_id),
            None,
        )
        return result

    def remove_plugin(self, group_id: str, plugin_id: str) -> dict[str, Any]:
        action = {"op": "remove", "group_id": group_id, "plugin_id": plugin_id}
        preview = self.preview([action])
        result = self.apply({"preview_token": preview["preview_token"], "confirm": True})
        result["group"] = next(
            (group for group in result.get("groups", []) if group.get("id") == group_id),
            None,
        )
        return result

    # -- preview/apply -------------------------------------------------
    @staticmethod
    def _coerce_actions(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, Mapping) and isinstance(payload.get("actions"), list):
            raw_actions = payload["actions"]
        elif isinstance(payload, Mapping) and payload.get("action") not in {None, "", "preview", "apply"}:
            raw_actions = [payload]
        elif isinstance(payload, list):
            raw_actions = payload
        else:
            raise GroupError("请提供 actions 数组或一个分组 action。")
        if not raw_actions:
            raise GroupError("分组操作不能为空。")
        if len(raw_actions) > MAX_BATCH_ACTIONS:
            raise GroupError(f"单次最多允许 {MAX_BATCH_ACTIONS} 个分组操作。")
        actions: list[dict[str, Any]] = []
        for raw in raw_actions:
            if not isinstance(raw, Mapping):
                raise GroupError("每个分组操作必须是 JSON object。")
            op = str(raw.get("op") or raw.get("type") or raw.get("action") or "").strip().lower()
            aliases = {
                "create_group": "create",
                "create": "create",
                "rename_group": "rename",
                "rename": "rename",
                "delete_group": "delete",
                "delete": "delete",
                "remove_group": "delete",
                "add_plugin": "add",
                "add_member": "add",
                "add": "add",
                "remove_plugin": "remove",
                "remove_member": "remove",
                "remove": "remove",
            }
            if op not in aliases:
                raise GroupError("不支持的分组操作。")
            action: dict[str, Any] = {"op": aliases[op]}
            if raw.get("group_id") not in {None, ""}:
                action["group_id"] = str(raw.get("group_id")).strip()
            if raw.get("name") is not None:
                action["name"] = str(raw.get("name"))
            if raw.get("plugin_id") not in {None, ""}:
                action["plugin_id"] = str(raw.get("plugin_id")).strip()
            # Preserve unknown safe metadata for forward compatibility, but do
            # not let it influence filesystem work (there is none here).
            for key, value in raw.items():
                if key not in {"op", "type", "action", "group_id", "name", "plugin_id"}:
                    action[key] = _deepcopy_json(value)
            actions.append(action)
        return actions

    def _plan_actions(self, actions: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        conn = self._connect()
        warnings: list[str] = []
        plans: list[dict[str, Any]] = []
        try:
            current = {group["id"]: group for group in self._list_groups_locked(conn)}
            names = {group["name"].casefold(): group for group in current.values()}
            hypothetical: dict[str, dict[str, Any]] = copy.deepcopy(current)
            for index, raw in enumerate(actions):
                op = str(raw.get("op") or "")
                if op == "create":
                    name = self._validate_group_name(raw.get("name"))
                    existing = names.get(name.casefold())
                    group_id = str(raw.get("group_id") or "").strip()
                    if existing:
                        plans.append({
                            "index": index, "op": "create", "group_id": existing["id"],
                            "name": name, "will_change": False, "reason": "同名分组已存在",
                        })
                        continue
                    if group_id:
                        group_id = self._validate_group_id(group_id)
                        if group_id in hypothetical:
                            raise GroupError("指定分组 ID 已存在。")
                    else:
                        group_id = f"grp-{secrets.token_hex(8)}"
                    plans.append({
                        "index": index, "op": "create", "group_id": group_id,
                        "name": name, "will_change": True, "reason": "",
                    })
                    hypothetical[group_id] = {
                        "id": group_id, "name": name, "plugin_ids": [], "created_at": "", "updated_at": "",
                    }
                    names[name.casefold()] = hypothetical[group_id]
                elif op == "rename":
                    group_id = self._validate_group_id(raw.get("group_id"))
                    name = self._validate_group_name(raw.get("name"))
                    group = hypothetical.get(group_id)
                    if group is None:
                        raise GroupNotFoundError("要重命名的插件分组不存在。")
                    if group["name"] == name:
                        plans.append({
                            "index": index, "op": "rename", "group_id": group_id,
                            "name": name, "will_change": False, "reason": "名称未变化",
                        })
                        continue
                    duplicate = names.get(name.casefold())
                    if duplicate is not None and duplicate["id"] != group_id:
                        raise PluginConflictError("已存在同名插件分组。")
                    old_name = group["name"]
                    names.pop(old_name.casefold(), None)
                    group["name"] = name
                    names[name.casefold()] = group
                    plans.append({
                        "index": index, "op": "rename", "group_id": group_id,
                        "name": name, "old_name": old_name,
                        "will_change": True, "reason": "",
                    })
                elif op == "delete":
                    group_id = self._validate_group_id(raw.get("group_id"))
                    group = hypothetical.get(group_id)
                    if group is None:
                        plans.append({
                            "index": index, "op": "delete", "group_id": group_id,
                            "name": "", "will_change": False, "reason": "分组已不存在",
                        })
                        continue
                    plans.append({
                        "index": index, "op": "delete", "group_id": group_id,
                        "name": group["name"], "will_change": True,
                        "reason": "只删除分组元数据，不删除任何文件或插件",
                    })
                    names.pop(str(group["name"]).casefold(), None)
                    hypothetical.pop(group_id, None)
                elif op in {"add", "remove"}:
                    group_id = self._validate_group_id(raw.get("group_id"))
                    plugin_id = str(raw.get("plugin_id") or "").strip()
                    if not PLUGIN_ID_RE.fullmatch(plugin_id):
                        raise GroupError("插件 ID 格式不合法。")
                    group = hypothetical.get(group_id)
                    if group is None:
                        raise GroupNotFoundError("插件分组不存在。")
                    present = plugin_id in group.get("plugin_ids", [])
                    if op == "add":
                        will_change = not present
                        reason = "" if will_change else "插件已在该分组中"
                    else:
                        will_change = present
                        reason = "" if will_change else "插件不在该分组中"
                    plans.append({
                        "index": index, "op": op, "group_id": group_id,
                        "plugin_id": plugin_id, "will_change": will_change, "reason": reason,
                    })
                    if op == "add" and will_change:
                        group.setdefault("plugin_ids", []).append(plugin_id)
                    if op == "remove" and will_change:
                        group["plugin_ids"] = [pid for pid in group["plugin_ids"] if pid != plugin_id]
                else:
                    raise GroupError("不支持的分组操作。")
        finally:
            conn.close()
        return plans, warnings

    def preview(self, payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        actions = self._coerce_actions(payload)
        with self.lock:
            plans, warnings = self._plan_actions(actions)
            token = f"gprev-{secrets.token_urlsafe(24)}"
            expires_at = time.time() + PREVIEW_TTL_SECONDS
            stored = {"actions": actions, "plans": plans, "warnings": warnings}
            try:
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "INSERT INTO plugin_previews(token, kind, payload_json, payload_hash, created_at, expires_at, used_at) "
                        "VALUES(?, 'groups', ?, ?, ?, ?, NULL)",
                        (
                            token,
                            json.dumps(stored, ensure_ascii=False, sort_keys=True),
                            _fingerprint(actions),
                            _now(),
                            expires_at,
                        ),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except sqlite3.DatabaseError as exc:
                raise self._db._translate_database_error(exc) from exc
        public_plan = []
        for item in plans:
            public_item = dict(item)
            public_plan.append(public_item)
        return {
            "ok": True,
            "preview_token": token,
            "kind": "plugin-groups",
            "actions": actions,
            "plan": public_plan,
            "warnings": warnings,
            "requires_confirmation": True,
            "expires_at": _iso_from_epoch(expires_at),
            "message": "分组变更预览已生成；确认后才会写入数据库。",
            "error": "",
        }

    def _lookup_idempotency(
        self,
        conn: sqlite3.Connection,
        key: str,
        scope: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT scope, request_hash, response_json FROM plugin_idempotency WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        if str(row["scope"]) != scope or str(row["request_hash"]) != request_hash:
            raise IdempotencyConflictError("同一幂等键已用于不同的插件操作。")
        try:
            response = json.loads(str(row["response_json"]))
        except ValueError as exc:
            raise PluginStateError("插件幂等记录损坏，已停止操作。") from exc
        return response if isinstance(response, dict) else None

    def apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise GroupError("apply 请求必须是 JSON object。")
        if payload.get("confirm") is not True:
            raise ConfirmationRequiredError("请先预览并显式确认分组变更（confirm=true）。")
        token = str(payload.get("preview_token") or "").strip()
        if not token:
            raise ConfirmationRequiredError("缺少 preview_token，请先调用 /plugin-groups/preview。")
        supplied_actions = payload.get("actions")
        if supplied_actions is not None and not isinstance(supplied_actions, list):
            raise GroupError("actions 必须为数组。")
        idem_key = str(payload.get("idempotency_key") or "").strip()
        if idem_key and (len(idem_key) > MAX_IDEMPOTENCY_KEY_LENGTH or CONTROL_RE.search(idem_key)):
            raise GroupError("idempotency_key 不合法。")
        with self.lock:
            try:
                conn = self._connect()
            except PluginMarketError:
                raise
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT kind, payload_json, payload_hash, expires_at, used_at FROM plugin_previews WHERE token = ?",
                    (token,),
                ).fetchone()
                if row is None or str(row["kind"]) != "groups":
                    conn.rollback()
                    raise PreviewExpiredError("分组预览不存在或已过期，请重新预览。")
                stored = json.loads(str(row["payload_json"]))
                if not isinstance(stored, dict):
                    conn.rollback()
                    raise PluginStateError("分组预览记录损坏，已停止操作。")
                actions = stored.get("actions")
                plans = stored.get("plans")
                if not isinstance(actions, list) or not isinstance(plans, list):
                    conn.rollback()
                    raise PluginStateError("分组预览记录损坏，已停止操作。")
                request_hash = _fingerprint(actions)
                if supplied_actions is not None and _fingerprint(supplied_actions) != request_hash:
                    conn.rollback()
                    raise IdempotencyConflictError("apply 的 actions 与预览内容不一致。")
                if idem_key:
                    existing = self._lookup_idempotency(conn, idem_key, "groups", request_hash)
                    if existing is not None:
                        conn.rollback()
                        existing["idempotent"] = True
                        return existing
                if float(row["expires_at"] or 0) < time.time():
                    conn.rollback()
                    raise PreviewExpiredError("分组预览已过期，请重新预览。")
                if row["used_at"]:
                    conn.rollback()
                    raise PreviewExpiredError("该分组预览已使用，请重新预览。")
                applied: list[dict[str, Any]] = []
                for plan in plans:
                    if not isinstance(plan, Mapping) or not plan.get("will_change"):
                        applied.append(dict(plan) if isinstance(plan, Mapping) else {})
                        continue
                    op = str(plan.get("op") or "")
                    now = _now()
                    if op == "create":
                        conn.execute(
                            "INSERT INTO plugin_groups(id, name, created_at, updated_at) VALUES(?,?,?,?)",
                            (str(plan["group_id"]), str(plan["name"]), now, now),
                        )
                    elif op == "rename":
                        cursor = conn.execute(
                            "UPDATE plugin_groups SET name = ?, updated_at = ? WHERE id = ?",
                            (str(plan["name"]), now, str(plan["group_id"])),
                        )
                        if cursor.rowcount != 1:
                            raise GroupNotFoundError("要重命名的插件分组已不存在。")
                    elif op == "delete":
                        # Intentionally DB-only.  No filesystem function is
                        # called anywhere in this branch.
                        conn.execute("DELETE FROM plugin_groups WHERE id = ?", (str(plan["group_id"]),))
                    elif op == "add":
                        conn.execute(
                            "INSERT OR IGNORE INTO plugin_group_members(group_id, plugin_id, created_at) VALUES(?,?,?)",
                            (str(plan["group_id"]), str(plan["plugin_id"]), now),
                        )
                    elif op == "remove":
                        conn.execute(
                            "DELETE FROM plugin_group_members WHERE group_id = ? AND plugin_id = ?",
                            (str(plan["group_id"]), str(plan["plugin_id"])),
                        )
                    applied.append(dict(plan))
                conn.execute(
                    "UPDATE plugin_previews SET used_at = ? WHERE token = ?",
                    (_now(), token),
                )
                groups = self._list_groups_locked(conn)
                response = {
                    "ok": True,
                    "preview_token": token,
                    "idempotent": False,
                    "applied": applied,
                    "groups": groups,
                    "count": len(groups),
                    "message": "插件分组变更已写入 SQLite；未删除任何插件文件。",
                    "error": "",
                }
                if idem_key:
                    conn.execute(
                        "INSERT INTO plugin_idempotency(key, scope, request_hash, response_json, created_at) "
                        "VALUES(?, 'groups', ?, ?, ?)",
                        (
                            idem_key,
                            request_hash,
                            json.dumps(response, ensure_ascii=False, sort_keys=True),
                            _now(),
                        ),
                    )
                conn.commit()
                return response
            except PluginMarketError:
                try:
                    conn.rollback()
                except sqlite3.DatabaseError:
                    pass
                raise
            except sqlite3.IntegrityError as exc:
                try:
                    conn.rollback()
                except sqlite3.DatabaseError:
                    pass
                raise PluginConflictError("分组变更与现有数据冲突。", detail=exc.__class__.__name__) from exc
            except sqlite3.DatabaseError as exc:
                try:
                    conn.rollback()
                except sqlite3.DatabaseError:
                    pass
                raise self._db._translate_database_error(exc) from exc
            finally:
                conn.close()

    def execute_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Preview + apply convenience used by POST /plugin-groups.

        The explicit confirmation and idempotency rules are identical to the
        dedicated preview/apply endpoints.
        """

        if not isinstance(payload, Mapping):
            raise GroupError("分组请求必须是 JSON object。")
        if payload.get("confirm") is not True:
            raise ConfirmationRequiredError("分组变更需要显式确认（confirm=true）。")
        preview_payload = {key: value for key, value in payload.items() if key not in {"confirm", "preview_token", "idempotency_key"}}
        preview = self.preview(preview_payload)
        apply_payload = {
            "preview_token": preview["preview_token"],
            "confirm": True,
            "idempotency_key": payload.get("idempotency_key") or "",
        }
        result = self.apply(apply_payload)
        result["preview_token"] = preview["preview_token"]
        return result

    def remove_plugin_everywhere(self, plugin_id: str) -> dict[str, Any]:
        """Remove plugin membership rows after a successful code deletion."""

        pid = str(plugin_id or "").strip()
        if not PLUGIN_ID_RE.fullmatch(pid):
            raise GroupError("插件 ID 格式不合法。")
        with self.lock:
            try:
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    cursor = conn.execute(
                        "DELETE FROM plugin_group_members WHERE plugin_id = ?",
                        (pid,),
                    )
                    conn.commit()
                    removed = max(0, int(cursor.rowcount))
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            except sqlite3.DatabaseError as exc:
                raise self._db._translate_database_error(exc) from exc
        return {"ok": True, "plugin_id": pid, "removed_relations": removed}

    def cleanup_plugin_references(self, plugin_id: str) -> dict[str, Any]:
        return self.remove_plugin_everywhere(plugin_id)


GroupStore = PluginGroupStore


# ---------------------------------------------------------------------------
# Filesystem lock and plugin market manager
# ---------------------------------------------------------------------------


class _ExclusiveFileLock:
    """Best-effort cross-process exclusive lock using stdlib primitives."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Any = None

    def __enter__(self) -> "_ExclusiveFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows only
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_LOCK, 1)
        except OSError:
            try:
                self._handle.close()
            finally:
                self._handle = None
            raise PluginStateError("无法获取插件市场操作锁。")
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


def _version_key(value: str) -> tuple[Any, ...]:
    """Small semver-ish comparison key that does not need packaging."""

    parts: list[Any] = []
    for item in re.split(r"[.+-]", str(value or "")):
        if item.isdigit():
            parts.append((0, int(item)))
        elif item:
            parts.append((1, item.lower()))
    return tuple(parts)


def _version_is_newer(candidate: str, current: str) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    try:
        return _version_key(candidate) > _version_key(current)
    except TypeError:
        return candidate != current


@dataclass
class _ArchiveSpec:
    kind: str  # local | url
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


class PluginMarket:
    """Marketplace + installed registry + grouped state for one instance."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        plugin_root: Path | str | None = None,
        lock: threading.RLock | None = None,
        redact: Callable[[str], str] | None = None,
        trusted_hosts: Iterable[str] | None = None,
        runtime_state: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
        max_archive_bytes: int = MAX_ARCHIVE_BYTES,
        max_extracted_bytes: int = MAX_EXTRACTED_BYTES,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.plugin_root = Path(plugin_root).expanduser().resolve() if plugin_root else self.data_dir / "data" / "astrbot" / "plugins"
        self.state_dir = self.data_dir / "studio-plugins"
        self.market_dir = self.state_dir / "market"
        self.registry_path = self.state_dir / "registry.json"
        self.cache_path = self.state_dir / "market-cache.json"
        self.backup_root = self.state_dir / "backups" / "plugins"
        self.staging_root = self.state_dir / "staging"
        self.download_root = self.state_dir / "downloads"
        self.lock = lock or threading.RLock()
        self._operation_gate = threading.Lock()
        self._redact = redact or (lambda value: value)
        self._file_lock_path = self.state_dir / "market.lock"
        self._configured_trusted_hosts = tuple(trusted_hosts or ())
        self._runtime_state_provider = runtime_state
        self._max_archive_bytes = max(1024, int(max_archive_bytes))
        self._max_extracted_bytes = max(1024, int(max_extracted_bytes))
        self.groups = PluginGroupStore(
            self.state_dir / "state.sqlite3",
            lock=self.lock,
        )

    # -- common paths / sources ---------------------------------------
    def _ensure_dirs(self) -> None:
        for path in (self.state_dir, self.market_dir, self.backup_root, self.staging_root, self.download_root):
            try:
                path.mkdir(parents=True, exist_ok=True)
                chmod_best_effort(path, 0o700)
            except OSError as exc:
                raise PluginStateError("无法创建插件市场数据目录。", detail=exc.__class__.__name__) from exc

    def _trusted_hosts(self) -> tuple[str, ...]:
        hosts: list[str] = list(DEFAULT_MARKET_TRUSTED_HOSTS)
        hosts.extend(self._configured_trusted_hosts)
        env = os.environ.get("QQBOT_PLUGIN_MARKET_HOSTS", "")
        if env.strip():
            hosts.extend(item for item in re.split(r"[,;\s]+", env) if item)
        config_path = self.market_dir / "trusted-hosts.json"
        if config_path.exists():
            raw = _read_json_object(config_path, max_bytes=128 * 1024)
            values = raw.get("hosts") if isinstance(raw.get("hosts"), list) else raw.get("trusted_hosts")
            if not isinstance(values, list):
                raise PluginStateError("插件市场受信主机配置格式不合法。")
            hosts.extend(str(item) for item in values)
        return tuple(sorted(_normalise_trusted_hosts(hosts)))

    def _normalise_source(self, source: Any) -> _SourceDocument:
        trusted = self._trusted_hosts()
        if source is None:
            # A fixed deployment can pin either a local catalog or a trusted
            # HTTPS URL in market/source.json.  Request bodies may override it,
            # but only through the same local-root/trusted-host checks.
            source_config = self.market_dir / "source.json"
            if source_config.exists():
                try:
                    raw_source = _read_json_object(source_config, max_bytes=128 * 1024)
                except PluginStateError as exc:
                    raise ManifestError("插件市场 source.json 损坏。", detail=exc.detail) from exc
                configured = raw_source.get("source") or raw_source.get("url") or raw_source.get("path")
                if not configured:
                    raise PluginStateError("插件市场 source.json 缺少 url/path/source 字段。")
                return self._normalise_source(configured)
            for name in ("catalog.json", "index.json", "manifest.json"):
                candidate = self.market_dir / name
                if candidate.exists():
                    return self._load_source_path(candidate, trusted)
            # Fresh instances have no local market cache.  Use the official
            # GitHub-hosted catalog by default; deployments can still pin a
            # different source through market/source.json.
            return self._normalise_source(DEFAULT_MARKET_SOURCE_URL)
        if isinstance(source, Mapping):
            if isinstance(source.get("plugins"), list) or isinstance(source.get("entries"), list):
                return _SourceDocument(
                    raw=dict(source), kind="inline", name="inline", base_dir=self.market_dir,
                    trusted_hosts=trusted,
                )
            if source.get("manifest") and isinstance(source["manifest"], Mapping):
                merged = dict(source["manifest"])
                for key in ("download", "archive", "download_url", "url"):
                    if key in source and key not in merged:
                        merged[key] = source[key]
                return _SourceDocument(
                    raw={"plugins": [merged]}, kind="inline", name="inline",
                    base_dir=self.market_dir, trusted_hosts=trusted,
                )
            if source.get("id") and (source.get("sha256") or source.get("checksum")):
                return _SourceDocument(
                    raw={"plugins": [dict(source)]}, kind="inline", name="inline",
                    base_dir=self.market_dir, trusted_hosts=trusted,
                )
            path_value = source.get("path") or source.get("file") or source.get("url")
            if path_value:
                return self._normalise_source(str(path_value))
            raise ManifestError("市场来源必须包含 path、url、plugins 或 manifest。")
        text = str(source or "").strip()
        if not text:
            raise ManifestError("市场来源不能为空。")
        if text.startswith(("http://", "https://")):
            body, final_url = _http_get_bytes(
                text,
                trusted_hosts=trusted,
                max_bytes=MAX_MANIFEST_BYTES,
                accept="application/json, application/*+json",
            )
            try:
                raw = json.loads(body.decode("utf-8-sig"))
            except (ValueError, UnicodeError) as exc:
                raise ManifestError("远程市场清单不是合法 JSON。", detail=exc.__class__.__name__) from exc
            if not isinstance(raw, (Mapping, list)):
                raise ManifestError("远程市场清单格式不合法。")
            return _SourceDocument(
                raw=raw, kind="url", name=urllib.parse.urlsplit(final_url).hostname or "remote",
                base_url=final_url, trusted_hosts=trusted,
            )
        if _has_parent_path_segment(text):
            raise UnsafePathError("市场清单路径不能包含路径穿越段。")
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = self.market_dir / path
        return self._load_source_path(path, trusted)

    def _load_source_path(self, path: Path, trusted: tuple[str, ...]) -> _SourceDocument:
        candidate = Path(path)
        if _has_parent_path_segment(candidate):
            raise UnsafePathError("市场清单路径不能包含路径穿越段。")
        if candidate.is_symlink() or not candidate.is_file():
            raise UnsafePathError("市场清单文件不存在、不是普通文件或为符号链接。")
        if not _is_within(candidate, self.market_dir):
            raise UnsafePathError("本地市场清单必须位于 studio-plugins/market 目录内。")
        try:
            raw = _read_json_object(candidate)
        except PluginStateError as exc:
            raise ManifestError("本地插件市场清单损坏或不可读。", detail=exc.detail) from exc
        return _SourceDocument(
            raw=raw,
            kind="local",
            name=candidate.name,
            base_dir=candidate.parent.resolve(),
            trusted_hosts=trusted,
        )

    def _resolve_archive_spec(
        self,
        manifest: PluginManifest,
        doc: _SourceDocument | None,
        *,
        override: str = "",
    ) -> _ArchiveSpec:
        text = str(override or manifest.download or "").strip()
        if not text:
            raise ManifestError("manifest 缺少安装包下载地址。")
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme.lower() in {"http", "https"}:
            if parsed.scheme.lower() != "https":
                raise UnsafePathError("插件下载地址只允许 HTTPS。")
            if not _host_is_trusted(parsed.hostname or "", doc.trusted_hosts if doc else self._trusted_hosts()):
                raise UnsafePathError("插件下载地址主机不在受信列表中。")
            _validate_remote_url(text, doc.trusted_hosts if doc else self._trusted_hosts())
            return _ArchiveSpec(kind="url", value=text)
        if parsed.scheme.lower() == "file":
            if doc is not None and doc.kind == "url":
                raise UnsafePathError("远程 manifest 不能引用本地 file URL。")
            if parsed.netloc not in {"", "localhost"}:
                raise UnsafePathError("本地 file URL 不合法。")
            local_value = urllib.parse.unquote(parsed.path)
        elif parsed.scheme:
            raise UnsafePathError("不支持插件安装包 URL scheme。")
        else:
            local_value = text
        if _has_parent_path_segment(local_value):
            raise UnsafePathError("插件安装包路径不能包含路径穿越段。")

        if doc is not None and doc.kind == "url":
            base_url = doc.base_url or ""
            resolved = urllib.parse.urljoin(base_url, text)
            _validate_remote_url(resolved, doc.trusted_hosts)
            return _ArchiveSpec(kind="url", value=resolved)

        base_dir = (doc.base_dir if doc and doc.base_dir else self.market_dir).resolve()
        candidate = Path(local_value).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if not _is_within(candidate, self.market_dir):
            raise UnsafePathError("本地插件安装包必须位于固定市场目录内。")
        if candidate.is_symlink() or not candidate.is_file():
            raise UnsafePathError("插件安装包不存在、不是普通文件或为符号链接。")
        return _ArchiveSpec(kind="local", value=str(candidate.resolve()))

    def _cache_manifest_entry(
        self,
        manifest: PluginManifest,
        doc: _SourceDocument,
        *,
        archive_override: str = "",
    ) -> dict[str, Any]:
        archive = self._resolve_archive_spec(manifest, doc, override=archive_override)
        payload = manifest.to_dict()
        payload["archive"] = archive.to_dict()
        payload["reserved"] = manifest.plugin_id in RESERVED_PLUGIN_IDS
        payload["source"] = {"kind": doc.kind, "name": doc.name}
        payload["error"] = ""
        return payload

    def refresh_market(self, source: Any = None) -> dict[str, Any]:
        with self.lock, _ExclusiveFileLock(self._file_lock_path):
            self._ensure_dirs()
            doc = self._normalise_source(source)
            entries: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            problems: list[dict[str, str]] = []
            for item in _catalog_entries_from_document(doc.raw):
                try:
                    merged = dict(item)
                    override = str(merged.pop("_archive_override", "") or "")
                    manifest = _manifest_from_dict(merged)
                    if manifest.plugin_id in seen_ids:
                        raise ManifestError("市场清单包含重复的插件 ID。")
                    seen_ids.add(manifest.plugin_id)
                    cached = self._cache_manifest_entry(manifest, doc, archive_override=override)
                    entries.append(cached)
                except PluginMarketError as exc:
                    # Keep the rest of the marketplace usable, but never hide
                    # the invalid item or install it.
                    item_id = sanitize_error_text(item.get("id") or "", limit=128) if isinstance(item, Mapping) else ""
                    problems.append({
                        "plugin_id": item_id,
                        "code": exc.code,
                        "error": sanitize_error_text(exc.message),
                    })
            if problems:
                # Validation is strict and all-or-nothing: never persist a
                # partially trusted catalog that a later refresh could use to
                # install an unreviewed archive.
                first = problems[0]
                raise ManifestError(
                    f"插件市场清单校验失败：{len(problems)} 个条目无效，未更新市场缓存。",
                    detail=first.get("error", ""),
                )
            cache = {
                "schema_version": MARKET_SCHEMA_VERSION,
                "refreshed_at": _now(),
                "source": {
                    "kind": doc.kind,
                    "name": doc.name,
                    "trusted_hosts_configured": bool(doc.trusted_hosts),
                },
                "entries": entries,
                "problems": problems,
            }
            _atomic_write_json(self.cache_path, cache)
            return self.market_status(cache=cache, message=f"插件市场已刷新：{len(entries)} 个有效条目。")

    # alias for direct service callers
    refresh = refresh_market

    def _read_cache(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {
                "schema_version": MARKET_SCHEMA_VERSION,
                "refreshed_at": "",
                "source": {"kind": "none", "name": "", "trusted_hosts_configured": bool(self._trusted_hosts())},
                "entries": [],
                "problems": [],
            }
        raw = _read_json_object(self.cache_path, max_bytes=8 * 1024 * 1024)
        try:
            cache_version = int(raw.get("schema_version") or 0)
        except (TypeError, ValueError) as exc:
            raise PluginStateError("插件市场缓存版本字段损坏，已停止读取。") from exc
        if cache_version > MARKET_SCHEMA_VERSION:
            raise PluginStateError("插件市场缓存来自更高版本，已停止读取以避免数据损坏。")
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise PluginStateError("插件市场缓存格式损坏，已停止读取。")
        return raw

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"schema_version": 1, "plugins": {}}
        raw = _read_json_object(self.registry_path, max_bytes=8 * 1024 * 1024)
        try:
            registry_version = int(raw.get("schema_version") or 0)
        except (TypeError, ValueError) as exc:
            raise PluginStateError("插件登记表版本字段损坏，已停止操作。") from exc
        if registry_version > 1:
            raise PluginStateError("插件登记表来自更高版本，已停止读取以避免数据损坏。")
        plugins = raw.get("plugins")
        if not isinstance(plugins, dict):
            raise PluginStateError("插件登记表格式损坏，已停止操作以保护现有数据。")
        for plugin_id, record in plugins.items():
            if not PLUGIN_ID_RE.fullmatch(str(plugin_id)) or not isinstance(record, dict):
                raise PluginStateError("插件登记表包含非法记录，已停止操作以保护现有数据。")
        return {"schema_version": 1, "plugins": plugins}

    def _write_registry(self, registry: Mapping[str, Any]) -> None:
        self._ensure_dirs()
        _atomic_write_json(self.registry_path, registry)

    # -- market status -------------------------------------------------
    def _installed_records(self) -> dict[str, dict[str, Any]]:
        registry = self._read_registry()
        records = registry.get("plugins") if isinstance(registry.get("plugins"), dict) else {}
        return {str(k): dict(v) for k, v in records.items() if isinstance(v, dict)}

    def _runtime_state(self) -> dict[str, dict[str, Any]]:
        provider = self._runtime_state_provider
        if provider is None:
            return {}
        try:
            raw = provider()
        except Exception:  # noqa: BLE001 - unknown runtime state is fail-closed
            return {}
        if not isinstance(raw, Mapping):
            return {}
        state: dict[str, dict[str, Any]] = {}
        for plugin_id, value in raw.items():
            if not PLUGIN_ID_RE.fullmatch(str(plugin_id or "")):
                continue
            if isinstance(value, Mapping):
                state[str(plugin_id)] = dict(value)
            elif isinstance(value, bool):
                state[str(plugin_id)] = {"enabled": value, "ready": value}
        return state

    @staticmethod
    def _dependency_state_for(
        manifest_record: Mapping[str, Any],
        catalog: Mapping[str, Mapping[str, Any]],
        installed: Mapping[str, Mapping[str, Any]],
        runtime_state: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        plugin_id = str(manifest_record.get("id") or manifest_record.get("plugin_id") or "")
        enabled: dict[str, bool] = {}
        ready: dict[str, bool] = {}
        for plugin_key, record in installed.items():
            runtime = runtime_state.get(plugin_key) or {}
            explicit_enabled = record.get("enabled") if "enabled" in record else runtime.get("enabled")
            explicit_ready = record.get("ready") if "ready" in record else runtime.get("ready")
            if explicit_ready is None:
                # A plugin that is enabled and reports no failed runtime state
                # may be considered ready; an absent runtime state is *not*
                # ready because dependencies must be proven.
                explicit_ready = bool(explicit_enabled) and not bool(record.get("error") or runtime.get("error"))
            enabled[plugin_key] = bool(explicit_enabled)
            ready[plugin_key] = bool(explicit_ready)
        for plugin_key, runtime in runtime_state.items():
            if plugin_key not in installed:
                continue
            if "enabled" in runtime:
                enabled[plugin_key] = _state_bool(runtime.get("enabled"))
            if "ready" in runtime:
                ready[plugin_key] = _state_bool(runtime.get("ready"))
        try:
            graph = validate_dependency_graph(
                manifest_record,
                catalog,
                installed=installed,
                enabled=enabled,
                ready=ready,
            )
        except ManifestError as exc:
            return {
                "ok": False,
                "ready": False,
                "status": "invalid_manifest",
                "plugin_id": plugin_id,
                "error": sanitize_error_text(exc.message),
                "install_plan": [],
                "missing_required": [],
            }
        return graph

    def _catalog_for_dependencies(
        self,
        *,
        cache: Mapping[str, Any] | None = None,
        extra_manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        raw = dict(cache) if isinstance(cache, Mapping) else self._read_cache()
        catalog: dict[str, dict[str, Any]] = {}
        for entry in raw.get("entries") or []:
            if not isinstance(entry, Mapping):
                continue
            plugin_id = str(entry.get("id") or entry.get("plugin_id") or "")
            if PLUGIN_ID_RE.fullmatch(plugin_id):
                catalog[plugin_id] = _manifest_for_record(entry)
        installed = self._installed_records()
        for plugin_id, record in installed.items():
            catalog.setdefault(plugin_id, _manifest_for_record(record))
        if isinstance(extra_manifest, Mapping):
            plugin_id = str(extra_manifest.get("id") or extra_manifest.get("plugin_id") or "")
            if PLUGIN_ID_RE.fullmatch(plugin_id):
                catalog[plugin_id] = _manifest_for_record(extra_manifest)
        return catalog

    def dependency_graph_for_manifest(
        self,
        manifest_record: Mapping[str, Any],
        *,
        cache: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Public/testable dependency graph for one catalog manifest."""

        with self.lock:
            catalog = self._catalog_for_dependencies(cache=cache, extra_manifest=manifest_record)
            installed = self._installed_records()
            return self._dependency_state_for(
                manifest_record,
                catalog,
                installed,
                self._runtime_state(),
            )

    def market_status(self, *, cache: Mapping[str, Any] | None = None, message: str = "") -> dict[str, Any]:
        with self.lock:
            raw = dict(cache) if isinstance(cache, Mapping) else self._read_cache()
            installed = self._installed_records()
            group_list: list[dict[str, Any]] = []
            group_error = ""
            try:
                group_list = self.groups.list_groups().get("groups", [])
            except PluginMarketError as exc:
                group_error = sanitize_error_text(exc.message)
            entries: list[dict[str, Any]] = []
            dependency_catalog = self._catalog_for_dependencies(cache=raw)
            runtime_state = self._runtime_state()
            dependency_graphs: dict[str, dict[str, Any]] = {}
            for item in raw.get("entries", []):
                if not isinstance(item, Mapping):
                    continue
                record = _public_catalog_entry(item)
                plugin_id = str(record.get("id") or "")
                current = installed.get(plugin_id)
                current_version = str(current.get("version") or "") if current else ""
                target_exists = self._plugin_target(plugin_id).exists() if PLUGIN_ID_RE.fullmatch(plugin_id) else False
                reserved = plugin_id in RESERVED_PLUGIN_IDS
                if reserved:
                    status = "reserved"
                elif current:
                    status = "update_available" if _version_is_newer(str(record.get("version") or ""), current_version) else "installed"
                elif target_exists:
                    status = "unregistered"
                else:
                    status = "available"
                dependency_graph = self._dependency_state_for(
                    record,
                    dependency_catalog,
                    installed,
                    runtime_state,
                )
                dependency_graphs[plugin_id] = dependency_graph
                if status in {"available", "update_available", "installed"} and not dependency_graph.get("ready"):
                    status = "dependency_unavailable"
                record.update({
                    "plugin_id": plugin_id,
                    "installed": bool(current),
                    "installed_version": current_version,
                    "update_available": status == "update_available",
                    "reserved": reserved,
                    "status": status,
                    "unregistered_directory": bool(target_exists and not current),
                    "dependency_status": dependency_graph,
                    "dependency_ready": bool(dependency_graph.get("ready")),
                    "install_plan": list(dependency_graph.get("install_plan") or []),
                })
                entries.append(record)
            groups_for_plugins: dict[str, list[str]] = {}
            for group in group_list:
                for plugin_id in group.get("plugin_ids", []):
                    groups_for_plugins.setdefault(str(plugin_id), []).append(str(group.get("id")))
            installed_items: list[dict[str, Any]] = []
            for plugin_id, record in sorted(installed.items()):
                item = _public_catalog_entry(record)
                item["plugin_id"] = plugin_id
                item["reserved"] = plugin_id in RESERVED_PLUGIN_IDS
                item["group_ids"] = groups_for_plugins.get(plugin_id, [])
                installed_items.append(item)
            if "qqbot_enhancer" not in installed and self._plugin_target("qqbot_enhancer").is_dir():
                installed_items.append({
                    "plugin_id": "qqbot_enhancer",
                    "id": "qqbot_enhancer",
                    "name": "QQ Bot 增强回复",
                    "version": "",
                    "reserved": True,
                    "market_managed": False,
                    "group_ids": groups_for_plugins.get("qqbot_enhancer", []),
                })
            return {
                "ok": True,
                "schema_version": MARKET_SCHEMA_VERSION,
                "entries": entries,
                "plugins": entries,
                "items": entries,
                "installed": installed_items,
                "groups": group_list,
                "count": len(entries),
                "refreshed_at": str(raw.get("refreshed_at") or ""),
                "source": dict(raw.get("source") or {}),
                "problems": list(raw.get("problems") or []),
                "trusted_hosts_configured": bool(self._trusted_hosts()),
                "group_error": group_error,
                "message": message or ("插件市场清单已读取。" if entries else "插件市场尚未刷新，请放入固定 catalog.json 或配置受信 URL 后刷新。"),
                "error": "",
            }

    get_market = market_status

    # -- plugin target / backups ---------------------------------------
    def _plugin_target(self, plugin_id: str) -> Path:
        valid = _normalise_plugin_id(plugin_id)
        target = self.plugin_root / valid
        if not _is_within(target, self.plugin_root):
            raise UnsafePathError("插件目录越界，已拒绝。")
        return target

    def _assert_safe_target(self, target: Path, *, must_exist: bool = False) -> None:
        root = self.plugin_root.resolve()
        if not _is_within(target, root):
            raise UnsafePathError("插件目录越界，已拒绝。")
        if target.exists() or target.is_symlink():
            if target.is_symlink():
                raise UnsafePathError("拒绝符号链接形式的插件代码目录。")
            try:
                if not stat.S_ISDIR(target.lstat().st_mode):
                    raise PluginConflictError("插件代码路径已存在且不是目录。")
            except OSError as exc:
                raise PluginStateError("无法检查插件代码目录。", detail=exc.__class__.__name__) from exc
        elif must_exist:
            raise PluginNotFoundError("插件代码目录不存在。")

    def _backup_plugin_code(self, plugin_id: str, target: Path, version: str = "") -> Path:
        self._ensure_dirs()
        target = Path(target)
        self._assert_safe_target(target, must_exist=True)
        folder = self.backup_root / plugin_id
        folder.mkdir(parents=True, exist_ok=True)
        chmod_best_effort(folder, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_version = re.sub(r"[^A-Za-z0-9._-]+", "_", str(version or "unknown"))[:48]
        backup = folder / f"{stamp}-{safe_version}-{secrets.token_hex(4)}.zip"
        tmp = backup.with_name(f".{backup.name}.{secrets.token_hex(4)}.tmp")
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for path in sorted(target.rglob("*")):
                    if path.is_symlink():
                        raise UnsafePathError("插件目录包含符号链接，无法安全备份；安装已中止。")
                    if path.is_dir():
                        continue
                    if not path.is_file():
                        raise UnsafePathError("插件目录包含特殊文件，无法安全备份；安装已中止。")
                    arcname = str(Path(plugin_id) / path.relative_to(target))
                    zf.write(path, arcname=arcname)
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp, backup)
            chmod_best_effort(backup, 0o600)
        except PluginMarketError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise PluginStateError("安装前备份插件代码失败，已中止安装。", detail=exc.__class__.__name__) from exc
        return backup

    def _materialize_archive(self, archive: _ArchiveSpec, expected_sha: str) -> Path:
        self._ensure_dirs()
        sha = _normalise_sha256(expected_sha)
        cached = self.download_root / f"{sha}.zip"
        if cached.exists():
            if not cached.is_symlink() and cached.is_file() and sha256_file(cached) == sha:
                return cached
            # Never use a stale/corrupt cache entry; re-materialise below.
        tmp = self.download_root / f".{sha}.{secrets.token_hex(6)}.tmp"
        try:
            if archive.kind == "local":
                source = Path(archive.value)
                if source.is_symlink() or not source.is_file() or not _is_within(source, self.market_dir):
                    raise UnsafePathError("本地插件安装包不在固定市场目录或不是普通文件。")
                if source.stat().st_size > self._max_archive_bytes:
                    raise ArchiveValidationError("插件安装包超过大小限制。")
                shutil.copyfile(source, tmp)
            elif archive.kind == "url":
                trusted = self._trusted_hosts()
                body, _final = _http_get_bytes(
                    archive.value,
                    trusted_hosts=trusted,
                    max_bytes=self._max_archive_bytes,
                    accept="application/zip, application/octet-stream",
                )
                tmp.write_bytes(body)
            else:
                raise UnsafePathError("不支持的插件安装包来源。")
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            actual = sha256_file(tmp)
            if not secrets.compare_digest(actual, sha):
                raise PluginConflictError("插件安装包 SHA256 校验失败，已拒绝安装。")
            os.replace(tmp, cached)
            chmod_best_effort(cached, 0o600)
            return cached
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def _operation_name(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "install": "install", "install_plugin": "install",
            "update": "update", "update_plugin": "update",
            "delete": "delete", "uninstall": "delete", "remove": "delete", "remove_plugin": "delete",
        }
        if text not in aliases:
            raise PluginMarketError("不支持插件操作；允许 install、update、delete。")
        return aliases[text]

    def _manifest_for_payload(self, payload: Mapping[str, Any]) -> tuple[PluginManifest, _SourceDocument | None, str]:
        raw_manifest = payload.get("manifest")
        if raw_manifest is not None:
            if not isinstance(raw_manifest, Mapping):
                raise ManifestError("manifest 必须是 JSON object。")
            merged = dict(raw_manifest)
            for key in ("sha256", "version", "download", "download_url", "archive", "url"):
                if payload.get(key) not in {None, ""} and key not in merged:
                    merged[key] = payload[key]
            manifest = _manifest_from_dict(merged)
            doc = _SourceDocument(
                raw={"plugins": []}, kind="inline", name="inline",
                base_dir=self.market_dir, trusted_hosts=self._trusted_hosts(),
            )
            override = str(payload.get("archive_path") or payload.get("download") or "")
            # For direct core use, an absolute archive within the fixed market
            # directory is allowed; the source check is repeated below.
            if payload.get("archive_path"):
                archive_text = str(payload["archive_path"])
                if _has_parent_path_segment(archive_text):
                    raise UnsafePathError("插件安装包路径不能包含路径穿越段。")
                candidate = Path(archive_text).expanduser()
                if not candidate.is_absolute() and doc.base_dir:
                    candidate = doc.base_dir / candidate
                if not _is_within(candidate, self.market_dir):
                    raise UnsafePathError("本地安装包必须位于固定市场目录内。")
                if candidate.is_symlink() or not candidate.is_file():
                    raise UnsafePathError("插件安装包不存在或为符号链接。")
                override = str(candidate.resolve())
            spec = self._resolve_archive_spec(manifest, doc, override=override)
            return manifest, doc, _canonical_json(spec.to_dict())
        plugin_id = str(payload.get("plugin_id") or payload.get("id") or "").strip()
        if not PLUGIN_ID_RE.fullmatch(plugin_id):
            raise ManifestError("缺少合法 plugin_id。")
        cache = self._read_cache()
        matches: list[Mapping[str, Any]] = []
        wanted_version = str(payload.get("version") or "").strip()
        wanted_sha = str(payload.get("sha256") or "").strip().lower()
        for entry in cache.get("entries", []):
            if not isinstance(entry, Mapping) or str(entry.get("id") or "") != plugin_id:
                continue
            if wanted_version and str(entry.get("version") or "") != wanted_version:
                continue
            if wanted_sha and str(entry.get("sha256") or "").lower() != wanted_sha:
                continue
            matches.append(entry)
        if not matches:
            raise PluginNotFoundError("市场清单中未找到该插件或指定版本，请先刷新市场。")
        chosen = dict(matches[0])
        raw_archive = chosen.get("archive")
        if not isinstance(raw_archive, Mapping):
            raise ManifestError("市场缓存缺少安装包信息，请刷新市场。")
        archive = _ArchiveSpec(kind=str(raw_archive.get("kind") or ""), value=str(raw_archive.get("value") or ""))
        manifest = _manifest_from_dict(chosen)
        # Local cache paths are revalidated by _materialize_archive.  Remote
        # hosts are revalidated here as well.
        if archive.kind == "url":
            _validate_remote_url(archive.value, self._trusted_hosts())
        elif archive.kind != "local":
            raise ManifestError("市场缓存中的安装包来源不合法。")
        return manifest, None, _canonical_json(archive.to_dict())

    def _build_operation_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        operation = self._operation_name(payload.get("operation") or payload.get("action"))
        plugin_id = str(payload.get("plugin_id") or payload.get("id") or "").strip()
        if operation == "delete":
            if not PLUGIN_ID_RE.fullmatch(plugin_id):
                raise ManifestError("删除插件需要合法 plugin_id。")
            if plugin_id in RESERVED_PLUGIN_IDS:
                raise PluginConflictError("qqbot_enhancer 是主回复/marker owner，插件市场不允许删除。")
            registry = self._installed_records()
            if plugin_id not in registry:
                raise PluginNotFoundError("该插件未由插件市场登记，拒绝按路径删除。")
            target = self._plugin_target(plugin_id)
            self._assert_safe_target(target)
            record = registry[plugin_id]
            return {
                "operation": "delete",
                "plugin_id": plugin_id,
                "version": str(record.get("version") or ""),
                "target_exists": target.exists(),
                "reserved": False,
                "requires_confirmation": True,
                "warnings": [] if target.exists() else ["登记目录已不存在；仍会清理登记和分组关系。"],
            }
        manifest, doc, archive_fingerprint = self._manifest_for_payload(payload)
        plugin_id = manifest.plugin_id
        if plugin_id in RESERVED_PLUGIN_IDS:
            raise PluginConflictError("qqbot_enhancer 是主回复/marker owner，插件市场不允许安装、覆盖或更新。")
        registry = self._installed_records()
        dependency_catalog = self._catalog_for_dependencies(extra_manifest=manifest.to_dict())
        dependency_graph = self._dependency_state_for(
            manifest.to_dict(),
            dependency_catalog,
            registry,
            self._runtime_state(),
        )
        if not dependency_graph.get("ready"):
            missing = [
                str(item.get("plugin_id") or "")
                for item in (
                    list(dependency_graph.get("missing_required") or [])
                    + list(dependency_graph.get("disabled_required") or [])
                    + list(dependency_graph.get("not_ready_required") or [])
                    + list(dependency_graph.get("incompatible") or [])
                )
                if isinstance(item, Mapping)
            ]
            summary = "、".join(dict.fromkeys(item for item in missing if item)) or "依赖图不满足"
            raise PluginDependencyError(
                f"插件依赖未满足，拒绝生成安装/更新计划：{summary}。",
                graph=dependency_graph,
                detail="需要先按拓扑顺序安装并启用/验证必需依赖。",
            )
        dependency_warnings = [
            f"可选依赖 {item.get('plugin_id')} 不可用（不会阻塞安装）。"
            for item in dependency_graph.get("missing_optional") or []
            if isinstance(item, Mapping)
        ]
        target = self._plugin_target(plugin_id)
        self._assert_safe_target(target)
        registered = plugin_id in registry
        exists = target.exists()
        if operation == "install":
            if registered or exists:
                raise PluginConflictError("插件已存在；请使用 update，或先人工确认未登记目录来源。")
            backup_required = False
            current_version = ""
        else:
            if not registered:
                raise PluginNotFoundError("只能更新已由插件市场登记的插件。")
            if not exists:
                raise PluginNotFoundError("已登记插件的代码目录不存在，无法更新。")
            backup_required = True
            current_version = str(registry[plugin_id].get("version") or "")
        if registry.get(plugin_id, {}).get("reserved") or plugin_id in RESERVED_PLUGIN_IDS:
            raise PluginConflictError("保留插件不能由市场更新。")
        archive_path = self._materialize_archive(
            _ArchiveSpec(**json.loads(archive_fingerprint)),
            manifest.sha256,
        )
        inspection = inspect_plugin_zip(
            archive_path,
            plugin_id,
            limits=_ZipLimits(
                max_archive_bytes=self._max_archive_bytes,
                max_extracted_bytes=self._max_extracted_bytes,
            ),
        )
        execution = {
            "manifest": manifest.to_dict(),
            "archive": json.loads(archive_fingerprint),
        }
        return {
            "operation": operation,
            "plugin_id": plugin_id,
            "name": manifest.name,
            "version": manifest.version,
            "sha256": manifest.sha256,
            "current_version": current_version,
            "backup_required": backup_required,
            "reserved": False,
            "file_count": inspection.file_count,
            "uncompressed_bytes": inspection.uncompressed_bytes,
            "schema_version": manifest.schema_version,
            "provides": [dict(item) for item in manifest.provides],
            "requires": [dict(item) for item in manifest.requires],
            "conflicts": [dict(item) for item in manifest.conflicts],
            "runtime_requirements": _deepcopy_json(dict(manifest.runtime_requirements)),
            "config_schema_version": manifest.config_schema_version,
            "data_schema_version": manifest.data_schema_version,
            "dependency_graph": dependency_graph,
            "dependency_plan": list(dependency_graph.get("install_plan") or []),
            "requires_confirmation": True,
            "warnings": dependency_warnings,
            "_execution": execution,
        }

    def preview_plan(self, token: str) -> dict[str, Any]:
        """Return a stored preview plan without consuming it.

        Dependency installation must not consume the plugin-operation preview:
        after the user consents to dependencies, the same preview token still
        installs the plugin.
        """
        token = str(token or "").strip()
        if not token:
            raise PluginMarketError("缺少预览 token。")
        with self.lock:
            preview = self._load_operation_preview(token)
            plan = preview.get("plan")
            if not isinstance(plan, Mapping):
                raise PluginMarketError("预览内容无效。")
            return self._public_plan(plan)

    @staticmethod
    def _public_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
        public = {key: value for key, value in plan.items() if not str(key).startswith("_")}
        return _deepcopy_json(public)

    def preview_operation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise PluginMarketError("插件操作预览请求必须是 JSON object。")
        with self.lock, _ExclusiveFileLock(self._file_lock_path):
            plan = self._build_operation_plan(payload)
            token = f"oprev-{secrets.token_urlsafe(24)}"
            expires_at = time.time() + PREVIEW_TTL_SECONDS
            stored = {"plan": plan}
            conn = self.groups._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO plugin_previews(token, kind, payload_json, payload_hash, created_at, expires_at, used_at) "
                    "VALUES(?, 'operation', ?, ?, ?, ?, NULL)",
                    (
                        token,
                        json.dumps(stored, ensure_ascii=False, sort_keys=True),
                        _fingerprint({"plan": plan}),
                        _now(),
                        expires_at,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        public_plan = self._public_plan(plan)
        return {
            "ok": True,
            "preview_token": token,
            "kind": "plugin-operation",
            "operation": plan["operation"],
            "plugin_id": plan["plugin_id"],
            "plan": public_plan,
            "warnings": public_plan.get("warnings", []),
            "requires_confirmation": True,
            "expires_at": _iso_from_epoch(expires_at),
            "message": "插件操作预览已生成；确认后才会修改插件代码目录。",
            "error": "",
        }

    @staticmethod
    def _public_operation(record: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            "id": str(record.get("id") or ""),
            "kind": str(record.get("kind") or ""),
            "operation": str(record.get("kind") or ""),
            "plugin_id": str(record.get("plugin_id") or ""),
            "status": str(record.get("status") or ""),
            "created_at": str(record.get("created_at") or ""),
            "updated_at": str(record.get("updated_at") or ""),
        }
        try:
            request = json.loads(str(record.get("request_json") or "{}"))
        except ValueError:
            request = {}
        try:
            outcome = json.loads(str(record.get("result_json") or "{}"))
        except ValueError:
            outcome = {}
        if isinstance(request, dict):
            result["version"] = str(request.get("version") or "")
            result["sha256"] = str(request.get("sha256") or "")
        if isinstance(outcome, dict):
            for key in (
                "message", "backup_name", "restart_required", "config_preserved",
                "plugin_data_preserved", "database_preserved", "sessions_preserved",
                "groups_cleaned", "removed_relations", "warnings",
            ):
                if key in outcome:
                    result[key] = outcome[key]
            if outcome.get("error"):
                result["error"] = sanitize_error_text(outcome["error"])
        return result

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        op_id = str(operation_id or "").strip()
        if not re.fullmatch(r"op-[0-9a-f]{16,64}", op_id):
            raise PluginNotFoundError("插件操作不存在。")
        conn = self.groups._connect()
        try:
            row = conn.execute("SELECT * FROM plugin_operations WHERE id = ?", (op_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise PluginNotFoundError("插件操作不存在。")
        return {"ok": True, "operation": self._public_operation(dict(row))}

    def _create_operation_record(
        self,
        operation_id: str,
        kind: str,
        plugin_id: str,
        request_hash: str,
        request_payload: Mapping[str, Any],
    ) -> None:
        now = _now()
        conn = self.groups._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO plugin_operations(id, kind, plugin_id, status, request_hash, request_json, result_json, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    operation_id,
                    kind,
                    plugin_id,
                    "running",
                    request_hash,
                    json.dumps(request_payload, ensure_ascii=False, sort_keys=True),
                    "{}",
                    now,
                    now,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _update_operation_record(self, operation_id: str, status: str, outcome: Mapping[str, Any]) -> None:
        conn = self.groups._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE plugin_operations SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (
                    status,
                    json.dumps(outcome, ensure_ascii=False, sort_keys=True),
                    _now(),
                    operation_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _lookup_operation_idempotency(
        self,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        if not key:
            return None
        conn = self.groups._connect()
        try:
            row = conn.execute(
                "SELECT scope, request_hash, response_json FROM plugin_idempotency WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        if str(row["scope"]) != "operation" or str(row["request_hash"]) != request_hash:
            raise IdempotencyConflictError("同一幂等键已用于不同的插件操作。")
        try:
            response = json.loads(str(row["response_json"]))
        except ValueError as exc:
            raise PluginStateError("插件操作幂等记录损坏，已停止操作。") from exc
        if not isinstance(response, dict):
            raise PluginStateError("插件操作幂等记录格式损坏，已停止操作。")
        response["idempotent"] = True
        return response

    def _lookup_operation_idempotency_by_key(self, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        conn = self.groups._connect()
        try:
            row = conn.execute(
                "SELECT scope, response_json FROM plugin_idempotency WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        if str(row["scope"]) != "operation":
            raise IdempotencyConflictError("该幂等键已用于不同类别的插件操作。")
        try:
            response = json.loads(str(row["response_json"]))
        except ValueError as exc:
            raise PluginStateError("插件操作幂等记录损坏，已停止操作。") from exc
        if not isinstance(response, dict):
            raise PluginStateError("插件操作幂等记录格式损坏，已停止操作。")
        response["idempotent"] = True
        return response

    @staticmethod
    def _cached_operation_matches_payload(
        cached: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> bool:
        operation = cached.get("operation") if isinstance(cached.get("operation"), Mapping) else {}
        requested_action = payload.get("operation") or payload.get("action")
        if requested_action:
            try:
                requested = PluginMarket._operation_name(requested_action)
            except PluginMarketError:
                return False
            if requested != str(operation.get("kind") or ""):
                return False
        requested_plugin = str(payload.get("plugin_id") or payload.get("id") or "").strip()
        if requested_plugin and requested_plugin != str(operation.get("plugin_id") or ""):
            return False
        requested_version = str(payload.get("version") or "").strip()
        if requested_version and requested_version != str(operation.get("version") or ""):
            return False
        requested_sha = str(payload.get("sha256") or "").strip().lower()
        if requested_sha and requested_sha != str(operation.get("sha256") or "").lower():
            return False
        return True

    def _store_operation_idempotency(self, key: str, request_hash: str, response: Mapping[str, Any]) -> None:
        if not key:
            return
        conn = self.groups._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO plugin_idempotency(key, scope, request_hash, response_json, created_at) "
                "VALUES(?, 'operation', ?, ?, ?)",
                (
                    key,
                    request_hash,
                    json.dumps(dict(response), ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _load_operation_preview(self, token: str) -> dict[str, Any]:
        """Load a preview without consuming it.

        Idempotent retries must be able to locate the original plan and return
        the cached response even after the one-time preview token was consumed.
        """

        conn = self.groups._connect()
        try:
            row = conn.execute(
                "SELECT kind, payload_json, expires_at, used_at FROM plugin_previews WHERE token = ?",
                (token,),
            ).fetchone()
            if row is None or str(row["kind"]) != "operation":
                raise PreviewExpiredError("插件操作预览不存在或已过期，请重新预览。")
            if float(row["expires_at"] or 0) < time.time():
                raise PreviewExpiredError("插件操作预览已过期，请重新预览。")
            raw = json.loads(str(row["payload_json"]))
            plan = raw.get("plan") if isinstance(raw, dict) else None
            if not isinstance(plan, dict):
                raise PluginStateError("插件操作预览记录损坏，已停止操作。")
            return {
                "plan": plan,
                "request_hash": _fingerprint({"plan": plan}),
                "used": bool(row["used_at"]),
            }
        except Exception:
            raise
        finally:
            conn.close()

    def _mark_operation_preview_consumed(self, token: str) -> None:
        conn = self.groups._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE plugin_previews SET used_at = ? WHERE token = ? AND used_at IS NULL",
                (_now(), token),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise PreviewExpiredError("该插件操作预览已被使用，请重新预览。")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.DatabaseError:
                pass
            raise
        finally:
            conn.close()

    @staticmethod
    def _plan_matches_payload(plan: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
        requested_operation = payload.get("operation") or payload.get("action")
        if requested_operation:
            try:
                if PluginMarket._operation_name(requested_operation) != str(plan.get("operation") or ""):
                    return False
            except PluginMarketError:
                return False
        requested_plugin = str(payload.get("plugin_id") or payload.get("id") or "").strip()
        if requested_plugin and requested_plugin != str(plan.get("plugin_id") or ""):
            return False
        requested_sha = str(payload.get("sha256") or "").strip().lower()
        if requested_sha and requested_sha != str(plan.get("sha256") or "").lower():
            return False
        requested_version = str(payload.get("version") or "").strip()
        if requested_version and requested_version != str(plan.get("version") or ""):
            return False
        return True

    def _plugin_target_snapshot(self, plugin_id: str) -> dict[str, bool]:
        target = self._plugin_target(plugin_id)
        return {
            "config_preserved": (self.data_dir / "data" / "astrbot" / "config" / f"{plugin_id}_config.json").exists(),
            "plugin_data_preserved": (self.data_dir / "data" / "astrbot" / "plugin_data" / plugin_id).exists(),
            "database_preserved": (self.data_dir / "data" / "astrbot" / "data_v4.db").exists(),
            "sessions_preserved": (self.data_dir / "data" / "astrbot" / "sessions").exists(),
            "target_existed": target.exists(),
        }

    def _execute_install(self, plan: Mapping[str, Any], operation_id: str) -> dict[str, Any]:
        operation = str(plan.get("operation") or "")
        plugin_id = _normalise_plugin_id(plan.get("plugin_id"))
        execution = plan.get("_execution")
        if not isinstance(execution, Mapping):
            raise PluginStateError("插件操作计划缺少执行信息，请重新预览。")
        manifest = _manifest_from_dict(execution.get("manifest") or {})
        archive_data = execution.get("archive") if isinstance(execution.get("archive"), Mapping) else {}
        archive = _ArchiveSpec(kind=str(archive_data.get("kind") or ""), value=str(archive_data.get("value") or ""))
        target = self._plugin_target(plugin_id)
        self._assert_safe_target(target)
        registry = self._read_registry()
        records = registry["plugins"]
        registered = plugin_id in records
        exists = target.exists()
        if operation == "install" and (registered or exists):
            raise PluginConflictError("插件在确认期间已被安装，已拒绝覆盖。")
        if operation == "update" and (not registered or not exists):
            raise PluginNotFoundError("插件在确认期间已变化，无法更新。")
        if plugin_id in RESERVED_PLUGIN_IDS:
            raise PluginConflictError("qqbot_enhancer 是保留插件，市场不能修改。")
        archive_path = self._materialize_archive(archive, manifest.sha256)
        inspection = inspect_plugin_zip(
            archive_path,
            plugin_id,
            limits=_ZipLimits(
                max_archive_bytes=self._max_archive_bytes,
                max_extracted_bytes=self._max_extracted_bytes,
            ),
        )
        operation_stage = self.staging_root / operation_id
        if operation_stage.exists():
            shutil.rmtree(operation_stage, ignore_errors=True)
        extracted_parent = operation_stage / "extracted"
        rollback_path = operation_stage / "previous"
        operation_stage.mkdir(parents=True, exist_ok=True)
        backup_path: Path | None = None
        if exists:
            backup_path = self._backup_plugin_code(plugin_id, target, str(records.get(plugin_id, {}).get("version") or ""))
        try:
            staged_root = extract_plugin_zip(
                archive_path,
                extracted_parent,
                plugin_id,
                limits=_ZipLimits(
                    max_archive_bytes=self._max_archive_bytes,
                    max_extracted_bytes=self._max_extracted_bytes,
                ),
            )
            # A final check guards against a malformed staging tree.
            for path in staged_root.rglob("*"):
                if path.is_symlink():
                    raise UnsafePathError("插件暂存目录包含符号链接，已拒绝。")
            if exists:
                os.replace(target, rollback_path)
            placed = False
            try:
                self.plugin_root.mkdir(parents=True, exist_ok=True)
                os.replace(staged_root, target)
                placed = True
                now = _now()
                updated_records = dict(records)
                updated_records[plugin_id] = {
                    "id": plugin_id,
                    "plugin_id": plugin_id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "sha256": manifest.sha256,
                    "installed_at": str(records.get(plugin_id, {}).get("installed_at") or now),
                    "updated_at": now,
                    "reserved": False,
                    "manifest": manifest.to_dict(),
                }
                new_registry = {"schema_version": 1, "plugins": updated_records}
                self._write_registry(new_registry)
                if rollback_path.exists():
                    shutil.rmtree(rollback_path, ignore_errors=True)
            except Exception:
                if placed:
                    try:
                        if rollback_path.exists():
                            os.replace(rollback_path, target)
                        else:
                            shutil.rmtree(target, ignore_errors=True)
                    except OSError:
                        pass
                elif rollback_path.exists():
                    try:
                        os.replace(rollback_path, target)
                    except OSError:
                        pass
                raise
        finally:
            # Keep the backup archive, but never leave staging code around.
            shutil.rmtree(operation_stage, ignore_errors=True)
        return {
            "message": "插件已安装。" if operation == "install" else "插件已更新。",
            "version": manifest.version,
            "sha256": manifest.sha256,
            "file_count": inspection.file_count,
            "backup_name": str(backup_path.relative_to(self.backup_root)) if backup_path else "",
            "restart_required": True,
            "config_preserved": True,
            "plugin_data_preserved": True,
            "database_preserved": True,
            "sessions_preserved": True,
            "warnings": [],
        }

    def _execute_delete(self, plan: Mapping[str, Any], operation_id: str) -> dict[str, Any]:
        plugin_id = _normalise_plugin_id(plan.get("plugin_id"))
        target = self._plugin_target(plugin_id)
        if plugin_id in RESERVED_PLUGIN_IDS:
            raise PluginConflictError("qqbot_enhancer 是主插件，市场不允许删除。")
        registry = self._read_registry()
        records = registry["plugins"]
        if plugin_id not in records:
            raise PluginNotFoundError("该插件未由插件市场登记，拒绝删除。")
        self._assert_safe_target(target)
        # Warm the group database before touching code so a corrupt state file
        # cannot leave plugin code removed without relationship cleanup.
        _ = self.groups.list_groups()
        snapshot = self._plugin_target_snapshot(plugin_id)
        quarantine = self.staging_root / f"delete-{operation_id}"
        if quarantine.exists():
            shutil.rmtree(quarantine, ignore_errors=True)
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        moved = False
        registry_written = False
        try:
            if target.exists():
                os.replace(target, quarantine)
                moved = True
            updated_records = dict(records)
            updated_records.pop(plugin_id, None)
            self._write_registry({"schema_version": 1, "plugins": updated_records})
            registry_written = True
            cleanup = self.groups.remove_plugin_everywhere(plugin_id)
            # The code directory has already been moved out of the plugin root,
            # so failure to clean the quarantine must not restore a plugin that
            # is no longer registered.
            if moved:
                shutil.rmtree(quarantine, ignore_errors=True)
        except Exception:
            if registry_written:
                try:
                    self._write_registry(registry)
                except Exception:
                    pass
            if moved and quarantine.exists() and not target.exists():
                try:
                    os.replace(quarantine, target)
                except OSError:
                    pass
            raise
        finally:
            if quarantine.exists():
                shutil.rmtree(quarantine, ignore_errors=True)
        return {
            "message": "插件代码已删除；配置、plugin_data、数据库和会话均保留。",
            "backup_name": "",
            "restart_required": True,
            **snapshot,
            "groups_cleaned": True,
            "removed_relations": int(cleanup.get("removed_relations") or 0),
            "warnings": [] if plan.get("target_exists", True) else ["登记目录原本已不存在，已清理残留登记。"],
        }

    def execute_operation(self, payload: Mapping[str, Any], *, wait: bool = True) -> dict[str, Any]:
        if not self._operation_gate.acquire(blocking=wait):
            raise PluginOperationBusy("已有插件操作正在进行，请等待完成后再试。")
        try:
            return self._execute_operation(payload)
        finally:
            self._operation_gate.release()

    def _execute_operation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise PluginMarketError("插件操作请求必须是 JSON object。")
        if payload.get("confirm") is not True:
            raise ConfirmationRequiredError("插件操作需要显式确认（confirm=true）。")
        idem_key = str(payload.get("idempotency_key") or "").strip()
        if idem_key and (len(idem_key) > MAX_IDEMPOTENCY_KEY_LENGTH or CONTROL_RE.search(idem_key)):
            raise PluginMarketError("idempotency_key 不合法。")
        token = str(payload.get("preview_token") or "").strip()
        with self.lock, _ExclusiveFileLock(self._file_lock_path):
            # Fast idempotent replay works even when the original one-time
            # preview was already consumed, or when a direct (tokenless)
            # operation is retried after its plugin directory already exists.
            cached_response = self._lookup_operation_idempotency_by_key(idem_key)
            if cached_response is not None:
                if not self._cached_operation_matches_payload(cached_response, payload):
                    raise IdempotencyConflictError("同一幂等键已用于不同的插件操作。")
                return cached_response
            preview: dict[str, Any] | None = None
            plan: dict[str, Any]
            if token:
                preview = self._load_operation_preview(token)
                plan = dict(preview["plan"])
            else:
                plan = self._build_operation_plan(payload)
            if not self._plan_matches_payload(plan, payload):
                raise IdempotencyConflictError("确认请求与预览的插件操作不一致，已拒绝。")
            request_hash = _fingerprint({
                "operation": plan.get("operation"),
                "plugin_id": plan.get("plugin_id"),
                "version": plan.get("version"),
                "sha256": plan.get("sha256"),
                "archive": (plan.get("_execution") or {}).get("archive") if isinstance(plan.get("_execution"), Mapping) else None,
            })
            existing = self._lookup_operation_idempotency(idem_key, request_hash)
            if existing is not None:
                return existing
            if preview is not None and preview.get("used"):
                raise PreviewExpiredError("该插件操作预览已被使用，请重新预览。")
            operation_id = f"op-{secrets.token_hex(12)}"
            self._create_operation_record(operation_id, str(plan["operation"]), str(plan["plugin_id"]), request_hash, plan)
            if token:
                try:
                    self._mark_operation_preview_consumed(token)
                except PluginMarketError:
                    # The operation record is immutable evidence; mark it
                    # failed rather than leaving a permanently running row.
                    self._update_operation_record(
                        operation_id,
                        "failed",
                        {"ok": False, "error": "插件操作预览已被使用。"},
                    )
                    raise
            try:
                if plan.get("operation") in {"install", "update"}:
                    outcome = self._execute_install(plan, operation_id)
                elif plan.get("operation") == "delete":
                    outcome = self._execute_delete(plan, operation_id)
                else:
                    raise PluginMarketError("不支持的插件操作。")
                public_outcome = dict(outcome)
                public_outcome["ok"] = True
                self._update_operation_record(operation_id, "completed", public_outcome)
            except Exception as exc:
                if isinstance(exc, PermissionError):
                    logger.exception("plugin operation permission failure: %s", exc)
                    safe_message = sanitize_error_text(
                        f"插件目录不可写，请先修复实例插件目录权限：{exc}"
                    )
                elif isinstance(exc, PluginMarketError):
                    safe_message = sanitize_error_text(exc.message)
                else:
                    logger.exception("plugin operation failed")
                    safe_message = "插件操作执行失败，详情请查看管理服务终端日志。"
                failed_outcome = {"ok": False, "error": safe_message, "message": safe_message}
                try:
                    self._update_operation_record(operation_id, "failed", failed_outcome)
                except Exception:
                    pass
                operation = self.get_operation(operation_id)["operation"]
                if isinstance(exc, PluginMarketError):
                    raise PluginOperationFailed(safe_message, operation=operation) from exc
                raise PluginOperationFailed(safe_message, operation=operation) from exc
            response = {
                "ok": True,
                "idempotent": False,
                "operation": self.get_operation(operation_id)["operation"],
                "message": outcome.get("message", "插件操作已完成。"),
                "error": "",
            }
            self._store_operation_idempotency(idem_key, request_hash, response)
            return response

    run_operation = execute_operation
    apply_operation = execute_operation
    preview = preview_operation
    get_plugin_operation = get_operation

    def _run_confirmed_operation(
        self,
        operation: str,
        payload: Mapping[str, Any] | str,
        *,
        archive_path: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if isinstance(payload, str):
            request: dict[str, Any] = {"plugin_id": payload}
        elif isinstance(payload, Mapping):
            request = dict(payload)
        else:
            raise PluginMarketError("插件操作参数必须是 plugin_id 或 JSON object。")
        request.pop("confirm", None)
        request.pop("preview_token", None)
        request.pop("idempotency_key", None)
        request["operation"] = operation
        if archive_path:
            request["archive_path"] = archive_path
        if (
            "manifest" not in request
            and request.get("sha256")
            and (request.get("download") or request.get("download_url") or request.get("archive"))
        ):
            request = {"operation": operation, "manifest": request}
            if archive_path:
                request["archive_path"] = archive_path
        preview = self.preview_operation(request)
        execute_payload = {
            **request,
            "preview_token": preview["preview_token"],
            "confirm": True,
        }
        if idempotency_key:
            execute_payload["idempotency_key"] = idempotency_key
        return self.execute_operation(execute_payload)

    def install_plugin(
        self,
        payload: Mapping[str, Any] | str,
        *,
        archive_path: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return self._run_confirmed_operation(
            "install", payload, archive_path=archive_path, idempotency_key=idempotency_key
        )

    def update_plugin(
        self,
        payload: Mapping[str, Any] | str,
        *,
        archive_path: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return self._run_confirmed_operation(
            "update", payload, archive_path=archive_path, idempotency_key=idempotency_key
        )

    def delete_plugin(self, payload: Mapping[str, Any] | str, *, idempotency_key: str = "") -> dict[str, Any]:
        if isinstance(payload, Mapping):
            payload = payload.get("plugin_id") or payload.get("id") or ""
        return self._run_confirmed_operation(
            "delete", str(payload), idempotency_key=idempotency_key
        )

    # -- direct group helpers used by DeployManager/API -----------------
    def list_plugin_groups(self) -> dict[str, Any]:
        return self.groups.list_groups()

    def preview_plugin_groups(self, payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self.groups.preview(payload)

    def apply_plugin_groups(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.groups.apply(payload)

    def execute_plugin_group_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.groups.execute_action(payload)


# Descriptive aliases used by integration code and future callers.
PluginMarketManager = PluginMarket
PluginStateStore = PluginGroupStore


__all__ = [
    "ArchiveValidationError",
    "ConfirmationRequiredError",
    "GroupError",
    "GroupNotFoundError",
    "GroupStore",
    "IdempotencyConflictError",
    "ManifestError",
    "MarketError",
    "PluginConflictError",
    "PluginGroupStore",
    "PluginManifest",
    "PluginMarket",
    "PluginMarketError",
    "PluginMarketManager",
    "PluginNotFoundError",
    "PluginOperationFailed",
    "PluginStateError",
    "PluginStateStore",
    "PreviewExpiredError",
    "RESERVED_PLUGIN_IDS",
    "UnsafePathError",
    "ZipInspection",
    "extract_plugin_zip",
    "inspect_plugin_zip",
    "load_manifest",
    "sanitize_error_text",
    "sha256_file",
    "validate_manifest",
    "verify_sha256",
]
