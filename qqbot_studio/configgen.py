"""Generate Docker Compose and application configuration files."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import tempfile
from pathlib import Path
from typing import Any

from .store import MODEL_NAME, fchmod_best_effort

ASTRBOT_IMAGE = "soulter/astrbot:latest"
SNOWLUMA_IMAGE = "motricseven7/snowluma:latest"
# Ordered fallbacks for users in mainland China.  Prefixes are intentionally
# expressed as image-repository prefixes, not Docker daemon registry mirrors.
DEFAULT_MIRROR_PREFIXES = (
    "m.daocloud.io/docker.io/",
    "docker.1ms.run/",
    "docker.xuanyuan.me/",
    "hub.rat.dev/",
)
MIRROR_ENV_VAR = "QQBOT_IMAGE_MIRROR_PREFIXES"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
PORT_RANGE_START = 5000
PORT_RANGE_END = 10000
PORT_RANGE_SIZE = 3
DEFAULT_PORTS = {
    "astrbot_webui": 6185,
    "snowluma_webui": 5099,
    "novnc": 6081,
}
INTERNAL_ASTRBOT_PORT = 6185
INTERNAL_ASTRBOT_ONEBOT_PORT = 6199
INTERNAL_SNOWLUMA_WEBUI_PORT = 5099
INTERNAL_SNOWLUMA_NOVNC_PORT = 6081


def md5_password(password: str) -> str:
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def pbkdf2_password(password: str, *, iterations: int = 600_000) -> str:
    """Return an AstrBot-compatible PBKDF2-SHA256 password string."""

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"




def _atomic_write_text(path: Path, payload: str, *, mode: int) -> None:
    """Atomically replace a generated file.

    AstrBot runs as root inside its container and can rewrite files on a bind
    mount with root ownership.  Atomic replace only requires write permission
    on the parent directory, so retries/re-deploys keep working for the desktop
    user in that situation.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        fchmod_best_effort(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    if not (1 <= int(port) <= 65535):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Windows SO_REUSEADDR can allow a second bind to appear successful
        # even when another process owns the port.  Probe exclusively there so
        # Compose port selection fails early instead of during `docker up`.
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if os.name == "nt" and exclusive is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def choose_ports(
    preferred: dict[str, int] | None = None,
    *,
    host: str = "127.0.0.1",
    max_attempts: int = 200,
    probe: bool = True,
    reserved: set[int] | None = None,
) -> dict[str, int]:
    """Choose unique, available host ports.

    ``reserved`` lets the multi-instance manager avoid ports already allocated
    to another instance even when that instance's containers are stopped.
    """

    preferred = {**DEFAULT_PORTS, **(preferred or {})}
    reserved_ports = {int(item) for item in (reserved or set())}
    chosen: dict[str, int] = {}
    used: set[int] = set()
    for name, start in preferred.items():
        start = int(start)
        candidate = start
        found = False
        for _ in range(max_attempts):
            if candidate > 65535:
                break
            if (
                candidate not in used
                and candidate not in reserved_ports
                and (not probe or is_port_free(candidate, host))
            ):
                chosen[name] = candidate
                used.add(candidate)
                found = True
                break
            candidate += 1
        if not found:
            raise RuntimeError(f"无法为 {name} 找到空闲端口（从 {start} 开始）")
    return chosen


def allocate_instance_port_block(
    used: set[int] | None = None,
    *,
    host: str = "127.0.0.1",
    start: int = PORT_RANGE_START,
    end: int = PORT_RANGE_END,
    probe: bool = True,
) -> dict[str, int]:
    """Allocate three adjacent host ports for one instance.

    Each instance owns a distinct block of exactly three consecutive ports:
    AstrBot WebUI, SnowLuma WebUI and noVNC. The whole project is constrained
    to the inclusive 5000-10000 range.
    """

    start = max(int(PORT_RANGE_START), int(start))
    end = min(int(PORT_RANGE_END), int(end))
    reserved = {int(item) for item in (used or set())}
    if end - start + 1 < PORT_RANGE_SIZE:
        raise RuntimeError("端口范围不足以容纳一个实例的三个服务端口")
    for base in range(start, end - PORT_RANGE_SIZE + 2):
        block = {base, base + 1, base + 2}
        if block & reserved:
            continue
        if probe and not all(is_port_free(port, host) for port in block):
            continue
        return {
            "astrbot_webui": base,
            "snowluma_webui": base + 1,
            "novnc": base + 2,
        }
    raise RuntimeError(
        f"无法在 {start}-{end} 范围内为实例分配三个相邻的宿主机端口"
    )


def _normalize_mirror_prefix(prefix: str) -> str:
    value = str(prefix or "").strip()
    if not value:
        return ""
    if not value.endswith("/"):
        value += "/"
    return value


def mirror_prefixes() -> list[str]:
    """Return mirror prefixes, with optional user-defined sources first.

    Set ``QQBOT_IMAGE_MIRROR_PREFIXES`` to a comma-separated list to override
    the default order, for example::

        QQBOT_IMAGE_MIRROR_PREFIXES="docker.1ms.run/,m.daocloud.io/docker.io/"
    """

    custom = [
        _normalize_mirror_prefix(item)
        for item in os.environ.get(MIRROR_ENV_VAR, "").split(",")
        if item.strip()
    ]
    ordered: list[str] = []
    for prefix in [*custom, *DEFAULT_MIRROR_PREFIXES]:
        prefix = _normalize_mirror_prefix(prefix)
        if prefix and prefix not in ordered:
            ordered.append(prefix)
    return ordered


def image_names_for_prefix(prefix: str) -> tuple[str, str]:
    """Return the two image names to use with a repository prefix."""

    normalized = _normalize_mirror_prefix(prefix)
    if not normalized:
        return ASTRBOT_IMAGE, SNOWLUMA_IMAGE
    return f"{normalized}{ASTRBOT_IMAGE}", f"{normalized}{SNOWLUMA_IMAGE}"


def set_image_names(env_path: Path | str, astrbot_image: str, snowluma_image: str) -> None:
    """Update only image entries in the generated .env file."""

    path = Path(env_path)
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到环境变量文件：{path}") from exc
    replacements = {
        "ASTRBOT_IMAGE": str(astrbot_image),
        "SNOWLUMA_IMAGE": str(snowluma_image),
    }
    found: set[str] = set()
    lines: list[str] = []
    for line in original.splitlines():
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in replacements:
            lines.append(f"{key}={replacements[key]}")
            found.add(key)
        else:
            lines.append(line)
    for key, value in replacements.items():
        if key not in found:
            lines.append(f"{key}={value}")
    _atomic_write_text(path, "\n".join(lines) + "\n", mode=0o600)


def _compose_yaml(
    *,
    project_name: str = "qqbot-studio",
    astrbot_container: str = "qqbot-astrbot",
    snowluma_container: str = "qqbot-snowluma",
    network_name: str = "qqbot-studio-net",
    include_localtime: bool | None = None,
) -> str:
    # Keep the project directory as the Compose working directory so all
    # relative bind mounts are deterministic.  Instance-specific names keep
    # multiple bots on one host completely isolated.
    #
    # Docker Desktop on Windows runs Linux containers in its own VM.  Its
    # /etc/localtime is not the Windows host file, so bind-mounting it is
    # either misleading or rejected on some Desktop versions.  TZ is already
    # passed explicitly through the environment; omit the Linux-only mount.
    if include_localtime is None:
        include_localtime = os.name != "nt"
    localtime_volume = (
        "      - /etc/localtime:/etc/localtime:ro\n" if include_localtime else ""
    )
    return f"""# Generated by QQ Bot Studio. Project: {project_name}
services:
  astrbot:
    image: ${{ASTRBOT_IMAGE}}
    container_name: {astrbot_container}
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    ports:
      - "${{BIND_ADDRESS}}:${{ASTRBOT_WEBUI_PORT}}:{INTERNAL_ASTRBOT_PORT}"
    environment:
      TZ: Asia/Shanghai
    volumes:
      - ./data/astrbot:/AstrBot/data
{localtime_volume}    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:{INTERNAL_ASTRBOT_PORT}/ >/dev/null || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 12
      start_period: 30s
    networks:
      - {network_name}

  snowluma:
    image: ${{SNOWLUMA_IMAGE}}
    container_name: {snowluma_container}
    restart: unless-stopped
    shm_size: 1gb
    ulimits:
      nofile:
        soft: 65536
        hard: 1048576
    cap_add:
      - SYS_PTRACE
    security_opt:
      - seccomp=unconfined
    environment:
      TZ: Asia/Shanghai
      VNC_PASSWD: ${{VNC_PASSWD}}
      SNOWLUMA_UID: ${{HOST_UID}}
      SNOWLUMA_GID: ${{HOST_GID}}
      SNOWLUMA_WEBUI_HOST: 0.0.0.0
      SNOWLUMA_WEBUI_PORT: "{INTERNAL_SNOWLUMA_WEBUI_PORT}"
      SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD: ${{SNOWLUMA_WEBUI_PASSWORD}}
      SNOWLUMA_HOOK_AUTOLOAD: "1"
      SNOWLUMA_LOG_LEVEL: info
      SNOWLUMA_QQ_FLAGS: "--disable-gpu --disable-software-rasterizer --disable-gpu-compositing"
    ports:
      - "${{BIND_ADDRESS}}:${{NOVNC_PORT}}:{INTERNAL_SNOWLUMA_NOVNC_PORT}"
      - "${{BIND_ADDRESS}}:${{SNOWLUMA_WEBUI_PORT_HOST}}:{INTERNAL_SNOWLUMA_WEBUI_PORT}"
    volumes:
      - ./data/snowluma:/app/data
      - ./data/snowluma-client-config:/app/.config
      - ./data/snowluma-client-data:/app/.local/share
    networks:
      - {network_name}

networks:
  {network_name}:
    name: {network_name}
    driver: bridge
"""


def _env_file(
    *,
    ports: dict[str, int],
    host_uid: int,
    host_gid: int,
    vnc_password: str,
    snowluma_webui_password: str = "",
    mirror_prefix: str = "",
    bind_address: str = "127.0.0.1",
) -> str:
    astrbot_image, snowluma_image = image_names_for_prefix(mirror_prefix)
    values = [
        ("SNOWLUMA_WEBUI_PASSWORD", str(snowluma_webui_password)),
        ("ASTRBOT_IMAGE", astrbot_image),
        ("SNOWLUMA_IMAGE", snowluma_image),
        ("BIND_ADDRESS", str(bind_address or "127.0.0.1")),
        ("ASTRBOT_WEBUI_PORT", str(ports["astrbot_webui"])),
        ("SNOWLUMA_WEBUI_PORT_HOST", str(ports["snowluma_webui"])),
        ("NOVNC_PORT", str(ports["novnc"])),
        ("HOST_UID", str(host_uid)),
        ("HOST_GID", str(host_gid)),
        ("VNC_PASSWD", vnc_password),
    ]
    lines = ["# Generated by QQ Bot Studio. Contains secrets; keep mode 600."]
    lines.extend(f"{key}={value}" for key, value in values)
    return "\n".join(lines) + "\n"


def _astrbot_config(
    *,
    api_key: str,
    onebot_token: str,
    dashboard_username: str,
    dashboard_password: str,
    jwt_secret: str,
) -> dict[str, Any]:
    persona_id = "qqbot-assistant"
    return {
        "config_version": 5,
        "timezone": "Asia/Shanghai",
        "wake_prefix": ["/"],
        "dashboard": {
            "enable": True,
            "username": dashboard_username,
            "password": md5_password(dashboard_password),
            "pbkdf2_password": pbkdf2_password(dashboard_password),
            "password_storage_upgraded": True,
            "password_change_required": False,
            "jwt_secret": jwt_secret,
            "host": "0.0.0.0",
            "port": INTERNAL_ASTRBOT_PORT,
        },
        "provider_sources": [],
        "provider": [
            {
                "id": "deepseek-flash",
                "provider": "deepseek",
                "type": "openai_chat_completion",
                "provider_type": "chat_completion",
                "enable": True,
                "key": [api_key],
                "api_base": DEEPSEEK_API_BASE,
                "model": MODEL_NAME,
                "timeout": 120,
                "proxy": "",
                "custom_headers": {},
                "max_context_tokens": 0,
            }
        ],
        "provider_settings": {
            "enable": True,
            "provider_pool": ["*"],
            "wake_prefix": "",
            "persona_pool": ["*"],
            "datetime_system_prompt": True,
            "prompt_prefix": "{{prompt}}",
        },
        "agent_runner": {
            "runner_type": "local",
            "config": {
                "model": {
                    "provider_id": "deepseek-flash",
                    "fallback_provider_ids": [],
                    "request_max_retries": 5,
                },
                "persona": {
                    "persona_id": persona_id,
                    "safety_mode": True,
                    "safety_mode_strategy": "system_prompt",
                },
                "compression": {
                    "max_turns": -1,
                    "trim_turns": 1,
                    "overflow_strategy": "llm_compress",
                    "instruction": "",
                    "keep_recent_ratio": 0.15,
                    "provider_id": "",
                    "fallback_max_tokens": 128000,
                },
                "misc": {
                    "max_steps": 128,
                    "tool_schema_mode": "full",
                    "tool_call_timeout": 120,
                    "sanitize_context_by_modalities": False,
                },
            },
        },
        "platform": [
            {
                "id": "qqbot-onebot",
                "type": "aiocqhttp",
                "enable": True,
                "ws_reverse_host": "0.0.0.0",
                "ws_reverse_port": INTERNAL_ASTRBOT_ONEBOT_PORT,
                "ws_reverse_token": onebot_token,
            }
        ],
        "platform_settings": {
            "enable_id_white_list": False,
            "id_whitelist": [],
            "empty_mention_waiting": True,
        },
        "plugin_set": ["*"],
    }


def _snowluma_config(
    *,
    onebot_token: str,
    http_token: str,
    ws_token: str,
) -> dict[str, Any]:
    common = {
        "messageFormat": "array",
        "reportSelfMessage": False,
    }
    return {
        "networks": {
            "httpServers": [
                {
                    "name": "http-default",
                    "enabled": False,
                    "host": "127.0.0.1",
                    "port": 3000,
                    "path": "/",
                    "accessToken": http_token,
                    **common,
                }
            ],
            "httpClients": [],
            "wsServers": [
                {
                    "name": "ws-default",
                    "enabled": False,
                    "host": "127.0.0.1",
                    "port": 3001,
                    "path": "/",
                    "role": "Universal",
                    "accessToken": ws_token,
                    **common,
                }
            ],
            "wsClients": [
                {
                    "name": "astrbot",
                    "enabled": True,
                    # SnowLuma runs in the same Compose network, so the AstrBot
                    # service name is used instead of 127.0.0.1.
                    "url": f"ws://astrbot:{INTERNAL_ASTRBOT_ONEBOT_PORT}/ws",
                    "role": "Universal",
                    "accessToken": onebot_token,
                    "reconnectIntervalMs": 5000,
                    **common,
                }
            ],
        },
        # Disabled by default because "#sl" collides with ordinary chat
        # commands.  The object remains for old SnowLuma schemas and manual
        # opt-in, but it is not registered unless a user explicitly enables it.
        "statusCommand": {
            "enabled": False,
            "swallow": False,
            "cooldownSeconds": 5,
            "trigger": "#sl",
        },
        "historySync": {"enabled": False},
        "notifications": {"channelIds": []},
    }


def generate_artifacts(
    data_dir: Path | str,
    *,
    api_key: str,
    prompt: str,
    qq_account: str,
    use_mirror: bool = False,
    secrets_data: dict[str, Any] | None = None,
    ports: dict[str, int] | None = None,
    host_uid: int | None = None,
    host_gid: int | None = None,
    mirror_prefix: str = "",
    bind_address: str = "127.0.0.1",
    project_name: str = "qqbot-studio",
    astrbot_container: str = "qqbot-astrbot",
    snowluma_container: str = "qqbot-snowluma",
    network_name: str = "qqbot-studio-net",
) -> dict[str, Any]:
    """Write all files required to run the deployment.

    The returned mapping contains paths only; it never includes the DeepSeek
    API key.  The caller is responsible for keeping ``api_key`` in memory only.
    """

    if secrets_data is None:
        raise ValueError("generate_artifacts() 需要 secrets_data")
    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    data_sub = root / "data"
    astrbot_dir = data_sub / "astrbot"
    snowluma_dir = data_sub / "snowluma"
    snowluma_config_dir = snowluma_dir / "config"
    snowluma_client_config = data_sub / "snowluma-client-config"
    snowluma_client_data = data_sub / "snowluma-client-data"
    for directory in (
        data_sub,
        astrbot_dir,
        snowluma_dir,
        snowluma_config_dir,
        snowluma_client_config,
        snowluma_client_data,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass

    probe_host = "0.0.0.0" if str(bind_address) in {"0.0.0.0", "::", ""} else str(bind_address)
    selected_ports = choose_ports(ports or secrets_data.get("ports") or None, host=probe_host)
    # os.getuid/os.getgid do not exist on Windows.  Docker Desktop bind mounts
    # do not preserve Windows NTFS owners as Linux uid/gid values anyway, so
    # the conventional first non-root container user is a safe fallback.
    default_uid = int(getattr(os, "getuid", lambda: 1000)())
    default_gid = int(getattr(os, "getgid", lambda: 1000)())
    uid = default_uid if host_uid is None else int(host_uid)
    gid = default_gid if host_gid is None else int(host_gid)
    if not mirror_prefix and use_mirror:
        mirror_prefix = mirror_prefixes()[0]
    webui_password = str(secrets_data.get("snowluma_password") or "").strip()
    if not webui_password:
        # SnowLuma supports a stable bootstrap password on a fresh data dir.
        # Generate it before writing compose/.env so the UI never has to show
        # a stale one-time password after a restart.
        webui_password = secrets.token_hex(8)
        secrets_data["snowluma_password"] = webui_password

    compose_path = root / "compose.yml"
    env_path = root / ".env"
    astrbot_config_path = astrbot_dir / "cmd_config.json"
    snowluma_config_path = snowluma_config_dir / "onebot.json"

    _atomic_write_text(
        compose_path,
        _compose_yaml(
            project_name=project_name,
            astrbot_container=astrbot_container,
            snowluma_container=snowluma_container,
            network_name=network_name,
        ),
        mode=0o644,
    )

    _atomic_write_text(
        env_path,
        _env_file(
            ports=selected_ports,
            host_uid=uid,
            host_gid=gid,
            vnc_password=str(secrets_data["vnc_password"]),
            snowluma_webui_password=webui_password,
            mirror_prefix=mirror_prefix,
            bind_address=bind_address,
        ),
        mode=0o600,
    )

    astrbot_config = _astrbot_config(
        api_key=api_key,
        onebot_token=str(secrets_data["onebot_token"]),
        dashboard_username=str(secrets_data["astrbot_username"]),
        dashboard_password=str(secrets_data["astrbot_password"]),
        jwt_secret=str(secrets_data["astrbot_jwt_secret"]),
    )
    _atomic_write_text(
        astrbot_config_path,
        json.dumps(astrbot_config, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )

    snowluma_config = _snowluma_config(
        onebot_token=str(secrets_data["onebot_token"]),
        http_token=str(secrets_data["snowluma_http_token"]),
        ws_token=str(secrets_data["snowluma_ws_token"]),
    )
    _atomic_write_text(
        snowluma_config_path,
        json.dumps(snowluma_config, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )

    result: dict[str, Any] = {
        "root": root,
        "compose": compose_path,
        "env": env_path,
        "astrbot_config": astrbot_config_path,
        "snowluma_config": snowluma_config_path,
        "data": data_sub,
        "astrbot_data": astrbot_dir,
        "snowluma_data": snowluma_dir,
        "ports": selected_ports,
        "mirror_prefix": mirror_prefix,
        "project_name": project_name,
        "bind_address": bind_address,
    }
    return result
