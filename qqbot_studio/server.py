"""HTTP server for the QQ Bot Studio management console and deployment wizard."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .assistant_ext import (
    ASSISTANT_DEFAULT_HOST,
    ASSISTANT_DEFAULT_PORT,
    ASSISTANT_DIR_NAME,
    ASSISTANT_REPO_URL,
)
from .astrbot_api import AstrBotAPIError
from .deployer import (
    PROMPT_PROFILES_MAX_REQUEST_BYTES,
    DeploymentError,
    MemoryPolicyUnsupportedError,
    MemoryPolicyWriteUnavailableError,
)
from .dockerctl import DockerError
from .memory_admin import (
    MemoryAdmin,
    MemoryAdminError,
    MemoryAdminUnavailable,
    MemoryConflict,
    MemoryRecordNotFound,
)
from .plugin_control import ControlError, ControlMethodError, ControlUnavailable, FIXED_RPC_METHODS
from .plugin_market import PluginMarketError, sanitize_error_text
from .plugin_state import (
    PluginRevisionConflict,
    PluginStateCorruptError,
    PluginStateError,
    StaleOwnerEpochError,
)
from .plugin_transactions import (
    MigrationBridgeUnavailable,
    MigrationConflict,
    MigrationError,
    MigrationManualRecovery,
    MigrationNotFound,
    MigrationPlanError,
    MigrationStateError,
)
from .store import default_data_dir
from .studio import DEFAULT_BIND_HOST, DEFAULT_BIND_PORT, StudioManager
from .updater import (
    UpdateError,
    check_update,
    list_versions,
    load_history,
    rollback,
    start_update,
    update_status,
)

STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
DEFAULT_REQUEST_BODY_MAX_BYTES = 2 * 1024 * 1024


def _read_optional(path: Path, fallback: bytes = b"") -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return fallback


class AppContext:
    def __init__(
        self,
        studio: StudioManager,
        token: str,
        static_dir: Path,
        *,
        allow_remote: bool = False,
    ) -> None:
        self.studio = studio
        self.token = token
        self.static_dir = static_dir
        self.allow_remote = bool(allow_remote)
        self.index_html = (static_dir / "index.html").read_text(encoding="utf-8")
        if '"{{TOKEN}}"' not in self.index_html:
            raise RuntimeError("前端 index.html 缺少 window.QB_TOKEN 占位符")
        # The console page is optional while the project is being upgraded; a
        # minimal fallback keeps the server usable if static assets are partial.
        console_path = static_dir / "console.html"
        if console_path.exists():
            self.console_html = console_path.read_text(encoding="utf-8")
        else:
            self.console_html = (
                "<!doctype html><meta charset='utf-8'><title>QQ Bot Studio Console</title>"
                "<script>window.QB_TOKEN = \"{{TOKEN}}\";</script>"
                "<h1>QQ Bot Studio Console</h1><p>控制台静态文件尚未安装。</p>"
            )
        self.styles = _read_optional(static_dir / "styles.css")
        self.script = _read_optional(static_dir / "app.js")
        self.console_css = _read_optional(static_dir / "console.css")
        self.console_script = _read_optional(static_dir / "console.js")

        # Optional standalone assistant B page.  Its absence or a broken
        # template must never prevent the management server from starting.
        self.assistant_html: str | None = None
        self.assistant_page_status = HTTPStatus.NOT_FOUND
        self.assistant_page_error = "独立 AI 助手页面尚未安装。请更新前端文件后重试。"
        assistant_path = static_dir / "assistant.html"
        if assistant_path.exists():
            try:
                assistant_html = assistant_path.read_text(encoding="utf-8")
            except OSError as exc:
                self.assistant_page_status = HTTPStatus.INTERNAL_SERVER_ERROR
                self.assistant_page_error = f"无法读取独立 AI 助手页面：{exc}"
            else:
                if '"{{TOKEN}}"' in assistant_html:
                    self.assistant_html = assistant_html
                    self.assistant_page_status = HTTPStatus.OK
                    self.assistant_page_error = ""
                else:
                    self.assistant_page_status = HTTPStatus.INTERNAL_SERVER_ERROR
                    self.assistant_page_error = (
                        "独立 AI 助手页面模板损坏：缺少 window.QB_TOKEN = \"{{TOKEN}}\"; 占位符。"
                    )

    def instance_id(self, query: dict[str, list[str]], payload: dict[str, Any] | None = None) -> str:
        explicit = ""
        if query.get("instance"):
            explicit = str(query["instance"][0]).strip()
        if not explicit and payload:
            explicit = str(payload.get("instance") or "").strip()
        if not explicit:
            explicit = self.studio.latest_instance_id()
        self.studio.get_instance_meta(explicit)
        return explicit

    def manager_for(self, query: dict[str, list[str]], payload: dict[str, Any] | None = None):
        return self.studio.get_manager(self.instance_id(query, payload))


class QQBotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], context: AppContext) -> None:
        self.context = context
        super().__init__(address, handler)


class QQBotRequestHandler(BaseHTTPRequestHandler):
    server: QQBotHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "QQBotStudio/2.0"
    sys_version = ""

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")

    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        cache: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=0" if cache else "no-store")
        self._common_headers()
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, status: int, payload: dict[str, Any], *, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", extra_headers=extra_headers)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "message": message})

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._common_headers()
        self.end_headers()

    # ------------------------------------------------------------------
    # Request validation
    # ------------------------------------------------------------------
    def _validate_host_header(self) -> bool:
        if self.server.context.allow_remote:
            # Remote bind is an explicit opt-in; token checks still protect
            # mutating APIs and the management endpoints.
            return True
        raw_host = str(self.headers.get("Host", "")).strip().lower()
        if not raw_host:
            return True
        if raw_host.startswith("["):
            hostname = raw_host.split("]", 1)[0] + "]"
        else:
            hostname = raw_host.rsplit(":", 1)[0] if ":" in raw_host else raw_host
        return hostname in {"127.0.0.1", "localhost"}

    def _check_token(self) -> bool:
        provided = str(self.headers.get("X-QB-Token", ""))
        return bool(provided) and hmac.compare_digest(provided, self.server.context.token)

    @staticmethod
    def _safe_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value) if value is not None else default
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _query(parsed) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    @staticmethod
    def _nested_instance_route(path: str) -> tuple[str, str] | None:
        """Parse ``/api/instances/<id>/<subroute>`` safely."""

        prefix = "/api/instances/"
        if not path.startswith(prefix):
            return None
        rest = urllib.parse.unquote(path[len(prefix) :]).strip()
        if "/" not in rest:
            return None
        instance_id, subroute = rest.split("/", 1)
        if not instance_id or not subroute:
            return None
        return instance_id, subroute.strip("/")

    def _method_not_allowed(self) -> None:
        self._json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"ok": False, "message": "不支持该 HTTP 方法。"},
            extra_headers={"Allow": "GET, HEAD, POST"},
        )

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._validate_host_header():
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        if not self._check_token():
            self._error(HTTPStatus.FORBIDDEN, "本地访问令牌无效或已过期，请回到启动器重新打开本页面。")
            return
        parsed = urlsplit(self.path)
        nested = self._nested_instance_route(parsed.path)
        route = self._memory_admin_route(nested[1]) if nested is not None else None
        if nested is None or route is None or route[0] != "record":
            self._method_not_allowed()
            return
        payload = self._read_json_body()
        if payload is None:
            return
        instance_id, _ = nested
        try:
            self.server.context.studio.get_instance_meta(instance_id)
            manager = self.server.context.studio.get_manager(instance_id)
            self._serve_memory_admin(
                manager, route, method="PATCH", payload=payload, query=self._query(parsed)
            )
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "更新记忆失败，请查看启动器终端日志。")

    def do_TRACE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    @staticmethod
    def _assistant_unavailable(detail: str) -> dict[str, Any]:
        data_dir = default_data_dir()
        path = data_dir / "extensions" / ASSISTANT_DIR_NAME
        return {
            "ok": False,
            "installed": False,
            "path": str(path),
            "repo": ASSISTANT_REPO_URL,
            "configured": False,
            "running": False,
            "url": "",
            "configure_url": "/api/assistant/config",
            "config_path": str(data_dir / "ai-assistant" / "config.json"),
            "key_present": False,
            "key_source": "independent",
            "instance_id": "",
            "port": ASSISTANT_DEFAULT_PORT,
            "host": ASSISTANT_DEFAULT_HOST,
            "public_host": "",
            "health_url": "",
            "pid": None,
            "log_path": str(data_dir / "ai-assistant" / "assistant.log"),
            "pid_path": str(data_dir / "ai-assistant" / "assistant.pid"),
            "access_note": "AI 助手 B 只允许监听本机回环地址；远端请使用 SSH 隧道。",
            "ssh_tunnel": f"ssh -N -L {ASSISTANT_DEFAULT_PORT}:127.0.0.1:{ASSISTANT_DEFAULT_PORT} <用户名>@<服务器地址>",
            "commit": "",
            "entry": str(path / "assistant.py"),
            "readme": str(path / "README.md"),
            "entry_file": "assistant.py",
            "readme_file": "README.md",
            "entry_exists": False,
            "readme_exists": False,
            "repo_ok": False,
            "directory_exists": path.exists(),
            "branch": "",
            "dirty": None,
            "action": "",
            "message": "可选 AI 助手 B 状态暂不可用。主控制台和其他实例不受影响。",
            "error": detail,
        }

    @staticmethod
    def _prompt_profiles_unavailable(detail: str) -> dict[str, Any]:
        return {
            "ok": False,
            "prompts": [],
            "active_id": "",
            "default_prompt_id": "",
            "default_profile": None,
            "restart_required": False,
            "conversation_created": False,
            "conversation_pending": False,
            "prompt_plugin_sync_pending": False,
            "prompt_plugin_sync_pending_at": "",
            "plugin_sync_required": False,
            "message": "提示词列表接口暂不可用；主控制台和其它功能不受影响。",
            "error": detail,
        }

    @staticmethod
    def _plugin_config_unavailable(plugin_id: str, detail: str) -> dict[str, Any]:
        return {
            "ok": False,
            "plugin_id": str(plugin_id or ""),
            "schema": {},
            "values": {},
            "schema_source": "",
            "schema_path": "",
            "schema_warning": "",
            "config_path": "",
            "source": "none",
            "recovered": False,
            "recovery_source": "none",
            "warning": "",
            "warnings": [],
            "corrupt_snapshot": "",
            "api_available": False,
            "restart_required": False,
            "message": "插件配置接口暂不可用；主控制台和其它插件不受影响。",
            "error": detail,
        }

    def _serve_plugin_api_result(self, operation) -> None:
        """Run one optional plugin-market call and map errors safely.

        Plugin errors carry a deliberate public code/message.  Unexpected
        exceptions are logged server-side, while the HTTP response contains only
        a generic message so filesystem paths, remote URLs and credentials are
        not reflected back to the browser.
        """

        try:
            result = operation()
        except PluginMarketError as exc:
            self._json(exc.http_status, exc.to_public_dict())
            return
        except ValueError as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "code": "invalid_request",
                    "message": sanitize_error_text(exc),
                },
            )
            return
        except Exception as exc:  # noqa: BLE001 - isolate optional plugin surface
            traceback.print_exc()
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "code": "plugin_internal_error",
                    "message": "插件市场操作失败，详情请查看管理服务终端日志。",
                },
            )
            return
        if not isinstance(result, dict):
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "code": "plugin_invalid_response",
                    "message": "插件市场接口返回格式异常。",
                },
            )
            return
        self._json(HTTPStatus.OK, result)

    @staticmethod
    def _plugin_group_route_action(payload: dict[str, Any]) -> str:
        return str(payload.get("action") or payload.get("op") or "").strip().lower()

    @staticmethod
    def _plugin_market_source(payload: dict[str, Any]) -> Any:
        if "source" in payload:
            return payload.get("source")
        for key in ("catalog", "url", "path", "file"):
            if payload.get(key) not in {None, ""}:
                return payload.get(key)
        return None

    @staticmethod
    def _migration_service_for(manager: Any) -> Any:
        service = getattr(manager, "plugin_migration_service", None)
        if service is not None:
            return service
        data_dir = getattr(manager, "data_dir", None)
        if data_dir is None:
            raise MigrationStateError("当前后端未提供迁移服务。")
        from .plugin_transactions import PluginMigrationService

        return PluginMigrationService(data_dir)

    @staticmethod
    def _plugin_bridge_status(manager: Any) -> dict[str, Any]:
        value = getattr(manager, "plugin_control_status", None)
        if callable(value):
            try:
                result = value()
            except Exception:  # noqa: BLE001 - bridge status is a safe card
                result = None
            if isinstance(result, dict):
                return result
        if isinstance(value, dict):
            return dict(value)
        return {
            "ok": True,
            "enabled": False,
            "configured": False,
            "ready": False,
            "reachable": None,
            "transport": "none",
            "reason": "bridge_disabled",
        }

    @classmethod
    def _require_plugin_bridge(cls, manager: Any) -> dict[str, Any]:
        status = cls._plugin_bridge_status(manager)
        if not bool(status.get("ready")):
            raise MigrationBridgeUnavailable(
                "插件控制桥未就绪，迁移/控制操作已按失败关闭（bridge_unavailable）。"
            )
        return status

    def _serve_migration_result(self, operation) -> None:
        try:
            result = operation()
        except (PluginStateError, MigrationError) as exc:
            self._json(int(getattr(exc, "http_status", 400) or 400), exc.to_public_dict())
            return
        except ControlError as exc:
            self._json(int(getattr(exc, "http_status", 400) or 400), exc.to_public_dict())
            return
        except ValueError as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "code": "invalid_request", "message": sanitize_error_text(exc)},
            )
            return
        except Exception:  # noqa: BLE001 - isolate optional migration surface
            traceback.print_exc()
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "code": "migration_internal_error",
                    "message": "迁移控制服务处理失败，详情请查看管理服务终端日志。",
                },
            )
            return
        if not isinstance(result, dict):
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "code": "migration_invalid_response", "message": "迁移服务返回格式异常。"},
            )
            return
        self._json(HTTPStatus.OK, result)

    def _serve_control_result(self, operation) -> None:
        try:
            result = operation()
        except ControlError as exc:
            self._json(int(getattr(exc, "http_status", 400) or 400), exc.to_public_dict())
            return
        except (PluginStateError, MigrationError) as exc:
            self._json(int(getattr(exc, "http_status", 400) or 400), exc.to_public_dict())
            return
        except ValueError as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "code": "invalid_request", "message": sanitize_error_text(exc)},
            )
            return
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "code": "bridge_unavailable",
                    "message": "插件控制桥不可用，操作已按失败关闭。",
                },
            )
            return
        if not isinstance(result, dict):
            self._json(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "code": "control_invalid_response", "message": "插件控制桥返回格式异常。"},
            )
            return
        self._json(HTTPStatus.OK, result)

    # ------------------------------------------------------------------
    # Per-group long-term memory policy
    # ------------------------------------------------------------------
    def _memory_policy_unavailable(self, detail: str) -> dict[str, Any]:
        """Explicit unsupported state; never a fabricated saved-policy value."""

        return {
            "ok": False,
            "available": False,
            "supported": False,
            "saved": False,
            "default_mode": "",
            "private": {},
            "groups": [],
            "warnings": [],
            "config_path": "",
            "policy_path": "memory.policy",
            "mode_options": [],
            "scope_options": [],
            "limits": {},
            "memory_enabled": None,
            "read_channel": "",
            "write_channel": "",
            "readable": False,
            "writable": False,
            "container_running": False,
            "permission_denied": False,
            "actions": [],
            "patch_hint": "unknown",
            "restart_required": False,
            "hot_reload": False,
            "warning": "长期记忆只对已列出的群生效；未列出的群保持 conversation（只用会话历史）。",
            "message": "当前实例不支持保存记忆策略。",
            "error": detail,
        }

    def _serve_memory_policy(
        self,
        manager: Any,
        *,
        payload: dict[str, Any] | None = None,
        read_only: bool = False,
    ) -> None:
        method = "get_memory_policy" if read_only else "set_memory_policy"
        handler = getattr(manager, method, None)
        if not callable(handler):
            self._json(
                HTTPStatus.NOT_IMPLEMENTED,
                self._memory_policy_unavailable("当前后端未提供记忆策略接口。"),
            )
            return
        try:
            result = handler() if read_only else handler(payload)
        except DeploymentError:
            # Validation failures (400) and unsupported instances (409) are
            # reported by the outer handlers, not swallowed as "ok: false".
            raise
        except Exception as exc:  # noqa: BLE001 - optional panel isolation
            traceback.print_exc()
            self._json(
                HTTPStatus.OK,
                self._memory_policy_unavailable(f"读取记忆策略失败：{exc}"),
            )
            return
        if not isinstance(result, dict):
            result = self._memory_policy_unavailable("记忆策略接口返回格式异常。")
        self._json(HTTPStatus.OK, result)

    # ------------------------------------------------------------------
    # Memory content administration (scope-scoped read/write)
    # ------------------------------------------------------------------
    @staticmethod
    def _memory_admin_route(subroute: str) -> tuple[str, str] | None:
        """Parse ``memory/...`` subroutes; ``memory`` alone stays the mode API."""

        prefix = "memory/"
        if not subroute.startswith(prefix):
            return None
        parts = [part for part in subroute[len(prefix) :].split("/") if part]
        if not parts:
            return None
        head = parts[0]
        if head in {"stats", "forget", "export", "import", "reflect"} and len(parts) == 1:
            return head, ""
        if head == "records":
            if len(parts) == 1:
                return "records", ""
            if len(parts) == 2:
                return "record", parts[1]
        return None

    @staticmethod
    def _memory_admin_for(manager: Any) -> MemoryAdmin:
        docker = getattr(manager, "docker", None)
        data_dir = getattr(manager, "astrbot_data_dir", None)
        if data_dir is None:
            base = getattr(manager, "data_dir", None)
            data_dir = Path(base) / "data" / "astrbot" if base else Path("data") / "astrbot"
        backup_base = getattr(manager, "data_dir", None) or Path(data_dir).parent.parent
        return MemoryAdmin(
            data_dir,
            docker=docker,
            container_name=str(getattr(docker, "ASTRBOT_CONTAINER", "") or ""),
            backup_dir=Path(backup_base) / "memory-backups",
        )

    def _serve_memory_admin(
        self,
        manager: Any,
        route: tuple[str, str],
        *,
        method: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, list[str]] | None = None,
    ) -> None:
        action, tail = route
        body = payload or {}
        values = query or {}

        def pick(name: str) -> Any:
            if body.get(name) is not None:
                return body.get(name)
            found = values.get(name) or []
            return found[0] if found else ""

        try:
            admin = self._memory_admin_for(manager)
            scope_type = pick("scope_type")
            scope_id = pick("scope_id")
            if method == "GET" and action == "stats":
                result = admin.stats(scope_type, scope_id)
            elif method == "GET" and action == "records":
                result = admin.list_records(
                    scope_type,
                    scope_id,
                    query=pick("q"),
                    limit=pick("limit") or None,
                    offset=pick("offset") or None,
                )
            elif method == "GET" and action == "record":
                result = admin.get_record(scope_type, scope_id, tail)
            elif method == "POST" and action == "records":
                result = admin.create_record(scope_type, scope_id, body)
            elif method == "PATCH" and action == "record":
                result = admin.update_record(scope_type, scope_id, tail, body)
            elif method == "DELETE" and action == "record":
                result = admin.delete_record(
                    scope_type, scope_id, tail, confirm=body.get("confirm", pick("confirm"))
                )
            elif method == "POST" and action == "forget":
                result = admin.forget(scope_type, scope_id, confirm=body.get("confirm", ""))
            elif method == "POST" and action == "export":
                result = admin.export_scope(scope_type, scope_id)
            elif method == "POST" and action == "import":
                result = admin.import_records(scope_type, scope_id, body)
            elif method == "POST" and action == "reflect":
                result = admin.reflect(
                    scope_type,
                    scope_id,
                    apply_changes=bool(body.get("apply")),
                    confirm=body.get("confirm", ""),
                )
            else:
                self._method_not_allowed()
                return
        except MemoryRecordNotFound as exc:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "available": True, "message": str(exc), "error": str(exc)},
            )
            return
        except MemoryConflict as exc:
            self._json(
                HTTPStatus.CONFLICT,
                {"ok": False, "available": True, "message": str(exc), "error": str(exc)},
            )
            return
        except MemoryAdminUnavailable as exc:
            actions = list(getattr(exc, "actions", []) or [])
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "available": False,
                    "permission_denied": bool(getattr(exc, "permission_denied", False)),
                    "empty": bool(getattr(exc, "empty", False)),
                    "corrupt": bool(getattr(exc, "corrupt", False)),
                    "actions": actions,
                    "message": str(exc),
                    "error": str(exc),
                },
            )
            return
        except MemoryAdminError as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "available": True, "message": str(exc), "error": str(exc)},
            )
            return
        if not isinstance(result, dict):
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "message": "记忆管理接口返回格式异常。"},
            )
            return
        self._json(HTTPStatus.OK, result)

    # ------------------------------------------------------------------
    # HTML/static routes
    # ------------------------------------------------------------------
    def _serve_html(self, html: str) -> None:
        token = self.server.context.token
        rendered = html.replace('"{{TOKEN}}"', json.dumps(token))
        self._send_bytes(HTTPStatus.OK, rendered.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_static(self, data: bytes, content_type: str) -> None:
        if not data:
            self._error(HTTPStatus.NOT_FOUND, "静态资源不存在。")
            return
        self._send_bytes(HTTPStatus.OK, data, content_type, cache=True)

    # ------------------------------------------------------------------
    # GET routes
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self._validate_host_header():
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        query = self._query(parsed)
        if path.startswith("/api/") and not self._check_token():
            self._error(HTTPStatus.FORBIDDEN, "本地访问令牌无效或已过期，请回到启动器重新打开本页面。")
            return
        ctx = self.server.context
        try:
            if path == "/":
                self._redirect("/console")
                return
            if path in ("/assistant", "/assistant.html"):
                if ctx.assistant_html is None:
                    self._error(ctx.assistant_page_status, ctx.assistant_page_error)
                    return
                self._serve_html(ctx.assistant_html)
                return
            if path in ("/console", "/console.html"):
                self._serve_html(ctx.console_html)
                return
            if path in ("/wizard", "/wizard.html", "/index.html"):
                self._serve_html(ctx.index_html)
                return
            if path == "/styles.css":
                self._serve_static(ctx.styles, "text/css; charset=utf-8")
                return
            if path == "/app.js":
                self._serve_static(ctx.script, "text/javascript; charset=utf-8")
                return
            if path == "/console.css":
                self._serve_static(ctx.console_css, "text/css; charset=utf-8")
                return
            if path == "/console.js":
                self._serve_static(ctx.console_script, "text/javascript; charset=utf-8")
                return
            if path.startswith(("/services/", "/components/")):
                relative = urllib.parse.unquote(path.lstrip("/"))
                candidate = (ctx.static_dir / relative).resolve()
                try:
                    candidate.relative_to(ctx.static_dir.resolve())
                except ValueError:
                    self._error(HTTPStatus.NOT_FOUND, "静态资源不存在。")
                    return
                if candidate.suffix != ".js" or not candidate.is_file():
                    self._error(HTTPStatus.NOT_FOUND, "静态资源不存在。")
                    return
                self._serve_static(candidate.read_bytes(), "text/javascript; charset=utf-8")
                return
            if path in ("/favicon.ico", "/assets/qqbot-studio.svg"):
                icon = Path(__file__).parent.parent / "assets" / "qqbot-studio.svg"
                if icon.exists():
                    self._send_bytes(HTTPStatus.OK, icon.read_bytes(), "image/svg+xml", cache=True)
                else:
                    self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/svg+xml")
                return
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"ok": True, "version": __version__})
                return
            if path == "/api/update/check":
                self._json(HTTPStatus.OK, check_update())
                return
            if path == "/api/update/status":
                self._json(HTTPStatus.OK, update_status(data_dir=ctx.studio.root))
                return
            if path == "/api/update/versions":
                channel = (query.get("channel") or ["stable"])[0]
                self._json(
                    HTTPStatus.OK,
                    list_versions(channel=channel, data_dir=ctx.studio.root),
                )
                return
            if path == "/api/update/history":
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "history": load_history(ctx.studio.root)},
                )
                return
            if path == "/api/settings":
                settings = ctx.studio.load_settings()
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "bind_host": settings.get("bind_host"),
                        "bind_port": settings.get("bind_port"),
                        "public_host": ctx.studio.public_host(),
                        "first_run_complete": bool(settings.get("first_run_complete")),
                        "mirror_nodes": ctx.studio.list_mirror_nodes(),
                        "selected_mirror_id": settings.get("selected_mirror_id"),
                    },
                )
                return
            if path == "/api/assistant/config":
                getter = getattr(ctx.studio, "assistant_config", None)
                if not callable(getter):
                    self._json(
                        HTTPStatus.OK,
                        self._assistant_unavailable("当前后端未提供 AI 助手 B 配置接口。"),
                    )
                    return
                try:
                    payload = getter()
                except Exception as exc:  # noqa: BLE001 - optional feature isolation
                    traceback.print_exc()
                    payload = self._assistant_unavailable(str(exc))
                if not isinstance(payload, dict):
                    payload = self._assistant_unavailable("AI 助手 B 配置返回格式异常。")
                self._json(HTTPStatus.OK, payload)
                return
            if path == "/api/assistant/status":
                getter = getattr(ctx.studio, "assistant_status", None)
                if not callable(getter):
                    self._json(
                        HTTPStatus.OK,
                        self._assistant_unavailable("当前后端未提供 AI 助手 B 管理接口。"),
                    )
                    return
                try:
                    payload = getter()
                except Exception as exc:  # noqa: BLE001 - optional feature isolation
                    traceback.print_exc()
                    payload = self._assistant_unavailable(str(exc))
                if not isinstance(payload, dict):
                    payload = self._assistant_unavailable("AI 助手 B 状态返回格式异常。")
                # The install-status endpoint also exposes the safe runtime
                # configuration summary so the console can show/open the URL
                # without a second request.  Config getter never returns keys.
                config_getter = getattr(ctx.studio, "assistant_config", None)
                if callable(config_getter):
                    try:
                        config_payload = config_getter()
                    except Exception:  # noqa: BLE001 - optional data only
                        config_payload = None
                    if isinstance(config_payload, dict):
                        for field in (
                            "configured",
                            "running",
                            "url",
                            "configure_url",
                            "config_path",
                            "key_present",
                            "key_source",
                            "instance_id",
                            "port",
                            "host",
                            "public_host",
                            "health_url",
                            "pid",
                            "log_path",
                            "pid_path",
                            "access_note",
                            "ssh_tunnel",
                        ):
                            if field in config_payload:
                                payload[field] = config_payload[field]
                self._json(HTTPStatus.OK, payload)
                return
            if path == "/api/assistant/diagnose":
                getter = getattr(ctx.studio, "assistant_diagnose", None)
                if not callable(getter):
                    payload = self._assistant_unavailable(
                        "当前后端未提供 AI 助手 B 诊断接口。"
                    )
                else:
                    try:
                        payload = getter()
                    except Exception as exc:  # noqa: BLE001 - optional feature isolation
                        # Never print arbitrary exception text: it can contain
                        # credentials copied from a malformed config.
                        print(
                            f"[assistant] diagnose failed: {exc.__class__.__name__}",
                            file=sys.stderr,
                        )
                        payload = self._assistant_unavailable(
                            "AI 助手 B 诊断失败；详情请查看管理服务终端日志。"
                        )
                    if not isinstance(payload, dict):
                        payload = self._assistant_unavailable(
                            "AI 助手 B 诊断返回格式异常。"
                        )
                payload.setdefault("healthy", False)
                payload.setdefault("error_code", "diagnose_unavailable")
                payload.setdefault("listener", {})
                payload.setdefault(
                    "local_health",
                    {"ok": False, "url": "", "detail": "", "error": "unavailable"},
                )
                payload.setdefault(
                    "public_probe",
                    {
                        "checked": False,
                        "skipped": True,
                        "status": "skipped",
                        "detail": "诊断接口不可用。",
                    },
                )
                payload.setdefault("old_b_suspected", False)
                payload.setdefault("log_tail", "")
                payload.setdefault("suggestions", [])
                payload.setdefault("recommendations", [])
                self._json(HTTPStatus.OK, payload)
                return
            if path == "/api/instances":
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "instances": ctx.studio.list_instance_summaries()},
                )
                return
            nested = self._nested_instance_route(path)
            if nested is not None:
                instance_id, subroute = nested
                ctx.studio.get_instance_meta(instance_id)
                manager = ctx.studio.get_manager(instance_id)
                if subroute == "plugin-migrations/status":
                    try:
                        service = self._migration_service_for(manager)
                        bridge_status = self._plugin_bridge_status(manager)
                        status = service.status(bridge_status=bridge_status)
                    except (PluginStateError, MigrationError) as exc:
                        self._json(int(getattr(exc, "http_status", 503) or 503), exc.to_public_dict())
                        return
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                        self._json(
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            {"ok": False, "code": "migration_internal_error", "message": "迁移状态读取失败。"},
                        )
                        return
                    self._json(
                        HTTPStatus.OK if status.get("ok") else HTTPStatus.SERVICE_UNAVAILABLE,
                        status,
                    )
                    return
                if subroute.startswith("plugin-migration-plans/"):
                    plan_id = urllib.parse.unquote(subroute[len("plugin-migration-plans/") :]).strip("/")
                    if not plan_id or "/" in plan_id:
                        self._error(HTTPStatus.NOT_FOUND, "迁移计划不存在。")
                        return
                    self._serve_migration_result(lambda: self._migration_service_for(manager).get_plan(plan_id))
                    return
                if subroute.startswith("plugin-migration-operations/"):
                    operation_id = urllib.parse.unquote(
                        subroute[len("plugin-migration-operations/") :]
                    ).strip("/")
                    if not operation_id or "/" in operation_id:
                        self._error(HTTPStatus.NOT_FOUND, "迁移操作不存在。")
                        return
                    self._serve_migration_result(
                        lambda: self._migration_service_for(manager).get_operation(operation_id)
                    )
                    return
                if subroute.startswith("plugin-migration-rollback-plans/"):
                    plan_id = urllib.parse.unquote(
                        subroute[len("plugin-migration-rollback-plans/") :]
                    ).strip("/")
                    if not plan_id or "/" in plan_id:
                        self._error(HTTPStatus.NOT_FOUND, "回滚计划不存在。")
                        return
                    self._serve_migration_result(
                        lambda: self._migration_service_for(manager).get_rollback_plan(plan_id)
                    )
                    return
                if subroute == "plugin-control/status":
                    bridge = self._plugin_bridge_status(manager)
                    self._json(HTTPStatus.OK, {"ok": True, "bridge": bridge})
                    return
                if subroute == "plugin-control/methods":
                    methods = [
                        {
                            "method": spec.name,
                            "provider": spec.provider,
                            "access": spec.access,
                        }
                        for name, spec in sorted(FIXED_RPC_METHODS.items())
                    ]
                    self._json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "protocol_version": 1,
                            "methods": methods,
                            "bridge": self._plugin_bridge_status(manager),
                        },
                    )
                    return
                if subroute == "prompts":
                    getter = getattr(manager, "get_prompt_profiles", None)
                    if not callable(getter):
                        self._json(
                            HTTPStatus.OK,
                            self._prompt_profiles_unavailable(
                                "当前后端未提供提示词列表接口。"
                            ),
                        )
                        return
                    try:
                        result = getter()
                    except Exception:  # noqa: BLE001 - optional endpoint isolation
                        traceback.print_exc()
                        result = self._prompt_profiles_unavailable(
                            "读取提示词列表失败。"
                        )
                    if not isinstance(result, dict):
                        result = self._prompt_profiles_unavailable(
                            "提示词列表返回格式异常。"
                        )
                    self._json(HTTPStatus.OK, result)
                    return
                plugin_config_parts = subroute.split("/")
                if (
                    len(plugin_config_parts) == 3
                    and plugin_config_parts[0] == "plugins"
                    and plugin_config_parts[1]
                    and plugin_config_parts[2] == "config"
                ):
                    plugin_id = plugin_config_parts[1]
                    getter = getattr(manager, "get_plugin_config", None)
                    if not callable(getter):
                        self._json(
                            HTTPStatus.OK,
                            self._plugin_config_unavailable(
                                plugin_id,
                                "当前后端未提供插件参数读取接口。",
                            ),
                        )
                        return
                    try:
                        result = getter(plugin_id)
                    except Exception:  # noqa: BLE001 - optional endpoint isolation
                        traceback.print_exc()
                        result = self._plugin_config_unavailable(
                            plugin_id, "读取插件参数失败。"
                        )
                    if not isinstance(result, dict):
                        result = self._plugin_config_unavailable(
                            plugin_id, "插件参数返回格式异常。"
                        )
                    self._json(HTTPStatus.OK, result)
                    return
                if subroute in {"plugins", "plugins/list"}:
                    getter = getattr(manager, "get_plugins", None)
                    if not callable(getter):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件列表接口。")
                        return
                    self._json(HTTPStatus.OK, getter())
                    return
                if subroute == "plugin-market":
                    getter = getattr(manager, "get_plugin_market", None)
                    if not callable(getter):
                        self._error(
                            HTTPStatus.NOT_IMPLEMENTED,
                            "当前后端未提供插件市场接口。",
                        )
                        return
                    self._serve_plugin_api_result(lambda: getter())
                    return
                if subroute == "plugin-groups":
                    getter = getattr(manager, "get_plugin_groups", None)
                    if not callable(getter):
                        self._error(
                            HTTPStatus.NOT_IMPLEMENTED,
                            "当前后端未提供插件分组接口。",
                        )
                        return
                    self._serve_plugin_api_result(lambda: getter())
                    return
                if subroute.startswith("plugin-operations/"):
                    operation_id = subroute[len("plugin-operations/") :].strip("/")
                    if not operation_id or "/" in operation_id:
                        self._error(HTTPStatus.NOT_FOUND, "插件操作不存在。")
                        return
                    getter = getattr(manager, "get_plugin_operation", None)
                    if not callable(getter):
                        self._error(
                            HTTPStatus.NOT_IMPLEMENTED,
                            "当前后端未提供插件操作查询接口。",
                        )
                        return
                    self._serve_plugin_api_result(lambda: getter(operation_id))
                    return
                memory_route = self._memory_admin_route(subroute)
                if memory_route is not None:
                    self._serve_memory_admin(manager, memory_route, method="GET", query=query)
                    return
                if subroute == "plugin-policy":
                    caller = getattr(manager, "plugin_control_call", None)
                    if not callable(caller):
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "ok": False,
                                "code": "bridge_unavailable",
                                "message": "当前后端未提供插件控制桥。",
                            },
                        )
                        return
                    self._serve_control_result(
                        lambda: caller(
                            "policy.snapshot",
                            scope=("admin", "global"),
                            body={},
                        )
                    )
                    return
                if subroute == "memory-policy":
                    self._serve_memory_policy(manager, read_only=True)
                    return
                if subroute == "group-whitelist":
                    getter = getattr(manager, "get_group_whitelist", None)
                    if not callable(getter):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供群组白名单接口。")
                        return
                    self._json(HTTPStatus.OK, getter())
                    return
                if subroute == "memory":
                    getter = getattr(manager, "get_memory_mode", None)
                    if not callable(getter):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供记忆模式接口。")
                        return
                    self._json(HTTPStatus.OK, getter())
                    return
                if subroute == "stats":
                    getter = getattr(ctx.studio, "get_stats", None)
                    if not callable(getter):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供资源统计接口。")
                        return
                    self._json(HTTPStatus.OK, getter(instance_id))
                    return
                self._error(HTTPStatus.NOT_FOUND, "接口不存在")
                return
            if path == "/api/memory-policy":
                instance_id = ctx.instance_id(query)
                manager = ctx.studio.get_manager(instance_id)
                self._serve_memory_policy(manager, read_only=True)
                return
            if path == "/api/memory":
                instance_id = ctx.instance_id(query)
                manager = ctx.studio.get_manager(instance_id)
                getter = getattr(manager, "get_memory_mode", None)
                if not callable(getter):
                    self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供记忆模式接口。")
                    return
                self._json(HTTPStatus.OK, getter())
                return
            if path == "/api/plugins":
                instance_id = ctx.instance_id(query)
                manager = ctx.studio.get_manager(instance_id)
                getter = getattr(manager, "get_plugins", None)
                if not callable(getter):
                    self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件列表接口。")
                    return
                self._json(HTTPStatus.OK, getter())
                return
            if path in {"/api/plugins/config", "/api/plugin-config"}:
                instance_id = ctx.instance_id(query)
                manager = ctx.studio.get_manager(instance_id)
                plugin_id = str((query.get("plugin_id") or [""])[0]).strip()
                if not plugin_id:
                    self._json(
                        HTTPStatus.OK,
                        self._plugin_config_unavailable("", "缺少 plugin_id 查询参数。"),
                    )
                    return
                getter = getattr(manager, "get_plugin_config", None)
                if not callable(getter):
                    self._json(
                        HTTPStatus.OK,
                        self._plugin_config_unavailable(
                            plugin_id, "当前后端未提供插件参数读取接口。"
                        ),
                    )
                    return
                try:
                    result = getter(plugin_id)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    result = self._plugin_config_unavailable(
                        plugin_id, "读取插件参数失败。"
                    )
                if not isinstance(result, dict):
                    result = self._plugin_config_unavailable(
                        plugin_id, "插件参数返回格式异常。"
                    )
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/stats":
                getter = getattr(ctx.studio, "get_stats", None)
                if not callable(getter):
                    self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供资源统计接口。")
                    return
                explicit_instance = str((query.get("instance") or [""])[0]).strip()
                self._json(HTTPStatus.OK, getter(explicit_instance or None))
                return
            if path == "/api/mirrors":
                settings = ctx.studio.load_settings()
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "nodes": ctx.studio.list_mirror_nodes(),
                        "selected_id": settings.get("selected_mirror_id"),
                    },
                )
                return
            if path == "/api/state":
                instance_id = ctx.instance_id(query)
                manager = ctx.studio.get_manager(instance_id)
                try:
                    state = manager.get_status()
                except Exception as exc:  # noqa: BLE001 - return a diagnosable card state
                    traceback.print_exc()
                    state = {
                        "app_version": __version__,
                        "requirements": {},
                        "deployment": {},
                        "notice": f"实例状态读取失败：{exc}",
                        "status_error": str(exc),
                    }
                meta = ctx.studio.get_instance_meta(instance_id)
                state["instance"] = {"id": instance_id, "name": meta.get("name") or instance_id}
                self._json(HTTPStatus.OK, state)
                return
            if path.startswith("/api/job/"):
                instance_id = ctx.instance_id(query)
                manager = ctx.studio.get_manager(instance_id)
                job_id = urllib.parse.unquote(path[len("/api/job/") :]).strip()
                if not job_id or len(job_id) > 120:
                    self._error(HTTPStatus.BAD_REQUEST, "无效的任务 ID")
                    return
                job = manager.get_job(job_id)
                if job is None:
                    self._error(HTTPStatus.NOT_FOUND, "找不到该部署任务。")
                    return
                self._json(HTTPStatus.OK, job)
                return
            if path == "/api/logs":
                instance_id = ctx.instance_id(query)
                manager = ctx.studio.get_manager(instance_id)
                service = (query.get("service") or [""])[0]
                lines = self._safe_int((query.get("lines") or ["200"])[0], 200, 1, 1000)
                if service not in {"astrbot", "snowluma"}:
                    self._error(HTTPStatus.BAD_REQUEST, "service 只能是 astrbot 或 snowluma")
                    return
                self._json(HTTPStatus.OK, {"ok": True, "logs": manager.get_logs(service, lines)})
                return
            self._error(HTTPStatus.NOT_FOUND, "接口不存在")
        except MemoryPolicyWriteUnavailableError as exc:
            # A permission/container problem is not "unsupported": keep the
            # instance editable-looking and return repair steps, never a fake save.
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "available": False,
                    "supported": True,
                    "saved": False,
                    "permission_denied": True,
                    "actions": list(getattr(exc, "actions", []) or []),
                    "message": str(exc),
                    "error": str(exc),
                },
            )
        except MemoryPolicyUnsupportedError as exc:
            self._json(
                HTTPStatus.CONFLICT,
                {"ok": False, "available": False, "supported": False, "message": str(exc)},
            )
        except (DeploymentError, ValueError, UpdateError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except DockerError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except AstrBotAPIError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "本地服务处理请求失败，请查看启动器终端日志。")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    # ------------------------------------------------------------------
    # POST routes
    # ------------------------------------------------------------------
    def _read_json_body(
        self,
        *,
        allow_empty: bool = False,
        max_bytes: int | None = None,
    ) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length == 0 and allow_empty:
            return {}
        content_type = str(self.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "请求必须使用 application/json。")
            return None
        request_limit = int(max_bytes or DEFAULT_REQUEST_BODY_MAX_BYTES)
        if length <= 0 or length > request_limit:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求体为空或过大。")
            return None
        try:
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (UnicodeDecodeError, ValueError):
            self._error(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON object。")
            return None

    def do_POST(self) -> None:  # noqa: N802
        if not self._validate_host_header():
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        if not self._check_token():
            self._error(HTTPStatus.FORBIDDEN, "本地访问令牌无效或已过期，请回到启动器重新打开本页面。")
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        stripped_path = path.rstrip("/")
        large_prompt_payload = (
            stripped_path.endswith("/prompts")
            or (
                stripped_path.endswith("/config")
                and (
                    "/plugins/" in stripped_path
                    or stripped_path in {"/api/plugins/config", "/api/plugin-config"}
                )
            )
        )
        payload = self._read_json_body(
            allow_empty=(
                path in {
                    "/api/assistant/install",
                    "/api/assistant/update",
                    "/api/assistant/start",
                    "/api/assistant/stop",
                }
                or stripped_path.endswith("/plugin-market/refresh")
            ),
            max_bytes=(
                PROMPT_PROFILES_MAX_REQUEST_BYTES
                if large_prompt_payload
                else DEFAULT_REQUEST_BODY_MAX_BYTES
            ),
        )
        if payload is None:
            return
        query = self._query(parsed)
        ctx = self.server.context
        try:
            if path == "/api/update/apply":
                if ctx.studio.has_active_deployments():
                    raise ValueError("有部署任务正在运行，不能自动更新。请在部署完成后再试。")
                channel = str(payload.get("channel") or "stable").strip()
                test_ref = str(payload.get("test_ref") or "").strip()
                result = start_update(
                    channel=channel,
                    data_dir=ctx.studio.root,
                    test_ref=test_ref,
                )
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/update/rollback":
                if ctx.studio.has_active_deployments():
                    raise ValueError("有部署任务正在运行，不能回滚。请在部署完成后再试。")
                target = str(payload.get("target") or "").strip()
                result = rollback(
                    target,
                    data_dir=ctx.studio.root,
                    allow_schema_downgrade=bool(payload.get("allow_schema_downgrade")),
                )
                self._json(HTTPStatus.OK, result)
                return
            if path in {"/api/assistant/install", "/api/assistant/update"}:
                method_name = "install_assistant" if path.endswith("/install") else "update_assistant"
                operation = getattr(ctx.studio, method_name, None)
                if not callable(operation):
                    self._json(
                        HTTPStatus.OK,
                        self._assistant_unavailable("当前后端未提供 AI 助手 B 安装/更新接口。"),
                    )
                    return
                try:
                    result = operation()
                except Exception as exc:  # noqa: BLE001 - optional feature isolation
                    traceback.print_exc()
                    result = self._assistant_unavailable(
                        f"AI 助手 B 安装/更新失败：{exc}。主项目仍可正常使用。"
                    )
                if not isinstance(result, dict):
                    result = self._assistant_unavailable("AI 助手 B 操作返回格式异常。")
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/assistant/config":
                saver = getattr(ctx.studio, "save_assistant_config", None)
                if not callable(saver):
                    self._json(
                        HTTPStatus.OK,
                        self._assistant_unavailable("当前后端未提供 AI 助手 B 配置接口。"),
                    )
                    return
                try:
                    result = saver(payload)
                except Exception as exc:  # noqa: BLE001 - optional feature isolation
                    traceback.print_exc()
                    result = self._assistant_unavailable(
                        f"AI 助手 B 配置保存失败：{exc}。主项目仍可正常使用。"
                    )
                if not isinstance(result, dict):
                    result = self._assistant_unavailable("AI 助手 B 配置返回格式异常。")
                self._json(HTTPStatus.OK, result)
                return
            if path in {"/api/assistant/start", "/api/assistant/stop"}:
                method_name = "start_assistant" if path.endswith("/start") else "stop_assistant"
                operation = getattr(ctx.studio, method_name, None)
                if not callable(operation):
                    self._json(
                        HTTPStatus.OK,
                        self._assistant_unavailable("当前后端未提供 AI 助手 B 启停接口。"),
                    )
                    return
                try:
                    result = operation()
                except Exception as exc:  # noqa: BLE001 - optional feature isolation
                    traceback.print_exc()
                    result = self._assistant_unavailable(
                        f"AI 助手 B 启停失败：{exc}。主项目仍可正常使用。"
                    )
                if not isinstance(result, dict):
                    result = self._assistant_unavailable("AI 助手 B 启停返回格式异常。")
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/instances":
                name = str(payload.get("name") or "").strip()
                meta = ctx.studio.create_instance(name or None)
                self._json(HTTPStatus.OK, {"ok": True, "instance": meta})
                return
            if path == "/api/mirrors":
                node = ctx.studio.add_mirror_node(
                    str(payload.get("name") or ""),
                    str(payload.get("prefix") or ""),
                )
                self._json(HTTPStatus.OK, {"ok": True, "node": node})
                return
            if path == "/api/mirrors/test":
                ids = payload.get("ids")
                if ids is not None and not isinstance(ids, list):
                    raise ValueError("ids 必须为数组")
                results = ctx.studio.test_mirrors([str(item) for item in ids] if ids else None)
                settings = ctx.studio.load_settings()
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "results": results,
                        "nodes": ctx.studio.list_mirror_nodes(),
                        "selected_id": settings.get("selected_mirror_id"),
                    },
                )
                return
            if path == "/api/mirrors/select":
                ctx.studio.select_mirror(str(payload.get("id") or ""))
                self._json(HTTPStatus.OK, {"ok": True})
                return

            nested = self._nested_instance_route(path)
            if nested is not None:
                instance_id, subroute = nested
                ctx.studio.get_instance_meta(instance_id)
                manager = ctx.studio.get_manager(instance_id)
                if subroute == "plugin-migration-plans":
                    self._serve_migration_result(
                        lambda: self._migration_service_for(manager).create_plan(
                            payload, actor="studio-admin"
                        )
                    )
                    return
                if subroute == "plugin-migration-operations":
                    try:
                        self._require_plugin_bridge(manager)
                        operation_result = self._migration_service_for(manager).start_operation(
                            payload, actor="studio-admin", run_async=True
                        )
                    except (PluginStateError, MigrationError) as exc:
                        self._json(int(getattr(exc, "http_status", 400) or 400), exc.to_public_dict())
                        return
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                        self._json(
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            {
                                "ok": False,
                                "code": "migration_internal_error",
                                "message": "迁移操作创建失败，详情请查看管理服务终端日志。",
                            },
                        )
                        return
                    self._json(HTTPStatus.ACCEPTED, operation_result)
                    return
                if subroute == "plugin-migration-rollback-plans":
                    self._serve_migration_result(
                        lambda: self._migration_service_for(manager).create_rollback_plan(
                            payload, actor="studio-admin"
                        )
                    )
                    return
                if subroute == "plugin-migration-rollback-operations":
                    try:
                        self._require_plugin_bridge(manager)
                        operation_result = self._migration_service_for(manager).start_rollback(
                            payload, actor="studio-admin", run_async=True
                        )
                    except (PluginStateError, MigrationError) as exc:
                        self._json(int(getattr(exc, "http_status", 400) or 400), exc.to_public_dict())
                        return
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                        self._json(
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            {
                                "ok": False,
                                "code": "migration_internal_error",
                                "message": "回滚操作创建失败，详情请查看管理服务终端日志。",
                            },
                        )
                        return
                    self._json(HTTPStatus.ACCEPTED, operation_result)
                    return
                if subroute.startswith("plugin-migration-operations/") and subroute.endswith("/resume"):
                    operation_id = urllib.parse.unquote(
                        subroute[len("plugin-migration-operations/") : -len("/resume")]
                    ).strip("/")
                    if not operation_id or "/" in operation_id:
                        self._error(HTTPStatus.NOT_FOUND, "迁移操作不存在。")
                        return
                    force_manual = bool(payload.get("force_manual"))
                    self._serve_migration_result(
                        lambda: self._migration_service_for(manager).resume_operation(
                            operation_id, force_manual=force_manual
                        )
                    )
                    return
                if subroute == "plugin-control/calls":
                    method = str(payload.get("method") or "").strip()
                    if method not in FIXED_RPC_METHODS:
                        self._json(
                            HTTPStatus.BAD_REQUEST,
                            {
                                "ok": False,
                                "code": "control_method_not_allowed",
                                "message": "未登记的固定 RPC 方法，已拒绝。",
                            },
                        )
                        return
                    bridge_status = self._plugin_bridge_status(manager)
                    if not bool(bridge_status.get("ready")):
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "ok": False,
                                "code": "bridge_unavailable",
                                "message": "插件控制桥未就绪，固定 RPC 调用已按失败关闭。",
                            },
                        )
                        return
                    caller = getattr(manager, "plugin_control_call", None)
                    if not callable(caller):
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "ok": False,
                                "code": "bridge_unavailable",
                                "message": "当前后端未提供固定 RPC 客户端，已按失败关闭。",
                            },
                        )
                        return
                    scope = payload.get("scope")
                    body = payload.get("body")
                    if body is None:
                        body = {}
                    if not isinstance(body, dict):
                        self._json(
                            HTTPStatus.BAD_REQUEST,
                            {
                                "ok": False,
                                "code": "control_invalid_payload",
                                "message": "body 必须是 JSON object。",
                            },
                        )
                        return
                    self._serve_control_result(lambda: caller(method, scope=scope, body=body))
                    return
                if subroute == "plugin-market/refresh":
                    refresher = getattr(manager, "refresh_plugin_market", None)
                    if not callable(refresher):
                        self._error(
                            HTTPStatus.NOT_IMPLEMENTED,
                            "当前后端未提供插件市场刷新接口。",
                        )
                        return
                    source = self._plugin_market_source(payload)
                    self._serve_plugin_api_result(lambda: refresher(source))
                    return
                if subroute == "plugin-groups/preview":
                    previewer = getattr(manager, "preview_plugin_groups", None)
                    if not callable(previewer):
                        self._error(
                            HTTPStatus.NOT_IMPLEMENTED,
                            "当前后端未提供插件分组预览接口。",
                        )
                        return
                    self._serve_plugin_api_result(lambda: previewer(payload))
                    return
                if subroute == "plugin-groups/apply":
                    applier = getattr(manager, "apply_plugin_groups", None)
                    if not callable(applier):
                        self._error(
                            HTTPStatus.NOT_IMPLEMENTED,
                            "当前后端未提供插件分组应用接口。",
                        )
                        return
                    self._serve_plugin_api_result(lambda: applier(payload))
                    return
                if subroute == "plugin-groups":
                    action = self._plugin_group_route_action(payload)
                    if action == "preview":
                        previewer = getattr(manager, "preview_plugin_groups", None)
                        if not callable(previewer):
                            self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件分组预览接口。")
                            return
                        self._serve_plugin_api_result(lambda: previewer(payload))
                        return
                    if action == "apply" or payload.get("preview_token"):
                        applier = getattr(manager, "apply_plugin_groups", None)
                        if not callable(applier):
                            self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件分组应用接口。")
                            return
                        self._serve_plugin_api_result(lambda: applier(payload))
                        return
                    executor = getattr(manager, "execute_plugin_group_action", None)
                    if not callable(executor):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件分组写入接口。")
                        return
                    self._serve_plugin_api_result(lambda: executor(payload))
                    return
                if subroute == "plugin-operations/preview":
                    previewer = getattr(manager, "preview_plugin_operation", None)
                    if not callable(previewer):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件操作预览接口。")
                        return
                    self._serve_plugin_api_result(lambda: previewer(payload))
                    return
                if subroute == "plugin-operations":
                    executor = getattr(manager, "execute_plugin_operation", None)
                    if not callable(executor):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件操作接口。")
                        return
                    self._serve_plugin_api_result(lambda: executor(payload))
                    return
                memory_route = self._memory_admin_route(subroute)
                if memory_route is not None:
                    self._serve_memory_admin(
                        manager, memory_route, method="POST", payload=payload, query=query
                    )
                    return
                if subroute == "plugin-policy":
                    caller = getattr(manager, "plugin_control_call", None)
                    if not callable(caller):
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {
                                "ok": False,
                                "code": "bridge_unavailable",
                                "message": "当前后端未提供插件控制桥。",
                            },
                        )
                        return
                    action = str(payload.get("action") or "set").strip()
                    if action not in {"set", "reset"}:
                        self._json(
                            HTTPStatus.BAD_REQUEST,
                            {"ok": False, "code": "invalid_action", "message": "action 必须是 set 或 reset。"},
                        )
                        return
                    method = "policy.set" if action == "set" else "policy.reset"
                    body = {
                        key: payload[key]
                        for key in (
                            "plugin_id",
                            "group_id",
                            "user_id",
                            "is_private",
                            "enabled",
                            "params",
                            "expected_revision",
                        )
                        if key in payload
                    }
                    self._serve_control_result(
                        lambda: caller(
                            method,
                            scope=("admin", "global"),
                            body=body,
                        )
                    )
                    return
                if subroute == "memory-policy":
                    self._serve_memory_policy(manager, payload=payload)
                    return
                if subroute == "prompts":
                    updater = getattr(manager, "update_prompt_profiles", None)
                    if not callable(updater):
                        self._json(
                            HTTPStatus.OK,
                            self._prompt_profiles_unavailable(
                                "当前后端未提供提示词列表接口。"
                            ),
                        )
                        return
                    try:
                        result = updater(payload)
                    except Exception:  # noqa: BLE001 - optional endpoint isolation
                        traceback.print_exc()
                        result = self._prompt_profiles_unavailable(
                            "更新提示词列表失败。"
                        )
                    if not isinstance(result, dict):
                        result = self._prompt_profiles_unavailable(
                            "提示词列表返回格式异常。"
                        )
                    self._json(HTTPStatus.OK, result)
                    return
                plugin_config_parts = subroute.split("/")
                if (
                    len(plugin_config_parts) == 3
                    and plugin_config_parts[0] == "plugins"
                    and plugin_config_parts[1]
                    and plugin_config_parts[2] == "config"
                ):
                    plugin_id = plugin_config_parts[1]
                    setter = getattr(manager, "set_plugin_config", None)
                    if not callable(setter):
                        self._json(
                            HTTPStatus.OK,
                            self._plugin_config_unavailable(
                                plugin_id,
                                "当前后端未提供插件参数保存接口。",
                            ),
                        )
                        return
                    restart_value = payload.get("restart", False)
                    if not isinstance(restart_value, bool):
                        restart_value = False
                    try:
                        result = setter(
                            plugin_id,
                            payload.get("values"),
                            restart=restart_value,
                        )
                    except Exception:  # noqa: BLE001 - optional endpoint isolation
                        traceback.print_exc()
                        result = self._plugin_config_unavailable(
                            plugin_id, "保存插件参数失败。"
                        )
                    if not isinstance(result, dict):
                        result = self._plugin_config_unavailable(
                            plugin_id, "插件参数返回格式异常。"
                        )
                    self._json(HTTPStatus.OK, result)
                    return
                if subroute in {"plugins", "plugins/toggle"}:
                    plugin_id = str(payload.get("plugin_id") or payload.get("id") or "").strip()
                    if not plugin_id:
                        raise ValueError("缺少 plugin_id。")
                    if not isinstance(payload.get("enabled"), bool):
                        raise ValueError("enabled 必须为 true 或 false。")
                    setter = getattr(manager, "set_plugin_enabled", None)
                    if not callable(setter):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件启停接口。")
                        return
                    self._json(
                        HTTPStatus.OK,
                        setter(plugin_id, bool(payload["enabled"])),
                    )
                    return
                if subroute == "group-whitelist":
                    setter = getattr(manager, "set_group_whitelist", None)
                    if not callable(setter):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供群组白名单接口。")
                        return
                    self._json(HTTPStatus.OK, setter(payload))
                    return
                if subroute == "memory":
                    mode = str(payload.get("mode") or payload.get("memory_mode") or "").strip()
                    setter = getattr(manager, "set_memory_mode", None)
                    if not callable(setter):
                        self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供记忆模式接口。")
                        return
                    self._json(HTTPStatus.OK, setter(mode))
                    return
                self._error(HTTPStatus.NOT_FOUND, "接口不存在")
                return

            instance_id = ctx.instance_id(query, payload)
            manager = ctx.studio.get_manager(instance_id)
            if path in {"/api/plugins/config", "/api/plugin-config"}:
                plugin_id = str(payload.get("plugin_id") or (query.get("plugin_id") or [""])[0]).strip()
                if not plugin_id:
                    self._json(
                        HTTPStatus.OK,
                        self._plugin_config_unavailable("", "缺少 plugin_id。"),
                    )
                    return
                setter = getattr(manager, "set_plugin_config", None)
                if not callable(setter):
                    self._json(
                        HTTPStatus.OK,
                        self._plugin_config_unavailable(
                            plugin_id, "当前后端未提供插件参数保存接口。"
                        ),
                    )
                    return
                restart_value = payload.get("restart", False)
                if not isinstance(restart_value, bool):
                    restart_value = False
                try:
                    result = setter(
                        plugin_id,
                        payload.get("values"),
                        restart=restart_value,
                    )
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    result = self._plugin_config_unavailable(
                        plugin_id, "保存插件参数失败。"
                    )
                if not isinstance(result, dict):
                    result = self._plugin_config_unavailable(
                        plugin_id, "插件参数返回格式异常。"
                    )
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/memory-policy":
                instance_id = ctx.instance_id(query, payload)
                manager = ctx.studio.get_manager(instance_id)
                self._serve_memory_policy(manager, payload=payload)
                return
            if path == "/api/memory":
                mode = str(payload.get("mode") or payload.get("memory_mode") or "").strip()
                setter = getattr(manager, "set_memory_mode", None)
                if not callable(setter):
                    self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供记忆模式接口。")
                    return
                self._json(HTTPStatus.OK, setter(mode))
                return
            if path == "/api/plugins":
                plugin_id = str(payload.get("plugin_id") or payload.get("id") or "").strip()
                if not plugin_id:
                    raise ValueError("缺少 plugin_id。")
                if not isinstance(payload.get("enabled"), bool):
                    raise ValueError("enabled 必须为 true 或 false。")
                setter = getattr(manager, "set_plugin_enabled", None)
                if not callable(setter):
                    self._error(HTTPStatus.NOT_IMPLEMENTED, "当前后端未提供插件启停接口。")
                    return
                self._json(HTTPStatus.OK, setter(plugin_id, bool(payload["enabled"])))
                return
            if path == "/api/test-key":
                self._json(HTTPStatus.OK, manager.test_deepseek_key(str(payload.get("deepseek_api_key") or "")))
                return
            if path == "/api/deploy":
                job_id = manager.start_deploy(
                    qq_account=str(payload.get("qq_account") or ""),
                    deepseek_api_key=str(payload.get("deepseek_api_key") or ""),
                    prompt=str(payload.get("prompt") or ""),
                    accept_risk=bool(payload.get("accept_risk")),
                    mirror_candidates=ctx.studio.candidate_mirror_prefixes(),
                )
                self._json(HTTPStatus.OK, {"ok": True, "job_id": job_id})
                return
            if path == "/api/action":
                message = manager.container_action(str(payload.get("action") or ""))
                self._json(HTTPStatus.OK, {"ok": True, "message": message})
                return
            if path == "/api/snowluma-reset":
                result = manager.reset_snowluma_password()
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/persona":
                message = manager.update_persona(str(payload.get("prompt") or ""))
                self._json(HTTPStatus.OK, {"ok": True, "message": message})
                return
            self._error(HTTPStatus.NOT_FOUND, "接口不存在")
        except MemoryPolicyWriteUnavailableError as exc:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "available": False,
                    "supported": True,
                    "saved": False,
                    "permission_denied": True,
                    "actions": list(getattr(exc, "actions", []) or []),
                    "message": str(exc),
                    "error": str(exc),
                },
            )
        except MemoryPolicyUnsupportedError as exc:
            self._json(
                HTTPStatus.CONFLICT,
                {"ok": False, "available": False, "supported": False, "message": str(exc)},
            )
        except (DeploymentError, ValueError, UpdateError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except DockerError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except AstrBotAPIError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "本地服务处理请求失败，请查看启动器终端日志。")

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._validate_host_header():
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        if not self._check_token():
            self._error(HTTPStatus.FORBIDDEN, "本地访问令牌无效或已过期，请回到启动器重新打开本页面。")
            return
        parsed = urlsplit(self.path)
        nested = self._nested_instance_route(parsed.path)
        memory_route = self._memory_admin_route(nested[1]) if nested is not None else None
        if nested is not None and memory_route is None:
            # Never let a nested path fall through to "delete instance".
            self._error(HTTPStatus.NOT_FOUND, "接口不存在。")
            return
        if nested is not None and memory_route is not None:
            instance_id, _ = nested
            payload = self._read_json_body(allow_empty=True)
            if payload is None:
                return
            try:
                self.server.context.studio.get_instance_meta(instance_id)
                manager = self.server.context.studio.get_manager(instance_id)
                self._serve_memory_admin(
                    manager,
                    memory_route,
                    method="DELETE",
                    payload=payload,
                    query=self._query(parsed),
                )
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "删除记忆失败，请查看启动器终端日志。")
            return
        try:
            if parsed.path.startswith("/api/instances/"):
                instance_id = urllib.parse.unquote(parsed.path[len("/api/instances/") :]).strip()
                result = self.server.context.studio.delete_instance(instance_id)
                self._json(HTTPStatus.OK, {"ok": True, "instance": result})
                return
            if parsed.path.startswith("/api/mirrors/"):
                node_id = urllib.parse.unquote(parsed.path[len("/api/mirrors/") :]).strip()
                self.server.context.studio.delete_mirror_node(node_id)
                self._json(HTTPStatus.OK, {"ok": True})
                return
            self._method_not_allowed()
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "删除操作失败，请查看启动器终端日志。")


def create_server(
    studio: StudioManager,
    *,
    static_dir: Path | None = None,
    host: str = DEFAULT_BIND_HOST,
    port: int = 0,
    token: str | None = None,
) -> QQBotHTTPServer:
    static = static_dir or STATIC_DIR
    allow_remote = str(host).strip() not in LOOPBACK_HOSTS
    context = AppContext(
        studio,
        token or secrets.token_urlsafe(32),
        static,
        allow_remote=allow_remote,
    )
    return QQBotHTTPServer((host, int(port)), QQBotRequestHandler, context)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qqbot-studio",
        description="QQ Bot Studio - QQ 机器人部署与多实例管理服务",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("QQBOT_STUDIO_HOST", DEFAULT_BIND_HOST),
        help="管理服务监听地址（默认 0.0.0.0；可设置 QQBOT_STUDIO_HOST）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int("QQBOT_STUDIO_PORT", DEFAULT_BIND_PORT, 0, 65535),
        help="管理服务端口（默认 8765；0=自动选择；可设置 QQBOT_STUDIO_PORT）",
    )
    parser.add_argument("--data-dir", default=None, help="数据目录（默认使用平台用户数据目录）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    host = str(args.host).strip() or DEFAULT_BIND_HOST
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_dir()
    try:
        studio = StudioManager(data_dir)
        pid_path = data_dir / "server.pid"
        pid_path.write_text(str(os.getpid()) + "\n", encoding="utf-8")
        try:
            os.chmod(pid_path, 0o600)
        except OSError:
            pass
        server = create_server(studio, host=host, port=args.port)
    except Exception as exc:  # noqa: BLE001
        print(f"启动 QQ Bot Studio 失败：{exc}", file=sys.stderr)
        return 1

    bound_host, bound_port = server.server_address[:2]
    runtime_path = data_dir / "server-runtime.json"
    try:
        runtime_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "host": host,
                    "port": int(bound_port),
                    "data_dir": str(data_dir),
                },
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        os.chmod(runtime_path, 0o600)
    except OSError:
        pass
    public_base = studio.public_base_url()
    print()
    print("QQ Bot Studio 管理服务已启动")
    print(f"控制台：{public_base}/console")
    print(f"新建部署：{public_base}/wizard")
    print(f"监听：{bound_host}:{bound_port}")
    print(f"数据目录：{data_dir}")
    if host not in LOOPBACK_HOSTS:
        print("警告：管理服务绑定在非回环地址，请使用防火墙/VPN 限制访问。")
    print("按 Ctrl+C 停止管理服务（Docker 容器会继续运行）。")
    print()
    if not args.no_browser:
        def open_browser() -> None:
            time.sleep(0.35)
            try:
                webbrowser.open_new_tab(f"{public_base}/console")
            except Exception:
                pass

        threading.Thread(target=open_browser, name="open-browser", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\n正在关闭管理服务…")
    finally:
        server.server_close()
        try:
            if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass
        try:
            runtime_path.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
