#!/usr/bin/env python3
"""Generate the S0 offline inventory from real source trees.

Usage from the main project root:

    python3 migrations/modular-v2-v3/tools/generate_inventory.py \
        --staging-root /path/to/staging-snapshot \
        --staging-commit 673820405f0d24c1b932ccc7ca6a7223f3aad37f \
        --release-root /path/to/.qqbot-plugins-staging

The checked-in JSON files and snapshots are the deliverables.  Running the
generator never imports AstrBot or any plugin module.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import inventory_lib as inv  # noqa: E402

MIGRATION_DIR = inv.MIGRATION_DIR
SNAPSHOT_DIR = inv.SNAPSHOT_DIR
FINAL = inv.FINAL_PLUGIN_IDS
LEGACY = inv.LEGACY_PLUGIN_IDS
ENHANCER_ROOT = inv.ROOT / "plugins" / "qqbot_enhancer"
SCHEMA_PATH = ENHANCER_ROOT / "_conf_schema.json"

# --- AstrBot target -------------------------------------------------------
ASTRBOT_TARGET = {
    "version": "4.29.3",
    "commit": "f61a7f4",
    "compatibility_range": ">=4.29,<5",
    "runtime_verification": "not_performed_s0_offline",
    "source": "integrations/astrbot-memory/README.md (pinned upstream)",
}

# --- command operation mapping -------------------------------------------
LEGACY_COMMAND_OPERATIONS: dict[str, str] = {
    "找图": "image.search",
    "唱歌": "core_reply.legacy_song_play",
    "food": "food.query",
    "会话列表": "session.list",
    "会话": "session.switch",
    "新会话": "session.new",
    "删除会话": "session.delete",
    "option": "core_reply.reply_status",
    "记忆": "memory.command",
    "收藏表情": "sticker.collect",
    "表情状态": "sticker.status",
    "表情分类": "sticker.category.list",
    "表情列表": "sticker.list",
    "发表情": "sticker.send",
}

MINIMAL_COMMAND_OPERATIONS: dict[tuple[str, str], str] = {
    ("qqbot_core_reply", "会话列表"): "session.list",
    ("qqbot_core_reply", "会话"): "session.switch",
    ("qqbot_core_reply", "新会话"): "session.new",
    ("qqbot_core_reply", "删除会话"): "session.delete",
    ("qqbot_core_reply", "option"): "core_reply.reply_status",
    ("qqbot_core_reply", "唱歌"): "core_reply.legacy_song_play",
    ("qqbot_food", "food"): "food.query",
    ("qqbot_image_search", "图片搜索"): "image.search",
    ("qqbot_memory_commands", "记忆"): "memory.command",
    ("qqbot_safe_tools", "工具协议"): "tool.catalog",
    ("qqbot_stickers", "收藏表情"): "sticker.collect",
    ("qqbot_stickers", "表情状态"): "sticker.status",
    ("qqbot_stickers", "表情分类"): "sticker.category.list",
    ("qqbot_stickers", "表情列表"): "sticker.list",
    ("qqbot_stickers", "发表情"): "sticker.send",
    ("qqbot_web_reader", "网页"): "web.read",
}

COMMAND_PERMISSIONS: dict[str, str] = {
    "memory.command": "authorized_actor: AstrBot admin / group owner/admin / allow_user_ids; destructive verbs additionally require destructive_enabled; scope-bound",
    "session.delete": "authenticated_actor; group conversation only; AstrBot ConversationManager delete API required",
    "session.list": "authenticated_actor; group conversation only",
    "session.switch": "authenticated_actor; group conversation only",
    "session.new": "authenticated_actor; group conversation only",
    "sticker.collect": "authenticated_actor; sticker store writable; explicit collect command",
    "sticker.send": "authenticated_actor; collected sticker store readable",
    "sticker.status": "authenticated_actor; diagnostic read",
    "sticker.category.list": "authenticated_actor; read-only list",
    "sticker.list": "authenticated_actor; read-only list",
    "food.query": "authenticated_actor; read-only",
    "image.search": "authenticated_actor; read-only network search",
    "web.read": "authenticated_actor; public network only; SSRF policy enforced",
    "tool.catalog": "authenticated_actor; read-only diagnostics",
    "core_reply.reply_status": "authenticated_actor; read-only plugin usage",
    "core_reply.legacy_song_play": "authenticated_actor; local song directory read",
}

# Contract §5 canonical operations and the source implementation status.
CONTRACT_OPERATIONS: dict[str, dict[str, Any]] = {
    "web.read": {"canonical": "/网页读取 URL", "aliases": ["/网页", "/web", "/读网页"], "plugin": "qqbot_web_reader", "implemented": ["/网页", "/读网页"]},
    "image.search": {"canonical": "/找图 QUERY", "aliases": ["/搜图", "/image", "/图片搜索", "/搜图片", "/找图"], "plugin": "qqbot_image_search", "implemented": ["/图片搜索", "/搜图片", "/找图", "/搜图"]},
    "food.query": {"canonical": "/food 查询", "aliases": ["/美食为根别名", "/吃什么", "/饭店"], "plugin": "qqbot_food", "implemented": ["/food", "/吃什么", "/饭店"]},
    "sticker.collect": {"canonical": "/表情 收藏", "aliases": ["/贴纸", "/stickers", "/收藏表情"], "plugin": "qqbot_stickers", "implemented": ["/收藏表情"]},
    "tool.catalog": {"canonical": "/工具 状态", "aliases": ["/tools", "/工具协议"], "plugin": "qqbot_safe_tools", "implemented": ["/工具协议"]},
    "memory.command": {"canonical": "/记忆 查看|搜索|添加|预览清空|确认|取消", "aliases": ["/memory", "/记忆管理"], "plugin": "qqbot_memory_commands", "implemented": ["/记忆", "/记忆管理"]},
    "session.list": {"canonical": "/会话 状态", "aliases": ["/session", "/会话列表"], "plugin": "qqbot_memory_commands", "implemented": ["/会话列表"]},
    "session.switch": {"canonical": "/会话 切换", "aliases": ["/切换会话"], "plugin": "qqbot_memory_commands", "implemented": ["/会话", "/切换会话"]},
    "session.new": {"canonical": "/会话 新建", "aliases": ["/新会话"], "plugin": "qqbot_memory_commands", "implemented": ["/新会话"]},
    "session.delete": {"canonical": "/会话 重置", "aliases": ["/删除会话", "/会话删除"], "plugin": "qqbot_memory_commands", "implemented": ["/删除会话", "/会话删除"]},
    "core_reply.reply_status": {"canonical": "/回复 状态", "aliases": ["/reply", "/option", "/插件用法", "/用法"], "plugin": "qqbot_core_reply", "implemented": ["/option", "/插件用法", "/用法"]},
    "core_reply.legacy_song_play": {"canonical": "(unassigned legacy capability)", "aliases": ["/唱歌", "/点歌"], "plugin": "qqbot_core_reply", "implemented": ["/唱歌", "/点歌"]},
}

# --- data paths -----------------------------------------------------------
LEGACY_CONFIG_KEYS = {
    "qqbot_enhancer": "data/astrbot/config/qqbot_enhancer_config.json",
    "qqbot_core_full": "data/astrbot/config/qqbot_core_full_config.json",
}

FINAL_CONFIG_PATHS = {
    plugin_id: f"data/astrbot/config/{plugin_id}_config.json" for plugin_id in FINAL
}


def _relabel_stats(stats: dict[str, Any], display_path: str) -> dict[str, Any]:
    result = json.loads(json.dumps(stats, ensure_ascii=False))
    result["source_path"] = display_path
    return result


def _package_root(staging_root: Path, plugin_id: str) -> Path:
    return staging_root / "plugins" / plugin_id


def _shape_of(key: str, definition: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(definition.get("type") or "")
    value_shape = "scalar"
    if kind == "list":
        value_shape = "list"
    elif kind == "text" and key in {"prompt_profiles", "group_personas", "food_group_locations"}:
        value_shape = "json_text"
    per_group = key in {
        "food_group_locations",
        "food_group_location_infer_from_name",
        "group_personas",
        "group_persona_enabled",
        "group_persona_max_groups",
        "group_persona_max_prompt_chars",
        "group_persona_max_name_chars",
        "group_identity_context_enabled",
        "group_identity_history_limit",
        "group_identity_history_ttl_seconds",
        "group_identity_message_max_chars",
        "group_identity_nickname_max_chars",
        "group_identity_redact_in_reply",
        "group_identity_agent_prompt_max_chars",
        "conversation_max_per_group",
        "conversation_auto_prune_enabled",
        "conversation_switch_enabled",
        "conversation_title_max_chars",
        "conversation_list_limit",
        "ambient_group_chat_enabled",
        "ambient_group_keywords",
        "ambient_group_keyword_probability",
        "ambient_group_min_interval_seconds",
        "ambient_group_round_interval",
        "ambient_group_cache_limit",
        "ambient_group_cache_ttl_seconds",
        "ambient_group_context_max_chars",
    }
    return {
        "storage": "global_flat_schema_field",
        "value_shape": value_shape,
        "runtime_scope": "per_group_override" if per_group else "instance_global",
        "scope_types": ["global"],
    }


def _unit_of(key: str, definition: Mapping[str, Any]) -> str:
    kind = str(definition.get("type") or "")
    if kind == "bool":
        return "boolean"
    if kind in {"int", "float"}:
        if key.endswith("_seconds") or key == "pending_sticker_ttl_seconds":
            return "seconds"
        if "bytes" in key or key.endswith("_file_bytes") or key.endswith("_response_bytes"):
            return "bytes"
        if "chars" in key or "length" in key:
            return "characters"
        if "probability" in key or "confidence" in key or key.endswith("_score"):
            return "ratio_0_1"
        if "count" in key or "limit" in key or key.startswith("max_") or key.startswith("min_"):
            return "count"
        if key.endswith("_ms"):
            return "milliseconds"
        return "number"
    if kind == "list":
        return "list"
    if kind == "text":
        return "text_template" if "reply" in key or "prefix" in key or "usage" in key or "header" in key else "text"
    if kind == "string":
        if key in {"memory_mode", "vision_provider_id", "prompt_profile_active_id", "image_search_safe_search"}:
            return "enum_or_id"
        return "string"
    return "unknown"


def _aggregate_refs(paths: Iterable[Path], keys: Iterable[str]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {key: [] for key in keys}
    for path in paths:
        refs = inv.function_config_references(path, result.keys())
        for key, lines in refs.items():
            result[key].extend(lines)
    return {key: sorted(set(lines)) for key, lines in result.items()}


def build_source_snapshots(staging_root: Path, staging_commit: str) -> dict[str, Any]:
    packages: dict[str, Any] = {}
    manifest_packages: dict[str, Any] = {}
    roots = {"qqbot_enhancer": ENHANCER_ROOT}
    for plugin_id in ["qqbot_core_full", *FINAL]:
        roots[plugin_id] = _package_root(staging_root, plugin_id)
    for plugin_id, root in roots.items():
        kind = "legacy" if plugin_id in LEGACY else "minimal_v1_v2_v3"
        snapshot = inv.package_snapshot(root, kind=kind)
        if plugin_id in LEGACY:
            display = f"plugins/{plugin_id}" if plugin_id == "qqbot_enhancer" else f".qqbot-plugins-staging/plugins/{plugin_id}@{staging_commit}"
        else:
            display = f".qqbot-plugins-staging/plugins/{plugin_id}@{staging_commit}"
        snapshot["stats"] = _relabel_stats(snapshot["stats"], display)
        snapshot["source_commit"] = staging_commit if plugin_id != "qqbot_enhancer" else inv.git_head(inv.ROOT)
        packages[plugin_id] = snapshot
        manifest_packages[plugin_id] = snapshot["stats"]

    schema_index = {
        plugin_id: snapshot["schema"] for plugin_id, snapshot in packages.items()
    }
    ast_index = {
        "schema_version": 1,
        "generated_by": "migrations/modular-v2-v3/tools/generate_inventory.py",
        "source_roots": {
            "qqbot_enhancer": "worktree plugins/qqbot_enhancer",
            "staging": f".qqbot-plugins-staging@{staging_commit}",
        },
        "packages": packages,
    }
    source_manifest = {
        "schema_version": 1,
        "staging_commit": staging_commit,
        "packages": manifest_packages,
    }
    inv.write_json(SNAPSHOT_DIR / "schema-index.json", schema_index)
    inv.write_json(SNAPSHOT_DIR / "ast-index.json", ast_index)
    inv.write_json(SNAPSHOT_DIR / "source-manifest.json", source_manifest)
    return {"packages": packages, "schema_index": schema_index, "ast_index": ast_index}


def build_baseline(
    snapshots: Mapping[str, Any],
    staging_root: Path,
    staging_commit: str,
    release_root: Path | None,
) -> dict[str, Any]:
    packages = snapshots["packages"]
    schema = inv.read_json(SCHEMA_PATH)
    baseline_packages: dict[str, Any] = {}
    for plugin_id in ["qqbot_enhancer", "qqbot_core_full", *FINAL]:
        snapshot = packages[plugin_id]
        stats = snapshot["stats"]
        entry = {
            "package_id": plugin_id,
            "package_kind": snapshot["package_kind"],
            "source_path": stats["source_path"],
            "source_commit": snapshot.get("source_commit", ""),
            "version": stats["metadata_version"] or stats["version_file"],
            "version_file": stats["version_file"],
            "metadata_version": stats["metadata_version"],
            "file_count": stats["file_count"],
            "text_file_count": stats["text_file_count"],
            "python_file_count": stats["python_file_count"],
            "text_line_count": stats["text_line_count"],
            "python_line_count": stats["python_line_count"],
            "total_bytes": stats["total_bytes"],
            "tree_sha256": stats["tree_sha256"],
            "main_py": stats["main_py"],
            "conf_schema": stats["conf_schema"],
            "source_manifest_sha256": hashlib.sha256(
                json.dumps(stats, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }
        baseline_packages[plugin_id] = entry

    release: dict[str, Any] | None = None
    if release_root is not None and (release_root / "catalog.json").is_file():
        catalog = inv.read_json(release_root / "catalog.json")
        manifest = inv.read_json(release_root / "manifest.json") if (release_root / "manifest.json").is_file() else {}
        records: dict[str, Any] = {}
        for item in catalog.get("plugins", []):
            plugin_id = str(item.get("id", ""))
            artifact_name = str(item.get("download", ""))
            artifact = release_root / "artifacts" / artifact_name
            record = {
                "id": plugin_id,
                "version": item.get("version"),
                "source_version": baseline_packages.get(plugin_id, {}).get("version"),
                "version_matches_source": str(item.get("version")) == str(baseline_packages.get(plugin_id, {}).get("version")),
                "sha256": item.get("sha256"),
                "size": item.get("size"),
                "artifact": f"artifacts/{artifact_name}",
                "artifact_present_in_release_root": artifact.is_file(),
                "artifact_sha256_on_disk": inv.sha256_file(artifact) if artifact.is_file() else "",
                "download": next(
                    (entry.get("download") for entry in manifest.get("plugins", []) if entry.get("id") == plugin_id),
                    item.get("download"),
                ),
                "dependencies": item.get("dependencies", []),
            }
            records[plugin_id] = record
        release = {
            "catalog_schema_version": catalog.get("schema_version"),
            "catalog_id": catalog.get("catalog_id"),
            "catalog_state": "working-tree release metadata" if staging_root != release_root else "release-root",
            "manifest_state": "working-tree release metadata",
            "packages": records,
            "retained_rollback_artifacts": sorted(
                path.name for path in (release_root / "artifacts").glob("*.zip")
                if path.name not in {f"{plugin}-{record.get('version')}.zip" for plugin, record in records.items()}
            ),
        }

    old_schema_264 = None
    old_schema_sha = None
    try:
        import subprocess

        old_text = subprocess.check_output(
            ["git", "show", "bb8d391^:plugins/qqbot_enhancer/_conf_schema.json"],
            cwd=str(inv.ROOT),
            text=True,
        )
        old_schema = json.loads(old_text)
        old_schema_264 = len(old_schema)
        old_schema_sha = hashlib.sha256(old_text.encode("utf-8")).hexdigest()
    except Exception:
        pass

    schema_reconciliation = {
        "contract_claimed_count": 264,
        "actual_schema_count": len(schema),
        "actual_defaults_count": len(inv_json_config_defaults()),
        "difference": len(schema) - 264,
        "contract_count_scope": "business schema before the v3 legacy cutover gate",
        "actual_count_scope": "current schema including migration cutover control",
        "added_after_contract": [
            {
                "key": "legacy_core_reply_enabled",
                "commit": "bb8d391c686a4507435a2dd2d96e58348547ee24",
                "commit_subject": "feat(core): add legacy main-reply cutover gate",
                "date": "2026-11-05T22:02:18+08:00",
                "purpose": "v3 cutover gate; does not represent a legacy user-facing business capability",
                "schema_lines_added": 6,
                "config_defaults_lines_added": 1,
                "main_py_gate_lines_added": 6,
            }
        ],
        "verification": {
            "pre_cutover_schema_count": old_schema_264,
            "pre_cutover_schema_sha256": old_schema_sha,
            "current_schema_count": len(schema),
            "current_schema_sha256": inv.sha256_file(SCHEMA_PATH),
            "defaults_equal_schema_keys": set(inv_json_config_defaults()) == set(schema),
            "command": "git show bb8d391^:plugins/qqbot_enhancer/_conf_schema.json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'",
        },
        "conclusion": "field-map.json covers the 265 actual current keys one-to-one; 264 is the frozen business-schema count before bb8d391, not a contradictory count and not a reason to drop legacy_core_reply_enabled.",
    }

    registration_conflicts = _registration_conflicts(snapshots)
    schema_entries = len(schema)
    return {
        "schema_version": 1,
        "generated_by": "migrations/modular-v2-v3/tools/generate_inventory.py",
        "offline_only": True,
        "never_loaded_as_plugin": True,
        "baseline": {
            "studio_release": "4.3.0",
            "branch": "feat/final-s0",
            "base_commit": "6885ae3",
            "astrbot_target": ASTRBOT_TARGET,
            "source_commit_staging": staging_commit,
        },
        "legacy_packages": {
            plugin_id: baseline_packages[plugin_id] for plugin_id in LEGACY
        },
        "minimal_packages": {
            plugin_id: baseline_packages[plugin_id] for plugin_id in FINAL
        },
        "release": release,
        "schema_count_reconciliation": schema_reconciliation,
        "counts": {
            "legacy_package_count": len(LEGACY),
            "minimal_package_count": len(FINAL),
            "schema_entries": schema_entries,
            "schema_entries_contract_business": 264,
            "schema_entries_migration_control": 1,
        },
        "known_registration_conflicts": registration_conflicts,
        "notes": [
            "qqbot_core_full is a byte-identical copy of qqbot_enhancer main.py/config.py/_conf_schema.json at this baseline; only metadata.yaml, VERSION and README differ.",
            "Loading qqbot_enhancer and qqbot_core_full together registers duplicate reply, marker, command and writer entrypoints.",
            "The staging source snapshot is commit 673820405f0d24c1b932ccc7ca6a7223f3aad37f; catalog/manifest and release ZIPs were captured from the staging working tree and are recorded separately.",
            "AstrBot 4.29.3 runtime behaviour was not executed in S0; runtime_verification is deliberately not_performed_s0_offline.",
        ],
    }


def inv_json_config_defaults() -> Mapping[str, Any]:
    """Load the pure stdlib config defaults without importing AstrBot.

    ``config.py`` has no third-party imports and no plugin side effects; the
    import is safe and resolves module constants such as
    ``COLLECTED_CATEGORY_MAX_CHARS`` that a bare ``ast.literal_eval`` cannot.
    """

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_s0_enhancer_config_defaults", ENHANCER_ROOT / "config.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return dict(module.DEFAULTS)


def _base_package_for_command(package: str) -> str:
    return package


def _command_operation(package: str, command: str) -> str:
    if package in LEGACY:
        return LEGACY_COMMAND_OPERATIONS.get(command, "unmapped.legacy")
    return MINIMAL_COMMAND_OPERATIONS.get((package, command), "unmapped.minimal")


def _registration_conflicts(snapshots: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_spelling: dict[str, list[dict[str, Any]]] = {}
    for package, snapshot in snapshots["packages"].items():
        for handler in snapshot["decorated_handlers"]:
            if handler["kind"] != "command":
                continue
            spellings = [handler["command"], *handler["aliases"]]
            for spelling in spellings:
                by_spelling.setdefault(str(spelling), []).append({
                    "package": package,
                    "symbol": handler["symbol"],
                    "line": handler["symbol_line"],
                })
    conflicts = []
    for spelling, records in sorted(by_spelling.items()):
        if len(records) > 1:
            conflicts.append({
                "spelling": spelling,
                "registrations": records,
                "consequence": "duplicate filter.command registration when packages are loaded together",
            })
    return conflicts



# --- field-map ------------------------------------------------------------
IMAGE_SEARCH_TARGETS: dict[str, str] = {
    "image_search_enabled": "enabled",
    "image_search_natural_language_enabled": "natural_language_enabled",
    "image_search_timeout_seconds": "timeout_seconds",
    "image_search_result_count": "max_results",
    "image_search_safe_search": "safe_search",
    "image_search_group_cooldown_seconds": "group_cooldown_seconds",
    "image_search_user_cooldown_seconds": "user_cooldown_seconds",
    "image_search_max_response_bytes": "max_response_bytes",
    "image_search_max_keyword_chars": "max_keyword_chars",
    "image_search_success_prefix": "success_prefix",
    "image_search_usage_reply": "usage_reply",
    "image_search_disabled_reply": "disabled_reply",
    "image_search_empty_reply": "empty_reply",
    "image_search_error_reply": "error_reply",
    "image_search_timeout_reply": "timeout_reply",
    "image_search_cooldown_reply": "cooldown_reply",
    "image_search_second_source_enabled": "baidu_fallback_enabled",
    "image_search_download_timeout_seconds": "download_timeout_seconds",
    "image_search_download_max_bytes": "download_max_bytes",
    "image_search_download_attempts": "download_attempts",
    "image_search_relevance_enabled": "relevance_enabled",
    "image_search_min_relevance_score": "min_relevance_score",
}

SAFE_TOOLS_TARGETS: dict[str, str] = {
    "safe_tools_enabled": "enabled",
    "safe_tools_marker_enabled": "marker_enabled",
    "safe_tools_max_actions_per_reply": "max_actions",
    "safe_tools_search_max_count": "max_search_count",
    "safe_tools_max_categories": "tool_catalog.max_categories",
    "safe_tools_allow_search_images": "allow_search_images",
    "safe_tools_allow_send_sticker": "allow_send_sticker",
    "safe_tools_allow_collect_sticker": "allow_collect_sticker",
    "safe_tools_allow_list_categories": "allow_list_categories",
    "safe_tools_allow_fetch_webpage": "allow_fetch_webpage",
    "safe_tools_sticker_prefix": "safe_tools_sticker_prefix",
    "safe_tools_collect_cooldown_seconds": "collect_sticker_cooldown_seconds",
    "safe_tools_allow_submit_food_review": "allow_submit_food_review",
    "safe_tools_allow_search_food_restaurants": "allow_search_food_restaurants",
}

WEB_FETCH_TARGETS: dict[str, tuple[str, str]] = {
    "web_fetch_timeout_seconds": ("qqbot_web_reader", "timeout_seconds"),
    "web_fetch_max_response_bytes": ("qqbot_web_reader", "max_bytes"),
    "web_fetch_max_chars": ("qqbot_web_reader", "max_chars"),
    "web_fetch_max_redirects": ("qqbot_web_reader", "max_redirects"),
    "web_fetch_cooldown_seconds": ("qqbot_safe_tools", "web_fetch_cooldown_seconds"),
}

MEMORY_TARGETS: dict[str, str] = {
    "memory_commands_enabled": "enabled",
    "memory_commands_destructive_enabled": "destructive_enabled",
    "memory_commands_allow_user_ids": "allow_user_ids",
    "memory_commands_max_results": "max_results",
    "memory_commands_content_chars": "content_chars",
    "memory_commands_max_reply_chars": "max_reply_chars",
}

CONVERSATION_TARGETS: dict[str, str] = {
    "conversation_switch_enabled": "session.switch_enabled",
    "conversation_title_max_chars": "session.title_max_chars",
    "conversation_list_limit": "session.list_limit",
    "conversation_max_per_group": "session.max_per_group",
    "conversation_auto_prune_enabled": "session.auto_prune_enabled",
}

CORE_REPLY_TARGETS: dict[str, str] = {
    "legacy_core_reply_enabled": "enabled",
    "user_sticker_reaction_attach_sticker": "participation.user_sticker_reaction_attach_sticker",
}
for _key in (
    "user_sticker_reaction_enabled",
    "user_sticker_reaction_probability",
    "user_sticker_reaction_replies",
):
    CORE_REPLY_TARGETS[_key] = _key

STICKER_TARGETS: dict[str, str] = {
    "collected_sticker_use_probability": "sticker_decorate_probability",
}

SONG_KEYS = {
    "song_enabled",
    "song_group_cooldown_seconds",
    "song_user_cooldown_seconds",
    "song_max_file_bytes",
    "song_success_prefix",
    "song_disabled_reply",
    "song_empty_reply",
    "song_not_found_reply",
    "song_too_large_reply",
    "song_error_reply",
    "song_cooldown_reply",
}

EXPECTED_ARCHIVE_ONLY_KEYS = {
    # Source review found zero function-level references in qqbot_enhancer.
    "ambient_group_reply_probability",
    "conversation_auto_create_enabled",
    "food_submit_disabled_reply",
    "sticker_ai_auto_collect_require_image",
}

TEST_CASE_BY_PREFIX: list[tuple[str, str]] = [
    ("food_submit_", "food.submit.confirm_once"),
    ("food_nl_", "food.nl.decide"),
    ("food_search_", "food.query.tags_locations"),
    ("food_", "food.query.basic"),
    ("image_search_", "image.search.basic"),
    ("web_auto_fetch_", "core.reply.web_auto_fetch"),
    ("web_fetch_", "web.read.public"),
    ("safe_tools_", "safe.marker.single_parse"),
    ("ai_unified_tool_protocol_enabled", "safe.marker.single_parse"),
    ("memory_commands_", "memory.scope.matrix"),
    ("conversation_", "memory.session.switch"),
    ("session.", "memory.session.switch"),
    ("song_", "core.song.play"),
    ("user_sticker_reaction_", "core.reply.image_reaction"),
    ("sticker_ai_auto_collect", "stickers.auto_collect"),
    ("sticker_ai_select", "stickers.ai_select"),
    ("sticker_random_fallback_enabled", "stickers.random_fallback"),
    ("sticker_natural_language", "stickers.natural_language"),
    ("sticker_", "stickers.send.once"),
    ("collected_sticker_", "stickers.collect.idempotent"),
    ("auto_collect_sticker", "stickers.auto_collect"),
    ("auto_collect_user_sticker_enabled", "stickers.auto_collect"),
    ("pending_sticker_ttl_seconds", "stickers.collect.idempotent"),
    ("group_persona", "core.reply.persona_identity"),
    ("group_identity", "core.reply.persona_identity"),
    ("prompt_profile", "core.reply.prompt_profiles"),
    ("ambient_group_", "core.reply.ambient"),
    ("option_", "core.reply.option"),
    ("legacy_core_reply_enabled", "core.handoff.cutover_gate"),
    ("memory_mode", "core.reply.memory_mode"),
]


def _test_case_for(key: str) -> str:
    for prefix, case in TEST_CASE_BY_PREFIX:
        if key.startswith(prefix):
            return case
    return "core.reply.at_private_probability"


def _resolve_field_target(key: str) -> tuple[str, str, str]:
    """Return (target_plugin, target_path, transform_semantics)."""

    if key in EXPECTED_ARCHIVE_ONLY_KEYS:
        return "retain_archive", f"archive.qqbot_enhancer.{key}", "no target write; preserve source value only in archive"
    if key.startswith("food_"):
        return "qqbot_food", key, "direct copy; same flat key in qqbot_food schema"
    if key in IMAGE_SEARCH_TARGETS:
        return "qqbot_image_search", IMAGE_SEARCH_TARGETS[key], "rename legacy prefix to qqbot_image_search canonical key; value unchanged"
    if key in SAFE_TOOLS_TARGETS:
        if key == "safe_tools_sticker_prefix":
            return "qqbot_stickers", SAFE_TOOLS_TARGETS[key], "presentation text owned by the sticker sender"
        return "qqbot_safe_tools", SAFE_TOOLS_TARGETS[key], "rename legacy safe_tools_* key to qqbot_safe_tools canonical key"
    if key in WEB_FETCH_TARGETS:
        plugin, target = WEB_FETCH_TARGETS[key]
        return plugin, target, "transport limit maps to web_reader; cooldown maps to safe_tools policy"
    if key.startswith("web_auto_fetch_"):
        suffix = key[len("web_auto_fetch_"):]
        return "qqbot_core_reply", f"context.web_auto_fetch.{suffix}", "legacy auto web context becomes a core_reply prompt-context policy backed by web_reader"
    if key in MEMORY_TARGETS:
        return "qqbot_memory_commands", MEMORY_TARGETS[key], "strip legacy memory_commands_ prefix; value unchanged"
    if key in CONVERSATION_TARGETS:
        return "qqbot_memory_commands", CONVERSATION_TARGETS[key], "session ownership moves from legacy/core_reply to memory_commands provider contract"
    if key in CORE_REPLY_TARGETS:
        return "qqbot_core_reply", CORE_REPLY_TARGETS[key], "legacy gate maps to core enabled; reaction fields stay in core reply owner"
    if key in SONG_KEYS:
        return "qqbot_core_reply", key, "value copy only; owner confirmation required before final cleanup"
    if key == "ai_unified_tool_protocol_enabled":
        return "qqbot_safe_tools", "marker_enabled", "legacy unified-tool gate collapses into the single safe_tools marker owner"
    if key in STICKER_TARGETS:
        return "qqbot_stickers", STICKER_TARGETS[key], "collected-sticker attachment probability maps to sticker_decorate_probability"
    if key.startswith((
        "sticker_",
        "collected_sticker_",
        "auto_collect_sticker_",
        "auto_collect_user_sticker_enabled",
        "pending_sticker_ttl_seconds",
    )):
        return "qqbot_stickers", key, "direct copy; same flat key in qqbot_stickers schema"
    # Core reply / persona / ambient / option / vision / llm / participation.
    return "qqbot_core_reply", key, "direct copy; same flat key in qqbot_core_reply schema"


def _duplicate_target_schemas(target_plugin: str, target_key: str, schemas: Mapping[str, Any]) -> list[dict[str, str]]:
    duplicates: list[dict[str, str]] = []
    if "." in target_key or target_plugin == "retain_archive":
        return duplicates
    for plugin_id, entries in schemas.items():
        if plugin_id == target_plugin:
            continue
        keys = {item["key"] for item in entries}
        if target_key in keys:
            duplicates.append({"plugin": plugin_id, "key": target_key})
    return sorted(duplicates, key=lambda item: item["plugin"])


def build_field_map(snapshots: Mapping[str, Any]) -> dict[str, Any]:
    enhancer = snapshots["packages"]["qqbot_enhancer"]
    schema_entries = enhancer["schema"]
    keys = [item["key"] for item in schema_entries]
    defaults = inv_json_config_defaults()
    if set(keys) != set(defaults):
        raise RuntimeError("schema keys and config.py DEFAULTS keys diverged")
    old_refs = enhancer["runtime_config_references"]
    observed_archive = {key for key in keys if not old_refs.get(key)}
    if observed_archive != EXPECTED_ARCHIVE_ONLY_KEYS:
        raise RuntimeError(
            "archive-only key set changed; review before regenerating: "
            f"observed={sorted(observed_archive)} expected={sorted(EXPECTED_ARCHIVE_ONLY_KEYS)}"
        )
    target_schemas = {
        plugin_id: snapshots["packages"][plugin_id]["schema"] for plugin_id in FINAL
    }
    target_schema_keys = {
        plugin_id: {item["key"] for item in entries}
        for plugin_id, entries in target_schemas.items()
    }
    target_refs = {
        plugin_id: snapshots["packages"][plugin_id]["runtime_config_references"]
        for plugin_id in FINAL
    }

    fields: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for index, definition in enumerate(schema_entries, start=1):
        key = definition["key"]
        source_default = defaults.get(key)
        target_plugin, target_path, semantics = _resolve_field_target(key)
        target_key_exists = (
            target_plugin in target_schema_keys
            and target_path in target_schema_keys[target_plugin]
        )
        target_locations = target_refs.get(target_plugin, {}).get(target_path, []) if target_key_exists else []
        active_behavior = bool(old_refs.get(key))
        owner_confirmation = key in SONG_KEYS
        if target_plugin == "retain_archive":
            action = "retain_archive"
            blocking = False
            status = "archive_only"
            reason = ""
        else:
            action = "copy" if target_path == key else "transform"
            if not target_key_exists:
                blocking = True
                reason = (
                    f"active legacy field has no key at current target path "
                    f"{target_plugin}.{target_path}; contract path is planned only"
                )
            elif not target_locations:
                blocking = True
                reason = (
                    f"target schema declares {target_plugin}.{target_path} but no runtime "
                    "function in that package references it"
                )
            elif owner_confirmation:
                blocking = True
                reason = (
                    "active legacy capability has no confirmed owner in the final "
                    "seven-plugin contract (§18.1.7); qqbot_core_reply currently ships "
                    "the code but the contract does not assign songs"
                )
            else:
                blocking = False
                reason = ""
            status = "blocked" if blocking else "ready"
        target_definition = None
        if target_key_exists:
            target_definition = next(
                item for item in target_schemas[target_plugin] if item["key"] == target_path
            )
        source_type = definition.get("type")
        target_type = target_definition.get("type") if target_definition else None
        transform = "copy"
        if target_type and source_type and target_type != source_type:
            if target_type in {"bool", "int", "float", "string", "text"}:
                transform = "string" if target_type in {"string", "text"} else target_type
            else:
                transform = "copy"
        entry: dict[str, Any] = {
            "index": index,
            "source_key": key,
            "source_line": definition.get("source_line", 0),
            "source_type": source_type,
            "source_default": source_default,
            "source_description": definition.get("description"),
            "unit": _unit_of(key, definition),
            "scope_shape": _shape_of(key, definition),
            "target_plugin": target_plugin,
            "target_key": target_path,
            "target_type": target_type,
            "target_key_exists_in_current_schema": target_key_exists,
            "transform": transform,
            "transform_semantics": semantics,
            "action": action,
            "rollback_reader": (
                "qqbot_enhancer ConfigReader reads the archived flat key; no target write"
                if action == "retain_archive"
                else "archived qqbot_enhancer_config.json remains the rollback source; target config is a separate file"
            ),
            "test_case": _test_case_for(key),
            "status": status,
            "active_behavior": active_behavior,
            "blocking": blocking,
            "blocking_reason": reason,
            "owner_confirmation_required": owner_confirmation,
            "legacy_reference_locations": old_refs.get(key, []),
            "target_reference_locations": target_locations,
            "duplicate_target_schemas": _duplicate_target_schemas(target_plugin, target_path, target_schemas),
            "required": False,
            "description": definition.get("description") or "",
            "sensitive": False,
        }
        fields.append(entry)
        if blocking:
            blockers.append({
                "id": f"BLOCK-FIELD-{len(blockers) + 1:03d}",
                "source_key": key,
                "target_plugin": target_plugin,
                "target_key": target_path,
                "active_behavior": active_behavior,
                "status": "blocked",
                "reason": reason,
            })

    counts_by_plugin: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    counts_by_action: dict[str, int] = {}
    for entry in fields:
        counts_by_plugin[entry["target_plugin"]] = counts_by_plugin.get(entry["target_plugin"], 0) + 1
        counts_by_status[entry["status"]] = counts_by_status.get(entry["status"], 0) + 1
        counts_by_action[entry["action"]] = counts_by_action.get(entry["action"], 0) + 1

    shared = []
    by_target: dict[tuple[str, str], list[str]] = {}
    for entry in fields:
        if entry["target_plugin"] == "retain_archive" or "." in entry["target_key"]:
            continue
        by_target.setdefault((entry["target_plugin"], entry["target_key"]), []).append(entry["source_key"])
    for (plugin, target_key), sources in sorted(by_target.items()):
        if len(sources) > 1:
            shared.append({
                "target_plugin": plugin,
                "target_key": target_key,
                "source_keys": sources,
                "authority_note": "multiple legacy fields collapse to one target key; resolve conflicts in preview order and never create two independent target authorities",
            })

    return {
        "schema_version": 1,
        "description": "Offline 265-key field map for qqbot_enhancer -> seven final plugins; never loaded as a plugin.",
        "source_schema": {
            "path": "plugins/qqbot_enhancer/_conf_schema.json",
            "sha256": inv.sha256_file(SCHEMA_PATH),
            "entries": len(schema_entries),
            "defaults_entries": len(defaults),
        },
        "target_schemas": {
            plugin_id: {
                "path": f".qqbot-plugins-staging/plugins/{plugin_id}/_conf_schema.json",
                "entries": len(entries),
                "sha256": snapshots["packages"][plugin_id]["stats"]["conf_schema"]["sha256"],
            }
            for plugin_id, entries in target_schemas.items()
        },
        "scope_model": {
            "old_schema_storage": "flat instance config JSON",
            "target_scopes": ["global", "persona", "conversation", "group", "user"],
            "scope_shape_rule": "scope_shape records where the old flat value lives and whether it is a per-group runtime override; it does not promote a flat value into multiple independent copies",
        },
        "transform_vocabulary": ["copy", "string", "bool", "int", "float", "retain_archive"],
        "counts": {
            "total": len(fields),
            "by_target_plugin": counts_by_plugin,
            "by_status": counts_by_status,
            "by_action": counts_by_action,
            "blocking": len(blockers),
        },
        "shared_authorities": shared,
        "blockers": blockers,
        "fields": fields,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", type=Path, required=True, help="git-archive snapshot of the staging plugin repo")
    parser.add_argument("--staging-commit", required=True)
    parser.add_argument("--release-root", type=Path, default=None, help="staging working tree containing catalog/manifest/artifacts")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    staging_root = args.staging_root.expanduser().resolve()
    release_root = args.release_root.expanduser().resolve() if args.release_root else None
    snapshots = build_source_snapshots(staging_root, args.staging_commit)
    baseline = build_baseline(snapshots, staging_root, args.staging_commit, release_root)
    inv.write_json(MIGRATION_DIR / "baseline.json", baseline)
    field_map = build_field_map(snapshots)
    inv.write_json(MIGRATION_DIR / "field-map.json", field_map)
    entrypoints = build_entrypoints(snapshots, staging_root, args.staging_commit)
    inv.write_json(MIGRATION_DIR / "entrypoints.json", entrypoints)
    command_aliases = build_command_aliases(snapshots, baseline)
    inv.write_json(MIGRATION_DIR / "command-aliases.json", command_aliases)
    print(json.dumps({
        "packages": {
            plugin_id: {
                "version": item["version"],
                "files": item["file_count"],
                "text_lines": item["text_line_count"],
                "schema": item["conf_schema"]["entries"],
                "main_lines": item["main_py"]["lines"],
            }
            for plugin_id, item in {**baseline["legacy_packages"], **baseline["minimal_packages"]}.items()
        },
        "schema_entries": baseline["counts"]["schema_entries"],
        "field_map_counts": field_map["counts"],
        "entrypoint_counts": entrypoints["counts"],
        "command_alias_counts": command_aliases["counts"],
        "registration_conflicts": len(baseline["known_registration_conflicts"]),
    }, ensure_ascii=False, indent=2))
    return 0



# --- entrypoints ----------------------------------------------------------
PACKAGE_CODE = {
    "qqbot_enhancer": "ENH",
    "qqbot_core_full": "CFULL",
    "qqbot_core_reply": "CREPLY",
    "qqbot_safe_tools": "SAFE",
    "qqbot_web_reader": "WEB",
    "qqbot_image_search": "IMG",
    "qqbot_food": "FOOD",
    "qqbot_stickers": "STK",
    "qqbot_memory_commands": "MEM",
    "qqbot_studio": "STUDIO",
}

COMMAND_TARGET_OWNER: dict[str, str] = {
    "image.search": "qqbot_image_search",
    "core_reply.legacy_song_play": "qqbot_core_reply",
    "food.query": "qqbot_food",
    "session.list": "qqbot_memory_commands",
    "session.switch": "qqbot_memory_commands",
    "session.new": "qqbot_memory_commands",
    "session.delete": "qqbot_memory_commands",
    "core_reply.reply_status": "qqbot_core_reply",
    "memory.command": "qqbot_memory_commands",
    "sticker.collect": "qqbot_stickers",
    "sticker.status": "qqbot_stickers",
    "sticker.category.list": "qqbot_stickers",
    "sticker.list": "qqbot_stickers",
    "sticker.send": "qqbot_stickers",
    "web.read": "qqbot_web_reader",
    "tool.catalog": "qqbot_safe_tools",
}

BLOCKED_OPERATIONS: dict[str, str] = {
    "session.list": "blocked_owner_missing",
    "session.switch": "blocked_owner_missing",
    "session.new": "blocked_owner_missing",
    "session.delete": "blocked_owner_missing",
    "core_reply.legacy_song_play": "blocked_owner_unconfirmed",
}

CAPABILITY_TESTS: dict[str, str] = {
    "reply.turn_run": "core.reply.at_private_probability",
    "reply.persona_context": "core.reply.persona_identity",
    "reply.identity_context": "core.reply.persona_identity",
    "reply.memory_context": "core.reply.memory_mode",
    "reply.memory_mode": "core.reply.memory_mode",
    "reply.image_reaction": "core.reply.image_reaction",
    "reply.ambient": "core.reply.ambient",
    "reply.web_auto_fetch": "core.reply.web_auto_fetch",
    "sticker.decorate": "stickers.decorate_once",
    "sticker.natural_language": "stickers.natural_language",
    "sticker.auto_collect": "stickers.auto_collect",
    "sticker.auto_collect.ai": "stickers.auto_collect",
    "sticker.auto_collect.local": "stickers.auto_collect",
    "sticker.categorize": "stickers.natural_language",
    "sticker.collect": "stickers.collect.idempotent",
    "sticker.collect.nl": "stickers.collect.idempotent",
    "sticker.send": "stickers.send.once",
    "sticker.send.nl": "stickers.natural_language",
    "sticker.status": "stickers.status",
    "sticker.category.list": "stickers.category.list",
    "sticker.list": "stickers.list",
    "sticker.migrate_legacy": "stickers.migration.idempotent",
    "sticker.asset_init": "stickers.builtin_assets",
    "image.search": "image.search.basic",
    "food.query": "food.query.basic",
    "food.confirm": "food.submit.confirm_once",
    "food.submit": "food.submit.confirm_once",
    "food.nl_decide": "food.nl.decide",
    "web.read": "web.read.public",
    "memory.command": "memory.scope.matrix",
    "session.list": "memory.session.switch",
    "session.switch": "memory.session.switch",
    "session.new": "memory.session.switch",
    "session.delete": "memory.session.switch",
    "tool.catalog": "safe.marker.single_parse",
    "tool.process_model_output": "safe.marker.single_parse",
    "tool.dispatch": "safe.marker.single_parse",
    "core_reply.reply_status": "core.reply.option",
    "core_reply.legacy_song_play": "core.song.play",
    "lifecycle.food_state_init": "food.submit.confirm_once",
    "lifecycle.terminate": "core.handoff.cutover_gate",
}

STATUS_VOCABULARY = {
    "active_legacy_target_ready": "legacy handler is active and the mapped seven-plugin target currently has an equivalent implementation",
    "active_legacy_target_blocked": "legacy handler is active but the mapped target does not yet implement the capability",
    "active_legacy_clone_target_ready": "qqbot_core_full duplicate handler is active and target-equivalent code exists",
    "active_legacy_clone_target_blocked": "qqbot_core_full duplicate handler is active and target capability is missing",
    "active_legacy_noop": "legacy lifecycle callback exists but has no side effect",
    "active_legacy_management": "Studio/deployer still directly reads, writes, installs or protects the legacy package",
    "implemented_transitional": "seven-plugin transitional handler exists but final ABI/E2E is not proven",
    "blocked_owner_missing": "handler exists in the wrong package or the contract owner does not implement the operation",
    "blocked_owner_unconfirmed": "active legacy capability has no confirmed final owner",
    "blocked_capability_missing": "target package lacks the active legacy capability",
    "blocked_runtime_verification": "requires AstrBot 4.29.3 runtime verification before it can be marked ready",
    "offline_tool_ready": "offline inventory helper; never loaded at runtime",
    "missing_from_branch": "management helper was observed outside this branch and is not part of the committed baseline",
}


def _symbol_line(path: Path, symbol: str) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            return node.lineno
    return 0


def _line_containing(path: Path, needle: str, *, nth: int = 1, start: int = 1, end: int | None = None) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    stop = len(lines) if end is None else min(end, len(lines))
    seen = 0
    for index in range(max(1, start), stop + 1):
        if needle in lines[index - 1]:
            seen += 1
            if seen == nth:
                return index
    return 0


def _display_path(package: str, source_commit: str) -> str:
    if package == "qqbot_enhancer":
        return "plugins/qqbot_enhancer/main.py"
    if package in FINAL or package == "qqbot_core_full":
        return f".qqbot-plugins-staging/plugins/{package}@{source_commit}/main.py"
    return ""


def _entry(
    *,
    package: str,
    source_kind: str,
    file: str,
    symbol: str,
    line: int,
    end_line: int,
    registration: str,
    trigger: Any,
    capability: str,
    enabled_condition: Any,
    new_owner: str,
    disable_method: Any,
    test_case: str,
    status: str,
    **extra: Any,
) -> dict[str, Any]:
    code = PACKAGE_CODE.get(package, "PKG")
    identifier = extra.pop("entry_id", "")
    if not identifier:
        identifier = f"{code}-{symbol}-{line}"
    entry = {
        "entry_id": identifier,
        "package": package,
        "source_kind": source_kind,
        "file": file,
        "symbol": symbol,
        "line": int(line),
        "end_line": int(end_line or line),
        "registration": registration,
        "trigger": trigger,
        "capability": capability,
        "enabled_condition": enabled_condition,
        "new_owner": new_owner,
        "disable_method": disable_method,
        "test_case": test_case,
        "status": status,
    }
    entry.update(extra)
    return entry


def _handler_enabled_condition(
    package: str,
    handler: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    gate: str = "",
) -> dict[str, Any]:
    refs = snapshot.get("function_config_references", {}).get(handler["symbol"], [])
    return {
        "plugin_loaded": True,
        "decorator_registered": handler["kind"] == "command",
        "gate": gate,
        "config_keys_read_by_handler": refs,
        "evaluation": "AST string-literal scan of the handler function body; not a branch truth table",
    }


def _target_status_for_operation(operation: str, *, clone: bool) -> str:
    blocked = BLOCKED_OPERATIONS.get(operation)
    if blocked:
        return "active_legacy_clone_target_blocked" if clone else "active_legacy_target_blocked"
    return "active_legacy_clone_target_ready" if clone else "active_legacy_target_ready"


def _disable_legacy(kind: str) -> dict[str, Any]:
    if kind == "command":
        transition = "remove @filter.command registration at load time"
    elif kind == "event_listener":
        transition = "remove listener registration; do not rely on a function-body if"
    elif kind == "result_hook":
        transition = "remove on_decorating_result registration"
    elif kind == "llm_request_hook":
        transition = "remove on_llm_request registration"
    elif kind == "initialize":
        transition = "remove constructor/initialize side effects and data creation"
    elif kind == "helper":
        transition = "remove helper call sites and route through the final provider"
    else:
        transition = "disable the entry"
    return {
        "transition": transition,
        "final": "do_not_load_package",
        "mechanism": "package absent from the AstrBot plugin load path; no runtime import or fallback",
        "function_body_gate_insufficient": kind in {"command", "event_listener", "result_hook", "llm_request_hook"},
    }


def _minimal_disable(kind: str, *, replace_with: str = "") -> dict[str, Any]:
    if replace_with:
        return {
            "transition": replace_with,
            "final": "owner remains one of the seven plugins; register the operation in the contract owner only",
            "mechanism": "remove duplicate registration from the current package and keep one canonical owner",
            "function_body_gate_insufficient": kind in {"command", "event_listener", "result_hook", "llm_request_hook"},
        }
    return {
        "transition": "retain as the seven-plugin transitional implementation",
        "final": "same package owns the operation after ABI/E2E acceptance",
        "mechanism": "no legacy package loaded; single registration enforced by final manifest",
        "function_body_gate_insufficient": False,
    }


def _handler_entry(package: str, handler: Mapping[str, Any], snapshot: Mapping[str, Any], source_commit: str) -> dict[str, Any]:
    kind = handler["kind"]
    symbol = handler["symbol"]
    file = _display_path(package, source_commit)
    clone = package == "qqbot_core_full"
    if kind == "command":
        operation = _command_operation(package, str(handler.get("command") or ""))
        owner = COMMAND_TARGET_OWNER.get(operation, "unmapped")
        test_case = CAPABILITY_TESTS.get(operation, _test_case_for(str(handler.get("command") or "")))
        if package in LEGACY:
            status = _target_status_for_operation(operation, clone=clone)
            disable = _disable_legacy("command")
        else:
            status = BLOCKED_OPERATIONS.get(operation, "implemented_transitional")
            disable = _minimal_disable(
                "command",
                replace_with=(
                    "move session commands to qqbot_memory_commands"
                    if operation.startswith("session.")
                    else "resolve song ownership before registering /唱歌 or /点歌"
                    if operation == "core_reply.legacy_song_play"
                    else ""
                ),
            )
        trigger = {
            "type": "command",
            "canonical": handler.get("command"),
            "aliases": handler.get("aliases", []),
            "match": "AstrBot filter.command token match",
        }
        registration = " | ".join(item["raw"] for item in handler["decorators"])
        return _entry(
            entry_id=f"CMD-{PACKAGE_CODE.get(package, 'PKG')}-{symbol}",
            package=package,
            source_kind="legacy" if package in LEGACY else "minimal_v1_v2_v3",
            file=file,
            symbol=symbol,
            line=handler["symbol_line"],
            end_line=handler["end_line"],
            registration=registration,
            trigger=trigger,
            capability=operation,
            enabled_condition=_handler_enabled_condition(package, handler, snapshot),
            new_owner=owner,
            disable_method=disable,
            test_case=test_case,
            status=status,
            priority=handler.get("priority"),
            decorators=handler["decorators"],
            aliases=handler.get("aliases", []),
        )

    if kind == "event_listener":
        if package in LEGACY:
            capability = "reply.turn_run"
            owner = "qqbot_core_reply"
            status = "active_legacy_clone_target_ready" if clone else "active_legacy_target_ready"
            disable = _disable_legacy("event_listener")
            gate = "legacy_core_reply_enabled"
        elif package == "qqbot_food":
            capability = "food.confirm"
            owner = "qqbot_food"
            status = "implemented_transitional"
            disable = _minimal_disable("event_listener")
            gate = ""
        elif package == "qqbot_image_search":
            capability = "image.search"
            owner = "qqbot_image_search"
            status = "implemented_transitional"
            disable = _minimal_disable("event_listener")
            gate = ""
        elif package == "qqbot_stickers":
            capability = "sticker.natural_language"
            owner = "qqbot_stickers"
            status = "implemented_transitional"
            disable = _minimal_disable("event_listener")
            gate = ""
        else:
            capability = "reply.turn_run"
            owner = "qqbot_core_reply"
            status = "implemented_transitional"
            disable = _minimal_disable("event_listener")
            gate = ""
        return _entry(
            entry_id=f"EVT-{PACKAGE_CODE.get(package, 'PKG')}-{symbol}",
            package=package,
            source_kind="legacy" if package in LEGACY else "minimal_v1_v2_v3",
            file=file,
            symbol=symbol,
            line=handler["symbol_line"],
            end_line=handler["end_line"],
            registration=" | ".join(item["raw"] for item in handler["decorators"]),
            trigger={
                "type": "event_message",
                "event": handler.get("event"),
                "priority": handler.get("priority"),
                "platform": handler.get("platform_condition"),
            },
            capability=capability,
            enabled_condition=_handler_enabled_condition(package, handler, snapshot, gate=gate),
            new_owner=owner,
            disable_method=disable,
            test_case=CAPABILITY_TESTS.get(capability, "core.reply.at_private_probability"),
            status=status,
            priority=handler.get("priority"),
            decorators=handler["decorators"],
        )

    if kind == "result_hook":
        if package in LEGACY:
            capability = "sticker.decorate"
            owner = "qqbot_stickers"
            status = "active_legacy_clone_target_ready" if clone else "active_legacy_target_ready"
            disable = _disable_legacy("result_hook")
        elif package == "qqbot_safe_tools":
            capability = "tool.process_model_output"
            owner = "qqbot_safe_tools"
            status = "implemented_transitional"
            disable = _minimal_disable("result_hook")
        else:
            capability = "sticker.decorate"
            owner = "qqbot_stickers"
            status = "implemented_transitional"
            disable = _minimal_disable("result_hook")
        return _entry(
            entry_id=f"HOOK-{PACKAGE_CODE.get(package, 'PKG')}-{symbol}",
            package=package,
            source_kind="legacy" if package in LEGACY else "minimal_v1_v2_v3",
            file=file,
            symbol=symbol,
            line=handler["symbol_line"],
            end_line=handler["end_line"],
            registration=" | ".join(item["raw"] for item in handler["decorators"]),
            trigger={"type": "decorating_result", "priority": handler.get("priority")},
            capability=capability,
            enabled_condition=_handler_enabled_condition(package, handler, snapshot),
            new_owner=owner,
            disable_method=disable,
            test_case=CAPABILITY_TESTS.get(capability, "stickers.decorate_once"),
            status=status,
            priority=handler.get("priority"),
            decorators=handler["decorators"],
        )

    # llm_request_hook
    if package in LEGACY:
        capability = "reply.persona_context" if "persona" in symbol else "reply.identity_context"
        owner = "qqbot_core_reply"
        status = "active_legacy_clone_target_ready" if clone else "active_legacy_target_ready"
        disable = _disable_legacy("llm_request_hook")
    else:
        capability = "reply.memory_context"
        owner = "qqbot_core_reply"
        status = "implemented_transitional"
        disable = _minimal_disable("llm_request_hook")
    return _entry(
        entry_id=f"HOOK-{PACKAGE_CODE.get(package, 'PKG')}-{symbol}",
        package=package,
        source_kind="legacy" if package in LEGACY else "minimal_v1_v2_v3",
        file=file,
        symbol=symbol,
        line=handler["symbol_line"],
        end_line=handler["end_line"],
        registration=" | ".join(item["raw"] for item in handler["decorators"]),
        trigger={"type": "llm_request"},
        capability=capability,
        enabled_condition=_handler_enabled_condition(package, handler, snapshot),
        new_owner=owner,
        disable_method=disable,
        test_case=CAPABILITY_TESTS.get(capability, "core.reply.persona_identity"),
        status=status,
    )


LEGACY_INLINE_BRANCHES: list[dict[str, Any]] = [
    {
        "id": "auto-collect-ai",
        "marker": "_maybe_ai_auto_collect_user_sticker",
        "capability": "sticker.auto_collect.ai",
        "owner": "qqbot_stickers",
        "target_ready": False,
        "flags": ["sticker_ai_auto_collect_enabled"],
    },
    {
        "id": "food-nl",
        "marker": "_food_nl_decide",
        "capability": "food.nl_decide",
        "owner": "qqbot_food",
        "target_ready": False,
        "flags": ["food_nl_enabled", "food_nl_probability", "food_nl_image_enabled", "food_nl_image_probability"],
    },
    {
        "id": "ambient",
        "marker": "_ambient_group_should_participate",
        "capability": "reply.ambient",
        "owner": "qqbot_core_reply",
        "target_ready": True,
        "flags": ["ambient_group_chat_enabled", "ambient_group_keywords", "ambient_group_keyword_probability"],
    },
    {
        "id": "sticker-reclassify",
        "marker": "detect_reclassify_request",
        "capability": "sticker.categorize",
        "owner": "qqbot_stickers",
        "target_ready": True,
        "flags": ["sticker_natural_language_reclassify_enabled"],
    },
    {
        "id": "sticker-collect-nl",
        "marker": "_detect_natural_language_collect_intent",
        "capability": "sticker.collect.nl",
        "owner": "qqbot_stickers",
        "target_ready": True,
        "flags": ["auto_collect_user_sticker_enabled"],
    },
    {
        "id": "sticker-send-nl",
        "marker": "_detect_natural_language_sticker_intent",
        "capability": "sticker.send.nl",
        "owner": "qqbot_stickers",
        "target_ready": True,
        "flags": ["sticker_natural_language_enabled"],
    },
    {
        "id": "auto-collect-local",
        "marker": "auto_collect_user_sticker_enabled",
        "capability": "sticker.auto_collect.local",
        "owner": "qqbot_stickers",
        "target_ready": True,
        "flags": ["auto_collect_user_sticker_enabled", "auto_collect_sticker_reply_enabled"],
    },
    {
        "id": "image-reaction",
        "marker": "_pick_user_sticker_reaction_reply",
        "capability": "reply.image_reaction",
        "owner": "qqbot_core_reply",
        "target_ready": True,
        "flags": ["user_sticker_reaction_enabled", "user_sticker_reaction_probability", "user_sticker_reaction_attach_sticker"],
    },
    {
        "id": "image-search-nl",
        "marker": "_detect_natural_language_image_search",
        "capability": "image.search",
        "owner": "qqbot_image_search",
        "target_ready": True,
        "flags": ["image_search_natural_language_enabled", "image_search_enabled"],
    },
    {
        "id": "food-confirm",
        "marker": "_handle_food_submit_confirmation",
        "capability": "food.confirm",
        "owner": "qqbot_food",
        "target_ready": True,
        "flags": ["food_submit_enabled", "safe_tools_allow_submit_food_review"],
    },
    {
        "id": "memory-handoff",
        "marker": "qqbot_enhancer_memory_reply",
        "capability": "reply.memory_mode",
        "owner": "qqbot_core_reply",
        "target_ready": False,
        "flags": ["memory_mode", "group_identity_context_enabled"],
    },
    {
        "id": "tool-route",
        "marker": "_route_safe_tool",
        "capability": "tool.process_model_output",
        "owner": "qqbot_safe_tools",
        "target_ready": True,
        "flags": ["safe_tools_enabled", "safe_tools_marker_enabled", "safe_tools_max_actions_per_reply"],
    },
    {
        "id": "llm-reply",
        "marker": "self._generate_reply(",
        "capability": "reply.turn_run",
        "owner": "qqbot_core_reply",
        "target_ready": True,
        "flags": ["reply_probability", "group_cooldown_seconds", "user_cooldown_seconds"],
    },
]

LEGACY_HELPERS: list[dict[str, Any]] = [
    {"symbol": "_route_safe_tool", "capability": "tool.process_model_output", "owner": "qqbot_safe_tools", "ready": True},
    {"symbol": "_execute_safe_tool", "capability": "tool.dispatch", "owner": "qqbot_safe_tools", "ready": True},
    {"symbol": "_fetch_webpage_text", "capability": "web.read", "owner": "qqbot_web_reader", "ready": True},
    {"symbol": "_build_web_auto_fetch_context", "capability": "reply.web_auto_fetch", "owner": "qqbot_core_reply", "ready": False},
    {"symbol": "_build_image_search_result", "capability": "image.search", "owner": "qqbot_image_search", "ready": True},
    {"symbol": "_perform_food_submit", "capability": "food.submit", "owner": "qqbot_food", "ready": True},
    {"symbol": "_execute_food_submit", "capability": "food.submit", "owner": "qqbot_food", "ready": True},
    {"symbol": "_execute_search_food_restaurants", "capability": "food.query", "owner": "qqbot_food", "ready": True},
    {"symbol": "_collect_user_sticker", "capability": "sticker.collect", "owner": "qqbot_stickers", "ready": True},
    {"symbol": "_save_collected_payload", "capability": "sticker.collect", "owner": "qqbot_stickers", "ready": True},
    {"symbol": "_load_collected_sticker_index", "capability": "sticker.index.read", "owner": "qqbot_stickers", "ready": True},
    {"symbol": "_save_collected_sticker_index", "capability": "sticker.index.write", "owner": "qqbot_stickers", "ready": True},
    {"symbol": "_resolve_collected_sticker_root", "capability": "sticker.store.init", "owner": "qqbot_stickers", "ready": True},
    {"symbol": "_load_sticker_entries", "capability": "sticker.asset_init", "owner": "qqbot_stickers", "ready": False},
    {"symbol": "_select_sticker_from_pool", "capability": "sticker.decorate", "owner": "qqbot_stickers", "ready": False},
    {"symbol": "_maybe_pick_sticker", "capability": "sticker.decorate", "owner": "qqbot_stickers", "ready": True},
    {"symbol": "_maybe_ai_auto_collect_user_sticker", "capability": "sticker.auto_collect.ai", "owner": "qqbot_stickers", "ready": False},
    {"symbol": "_maybe_auto_collect_user_sticker", "capability": "sticker.auto_collect.local", "owner": "qqbot_stickers", "ready": True},
    {"symbol": "_read_prompt_profile_state_from_disk", "capability": "reply.prompt_profiles", "owner": "qqbot_core_reply", "ready": True},
    {"symbol": "_agent_group_persona_target", "capability": "reply.persona_context", "owner": "qqbot_core_reply", "ready": True},
]

SAFE_TOOL_BRANCHES: list[dict[str, Any]] = [
    {"tool": "search_images", "capability": "image.search", "owner": "qqbot_image_search", "ready": True},
    {"tool": "send_sticker", "capability": "sticker.send", "owner": "qqbot_stickers", "ready": True},
    {"tool": "collect_sticker", "capability": "sticker.collect", "owner": "qqbot_stickers", "ready": True},
    {"tool": "search_food_restaurants", "capability": "food.query", "owner": "qqbot_food", "ready": True},
    {"tool": "submit_food_review", "capability": "food.submit", "owner": "qqbot_food", "ready": True},
    {"tool": "fetch_webpage", "capability": "web.read", "owner": "qqbot_web_reader", "ready": True},
    {"tool": "list_sticker_categories", "capability": "sticker.category.list", "owner": "qqbot_stickers", "ready": True},
]

MINIMAL_TOOL_METHODS: dict[str, tuple[str, str]] = {
    "tool_fetch_webpage": ("web.read", "qqbot_web_reader"),
    "tool_search_images": ("image.search", "qqbot_image_search"),
    "tool_search_restaurants": ("food.query", "qqbot_food"),
    "tool_submit_food_review": ("food.submit", "qqbot_food"),
    "tool_send_sticker": ("sticker.send", "qqbot_stickers"),
    "tool_collect_sticker": ("sticker.collect", "qqbot_stickers"),
    "tool_list_categories": ("sticker.category.list", "qqbot_stickers"),
    "tool_instruction": ("tool.catalog", "qqbot_safe_tools"),
    "_invoke_stable_method": ("tool.dispatch", "qqbot_safe_tools"),
    "_handle_parsed_marker": ("tool.process_model_output", "qqbot_safe_tools"),
}

MINIMAL_HELPERS: dict[str, list[dict[str, Any]]] = {
    "qqbot_core_reply": [
        {"symbol": "_generate_reply", "capability": "reply.turn_run", "owner": "qqbot_core_reply"},
        {"symbol": "_mark_candidate", "capability": "reply.turn_run", "owner": "qqbot_core_reply"},
        {"symbol": "_is_command_message", "capability": "reply.command_exclusion", "owner": "qqbot_core_reply"},
    ],
    "qqbot_web_reader": [
        {"symbol": "_fetch_result", "capability": "web.read", "owner": "qqbot_web_reader"},
    ],
    "qqbot_image_search": [
        {"symbol": "_build_image_result", "capability": "image.search", "owner": "qqbot_image_search"},
    ],
    "qqbot_food": [
        {"symbol": "_run_search", "capability": "food.query", "owner": "qqbot_food"},
        {"symbol": "_perform_submit", "capability": "food.submit", "owner": "qqbot_food"},
    ],
    "qqbot_stickers": [
        {"symbol": "_save_payload", "capability": "sticker.collect", "owner": "qqbot_stickers"},
        {"symbol": "_handle_reclassify", "capability": "sticker.categorize", "owner": "qqbot_stickers"},
    ],
    "qqbot_memory_commands": [
        {"symbol": "handle_text", "capability": "memory.command", "owner": "qqbot_memory_commands", "file": "memory_commands.py"},
        {"symbol": "open_service", "capability": "memory.read", "owner": "qqbot_memory_commands", "file": "memory_commands.py"},
        {"symbol": "parse_command", "capability": "memory.command", "owner": "qqbot_memory_commands", "file": "memory_commands.py"},
    ],
}


def _function_span(path: Path, symbol: str) -> tuple[int, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return 0, 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            return node.lineno, getattr(node, "end_lineno", node.lineno)
    return 0, 0


def _find_compare_line(path: Path, function_name: str, variable: str, value: str) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == variable):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and comparator.value == value:
                return node.lineno
    return 0


def _legacy_lifecycle_entries(package: str, snapshot: Mapping[str, Any], actual_root: Path, source_commit: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    file = _display_path(package, source_commit)
    clone = package == "qqbot_core_full"
    constructor = snapshot["lifecycle"].get("constructor")
    if constructor:
        entries.append(_entry(
            entry_id=f"LIFE-{PACKAGE_CODE[package]}-constructor",
            package=package,
            source_kind="legacy",
            file=file,
            symbol="__init__",
            line=constructor["line"],
            end_line=constructor["end_line"],
            registration="AstrBot Star construction (AstrBot instantiates the plugin class)",
            trigger={"type": "plugin_construction"},
            capability="sticker.asset_init",
            enabled_condition={"plugin_loaded": True, "side_effects_observed": [
                "_load_sticker_entries reads bundled stickers/sticker_rules.json",
                "_resolve_collected_sticker_root creates/chmods plugin_data/qqbot_enhancer/collected_stickers and repairs permissions",
                "_load_collected_sticker_index reads plugin_data index.json; malformed index is treated as empty",
            ]},
            new_owner="qqbot_stickers",
            disable_method=_disable_legacy("initialize"),
            test_case="stickers.builtin_assets",
            status="active_legacy_clone_target_blocked" if clone else "active_legacy_target_blocked",
            side_effects=["read_bundled_assets", "create_data_dir", "chmod_data_dir", "read_index", "migrate_permissions"],
            note="target qqbot_stickers materializes its store and imports legacy collected stickers, but ships no built-in sticker PNG/rules asset set",
        ))
    terminate = snapshot["lifecycle"].get("terminate")
    if terminate:
        entries.append(_entry(
            entry_id=f"LIFE-{PACKAGE_CODE[package]}-terminate",
            package=package,
            source_kind="legacy",
            file=file,
            symbol="terminate",
            line=terminate["line"],
            end_line=terminate["end_line"],
            registration="AstrBot Star lifecycle terminate hook",
            trigger={"type": "plugin_terminate"},
            capability="lifecycle.terminate",
            enabled_condition={"plugin_loaded": True, "body": "docstring only"},
            new_owner="qqbot_core_reply",
            disable_method=_disable_legacy("initialize"),
            test_case="core.handoff.cutover_gate",
            status="active_legacy_noop",
            side_effects=[],
        ))
    return entries


def _legacy_inline_entries(package: str, snapshot: Mapping[str, Any], actual_main: Path, source_commit: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    start, end = _function_span(actual_main, "on_qq_message")
    clone = package == "qqbot_core_full"
    file = _display_path(package, source_commit)
    for branch in LEGACY_INLINE_BRANCHES:
        line = _line_containing(actual_main, branch["marker"], start=start, end=end)
        if not line:
            continue
        status = (
            "active_legacy_clone_target_ready" if clone and branch["target_ready"]
            else "active_legacy_clone_target_blocked" if clone
            else "active_legacy_target_ready" if branch["target_ready"]
            else "active_legacy_target_blocked"
        )
        entries.append(_entry(
            entry_id=f"BR-{PACKAGE_CODE[package]}-{branch['id']}",
            package=package,
            source_kind="legacy",
            file=file,
            symbol="on_qq_message",
            line=line,
            end_line=line,
            registration="inline branch inside filter.event_message_type(ALL, priority=100) + platform_adapter_type",
            trigger={
                "type": "inline_branch",
                "parent_symbol": "on_qq_message",
                "branch": branch["id"],
                "marker": branch["marker"],
            },
            capability=branch["capability"],
            enabled_condition={
                "plugin_loaded": True,
                "legacy_cutover_gate": "legacy_core_reply_enabled",
                "observed_config_flags": branch["flags"],
                "evaluation": "branch marker located inside the real on_qq_message function; flags come from adjacent source conditions",
            },
            new_owner=branch["owner"],
            disable_method=_disable_legacy("event_listener"),
            test_case=CAPABILITY_TESTS.get(branch["capability"], "core.reply.at_private_probability"),
            status=status,
            branch=branch["id"],
        ))
    return entries


def _legacy_safe_tool_entries(package: str, snapshot: Mapping[str, Any], actual_main: Path, source_commit: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    clone = package == "qqbot_core_full"
    file = _display_path(package, source_commit)
    for branch in SAFE_TOOL_BRANCHES:
        line = _find_compare_line(actual_main, "_execute_safe_tool", "name", branch["tool"])
        if not line:
            continue
        entries.append(_entry(
            entry_id=f"HELP-{PACKAGE_CODE[package]}-tool-{branch['tool']}",
            package=package,
            source_kind="legacy",
            file=file,
            symbol="_execute_safe_tool",
            line=line,
            end_line=line,
            registration="fixed whitelist branch in the legacy marker executor",
            trigger={"type": "internal_helper", "tool": branch["tool"]},
            capability=branch["capability"],
            enabled_condition={
                "plugin_loaded": True,
                "legacy_gate": "safe_tools_enabled and tool-specific safe_tools_allow_* flag",
                "observed_config_flags": ["safe_tools_enabled", "safe_tools_marker_enabled"],
            },
            new_owner="qqbot_safe_tools",
            disable_method={
                "transition": "remove the legacy _execute_safe_tool branch and route the same intent through qqbot_safe_tools.dispatch",
                "final": "do_not_load_package",
                "mechanism": "legacy package absent; only qqbot_safe_tools tool dispatcher remains",
                "function_body_gate_insufficient": True,
            },
            test_case=CAPABILITY_TESTS.get(branch["capability"], "safe.marker.single_parse"),
            status="active_legacy_clone_target_ready" if clone else "active_legacy_target_ready",
            tool=branch["tool"],
        ))
    return entries


def _legacy_helper_entries(package: str, snapshot: Mapping[str, Any], actual_main: Path, source_commit: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    clone = package == "qqbot_core_full"
    file = _display_path(package, source_commit)
    for helper in LEGACY_HELPERS:
        line, end_line = _function_span(actual_main, helper["symbol"])
        if not line:
            continue
        ready = bool(helper["ready"])
        status = (
            "active_legacy_clone_target_blocked" if clone and not ready
            else "active_legacy_clone_target_ready" if clone
            else "active_legacy_target_blocked" if not ready
            else "active_legacy_target_ready"
        )
        entries.append(_entry(
            entry_id=f"HELP-{PACKAGE_CODE[package]}-{helper['symbol']}",
            package=package,
            source_kind="legacy",
            file=file,
            symbol=helper["symbol"],
            line=line,
            end_line=end_line,
            registration="internal helper called by the legacy listener/hook/command path",
            trigger={"type": "internal_helper", "symbol": helper["symbol"]},
            capability=helper["capability"],
            enabled_condition={"plugin_loaded": True, "legacy_cutover_gate": "legacy_core_reply_enabled for listener callers"},
            new_owner=helper["owner"],
            disable_method=_disable_legacy("helper"),
            test_case=CAPABILITY_TESTS.get(helper["capability"], "core.reply.at_private_probability"),
            status=status,
        ))
    return entries


def _minimal_lifecycle_entries(package: str, snapshot: Mapping[str, Any], source_commit: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    file = _display_path(package, source_commit)
    lifecycle = snapshot["lifecycle"]
    constructor = lifecycle.get("constructor")
    if package == "qqbot_food" and constructor:
        entries.append(_entry(
            entry_id="LIFE-FOOD-constructor",
            package=package,
            source_kind="minimal_v1_v2_v3",
            file=file,
            symbol="__init__",
            line=constructor["line"],
            end_line=constructor["end_line"],
            registration="AstrBot Star construction",
            trigger={"type": "plugin_construction"},
            capability="lifecycle.food_state_init",
            enabled_condition={"plugin_loaded": True, "side_effects_observed": [
                "creates FoodAPIClient objects (no request)",
                "constructs FoodStateStore at plugin_data/qqbot_food/food_state.json (no eager write)",
            ]},
            new_owner="qqbot_food",
            disable_method=_minimal_disable("initialize"),
            test_case="food.submit.confirm_once",
            status="implemented_transitional",
            side_effects=["create_api_clients", "construct_state_store_no_write"],
        ))
    initialize = lifecycle.get("initialize")
    if package == "qqbot_stickers" and initialize:
        entries.append(_entry(
            entry_id="LIFE-STK-initialize",
            package=package,
            source_kind="minimal_v1_v2_v3",
            file=file,
            symbol="initialize",
            line=initialize["line"],
            end_line=initialize["end_line"],
            registration="AstrBot Star lifecycle initialize hook",
            trigger={"type": "plugin_initialize"},
            capability="sticker.migrate_legacy",
            enabled_condition={"plugin_loaded": True, "config_gate": "collected_sticker_migration_enabled"},
            new_owner="qqbot_stickers",
            disable_method=_minimal_disable("initialize"),
            test_case="stickers.migration.idempotent",
            status="implemented_transitional",
            side_effects=["create_data_dir", "chmod_data_dir", "repair_permissions", "one_shot_legacy_import"],
            note="legacy source tree is read-only input; import is idempotent and never rewrites qqbot_enhancer data",
        ))
    terminate = lifecycle.get("terminate")
    if terminate:
        entries.append(_entry(
            entry_id=f"LIFE-{PACKAGE_CODE[package]}-terminate",
            package=package,
            source_kind="minimal_v1_v2_v3",
            file=file,
            symbol="terminate",
            line=terminate["line"],
            end_line=terminate["end_line"],
            registration="AstrBot Star lifecycle terminate hook",
            trigger={"type": "plugin_terminate"},
            capability="lifecycle.terminate",
            enabled_condition={"plugin_loaded": True},
            new_owner=package,
            disable_method=_minimal_disable("initialize"),
            test_case="core.handoff.cutover_gate",
            status="implemented_transitional",
            side_effects=[],
        ))
    return entries


def _minimal_tool_entries(package: str, snapshot: Mapping[str, Any], actual_root: Path, source_commit: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    file = _display_path(package, source_commit)
    functions = {item["symbol"]: item for item in snapshot["functions"]}
    for symbol, (capability, owner) in MINIMAL_TOOL_METHODS.items():
        function = functions.get(symbol)
        if not function:
            continue
        entries.append(_entry(
            entry_id=f"HELP-{PACKAGE_CODE[package]}-{symbol}",
            package=package,
            source_kind="minimal_v1_v2_v3",
            file=file,
            symbol=symbol,
            line=function["line"],
            end_line=function["end_line"],
            registration="stable cross-plugin helper exposed on the Star instance",
            trigger={"type": "provider_helper", "symbol": symbol},
            capability=capability,
            enabled_condition={"plugin_loaded": True, "dispatch": "qqbot_safe_tools resolves this method via context.get_registered_star"},
            new_owner=owner,
            disable_method=_minimal_disable("helper"),
            test_case=CAPABILITY_TESTS.get(capability, "safe.marker.single_parse"),
            status="implemented_transitional",
        ))
    for helper in MINIMAL_HELPERS.get(package, []):
        target_file_name = helper.get("file", "main.py")
        relative = f".qqbot-plugins-staging/plugins/{package}@{source_commit}/{target_file_name}"
        item = {
            "symbol": helper["symbol"],
            "line": 0,
            "end_line": 0,
        }
        helper_path = actual_root / target_file_name
        helper_line, helper_end = _function_span(helper_path, helper["symbol"])
        if target_file_name == "main.py":
            function = functions.get(helper["symbol"])
            if not function:
                continue
            item = function
        elif helper_line:
            item = {"line": helper_line, "end_line": helper_end}
        else:
            continue
        entries.append(_entry(
            entry_id=f"HELP-{PACKAGE_CODE[package]}-{helper['symbol']}",
            package=package,
            source_kind="minimal_v1_v2_v3",
            file=relative,
            symbol=helper["symbol"],
            line=item["line"],
            end_line=item["end_line"],
            registration="internal seven-plugin helper",
            trigger={"type": "internal_helper", "symbol": helper["symbol"]},
            capability=helper["capability"],
            enabled_condition={"plugin_loaded": True},
            new_owner=helper["owner"],
            disable_method=_minimal_disable("helper"),
            test_case=CAPABILITY_TESTS.get(helper["capability"], "core.reply.at_private_probability"),
            status="implemented_transitional",
        ))
    return entries


def _management_entry(
    *,
    entry_id: str,
    managed_package: str,
    file: str,
    symbol: str,
    line: int,
    route: str,
    capability: str,
    status: str,
    disable_transition: str,
    test_case: str,
    file_exists: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    return _entry(
        entry_id=entry_id,
        package="qqbot_studio",
        source_kind="studio",
        file=file,
        symbol=symbol,
        line=line,
        end_line=line,
        registration=route,
        trigger={"type": "management_route_or_helper", "route_or_helper": route, "managed_legacy_package": managed_package},
        capability=capability,
        enabled_condition={"studio_authenticated": True, "managed_legacy_package": managed_package},
        new_owner="qqbot_studio control bridge",
        disable_method={
            "transition": disable_transition,
            "final": "fixed authenticated control-bridge RPC; no direct legacy file/database/plugin installation path",
            "mechanism": "remove route/helper; owner plugin handles the operation through its provider ABI",
            "function_body_gate_insufficient": True,
        },
        test_case=test_case,
        status=status,
        managed_legacy_package=managed_package,
        file_exists_in_worktree=file_exists,
        notes=notes,
    )


def _management_entries(source_commit: str) -> list[dict[str, Any]]:
    root = inv.ROOT
    server = root / "qqbot_studio" / "server.py"
    deployer = root / "qqbot_studio" / "deployer.py"
    market = root / "qqbot_studio" / "plugin_market.py"
    migration = root / "qqbot_studio" / "plugin_config_migration.py"
    auto_update = root / "scripts" / "auto-update.sh"
    final_migration = root / "qqbot_studio" / "plugin_final_migration.py"
    entries: list[dict[str, Any]] = []
    get_start, get_end = _function_span(server, "do_GET")
    post_start, post_end = _function_span(server, "do_POST")
    nested_get_line = _line_containing(server, 'plugin_config_parts = subroute.split("/")', start=get_start, end=get_end)
    legacy_get_line = _line_containing(server, 'path in {"/api/plugins/config", "/api/plugin-config"}', start=get_start, end=get_end)
    nested_post_line = _line_containing(server, 'plugin_config_parts = subroute.split("/")', start=post_start, end=post_end)
    legacy_post_line = _line_containing(server, 'path in {"/api/plugins/config", "/api/plugin-config"}', start=post_start, end=post_end)
    for managed in LEGACY:
        entries.extend([
            _management_entry(
                entry_id=f"MGMT-STUDIO-get-instance-config-{managed}",
                managed_package=managed,
                file="qqbot_studio/server.py",
                symbol="do_GET",
                line=nested_get_line,
                route="GET /api/instances/{instance_id}/plugins/{plugin_id}/config",
                capability="management.plugin_config.read",
                status="active_legacy_management",
                disable_transition="replace direct local config fallback with fixed owner RPC",
                test_case="migration.control_bridge_auth",
            ),
            _management_entry(
                entry_id=f"MGMT-STUDIO-post-instance-config-{managed}",
                managed_package=managed,
                file="qqbot_studio/server.py",
                symbol="do_POST",
                line=nested_post_line,
                route="POST /api/instances/{instance_id}/plugins/{plugin_id}/config",
                capability="management.plugin_config.write",
                status="active_legacy_management",
                disable_transition="replace direct local/AstrBot config save with fixed owner RPC",
                test_case="migration.control_bridge_auth",
            ),
            _management_entry(
                entry_id=f"MGMT-STUDIO-get-legacy-config-{managed}",
                managed_package=managed,
                file="qqbot_studio/server.py",
                symbol="do_GET",
                line=legacy_get_line,
                route="GET /api/plugins/config?instance={instance_id}&plugin_id={plugin_id}",
                capability="management.plugin_config.read",
                status="active_legacy_management",
                disable_transition="remove legacy query endpoint or route it through fixed owner RPC",
                test_case="migration.control_bridge_auth",
            ),
            _management_entry(
                entry_id=f"MGMT-STUDIO-post-legacy-config-{managed}",
                managed_package=managed,
                file="qqbot_studio/server.py",
                symbol="do_POST",
                line=legacy_post_line,
                route="POST /api/plugins/config",
                capability="management.plugin_config.write",
                status="active_legacy_management",
                disable_transition="remove legacy write endpoint or route it through fixed owner RPC",
                test_case="migration.control_bridge_auth",
            ),
            _management_entry(
                entry_id=f"MGMT-STUDIO-field-map-preview-{managed}",
                managed_package=managed,
                file="qqbot_studio/plugin_config_migration.py",
                symbol="preview_config_migration",
                line=_symbol_line(migration, "preview_config_migration"),
                route="offline read-only field-map preview helper",
                capability="management.field_map.preview",
                status="offline_tool_ready",
                disable_transition="keep offline; no runtime writer",
                test_case="migration.field_map_preview",
            ),
            _management_entry(
                entry_id=f"MGMT-STUDIO-final-migration-{managed}",
                managed_package=managed,
                file="qqbot_studio/plugin_final_migration.py",
                symbol="retire_legacy_plugin_code",
                line=0,
                route="external untracked final config/code migration helper observed in the main worktree",
                capability="management.final_migration",
                status="missing_from_branch",
                disable_transition="commit a reviewed S1 transaction helper or omit it; do not rely on an untracked file",
                test_case="migration.crash_recovery",
                file_exists=final_migration.is_file(),
                notes="File existed as an untracked main-worktree file during S0 but is not present on feat/final-s0; it is not part of the committed baseline.",
            ),
        ])

    # Enhancer-specific deploy/update paths.
    entries.extend([
        _management_entry(
            entry_id="MGMT-DEPLOY-install-bundled-enhancer",
            managed_package="qqbot_enhancer",
            file="qqbot_studio/deployer.py",
            symbol="_install_bundled_plugin",
            line=_symbol_line(deployer, "_install_bundled_plugin"),
            route="deploy-time copytree plugins/qqbot_enhancer -> data/astrbot/plugins/qqbot_enhancer",
            capability="management.legacy_install",
            status="active_legacy_management",
            disable_transition="remove bundled enhancer installation and all automatic fallback",
            test_case="migration.no_legacy_runtime",
        ),
        _management_entry(
            entry_id="MGMT-DEPLOY-write-enhancer-config",
            managed_package="qqbot_enhancer",
            file="qqbot_studio/deployer.py",
            symbol="_write_enhancer_config",
            line=_symbol_line(deployer, "_write_enhancer_config"),
            route="write data/astrbot/config/qqbot_enhancer_config.json",
            capability="management.legacy_config.write",
            status="active_legacy_management",
            disable_transition="remove direct legacy config writer; migrate through field-map owner targets",
            test_case="migration.field_map_preview",
        ),
        _management_entry(
            entry_id="MGMT-DEPLOY-sync-prompt-profiles",
            managed_package="qqbot_enhancer",
            file="qqbot_studio/deployer.py",
            symbol="_sync_prompt_profiles_to_plugin",
            line=_symbol_line(deployer, "_sync_prompt_profiles_to_plugin"),
            route="write prompt_profiles/system_prompt into legacy enhancer config and AstrBot live config",
            capability="management.legacy_prompt_sync",
            status="active_legacy_management",
            disable_transition="move prompt/persona writes into the core_reply/memory_commands owner RPC",
            test_case="core.reply.prompt_profiles",
        ),
        _management_entry(
            entry_id="MGMT-SCRIPT-auto-update-copy-enhancer",
            managed_package="qqbot_enhancer",
            file="scripts/auto-update.sh",
            symbol="shell-copy",
            line=_line_containing(auto_update, 'cp -a "$ROOT_DIR/plugins/qqbot_enhancer/.', start=1),
            route="auto-update copies plugins/qqbot_enhancer into the live plugin root",
            capability="management.legacy_update",
            status="active_legacy_management",
            disable_transition="remove enhancer copy; update only the seven repository releases",
            test_case="migration.no_legacy_runtime",
        ),
        _management_entry(
            entry_id="MGMT-MARKET-reserved-enhancer",
            managed_package="qqbot_enhancer",
            file="qqbot_studio/plugin_market.py",
            symbol="RESERVED_PLUGIN_IDS",
            line=_line_containing(market, "RESERVED_PLUGIN_IDS =", start=1),
            route="market reserves qqbot_enhancer and rejects install/update/delete",
            capability="management.market.reserved_legacy",
            status="active_legacy_management",
            disable_transition="remove the reserved legacy special case after the enhancer is unloaded; market owns only seven releases",
            test_case="migration.market_no_legacy",
        ),
        _management_entry(
            entry_id="MGMT-MARKET-delete-plugin",
            managed_package="qqbot_enhancer",
            file="qqbot_studio/plugin_market.py",
            symbol="delete_plugin",
            line=_symbol_line(market, "delete_plugin"),
            route="market deletion path preserves config/plugin_data/db/sessions",
            capability="management.market.delete",
            status="active_legacy_management",
            disable_transition="remove enhancer-specific conflict text and legacy id handling; keep data-preservation semantics for the seven packages",
            test_case="migration.market_no_legacy",
        ),
    ])
    return entries


def build_entrypoints(snapshots: Mapping[str, Any], staging_root: Path, staging_commit: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for package in ["qqbot_enhancer", "qqbot_core_full", *FINAL]:
        snapshot = snapshots["packages"][package]
        for handler in snapshot["decorated_handlers"]:
            entries.append(_handler_entry(package, handler, snapshot, staging_commit))
        if package in LEGACY:
            actual_root = ENHANCER_ROOT if package == "qqbot_enhancer" else staging_root / "plugins" / package
            actual_main = actual_root / "main.py"
            entries.extend(_legacy_lifecycle_entries(package, snapshot, actual_root, staging_commit))
            entries.extend(_legacy_inline_entries(package, snapshot, actual_main, staging_commit))
            entries.extend(_legacy_safe_tool_entries(package, snapshot, actual_main, staging_commit))
            entries.extend(_legacy_helper_entries(package, snapshot, actual_main, staging_commit))
        else:
            actual_root = staging_root / "plugins" / package
            entries.extend(_minimal_lifecycle_entries(package, snapshot, staging_commit))
            entries.extend(_minimal_tool_entries(package, snapshot, actual_root, staging_commit))
    entries.extend(_management_entries(staging_commit))

    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in entries:
        if entry["entry_id"] in seen:
            duplicates.append(entry["entry_id"])
        seen.add(entry["entry_id"])
    if duplicates:
        raise RuntimeError(f"duplicate entry ids: {sorted(set(duplicates))}")

    lifecycle_summary: dict[str, Any] = {}
    for package in ["qqbot_enhancer", "qqbot_core_full", *FINAL]:
        lifecycle = snapshots["packages"][package]["lifecycle"]
        lifecycle_summary[package] = {
            "constructor": bool(lifecycle.get("constructor")),
            "initialize": bool(lifecycle.get("initialize")),
            "terminate": bool(lifecycle.get("terminate")),
            "task_calls": lifecycle.get("task_calls", []),
            "scheduler_or_task_side_effects": bool(lifecycle.get("task_calls")),
        }

    for entry in entries:
        entry.setdefault("entry_kind", {
            "CMD": "command",
            "EVT": "event_listener",
            "HOOK": "hook",
            "LIFE": "lifecycle",
            "HELP": "provider_helper",
            "BR": "inline_branch",
            "MGMT": "management",
        }.get(entry["entry_id"].split("-", 1)[0].upper(), "other"))

    entries.sort(key=lambda item: (
        ["qqbot_enhancer", "qqbot_core_full", *FINAL, "qqbot_studio"].index(item["package"])
        if item["package"] in ["qqbot_enhancer", "qqbot_core_full", *FINAL, "qqbot_studio"] else 99,
        item["line"],
        item["entry_id"],
    ))

    def _count(key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in entries:
            value = str(entry.get(key, ""))
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items()))

    return {
        "schema_version": 1,
        "description": "Offline entrypoint inventory; this directory is never loaded as an AstrBot plugin.",
        "source_commit_staging": staging_commit,
        "entry_status_vocabulary": STATUS_VOCABULARY,
        "counts": {
            "total": len(entries),
            "by_package": _count("package"),
            "by_entry_kind": _count("entry_kind"),
            "by_status": _count("status"),
            "by_source_kind": _count("source_kind"),
            "by_managed_legacy_package": {
                key: value for key, value in _count("managed_legacy_package").items() if key
            },
        },
        "lifecycle_summary": lifecycle_summary,
        "known_registration_conflicts": _registration_conflicts(snapshots),
        "entrypoints": entries,
    }



# --- command aliases ------------------------------------------------------
OPERATION_TARGET_HANDLER: dict[str, tuple[str, str]] = {
    "image.search": ("qqbot_image_search", "search_images"),
    "food.query": ("qqbot_food", "food_command"),
    "sticker.collect": ("qqbot_stickers", "collect_sticker_command"),
    "sticker.status": ("qqbot_stickers", "sticker_status_command"),
    "sticker.category.list": ("qqbot_stickers", "sticker_categories_command"),
    "sticker.list": ("qqbot_stickers", "sticker_list_command"),
    "sticker.send": ("qqbot_stickers", "send_sticker_command"),
    "memory.command": ("qqbot_memory_commands", "memory_command"),
    "web.read": ("qqbot_web_reader", "read_web"),
    "tool.catalog": ("qqbot_safe_tools", "status"),
    "core_reply.reply_status": ("qqbot_core_reply", "option_command"),
    "core_reply.legacy_song_play": ("qqbot_core_reply", "local_song_command"),
    "session.list": ("qqbot_memory_commands", ""),
    "session.switch": ("qqbot_memory_commands", ""),
    "session.new": ("qqbot_memory_commands", ""),
    "session.delete": ("qqbot_memory_commands", ""),
}


def _target_handler_ref(snapshots: Mapping[str, Any], operation: str) -> str:
    target = OPERATION_TARGET_HANDLER.get(operation)
    if not target:
        return ""
    plugin, symbol = target
    if not symbol:
        method = {
            "session.list": "session.read",
            "session.switch": "session.switch",
            "session.new": "session.open",
            "session.delete": "session.reset",
        }.get(operation, operation)
        return f"{plugin}:<contract {method} not implemented in current source>"
    snapshot = snapshots["packages"][plugin]
    for function in snapshot["functions"]:
        if function["symbol"] == symbol:
            file = f".qqbot-plugins-staging/plugins/{plugin}@{snapshots['ast_index']['source_roots']['staging'].split('@')[-1]}/main.py"
            return f"{plugin}:{file}:{symbol}:{function['line']}"
    for handler in snapshot["decorated_handlers"]:
        if handler["symbol"] == symbol:
            file = f".qqbot-plugins-staging/plugins/{plugin}@{snapshots['ast_index']['source_roots']['staging'].split('@')[-1]}/main.py"
            return f"{plugin}:{file}:{symbol}:{handler['symbol_line']}"
    return f"{plugin}:{symbol} (not found)"


def build_command_aliases(snapshots: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    conflicts_by_spelling = {
        item["spelling"]: item["registrations"]
        for item in baseline["known_registration_conflicts"]
    }
    aliases: list[dict[str, Any]] = []
    for package in ["qqbot_enhancer", "qqbot_core_full", *FINAL]:
        snapshot = snapshots["packages"][package]
        for handler in snapshot["decorated_handlers"]:
            if handler["kind"] != "command" or not handler.get("command"):
                continue
            base = str(handler["command"])
            operation = _command_operation(package, base)
            target_ref = _target_handler_ref(snapshots, operation)
            operation_blocked = operation in BLOCKED_OPERATIONS
            for spelling in [base, *handler.get("aliases", [])]:
                if package in LEGACY:
                    status = "legacy_active_target_blocked" if operation_blocked else "legacy_active_target_ready"
                    if package == "qqbot_core_full":
                        status = "legacy_clone_target_blocked" if operation_blocked else "legacy_clone_target_ready"
                else:
                    status = BLOCKED_OPERATIONS.get(operation, "transitional_ready")
                aliases.append({
                    "alias_id": f"ALIAS-{PACKAGE_CODE.get(package, 'PKG')}-{handler['symbol']}-{spelling}",
                    "package": package,
                    "source_kind": "legacy" if package in LEGACY else "minimal_v1_v2_v3",
                    "spelling": spelling,
                    "spelling_with_prefix": f"/{spelling}",
                    "canonical_command": base,
                    "alias_kind": "canonical" if spelling == base else "alias",
                    "operation_id": operation,
                    "target_plugin": COMMAND_TARGET_OWNER.get(operation, ""),
                    "matching": {
                        "engine": "astrbot_filter.command",
                        "declared_name": base,
                        "declared_aliases": handler.get("aliases", []),
                        "exact_command_token": True,
                        "argument_parsing": "handler-specific; see old_handler",
                        "wake_prefix": "AstrBot configured global wake prefix; no private matching engine",
                    },
                    "permission": COMMAND_PERMISSIONS.get(operation, "authenticated_actor"),
                    "old_handler": f"{package}:{_display_path(package, snapshots['ast_index']['source_roots']['staging'].split('@')[-1])}:{handler['symbol']}:{handler['symbol_line']}",
                    "new_handler": target_ref,
                    "new_handler_implemented": bool(target_ref) and "not implemented" not in target_ref,
                    "test_case": CAPABILITY_TESTS.get(operation, _test_case_for(base)),
                    "status": status,
                    "registration_conflicts": [
                        item for item in conflicts_by_spelling.get(spelling, [])
                        if not (item["package"] == package and item["symbol"] == handler["symbol"] and item["line"] == handler["symbol_line"])
                    ],
                    "documented": True,
                })

    operations: dict[str, dict[str, Any]] = {}
    for item in aliases:
        operation = item["operation_id"]
        record = operations.setdefault(operation, {
            "operation_id": operation,
            "target_plugin": item["target_plugin"],
            "target_handler": item["new_handler"],
            "implemented_in_source": item["new_handler_implemented"],
            "spellings": [],
            "packages": [],
            "status": "blocked" if operation in BLOCKED_OPERATIONS else "implemented_or_transitional",
            "test_case": item["test_case"],
            "contract": CONTRACT_OPERATIONS.get(operation),
        })
        record["spellings"].append(item["spelling"])
        if item["package"] not in record["packages"]:
            record["packages"].append(item["package"])
    for record in operations.values():
        record["spellings"] = sorted(set(record["spellings"]))
        record["packages"] = sorted(record["packages"])
        if record["status"] == "blocked":
            record["blocked_reason"] = {
                "session.list": "qqbot_memory_commands does not expose session.list/session.read yet",
                "session.switch": "qqbot_memory_commands does not expose session.switch yet",
                "session.new": "qqbot_memory_commands does not expose session.open yet",
                "session.delete": "qqbot_memory_commands does not expose session.reset yet",
                "core_reply.legacy_song_play": "song ownership not confirmed by the final contract (§18.1.7)",
            }[record["operation_id"]]

    return {
        "schema_version": 1,
        "description": "Offline command/alias inventory; names are extracted from AstrBot filter.command decorators, never invented.",
        "counts": {
            "spellings": len(aliases),
            "canonical_commands": sum(1 for item in aliases if item["alias_kind"] == "canonical"),
            "alias_spellings": sum(1 for item in aliases if item["alias_kind"] == "alias"),
            "unique_operations": len(operations),
            "legacy_spellings": sum(1 for item in aliases if item["source_kind"] == "legacy"),
            "minimal_spellings": sum(1 for item in aliases if item["source_kind"] == "minimal_v1_v2_v3"),
        },
        "matching_contract": {
            "source": "real AstrBot filter.command decorators in every package main.py",
            "duplicate_behavior": "if two packages are loaded, AstrBot may register both handlers for the same spelling; the final manifest must load exactly one owner",
            "no_private_matcher": "this inventory does not invent a second command parser",
        },
        "contract_operations": CONTRACT_OPERATIONS,
        "operations": dict(sorted(operations.items())),
        "aliases": aliases,
    }


if __name__ == "__main__":
    raise SystemExit(main())
