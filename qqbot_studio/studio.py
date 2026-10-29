"""Studio-wide settings, multi-instance catalog, and mirror-node management."""

from __future__ import annotations

import concurrent.futures
import copy
import ctypes
import json
import os
import re
import secrets
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .assistant_ext import AssistantExtensionManager
from .configgen import allocate_instance_port_block
from .dockerctl import DockerController, DockerError
from .store import Store, chmod_best_effort, default_data_dir, utc_now

STUDIO_SCHEMA_VERSION = 2
INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_BIND_PORT = 8765

DEFAULT_MIRROR_NODES: list[dict[str, Any]] = [
    {
        "id": "daocloud",
        "name": "DaoCloud",
        "prefix": "m.daocloud.io/docker.io/",
        "custom": False,
    },
    {
        "id": "1ms",
        "name": "1ms.run",
        "prefix": "docker.1ms.run/",
        "custom": False,
    },
    {
        "id": "xuanyuan",
        "name": "轩辕镜像",
        "prefix": "docker.xuanyuan.me/",
        "custom": False,
    },
    {
        "id": "rat",
        "name": "RatHub",
        "prefix": "hub.rat.dev/",
        "custom": False,
    },
]


def _directory_usage(path: Path | str) -> tuple[int, int, list[str]]:
    """Return (bytes, files, errors) for a directory tree without following links."""

    root = Path(path)
    total = 0
    files = 0
    errors: list[str] = []
    if not root.exists():
        return 0, 0, errors
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            errors.append(f"无法读取 {current}：{exc}")
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += int(entry.stat(follow_symlinks=False).st_size)
                    files += 1
            except OSError as exc:
                errors.append(f"无法统计 {entry.path}：{exc}")
    return total, files, errors[:20]


def _disk_usage(path: Path | str) -> dict[str, Any]:
    """Return filesystem capacity with a Windows/Linux-safe fallback."""

    try:
        usage = shutil.disk_usage(str(path))
        used_percent = round((usage.used / usage.total) * 100, 2) if usage.total else None
        return {
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "used_percent": used_percent,
            "error": "",
        }
    except (OSError, AttributeError) as exc:
        # os.statvfs exists on Unix-like systems and does not require third-party
        # packages.  On Windows it is expected to be absent.
        try:
            stat = os.statvfs(str(path))
            total = int(stat.f_frsize * stat.f_blocks)
            free = int(stat.f_frsize * stat.f_bavail)
            used = max(0, total - free)
            return {
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "used_percent": round((used / total) * 100, 2) if total else None,
                "error": "",
            }
        except (AttributeError, OSError):
            return {
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
                "used_percent": None,
                "error": f"磁盘容量读取失败：{exc}",
            }


def _host_memory_info() -> dict[str, Any]:
    """Read physical host memory using only the standard library."""

    result: dict[str, Any] = {
        "total_bytes": None,
        "available_bytes": None,
        "used_bytes": None,
        "used_percent": None,
        "source": "",
        "error": "",
    }
    if os.name == "nt":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.ullTotalPhys)
                available = int(status.ullAvailPhys)
                used = max(0, total - available)
                result.update(
                    {
                        "total_bytes": total,
                        "available_bytes": available,
                        "used_bytes": used,
                        "used_percent": round((used / total) * 100, 2) if total else None,
                        "source": "GlobalMemoryStatusEx",
                    }
                )
                return result
            result["error"] = "Windows GlobalMemoryStatusEx 读取失败。"
        except (AttributeError, OSError, ValueError) as exc:
            result["error"] = f"Windows 内存读取失败：{exc}"
        return result

    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0]) * 1024
        total = int(values.get("MemTotal", 0))
        if total > 0:
            available = int(
                values.get("MemAvailable", 0)
                or values.get("MemFree", 0)
                + values.get("Buffers", 0)
                + values.get("Cached", 0)
            )
            available = max(0, min(total, available))
            used = total - available
            result.update(
                {
                    "total_bytes": total,
                    "available_bytes": available,
                    "used_bytes": used,
                    "used_percent": round((used / total) * 100, 2),
                    "source": "/proc/meminfo",
                }
            )
            return result
    except (OSError, ValueError) as exc:
        result["error"] = f"/proc/meminfo 读取失败：{exc}"

    # Best-effort Unix fallback (macOS/BSD or unusual proc mounts).
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        total = page_size * total_pages
        available = page_size * available_pages
        used = max(0, total - available)
        result.update(
            {
                "total_bytes": total,
                "available_bytes": available,
                "used_bytes": used,
                "used_percent": round((used / total) * 100, 2) if total else None,
                "source": "os.sysconf",
            }
        )
        if total <= 0:
            result["error"] = "宿主机内存总量为 0 或不可用。"
    except (AttributeError, OSError, ValueError) as exc:
        result["error"] = result["error"] or f"宿主机内存读取失败：{exc}"
    return result


def _parse_size_bytes(value: Any) -> int | None:
    """Parse Docker's human-readable memory values (for example 123.4MiB)."""

    text = str(value or "").strip().replace(" ", "")
    if not text or text.lower() in {"n/a", "0b"}:
        return 0 if text.lower() == "0b" else None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]+)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    factors = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    factor = factors.get(unit)
    return int(number * factor) if factor is not None else None


