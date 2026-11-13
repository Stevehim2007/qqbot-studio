"""Generate Docker Compose and official QQ Bot configuration files."""

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
NAPCAT_IMAGE = "docker.m.daocloud.io/mlikiowa/napcat-docker:latest"
NAPCAT_IMAGE_PATH = "mlikiowa/napcat-docker:latest"
INTERNAL_NAPCAT_WEBUI_PORT = 6099
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
PORT_RANGE_SIZE = 1
DEFAULT_PORTS = {"astrbot_webui": 6185}
INTERNAL_ASTRBOT_PORT = 6185


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
    """Choose one available AstrBot WebUI host port.

    ``reserved`` lets the multi-instance manager avoid ports already allocated
    to another instance even when that instance's container is stopped.
    Legacy extra port keys in old configs are ignored deliberately: official
    deployments only own one host port.
    """

    start = int((preferred or {}).get("astrbot_webui") or DEFAULT_PORTS["astrbot_webui"])
    reserved_ports = {int(item) for item in (reserved or set())}
    candidate = start
    for _ in range(max_attempts):
        if candidate > 65535:
            break
        if candidate not in reserved_ports and (not probe or is_port_free(candidate, host)):
            return {"astrbot_webui": candidate}
        candidate += 1
    raise RuntimeError(f"无法为 astrbot_webui 找到空闲端口（从 {start} 开始）")


def allocate_instance_port_block(
    used: set[int] | None = None,
    *,
    host: str = "127.0.0.1",
    start: int = PORT_RANGE_START,
    end: int = PORT_RANGE_END,
    probe: bool = True,
) -> dict[str, int]:
    """Allocate one AstrBot WebUI host port for an official instance.

    Official QQ bots run entirely inside AstrBot, so each instance owns exactly
    one consecutive port. The whole project stays in the inclusive 5000-10000
    range.
    """

    start = max(int(PORT_RANGE_START), int(start))
    end = min(int(PORT_RANGE_END), int(end))
    reserved = {int(item) for item in (used or set())}
    if end - start + 1 < PORT_RANGE_SIZE:
        raise RuntimeError("端口范围不足以容纳一个实例端口")
    for port in range(start, end + 1):
        if port in reserved:
            continue
        if probe and not is_port_free(port, host):
            continue
        return {"astrbot_webui": port}
    raise RuntimeError(f"无法在 {start}-{end} 范围内为实例分配宿主机端口")


def allocate_onebot_ws_port(webui_port: int, *, host: str = "127.0.0.1", reserved: set[int] | None = None) -> int:
    """Pick a host port for the external OneBot v11 reverse WebSocket."""

    reserved = {int(item) for item in (reserved or set())}
    start = max(PORT_RANGE_START, int(webui_port) + 1)
    for candidate in range(start, PORT_RANGE_END + 1):
        if candidate in reserved or candidate == int(webui_port):
            continue
        if is_port_free(candidate, host):
            return candidate
    raise RuntimeError(f"无法为 OneBot WebSocket 找到空闲端口（从 {start} 开始）")


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


def image_names_for_prefix(prefix: str) -> str:
    """Return the AstrBot image name with an optional repository prefix."""

    normalized = _normalize_mirror_prefix(prefix)
    return f"{normalized}{ASTRBOT_IMAGE}" if normalized else ASTRBOT_IMAGE


def napcat_image_names_for_prefix(prefix: str) -> str:
    """Return the NapCat image name with an optional repository prefix."""

    normalized = _normalize_mirror_prefix(prefix)
    return f"{normalized}{NAPCAT_IMAGE_PATH}" if normalized else NAPCAT_IMAGE


def allocate_napcat_webui_port(webui_port: int, *, host: str = "127.0.0.1", reserved: set[int] | None = None) -> int:
    """Pick a host port for the built-in NapCat WebUI."""

    reserved = {int(item) for item in (reserved or set())}
    start = max(PORT_RANGE_START, int(webui_port) + 1)
    for candidate in range(start, PORT_RANGE_END + 1):
        if candidate in reserved or candidate == int(webui_port):
            continue
        if is_port_free(candidate, host):
            return candidate
    raise RuntimeError(f"无法为 NapCat WebUI 找到空闲端口（从 {start} 开始）")


