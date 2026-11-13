"""Fixed-RPC control bridge between Studio and ``qqbot_safe_tools``.

Transport contract (v1, exact field names)
===========================================

Every request is one canonical JSON object with these fields::

    version        integer protocol version (1)
    instance_id    Studio instance id
    actor_id       authenticated Studio actor id
    scope          list of business/admin scope tokens (string accepted)
    method         one name from FIXED_RPC_METHODS (never arbitrary)
    request_id     caller-generated bounded id
    nonce          caller-generated random replay guard
    timestamp      integer epoch seconds
    body           bounded JSON object with method arguments
    body_digest    lowercase SHA-256 of canonical JSON(body)
    signature      HMAC-SHA256(secret, canonical JSON(fields without signature))

The receiver verifies the signature, body digest, timestamp window, nonce
replay, instance/actor/scope allowlists, and the fixed method table before it
calls a registered handler.  There is deliberately no URL, path, shell, SQL,
expression, or dynamic method parameter in the envelope.

Transports
==========

* ``HttpControlTransport`` talks to a fixed loopback endpoint (default
  ``127.0.0.1:6198/rpc``).  The host must literally be loopback; callers
  cannot supply an arbitrary URL.
* ``DockerExecTransport`` runs a fixed ``docker exec -i <container> python3 -c
  <script>`` command.  The script only forwards stdin JSON to the container's
  loopback control endpoint and prints the response JSON.  No shell is used.

Secrets are stored per instance at::

    <instance>/data/astrbot/plugin_data/qqbot_safe_tools/control.secret

or in the file named by ``QQBOT_PLUGIN_CONTROL_SECRET_FILE``.  The file is
created atomically with mode 0600 and is never returned through any status API.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping

CONTROL_PROTOCOL_VERSION = 1
CONTROL_DEFAULT_HOST = "127.0.0.1"
CONTROL_DEFAULT_PORT = 6198
CONTROL_DEFAULT_PATH = "/rpc"
CONTROL_ALLOWED_PATHS = frozenset({"/rpc", "/control", "/v1/control"})
CONTROL_HEALTH_PATH = "/healthz"
CONTROL_MAX_BODY_BYTES = 256 * 1024
CONTROL_MAX_RESPONSE_BYTES = 512 * 1024
CONTROL_MAX_CLOCK_SKEW_SECONDS = 60
CONTROL_DEFAULT_MAX_AGE_SECONDS = 300
CONTROL_SECRET_BYTES = 32
CONTROL_SECRET_MIN_LENGTH = 32
CONTROL_SECRET_MAX_BYTES = 4096
CONTROL_SECRET_RELATIVE_PATH = "data/astrbot/plugin_data/qqbot_safe_tools/control.secret"
CONTROL_SECRET_ENV = "QQBOT_PLUGIN_CONTROL_SECRET_FILE"
CONTROL_ENABLED_ENV = "QQBOT_PLUGIN_CONTROL_ENABLED"

BUSINESS_SCOPES: tuple[str, ...] = (
    "global",
    "persona",
    "conversation",
    "group",
    "user",
    "admin",
)
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_REQUEST_FIELDS = frozenset(
    {
        "version",
        "instance_id",
        "actor_id",
        "scope",
        "method",
        "request_id",
        "nonce",
        "timestamp",
        "body",
        "body_digest",
        "signature",
    }
)
_PLUGIN_REQUEST_FIELDS = frozenset(
    {
        "version",
        "instance_id",
        "actor_id",
        "scope",
        "method",
        "request_id",
        "nonce",
        "timestamp",
        "body",
        "digest",
        "hmac",
    }
)


class ControlError(RuntimeError):
    """Base class for safe control-bridge errors (no secrets included)."""

    http_status = 400
    code = "control_error"

    def __init__(self, message: str = "控制桥请求失败。", *, detail: str = "") -> None:
        super().__init__(str(message))
        self.message = str(message)
        self.detail = str(detail or "")

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


class ControlAuthError(ControlError):
    http_status = 401
    code = "control_auth_failed"


class ControlReplayError(ControlError):
    http_status = 409
    code = "control_replay"


class ControlExpiredError(ControlError):
    http_status = 401
    code = "control_expired"


class ControlMethodError(ControlError):
    http_status = 400
    code = "control_method_not_allowed"


class ControlScopeError(ControlError):
    http_status = 403
    code = "control_scope_denied"


class ControlInstanceError(ControlError):
    http_status = 403
    code = "control_instance_denied"


class ControlActorError(ControlError):
    http_status = 403
    code = "control_actor_denied"


class ControlPayloadError(ControlAuthError):
    http_status = 400
    code = "control_invalid_payload"


class ControlUnavailable(ControlError):
    http_status = 503
    code = "bridge_unavailable"


class ControlSecretError(ControlError):
    http_status = 503
    code = "control_secret_unavailable"


class ControlResponseError(ControlError):
    http_status = 502
    code = "control_invalid_response"


@dataclass(frozen=True)
class RpcMethod:
    """One fixed RPC method from the migration contract."""

    name: str
    provider: str
    access: str  # read / write / confirm
    description: str = ""

    @property
    def requires_admin_scope(self) -> bool:
        return self.access in {"write", "confirm"}


# The fixed method table comes from contract section 4.4 (and the admin subset
# in section 14.1).  No method name is constructed from request input.
FIXED_RPC_METHODS: dict[str, RpcMethod] = {}


def _register(name: str, provider: str, access: str, description: str = "") -> None:
    if name in FIXED_RPC_METHODS:
        raise RuntimeError(f"duplicate fixed RPC method: {name}")
    FIXED_RPC_METHODS[name] = RpcMethod(name, provider, access, description)


for _name, _provider, _access in (
    ("reply.status", "core_reply", "read"),
    ("reply.configure_plan", "core_reply", "write"),
    ("reply.configure_commit", "core_reply", "confirm"),
    ("reply.turn_run", "core_reply", "write"),
    ("reply.cancel_turn", "core_reply", "write"),
    ("tool.catalog", "safe_tools", "read"),
    ("policy.snapshot", "safe_tools", "read"),
    ("policy.scope", "safe_tools", "read"),
    ("policy.audit", "safe_tools", "read"),
    ("policy.templates", "safe_tools", "read"),
    ("policy.param_schema", "safe_tools", "read"),
    ("policy.set", "safe_tools", "write"),
    ("policy.reset", "safe_tools", "write"),
    ("policy.scope_reset", "safe_tools", "write"),
    ("policy.template_set", "safe_tools", "write"),
    ("policy.template_delete", "safe_tools", "write"),
    ("policy.template_apply", "safe_tools", "write"),
    ("aux_budget.get", "safe_tools", "read"),
    ("aux_budget.set", "safe_tools", "write"),
    ("aux_budget.usage", "safe_tools", "read"),
    ("aux_budget.feature_set", "safe_tools", "write"),
    ("aux_budget.cycle_reset", "safe_tools", "write"),
    ("tool.process_model_output", "safe_tools", "write"),
    ("tool.dispatch", "safe_tools", "write"),
    ("confirmation.preview", "safe_tools", "write"),
    ("confirmation.confirm", "safe_tools", "confirm"),
    ("confirmation.cancel", "safe_tools", "write"),
    ("tool.operation_status", "safe_tools", "read"),
    ("web.read", "web_reader", "read"),
    ("web.next_page", "web_reader", "read"),
    ("image.search", "image_search", "read"),
    ("image.next_page", "image_search", "read"),
    ("image.collect_plan", "image_search", "write"),
    ("food.query", "food", "read"),
    ("food.locations", "food", "read"),
    ("food.tags", "food", "read"),
    ("food.set_location", "food", "write"),
    ("food.change_tags", "food", "write"),
    ("food.draft_create", "food", "write"),
    ("food.draft_preview", "food", "read"),
    ("food.confirm", "food", "confirm"),
    ("food.cancel", "food", "write"),
    ("food.operation_status", "food", "read"),
    ("sticker.collect_plan", "stickers", "write"),
    ("sticker.collect_commit", "stickers", "confirm"),
    ("sticker.category_list", "stickers", "read"),
    ("sticker.category_create", "stickers", "write"),
    ("sticker.category_rename", "stickers", "write"),
    ("sticker.category_remove", "stickers", "confirm"),
    ("sticker.categorize", "stickers", "write"),
    ("sticker.search", "stickers", "read"),
    ("sticker.send_plan", "stickers", "read"),
    ("sticker.remove", "stickers", "confirm"),
    ("sticker.index_rebuild_plan", "stickers", "write"),
    ("sticker.index_commit", "stickers", "confirm"),
    ("memory.read", "memory_commands", "read"),
    ("memory.search", "memory_commands", "read"),
    ("memory.add", "memory_commands", "write"),
    ("memory.preview_clear", "memory_commands", "write"),
    ("memory.confirm_clear", "memory_commands", "confirm"),
    ("session.open", "memory_commands", "write"),
    ("session.read", "memory_commands", "read"),
    ("session.append", "memory_commands", "write"),
    ("session.reset", "memory_commands", "confirm"),
    ("session.switch", "memory_commands", "write"),
    ("persona.list", "memory_commands", "read"),
    ("persona.bind", "memory_commands", "write"),
    ("persona.switch", "memory_commands", "write"),
):
    _register(_name, _provider, _access)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _secret_bytes(secret: str | bytes) -> bytes:
    if isinstance(secret, bytes):
        raw = bytes(secret)
    else:
        text = str(secret or "").strip()
        if not text:
            raise ControlSecretError("控制桥秘密为空，已拒绝。")
        if len(text) == 64 and re.fullmatch(r"[0-9a-fA-F]{64}", text):
            try:
                raw = bytes.fromhex(text)
            except ValueError as exc:  # pragma: no cover - defensive
                raise ControlSecretError("控制桥秘密格式不合法。") from exc
        else:
            raw = text.encode("utf-8")
    if len(raw) < CONTROL_SECRET_MIN_LENGTH:
        raise ControlSecretError("控制桥秘密长度不足，已拒绝。")
    return raw


def generate_control_secret() -> str:
    """Return a new 64-character URL-safe secret text.

    The plugin-side control server treats the file contents as text; keeping
    the host-side representation identical avoids HMAC key mismatches caused
    by interpreting a 64-character hex value as 32 raw bytes.
    """

    return secrets.token_urlsafe(48)


def _bounded_string(value: Any, name: str, *, max_len: int = 128, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ControlPayloadError(f"{name} 不能为空。")
    if len(text) > max_len or _CONTROL_RE.search(text):
        raise ControlPayloadError(f"{name} 不合法。")
    return text


def _normalise_scope(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise ControlScopeError("scope 必须是字符串或字符串数组。")
    result: list[str] = []
    for item in items:
        text = item.strip().lower()
        if not text or len(text) > 64 or not _SCOPE_RE.fullmatch(text):
            raise ControlScopeError("scope 不合法。")
        if text not in result:
            result.append(text)
    if not result:
        raise ControlScopeError("scope 不能为空。")
    return tuple(result)


def _bounded_json(value: Any, *, max_bytes: int = CONTROL_MAX_BODY_BYTES, where: str = "body") -> Any:
    """Validate a bounded JSON value and return a JSON-safe deep copy."""

    def walk(item: Any, depth: int, counter: list[int]) -> Any:
        if depth > 12:
            raise ControlPayloadError(f"{where} 嵌套过深。")
        counter[0] += 1
        if counter[0] > 5000:
            raise ControlPayloadError(f"{where} 条目过多。")
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise ControlPayloadError(f"{where} 包含非法数值。")
            return item
        if isinstance(item, str):
            if len(item) > 65536 or _CONTROL_RE.search(item):
                raise ControlPayloadError(f"{where} 字符串过长或包含控制字符。")
            return item
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ControlPayloadError(f"{where} 的键必须是字符串。")
                if len(key) > 256 or _CONTROL_RE.search(key):
                    raise ControlPayloadError(f"{where} 的键不合法。")
                result[key] = walk(child, depth + 1, counter)
            return result
        if isinstance(item, (list, tuple)):
            if len(item) > 1000:
                raise ControlPayloadError(f"{where} 数组过大。")
            return [walk(child, depth + 1, counter) for child in item]
        raise ControlPayloadError(f"{where} 必须是 JSON 兼容类型。")

    copied = walk(value, 0, [0])
    encoded = _canonical_json(copied)
    if len(encoded) > max_bytes:
        raise ControlPayloadError(f"{where} 超过大小限制。")
    return copied


def _parse_json_object(raw: bytes, *, max_bytes: int, where: str = "payload") -> dict[str, Any]:
    if not isinstance(raw, (bytes, bytearray)):
        raise ControlPayloadError("控制桥请求必须是字节串。")
    if len(raw) > max_bytes:
        raise ControlPayloadError("控制桥请求超过大小限制。")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlPayloadError("控制桥请求不是 UTF-8。") from exc

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ControlPayloadError("控制桥请求包含重复字段。")
            result[key] = item
        return result

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates)
    except ControlError:
        raise
    except ValueError as exc:
        raise ControlPayloadError("控制桥请求不是合法 JSON。", detail=exc.__class__.__name__) from exc
    if not isinstance(value, dict):
        raise ControlPayloadError("控制桥请求顶层必须是 JSON object。")
    return value


def _sign(secret: str | bytes, request_without_signature: Mapping[str, Any]) -> str:
    key = _secret_bytes(secret)
    return hmac.new(key, _canonical_json(dict(request_without_signature)), hashlib.sha256).hexdigest()


def _normalise_body_digest(body: Any) -> str:
    return _sha256_hex(_canonical_json(body))


def build_request(
    secret: str | bytes,
    *,
    instance_id: str,
    actor_id: str,
    scope: Iterable[str] | str,
    method: str,
    body: Mapping[str, Any] | None = None,
    clock: Callable[[], float] | None = None,
    nonce: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build a signed request envelope with the exact v1 field names."""

    if method not in FIXED_RPC_METHODS:
        raise ControlMethodError("未登记的固定 RPC 方法，已拒绝。")
    instance = _bounded_string(instance_id, "instance_id")
    actor = _bounded_string(actor_id, "actor_id")
    scopes = _normalise_scope(scope)
    body_value = _bounded_json(dict(body or {}), where="body")
    now = int((clock or time.time)())
    nonce_value = str(nonce or secrets.token_hex(16)).strip()
    if not _NONCE_RE.fullmatch(nonce_value):
        raise ControlPayloadError("nonce 不合法。")
    request_value = str(request_id or secrets.token_hex(16)).strip()
    if not _ID_RE.fullmatch(request_value):
        raise ControlPayloadError("request_id 不合法。")
    # The plugin-side control server accepts one scope token per request.
    # Prefer admin for write methods, otherwise the first requested scope.
    scope_value = "global" if "global" in scopes else scopes[0]
    request: dict[str, Any] = {
        "version": CONTROL_PROTOCOL_VERSION,
        "instance_id": instance,
        "actor_id": actor,
        "scope": scope_value,
        "method": method,
        "request_id": request_value,
        "nonce": nonce_value,
        "timestamp": now,
        "body": body_value,
        "digest": _normalise_body_digest(body_value),
    }
    request["hmac"] = _sign(secret, request)
    return request