def _parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().rstrip("%")
    if not text or text.lower() in {"n/a", "--"}:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def default_studio_settings() -> dict[str, Any]:
    return {
        "schema_version": STUDIO_SCHEMA_VERSION,
        "bind_host": DEFAULT_BIND_HOST,
        "bind_port": DEFAULT_BIND_PORT,
        "public_host": "",
        "first_run_complete": False,
        "mirror_nodes": [dict(node) for node in DEFAULT_MIRROR_NODES],
        "selected_mirror_id": DEFAULT_MIRROR_NODES[0]["id"],
        "next_instance_seq": 1,
    }


def detect_public_host() -> str:
    """Best-effort LAN/public address used to print browser URLs."""

    # UDP connect does not send packets but asks the kernel for the preferred
    # source address. It is useful for private/LAN deployments; users behind
    # NAT should override with --public-host.
    for target in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.5)
                sock.connect(target)
                address = sock.getsockname()[0]
                if address and not address.startswith("127."):
                    return address
        except OSError:
            continue
    try:
        address = socket.gethostbyname(socket.gethostname())
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    return "127.0.0.1"


def _normalize_mirror_prefix(prefix: str) -> str:
    value = str(prefix or "").strip()
    if not value:
        raise ValueError("镜像前缀不能为空")
    if "://" in value:
        raise ValueError("镜像前缀不要包含 http:// 或 https://，只填写主机和路径")
    if any(ch.isspace() for ch in value):
        raise ValueError("镜像前缀不能包含空白字符")
    if not value.endswith("/"):
        value += "/"
    # Basic host/path sanity; repositories may contain dots, ports, hyphens.
    if not re.fullmatch(r"[A-Za-z0-9._:\-/]+", value):
        raise ValueError("镜像前缀包含不支持的字符")
    return value