def set_image_names(env_path: Path | str, astrbot_image: str) -> None:
    """Update only the AstrBot image entry in the generated .env file."""

    path = Path(env_path)
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到环境变量文件：{path}") from exc
    replacements = {"ASTRBOT_IMAGE": str(astrbot_image)}
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
    network_name: str = "qqbot-studio-net",
    include_localtime: bool | None = None,
    onebot_ws_port: int | None = None,
    onebot_gateway: str = "external",
    napcat_webui_port: int | None = None,
    napcat_container: str = "qqbot-napcat",
) -> str:
    """Return the Compose file for the selected QQ platform."""

    if include_localtime is None:
        include_localtime = os.name != "nt"
    localtime_volume = (
        "      - /etc/localtime:/etc/localtime:ro\n" if include_localtime else ""
    )
    expose_ws = bool(onebot_ws_port and onebot_gateway != "builtin")
    onebot_port_line = (
        f'      - "${{BIND_ADDRESS}}:${{ASTRBOT_ONEBOT_PORT}}:6199"\n'
        if expose_ws
        else ""
    )
    napcat_service = ""
    if onebot_gateway == "builtin" and napcat_webui_port:
        napcat_service = f"""  napcat:
    image: ${{NAPCAT_IMAGE}}
    container_name: {napcat_container}
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    depends_on:
      - astrbot
    ports:
      - "${{BIND_ADDRESS}}:${{NAPCAT_WEBUI_PORT}}:{INTERNAL_NAPCAT_WEBUI_PORT}"
    environment:
      TZ: Asia/Shanghai
      MODE: astrbot
      WEBUI_TOKEN: ${{NAPCAT_WEBUI_TOKEN}}
      NAPCAT_UID: ${{NAPCAT_UID:-1000}}
      NAPCAT_GID: ${{NAPCAT_GID:-1000}}
    volumes:
      - ./data/napcat/config:/app/napcat/config
      - ./data/napcat/qq:/app/.config/QQ
      - ./data/napcat/plugins:/app/napcat/plugins
    networks:
      - {network_name}

"""
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
{onebot_port_line}    environment:
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

{napcat_service}networks:
  {network_name}:
    name: {network_name}
    driver: bridge
