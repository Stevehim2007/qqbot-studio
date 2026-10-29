"""Host-side launcher for the background Studio server and new bot instances."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Iterable

from .dockerctl import DockerController, DockerError
from .store import default_data_dir
from .studio import DEFAULT_BIND_HOST, DEFAULT_BIND_PORT, StudioManager, detect_public_host

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREEN_NAME = "qqbot-studio-server"


def _prompt(prompt: str, default: str) -> str:
    if not sys.stdin.isatty():
        return default
    try:
        value = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        return default
    return value or default


def _prompt_port(default: int) -> int:
    while True:
        raw = _prompt("管理控制台端口", str(default))
        try:
            port = int(raw)
        except ValueError:
            print("端口必须是数字。")
            continue
        if 1 <= port <= 65535:
            return port
        print("端口必须在 1–65535 之间。")


def _configure(studio: StudioManager, args: argparse.Namespace) -> dict:
    existing = studio.load_settings()
    first_run = not bool(existing.get("first_run_complete")) or not studio.settings_path.exists()
    bind_host = args.ip or str(existing.get("bind_host") or DEFAULT_BIND_HOST)
    bind_port = int(args.port or existing.get("bind_port") or DEFAULT_BIND_PORT)
    public_host = args.public_host or str(existing.get("public_host") or "")

    if first_run:
        print("\n首次启动需要确认管理服务监听参数。")
        bind_host = _prompt("管理服务监听 IP（0.0.0.0 表示所有网卡）", bind_host)
        bind_port = _prompt_port(bind_port)
        default_public = detect_public_host()
        public_host = _prompt("对外访问 IP/域名（用于生成浏览器链接）", public_host or default_public)
    else:
        if args.ip:
            bind_host = args.ip
        if args.port:
            bind_port = int(args.port)
        if args.public_host:
            public_host = args.public_host

    if not public_host:
        public_host = detect_public_host()
    return studio.update_network(
        bind_host=bind_host,
        bind_port=bind_port,
        public_host=public_host,
    )


def _read_runtime(studio: StudioManager) -> dict:
    try:
        data = json.loads((studio.root / "server-runtime.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _health_ok(port: int, timeout: float = 1.0) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{int(port)}/healthz", timeout=timeout) as response:
            return int(getattr(response, "status", 200)) == 200
    except Exception:
        return False


def _read_pid(studio: StudioManager) -> int | None:
    try:
        return int(studio.server_pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop_server(studio: StudioManager) -> None:
    # Quit the named screen session first; then fall back to the pid file for
    # nohup/legacy launches.
    screen = shutil.which("screen")
    if screen:
        subprocess.run([screen, "-S", SCREEN_NAME, "-X", "quit"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    pid = _read_pid(studio)
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(30):
            if not _pid_alive(pid):
                break
            time.sleep(0.2)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        studio.server_pid_path.unlink()
    except OSError:
        pass


def _screen_available() -> bool:
    return bool(shutil.which("screen"))


def _server_command(studio: StudioManager, host: str, port: int) -> str:
    log_path = studio.root / "studio-server.log"
    root = shlex.quote(str(PROJECT_ROOT))
    data = shlex.quote(str(studio.root))
    host_q = shlex.quote(str(host))
    command = (
        f"cd {root} && exec python3 -m qqbot_studio "
        f"--host {host_q} --port {int(port)} --data-dir {data} --no-browser "
        f">>{shlex.quote(str(log_path))} 2>&1"
    )
    return command


def _start_server(studio: StudioManager, settings: dict) -> None:
    host = str(settings.get("bind_host") or DEFAULT_BIND_HOST)
    port = int(settings.get("bind_port") or DEFAULT_BIND_PORT)
    command = _server_command(studio, host, port)
    screen = shutil.which("screen")
    screen_error = ""
    if screen:
        result = subprocess.run(
            [screen, "-dmS", SCREEN_NAME, "bash", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            screen_error = (result.stderr or result.stdout or "screen 启动失败").strip()
    if not screen or screen_error:
        # Screen is installed by install.sh, but container/restricted systems
        # may make /run/screen read-only. Fall back to nohup so the manager
        # still starts; deployment/job persistence remains available.
        if screen_error:
            print(f"警告：screen 启动失败（{screen_error}），改用 nohup 后台模式。")
        else:
            print("警告：未安装 screen，已使用 nohup 后台模式。建议安装 screen 以便管理。")
        subprocess.Popen(
            ["bash", "-c", command],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    for _ in range(80):
        if _health_ok(port, timeout=0.8):
            return
        time.sleep(0.25)
    log_path = studio.root / "studio-server.log"
    tail = ""
    try:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
    except OSError:
        pass
    raise RuntimeError(f"管理服务启动超时。日志：{log_path}\n{tail}")


def _ensure_server(studio: StudioManager, settings: dict, *, force_restart: bool = False) -> None:
    port = int(settings.get("bind_port") or DEFAULT_BIND_PORT)
    host = str(settings.get("bind_host") or DEFAULT_BIND_HOST)
    if not force_restart and _health_ok(port):
        runtime = _read_runtime(studio)
        if (
            int(runtime.get("port") or 0) == port
            and str(runtime.get("host") or "") == host
        ):
            return
        # An older/other process may be listening on the same port. Restart
        # only when the persisted runtime parameters do not match.
    _stop_server(studio)
    _start_server(studio, settings)


def _docker_check(studio: StudioManager) -> None:
    # A throw-away controller is enough: detect() only checks Docker/compose.
    controller = DockerController(studio.root / "doctor")
    try:
        controller.require_ready()
    except DockerError as exc:
        raise RuntimeError(str(exc)) from exc


def _print_repair_results(results: Iterable[dict]) -> None:
    for item in results:
        name = item.get("name") or item.get("instance_id") or "实例"
        action = item.get("action") or "unknown"
        ok = "OK" if item.get("ok") else "FAIL"
        print(f"[{ok}] {name}: {action} {item.get('message') or ''}".rstrip())


def _open_if_possible(url: str, no_open: bool) -> None:
    if no_open:
        return
    if os.environ.get("DISPLAY") or sys.platform in {"darwin", "win32"}:
        try:
            import webbrowser

            webbrowser.open_new_tab(url)
        except Exception:
            pass


def _base_url(studio: StudioManager) -> str:
    settings = studio.load_settings()
    host = studio.public_host()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{int(settings['bind_port'])}"


def _run_deploy(studio: StudioManager, args: argparse.Namespace, settings: dict) -> int:
    _docker_check(studio)
    print("正在检查已有容器状态…")
    repairs = studio.repair_instances()
    _print_repair_results(repairs)

    meta = studio.create_instance(args.name or None)
    base = _base_url(studio)
    url = f"{base}/wizard?instance={meta['id']}"
    print()
    print(f"已创建机器人实例：{meta['name']} ({meta['id']})")
    print(f"请用电脑/手机浏览器打开部署向导：{url}")
    print(f"管理控制台：{base}/console")
    _open_if_possible(url, args.no_open)
    return 0


def _run_console(studio: StudioManager, args: argparse.Namespace, settings: dict) -> int:
    _docker_check(studio)
    print("正在检查已有容器状态…")
    repairs = studio.repair_instances()
    _print_repair_results(repairs)
    base = _base_url(studio)
    url = f"{base}/console"
    print()
    print(f"管理控制台：{url}")
    _open_if_possible(url, args.no_open)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qqbot-studio-launcher",
        description="QQ Bot Studio 管理服务 / 多机器人实例启动器",
    )
    parser.add_argument("mode", choices=("deploy", "console"), help="deploy=新建机器人部署，console=打开控制台")
    parser.add_argument("--ip", default=None, help="管理服务监听 IP（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=None, help="管理服务端口（首次运行会询问并保存）")
    parser.add_argument("--public-host", default=None, help="浏览器访问使用的外部 IP/域名")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 ~/.local/share/qqbot-studio）")
    parser.add_argument("--name", default=None, help="新建机器人实例的显示名称")
    parser.add_argument("--no-open", action="store_true", help="不尝试自动打开浏览器")
    parser.add_argument("--restart-service", action="store_true", help="强制重启后台管理服务")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_dir()
    try:
        studio = StudioManager(root)
        settings = _configure(studio, args)
        # If the owner changed parameters, the old listener may be on a
        # different port. Restart it when explicitly requested or when health
        # on the configured port is absent.
        if not _screen_available():
            print("提示：未检测到 screen。install.sh 会自动安装 screen；当前将使用 nohup 后台模式。")
        # Fail before launching a background management server if Docker is not
        # usable.  This prevents the confusing "UI is up, deployment is dead"
        # state reported by users after cancelling Docker installation.
        _docker_check(studio)
        _ensure_server(studio, settings, force_restart=args.restart_service)
        if args.mode == "deploy":
            return _run_deploy(studio, args, settings)
        return _run_console(studio, args, settings)
    except (RuntimeError, ValueError, DockerError) as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
