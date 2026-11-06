"""Tests for the safe final seven-plugin configuration migration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.plugin_final_migration import (
    FINAL_PLUGIN_IDS,
    apply_plan,
    build_plan,
    find_legacy_config,
    load_schemas,
    retire_legacy_plugin_code,
)


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class FinalMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.astrbot = self.root / "data" / "astrbot"
        self.plugins = self.astrbot / "plugins"
        self.config_dir = self.astrbot / "config"
        self.config_dir.mkdir(parents=True)
        self._make_plugins()
        self.legacy_path = self.config_dir / "qqbot_enhancer_config.json"
        write(
            self.legacy_path,
            {
                "legacy_core_reply_enabled": False,
                "reply_probability": 0.35,
                "system_prompt": "legacy prompt",
                "memory_mode": "astrbot",
                "memory_commands_enabled": True,
                "memory_commands_allow_user_ids": "10001,10002",
                "safe_tools_enabled": True,
                "safe_tools_marker_enabled": True,
                "web_fetch_timeout_seconds": 22,
                "web_fetch_max_response_bytes": 123456,
                "food_enabled": True,
                "food_max_results": 4,
                "sticker_enabled": True,
                "sticker_probability": 0.2,
                "collected_sticker_enabled": True,
                "image_search_enabled": False,
                "unknown_old_feature": "kept-only-in-report",
            },
        )

    def _make_plugins(self):
        schemas = {
            "qqbot_core_reply": {
                "enabled": {"type": "bool", "default": False},
                "reply_probability": {"type": "float", "default": 0.0, "slider": {"min": 0, "max": 1}},
                "system_prompt": {"type": "string", "default": "new default"},
                "memory_mode": {"type": "string", "default": "direct", "options": ["direct", "astrbot"]},
            },
            "qqbot_safe_tools": {
                "enabled": {"type": "bool", "default": True},
                "marker_enabled": {"type": "bool", "default": True},
                "allow_submit_food_review": {"type": "bool", "default": False},
                "web_fetch_cooldown_seconds": {"type": "int", "default": 30},
            },
            "qqbot_web_reader": {
                "enabled": {"type": "bool", "default": True},
                "timeout_seconds": {"type": "int", "default": 12, "slider": {"min": 3, "max": 60}},
                "max_bytes": {"type": "int", "default": 1048576},
            },
            "qqbot_image_search": {
                "enabled": {"type": "bool", "default": True},
                "user_cooldown_seconds": {"type": "int", "default": 60},
            },
            "qqbot_food": {
                "food_enabled": {"type": "bool", "default": True},
                "food_max_results": {"type": "int", "default": 3, "slider": {"min": 1, "max": 5}},
            },
            "qqbot_stickers": {
                "sticker_enabled": {"type": "bool", "default": True},
                "sticker_probability": {"type": "float", "default": 0.25},
                "collected_sticker_enabled": {"type": "bool", "default": True},
            },
            "qqbot_memory_commands": {
                "enabled": {"type": "bool", "default": False},
                "allow_user_ids": {"type": "string", "default": ""},
                "max_results": {"type": "int", "default": 5},
            },
        }
        for plugin_id in FINAL_PLUGIN_IDS:
            directory = self.plugins / plugin_id
            directory.mkdir(parents=True)
            write(directory / "_conf_schema.json", schemas[plugin_id])

    def test_find_legacy_config_and_fallback(self):
        self.assertEqual(find_legacy_config(self.astrbot), self.legacy_path)
        self.legacy_path.unlink()
        fallback = self.config_dir / "qqbot_core_full_config.json"
        write(fallback, {})
        self.assertEqual(find_legacy_config(self.astrbot), fallback)

    def test_plan_maps_alias_exact_and_defaults(self):
        plan = build_plan(self.astrbot, self.plugins)
        self.assertTrue(plan.targets["qqbot_core_reply"]["enabled"])
        self.assertEqual(plan.targets["qqbot_core_reply"]["reply_probability"], 0.35)
        self.assertEqual(plan.targets["qqbot_core_reply"]["system_prompt"], "legacy prompt")
        self.assertEqual(plan.targets["qqbot_safe_tools"]["marker_enabled"], True)
        self.assertEqual(plan.targets["qqbot_safe_tools"]["web_fetch_cooldown_seconds"], 30)
        self.assertEqual(plan.targets["qqbot_web_reader"]["timeout_seconds"], 22)
        self.assertEqual(plan.targets["qqbot_web_reader"]["max_bytes"], 123456)
        self.assertEqual(plan.targets["qqbot_food"]["food_max_results"], 4)
        self.assertEqual(plan.targets["qqbot_stickers"]["sticker_probability"], 0.2)
        self.assertEqual(plan.targets["qqbot_memory_commands"]["allow_user_ids"], "10001,10002")
        self.assertIn("unknown_old_feature", plan.unmapped_source_keys)
        self.assertNotIn("unknown_old_feature", json.dumps(plan.targets, ensure_ascii=False))

    def test_nondefault_existing_target_wins_and_default_is_replaced(self):
        write(
            self.config_dir / "qqbot_stickers_config.json",
            {"sticker_probability": 0.9, "sticker_enabled": True},
        )
        write(
            self.config_dir / "qqbot_food_config.json",
            {"food_max_results": 3},
        )
        plan = build_plan(self.astrbot, self.plugins)
        self.assertEqual(plan.targets["qqbot_stickers"]["sticker_probability"], 0.9)
        self.assertIn("sticker_probability", plan.conflicts["qqbot_stickers"])
        self.assertEqual(plan.targets["qqbot_food"]["food_max_results"], 4)
        self.assertNotIn("qqbot_food", plan.conflicts)

    def test_invalid_type_uses_default_and_warns(self):
        config = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        config["food_max_results"] = "not-a-number"
        write(self.legacy_path, config)
        plan = build_plan(self.astrbot, self.plugins)
        self.assertEqual(plan.targets["qqbot_food"]["food_max_results"], 3)
        self.assertTrue(any("food_max_results" in item for item in plan.warnings))

    def test_apply_writes_all_targets_backups_and_report(self):
        backup_seed = self.config_dir / "qqbot_core_reply_config.json"
        write(backup_seed, {"enabled": False, "system_prompt": "keep me"})
        plan = build_plan(self.astrbot, self.plugins)
        result = apply_plan(plan, self.astrbot)
        backup = Path(result["backup_dir"])
        self.assertTrue((backup / "qqbot_enhancer_config.json").is_file())
        self.assertTrue((backup / "qqbot_core_reply_config.json").is_file())
        self.assertTrue((backup / "migration-report.json").is_file())
        for plugin_id in FINAL_PLUGIN_IDS:
            value = json.loads((self.config_dir / f"{plugin_id}_config.json").read_text(encoding="utf-8"))
            self.assertIsInstance(value, dict)
        self.assertTrue(result["report"]["preserved"])
        self.assertNotIn("10001,10002", json.dumps(result["report"], ensure_ascii=False))

    def test_apply_rolls_back_on_write_failure(self):
        original = {"enabled": False, "system_prompt": "before failure"}
        target = self.config_dir / "qqbot_core_reply_config.json"
        write(target, original)
        plan = build_plan(self.astrbot, self.plugins)
        real_write = __import__("qqbot_studio.plugin_final_migration", fromlist=["_atomic_write_json"])._atomic_write_json
        calls = {"count": 0}

        def flaky(path, value, **kwargs):
            calls["count"] += 1
            if calls["count"] == 3:
                raise OSError("simulated disk error")
            return real_write(path, value, **kwargs)

        with mock.patch("qqbot_studio.plugin_final_migration._atomic_write_json", side_effect=flaky):
            with self.assertRaises(OSError):
                apply_plan(plan, self.astrbot)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), original)

    def test_retire_moves_only_legacy_code_and_preserves_data(self):
        legacy = self.plugins / "qqbot_enhancer"
        core_full = self.plugins / "qqbot_core_full"
        final = self.plugins / "qqbot_core_reply"
        plugin_data = self.astrbot / "plugin_data" / "qqbot_enhancer" / "collected_stickers"
        database = self.astrbot / "data_v4.db"
        for path in (legacy, core_full, final):
            path.mkdir(parents=True, exist_ok=True)
            write(path / "main.py", "VALUE=1")
        plugin_data.mkdir(parents=True)
        (plugin_data / "x.png").write_bytes(b"x")
        database.write_bytes(b"db")
        result = retire_legacy_plugin_code(self.root)
        self.assertTrue(final.is_dir())
        self.assertFalse(legacy.exists())
        self.assertFalse(core_full.exists())
        self.assertTrue(plugin_data.is_dir())
        self.assertEqual(database.read_bytes(), b"db")
        self.assertEqual(len(result["moved"]), 2)
        self.assertTrue((Path(result["backup_dir"]) / "qqbot_enhancer" / "main.py").is_file())

    def test_rejects_symlink_legacy_plugin_code(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.plugins / "qqbot_enhancer"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ValueError):
            retire_legacy_plugin_code(self.root)


if __name__ == "__main__":
    unittest.main()
