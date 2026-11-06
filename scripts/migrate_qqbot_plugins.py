#!/usr/bin/env python3
"""Migrate legacy qqbot_enhancer/core_full config to the final seven plugins.

Default is a read-only dry-run.  --apply writes only plugin config files after
taking byte-for-byte backups.  --retire moves legacy plugin *code* directories
into studio-plugins/retired-plugin-code/<timestamp>/; config, plugin_data,
databases, sessions and memory are never deleted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qqbot_studio.plugin_final_migration import (  # noqa: E402
    apply_plan,
    build_plan,
    retire_legacy_plugin_code,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True, help="QQ Bot Studio instance dir, e.g. .../instances/bot-4")
    parser.add_argument("--plugins-dir", default="", help="AstrBot plugin dir; default <instance>/data/astrbot/plugins")
    parser.add_argument("--source", default="", help="optional explicit old config JSON path")
    parser.add_argument("--apply", action="store_true", help="write migrated target configs (default dry-run)")
    parser.add_argument("--retire", action="store_true", help="after --apply, move qqbot_enhancer/core_full code out of plugin load path")
    args = parser.parse_args(argv)

    instance = Path(args.instance).expanduser().resolve()
    astrbot_data = instance / "data" / "astrbot"
    plugins_dir = Path(args.plugins_dir).expanduser().resolve() if args.plugins_dir else astrbot_data / "plugins"
    if not astrbot_data.is_dir():
        print(f"error: AstrBot data dir not found: {astrbot_data}", file=sys.stderr)
        return 2
    if not plugins_dir.is_dir():
        print(f"error: final plugin dir not found: {plugins_dir}", file=sys.stderr)
        return 2
    source = Path(args.source).expanduser().resolve() if args.source else None
    try:
        plan = build_plan(astrbot_data, plugins_dir, source_path=source)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = plan.report()
    if not args.apply:
        print(json.dumps({"mode": "dry-run", **report}, ensure_ascii=False, indent=2))
        return 0
    try:
        result = apply_plan(plan, astrbot_data)
    except Exception as exc:  # noqa: BLE001 - user-facing migration failure
        print(f"error: migration failed and config rollback was attempted: {type(exc).__name__}", file=sys.stderr)
        return 1
    output = {"mode": "apply", **result}
    if args.retire:
        try:
            output["retired_code"] = retire_legacy_plugin_code(instance)
        except ValueError as exc:
            print(f"error: config migrated but code retirement failed: {exc}", file=sys.stderr)
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 3
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
