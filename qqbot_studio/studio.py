"""Studio-wide settings, multi-instance catalog, and mirror-node management."""

from __future__ import annotations

import concurrent.futures
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

from .configgen import choose_ports
from .dockerctl import DockerController, DockerError
from .store import Store, chmod_best_effort, default_data_dir, utc_now

STUDIO_SCHEMA_VERSION = 1
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
        self._mirror_cache: dict[str, dict[str, Any]] = {}
        self._manager_cache: dict[str, Any] = {}
        self._lock = __import__("threading").RLock()

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
        ports = choose_ports(
            host=str(settings.get("bind_host") or DEFAULT_BIND_HOST),
            reserved=reserved,
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
            raise ValueError(f"实例不存在：{instance_id}") from exc
        except (OSError, ValueError) as exc:
            raise ValueError(f"实例配置损坏：{instance_id}：{exc}") from exc
        if not isinstance(meta, dict) or meta.get("id") != instance_id:
            raise ValueError(f"实例配置无效：{instance_id}")
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
