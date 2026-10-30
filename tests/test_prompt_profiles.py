"""Prompt profile persistence and API contract regression tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qqbot_studio.deployer import DeployManager
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
        self.assertEqual(updated["prompts"][1]["prompt"], updated_prompt)
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

    def test_astrbot_activation_is_deferred_to_plugin_next_turn(self):
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
        self.assertTrue(result["conversation_pending"])
        self.assertFalse(result["conversation_created"])
        self.assertIn("下一轮", result["message"])
        self.assertIn("restart_required", result)


if __name__ == "__main__":
    unittest.main()