@dataclass
class VerifiedControlRequest:
    version: int
    instance_id: str
    actor_id: str
    scope: tuple[str, ...]
    method: str
    request_id: str
    nonce: str
    timestamp: int
    body: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def verify_request_fields(
    request: Mapping[str, Any],
    *,
    clock: Callable[[], float] | None = None,
    max_age_seconds: int = CONTROL_DEFAULT_MAX_AGE_SECONDS,
) -> VerifiedControlRequest:
    """Validate envelope shape/digest/timestamp.  Signature is checked separately."""

    if not isinstance(request, Mapping):
        raise ControlPayloadError("控制桥请求必须是 JSON object。")
    unknown = {str(key) for key in request.keys()} - _REQUEST_FIELDS
    if unknown:
        raise ControlPayloadError("控制桥请求包含未知字段，已拒绝。")
    missing = _REQUEST_FIELDS - {str(key) for key in request.keys()}
    if missing:
        raise ControlPayloadError("控制桥请求缺少必需字段，已拒绝。")
    version = request.get("version")
    if version != CONTROL_PROTOCOL_VERSION:
        raise ControlPayloadError("控制桥协议版本不受支持。")
    instance_id = _bounded_string(request.get("instance_id"), "instance_id")
    actor_id = _bounded_string(request.get("actor_id"), "actor_id")
    scope = _normalise_scope(request.get("scope"))
    method = _bounded_string(request.get("method"), "method", max_len=128)
    if method not in FIXED_RPC_METHODS:
        raise ControlMethodError("未登记的固定 RPC 方法，已拒绝。")
    request_id = _bounded_string(request.get("request_id"), "request_id")
    nonce = _bounded_string(request.get("nonce"), "nonce", max_len=128)
    if not _NONCE_RE.fullmatch(nonce):
        raise ControlPayloadError("nonce 不合法。")
    timestamp = request.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ControlExpiredError("控制桥请求时间戳不合法。")
    now = float((clock or time.time)())
    max_age = max(1, int(max_age_seconds))
    if timestamp < int(now - max_age) or timestamp > int(now + CONTROL_MAX_CLOCK_SKEW_SECONDS):
        raise ControlExpiredError("控制桥请求已过期或时间戳超前，已拒绝。")
    body = request.get("body")
    if not isinstance(body, Mapping):
        raise ControlPayloadError("控制桥 body 必须是 JSON object。")
    body_value = _bounded_json(dict(body), where="body")
    body_digest = str(request.get("body_digest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", body_digest):
        raise ControlPayloadError("控制桥 body_digest 不合法。")
    if not hmac.compare_digest(body_digest, _normalise_body_digest(body_value)):
        raise ControlAuthError("控制桥 body_digest 不匹配，已拒绝。")
    signature = str(request.get("signature") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ControlAuthError("控制桥签名格式不合法。")
    return VerifiedControlRequest(
        version=CONTROL_PROTOCOL_VERSION,
        instance_id=instance_id,
        actor_id=actor_id,
        scope=scope,
        method=method,
        request_id=request_id,
        nonce=nonce,
        timestamp=int(timestamp),
        body=body_value,
        raw={str(key): value for key, value in request.items()},
    )


def _verify_plugin_request(
    secret: str | bytes,
    request: Mapping[str, Any],
    *,
    clock: Callable[[], float] | None = None,
    max_age_seconds: int = CONTROL_DEFAULT_MAX_AGE_SECONDS,
) -> VerifiedControlRequest:
    if not isinstance(request, Mapping) or set(request) != _PLUGIN_REQUEST_FIELDS:
        raise ControlPayloadError("控制桥请求包含未知或缺失字段，已拒绝。")
    version = request.get("version")
    if version != CONTROL_PROTOCOL_VERSION:
        raise ControlPayloadError("控制桥协议版本不受支持。")
    instance_id = _bounded_string(request.get("instance_id"), "instance_id")
    actor_id = _bounded_string(request.get("actor_id"), "actor_id")
    scope = _normalise_scope(request.get("scope"))
    method = _bounded_string(request.get("method"), "method", max_len=128)
    if method not in FIXED_RPC_METHODS:
        raise ControlMethodError("未登记的固定 RPC 方法，已拒绝。")
    request_id = _bounded_string(request.get("request_id"), "request_id")
    nonce = _bounded_string(request.get("nonce"), "nonce", max_len=128)
    if not _NONCE_RE.fullmatch(nonce):
        raise ControlPayloadError("nonce 不合法。")
    timestamp = request.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ControlExpiredError("控制桥请求时间戳不合法。")
    now = float((clock or time.time)())
    max_age = max(1, int(max_age_seconds))
    if timestamp < int(now - max_age) or timestamp > int(now + CONTROL_MAX_CLOCK_SKEW_SECONDS):
        raise ControlExpiredError("控制桥请求已过期或时间戳超前，已拒绝。")
    body = request.get("body")
    if not isinstance(body, Mapping):
        raise ControlPayloadError("控制桥 body 必须是 JSON object。")
    body_value = _bounded_json(dict(body), where="body")
    digest = str(request.get("digest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ControlPayloadError("控制桥 digest 不合法。")
    if not hmac.compare_digest(digest, _normalise_body_digest(body_value)):
        raise ControlAuthError("控制桥 body digest 不匹配，已拒绝。")
    supplied_hmac = str(request.get("hmac") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", supplied_hmac):
        raise ControlAuthError("控制桥签名格式不合法。")
    unsigned = {key: value for key, value in request.items() if key != "hmac"}
    expected = _sign(secret, unsigned)
    if not hmac.compare_digest(expected, supplied_hmac):
        raise ControlAuthError("控制桥签名校验失败，已拒绝。")
    return VerifiedControlRequest(
        version=CONTROL_PROTOCOL_VERSION,
        instance_id=instance_id,
        actor_id=actor_id,
        scope=scope,
        method=method,
        request_id=request_id,
        nonce=nonce,
        timestamp=int(timestamp),
        body=body_value,
        raw={str(key): value for key, value in request.items()},
    )


def verify_request_signature(
    secret: str | bytes,
    request: Mapping[str, Any],
    *,
    clock: Callable[[], float] | None = None,
    max_age_seconds: int = CONTROL_DEFAULT_MAX_AGE_SECONDS,
) -> VerifiedControlRequest:
    if "hmac" in request:
        return _verify_plugin_request(secret, request, clock=clock, max_age_seconds=max_age_seconds)
    verified = verify_request_fields(request, clock=clock, max_age_seconds=max_age_seconds)
    without_signature = {key: value for key, value in request.items() if key != "signature"}
    expected = _sign(secret, without_signature)
    provided = str(request.get("signature") or "").strip().lower()
    if not hmac.compare_digest(expected, provided):
        raise ControlAuthError("控制桥签名校验失败，已拒绝。")
    return verified


class NonceCache:
    """Thread-safe bounded nonce replay cache."""

    def __init__(self, *, max_entries: int = 10000, clock: Callable[[], float] | None = None) -> None:
        self.max_entries = max(100, int(max_entries))
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, str], float] = {}

    def consume(self, instance_id: str, nonce: str, *, expires_at: float) -> None:
        now = float(self.clock())
        key = (str(instance_id), str(nonce))
        with self._lock:
            # Prune expired entries opportunistically.
            expired = [item for item, expiry in self._seen.items() if expiry <= now]
            for item in expired:
                self._seen.pop(item, None)
            if key in self._seen:
                raise ControlReplayError("控制桥 nonce 已被使用，重放请求已拒绝。")
            if len(self._seen) >= self.max_entries:
                oldest = min(self._seen.items(), key=lambda pair: pair[1])[0]
                self._seen.pop(oldest, None)
            self._seen[key] = max(now, float(expires_at))


@dataclass(frozen=True)
class ControlPolicy:
    """Server-side instance/actor/scope allowlists."""

    allowed_instances: frozenset[str]
    allowed_actors: frozenset[str]
    allowed_scopes: frozenset[str] = frozenset(BUSINESS_SCOPES)
    actor_scopes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    write_scope: str = "admin"

    @classmethod
    def single(
        cls,
        instance_id: str,
        actor_id: str,
        *,
        allowed_scopes: Iterable[str] | None = None,
    ) -> "ControlPolicy":
        scopes = frozenset(allowed_scopes or BUSINESS_SCOPES)
        return cls(
            allowed_instances=frozenset({str(instance_id)}),
            allowed_actors=frozenset({str(actor_id)}),
            allowed_scopes=scopes,
        )

    def authorise(self, request: VerifiedControlRequest) -> None:
        if request.instance_id not in self.allowed_instances:
            raise ControlInstanceError("控制桥实例不在允许列表中，已拒绝。")
        if request.actor_id not in self.allowed_actors:
            raise ControlActorError("控制桥 actor 不在允许列表中，已拒绝。")
        requested = set(request.scope)
        allowed = set(self.allowed_scopes)
        if self.actor_scopes:
            actor_allowed = self.actor_scopes.get(request.actor_id)
            if actor_allowed is not None:
                allowed &= set(actor_allowed)
        if not requested.issubset(allowed):
            raise ControlScopeError("控制桥 scope 不在允许列表中，已拒绝。")
        method = FIXED_RPC_METHODS.get(request.method)
        if method is None:
            raise ControlMethodError("未登记的固定 RPC 方法，已拒绝。")
        if method.requires_admin_scope and self.write_scope not in requested:
            raise ControlScopeError("该固定 RPC 方法需要管理员 scope，已拒绝。")


class ControlServer:
    """Verifying/dispatching side of the fixed-RPC bridge."""

    def __init__(
        self,
        secret: str | bytes,
        policy: ControlPolicy,
        handlers: Mapping[str, Callable[[VerifiedControlRequest], Any]],
        *,
        clock: Callable[[], float] | None = None,
        nonces: NonceCache | None = None,
        max_body_bytes: int = CONTROL_MAX_BODY_BYTES,
        max_age_seconds: int = CONTROL_DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self._secret = _secret_bytes(secret)
        self.policy = policy
        self._handlers = dict(handlers)
        for method in self._handlers:
            if method not in FIXED_RPC_METHODS:
                raise ControlMethodError("控制桥处理器只能注册固定方法表中的方法。")
        self.clock = clock or time.time
        self.nonces = nonces or NonceCache(clock=self.clock)
        self.max_body_bytes = max(1024, int(max_body_bytes))
        self.max_age_seconds = max(1, int(max_age_seconds))

    @property
    def secret(self) -> bytes:
        # Intentionally not displayed by repr; callers that need to sign local
        # responses should use sign_response().
        return self._secret

    def available_methods(self) -> list[dict[str, Any]]:
        return [
            {
                "method": spec.name,
                "provider": spec.provider,
                "access": spec.access,
            }
            for name, spec in sorted(FIXED_RPC_METHODS.items())
            if name in self._handlers
        ]

    def sign_response(
        self,
        *,
        request: VerifiedControlRequest,
        ok: bool,
        body: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": CONTROL_PROTOCOL_VERSION,
            "ok": bool(ok),
            "request_id": request.request_id,
            "method": request.method,
            "timestamp": int((self.clock)()),
            "body": _bounded_json(dict(body or {}), max_bytes=CONTROL_MAX_RESPONSE_BYTES, where="response body"),
        }
        if error is not None:
            payload["error"] = _bounded_json(dict(error), max_bytes=8192, where="error")
        payload["body_digest"] = _normalise_body_digest(payload["body"])
        payload["signature"] = _sign(self._secret, payload)
        return payload

    def handle_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        verified = verify_request_signature(
            self._secret,
            request,
            clock=self.clock,
            max_age_seconds=self.max_age_seconds,
        )
        self.policy.authorise(verified)
        handler = self._handlers.get(verified.method)
        if handler is None:
            raise ControlMethodError("该固定 RPC 方法尚未提供处理器。")
        # Consume only after auth/method checks; a failed signature must not be
        # able to burn a legitimate nonce.
        expires_at = verified.timestamp + self.max_age_seconds
        self.nonces.consume(verified.instance_id, verified.nonce, expires_at=expires_at)
        try:
            result = handler(verified)
        except ControlError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak handler internals
            raise ControlUnavailable(
                "固定 RPC 处理器执行失败。",
                detail=exc.__class__.__name__ if exc else "",
            ) from exc
        if result is None:
            result = {}
        if not isinstance(result, Mapping):
            raise ControlResponseError("固定 RPC 处理器必须返回 JSON object。")
        body = _bounded_json(dict(result), max_bytes=CONTROL_MAX_RESPONSE_BYTES, where="response body")
        return self.sign_response(request=verified, ok=True, body=body)

    def handle_raw(self, raw: bytes) -> dict[str, Any]:
        request = _parse_json_object(raw, max_bytes=self.max_body_bytes)
        return self.handle_request(request)

    def handle_raw_safe(self, raw: bytes) -> tuple[int, dict[str, Any]]:
        """Return ``(http_status, signed_or_error_payload)`` without raising."""

        try:
            response = self.handle_raw(raw)
            return HTTPStatus.OK, response
        except ControlError as exc:
            return int(exc.http_status), exc.to_public_dict()
        except Exception as exc:  # noqa: BLE001
            return HTTPStatus.INTERNAL_SERVER_ERROR, ControlUnavailable(
                "控制桥内部错误。",
                detail=exc.__class__.__name__ if exc else "",
            ).to_public_dict()


class ControlSecretStore:
    """Per-instance secret file with 0600 atomic creation."""

    def __init__(
        self,
        instance_dir: Path | str,
        *,
        secret_path: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.instance_dir = Path(instance_dir).expanduser().resolve()
        environment = dict(os.environ if env is None else env)
        override = str(secret_path or environment.get(CONTROL_SECRET_ENV, "") or "").strip()
        if override:
            candidate = Path(override).expanduser()
            if not candidate.is_absolute():
                candidate = self.instance_dir / candidate
        else:
            candidate = self.instance_dir / CONTROL_SECRET_RELATIVE_PATH
        self.path = candidate.resolve(strict=False)
        self.clock = clock

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"ControlSecretStore(instance_dir={self.instance_dir!s}, path={self.path!s})"

    def exists(self) -> bool:
        try:
            return self.path.is_file() and not self.path.is_symlink()
        except OSError:
            return False

    def ensure(self) -> bytes:
        if self.path.is_symlink():
            raise ControlSecretError("控制桥秘密文件不能是符号链接。")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            if not self.path.exists():
                secret = generate_control_secret().encode("ascii")
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(secret + b"\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    raise
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                try:
                    dir_fd = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
                return _secret_bytes(secret.decode("ascii"))
            return self._read_existing()
        except ControlError:
            raise
        except OSError as exc:
            raise ControlSecretError(
                "无法读取或创建控制桥秘密文件。", detail=exc.__class__.__name__
            ) from exc

    def read(self) -> bytes:
        if not self.path.exists():
            raise ControlSecretError("控制桥秘密文件尚未初始化。")
        return self._read_existing()

    def _read_existing(self) -> bytes:
        if self.path.is_symlink():
            raise ControlSecretError("控制桥秘密文件不能是符号链接。")
        try:
            info = self.path.stat()
        except OSError as exc:
            raise ControlSecretError("无法读取控制桥秘密文件。", detail=exc.__class__.__name__) from exc
        if not stat.S_ISREG(info.st_mode):
            raise ControlSecretError("控制桥秘密必须是普通文件。")
        if info.st_size <= 0 or info.st_size > CONTROL_SECRET_MAX_BYTES:
            raise ControlSecretError("控制桥秘密文件大小异常。")
        if os.name != "nt":
            if info.st_mode & 0o077:
                try:
                    os.chmod(self.path, 0o600)
                except OSError as exc:
                    raise ControlSecretError("控制桥秘密文件权限不安全，已拒绝。") from exc
                info = self.path.stat()
                if info.st_mode & 0o077:
                    raise ControlSecretError("控制桥秘密文件权限必须为 0600。")
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise ControlSecretError("无法读取控制桥秘密文件。", detail=exc.__class__.__name__) from exc
        text = raw.decode("utf-8", errors="strict").strip()
        # Canonical runtime form is the 32 raw bytes; both ensure() and read()
        # return it so either can feed ControlServer/ControlClient.
        return _secret_bytes(text)


@dataclass(frozen=True)
class LoopbackEndpoint:
    host: str = CONTROL_DEFAULT_HOST
    port: int = CONTROL_DEFAULT_PORT
    path: str = CONTROL_DEFAULT_PATH

    def __post_init__(self) -> None:
        host = str(self.host or "").strip().lower()
        if host not in {"127.0.0.1", "localhost"}:
            raise ControlPayloadError("控制桥只允许 127.0.0.1 回环地址。")
        try:
            port = int(self.port)
        except (TypeError, ValueError) as exc:
            raise ControlPayloadError("控制桥端口不合法。") from exc
        if port < 1 or port > 65535:
            raise ControlPayloadError("控制桥端口不合法。")
        path = str(self.path or "").strip()
        if path not in CONTROL_ALLOWED_PATHS:
            raise ControlPayloadError("控制桥路径必须是固定控制端点。")
        object.__setattr__(self, "host", "127.0.0.1")
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "path", path)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"


def _read_positive_int_env(env: Mapping[str, str], name: str, default: int, maximum: int = 65535) -> int:
    raw = str(env.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ControlPayloadError(f"{name} 必须是整数。") from exc
    if value < 1 or value > maximum:
        raise ControlPayloadError(f"{name} 超出允许范围。")
    return value


def endpoint_from_env(env: Mapping[str, str] | None = None) -> LoopbackEndpoint:
    environment = dict(os.environ if env is None else env)
    host = str(environment.get("QQBOT_SAFE_TOOLS_CONTROL_HOST", CONTROL_DEFAULT_HOST) or CONTROL_DEFAULT_HOST)
    port = _read_positive_int_env(environment, "QQBOT_SAFE_TOOLS_CONTROL_PORT", CONTROL_DEFAULT_PORT)
    path = str(environment.get("QQBOT_SAFE_TOOLS_CONTROL_PATH", CONTROL_DEFAULT_PATH) or CONTROL_DEFAULT_PATH)
    return LoopbackEndpoint(host=host, port=port, path=path)


class TransportError(ControlUnavailable):
    pass


class HttpControlTransport:
    """Direct HTTP transport to a fixed loopback endpoint."""

    def __init__(
        self,
        endpoint: LoopbackEndpoint | None = None,
        *,
        timeout: float = 5.0,
        opener: Any | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint or endpoint_from_env(env)
        if not isinstance(self.endpoint, LoopbackEndpoint):
            raise ControlPayloadError("控制桥 HTTP 端点不合法。")
        self.timeout = max(0.5, min(30.0, float(timeout)))
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def send(self, payload: bytes) -> bytes:
        if not isinstance(payload, (bytes, bytearray)) or len(payload) > CONTROL_MAX_BODY_BYTES:
            raise ControlPayloadError("控制桥 HTTP 请求体不合法或过大。")
        request = urllib.request.Request(
            self.endpoint.url,
            data=bytes(payload),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(CONTROL_MAX_RESPONSE_BYTES + 1)
                if len(body) > CONTROL_MAX_RESPONSE_BYTES:
                    raise ControlResponseError("控制桥响应超过大小限制。")
                return body
        except urllib.error.HTTPError as exc:
            # A verifying control server returns a JSON error body for denied
            # requests.  Preserve it so the client can map the stable error
            # code; never inspect/log headers that could contain a secret.
            try:
                body = exc.read(CONTROL_MAX_RESPONSE_BYTES + 1)
            except Exception:
                body = b""
            if body and len(body) <= CONTROL_MAX_RESPONSE_BYTES:
                return body
            raise ControlUnavailable(
                "插件控制桥返回错误且响应不可读，操作已按失败关闭。",
                detail=exc.__class__.__name__,
            ) from exc
        except ControlError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, socket.error) as exc:
            raise ControlUnavailable(
                "插件控制桥不可达，操作已按失败关闭。", detail=exc.__class__.__name__
            ) from exc

    close = lambda self: None  # noqa: E731 - small compatibility helper


# Fixed in-container client.  It reads a complete JSON request from stdin,
# forwards it to the container-local loopback endpoint, and prints only the
# response JSON.  The container name is the only interpolated value and is
# validated before this script is used.
_DOCKER_CLIENT_SCRIPT = r"""
import json, sys, urllib.request
try:
    payload = sys.stdin.buffer.read(262145)
    if not payload or len(payload) > 262144:
        raise ValueError("bad input")
    req = urllib.request.Request(
        "http://127.0.0.1:__PORT____PATH__",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=__TIMEOUT__) as resp:
        body = resp.read(524289)
    if len(body) > 524288:
        raise ValueError("response too large")
    json.loads(body.decode("utf-8"))
    sys.stdout.buffer.write(body)
except Exception as exc:
    sys.stdout.write(json.dumps({"ok": False, "code": "bridge_unavailable", "message": type(exc).__name__}))
    raise SystemExit(1)
"""

_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DockerExecTransport:
    """Fixed ``docker exec`` transport for hosts where loopback is in a container."""

    def __init__(
        self,
        container_name: str,
        *,
        endpoint: LoopbackEndpoint | None = None,
        runner: Callable[[list[str], bytes, float], Any] | None = None,
        timeout: float = 10.0,
    ) -> None:
        name = str(container_name or "").strip()
        if not _CONTAINER_RE.fullmatch(name):
            raise ControlPayloadError("docker 容器名不合法。")
        if not name.startswith("qqbot-"):
            # Defense in depth: this bridge is only for Studio-managed
            # containers.  Tests may use a qqbot-* fake name.
            raise ControlPayloadError("控制桥只允许 Studio 管理的容器。")
        endpoint = endpoint or LoopbackEndpoint()
        if not isinstance(endpoint, LoopbackEndpoint):
            raise ControlPayloadError("控制桥容器端点不合法。")
        self.container_name = name
        self.endpoint = endpoint
        self.timeout = max(0.5, min(60.0, float(timeout)))
        self._runner = runner or self._default_runner
        script = (
            _DOCKER_CLIENT_SCRIPT.replace("__PORT__", str(endpoint.port))
            .replace("__PATH__", endpoint.path)
            .replace("__TIMEOUT__", str(min(30.0, self.timeout)))
        )
        self._args = ["docker", "exec", "-i", name, "python3", "-c", script]

    @property
    def command(self) -> tuple[str, ...]:
        return tuple(self._args)

    @staticmethod
    def _default_runner(args: list[str], payload: bytes, timeout: float) -> Any:
        return subprocess.run(
            args,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
        )

    def send(self, payload: bytes) -> bytes:
        if not isinstance(payload, (bytes, bytearray)) or len(payload) > CONTROL_MAX_BODY_BYTES:
            raise ControlPayloadError("控制桥 docker 请求体不合法或过大。")
        try:
            result = self._runner(list(self._args), bytes(payload), self.timeout)
        except (subprocess.TimeoutExpired, TimeoutError, OSError) as exc:
            raise ControlUnavailable(
                "插件控制桥 docker 传输不可用。", detail=exc.__class__.__name__
            ) from exc
        returncode = int(getattr(result, "returncode", 0) or 0)
        stdout = getattr(result, "stdout", b"") or b""
        stderr = getattr(result, "stderr", b"") or b""
        if returncode != 0:
            # The in-container shim exits 1 when the plugin rejects the
            # request (scope/actor/auth/protocol errors), but it still writes
            # a JSON error body.  Preserve that body so the caller can surface
            # the plugin's stable error code instead of mislabelling every
            # rejection as a transport failure.
            if stdout:
                try:
                    _parse_json_object(
                        stdout, max_bytes=CONTROL_MAX_RESPONSE_BYTES, where="response"
                    )
                except ControlError:
                    pass
                else:
                    return bytes(stdout)
            raise ControlUnavailable(
                "插件控制桥 docker 传输失败。",
                detail="stderr" if stderr else f"exit_{returncode}",
            )
        if not isinstance(stdout, (bytes, bytearray)):
            stdout = str(stdout).encode("utf-8")
        if len(stdout) > CONTROL_MAX_RESPONSE_BYTES:
            raise ControlResponseError("控制桥响应超过大小限制。")
        return bytes(stdout)


class _CallableTransport:
    """Adapter for tests/local callers that provide ``payload -> bytes``."""

    def __init__(self, sender):
        self._sender = sender

    def send(self, payload: bytes) -> bytes:
        return self._sender(payload)


class ControlClient:
    """Signed fixed-RPC client.  Transport is injected; no arbitrary URLs."""

    def __init__(
        self,
        secret: str | bytes,
        transport: Any,
        *,
        instance_id: str,
        actor_id: str,
        allowed_scopes: Iterable[str] | None = None,
        clock: Callable[[], float] | None = None,
        max_age_seconds: int = CONTROL_DEFAULT_MAX_AGE_SECONDS,
        nonce_factory: Callable[[], str] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._secret = _secret_bytes(secret)
        if transport is not None and callable(transport) and not callable(
            getattr(transport, "send", None)
        ):
            transport = _CallableTransport(transport)
        if transport is None or not callable(getattr(transport, "send", None)):
            raise ControlUnavailable("插件控制桥未配置传输层。")
        self.transport = transport
        self.instance_id = _bounded_string(instance_id, "instance_id")
        self.actor_id = _bounded_string(actor_id, "actor_id")
        self.allowed_scopes = frozenset(_normalise_scope(allowed_scopes or BUSINESS_SCOPES))
        self.clock = clock or time.time
        self.max_age_seconds = max(1, int(max_age_seconds))
        self._nonce_factory = nonce_factory or (lambda: secrets.token_hex(16))
        self._request_id_factory = request_id_factory or (lambda: secrets.token_hex(16))

    def call(
        self,
        method: str,
        *,
        scope: Iterable[str] | str | None = None,
        body: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if method not in FIXED_RPC_METHODS:
            raise ControlMethodError("未登记的固定 RPC 方法，已拒绝。")
        requested = _normalise_scope(scope or ("admin", "global"))
        if not set(requested).issubset(self.allowed_scopes):
            raise ControlScopeError("客户端 scope 不在本地允许列表中，已拒绝。")
        request = build_request(
            self._secret,
            instance_id=self.instance_id,
            actor_id=self.actor_id,
            scope=requested,
            method=method,
            body=body,
            clock=self.clock,
            nonce=self._nonce_factory(),
            request_id=self._request_id_factory(),
        )
        raw = _canonical_json(request)
        try:
            response_raw = self.transport.send(raw)
        except ControlError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport contract boundary
            raise ControlUnavailable(
                "插件控制桥不可达，操作已按失败关闭。", detail=exc.__class__.__name__
            ) from exc
        response = _parse_json_object(response_raw, max_bytes=CONTROL_MAX_RESPONSE_BYTES, where="response")
        return self._verify_response(response, request.request_id if hasattr(request, "request_id") else request["request_id"])

    def _verify_response(self, response: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if not isinstance(response, Mapping):
            raise ControlResponseError("控制桥响应格式异常。")
        if "ok" in response and "signature" not in response and "version" not in response:
            response_request_id = str(response.get("request_id") or "")
            if response_request_id and response_request_id != str(request_id):
                raise ControlResponseError("控制桥响应 request_id 不匹配。")
            if not bool(response.get("ok")):
                code = str(response.get("error") or response.get("code") or "control_remote_error")
                message = str(response.get("message") or code)
                raise _remote_error(code, message)
            result = response.get("result")
            if not isinstance(result, Mapping):
                raise ControlResponseError("控制桥响应 result 必须是 JSON object。")
            return dict(result)
        if response.get("version") != CONTROL_PROTOCOL_VERSION:
            raise ControlResponseError("控制桥响应协议版本不受支持。")
        if str(response.get("request_id") or "") != str(request_id):
            raise ControlResponseError("控制桥响应 request_id 不匹配。")
        body = response.get("body")
        if not isinstance(body, Mapping):
            raise ControlResponseError("控制桥响应 body 必须是 JSON object。")
        if not bool(response.get("ok")) and "signature" not in response:
            error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
            code = str(response.get("code") or error.get("code") or "control_remote_error")
            message = str(response.get("message") or error.get("message") or "控制桥返回失败。")
            raise _remote_error(code, message)
        if "signature" not in response or "body_digest" not in response:
            raise ControlResponseError("控制桥响应缺少签名信息。")
        expected_digest = _normalise_body_digest(body)
        if not hmac.compare_digest(str(response.get("body_digest") or ""), expected_digest):
            raise ControlAuthError("控制桥响应 body_digest 校验失败。")
        without_signature = {key: value for key, value in response.items() if key != "signature"}
        expected_signature = _sign(self._secret, without_signature)
        if not hmac.compare_digest(str(response.get("signature") or ""), expected_signature):
            raise ControlAuthError("控制桥响应签名校验失败。")
        if not bool(response.get("ok")):
            error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
            code = str(error.get("code") or response.get("code") or "control_remote_error")
            message = str(error.get("message") or response.get("message") or "控制桥返回失败。")
            raise _remote_error(code, message)
        return dict(body)


def _remote_error(code: str, message: str) -> ControlError:
    mapping: dict[str, type[ControlError]] = {
        "control_auth_failed": ControlAuthError,
        "control_replay": ControlReplayError,
        "control_expired": ControlExpiredError,
        "control_method_not_allowed": ControlMethodError,
        "control_scope_denied": ControlScopeError,
        "control_instance_denied": ControlInstanceError,
        "control_actor_denied": ControlActorError,
        "control_invalid_payload": ControlPayloadError,
        "bridge_unavailable": ControlUnavailable,
    }
    cls = mapping.get(str(code), ControlError)
    error = cls(message)
    error.code = str(code or error.code)
    return error


class _ControlHTTPRequestHandler(BaseHTTPRequestHandler):
    server: "_ControlHTTPServer"
    protocol_version = "HTTP/1.1"
    server_version = "QQBotSafeToolsControl/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover - stderr only
        # Never log request bodies, nonces, or secrets.
        super().log_message(fmt, *args)

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == CONTROL_HEALTH_PATH:
            self._send_json(HTTPStatus.OK, {"ok": True, "service": "qqbot_safe_tools-control"})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "code": "control_path_not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in CONTROL_ALLOWED_PATHS:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "code": "control_path_not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > self.server.max_body_bytes:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "code": "control_invalid_payload", "message": "请求体为空或过大。"},
            )
            return
        content_type = str(self.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "code": "control_invalid_payload", "message": "请求必须使用 application/json。"},
            )
            return
        raw = self.rfile.read(length)
        status, payload = self.server.control.handle_raw_safe(raw)
        self._send_json(status, payload)


class _ControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], control: ControlServer) -> None:
        self.control = control
        self.max_body_bytes = control.max_body_bytes
        super().__init__(address, _ControlHTTPRequestHandler)

    def server_bind(self) -> None:
        host = str(self.server_address[0])
        if host not in {"127.0.0.1", "::1"}:
            raise ControlPayloadError("控制桥 HTTP 服务只允许绑定回环地址。")
        super().server_bind()


def create_control_http_server(
    control: ControlServer,
    *,
    host: str = CONTROL_DEFAULT_HOST,
    port: int = 0,
) -> ThreadingHTTPServer:
    """Create (but do not start) a loopback-only control HTTP server."""

    if str(host).strip().lower() not in {"127.0.0.1", "localhost"}:
        raise ControlPayloadError("控制桥 HTTP 服务只允许绑定 127.0.0.1。")
    return _ControlHTTPServer((CONTROL_DEFAULT_HOST, int(port or 0)), control)


def start_control_http_server(
    control: ControlServer,
    *,
    port: int = 0,
) -> tuple[ThreadingHTTPServer, threading.Thread, LoopbackEndpoint]:
    """Start a loopback test/dev server and return it with its endpoint."""

    server = create_control_http_server(control, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, actual_port = server.server_address[:2]
    return server, thread, LoopbackEndpoint(host=str(host), port=int(actual_port))


class ControlBridge:
    """Per-instance bridge facade used by Studio status/API layers."""

    def __init__(
        self,
        instance_id: str,
        secret_store: ControlSecretStore,
        *,
        actor_id: str = "studio-admin",
        transport_factory: Callable[[bytes], Any] | None = None,
        clock: Callable[[], float] | None = None,
        enabled: bool | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.instance_id = str(instance_id or "").strip()
        if not self.instance_id or not _ID_RE.fullmatch(self.instance_id):
            raise ControlPayloadError("控制桥 instance_id 不合法。")
        self.secret_store = secret_store
        self.actor_id = str(actor_id or "studio-admin")
        self._transport_factory = transport_factory
        self.clock = clock or time.time
        environment = dict(os.environ if env is None else env)
        if enabled is None:
            enabled = str(environment.get(CONTROL_ENABLED_ENV, "")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        self.enabled = bool(enabled)
        self._client: ControlClient | None = None

    def _build_client(self) -> ControlClient:
        secret = self.secret_store.ensure()
        transport = self._transport_factory(secret) if self._transport_factory else None
        if transport is None:
            # The host-side HTTP transport is a valid default only if the
            # container publishes no port; deployments normally inject a
            # docker-exec transport.  Status reports the distinction.
            raise ControlUnavailable("插件控制桥未配置传输层。")
        return ControlClient(
            secret,
            transport,
            instance_id=self.instance_id,
            actor_id=self.actor_id,
            clock=self.clock,
        )

    @property
    def client(self) -> ControlClient:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def status(self, *, probe: bool = False, timeout: float = 2.0) -> dict[str, Any]:
        secret_exists = self.secret_store.exists()
        payload: dict[str, Any] = {
            "ok": True,
            "enabled": bool(self.enabled),
            "configured": bool(self.enabled and secret_exists and self._transport_factory is not None),
            "ready": False,
            "reachable": None,
            "transport": "configured" if self._transport_factory is not None else "none",
            "endpoint": f"{CONTROL_DEFAULT_HOST}:{CONTROL_DEFAULT_PORT}{CONTROL_DEFAULT_PATH}",
            "secret_present": bool(secret_exists),
            "reason": "",
        }
        if not self.enabled:
            payload["reason"] = "bridge_disabled"
            return payload
        if not secret_exists:
            payload["reason"] = "secret_missing"
            return payload
        if self._transport_factory is None:
            payload["reason"] = "transport_missing"
            return payload
        payload["ready"] = True
        if probe:
            try:
                client = self.client
                original = client.transport
                # We cannot change timeout generically; the transport has its
                # own bounded timeout.  ``tool.catalog`` is a read-only fixed
                # method and is the contract's health probe.
                client.call("tool.catalog", scope=("admin", "global"), body={})
                payload["reachable"] = True
            except ControlError as exc:
                payload["reachable"] = False
                payload["reason"] = exc.code
            except Exception as exc:  # noqa: BLE001
                payload["reachable"] = False
                payload["reason"] = "bridge_unavailable"
        return payload

    def call(
        self,
        method: str,
        *,
        scope: Iterable[str] | str | None = None,
        body: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise ControlUnavailable("插件控制桥未启用，操作已按失败关闭。")
        if not self.secret_store.exists():
            raise ControlUnavailable("插件控制桥秘密尚未初始化，操作已按失败关闭。")
        if method not in FIXED_RPC_METHODS:
            raise ControlMethodError("未登记的固定 RPC 方法，已拒绝。")
        return self.client.call(method, scope=scope, body=body, timeout=timeout)


def parse_timestamp(value: Any) -> int:
    """Small helper kept public for adapters/tests."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ControlExpiredError("timestamp 必须是整数。")
    return int(value)


__all__ = [
    "BUSINESS_SCOPES",
    "CONTROL_DEFAULT_HOST",
    "CONTROL_DEFAULT_PATH",
    "CONTROL_DEFAULT_PORT",
    "CONTROL_PROTOCOL_VERSION",
    "CONTROL_SECRET_ENV",
    "CONTROL_SECRET_RELATIVE_PATH",
    "ControlActorError",
    "ControlAuthError",
    "ControlBridge",
    "ControlClient",
    "ControlError",
    "ControlExpiredError",
    "ControlInstanceError",
    "ControlMethodError",
    "ControlPayloadError",
    "ControlPolicy",
    "ControlReplayError",
    "ControlResponseError",
    "ControlScopeError",
    "ControlSecretError",
    "ControlSecretStore",
    "ControlServer",
    "ControlUnavailable",
    "DockerExecTransport",
    "FIXED_RPC_METHODS",
    "HttpControlTransport",
    "LoopbackEndpoint",
    "NonceCache",
    "RpcMethod",
    "VerifiedControlRequest",
    "build_request",
    "create_control_http_server",
    "endpoint_from_env",
    "generate_control_secret",
    "start_control_http_server",
    "verify_request_fields",
    "verify_request_signature",
]
