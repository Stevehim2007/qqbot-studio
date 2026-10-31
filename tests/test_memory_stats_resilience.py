"""Regression tests for transient AstrBot config reads and stats permission degradation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.deployer import DeployManager
from qqbot_studio.dockerctl import DockerController, DockerStatus
from qqbot_studio.resource_stats import directory_usage, directory_usage_details
from qqbot_studio.store import Store
from qqbot_studio.studio import StudioManager


class JsonReadRetryTests(unittest.TestCase):
    def test_empty_file_is_retried_until_json_is_complete(self):
        path = Path("/tmp/qqbot-test-cmd-config.json")
        reads = ["", "", '{"platform_settings": {"unique_session": false}}']
        with mock.patch.object(Path, "read_text", side_effect=reads), mock.patch(
            "qqbot_studio.deployer.time.sleep"
        ) as sleeper:
            value, error = DeployManager._read_json_object_with_error(path)
        self.assertEqual(error, "")
        self.assertEqual(value["platform_settings"]["unique_session"], False)
        self.assertEqual(sleeper.call_count, 2)

    def test_half_written_json_is_retried(self):
        path = Path("/tmp/qqbot-test-cmd-config.json")
        reads = ['{"provider": [', '{"provider": []}']
        with mock.patch.object(Path, "read_text", side_effect=reads), mock.patch(
            "qqbot_studio.deployer.time.sleep"
        ):
            value, error = DeployManager._read_json_object_with_error(path)
        self.assertEqual(error, "")
        self.assertEqual(value, {"provider": []})

    def test_stable_corrupt_json_is_not_silently_treated_as_empty(self):
        path = Path("/tmp/qqbot-test-cmd-config.json")
        with mock.patch.object(
            Path,
            "read_text",
            return_value='{"provider": [',
        ), mock.patch("qqbot_studio.deployer.time.sleep") as sleeper:
            value, error = DeployManager._read_json_object_with_error(path)
        self.assertIsNone(value)
        self.assertIn("JSON", error)
        self.assertEqual(sleeper.call_count, 2)

    def test_unique_session_migration_reports_stable_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = DeployManager(
                store=Store(tmp),
                docker=DockerController(tmp),
            )
            manager.astrbot_data_dir.mkdir(parents=True, exist_ok=True)
            manager.astrbot_cmd_config_path.write_text('{"platform_settings": ', encoding="utf-8")
            with mock.patch("qqbot_studio.deployer.time.sleep"):
                changed, error = manager._migrate_unique_session_for_shared_memory()
                changed_again, error_again = manager._ensure_unique_session_default()
        self.assertFalse(changed)
        self.assertFalse(changed_again)
        self.assertIn("JSON", error)
        self.assertIn("JSON", error_again)


class PluginConfigReadRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = DeployManager(
            store=Store(self.root),
            docker=DockerController(self.root),
        )
        plugin_dir = self.root / "data" / "astrbot" / "plugins" / "qqbot_enhancer"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "_conf_schema.json").write_text(
            json.dumps(
                {
                    "reply_probability": {
                        "type": "float",
                        "default": 0.12,
                        "slider": {"min": 0, "max": 1, "step": 0.01},
                    },
                    "memory_mode": {
                        "type": "string",
                        "default": "direct",
                        "options": ["direct", "astrbot"],
                    },
                }
            ),
            encoding="utf-8",
        )
        self.config_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_plugin_config_retries_empty_and_half_written_file(self):
        self.config_path.write_text("", encoding="utf-8")
        with mock.patch.object(
            Path,
            "read_text",
            side_effect=["", "", '{"memory_mode": "astrbot"}'],
        ), mock.patch("qqbot_studio.deployer.time.sleep") as sleeper:
            value, error, exists = self.manager._read_local_plugin_config("qqbot_enhancer")
        self.assertTrue(exists)
        self.assertEqual(error, "")
        self.assertEqual(value["memory_mode"], "astrbot")
        self.assertEqual(sleeper.call_count, 2)

    def test_save_plugin_config_then_reread_is_not_reported_corrupt(self):
        saved = self.manager.set_plugin_config("qqbot_enhancer", {"reply_probability": 0.5})
        self.assertTrue(saved["ok"], saved.get("error"))
        loaded = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertEqual(loaded["values"]["reply_probability"], 0.5)
        self.assertEqual(
            json.loads(self.config_path.read_text(encoding="utf-8"))["reply_probability"],
            0.5,
        )
        # Reopen through a fresh manager to catch any stale in-memory state.
        reopened = DeployManager(store=Store(self.root), docker=DockerController(self.root))
        reread = reopened.get_plugin_config("qqbot_enhancer")
        self.assertTrue(reread["ok"], reread.get("error"))
        self.assertEqual(reread["values"]["reply_probability"], 0.5)

    def test_stable_corrupt_local_plugin_config_recovers_then_save_repairs(self):
        self.config_path.write_text('{"reply_probability": ', encoding="utf-8")
        original = self.config_path.read_text(encoding="utf-8")
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            loaded = self.manager.get_plugin_config("qqbot_enhancer")
            after_get = self.config_path.read_text(encoding="utf-8")
            saved = self.manager.set_plugin_config(
                "qqbot_enhancer", {"reply_probability": 0.3}
            )
        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertTrue(loaded["recovered"])
        self.assertIn(loaded["recovery_source"], {"cache", "studio", "schema"})
        self.assertIn("已从", loaded["message"])
        self.assertEqual(loaded["warning"], loaded["warnings"][0] if loaded["warnings"] else "")
        self.assertTrue(loaded["corrupt_snapshot"])
        self.assertTrue(Path(loaded["corrupt_snapshot"]).exists())
        self.assertEqual(after_get, original)
        self.assertTrue(saved["ok"], saved.get("error"))
        repaired = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["reply_probability"], 0.3)
        self.assertTrue((self.config_path.with_name(self.config_path.name + ".bak")).exists())
        self.assertTrue(Path(loaded["corrupt_snapshot"]).exists())


class MemoryModeNonFatalWarningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = DeployManager(
            store=Store(self.root),
            docker=DockerController(self.root),
        )
        self.plugin_config = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        self.plugin_config.parent.mkdir(parents=True, exist_ok=True)
        self.plugin_config.write_text(
            json.dumps({"memory_mode": "direct", "reply_probability": 0.33}),
            encoding="utf-8",
        )
        self.cmd_config = self.root / "data" / "astrbot" / "cmd_config.json"
        self.cmd_config.write_text('{"platform_settings": ', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_mode_saved_but_cmd_config_error_is_nonfatal_warning(self):
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            result = self.manager.set_memory_mode("astrbot")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["mode"], "astrbot")
        self.assertEqual(result["memory_mode"], "astrbot")
        self.assertTrue(result["restart_required"])
        self.assertTrue(result["nonfatal"])
        self.assertTrue(result["nonfatal_warning"])
        self.assertTrue(result["warning"])
        self.assertTrue(result["warnings"])
        self.assertIn("unique_session", " ".join(result["warnings"]))
        self.assertEqual(result["error"], "")
        saved = json.loads(self.plugin_config.read_text(encoding="utf-8"))
        self.assertEqual(saved["memory_mode"], "astrbot")
        self.assertEqual(saved["reply_probability"], 0.33)
        self.assertIsNone(result["unique_session"])
        self.assertFalse(result["group_memory_shared"])

    def test_cmd_config_failure_is_not_parsed_repeatedly_in_one_request(self):
        original = self.manager._read_json_object_with_error
        cmd_calls = []

        def tracking(path, **kwargs):
            if Path(path) == self.cmd_config:
                cmd_calls.append(Path(path))
            return original(path, **kwargs)

        with mock.patch.object(
            self.manager, "_read_json_object_with_error", side_effect=tracking
        ), mock.patch("qqbot_studio.deployer.time.sleep"):
            result = self.manager.set_memory_mode("astrbot")
        self.assertTrue(result["ok"])
        self.assertTrue(result["nonfatal"])
        self.assertEqual(cmd_calls, [self.cmd_config])


class _OfflineDocker:
    def detect(self, force=False):
        return DockerStatus(False, False, False, False, detail="Docker 不可用")


class _OfflineManager:
    docker = _OfflineDocker()


class StatsPermissionDegradeTests(unittest.TestCase):
    def test_directory_usage_skips_unreadable_subdir_and_keeps_readable_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "core.bin").write_bytes(b"c" * 512)
            locked = root / "plugin_data" / "qqbot_enhancer" / "collected_stickers"
            locked.mkdir(parents=True)
            real_scandir = os.scandir

            def guarded_scandir(path):
                if Path(path) == locked:
                    raise PermissionError(13, "Permission denied", str(path))
                return real_scandir(path)

            with mock.patch(
                "qqbot_studio.resource_stats.os.scandir",
                side_effect=guarded_scandir,
            ):
                total, files, warnings = directory_usage(root)
                details = directory_usage_details(root)
        self.assertEqual(total, 512)
        self.assertEqual(files, 1)
        self.assertTrue(warnings)
        self.assertIn("部分不可读", warnings[0])
        self.assertIn("权限不足", warnings[0])
        self.assertEqual(details["bytes"], 512)
        self.assertEqual(details["file_count"], 1)
        self.assertTrue(details["partial"])
        self.assertTrue(details["permission_denied"])
        self.assertGreaterEqual(details["skipped_entries"], 1)

    def test_stats_keeps_ok_and_reports_partial_permission_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = StudioManager(tmp)
            meta = studio.create_instance("权限统计测试")
            instance_dir = Path(tmp) / "instances" / meta["id"]
            (instance_dir / "core.bin").write_bytes(b"x" * 2048)
            locked = (
                instance_dir
                / "data"
                / "astrbot"
                / "plugin_data"
                / "qqbot_enhancer"
                / "collected_stickers"
            )
            locked.mkdir(parents=True)
            real_scandir = os.scandir

            def guarded_scandir(path):
                if Path(path) == locked:
                    raise PermissionError(13, "Permission denied", str(path))
                return real_scandir(path)

            with mock.patch.object(studio, "get_manager", return_value=_OfflineManager()), mock.patch(
                "qqbot_studio.resource_stats.os.scandir",
                side_effect=guarded_scandir,
            ):
                stats = studio.get_stats(meta["id"], force_refresh=True)

        self.assertTrue(stats["ok"])
        self.assertEqual(stats["error"], "")
        self.assertTrue(stats["partial"])
        self.assertTrue(stats["permission_denied"])
        self.assertTrue(stats["warnings"])
        self.assertEqual(stats["warnings"], stats["errors"])
        self.assertIn("部分不可读", " ".join(stats["warnings"]))
        self.assertIn("部分不可读", stats["message"])
        self.assertIn("data_dir_size_bytes", stats["global"])
        self.assertIn("disk", stats["global"])
        self.assertGreaterEqual(stats["instance"]["data_dir_size_bytes"], 2048)
        self.assertTrue(stats["instance"]["partial"])
        self.assertTrue(stats["instance"]["permission_denied"])
        self.assertTrue(stats["instance"]["warnings"])
        self.assertTrue(any(item.get("partial") for item in stats["instance"]["rollup"]))


if __name__ == "__main__":
    unittest.main()
