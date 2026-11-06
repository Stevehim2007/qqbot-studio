"""Safe final migration from qqbot_enhancer/core_full to the seven plugins.

This module moves no databases, sessions, memory or plugin_data.  It only:

1. reads the legacy enhancer/core-full plugin config;
2. maps explicitly known keys into the final seven plugin config files;
3. fills target schema defaults for missing keys;
4. backs up old/target config bytes before any write;
5. optionally moves retired plugin *code* directories to a timestamped backup
   directory so AstrBot can no longer load them.

The old config files and every runtime data directory are preserved.  A failed
write is rolled back to the pre-migration bytes by the caller-visible backup
manifest.  The operation is deterministic and uses no network.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

FINAL_PLUGIN_IDS: tuple[str, ...] = (
    "qqbot_core_reply",
    "qqbot_safe_tools",
    "qqbot_web_reader",
    "qqbot_image_search",
    "qqbot_food",
    "qqbot_stickers",
    "qqbot_memory_commands",
)
LEGACY_PLUGIN_IDS: tuple[str, ...] = ("qqbot_enhancer", "qqbot_core_full")
MAX_CONFIG_BYTES = 2 * 1024 * 1024
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# source key -> (target plugin, target key)
EXPLICIT_FIELD_MAP: dict[str, tuple[str, str]] = {
    # Memory commands
    "memory_commands_enabled": ("qqbot_memory_commands", "enabled"),
    "memory_commands_destructive_enabled": ("qqbot_memory_commands", "destructive_enabled"),
    "memory_commands_allow_user_ids": ("qqbot_memory_commands", "allow_user_ids"),
    "memory_commands_max_results": ("qqbot_memory_commands", "max_results"),
    "memory_commands_content_chars": ("qqbot_memory_commands", "content_chars"),
    "memory_commands_max_reply_chars": ("qqbot_memory_commands", "max_reply_chars"),
    # Safe tools canonical names
    "safe_tools_enabled": ("qqbot_safe_tools", "enabled"),
    "safe_tools_marker_enabled": ("qqbot_safe_tools", "marker_enabled"),
    "safe_tools_max_actions_per_reply": ("qqbot_safe_tools", "max_actions"),
    "safe_tools_search_max_count": ("qqbot_safe_tools", "max_search_count"),
    "safe_tools_collect_cooldown_seconds": ("qqbot_safe_tools", "collect_sticker_cooldown_seconds"),
    "safe_tools_allow_search_images": ("qqbot_safe_tools", "allow_search_images"),
    "safe_tools_allow_send_sticker": ("qqbot_safe_tools", "allow_send_sticker"),
    "safe_tools_allow_collect_sticker": ("qqbot_safe_tools", "allow_collect_sticker"),
    "safe_tools_allow_list_categories": ("qqbot_safe_tools", "allow_list_categories"),
    "safe_tools_allow_fetch_webpage": ("qqbot_safe_tools", "allow_fetch_webpage"),
    "safe_tools_allow_submit_food_review": ("qqbot_safe_tools", "allow_submit_food_review"),
    "safe_tools_allow_search_food_restaurants": (
        "qqbot_safe_tools",
        "allow_search_food_restaurants",
    ),
    # Web reader owns the fetch transport limits
    "web_fetch_timeout_seconds": ("qqbot_web_reader", "timeout_seconds"),
    "web_fetch_max_response_bytes": ("qqbot_web_reader", "max_bytes"),
    "web_fetch_max_chars": ("qqbot_web_reader", "max_chars"),
    "web_fetch_max_redirects": ("qqbot_web_reader", "max_redirects"),
    # Web/image food cooldowns live in safe-tools for marker calls
    "web_fetch_cooldown_seconds": ("qqbot_safe_tools", "web_fetch_cooldown_seconds"),
    "food_search_cooldown_seconds": ("qqbot_safe_tools", "food_search_cooldown_seconds"),
    "food_submit_cooldown_seconds": ("qqbot_safe_tools", "food_submit_cooldown_seconds"),
    "image_search_group_cooldown_seconds": ("qqbot_safe_tools", "image_search_cooldown_seconds"),
    "image_search_user_cooldown_seconds": ("qqbot_safe_tools", "image_search_cooldown_seconds"),
    "sticker_cooldown_seconds": ("qqbot_safe_tools", "sticker_cooldown_seconds"),
    # Final ownership/defaults
    "legacy_core_reply_enabled": ("qqbot_core_reply", "enabled"),
}


@dataclass
class MigrationPlan:
    source_path: Path
    source_config: dict[str, Any]
    schemas: dict[str, dict[str, Any]]
    targets: dict[str, dict[str, Any]]
    mapped: dict[str, dict[str, str]] = field(default_factory=dict)
    unmapped_source_keys: list[str] = field(default_factory=list)
    conflicts: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "targets": {
                plugin_id: {
                    "keys": sorted(values),
                    "mapped_from": {
                        key: source for key, source in sorted(
                            self.mapped.get(plugin_id, {}).items()
                        )
                    },
                }
                for plugin_id, values in self.targets.items()
            },
            "unmapped_source_keys": sorted(self.unmapped_source_keys),
            "conflicts": {key: sorted(value) for key, value in self.conflicts.items()},
            "warnings": list(self.warnings),
            "preserved": [
                "legacy config files",
                "plugin_data",
                "AstrBot databases",
                "sessions",
                "long-term memory",
            ],
        }


def _read_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("拒绝读取符号链接形式的插件配置。")
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise ValueError("插件配置文件过大。")
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"无法读取插件配置：{path.name}") from exc
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"插件配置不是合法 JSON：{path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"插件配置顶层必须是 JSON object：{path.name}")
    return value


def _iter_schema_defaults(schema: Mapping[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for key, raw in schema.items():
        if not isinstance(key, str) or not isinstance(raw, Mapping):
            continue
        if "default" in raw:
            defaults[key] = copy.deepcopy(raw["default"])
    return defaults


def _coerce_by_schema(value: Any, raw_schema: Mapping[str, Any]) -> tuple[bool, Any]:
    kind = str(raw_schema.get("type") or "").strip().lower()
    if kind in {"bool", "boolean"}:
        if isinstance(value, bool):
            return True, value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return True, bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "on"}:
                return True, True
            if text in {"false", "0", "no", "off"}:
                return True, False
        return False, None
    if kind in {"int", "integer"}:
        if isinstance(value, bool):
            return True, int(value)
        if isinstance(value, int):
            number = value
        elif isinstance(value, float) and value.is_integer():
            number = int(value)
        elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            number = int(value.strip())
        else:
            return False, None
        slider = raw_schema.get("slider")
        if isinstance(slider, Mapping):
            low = slider.get("min")
            high = slider.get("max")
            if isinstance(low, (int, float)):
                number = max(int(low), number)
            if isinstance(high, (int, float)):
                number = min(int(high), number)
        return True, number
    if kind in {"float", "number"}:
        if isinstance(value, bool):
            value = int(value)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, None
        slider = raw_schema.get("slider")
        if isinstance(slider, Mapping):
            low = slider.get("min")
            high = slider.get("max")
            if isinstance(low, (int, float)):
                number = max(float(low), number)
            if isinstance(high, (int, float)):
                number = min(float(high), number)
        return True, number
    if kind in {"string", "str", "text"}:
        if isinstance(value, (dict, list, tuple)):
            return False, None
        text = str(value)
        if CONTROL_RE.search(text):
            return False, None
        options = raw_schema.get("options")
        if isinstance(options, list) and options and text not in options:
            return False, None
        return True, text
    # Unknown schema types are copied only when JSON-safe.
    if value is None or isinstance(value, (str, int, float, bool)):
        return True, value
    if isinstance(value, (list, dict)):
        return True, copy.deepcopy(value)
    return False, None


def find_legacy_config(astrbot_data: Path) -> Path | None:
    config_dir = Path(astrbot_data) / "config"
    for plugin_id in LEGACY_PLUGIN_IDS:
        candidate = config_dir / f"{plugin_id}_config.json"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def load_schemas(plugin_root: Path) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for plugin_id in FINAL_PLUGIN_IDS:
        path = Path(plugin_root) / plugin_id / "_conf_schema.json"
        if path.is_symlink():
            raise ValueError(f"插件 {plugin_id} 的 schema 不能是符号链接。")
        try:
            raw = path.read_text(encoding="utf-8-sig")
            value = json.loads(raw)
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError(f"无法读取插件 {plugin_id} 的 schema。") from exc
        if not isinstance(value, dict) or not value:
            raise ValueError(f"插件 {plugin_id} 的 schema 必须是非空 JSON object。")
        schemas[plugin_id] = {
            str(key): dict(item)
            for key, item in value.items()
            if isinstance(key, str) and isinstance(item, Mapping)
        }
    return schemas


def _existing_target_configs(config_dir: Path) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for plugin_id in FINAL_PLUGIN_IDS:
        path = config_dir / f"{plugin_id}_config.json"
        if path.is_file() and not path.is_symlink():
            try:
                payload[plugin_id] = _read_json_object(path)
            except ValueError:
                payload[plugin_id] = {}
    return payload


def build_plan(
    astrbot_data: Path,
    plugin_root: Path,
    *,
    source_path: Path | None = None,
) -> MigrationPlan:
    astrbot_data = Path(astrbot_data)
    config_dir = astrbot_data / "config"
    source = source_path or find_legacy_config(astrbot_data)
    if source is None:
        raise ValueError("没有找到 qqbot_enhancer/qqbot_core_full 旧配置。")
    source_config = _read_json_object(source)
    schemas = load_schemas(plugin_root)
    existing = _existing_target_configs(config_dir)
    defaults: dict[str, dict[str, Any]] = {
        plugin_id: _iter_schema_defaults(schema) for plugin_id, schema in schemas.items()
    }
    targets: dict[str, dict[str, Any]] = {
        plugin_id: copy.deepcopy(defaults[plugin_id]) for plugin_id in FINAL_PLUGIN_IDS
    }
    mapped: dict[str, dict[str, str]] = {plugin_id: {} for plugin_id in FINAL_PLUGIN_IDS}
    unmapped: list[str] = []
    warnings: list[str] = []

    exact_targets: dict[str, list[str]] = {}
    for plugin_id, schema in schemas.items():
        for key in schema:
            exact_targets.setdefault(key, []).append(plugin_id)

    for source_key, raw_value in source_config.items():
        destinations: list[tuple[str, str]] = []
        explicit = EXPLICIT_FIELD_MAP.get(source_key)
        if explicit is not None:
            destinations = [explicit]
        elif source_key in exact_targets:
            destinations = [(plugin_id, source_key) for plugin_id in exact_targets[source_key]]
        if not destinations:
            unmapped.append(source_key)
            continue
        any_mapped = False
        for plugin_id, target_key in destinations:
            if plugin_id not in schemas or target_key not in schemas[plugin_id]:
                continue
            ok, coerced = _coerce_by_schema(raw_value, schemas[plugin_id][target_key])
            if not ok:
                warnings.append(
                    f"{source_key} -> {plugin_id}.{target_key} 类型/选项不兼容，使用默认值"
                )
                continue
            targets[plugin_id][target_key] = coerced
            mapped[plugin_id][target_key] = source_key
            any_mapped = True
        if not any_mapped and not explicit:
            unmapped.append(source_key)

    # Existing non-default target config values always win unless absent.
    conflicts: dict[str, list[str]] = {}
    for plugin_id, existing_values in existing.items():
        plugin_defaults = defaults.get(plugin_id, {})
        for key, value in existing_values.items():
            if key not in schemas.get(plugin_id, {}):
                warnings.append(f"现有 {plugin_id} 配置包含 schema 外字段 {key}，未迁移")
                continue
            # A target value that is still the schema default is not considered
            # user intent; migration may improve it.  Any non-default existing
            # value wins and is reported as a conflict.
            if key in plugin_defaults and value == plugin_defaults[key]:
                continue
            if key in targets[plugin_id] and targets[plugin_id][key] != value:
                conflicts.setdefault(plugin_id, []).append(key)
                targets[plugin_id][key] = copy.deepcopy(value)

    # Final safety cutover: the seven final packages must be enabled, and the
    # former legacy_main gate must never disable the new owner.
    if "enabled" in schemas["qqbot_core_reply"]:
        targets["qqbot_core_reply"]["enabled"] = True
    if "enabled" in schemas["qqbot_safe_tools"]:
        targets["qqbot_safe_tools"]["enabled"] = True
    if "enabled" in schemas["qqbot_web_reader"]:
        targets["qqbot_web_reader"]["enabled"] = True
    if "enabled" in schemas["qqbot_image_search"]:
        targets["qqbot_image_search"]["enabled"] = True
    for plugin_id in FINAL_PLUGIN_IDS:
        if plugin_id in targets:
            targets[plugin_id].pop("legacy_core_reply_enabled", None)
    return MigrationPlan(
        source_path=source,
        source_config=source_config,
        schemas=schemas,
        targets=targets,
        mapped=mapped,
        unmapped_source_keys=unmapped,
        conflicts=conflicts,
        warnings=warnings,
    )


def _atomic_write_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    payload = json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            path.chmod(mode)
        except OSError:
            pass
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def apply_plan(plan: MigrationPlan, astrbot_data: Path) -> dict[str, Any]:
    astrbot_data = Path(astrbot_data)
    config_dir = astrbot_data / "config"
    backup_dir = astrbot_data / "config" / f".plugin-final-migration-backup-{int(time.time())}-{secrets.token_hex(3)}"
    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    config_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    candidates = [plan.source_path]
    candidates.extend(config_dir / f"{plugin_id}_config.json" for plugin_id in FINAL_PLUGIN_IDS)
    for source in candidates:
        if not source.exists() or source.is_symlink():
            continue
        target = backup_dir / source.name
        shutil.copy2(source, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        files.append(source.name)
    try:
        for plugin_id, values in plan.targets.items():
            _atomic_write_json(config_dir / f"{plugin_id}_config.json", values)
        report_path = backup_dir / "migration-report.json"
        _atomic_write_json(report_path, plan.report(), mode=0o600)
    except Exception:
        # Best-effort rollback of config files from the pre-write backup.
        for name in files:
            backup = backup_dir / name
            if backup.is_file():
                shutil.copy2(backup, config_dir / name)
        raise
    return {
        "backup_dir": str(backup_dir),
        "backed_up_files": sorted(files),
        "written_plugins": sorted(plan.targets),
        "report": plan.report(),
    }


def retire_legacy_plugin_code(
    instance_root: Path,
    *,
    plugin_root_name: str = "data/astrbot/plugins",
) -> dict[str, Any]:
    """Move retired plugin code directories to a preserved backup folder."""

    instance_root = Path(instance_root)
    plugin_dir = instance_root / plugin_root_name
    if not plugin_dir.is_dir():
        raise ValueError("AstrBot 插件目录不存在。")
    backup_root = instance_root / "studio-plugins" / "retired-plugin-code"
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = f"{int(time.time())}-{secrets.token_hex(3)}"
    destination = backup_root / stamp
    destination.mkdir(mode=0o700)
    moved: list[dict[str, str]] = []
    for plugin_id in LEGACY_PLUGIN_IDS:
        source = plugin_dir / plugin_id
        if not source.exists():
            continue
        if source.is_symlink():
            raise ValueError(f"拒绝移动符号链接插件目录：{plugin_id}")
        target = destination / plugin_id
        shutil.move(str(source), str(target))
        moved.append({"id": plugin_id, "from": str(source), "to": str(target)})
    return {"backup_dir": str(destination), "moved": moved}