class StudioManager:
    """Manage global settings and multiple isolated bot instances."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else default_data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        chmod_best_effort(self.root, 0o700)
        self.instances_dir = self.root / "instances"
        self.instances_dir.mkdir(parents=True, exist_ok=True)
        chmod_best_effort(self.instances_dir, 0o700)
        self.settings_path = self.root / "studio.json"
        self.server_pid_path = self.root / "server.pid"
        # Optional assistant B is an independent extension.  Constructing this
        # manager performs no network access and never starts the assistant.
        self.assistant_extension = AssistantExtensionManager(self.root)
        self._mirror_cache: dict[str, dict[str, Any]] = {}
        self._manager_cache: dict[str, Any] = {}
        self._lock = __import__("threading").RLock()
        self._stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._migration_notes: list[str] = []
        try:
            self._migrate_legacy_root_instance()
        except (OSError, ValueError) as exc:
            self._migration_notes.append(f"旧实例迁移未完成：{exc}")
        try:
            self.migrate_studio_settings()
        except (OSError, ValueError) as exc:
            self._migration_notes.append(f"控制台设置迁移未完成：{exc}")

    # ------------------------------------------------------------------
    # Global settings
    # ------------------------------------------------------------------
    def load_settings(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法读取 studio.json：{exc}") from exc
        defaults = default_studio_settings()
        if not isinstance(raw, dict):
            raw = {}
        settings = {**defaults, **raw}
        if not isinstance(settings.get("mirror_nodes"), list):
            settings["mirror_nodes"] = [dict(node) for node in DEFAULT_MIRROR_NODES]
        # Ensure built-in nodes are always present, but keep user nodes.
        by_id = {
            str(node.get("id")): node
            for node in settings["mirror_nodes"]
            if isinstance(node, dict) and node.get("id")
        }
        for node in DEFAULT_MIRROR_NODES:
            by_id.setdefault(node["id"], dict(node))
        settings["mirror_nodes"] = list(by_id.values())
        if not any(node.get("id") == settings.get("selected_mirror_id") for node in settings["mirror_nodes"]):
            settings["selected_mirror_id"] = settings["mirror_nodes"][0]["id"]
        try:
            schema_version = int(settings.get("schema_version") or 0)
        except (TypeError, ValueError):
            schema_version = 0
        if schema_version < STUDIO_SCHEMA_VERSION:
            settings["schema_version"] = STUDIO_SCHEMA_VERSION
        return settings

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
        tmp = self.settings_path.with_name(f".{self.settings_path.name}.{secrets.token_hex(4)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.settings_path)
            chmod_best_effort(self.settings_path, 0o600)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return settings

    def update_network(
        self,
        *,
        bind_host: str | None = None,
        bind_port: int | None = None,
        public_host: str | None = None,
    ) -> dict[str, Any]:
        settings = self.load_settings()
        if bind_host is not None:
            host = str(bind_host).strip()
            if not host:
                raise ValueError("监听 IP 不能为空")
            settings["bind_host"] = host
        if bind_port is not None:
            port = int(bind_port)
            if not (1 <= port <= 65535):
                raise ValueError("端口必须在 1–65535 之间")
            settings["bind_port"] = port
        if public_host is not None:
            settings["public_host"] = str(public_host).strip()
        settings["first_run_complete"] = True
        return self.save_settings(settings)

    def public_host(self) -> str:
        settings = self.load_settings()
        value = str(settings.get("public_host") or "").strip()
        if value:
            return value
        bind_host = str(settings.get("bind_host") or "")
        if bind_host not in {"0.0.0.0", "::", ""}:
            return bind_host
        return detect_public_host()

    def public_base_url(self) -> str:
        settings = self.load_settings()
        host = self.public_host()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{int(settings['bind_port'])}"

    # ------------------------------------------------------------------
    # Mirror nodes
    # ------------------------------------------------------------------
    def list_mirror_nodes(self) -> list[dict[str, Any]]:
        settings = self.load_settings()
        selected = str(settings.get("selected_mirror_id") or "")
        nodes: list[dict[str, Any]] = []
        for node in settings["mirror_nodes"]:
            if not isinstance(node, dict):
                continue
            item = dict(node)
            item["selected"] = item.get("id") == selected
            cached = self._mirror_cache.get(str(item.get("id")))
            if cached:
                item.update(cached)
            else:
                item.setdefault("latency_ms", None)
                item.setdefault("status", "unknown")
            nodes.append(item)
        return nodes

    def add_mirror_node(self, name: str, prefix: str) -> dict[str, Any]:
        node_name = str(name or "").strip()
        if len(node_name) < 1 or len(node_name) > 40:
            raise ValueError("节点名称长度必须为 1–40 个字符")
        normalized = _normalize_mirror_prefix(prefix)
        settings = self.load_settings()
        if any(str(node.get("prefix")) == normalized for node in settings["mirror_nodes"]):
            raise ValueError("该镜像前缀已存在")
        node_id = f"custom-{secrets.token_hex(4)}"
        node = {"id": node_id, "name": node_name, "prefix": normalized, "custom": True}
        settings["mirror_nodes"].append(node)
        self.save_settings(settings)
        return node

    def delete_mirror_node(self, node_id: str) -> None:
        settings = self.load_settings()
        target = next(
            (node for node in settings["mirror_nodes"] if node.get("id") == node_id),
            None,
        )
        if not target:
            raise ValueError("镜像节点不存在")
        if not target.get("custom"):
            raise ValueError("内置节点不能删除")
        settings["mirror_nodes"] = [node for node in settings["mirror_nodes"] if node.get("id") != node_id]
        if settings.get("selected_mirror_id") == node_id:
            settings["selected_mirror_id"] = settings["mirror_nodes"][0]["id"]
        self._mirror_cache.pop(node_id, None)
        self.save_settings(settings)

    def select_mirror(self, node_id: str) -> None:
        settings = self.load_settings()
        if not any(node.get("id") == node_id for node in settings["mirror_nodes"]):
            raise ValueError("镜像节点不存在")
        settings["selected_mirror_id"] = node_id
        self.save_settings(settings)

    def selected_mirror_prefix(self) -> str:
        settings = self.load_settings()
        selected_id = str(settings.get("selected_mirror_id") or "")
        for node in settings["mirror_nodes"]:
            if node.get("id") == selected_id:
                return str(node.get("prefix") or "")
        return ""

    def candidate_mirror_prefixes(self) -> list[str]:
        """Selected node first, then all remaining nodes as fallbacks."""

        settings = self.load_settings()
        selected_id = str(settings.get("selected_mirror_id") or "")
        prefixes: list[str] = []
        for node in settings["mirror_nodes"]:
            if node.get("id") == selected_id and node.get("prefix"):
                prefixes.append(str(node["prefix"]))
        for node in settings["mirror_nodes"]:
            prefix = str(node.get("prefix") or "")
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
        return prefixes

    @staticmethod
    def _mirror_test_url(prefix: str) -> str:
        host = str(prefix).split("/", 1)[0].strip()
        if not host:
            raise ValueError("镜像节点前缀无效")
        return f"https://{host}/v2/"

    def _test_mirror_node(self, node: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        node_id = str(node.get("id") or "")
        try:
            url = self._mirror_test_url(str(node.get("prefix") or ""))
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "QQBotStudio/2.0"},
                method="GET",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(request, timeout=7) as response:
                    code = int(getattr(response, "status", 200))
                    response.read(1)
            except urllib.error.HTTPError as exc:
                code = int(exc.code)
            latency = max(0, int(round((time.perf_counter() - started) * 1000)))
            status = "ok" if code in (200, 401, 403) else "warn"
            result = {"latency_ms": latency, "status": status, "http_status": code, "error": ""}
        except Exception as exc:  # noqa: BLE001 - per-node diagnostics
            latency = max(0, int(round((time.perf_counter() - started) * 1000)))
            result = {
                "latency_ms": latency,
                "status": "error",
                "http_status": 0,
                "error": str(exc),
            }
        with self._lock:
            self._mirror_cache[node_id] = {**result, "tested_at": utc_now()}
        return {"id": node_id, **result}

    def test_mirrors(self, ids: list[str] | None = None) -> list[dict[str, Any]]:
        nodes = self.list_mirror_nodes()
        wanted = set(ids or [])
        selected_nodes = [
            node for node in nodes if not wanted or str(node.get("id")) in wanted
        ]
        if not selected_nodes:
            return []
        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(selected_nodes))) as pool:
            futures = [pool.submit(self._test_mirror_node, node) for node in selected_nodes]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.append(future.result())
                except Exception:
                    continue
        order = {str(node.get("id")): index for index, node in enumerate(nodes)}
        results.sort(key=lambda item: order.get(str(item.get("id")), 999))
        return results

    # ------------------------------------------------------------------
    # Instances
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Legacy/v1 and v2 compatibility migrations
    # ------------------------------------------------------------------
    @staticmethod
    def _infer_compose_meta(instance_dir: Path, instance_id: str) -> dict[str, Any]:
        """Infer instance metadata from an old compose file and config.json."""

        compose_path = instance_dir / "compose.yml"
        compose_text = ""
        try:
            compose_text = compose_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        containers: dict[str, str] = {}
        for service in ("astrbot", "snowluma"):
            match = re.search(
                rf"(?ms)^  {service}:\s*\n(.*?)(?=^  [A-Za-z0-9_.-]+:\s*$|^networks:\s*$|\Z)",
                compose_text,
            )
            if match:
                name_match = re.search(r"(?m)^\s+container_name:\s*[\"']?([A-Za-z0-9_.-]+)", match.group(1))
                if name_match:
                    containers[service] = name_match.group(1)
        legacy_names = (
            containers.get("astrbot") == "qqbot-astrbot"
            or containers.get("snowluma") == "qqbot-snowluma"
        )
        project_name = "qqbot-studio" if legacy_names else f"qqbot-studio-{instance_id}"
        network_name = "qqbot-studio-net" if legacy_names else f"qqbot-studio-{instance_id}-net"
        network_match = re.search(
            r"(?ms)^networks:\s*\n(?:.*?^\s+name:\s*[\"']?([A-Za-z0-9_.-]+)[\"']?\s*$)",
            compose_text,
        )
        if network_match:
            network_name = network_match.group(1)
        created_at = utc_now()
        try:
            config = json.loads((instance_dir / "config.json").read_text(encoding="utf-8"))
            if isinstance(config, dict) and config.get("deployed_at"):
                created_at = str(config["deployed_at"])
        except (OSError, ValueError):
            pass
        return {
            "id": instance_id,
            "name": "旧版 QQ 机器人",
            "created_at": created_at,
            "project_name": project_name,
            "astrbot_container": containers.get("astrbot") or f"qqbot-{instance_id}-astrbot",
            "snowluma_container": containers.get("snowluma") or f"qqbot-{instance_id}-snowluma",
            "network_name": network_name,
            "legacy": True,
        }

    def _infer_instance_meta(self, path: Path, instance_id: str) -> dict[str, Any] | None:
        """Recover an instance directory whose metadata file is missing."""

        if not (path / "config.json").exists() and not (path / "compose.yml").exists():
            return None
        return self._infer_compose_meta(path, instance_id)

    def _migrate_legacy_root_instance(self) -> None:
        """Import the v1 single-instance layout into ``instances/bot-1``.

        v1 kept ``config.json``, ``secrets.json``, ``compose.yml``, ``.env``,
        ``jobs/`` and ``data/`` directly in the application data directory.
        The move is same-filesystem and atomic per item.  Existing v2/v3
        instances always take precedence and are never touched.
        """

        if any(self.instances_dir.iterdir()):
            return
        if not (self.root / "config.json").exists():
            return
        target = self._instance_path("bot-1")
        if target.exists():
            return
        target.mkdir(parents=True, exist_ok=False)
        chmod_best_effort(target, 0o700)
        moved: list[str] = []
        try:
            for name in (
                "config.json",
                "secrets.json",
                "deploy-state.json",
                "compose.yml",
                ".env",
                "jobs",
                "data",
            ):
                source = self.root / name
                if not source.exists():
                    continue
                destination = target / name
                try:
                    os.replace(source, destination)
                except OSError:
                    shutil.move(str(source), str(destination))
                moved.append(name)
            meta = self._infer_compose_meta(target, "bot-1")
            self._write_instance_meta(target, meta)
            store = Store(target)
            store.migrate_config()
            settings = self.load_settings()
            settings["schema_version"] = STUDIO_SCHEMA_VERSION
            try:
                settings["next_instance_seq"] = max(2, int(settings.get("next_instance_seq") or 1))
            except (TypeError, ValueError):
                settings["next_instance_seq"] = 2
            self.save_settings(settings)
            self._migration_notes.append(
                "已将旧版单实例数据迁移到 instances/bot-1；原 compose/.env/cmd_config 保持不变。"
            )
        except OSError as exc:
            # Do not delete or rewrite moved data.  Keep the remaining root
            # files so the user can recover manually.
            raise ValueError(f"迁移旧实例数据时失败：{exc}；已迁移文件位于 {target}") from exc

    def migrate_studio_settings(self) -> bool:
        """Persist additive Studio-level defaults for older installations."""

        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(raw, dict):
            return False
        settings = {**default_studio_settings(), **raw}
        changed = False
        try:
            version = int(settings.get("schema_version") or 0)
        except (TypeError, ValueError):
            version = 0
        if version < STUDIO_SCHEMA_VERSION:
            settings["schema_version"] = STUDIO_SCHEMA_VERSION
            changed = True
        if changed:
            self.save_settings(settings)
        return changed

    def _instance_path(self, instance_id: str) -> Path:
        if not INSTANCE_ID_RE.fullmatch(str(instance_id)):
            raise ValueError("无效的实例 ID")
        return self.instances_dir / str(instance_id)

    def used_instance_ports(self, *, exclude: str | None = None) -> set[int]:
        """Collect ports already allocated to instance configs or env files."""

        used: set[int] = set()
        for meta in self.list_instance_metas():
            instance_id = str(meta.get("id") or "")
            if not instance_id or instance_id == exclude:
                continue
            path = self._instance_path(instance_id)
            for filename in ("config.json", "secrets.json"):
                try:
                    data = json.loads((path / filename).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                ports = data.get("ports") if isinstance(data, dict) else None
                if isinstance(ports, dict):
                    for value in ports.values():
                        try:
                            used.add(int(value))
                        except (TypeError, ValueError):
                            pass
            try:
                env_text = (path / ".env").read_text(encoding="utf-8")
            except OSError:
                continue
            for line in env_text.splitlines():
                key, _, value = line.partition("=")
                if key in {"ASTRBOT_WEBUI_PORT", "SNOWLUMA_WEBUI_PORT_HOST", "NOVNC_PORT"}:
                    try:
                        used.add(int(value.strip()))
                    except ValueError:
                        pass
        return used

    def delete_instance(self, instance_id: str) -> dict[str, Any]:
        """Remove one instance's containers, metadata and data directory."""

        meta = self.get_instance_meta(instance_id)
        path = self._instance_path(instance_id)
        manager = self.get_manager(instance_id)
        if getattr(manager, "_active_job_id", ""):
            raise ValueError("实例有部署任务正在运行，不能删除。")
        compose_path = path / "compose.yml"
        env_path = path / ".env"
        cleanup_error = ""
        try:
            if compose_path.exists() and env_path.exists():
                manager.docker.down()
            else:
                manager.docker.require_ready()
                manager.docker._docker(
                    [
                        "rm",
                        "-f",
                        str(meta.get("astrbot_container") or ""),
                        str(meta.get("snowluma_container") or ""),
                    ],
                    timeout=120,
                )
        except Exception as exc:  # noqa: BLE001 - report but do not orphan silently
            cleanup_error = str(exc)
        if cleanup_error:
            raise ValueError(
                "实例容器清理失败，未删除数据目录。请先在终端确认容器已停止："
                f" {cleanup_error}"
            )
        with self._lock:
            self._manager_cache.pop(instance_id, None)
        shutil.rmtree(path)
        return {"id": instance_id, "name": meta.get("name") or instance_id}

    def create_instance(self, name: str | None = None) -> dict[str, Any]:
        settings = self.load_settings()
        seq = int(settings.get("next_instance_seq") or 1)
        instance_id = f"bot-{seq}"
        path = self._instance_path(instance_id)
        while path.exists():
            seq += 1
            instance_id = f"bot-{seq}"
            path = self._instance_path(instance_id)
        settings["next_instance_seq"] = seq + 1
        self.save_settings(settings)
        path.mkdir(parents=True, exist_ok=False)
        chmod_best_effort(path, 0o700)
        display_name = str(name or "").strip() or f"QQ 机器人 {seq}"
        if len(display_name) > 60:
            display_name = display_name[:60]
        meta = {
            "id": instance_id,
            "name": display_name,
            "created_at": utc_now(),
            "project_name": f"qqbot-studio-{instance_id}",
            "astrbot_container": f"qqbot-{instance_id}-astrbot",
            "snowluma_container": f"qqbot-{instance_id}-snowluma",
            "network_name": f"qqbot-studio-{instance_id}-net",
        }
        self._write_instance_meta(path, meta)
        # Initialize instance-local Store immediately (creates 0700 directories)
        # and reserve host ports at creation time. This prevents two instances
        # from choosing the same defaults when the first one is not running yet.
        store = Store(path)
        settings = self.load_settings()
        reserved = self.used_instance_ports(exclude=instance_id)
        try:
            reserved.add(int(settings.get("bind_port") or DEFAULT_BIND_PORT))
        except (TypeError, ValueError):
            pass
        ports = allocate_instance_port_block(
            reserved,
            host=str(settings.get("bind_host") or DEFAULT_BIND_HOST),
        )
        config = store.load_config()
        config["ports"] = ports
        store.save_config(config)
        return meta

    @staticmethod
    def _write_instance_meta(path: Path, meta: dict[str, Any]) -> None:
        target = path / "instance.json"
        tmp = target.with_name(f".instance.{secrets.token_hex(4)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(meta, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            chmod_best_effort(target, 0o600)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def get_instance_meta(self, instance_id: str) -> dict[str, Any]:
        path = self._instance_path(instance_id)
        meta_path = path / "instance.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            inferred = self._infer_instance_meta(path, instance_id)
            if inferred is None:
                raise ValueError(f"实例不存在：{instance_id}") from exc
            # Old v2 directories could have compose/config but no metadata.
            # Write a canonical copy so subsequent requests do not re-infer.
            try:
                self._write_instance_meta(path, inferred)
            except OSError:
                pass
            meta = inferred
        except (OSError, ValueError) as exc:
            raise ValueError(f"实例配置损坏：{instance_id}：{exc}") from exc
        if not isinstance(meta, dict) or meta.get("id") != instance_id:
            raise ValueError(f"实例配置无效：{instance_id}")
        # Additive defaults for pre-v2 metadata.  Existing values always win.
        meta.setdefault("name", instance_id)
        meta.setdefault("created_at", utc_now())
        meta.setdefault("project_name", f"qqbot-studio-{instance_id}")
        meta.setdefault("astrbot_container", f"qqbot-{instance_id}-astrbot")
        meta.setdefault("snowluma_container", f"qqbot-{instance_id}-snowluma")
        meta.setdefault("network_name", f"qqbot-studio-{instance_id}-net")
        return meta

    def list_instance_metas(self) -> list[dict[str, Any]]:
        metas: list[dict[str, Any]] = []
        if not self.instances_dir.exists():
            return metas
        for path in sorted(self.instances_dir.iterdir()):
            if not path.is_dir():
                continue
            try:
                metas.append(self.get_instance_meta(path.name))
            except ValueError:
                continue
        metas.sort(key=lambda item: str(item.get("created_at") or ""))
        return metas

    def get_manager(self, instance_id: str) -> Any:
        meta = self.get_instance_meta(instance_id)
        with self._lock:
            manager = self._manager_cache.get(instance_id)
            if manager is not None:
                settings = self.load_settings()
                manager.public_host = self.public_host()
                manager.bind_address = str(settings.get("bind_host") or DEFAULT_BIND_HOST)
                return manager
        from .deployer import DeployManager

        instance_dir = self._instance_path(instance_id)
        store = Store(instance_dir)
        docker = DockerController(
            instance_dir,
            project_name=str(meta["project_name"]),
            astrbot_container=str(meta["astrbot_container"]),
            snowluma_container=str(meta["snowluma_container"]),
        )
        settings = self.load_settings()
        manager = DeployManager(
            store=store,
            docker=docker,
            public_host=self.public_host(),
            bind_address=str(settings.get("bind_host") or DEFAULT_BIND_HOST),
            project_name=str(meta["project_name"]),
            astrbot_container=str(meta["astrbot_container"]),
            snowluma_container=str(meta["snowluma_container"]),
            network_name=str(meta["network_name"]),
        )
        with self._lock:
            self._manager_cache[instance_id] = manager
        return manager

    def instance_summary(self, instance_id: str) -> dict[str, Any]:
        meta = self.get_instance_meta(instance_id)
        manager = self.get_manager(instance_id)
        status = manager.get_status()
        deployment = status.get("deployment", {})
        return {
            "id": instance_id,
            "name": meta.get("name") or instance_id,
            "created_at": meta.get("created_at") or "",
            "deployed": bool(deployment.get("deployed")),
            "running": deployment.get("running"),
            "qq_account": deployment.get("qq_account") or "",
            "urls": deployment.get("urls") or {},
            "active_job_id": deployment.get("active_job_id") or "",
            "active_job_status": deployment.get("active_job_status"),
            "memory_mode": deployment.get("memory_mode") or status.get("memory_mode") or "",
            "notice": status.get("notice") or "",
        }

    def list_instance_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for meta in self.list_instance_metas():
            try:
                summaries.append(self.instance_summary(str(meta["id"])))
            except Exception as exc:  # noqa: BLE001 - keep console usable
                summaries.append(
                    {
                        "id": meta.get("id"),
                        "name": meta.get("name") or meta.get("id"),
                        "created_at": meta.get("created_at") or "",
                        "deployed": False,
                        "running": None,
                        "qq_account": "",
                        "urls": {},
                        "active_job_id": "",
                        "active_job_status": None,
                        "memory_mode": "",
                        "state_error": str(exc),
                        "notice": f"状态读取失败：{exc}",
                    }
                )
        return summaries

    def repair_instances(self) -> list[dict[str, Any]]:
        """Check all deployed instances and let Compose repair bad containers."""

        results: list[dict[str, Any]] = []
        for meta in self.list_instance_metas():
            instance_id = str(meta["id"])
            try:
                manager = self.get_manager(instance_id)
                results.append(manager.repair_containers())
            except Exception as exc:  # noqa: BLE001 - report per instance
                results.append(
                    {
                        "instance_id": instance_id,
                        "name": meta.get("name") or instance_id,
                        "ok": False,
                        "action": "error",
                        "message": str(exc),
                    }
                )
        return results

    # ------------------------------------------------------------------
    # Storage, host memory and Docker container statistics
    # ------------------------------------------------------------------
    @staticmethod
    def _docker_stats_records(
        metas: list[dict[str, Any]],
        managers: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Collect docker stats for all requested containers.

        Every failure is represented as a per-container record with the
        standard fields set to null.  This endpoint never lets an absent Docker
        daemon make the whole management console fail.
        """

        expected: list[dict[str, str]] = []
        for meta in metas:
            instance_id = str(meta.get("id") or "")
            expected.append(
                {
                    "instance_id": instance_id,
                    "container": str(meta.get("astrbot_container") or ""),
                    "service": "astrbot",
                }
            )
            expected.append(
                {
                    "instance_id": instance_id,
                    "container": str(meta.get("snowluma_container") or ""),
                    "service": "snowluma",
                }
            )
        if not expected:
            return []

        base: list[dict[str, Any]] = [
            {
                "instance_id": item["instance_id"],
                "container": item["container"],
                "service": item["service"],
                "status": "unknown",
                "cpu_percent": None,
                "memory_usage_bytes": None,
                "memory_limit_bytes": None,
                "memory_percent": None,
                "error": "",
            }
            for item in expected
        ]
        names = [item["container"] for item in expected if item["container"]]
        if not names:
            for record in base:
                record["error"] = "实例元数据缺少容器名。"
            return base

        manager = next(iter(managers.values()), None)
        if manager is None:
            for record in base:
                record["error"] = "实例管理器不可用，无法读取 Docker 统计。"
            return base
        docker = getattr(manager, "docker", None)
        if docker is None:
            for record in base:
                record["error"] = "Docker 控制器不可用。"
            return base
        try:
            status = docker.detect()
        except Exception as exc:  # noqa: BLE001 - degrade per container
            status = None
            detail = f"Docker 状态检测失败：{exc}"
        else:
            detail = ""
            if not getattr(status, "ready", False):
                detail = getattr(status, "detail", "") or "Docker 或 Compose 当前不可用。"
        if detail:
            for record in base:
                record["error"] = detail
            return base

        runner = getattr(docker, "_docker", None)
        if not callable(runner):
            for record in base:
                record["error"] = "Docker 控制器不支持统计命令。"
            return base
        try:
            result = runner(
                ["stats", "--no-stream", "--format", "{{json .}}", *names],
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001
            for record in base:
                record["error"] = f"Docker 容器统计读取失败：{exc}"
            return base
        if not getattr(result, "ok", False):
            output = getattr(result, "output", "") or getattr(result, "stderr", "") or "Docker stats 命令失败"
            for record in base:
                record["error"] = str(output).strip()[:500]
            return base

        parsed: dict[str, dict[str, Any]] = {}
        for line in str(getattr(result, "stdout", "") or "").splitlines():
            text = line.strip()
            if not text:
                continue
            record: dict[str, Any] = {}
            if text.startswith("{"):
                try:
                    raw = json.loads(text)
                    if isinstance(raw, dict):
                        record = raw
                except ValueError:
                    continue
            else:
                parts = text.split("\t")
                if len(parts) >= 4:
                    record = {
                        "Name": parts[0],
                        "CPUPerc": parts[1],
                        "MemUsage": parts[2],
                        "MemPerc": parts[3],
                    }
            name = str(record.get("Name") or record.get("name") or "").strip().lstrip("/")
            if not name:
                continue
            memory_usage = record.get("MemUsage") or record.get("mem_usage") or ""
            usage_bytes: int | None = None
            limit_bytes: int | None = None
            pieces = str(memory_usage).split("/", 1)
            if pieces:
                usage_bytes = _parse_size_bytes(pieces[0])
            if len(pieces) > 1:
                limit_bytes = _parse_size_bytes(pieces[1])
            parsed[name] = {
                "cpu_percent": _parse_percent(record.get("CPUPerc") or record.get("cpu_perc")),
                "memory_usage_bytes": usage_bytes,
                "memory_limit_bytes": limit_bytes,
                "memory_percent": _parse_percent(record.get("MemPerc") or record.get("mem_perc")),
            }

        for record in base:
            stats = parsed.get(record["container"])
            if stats is None:
                record["error"] = "Docker stats 未返回该容器的数据（容器可能未运行）。"
                continue
            record.update(stats)
            record["status"] = "available"
        return base

    def get_stats(
        self,
        instance_id: str | None = None,
        *,
        force_refresh: bool = False,
        cache_ttl_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Return host, disk, per-instance and Docker resource statistics.

        The return value is intentionally partial-data friendly: ``ok`` remains
        true when Docker or a platform-specific metric is unavailable, while
        ``errors`` and per-record ``error`` fields explain what could not be
        read.  This keeps the console usable on Windows without Docker.
        """

        wanted = str(instance_id or "").strip() or None
        cache_key = wanted or "global"
        now = time.monotonic()
        with self._lock:
            cached = self._stats_cache.get(cache_key)
            if cached and not force_refresh and now - cached[0] <= max(0.0, cache_ttl_seconds):
                response = copy.deepcopy(cached[1])
                response["cached"] = True
                return response

        if wanted:
            meta = self.get_instance_meta(wanted)
            metas = [meta]
        else:
            metas = self.list_instance_metas()

        managers: dict[str, Any] = {}
        instance_entries: list[dict[str, Any]] = []
        errors: list[str] = []
        for meta in metas:
            current_id = str(meta.get("id") or "")
            instance_dir = self._instance_path(current_id)
            size_bytes, file_count, read_errors = _directory_usage(instance_dir)
            rollup: list[dict[str, Any]] = []
            if instance_dir.is_dir():
                try:
                    children = sorted(instance_dir.iterdir(), key=lambda item: item.name.lower())
                except OSError as exc:
                    children = []
                    read_errors.append(f"无法读取实例目录：{exc}")
                for child in children:
                    if child.is_symlink():
                        continue
                    if child.is_dir():
                        child_bytes, child_files, child_errors = _directory_usage(child)
                    elif child.is_file():
                        child_errors = []
                        try:
                            child_bytes = int(child.stat().st_size)
                            child_files = 1
                        except OSError as exc:
                            child_bytes = 0
                            child_files = 0
                            child_errors = [f"无法读取 {child}：{exc}"]
                    else:
                        continue
                    rollup.append(
                        {
                            "path": child.name,
                            "bytes": child_bytes,
                            "file_count": child_files,
                            "errors": list(dict.fromkeys(child_errors))[:5],
                        }
                    )
                    for item in child_errors:
                        if item not in read_errors:
                            read_errors.append(item)
            for item in read_errors:
                errors.append(f"{current_id}: {item}")
            instance_entries.append(
                {
                    "instance_id": current_id,
                    "id": current_id,
                    "name": str(meta.get("name") or current_id),
                    "directory": str(instance_dir),
                    "data_dir_size_bytes": int(size_bytes),
                    "data_size_bytes": int(size_bytes),
                    "file_count": int(file_count),
                    "rollup": rollup,
                    "errors": list(dict.fromkeys(read_errors))[:20],
                }
            )
            try:
                managers[current_id] = self.get_manager(current_id)
            except Exception as exc:  # noqa: BLE001 - keep other instances readable
                managers[current_id] = None
                errors.append(f"{current_id}: 无法创建实例管理器：{exc}")

        host_memory = _host_memory_info()
        if host_memory.get("error"):
            errors.append(str(host_memory["error"]))

        docker_records = self._docker_stats_records(
            metas,
            {key: value for key, value in managers.items() if value is not None},
        )
        for record in docker_records:
            if record.get("error"):
                label = record.get("container") or record.get("instance_id") or "容器"
                errors.append(f"{label}: {record['error']}")

        global_bytes, global_files, global_read_errors = _directory_usage(self.root)
        for item in global_read_errors:
            if item not in errors:
                errors.append(item)
        global_disk = _disk_usage(self.root)
        if global_disk.get("error"):
            errors.append(str(global_disk["error"]))
        instances_total = sum(int(item.get("data_dir_size_bytes") or 0) for item in instance_entries)

        response: dict[str, Any] = {
            "ok": True,
            "scope": "instance" if wanted else "global",
            "instance_id": wanted or "",
            "generated_at": utc_now(),
            "cached": False,
            "host_memory": host_memory,
            "instances": instance_entries,
            "items": instance_entries,
            "docker": docker_records,
            "global": {
                "data_dir": str(self.root),
                "data_dir_size_bytes": int(global_bytes),
                "file_count": int(global_files),
                "instances_total_bytes": int(instances_total),
                "disk": global_disk,
            },
            "errors": list(dict.fromkeys(errors)),
            "error": "",
            "message": "资源统计已更新。",
        }
        if wanted:
            response["instance"] = instance_entries[0]
            response["data_dir_size_bytes"] = int(instance_entries[0]["data_dir_size_bytes"])
            response["file_count"] = int(instance_entries[0]["file_count"])
        with self._lock:
            self._stats_cache[cache_key] = (now, copy.deepcopy(response))
        return response

    # ------------------------------------------------------------------
    # Optional AI assistant B extension
    # ------------------------------------------------------------------
    def assistant_status(self) -> dict[str, Any]:
        """Return assistant B status without reading its config or API key."""

        return self.assistant_extension.status()

    def install_assistant(self) -> dict[str, Any]:
        """Clone or update the optional assistant B extension when asked."""

        return self.assistant_extension.install()

    def update_assistant(self) -> dict[str, Any]:
        """Fast-forward the optional assistant B extension when possible."""

        return self.assistant_extension.update()

    def assistant_config(self) -> dict[str, Any]:
        """Return safe assistant B config/runtime state without its key."""

        return self.assistant_extension.get_config()

    def save_assistant_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist assistant B config without echoing any API key."""

        return self.assistant_extension.save_config(payload)

    def start_assistant(self) -> dict[str, Any]:
        """Start assistant B through the Python entrypoint with logs/PID."""

        return self.assistant_extension.start()

    def stop_assistant(self) -> dict[str, Any]:
        """Stop the PID tracked assistant B process if safe."""

        return self.assistant_extension.stop()

    def has_active_deployments(self) -> bool:
        """Return True when any cached instance is currently deploying."""

        with self._lock:
            for manager in self._manager_cache.values():
                if getattr(manager, "_active_job_id", ""):
                    return True
        return False

    def latest_instance_id(self) -> str:
        metas = self.list_instance_metas()
        if not metas:
            raise ValueError("还没有机器人实例，请先运行 ./start.sh")
        return str(metas[-1]["id"])
