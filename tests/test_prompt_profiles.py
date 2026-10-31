"""Prompt profile persistence and API contract regression tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from qqbot_studio.deployer import (
    PROMPT_PROFILE_MAX_COUNT,
    PROMPT_PROFILE_MAX_PROMPT_CHARS,
    PROMPT_PROFILES_MAX_JSON_CHARS,
    DeployManager,
)
from qqbot_studio.dockerctl import DockerController
from qqbot_studio.store import Store


class PromptProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.manager = DeployManager(store=self.store, docker=DockerController(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_prompt_migrates_to_single_default_profile_with_newlines(self):
        prompt = "第一行\n第二行\n\n第四行  \n"
        config = self.store.load_config()
        config["prompt"] = prompt
        self.store.save_config(config)
        result = self.manager.get_prompt_profiles()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["active_id"], "default")
        self.assertEqual(len(result["prompts"]), 1)
        profile = result["prompts"][0]
        self.assertEqual(profile["id"], "default")
        self.assertEqual(profile["name"], "默认提示词")
        self.assertEqual(profile["prompt"], prompt)
        self.assertTrue(profile["pinned"])
        for field in ("created_at", "updated_at"):
            self.assertTrue(profile[field])
        saved = self.store.load_config()
        self.assertEqual(saved["prompt_profiles_active_id"], "default")
        self.assertEqual(saved["prompt"], prompt)
        self.assertEqual(self.store.config_path.stat().st_mode & 0o777, 0o600)

    def _write_raw_profiles(self, count: int, active_id: str):
        items = [
            {
                "id": f"p{index:03d}",
                "name": f"P{index}",
                "prompt": f"prompt-{index}",
                "pinned": item_id == active_id,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
            for index, item_id in enumerate(f"p{i:03d}" for i in range(count))
        ]
        config = self.store.load_config()
        config["prompt_profiles"] = items
        config["prompt_profiles_active_id"] = active_id
        self.store.save_config(config)
        return items

    def test_101_profiles_returns_actionable_error_without_truncation(self):
        for active in ("p000", "p100"):
            items = self._write_raw_profiles(101, active)
            result = self.manager.get_prompt_profiles()
            self.assertFalse(result["ok"])
            self.assertIn("超过上限 100", result["error"])
            self.assertEqual(len(result["prompts"]), 101)
            self.assertEqual(result["active_id"], active)
            self.assertTrue(result.get("limit_exceeded"))
            saved = self.store.load_config()["prompt_profiles"]
            self.assertEqual(len(saved), 101)
            self.assertEqual(saved, items)
            rejected_update = self.manager.update_prompt_profiles(
                {"action": "create", "name": "overflow", "prompt": "x"}
            )
            self.assertFalse(rejected_update["ok"])
            self.assertTrue(rejected_update.get("limit_exceeded"))
            self.assertEqual(len(self.store.load_config()["prompt_profiles"]), 101)

    def test_12000_and_12001_prompt_boundaries_are_not_silently_dropped(self):
        accepted = "x" * 12000
        config = self.store.load_config()
        config["prompt_profiles"] = [
            {
                "id": "p_ok",
                "name": "ok",
                "prompt": accepted,
                "pinned": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        config["prompt_profiles_active_id"] = "p_ok"
        self.store.save_config(config)
        result = self.manager.get_prompt_profiles()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["prompts"][0]["prompt"], accepted)

        rejected_prompt = "y" * 12001
        config = self.store.load_config()
        config["prompt_profiles"] = [
            {
                "id": "p_too_long",
                "name": "too-long",
                "prompt": rejected_prompt,
                "pinned": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        config["prompt_profiles_active_id"] = "p_too_long"
        self.store.save_config(config)
        rejected = self.manager.get_prompt_profiles()
        self.assertFalse(rejected["ok"])
        self.assertIn("12001", rejected["error"])
        self.assertEqual(len(self.store.load_config()["prompt_profiles"][0]["prompt"]), 12001)

    def test_get_repairs_stale_plugin_prompt_state(self):
        first = self.manager.get_prompt_profiles()
        self.assertTrue(first["ok"])
        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_config = json.loads(plugin_path.read_text(encoding="utf-8"))
        plugin_config["prompt_profiles"] = "[]"
        plugin_config["prompt_profile_active_id"] = ""
        plugin_config["system_prompt"] = "STALE LEGACY"
        plugin_path.write_text(json.dumps(plugin_config), encoding="utf-8")

        refreshed = self.manager.get_prompt_profiles()
        self.assertTrue(refreshed["ok"], refreshed.get("error"))
        repaired = json.loads(plugin_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["prompt_profile_active_id"], refreshed["active_id"])
        self.assertEqual(
            repaired["system_prompt"], refreshed["prompts"][0]["prompt"]
        )
        self.assertEqual(
            json.loads(repaired["prompt_profiles"]), refreshed["prompts"]
        )
        self.assertIn("修复", refreshed["message"])

    def test_edge_whitespace_round_trip_through_api_and_plugin_config(self):
        self.manager.get_prompt_profiles()
        prompt = "  第一行\n第二行  \n"
        created = self.manager.update_prompt_profiles(
            {"action": "create", "name": "边缘空白", "prompt": prompt}
        )
        self.assertTrue(created["ok"], created.get("error"))
        new_profile = next(item for item in created["prompts"] if item["name"] == "边缘空白")
        self.assertEqual(new_profile["prompt"], prompt)
        activated = self.manager.update_prompt_profiles(
            {"action": "activate", "profile_id": new_profile["id"]}
        )
        self.assertTrue(activated["ok"], activated.get("error"))
        self.assertEqual(self.store.load_config()["prompt"], prompt)
        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_config = json.loads(plugin_path.read_text(encoding="utf-8"))
        self.assertEqual(plugin_config["system_prompt"], prompt)
        synced = json.loads(plugin_config["prompt_profiles"])
        self.assertEqual(
            next(item for item in synced if item["id"] == new_profile["id"])["prompt"],
            prompt,
        )

    def test_100_profiles_at_12000_chars_fit_schema_and_plugin_state(self):
        profiles = [
            {
                "id": f"p{i:03d}",
                "name": f"P{i}",
                "prompt": "x" * PROMPT_PROFILE_MAX_PROMPT_CHARS,
                "pinned": i == PROMPT_PROFILE_MAX_COUNT - 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
            for i in range(PROMPT_PROFILE_MAX_COUNT)
        ]
        config = self.store.load_config()
        config["prompt_profiles"] = profiles
        config["prompt_profiles_active_id"] = profiles[-1]["id"]
        self.store.save_config(config)
        result = self.manager.get_prompt_profiles()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(len(result["prompts"]), PROMPT_PROFILE_MAX_COUNT)
        self.assertTrue(all(len(item["prompt"]) == 12000 for item in result["prompts"]))
        self.assertEqual(result["active_id"], profiles[-1]["id"])

        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_config = json.loads(plugin_path.read_text(encoding="utf-8"))
        serialized = plugin_config["prompt_profiles"]
        self.assertLessEqual(len(serialized), PROMPT_PROFILES_MAX_JSON_CHARS)
        synced = json.loads(serialized)
        self.assertEqual(len(synced), 100)
        self.assertTrue(all(len(item["prompt"]) == 12000 for item in synced))

        schema = json.loads(
            (Path(__file__).resolve().parent.parent / "plugins" / "qqbot_enhancer" / "_conf_schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertGreaterEqual(
            schema["prompt_profiles"]["max_length"], PROMPT_PROFILES_MAX_JSON_CHARS
        )

    def test_nine_profiles_at_12000_are_accepted_by_plugin_config_api(self):
        profiles = [
            {
                "id": f"p{i:03d}",
                "name": f"P{i}",
                "prompt": "x" * PROMPT_PROFILE_MAX_PROMPT_CHARS,
                "pinned": i == 0,
            }
            for i in range(9)
        ]
        values = {"prompt_profiles": json.dumps(profiles, ensure_ascii=False)}
        result = self.manager.set_plugin_config("qqbot_enhancer", values)
        self.assertTrue(result["ok"], result.get("error"))
        stored = json.loads(
            (self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(json.loads(stored["prompt_profiles"])), 9)

    def test_over_total_serialized_prompt_profiles_is_rejected(self):
        values = {"prompt_profiles": "x" * (PROMPT_PROFILES_MAX_JSON_CHARS + 1)}
        result = self.manager.set_plugin_config("qqbot_enhancer", values)
        self.assertFalse(result["ok"])
        self.assertIn(str(PROMPT_PROFILES_MAX_JSON_CHARS), result["error"])

    def test_pinned_profile_is_sorted_first_and_unique_on_read(self):
        profiles = [
            {
                "id": "p1",
                "name": "one",
                "prompt": "prompt one",
                "pinned": False,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "p2",
                "name": "two",
                "prompt": "prompt two",
                "pinned": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "p3",
                "name": "three",
                "prompt": "prompt three",
                "pinned": False,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        config = self.store.load_config()
        config["prompt_profiles"] = profiles
        config["prompt_profiles_active_id"] = "p2"
        self.store.save_config(config)
        result = self.manager.get_prompt_profiles()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual([item["id"] for item in result["prompts"]], ["p2", "p1", "p3"])
        self.assertEqual(sum(bool(item["pinned"]) for item in result["prompts"]), 1)
        self.assertEqual(result["default_prompt_id"], "p2")
        self.assertEqual(result["default_profile"]["id"], "p2")

    def test_activate_only_changes_pinned_default_not_conversation(self):
        self.manager.get_prompt_profiles()
        created = self.manager.update_prompt_profiles(
            {"action": "create", "name": "new", "prompt": "new prompt"}
        )
        new_id = created["prompts"][1]["id"]
        activated = self.manager.update_prompt_profiles(
            {"action": "activate", "profile_id": new_id}
        )
        self.assertTrue(activated["ok"], activated.get("error"))
        self.assertFalse(activated["conversation_pending"])
        self.assertFalse(activated["conversation_created"])
        self.assertEqual(activated["prompts"][0]["id"], new_id)
        self.assertEqual(activated["default_profile"]["prompt"], "new prompt")
        self.assertIn("新会话默认模板", activated["message"])
        self.assertIn("当前会话不会被重置", activated["message"])

    def test_plugin_config_backup_is_created_private_and_readable(self):
        saved = self.manager.set_plugin_config(
            "qqbot_enhancer",
            {"safe_tools_enabled": False, "memory_mode": "astrbot"},
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        primary = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        backup = primary.with_name(primary.name + ".bak")
        self.assertTrue(primary.exists())
        self.assertTrue(backup.exists())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            json.loads(primary.read_text(encoding="utf-8")),
            json.loads(backup.read_text(encoding="utf-8")),
        )

    def test_plugin_config_get_falls_back_to_backup_without_overwriting(self):
        saved = self.manager.set_plugin_config(
            "qqbot_enhancer",
            {"safe_tools_enabled": False, "memory_mode": "astrbot"},
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        primary = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        backup = primary.with_name(primary.name + ".bak")
        primary.write_text('{"broken": ', encoding="utf-8")
        corrupt_before = primary.read_text(encoding="utf-8")
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            loaded = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertTrue(loaded["recovered"])
        self.assertEqual(loaded["recovery_source"], "backup")
        self.assertIn("请检查并保存", loaded["message"])
        self.assertIn("只有显式保存", loaded["warning"])
        self.assertTrue(Path(loaded["corrupt_snapshot"]).exists())
        self.assertIs(loaded["values"]["safe_tools_enabled"], False)
        self.assertEqual(loaded["values"]["memory_mode"], "astrbot")
        self.assertEqual(primary.read_text(encoding="utf-8"), corrupt_before)
        self.assertTrue(backup.exists())

    def test_concurrent_atomic_enhancer_config_writes_keep_primary_and_backup_valid(self):
        import threading

        primary = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        errors: list[BaseException] = []

        def writer(index: int):
            try:
                DeployManager._write_private_json(
                    primary,
                    {
                        "memory_mode": "astrbot" if index % 2 else "direct",
                        "system_prompt": f"prompt-{index}",
                        "writer": index,
                    },
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        backup = primary.with_name(primary.name + ".bak")
        primary_data = json.loads(primary.read_text(encoding="utf-8"))
        backup_data = json.loads(backup.read_text(encoding="utf-8"))
        self.assertIn(primary_data["memory_mode"], {"direct", "astrbot"})
        self.assertIn(backup_data["memory_mode"], {"direct", "astrbot"})
        self.assertIn("writer", primary_data)
        self.assertIn("writer", backup_data)

    def test_memory_mode_uses_last_good_backup_and_does_not_overwrite_corrupt_file(self):
        self.manager.set_memory_mode("astrbot")
        store_config = self.store.load_config()
        store_config["memory_mode"] = "direct"  # mirror must not win over backup
        self.store.save_config(store_config)
        primary = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        backup = primary.with_name(primary.name + ".bak")
        self.assertTrue(backup.exists())
        primary.write_text('{"broken": ', encoding="utf-8")
        corrupt_before = primary.read_text(encoding="utf-8")
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            reopened = DeployManager(
                store=Store(self.root), docker=DockerController(self.root)
            )
            memory = reopened.get_memory_mode()
        self.assertEqual(memory["mode"], "astrbot")
        self.assertIn("快照", memory["error"])
        self.assertEqual(primary.read_text(encoding="utf-8"), corrupt_before)
        self.assertIsNotNone(reopened._enhancer_config_cache)

    def test_memory_mode_without_backup_keeps_studio_current_mode(self):
        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text('{"broken": ', encoding="utf-8")
        backup = plugin_path.with_name(plugin_path.name + ".bak")
        if backup.exists():
            backup.unlink()
        config = self.store.load_config()
        config["memory_mode"] = "astrbot"
        self.store.save_config(config)
        corrupt_before = plugin_path.read_text(encoding="utf-8")
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            reopened = DeployManager(
                store=Store(self.root), docker=DockerController(self.root)
            )
            memory = reopened.get_memory_mode()
        self.assertEqual(memory["mode"], "astrbot")
        self.assertIn("未写入默认 direct", memory["error"])
        self.assertEqual(plugin_path.read_text(encoding="utf-8"), corrupt_before)

    def test_plugin_config_recovery_uses_cache_when_backup_missing(self):
        saved = self.manager.set_plugin_config(
            "qqbot_enhancer",
            {"safe_tools_enabled": True, "memory_mode": "astrbot"},
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        primary = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        backup = primary.with_name(primary.name + ".bak")
        backup.unlink()
        primary.write_text('{"broken": ', encoding="utf-8")
        corrupt_before = primary.read_text(encoding="utf-8")
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            loaded = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertTrue(loaded["recovered"])
        self.assertEqual(loaded["recovery_source"], "cache")
        self.assertEqual(loaded["values"]["memory_mode"], "astrbot")
        self.assertEqual(primary.read_text(encoding="utf-8"), corrupt_before)
        self.assertTrue(Path(loaded["corrupt_snapshot"]).exists())

    def test_plugin_config_recovery_uses_studio_mirror_without_fake_direct(self):
        primary = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.write_text('{"broken": ', encoding="utf-8")
        backup = primary.with_name(primary.name + ".bak")
        if backup.exists():
            backup.unlink()
        config = self.store.load_config()
        config["memory_mode"] = "astrbot"
        config["prompt"] = "Studio mirror prompt"
        self.store.save_config(config)
        # Use a fresh manager so no in-memory plugin config cache exists.
        reopened = DeployManager(
            store=Store(self.root), docker=DockerController(self.root)
        )
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            loaded = reopened.get_plugin_config("qqbot_enhancer")
        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertTrue(loaded["recovered"])
        self.assertEqual(loaded["recovery_source"], "studio")
        self.assertEqual(loaded["values"]["memory_mode"], "astrbot")
        self.assertEqual(loaded["values"]["system_prompt"], "Studio mirror prompt")
        self.assertEqual(primary.read_text(encoding="utf-8"), '{"broken": ')

    def test_plugin_config_recovery_uses_schema_defaults_last(self):
        plugin_id = "recovery_schema_plugin"
        plugin_dir = self.root / "data" / "astrbot" / "plugins" / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "_conf_schema.json").write_text(
            json.dumps(
                {
                    "custom_flag": {
                        "type": "bool",
                        "default": True,
                    }
                }
            ),
            encoding="utf-8",
        )
        config_path = (
            self.root / "data" / "astrbot" / "config" / f"{plugin_id}_config.json"
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('{"custom_flag": ', encoding="utf-8")
        backup = config_path.with_name(config_path.name + ".bak")
        if backup.exists():
            backup.unlink()
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            loaded = self.manager.get_plugin_config(plugin_id)
        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertTrue(loaded["recovered"])
        self.assertEqual(loaded["recovery_source"], "schema")
        self.assertIs(loaded["values"]["custom_flag"], True)
        self.assertTrue(Path(loaded["corrupt_snapshot"]).exists())

    def test_memory_mode_without_any_history_is_unknown_not_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary = (
                root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
            )
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text('{"broken": ', encoding="utf-8")
            corrupt_before = primary.read_text(encoding="utf-8")
            with mock.patch("qqbot_studio.deployer.time.sleep"):
                manager = DeployManager(
                    store=Store(root), docker=DockerController(root)
                )
                memory = manager.get_memory_mode()
            after_read = primary.read_text(encoding="utf-8")
        self.assertEqual(memory["mode"], "unknown")
        self.assertEqual(memory["memory_mode"], "unknown")
        self.assertIn("无法确定", memory["message"])
        self.assertIn("未写入默认 direct", memory["error"])
        self.assertEqual(after_read, corrupt_before)

    def test_consecutive_corrupt_plugin_get_keeps_ok_and_saves_repair(self):
        primary = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.write_text('{"broken": ', encoding="utf-8")
        with mock.patch("qqbot_studio.deployer.time.sleep"):
            first = self.manager.get_plugin_config("qqbot_enhancer")
            second = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(first["recovered"])
        self.assertTrue(second["recovered"])
        self.assertIn(second["recovery_source"], {"backup", "cache", "studio", "schema"})
        saved = self.manager.set_plugin_config(
            "qqbot_enhancer", {"safe_tools_enabled": False}
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        repaired = json.loads(primary.read_text(encoding="utf-8"))
        self.assertIs(repaired["safe_tools_enabled"], False)
        corrupt_snapshots = list(primary.parent.glob(primary.name + ".corrupt-*"))
        self.assertTrue(corrupt_snapshots)

    def test_old_plugin_system_prompt_migrates_before_store_prompt(self):
        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text(
            json.dumps({"system_prompt": "插件旧人格\n第二行"}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = self.manager.get_prompt_profiles()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["prompts"][0]["prompt"], "插件旧人格\n第二行")

    def test_create_activate_update_delete_keeps_one_pinned_and_plugin_sync(self):
        initial = self.manager.get_prompt_profiles()
        first_id = initial["active_id"]
        prompt = "角色A\n- 规则1\n- 规则2\n"
        created = self.manager.update_prompt_profiles(
            {"action": "create", "name": "角色A", "prompt": prompt}
        )
        self.assertTrue(created["ok"], created.get("error"))
        self.assertEqual(len(created["prompts"]), 2)
        second = next(item for item in created["prompts"] if item["id"] != first_id)

        activated = self.manager.update_prompt_profiles(
            {"action": "activate", "profile_id": second["id"]}
        )
        self.assertTrue(activated["ok"], activated.get("error"))
        self.assertEqual(activated["active_id"], second["id"])
        self.assertEqual(sum(bool(item["pinned"]) for item in activated["prompts"]), 1)
        self.assertEqual(activated["conversation_created"], False)
        self.assertEqual(self.store.load_config()["prompt"], prompt)

        plugin_config = json.loads(
            (self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plugin_config["prompt_profile_active_id"], second["id"])
        self.assertEqual(plugin_config["system_prompt"], prompt)
        synced = json.loads(plugin_config["prompt_profiles"])
        self.assertEqual(len(synced), 2)
        self.assertEqual(next(item for item in synced if item["id"] == second["id"])["prompt"], prompt)

        updated_prompt = "角色A v2\n\n保留空行\n"
        updated = self.manager.update_prompt_profiles(
            {"action": "update", "profile_id": second["id"], "prompt": updated_prompt}
        )
        self.assertTrue(updated["ok"], updated.get("error"))
        self.assertEqual(updated["prompts"][0]["id"], second["id"])
        self.assertEqual(updated["prompts"][0]["prompt"], updated_prompt)
        self.assertTrue(updated["prompts"][0]["pinned"])
        self.assertEqual(self.store.load_config()["prompt"], updated_prompt)

        deleted = self.manager.update_prompt_profiles(
            {"action": "delete", "profile_id": first_id}
        )
        self.assertTrue(deleted["ok"], deleted.get("error"))
        self.assertEqual(len(deleted["prompts"]), 1)
        rejected = self.manager.update_prompt_profiles(
            {"action": "delete", "profile_id": deleted["active_id"]}
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("最后一个", rejected["message"])

    def test_activate_deleted_profile_and_invalid_action_are_safe(self):
        self.manager.get_prompt_profiles()
        created = self.manager.update_prompt_profiles(
            {"action": "create", "name": "临时", "prompt": "临时提示词"}
        )
        temp_id = created["prompts"][1]["id"]
        activated = self.manager.update_prompt_profiles(
            {"action": "activate", "profile_id": temp_id}
        )
        self.assertTrue(activated["ok"])
        deleted = self.manager.update_prompt_profiles(
            {"action": "delete", "profile_id": temp_id}
        )
        self.assertTrue(deleted["ok"])
        self.assertEqual(len(deleted["prompts"]), 1)
        self.assertEqual(deleted["active_id"], deleted["prompts"][0]["id"])
        invalid = self.manager.update_prompt_profiles({"action": "unknown"})
        self.assertFalse(invalid["ok"])
        self.assertIn("action", invalid["message"])

    def test_activate_sets_new_session_default_without_pending_conversation(self):
        config = self.store.load_config()
        config["memory_mode"] = "astrbot"
        self.store.save_config(config)
        plugin_config_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_config_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_config_path.write_text(
            json.dumps({"memory_mode": "astrbot", "system_prompt": "astrbot prompt"}),
            encoding="utf-8",
        )
        self.manager.get_prompt_profiles()
        created = self.manager.update_prompt_profiles(
            {"action": "create", "name": "B", "prompt": "B prompt"}
        )
        profile_id = created["prompts"][1]["id"]
        result = self.manager.update_prompt_profiles(
            {"action": "activate", "profile_id": profile_id}
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(result["conversation_pending"])
        self.assertFalse(result["conversation_created"])
        self.assertEqual(result["prompts"][0]["id"], profile_id)
        self.assertTrue(result["prompts"][0]["pinned"])
        self.assertEqual(result["default_prompt_id"], profile_id)
        self.assertEqual(result["default_profile"]["id"], profile_id)
        self.assertIn("新会话默认模板", result["message"])
        self.assertIn("当前会话不会被重置", result["message"])
        self.assertIn("restart_required", result)


    def _mark_deployed_with_stale_plugin(self):
        config = self.store.load_config()
        config["deployed"] = True
        config["ports"] = {"astrbot_webui": 6185}
        self.store.save_config(config)
        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text(
            json.dumps(
                {
                    "prompt_profiles": "[]",
                    "prompt_profile_active_id": "",
                    "system_prompt": "STALE LEGACY",
                }
            ),
            encoding="utf-8",
        )
        return plugin_path

    def test_restart_required_persists_across_consecutive_gets(self):
        self._mark_deployed_with_stale_plugin()
        with mock.patch.object(self.manager, "_memory_api_client", return_value=None):
            first = self.manager.get_prompt_profiles()
        reopened = DeployManager(
            store=Store(self.root), docker=DockerController(self.root)
        )
        with mock.patch.object(reopened, "_memory_api_client", return_value=None):
            second = reopened.get_prompt_profiles()
        for result in (first, second):
            self.assertTrue(result["ok"], result.get("error"))
            self.assertTrue(result["restart_required"])
            self.assertFalse(result["api_available"])
            self.assertTrue(result["prompt_plugin_sync_pending"])
            self.assertIn("重启", result["message"])
            self.assertIn("热重载", " ".join(result["warnings"]))
            self.assertTrue(result["prompt_plugin_sync_pending_at"])
        self.assertEqual(
            first["prompt_plugin_sync_pending_at"],
            second["prompt_plugin_sync_pending_at"],
        )
        saved = self.store.load_config()
        self.assertTrue(saved["prompt_plugin_sync_pending"])
        self.assertTrue(saved["prompt_plugin_sync_pending_at"])
        self.assertEqual(self.store.config_path.stat().st_mode & 0o777, 0o600)

    def test_api_recovery_and_matching_live_state_clears_pending(self):
        self._mark_deployed_with_stale_plugin()
        with mock.patch.object(self.manager, "_memory_api_client", return_value=None):
            first = self.manager.get_prompt_profiles()
        self.assertTrue(first["restart_required"])

        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        live_state = json.loads(plugin_path.read_text(encoding="utf-8"))
        client = object()
        with mock.patch.object(
            self.manager, "_memory_api_client", return_value=client
        ), mock.patch.object(
            self.manager,
            "_get_plugin_config_via_api",
            return_value=live_state,
        ), mock.patch.object(
            self.manager,
            "_save_plugin_config_via_api",
            side_effect=AssertionError("matching live state must not be re-saved"),
        ):
            recovered = self.manager.get_prompt_profiles()
        self.assertTrue(recovered["ok"], recovered.get("error"))
        self.assertFalse(recovered["restart_required"])
        self.assertTrue(recovered["api_available"])
        self.assertFalse(recovered["prompt_plugin_sync_pending"])
        saved = self.store.load_config()
        self.assertFalse(saved["prompt_plugin_sync_pending"])
        self.assertEqual(saved["prompt_plugin_sync_pending_at"], "")

    def test_undeployed_instance_never_reports_restart_pending(self):
        plugin_path = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text(
            json.dumps(
                {
                    "prompt_profiles": "[]",
                    "prompt_profile_active_id": "",
                    "system_prompt": "STALE",
                }
            ),
            encoding="utf-8",
        )
        first = self.manager.get_prompt_profiles()
        second = self.manager.get_prompt_profiles()
        for result in (first, second):
            self.assertTrue(result["ok"], result.get("error"))
            self.assertFalse(result["restart_required"])
            self.assertFalse(result["prompt_plugin_sync_pending"])
        self.assertFalse(self.store.load_config()["prompt_plugin_sync_pending"])

    def test_local_plugin_write_failure_does_not_claim_synced_or_pending(self):
        self._mark_deployed_with_stale_plugin()
        with mock.patch.object(self.manager, "_memory_api_client", return_value=None), mock.patch.object(
            self.manager,
            "_write_private_json",
            side_effect=OSError("permission denied"),
        ):
            result = self.manager.get_prompt_profiles()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(result["prompt_plugin_sync_pending"])
        self.assertIn("无法写入", " ".join(result["warnings"]))
        self.assertFalse(self.store.load_config()["prompt_plugin_sync_pending"])

    def test_mutation_api_unavailable_sets_pending_semantics(self):
        config = self.store.load_config()
        config["deployed"] = True
        config["ports"] = {"astrbot_webui": 6185}
        self.store.save_config(config)
        with mock.patch.object(self.manager, "_memory_api_client", return_value=None):
            result = self.manager.update_prompt_profiles(
                {"action": "create", "name": "pending", "prompt": "pending prompt"}
            )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["restart_required"])
        self.assertTrue(result["prompt_plugin_sync_pending"])
        self.assertIn("热重载", result["message"])
        self.assertTrue(self.store.load_config()["prompt_plugin_sync_pending"])


if __name__ == "__main__":
    unittest.main()
