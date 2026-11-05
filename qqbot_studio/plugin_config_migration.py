"""Read-only foundation for the qqbot_enhancer field-map migration.

This module intentionally does **not** move or write plugin configuration.  It
provides a deterministic field-map reader and a preview object which can be used
by later releases to migrate the large ``qqbot_enhancer`` schema one field at a
time.

Hard guarantees:

* preview is read-only;
* the old/source configuration is never modified;
* existing target values always win (a conflict is reported, never overwritten);
* source keys not present in the field map are retained under ``unknown_fields``;
* unknown field-map metadata, target-only keys, and nested values are preserved
  for inspection rather than discarded.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from .plugin_market import (
        CONTROL_RE,
        PLUGIN_ID_RE,
        PluginMarketError,
        _is_sensitive_key,
        _iter_redacted,
    )
except ImportError:  # pragma: no cover - direct script import fallback
    from qqbot_studio.plugin_market import (  # type: ignore
        CONTROL_RE,
        PLUGIN_ID_RE,
        PluginMarketError,
        _is_sensitive_key,
    )

FIELD_MAP_SCHEMA_VERSION = 1
MAX_FIELD_MAP_BYTES = 2 * 1024 * 1024
MAX_FIELD_MAP_FIELDS = 5000


class FieldMapError(PluginMarketError):
    code = "invalid_field_map"


def _json_copy(value: Any) -> Any:
    """Return a JSON-safe deep copy without accepting custom objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    raise FieldMapError("field-map 与配置必须是 JSON 兼容类型。")


