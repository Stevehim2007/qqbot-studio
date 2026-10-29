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
from .astrbot_api import AstrBotAPIError
from .deployer import DeploymentError
from .dockerctl import DockerError
from .store import default_data_dir
from .studio import DEFAULT_BIND_HOST, DEFAULT_BIND_PORT, StudioManager
from .updater import UpdateError, check_update, start_update

STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


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
        self._method_not_allowed()

    def do_TRACE(self) -> None:  # noqa: N802
        self._method_not_allowed()

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
            if path == "/api/instances":
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "instances": ctx.studio.list_instance_summaries()},
                )
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
    def _read_json_body(self) -> dict[str, Any] | None:
        content_type = str(self.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "请求必须使用 application/json。")
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 2 * 1024 * 1024:
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
        payload = self._read_json_body()
        if payload is None:
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        query = self._query(parsed)
        ctx = self.server.context
        try:
            if path == "/api/update/apply":
                if ctx.studio.has_active_deployments():
                    raise ValueError("有部署任务正在运行，不能自动更新。请在部署完成后再试。")
                result = start_update(data_dir=ctx.studio.root)
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

            instance_id = ctx.instance_id(query, payload)
            manager = ctx.studio.get_manager(instance_id)
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