"""

def _env_file(
    *,
    ports: dict[str, int],
    mirror_prefix: str = "",
    bind_address: str = "127.0.0.1",
    onebot_ws_port: int | None = None,
    onebot_gateway: str = "external",
    napcat_webui_port: int | None = None,
    napcat_token: str = "",
) -> str:
    astrbot_image = image_names_for_prefix(mirror_prefix)
    values = [
        ("ASTRBOT_IMAGE", astrbot_image),
        ("BIND_ADDRESS", str(bind_address or "127.0.0.1")),
        ("ASTRBOT_WEBUI_PORT", str(ports["astrbot_webui"])),
    ]
    if onebot_ws_port and onebot_gateway != "builtin":
        values.append(("ASTRBOT_ONEBOT_PORT", str(onebot_ws_port)))
    if onebot_gateway == "builtin" and napcat_webui_port:
        values.extend([
            ("NAPCAT_IMAGE", napcat_image_names_for_prefix(mirror_prefix)),
            ("NAPCAT_WEBUI_PORT", str(napcat_webui_port)),
            ("NAPCAT_WEBUI_TOKEN", str(napcat_token)),
            ("NAPCAT_UID", str(getattr(os, "getuid", lambda: 1000)())),
            ("NAPCAT_GID", str(getattr(os, "getgid", lambda: 1000)())),
        ])
    lines = ["# Generated by QQ Bot Studio. Contains secrets; keep mode 600."]
    lines.extend(f"{key}={value}" for key, value in values)
    return "\n".join(lines) + "\n"


def _astrbot_config(
    *,
    api_key: str,
    dashboard_username: str,
    dashboard_password: str,
    jwt_secret: str,
    official_appid: str = "",
    official_secret: str = "",
    platform_mode: str = "official",
    onebot_ws_port: int = 6199,
    onebot_token: str = "",
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
            (
                {
                    "id": "qqbot-onebot",
                    "type": "aiocqhttp",
                    "enable": True,
                    "ws_reverse_host": "0.0.0.0",
                    "ws_reverse_port": 6199,
                    "ws_reverse_token": str(onebot_token or ""),
                }
                if platform_mode == "onebot"
                else {
                    "id": "qqbot-official",
                    "type": "qq_official",
                    "enable": True,
                    "appid": str(official_appid or "").strip(),
                    "secret": str(official_secret or ""),
                    "enable_group_c2c": True,
                    "enable_guild_direct_message": True,
                    "use_markdown": True,
                }
            )
        ],
        "platform_settings": {
            "enable_id_white_list": False,
            "id_whitelist": [],
            "empty_mention_waiting": True,
        },
        "plugin_set": ["*"],
    }


def generate_artifacts(
    data_dir: Path | str,
    *,
    api_key: str,
    prompt: str,
    use_mirror: bool = False,
    secrets_data: dict[str, Any] | None = None,
    ports: dict[str, int] | None = None,
    mirror_prefix: str = "",
    bind_address: str = "127.0.0.1",
    project_name: str = "qqbot-studio",
    astrbot_container: str = "qqbot-astrbot",
    network_name: str = "qqbot-studio-net",
    official_appid: str = "",
    official_secret: str = "",
    platform_mode: str = "official",
    onebot_ws_port: int = 0,
    onebot_token: str = "",
    onebot_gateway: str = "external",
    qq_account: str = "",
    napcat_webui_port: int = 0,
    napcat_webui_token: str = "",
) -> dict[str, Any]:
    """Write the files required for an official single-container deployment.

    The returned mapping contains paths only; it never includes the DeepSeek
    API key. The caller is responsible for keeping ``api_key`` in memory only.
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
    for directory in (data_sub, astrbot_dir):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass

    probe_host = "0.0.0.0" if str(bind_address) in {"0.0.0.0", "::", ""} else str(bind_address)
    selected_ports = choose_ports(ports or secrets_data.get("ports") or None, host=probe_host)
    platform_mode = str(platform_mode or "official").strip().lower()
    if platform_mode not in {"official", "onebot"}:
        raise ValueError(f"不支持的 platform_mode：{platform_mode}")
    onebot_gateway = str(onebot_gateway or "external").strip().lower()
    if platform_mode == "onebot" and onebot_gateway not in {"external", "builtin"}:
        raise ValueError(f"不支持的 onebot_gateway：{onebot_gateway}")
    if platform_mode == "onebot" and onebot_gateway == "external" and not onebot_ws_port:
        onebot_ws_port = allocate_onebot_ws_port(
            selected_ports["astrbot_webui"],
            host=probe_host,
            reserved=set(selected_ports.values()),
        )
    if platform_mode == "onebot" and onebot_gateway == "builtin" and not napcat_webui_port:
        napcat_webui_port = allocate_napcat_webui_port(
            selected_ports["astrbot_webui"],
            host=probe_host,
            reserved=set(selected_ports.values()),
        )
    if platform_mode != "onebot":
        onebot_ws_port = 0
        onebot_token = ""
        onebot_gateway = "external"
        qq_account = ""
        napcat_webui_port = 0
        napcat_webui_token = ""
    if not mirror_prefix and use_mirror:
        mirror_prefix = mirror_prefixes()[0]

    compose_path = root / "compose.yml"
    env_path = root / ".env"
    astrbot_config_path = astrbot_dir / "cmd_config.json"

    _atomic_write_text(
        compose_path,
        _compose_yaml(
            project_name=project_name,
            astrbot_container=astrbot_container,
            network_name=network_name,
            onebot_ws_port=onebot_ws_port or None,
            onebot_gateway=onebot_gateway,
            napcat_webui_port=napcat_webui_port or None,
            napcat_container=astrbot_container.replace("astrbot", "napcat"),
        ),
        mode=0o644,
    )

    _atomic_write_text(
        env_path,
        _env_file(
            ports=selected_ports,
            mirror_prefix=mirror_prefix,
            bind_address=bind_address,
            onebot_ws_port=onebot_ws_port or None,
            onebot_gateway=onebot_gateway,
            napcat_webui_port=napcat_webui_port or None,
            napcat_token=napcat_webui_token,
        ),
        mode=0o600,
    )

    if platform_mode == "onebot" and onebot_gateway == "builtin":
        if not str(qq_account or "").isdigit():
            raise ValueError("内置 OneBot 网关需要有效的机器人 QQ 号")
        napcat_config_dir = data_sub / "napcat" / "config"
        for directory in (
            napcat_config_dir,
            data_sub / "napcat" / "qq",
            data_sub / "napcat" / "plugins",
        ):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        onebot_client = {
            "name": "astrbot",
            "enable": True,
            "url": "ws://astrbot:6199/ws",
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "token": str(onebot_token or ""),
            "reconnectInterval": 5000,
            "heartInterval": 30000,
        }
        _atomic_write_text(
            napcat_config_dir / f"onebot11_{qq_account}.json",
            json.dumps(onebot_client, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )

    astrbot_config = _astrbot_config(
        api_key=api_key,
        dashboard_username=str(secrets_data["astrbot_username"]),
        dashboard_password=str(secrets_data["astrbot_password"]),
        jwt_secret=str(secrets_data["astrbot_jwt_secret"]),
        official_appid=official_appid,
        official_secret=official_secret,
        platform_mode=platform_mode,
        onebot_ws_port=onebot_ws_port or 0,
        onebot_token=onebot_token,
    )
    _atomic_write_text(
        astrbot_config_path,
        json.dumps(astrbot_config, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )

    return {
        "root": root,
        "compose": compose_path,
        "env": env_path,
        "astrbot_config": astrbot_config_path,
        "platform_mode": platform_mode,
        "qq_official_appid": str(official_appid or "").strip(),
        "onebot_ws_port": int(onebot_ws_port or 0),
        "onebot_gateway": onebot_gateway,
        "qq_account": str(qq_account or ""),
        "napcat_webui_port": int(napcat_webui_port or 0),
        "data": data_sub,
        "astrbot_data": astrbot_dir,
        "ports": selected_ports,
        "mirror_prefix": mirror_prefix,
        "project_name": project_name,
        "bind_address": bind_address,
    }
