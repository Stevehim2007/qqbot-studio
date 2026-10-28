"""Local-only HTTP server for the QQ Bot Studio browser wizard."""

from __future__ import annotations

import argparse
import hmac
import json
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
from .deployer import DeployManager, DeploymentError
from .dockerctl import DockerError
from .store import Store, default_data_dir


class AppContext:
    def __init__(self, manager: DeployManager, token: str, static_dir: Path) -> None:
        self.manager = manager
        self.token = token
        self.static_dir = static_dir
        self.index_html = (static_dir / "index.html").read_text(encoding="utf-8")
        if '"{{TOKEN}}"' not in self.index_html:
            raise RuntimeError("前端 index.html 缺少 window.QB_TOKEN 占位符")
        self.styles = (static_dir / "styles.css").read_bytes()
        self.script = (static_dir / "app.js").read_bytes()


class QQBotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], context: AppContext) -> None:
        self.context = context
        super().__init__(address, handler)


class QQBotRequestHandler(BaseHTTPRequestHandler):
    server: QQBotHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "QQBotStudio/1.0"
    sys_version = ""

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the terminal quiet enough to be useful; never log request bodies.
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

    def _json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            extra_headers=extra_headers,
        )

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "message": message})

    # ------------------------------------------------------------------
    # Request validation
    # ------------------------------------------------------------------
    def _validate_host_header(self) -> bool:
        """Reduce DNS-rebinding risk for a loopback-only management UI."""

        raw_host = str(self.headers.get("Host", "")).strip().lower()
        if not raw_host:
            return True
        hostname = raw_host
        if hostname.startswith("["):
            hostname = hostname.split("]", 1)[0] + "]"
        else:
            hostname = hostname.rsplit(":", 1)[0] if ":" in hostname else hostname
        allowed = {"127.0.0.1", "localhost"}
        return hostname in allowed

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

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_TRACE(self) -> None:  # noqa: N802
        self._method_not_allowed()

    # ------------------------------------------------------------------
    # GET routes
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._validate_host_header():
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        token = self.server.context.token
        if path.startswith("/api/") and not self._check_token():
            self._error(HTTPStatus.FORBIDDEN, "本地访问令牌无效或已过期，请回到启动器重新打开本页面。")
            return
        try:
            if path in ("/", "/index.html"):
                html = self.server.context.index_html.replace('"{{TOKEN}}"', json.dumps(token))
                self._send_bytes(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/styles.css":
                self._send_bytes(HTTPStatus.OK, self.server.context.styles, "text/css; charset=utf-8", cache=True)
                return
            if path == "/app.js":
                self._send_bytes(
                    HTTPStatus.OK,
                    self.server.context.script,
                    "text/javascript; charset=utf-8",
                    cache=True,
                )
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
            if path == "/api/state":
                self._json(HTTPStatus.OK, self.server.context.manager.get_status())
                return
            if path.startswith("/api/job/"):
                job_id = urllib.parse.unquote(path[len("/api/job/") :]).strip()
                if not job_id or len(job_id) > 120:
                    self._error(HTTPStatus.BAD_REQUEST, "无效的任务 ID")
                    return
                job = self.server.context.manager.get_job(job_id)
                if job is None:
                    self._error(HTTPStatus.NOT_FOUND, "找不到该部署任务。服务重启后旧任务进度不会保留。")
                    return
                self._json(HTTPStatus.OK, job)
                return
            if path == "/api/logs":
                query = urllib.parse.parse_qs(parsed.query)
                service = (query.get("service") or [""])[0]
                lines = self._safe_int((query.get("lines") or ["200"])[0], 200, 1, 1000)
                if service not in {"astrbot", "snowluma"}:
                    self._error(HTTPStatus.BAD_REQUEST, "service 只能是 astrbot 或 snowluma")
                    return
                logs = self.server.context.manager.get_logs(service, lines)
                self._json(HTTPStatus.OK, {"ok": True, "logs": logs})
                return
            self._error(HTTPStatus.NOT_FOUND, "接口不存在")
        except (DeploymentError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except DockerError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except AstrBotAPIError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
        except Exception:  # noqa: BLE001 - local server must stay alive
            traceback.print_exc()
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "本地服务处理请求失败，请查看启动器终端日志。",
            )

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    # ------------------------------------------------------------------
    # POST routes
    # ------------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        if not self._validate_host_header():
            self._error(HTTPStatus.BAD_REQUEST, "Invalid Host header")
            return
        if not self._check_token():
            self._error(HTTPStatus.FORBIDDEN, "本地访问令牌无效或已过期，请回到启动器重新打开本页面。")
            return
        content_type = str(self.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "请求必须使用 application/json。")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 2 * 1024 * 1024:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求体为空或过大。")
            return
        try:
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
        except (UnicodeDecodeError, ValueError):
            self._error(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON object。")
            return

        path = urlsplit(self.path).path
        manager = self.server.context.manager
        try:
            if path == "/api/test-key":
                key = str(payload.get("deepseek_api_key") or "").strip()
                result = manager.test_deepseek_key(key)
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/deploy":
                job_id = manager.start_deploy(
                    qq_account=str(payload.get("qq_account") or ""),
                    deepseek_api_key=str(payload.get("deepseek_api_key") or ""),
                    prompt=str(payload.get("prompt") or ""),
                    use_mirror=bool(payload.get("use_mirror")),
                    accept_risk=bool(payload.get("accept_risk")),
                )
                self._json(HTTPStatus.OK, {"ok": True, "job_id": job_id})
                return
            if path == "/api/action":
                message = manager.container_action(str(payload.get("action") or ""))
                self._json(HTTPStatus.OK, {"ok": True, "message": message})
                return
            if path == "/api/persona":
                message = manager.update_persona(str(payload.get("prompt") or ""))
                self._json(HTTPStatus.OK, {"ok": True, "message": message})
                return
            self._error(HTTPStatus.NOT_FOUND, "接口不存在")
        except (DeploymentError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except DockerError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except AstrBotAPIError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "本地服务处理请求失败，请查看启动器终端日志。",
            )


def create_server(
    manager: DeployManager,
    *,
    static_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
) -> QQBotHTTPServer:
    static = static_dir or (Path(__file__).parent / "static")
    context = AppContext(manager, token or secrets.token_urlsafe(32), static)
    server = QQBotHTTPServer((host, int(port)), QQBotRequestHandler, context)
    return server


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qqbot-studio",
        description="QQ Bot Studio - 本地一键部署 AstrBot + SnowLuma + DeepSeek",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机 127.0.0.1）")
    parser.add_argument("--port", type=int, default=0, help="本地 Web 端口（默认自动选择）")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 ~/.local/share/qqbot-studio）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    host = str(args.host).strip()
    if host not in {"127.0.0.1", "localhost"}:
        print(
            "为安全起见，管理界面只允许监听 127.0.0.1 或 localhost。\n"
            "如需远程访问，请使用 SSH 隧道或带认证的反向代理，不要把管理端口暴露到公网。",
            file=sys.stderr,
        )
        return 2

    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_dir()
    try:
        store = Store(data_dir)
        manager = DeployManager(store=store)
        server = create_server(manager, host=host, port=args.port)
    except Exception as exc:  # noqa: BLE001
        print(f"启动 QQ Bot Studio 失败：{exc}", file=sys.stderr)
        return 1

    bound_host, bound_port = server.server_address[:2]
    browser_host = "127.0.0.1" if bound_host in ("0.0.0.0", "::") else bound_host
    url = f"http://{browser_host}:{bound_port}/"
    print()
    print("QQ Bot Studio 已启动")
    print(f"打开向导：{url}")
    print(f"数据目录：{data_dir}")
    print("按 Ctrl+C 停止本地向导（Docker 容器会继续运行）。")
    print()
    if not args.no_browser:
        def open_browser() -> None:
            time.sleep(0.35)
            try:
                webbrowser.open_new_tab(url)
            except Exception:
                pass

        threading.Thread(target=open_browser, name="open-browser", daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\n正在关闭本地向导…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