def _read_json(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink():
        raise FieldMapError("拒绝读取符号链接形式的 field-map。")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FieldMapError("无法读取 field-map 文件。", detail=exc.__class__.__name__) from exc
    if size > MAX_FIELD_MAP_BYTES:
        raise FieldMapError("field-map 文件超过大小限制。")
    try:
        raw = path.read_text(encoding="utf-8-sig")
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise FieldMapError("field-map 不是合法 JSON。", detail=exc.__class__.__name__) from exc
    if not isinstance(value, dict):
        raise FieldMapError("field-map 顶层必须是 JSON object。")
    return value


def _normalise_target_key(value: Any, field_name: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise FieldMapError(f"field-map 条目缺少 {field_name}。")
    if len(key) > 256 or CONTROL_RE.search(key):
        raise FieldMapError(f"field-map 条目的 {field_name} 不合法。")
    return key


def _normalise_filter(value: Any) -> str:
    text = str(value or "copy").strip().lower()
    if text not in {"copy", "string", "bool", "int", "float"}:
        raise FieldMapError("field-map 只允许 copy/string/bool/int/float 这些声明式转换；不会执行任意代码。")
    return text


def _entry_from_mapping(
    source_key: str,
    raw: Mapping[str, Any],
    *,
    index: int,
    default_target_plugin: str = "",
) -> dict[str, Any]:
    source = str(
        source_key
        or raw.get("source")
        or raw.get("source_key")
        or raw.get("from")
        or raw.get("old")
        or raw.get("legacy_key")
        or ""
    ).strip()
    if not source:
        raise FieldMapError("field-map 条目缺少 source_key。")
    if len(source) > 256 or CONTROL_RE.search(source):
        raise FieldMapError("field-map 条目的 source_key 不合法。")

    target_plugin = str(
        raw.get("target_plugin")
        or raw.get("plugin")
        or raw.get("plugin_id")
        or raw.get("target")
        or default_target_plugin
        or ""
    ).strip()
    if not PLUGIN_ID_RE.fullmatch(target_plugin):
        raise FieldMapError("field-map 条目的 target_plugin 不合法。")
    target_key = _normalise_target_key(
        raw.get("target_key")
        or raw.get("key")
        or raw.get("to")
        or raw.get("new")
        or raw.get("new_key"),
        "target_key",
    )
    transform = _normalise_filter(raw.get("transform") or raw.get("convert") or "copy")
    known = {
        "source", "source_key", "from", "old", "legacy_key",
        "target_plugin", "plugin", "plugin_id", "target",
        "target_key", "key", "to", "new", "new_key",
        "transform", "convert", "required", "description", "sensitive",
    }
    metadata = {
        str(key): _json_copy(value)
        for key, value in raw.items()
        if str(key) not in known
    }
    return {
        "index": index,
        "source_key": source,
        "target_plugin": target_plugin,
        "target_key": target_key,
        "transform": transform,
        "required": bool(raw.get("required", False)),
        "description": str(raw.get("description") or ""),
        "sensitive": bool(raw.get("sensitive")) or _is_sensitive_key(source) or _is_sensitive_key(target_key),
        "metadata": metadata,
    }


def _normalise_field_map(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FieldMapError("field-map 必须是 JSON object。")
    version_raw = raw.get("schema_version", FIELD_MAP_SCHEMA_VERSION)
    try:
        version = int(version_raw)
    except (TypeError, ValueError) as exc:
        raise FieldMapError("field-map 的 schema_version 不合法。") from exc
    if version > FIELD_MAP_SCHEMA_VERSION:
        raise FieldMapError("field-map 来自更高版本，已拒绝读取。")
    default_target_plugin = str(
        raw.get("target_plugin") or raw.get("default_plugin") or ""
    ).strip()
    if default_target_plugin and not PLUGIN_ID_RE.fullmatch(default_target_plugin):
        raise FieldMapError("field-map 的默认 target_plugin 不合法。")
    entries_raw = (
        raw.get("fields")
        if raw.get("fields") is not None
        else raw.get("field_map")
        if raw.get("field_map") is not None
        else raw.get("mapping")
        if raw.get("mapping") is not None
        else raw.get("map")
    )
    if entries_raw is None:
        # Tolerate a compact mapping form while preserving top-level metadata.
        reserved = {
            "schema_version", "version", "description", "metadata", "unknown_fields",
            "extensions", "fields", "field_map", "mapping", "map",
            "target_plugin", "default_plugin",
        }
        entries_raw = {key: value for key, value in raw.items() if str(key) not in reserved}
    if isinstance(entries_raw, Mapping):
        iterable: Iterable[tuple[str, Any]] = [(str(key), value) for key, value in entries_raw.items()]
    elif isinstance(entries_raw, list):
        iterable = [("", value) for value in entries_raw]
    else:
        raise FieldMapError("field-map 的 fields 必须是数组或 object。")

    entries: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for index, (source_key, item) in enumerate(iterable):
        if isinstance(item, str):
            if not default_target_plugin:
                raise FieldMapError("field-map 的字符串简写需要顶层 target_plugin/default_plugin。")
            item = {"target_plugin": default_target_plugin, "target_key": item}
        if not isinstance(item, Mapping):
            raise FieldMapError("field-map 的每个条目必须是 JSON object 或字符串简写。")
        entry = _entry_from_mapping(
            source_key,
            item,
            index=index,
            default_target_plugin=default_target_plugin,
        )
        if entry["source_key"] in seen_sources:
            raise FieldMapError("field-map 包含重复的 source_key。")
        seen_sources.add(entry["source_key"])
        entries.append(entry)
        if len(entries) > MAX_FIELD_MAP_FIELDS:
            raise FieldMapError("field-map 字段数量超过限制。")
    known_top = {
        "schema_version", "version", "description", "metadata", "unknown_fields",
        "extensions", "fields", "field_map", "mapping", "map",
        "target_plugin", "default_plugin",
    }
    extensions = {
        str(key): _json_copy(value)
        for key, value in raw.items()
        if str(key) not in known_top
    }
    metadata = raw.get("metadata")
    if metadata is None:
        metadata = {}
    elif not isinstance(metadata, Mapping):
        raise FieldMapError("field-map 的 metadata 必须是 JSON object。")
    return {
        "schema_version": FIELD_MAP_SCHEMA_VERSION,
        "source_schema_version": version,
        "description": str(raw.get("description") or ""),
        "metadata": _json_copy(metadata),
        "extensions": extensions,
        "unknown_fields": _json_copy(raw.get("unknown_fields") or {}),
        "fields": entries,
    }


def load_field_map(
    source: str | Path | Mapping[str, Any],
    *,
    max_bytes: int = MAX_FIELD_MAP_BYTES,
) -> dict[str, Any]:
    """Read, validate, and normalise a field-map.

    The returned structure retains unknown top-level keys in ``extensions`` and
    every entry's unknown metadata in ``metadata``.
    """

    if isinstance(source, Mapping):
        return _normalise_field_map(source)
    path = Path(source).expanduser()
    if max_bytes < 1:
        raise FieldMapError("max_bytes 必须为正数。")
    if path.is_symlink():
        raise FieldMapError("拒绝符号链接形式的 field-map。")
    try:
        if path.stat().st_size > max_bytes:
            raise FieldMapError("field-map 文件超过大小限制。")
    except OSError as exc:
        raise FieldMapError("无法读取 field-map 文件。", detail=exc.__class__.__name__) from exc
    return _normalise_field_map(_read_json(path))


read_field_map = load_field_map
validate_field_map = load_field_map


def _redact_scalar(key: str, value: Any, redact_sensitive: bool) -> Any:
    if redact_sensitive and _is_sensitive_key(key):
        return "[已脱敏]"
    return _json_copy(value)


def _declared_transform(value: Any, transform: str) -> Any:
    """Preview the declared conversion without mutating or guessing."""

    if transform == "copy":
        return value
    if transform == "string":
        if isinstance(value, (dict, list, tuple)):
            return None
        return str(value)
    if transform == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        return None
    if transform == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            return int(value.strip())
        return None
    if transform == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None
    raise FieldMapError("field-map 包含不支持的安全转换。")


def preview_config_migration(
    field_map: str | Path | Mapping[str, Any],
    source_config: Mapping[str, Any],
    *,
    existing_configs: Mapping[str, Mapping[str, Any]] | None = None,
    target_configs: Mapping[str, Mapping[str, Any]] | None = None,
    redact_sensitive: bool = True,
) -> dict[str, Any]:
    """Build a read-only migration preview.

    ``existing_configs`` (new name) and ``target_configs`` (backward-compatible
    alias) both mean "configuration already present in destination plugins".
    Existing values are never overwritten: a conflict is emitted in the plan and
    the existing value is retained in ``merged_targets``.

    No filesystem write or move is performed by this function.
    """

    if not isinstance(source_config, Mapping):
        raise FieldMapError("source_config 必须是 JSON object。")
    mapping = load_field_map(field_map)
    existing_raw = existing_configs if existing_configs is not None else target_configs
    if existing_raw is None:
        existing_raw = {}
    if not isinstance(existing_raw, Mapping):
        raise FieldMapError("existing_configs/target_configs 必须是 JSON object。")
    existing: dict[str, dict[str, Any]] = {}
    for plugin_id, config in existing_raw.items():
        if not PLUGIN_ID_RE.fullmatch(str(plugin_id)):
            raise FieldMapError("existing_configs 包含非法插件 ID。")
        if not isinstance(config, Mapping):
            raise FieldMapError("existing_configs 中的每个配置必须是 JSON object。")
        existing[str(plugin_id)] = {str(key): _json_copy(value) for key, value in config.items()}

    source = {str(key): _json_copy(value) for key, value in source_config.items()}
    mapped_sources: set[str] = set()
    moves: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    merged_targets = copy.deepcopy(existing)
    unknown_source: dict[str, Any] = {}

    for entry in mapping["fields"]:
        source_key = str(entry["source_key"])
        target_plugin = str(entry["target_plugin"])
        target_key = str(entry["target_key"])
        if source_key in source:
            mapped_sources.add(source_key)
            raw_value = source[source_key]
            preview_value = _declared_transform(raw_value, str(entry["transform"]))
            conflict = False
            current_value: Any = None
            has_current = False
            target_values = merged_targets.setdefault(target_plugin, {})
            if target_key in target_values:
                has_current = True
                current_value = target_values[target_key]
                conflict = current_value != preview_value
            status = "conflict" if conflict else ("same" if has_current else "new")
            effective_value = preview_value if preview_value is not None else raw_value
            if redact_sensitive:
                public_value = "[已脱敏]" if _is_sensitive_key(source_key) else _iter_redacted(effective_value)
                public_current = (
                    ("[已脱敏]" if _is_sensitive_key(target_key) else _iter_redacted(current_value))
                    if has_current else None
                )
            else:
                public_value = _json_copy(effective_value)
                public_current = _json_copy(current_value) if has_current else None
            move = {
                "source_key": source_key,
                "target_plugin": target_plugin,
                "target_key": target_key,
                "status": status,
                "would_write": not conflict and not has_current,
                "conflict": conflict,
                "value": public_value,
                "current_value": public_current,
                "description": str(entry.get("description") or ""),
            }
            if preview_value is None and str(entry["transform"]) != "copy":
                move["status"] = "invalid_value"
                move["would_write"] = False
                constraints.append({
                    "source_key": source_key,
                    "target_plugin": target_plugin,
                    "target_key": target_key,
                    "problem": "值不符合 field-map 声明的声明式转换。",
                })
            elif not conflict and not has_current:
                target_values[target_key] = preview_value
            moves.append(move)
        elif entry["required"]:
            constraints.append({
                "source_key": source_key,
                "target_plugin": target_plugin,
                "target_key": target_key,
                "problem": "field-map 标记为 required，但源配置缺少该字段。",
            })

    for key, value in source.items():
        if key not in mapped_sources:
            if redact_sensitive:
                unknown_source[key] = "[已脱敏]" if _is_sensitive_key(key) else _iter_redacted(value)
            else:
                unknown_source[key] = _json_copy(value)

    # Preserve target-only keys verbatim in the dry-run view.  This is the key
    # guarantee that a later migration can never drop unknown fields.
    preserved_target_keys: dict[str, list[str]] = {}
    for plugin_id, config in existing.items():
        mapped_target_keys = {
            str(entry["target_key"])
            for entry in mapping["fields"]
            if str(entry["target_plugin"]) == plugin_id
        }
        preserved_target_keys[plugin_id] = sorted(key for key in config if key not in mapped_target_keys)

    public_merged = _json_copy(merged_targets)
    if redact_sensitive:
        public_merged = {
            plugin_id: _iter_redacted(config)
            for plugin_id, config in public_merged.items()
        }
    return {
        "ok": True,
        "dry_run": True,
        "writes_config": False,
        "writes_files": False,
        "schema_version": FIELD_MAP_SCHEMA_VERSION,
        "source_schema_version": mapping["source_schema_version"],
        "moves": moves,
        "constraints": constraints,
        "conflicts": [dict(move) for move in moves if move["conflict"]],
        "unknown_fields": unknown_source,
        "preserved_target_keys": preserved_target_keys,
        "merged_targets": public_merged,
        "field_map_unknown_fields": copy.deepcopy(mapping["unknown_fields"]),
        "field_map_extensions": copy.deepcopy(mapping["extensions"]),
        "metadata": copy.deepcopy(mapping["metadata"]),
        "message": "只读迁移预览；没有写入旧配置或新插件配置。",
        "error": "",
    }


preview_field_map = preview_config_migration


__all__ = [
    "FIELD_MAP_SCHEMA_VERSION",
    "FieldMapError",
    "load_field_map",
    "preview_config_migration",
    "preview_field_map",
    "read_field_map",
    "validate_field_map",
]
