"""Small, testable wrapper around the Docker CLI and Compose plugin."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .store import PROJECT_NAME

CommandRunner = Callable[
    [Sequence[str], Path, int, Mapping[str, str] | None],
    subprocess.CompletedProcess[str],
]


class DockerError(RuntimeError):
    """A Docker command failed or Docker is not usable."""


@dataclass(frozen=True)
class DockerStatus:
    installed: bool
    daemon_running: bool
    compose_installed: bool
    permission_ok: bool
    uses_sudo: bool = False
    version: str = ""
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and self.daemon_running and self.compose_installed and self.permission_ok


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        parts = [self.stdout.strip(), self.stderr.strip()]
        return "\n".join(part for part in parts if part)


def _default_runner(
    argv: Sequence[str],
    cwd: Path,
    timeout: int,
    extra_env: Mapping[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )


class DockerController:
    """Execute Docker commands with optional non-interactive sudo fallback."""

    ASTRBOT_CONTAINER = "qqbot-astrbot"
    SNOWLUMA_CONTAINER = "qqbot-snowluma"

    def __init__(
        self,
        data_dir: Path | str,
        *,
        runner: CommandRunner | None = None,
        compose_file: Path | str | None = None,
        env_file: Path | str | None = None,
        project_name: str = PROJECT_NAME,
        astrbot_container: str = "qqbot-astrbot",
        snowluma_container: str = "qqbot-snowluma",
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        # ``subprocess.run(cwd=...)`` fails with FileNotFoundError if the
        # directory does not exist.  The old launcher used a throw-away
        # ``doctor`` directory that was never created, so Docker was reported
        # missing even when the CLI and daemon were perfectly healthy.
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.project_name = str(project_name or PROJECT_NAME)
        self.ASTRBOT_CONTAINER = str(astrbot_container or "qqbot-astrbot")
        self.SNOWLUMA_CONTAINER = str(snowluma_container or "qqbot-snowluma")
        self.compose_file = Path(compose_file) if compose_file else self.data_dir / "compose.yml"
        self.env_file = Path(env_file) if env_file else self.data_dir / ".env"
        self._runner = runner or _default_runner
        self._prefix: list[str] = []
        self._last_detect_at = 0.0
        self._last_status: DockerStatus | None = None

    # ------------------------------------------------------------------
    # Low-level command execution
    # ------------------------------------------------------------------
    def _exec(
        self,
        argv: Sequence[str],
        *,
        timeout: int = 60,
        extra_env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command = [str(item) for item in argv]
        try:
            proc = self._runner(command, self.data_dir, timeout, extra_env)
        except FileNotFoundError:
            if not self.data_dir.exists():
                return CommandResult(
                    tuple(command),
                    127,
                    "",
                    f"工作目录不存在或不可访问：{self.data_dir}",
                )
            return CommandResult(tuple(command), 127, "", f"找不到命令：{command[0]}")
        except subprocess.TimeoutExpired:
            return CommandResult(tuple(command), 124, "", f"命令执行超时（{timeout}s）：{' '.join(command[:3])}…")
        except OSError as exc:
            return CommandResult(tuple(command), 126, "", str(exc))
        stdout = proc.stdout if isinstance(proc.stdout, str) else (proc.stdout or b"").decode("utf-8", "replace")
        stderr = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode("utf-8", "replace")
        return CommandResult(tuple(command), int(proc.returncode), stdout or "", stderr or "")

    def _docker(self, args: Sequence[str], *, timeout: int = 60, extra_env: Mapping[str, str] | None = None) -> CommandResult:
        return self._exec([*self._prefix, "docker", *args], timeout=timeout, extra_env=extra_env)

    @staticmethod
    def _tail(text: str, limit: int = 1800) -> str:
        text = (text or "").strip()
        return text if len(text) <= limit else "…" + text[-limit:]

    def _raise_for(self, result: CommandResult, what: str) -> None:
        if result.ok:
            return
        detail = self._tail(result.output) or f"退出码 {result.returncode}"
        raise DockerError(f"{what}失败：{detail}")

    # ------------------------------------------------------------------
    # Environment detection
    # ------------------------------------------------------------------
    def detect(self, *, force: bool = False) -> DockerStatus:
        now = time.monotonic()
        if not force and self._last_status is not None and now - self._last_detect_at < 2.5:
            return self._last_status

        version_result = self._exec(["docker", "--version"], timeout=10)
        installed = version_result.ok
        version = version_result.stdout.strip() if version_result.ok else ""
        version_detail = self._tail(version_result.output, 500) if not installed else ""
        compose_installed = False
        compose_detail = ""
        if installed:
            compose_result = self._exec(["docker", "compose", "version"], timeout=10)
            compose_installed = compose_result.ok
            if not compose_installed:
                compose_detail = "未找到 docker compose 插件"

        daemon_running = False
        permission_ok = False
        uses_sudo = False
        detail = compose_detail

        if installed and compose_installed:
            info_direct = self._exec(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
            if info_direct.ok:
                daemon_running = True
                permission_ok = True
                self._prefix = []
            else:
                # The launcher may have cached sudo credentials.  Never prompt
                # from the local web server: only use `sudo -n`.
                info_sudo = self._exec(
                    ["sudo", "-n", "docker", "info", "--format", "{{.ServerVersion}}"],
                    timeout=15,
                )
                if info_sudo.ok:
                    daemon_running = True
                    permission_ok = True
                    uses_sudo = True
                    self._prefix = ["sudo", "-n"]
                else:
                    self._prefix = []
                    direct_error = self._tail(info_direct.output, 500)
                    sudo_error = self._tail(info_sudo.output, 500)
                    if "permission denied" in (direct_error + sudo_error).lower():
                        detail = "当前用户没有 Docker 权限，请重新打开启动器以缓存 sudo 凭据，或注销后重新登录。"
                    elif sudo_error:
                        detail = "Docker 守护进程未运行，或 sudo 凭据已过期。请启动 Docker 后重试。"
                    else:
                        detail = "Docker 守护进程当前不可用。"

        if not installed and version_detail:
            detail = version_detail

        status = DockerStatus(
            installed=installed,
            daemon_running=daemon_running,
            compose_installed=compose_installed,
            permission_ok=permission_ok,
            uses_sudo=uses_sudo,
            version=version,
            detail=detail,
        )
        self._last_status = status
        self._last_detect_at = now
        return status

    def require_ready(self) -> DockerStatus:
        status = self.detect(force=True)
        if not status.installed:
            reason = f"：{status.detail}" if status.detail else ""
            raise DockerError(
                "未检测到可用的 Docker CLI"
                f"{reason}。请先运行 ./install.sh 安装 Docker，或手动安装后重试。"
            )
        if not status.compose_installed:
            raise DockerError("Docker Compose 插件不可用。请安装 docker-compose-plugin / docker-compose-v2 后重试。")
        if not status.daemon_running:
            raise DockerError(status.detail or "Docker 守护进程未运行。请启动 Docker 后重试。")
        if not status.permission_ok:
            raise DockerError(
                status.detail
                or "当前用户没有 Docker 权限。请在终端运行 sudo -v 后重新打开本程序，或注销后重新登录。"
            )
        return status

    # ------------------------------------------------------------------
    # Compose operations
    # ------------------------------------------------------------------
    def compose_argv(self, args: Sequence[str]) -> list[str]:
        if not self.compose_file.exists():
            raise DockerError(f"找不到 Compose 文件：{self.compose_file}")
        if not self.env_file.exists():
            raise DockerError(f"找不到环境变量文件：{self.env_file}")
        return [
            *self._prefix,
            "docker",
            "compose",
            "--project-name",
            self.project_name,
            "--env-file",
            str(self.env_file),
            "-f",
            str(self.compose_file),
            *[str(arg) for arg in args],
        ]

    def compose(
        self,
        args: Sequence[str],
        *,
        timeout: int = 900,
        check: bool = True,
        what: str = "Docker Compose 操作",
    ) -> CommandResult:
        self.require_ready()
        result = self._exec(self.compose_argv(args), timeout=timeout)
        if check:
            self._raise_for(result, what)
        return result

    def compose_stream(
        self,
        args: Sequence[str],
        *,
        on_line: Callable[[str], None],
        timeout: int = 1800,
        what: str = "Docker Compose 操作",
    ) -> CommandResult:
        """Run Compose while forwarding each output line to a callback."""

        self.require_ready()
        argv = self.compose_argv(args)
        # In tests or unusual environments a custom runner may be injected;
        # preserve compatibility by running it and replaying captured output.
        if self._runner is not _default_runner:
            result = self._exec(argv, timeout=timeout)
            for line in result.output.splitlines():
                on_line(line)
            self._raise_for(result, what)
            return result

        env = os.environ.copy()
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(self.data_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise DockerError(f"{what}启动失败：{exc}") from exc

        lines: list[str] = []
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\r\n")
                    lines.append(line)
                    if line.strip():
                        on_line(line)
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise DockerError(f"{what}超时（{timeout}s）") from exc
        output = "\n".join(lines)
        result = CommandResult(tuple(argv), int(returncode), output, "")
        self._raise_for(result, what)
        return result

    def _stream_command(
        self,
        argv: Sequence[str],
        *,
        on_line: Callable[[str], None],
        timeout: int,
        what: str,
    ) -> CommandResult:
        if self._runner is not _default_runner:
            result = self._exec(argv, timeout=timeout)
            for line in result.output.splitlines():
                on_line(line)
            self._raise_for(result, what)
            return result
        env = os.environ.copy()
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=str(self.data_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise DockerError(f"{what}启动失败：{exc}") from exc
        lines: list[str] = []
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\r\n")
                    lines.append(line)
                    if line.strip():
                        on_line(line)
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise DockerError(f"{what}超时（{timeout}s）") from exc
        result = CommandResult(tuple(argv), int(returncode), "\n".join(lines), "")
        self._raise_for(result, what)
        return result

    def image_exists(self, image: str) -> bool:
        self.require_ready()
        return self._docker(["image", "inspect", image], timeout=20).ok

    def pull_image(self, image: str, on_line: Callable[[str], None] | None = None) -> CommandResult:
        self.require_ready()
        if self.image_exists(image):
            if on_line:
                on_line(f"镜像已存在，跳过拉取：{image}")
            return CommandResult(tuple([*self._prefix, "docker", "image", "inspect", image]), 0, "exists", "")
        if on_line:
            on_line(f"开始拉取镜像：{image}")
        argv = [*self._prefix, "docker", "pull", image]
        if on_line is not None:
            return self._stream_command(argv, on_line=on_line, timeout=1800, what=f"拉取镜像 {image}")
        return self._exec(argv, timeout=1800)

    def pull(
        self,
        on_line: Callable[[str], None] | None = None,
        *,
        images: Sequence[str] | None = None,
    ) -> CommandResult:
        if images:
            last: CommandResult | None = None
            for image in images:
                last = self.pull_image(str(image), on_line=on_line)
            if last is None:
                raise DockerError("没有需要拉取的镜像")
            return last
        if on_line is not None:
            return self.compose_stream(["pull"], on_line=on_line, timeout=1800, what="拉取 Docker 镜像")
        return self.compose(["pull"], timeout=1800, what="拉取 Docker 镜像")

    def up(self, *, force_recreate: bool = False) -> CommandResult:
        args = ["up", "-d", "--remove-orphans"]
        if force_recreate:
            args.append("--force-recreate")
        return self.compose(args, timeout=900, what="启动容器")

    def stop(self) -> CommandResult:
        return self.compose(["stop"], timeout=180, what="停止容器")

    def start(self) -> CommandResult:
        return self.compose(["start"], timeout=180, what="启动容器")

    def restart(self) -> CommandResult:
        return self.compose(["restart"], timeout=240, what="重启容器")

    def down(self) -> CommandResult:
        return self.compose(["down", "--remove-orphans"], timeout=240, what="停止并移除容器")

    def container_health(self, container_name: str) -> str:
        """Return `healthy`, `unhealthy`, `starting` or `none`."""

        self.require_ready()
        result = self._docker(
            [
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                container_name,
            ],
            timeout=20,
        )
        return result.stdout.strip() if result.ok else "unknown"

    def container_status(self, container_name: str) -> str | None:
        self.require_ready()
        result = self._docker(
            ["inspect", "--format", "{{.State.Status}}", container_name],
            timeout=20,
        )
        if result.ok:
            return result.stdout.strip() or None
        return None

    def is_running(self) -> bool:
        return self.container_status(self.ASTRBOT_CONTAINER) == "running" and (
            self.container_status(self.SNOWLUMA_CONTAINER) == "running"
        )

    def logs(self, service: str, lines: int = 200) -> str:
        if service not in {"astrbot", "snowluma"}:
            raise ValueError("service 只能是 astrbot 或 snowluma")
        lines = max(1, min(int(lines), 1000))
        result = self.compose(
            ["logs", "--no-color", "--tail", str(lines), service],
            timeout=45,
            check=False,
            what="读取容器日志",
        )
        if not result.ok:
            raise DockerError(self._tail(result.output) or "读取容器日志失败")
        return result.output
