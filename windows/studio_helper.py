#!/usr/bin/env python3
"""Small JSON bridge between the Windows PowerShell scripts and the core.

The management server itself is launched by PowerShell (``Start-Process``).
This helper only performs operations that already live in ``qqbot_studio``:
persisting network settings, creating an instance, and repairing existing
containers.  Keeping stdout JSON-only makes PowerShell consumption reliable;
names and paths are ASCII-escaped so Windows console code pages cannot corrupt
them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qqbot_studio.dockerctl import DockerController, DockerError  # noqa: E402
from qqbot_studio.studio import StudioManager, detect_public_host  # noqa: E402


def emit(payload: dict[str, Any]) -> None:
    # ensure_ascii keeps stdout portable across Windows PowerShell 5.1 code
    # pages.  ConvertFrom-Json reconstructs the original Unicode text.
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def manager_for(args: argparse.Namespace) -> StudioManager:
    return StudioManager(Path(args.data_dir))


def require_docker(manager: StudioManager) -> None:
    DockerController(manager.root).require_ready()


def settings_payload(manager: StudioManager) -> dict[str, Any]:
    settings = manager.load_settings()
    return {
        "ok": True,
        "data_dir": str(manager.root),
        "settings_path": str(manager.settings_path),
        "settings_exists": manager.settings_path.is_file(),
        "first_run_complete": bool(settings.get("first_run_complete")),
        "bind_host": str(settings.get("bind_host") or "0.0.0.0"),
        "bind_port": int(settings.get("bind_port") or 8765),
        "public_host": str(settings.get("public_host") or ""),
        "resolved_public_host": manager.public_host(),
        "public_base_url": manager.public_base_url(),
        "instance_count": len(manager.list_instance_metas()),
    }


def command_detect_public_host(_: argparse.Namespace) -> int:
    emit({"ok": True, "public_host": detect_public_host()})
    return 0


def command_get_settings(args: argparse.Namespace) -> int:
    emit(settings_payload(manager_for(args)))
    return 0


def command_configure(args: argparse.Namespace) -> int:
    manager = manager_for(args)
    existing = manager.load_settings()
    bind_host = args.bind_host or str(existing.get("bind_host") or "0.0.0.0")
    bind_port = int(args.bind_port)
    public_host = (
        args.public_host
        if args.public_host is not None
        else str(existing.get("public_host") or "")
    )
    if not public_host.strip():
        public_host = detect_public_host()
    manager.update_network(
        bind_host=bind_host,
        bind_port=bind_port,
        public_host=public_host,
    )
    emit(settings_payload(manager))
    return 0


def _repair(manager: StudioManager) -> list[dict[str, Any]]:
    require_docker(manager)
    return manager.repair_instances()


def command_create_instance(args: argparse.Namespace) -> int:
    manager = manager_for(args)
    repairs = _repair(manager)
    meta = manager.create_instance(args.name or None)
    base = manager.public_base_url()
    emit(
        {
            "ok": True,
            "instance": {
                "id": str(meta["id"]),
                "name": str(meta.get("name") or meta["id"]),
            },
            "wizard_url": f"{base}/wizard?instance={meta['id']}",
            "console_url": f"{base}/console",
            "repairs": repairs,
            "settings": settings_payload(manager),
        }
    )
    return 0


def command_prepare_console(args: argparse.Namespace) -> int:
    manager = manager_for(args)
    repairs = _repair(manager)
    settings = settings_payload(manager)
    emit(
        {
            "ok": True,
            "console_url": manager.public_base_url() + "/console",
            "repairs": repairs,
            "settings": settings,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QQ Bot Studio Windows helper")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect-public-host", help="print a best-effort LAN IP")
    detect.set_defaults(func=command_detect_public_host)

    get_settings = sub.add_parser("get-settings", help="print persisted settings as JSON")
    get_settings.add_argument("--data-dir", required=True)
    get_settings.set_defaults(func=command_get_settings)

    configure = sub.add_parser("configure", help="persist first-run network settings")
    configure.add_argument("--data-dir", required=True)
    configure.add_argument("--bind-host", required=True)
    configure.add_argument("--bind-port", required=True, type=int)
    configure.add_argument("--public-host", default=None)
    configure.set_defaults(func=command_configure)

    create = sub.add_parser("create-instance", help="repair existing containers and create one instance")
    create.add_argument("--data-dir", required=True)
    create.add_argument("--name", default=None)
    create.set_defaults(func=command_create_instance)

    prepare = sub.add_parser("prepare-console", help="check Docker and repair known instances")
    prepare.add_argument("--data-dir", required=True)
    prepare.set_defaults(func=command_prepare_console)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (DockerError, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
