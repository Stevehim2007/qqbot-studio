"""Regression tests for schema fallback, SnowLuma #sl, and prompt newlines."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.configgen import _snowluma_config
from qqbot_studio.deployer import DeployManager
from qqbot_studio.dockerctl import DockerController, DockerStatus
from qqbot_studio.store import Store


class PluginSchemaFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = DeployManager(
            store=Store(self.root),
            docker=DockerController(self.root),
        )
        self.instance_plugin_dir = (
            self.root / "data" / "astrbot" / "plugins" / "qqbot_enhancer"
        )
        self.local_config = (
            self.root / "data" / "astrbot" / "config" / "qqbot_enhancer_config.json"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_instance_without_local_schema_uses_readonly_bundled_fallback(self):
        result = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["schema_source"], "bundled")
        self.assertTrue(result["schema_path"].endswith(
            "plugins/qqbot_enhancer/_conf_schema.json"
        ))
        self.assertEqual(result["values"]["reply_probability"], 0.12)
        self.assertFalse(self.instance_plugin_dir.exists())

        saved = self.manager.set_plugin_config(
            "qqbot_enhancer", {"reply_probability": 0.5}
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        self.assertEqual(saved["schema_source"], "bundled")
        self.assertEqual(saved["values"]["reply_probability"], 0.5)
        self.assertTrue(self.local_config.exists())
        self.assertFalse(self.instance_plugin_dir.exists())

        reread = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(reread["ok"], reread.get("error"))
        self.assertEqual(reread["schema_source"], "bundled")
        self.assertEqual(reread["values"]["reply_probability"], 0.5)

    def test_instance_schema_takes_precedence_over_bundled_fallback(self):
        self.instance_plugin_dir.mkdir(parents=True, exist_ok=True)
        local_schema_path = self.instance_plugin_dir / "_conf_schema.json"
        local_schema_path.write_text(
            json.dumps(
                {
                    "local_only": {
                        "type": "bool",
                        "description": "仅实例 schema 字段",
                        "default": True,
                    }
                }
            ),
            encoding="utf-8",
        )
        result = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["schema_source"], "local")
        self.assertEqual(Path(result["schema_path"]), local_schema_path)
        self.assertIn("local_only", result["schema"])
        self.assertNotIn("image_search_enabled", result["schema"])

    def test_missing_both_schemas_returns_actionable_non_crash_error(self):
        missing_id = "no_such_bundled_plugin_123"
        result = self.manager.get_plugin_config(missing_id)
        self.assertFalse(result["ok"])
        self.assertIn("主项目内置", result["error"])
        self.assertIn("实例路径", result["error"])
        self.assertEqual(result["schema"], {})
        self.assertFalse(
            (self.root / "data" / "astrbot" / "plugins" / missing_id).exists()
        )

    def test_plugin_text_field_preserves_internal_newlines_on_save_and_reload(self):
        prompt = "第一行\n第二行\n\n第四行  \n"
        saved = self.manager.set_plugin_config(
            "qqbot_enhancer", {"system_prompt": prompt}
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        self.assertEqual(saved["values"]["system_prompt"], prompt)
        stored = json.loads(self.local_config.read_text(encoding="utf-8"))
        self.assertEqual(stored["system_prompt"], prompt)
        reloaded = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertEqual(reloaded["values"]["system_prompt"], prompt)


class SnowLumaStatusCommandTests(unittest.TestCase):
    def test_generated_config_has_disabled_status_command(self):
        config = _snowluma_config(
            onebot_token="onebot-token",
            http_token="http-token",
            ws_token="ws-token",
        )
        self.assertIs(config["statusCommand"]["enabled"], False)
        self.assertEqual(config["statusCommand"]["trigger"], "#sl")

    def test_old_onebot_files_are_migrated_without_losing_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "data" / "snowluma" / "config"
            config_dir.mkdir(parents=True)
            global_config = {
                "networks": {"wsClients": [{"name": "keep"}]},
                "statusCommand": {
                    "enabled": True,
                    "swallow": False,
                    "cooldownSeconds": 9,
                    "trigger": "#sl",
                    "custom": "keep-me",
                },
                "historySync": {"enabled": False},
            }
            account_config = {
                "uin": "10001",
                "statusCommand": {"enabled": True, "trigger": "#sl", "x": 7},
                "otherCommand": {"enabled": True, "trigger": "#other"},
            }
            global_path = config_dir / "onebot.json"
            account_path = config_dir / "onebot_10001.json"
            global_path.write_text(json.dumps(global_config), encoding="utf-8")
            account_path.write_text(json.dumps(account_config), encoding="utf-8")

            manager = DeployManager(
                store=Store(root),
                docker=DockerController(root),
            )
            migrated = manager.snowluma_status_migration
            self.assertEqual(
                sorted(Path(item).name for item in migrated["changed"]),
                ["onebot.json", "onebot_10001.json"],
            )
            self.assertEqual(migrated["errors"], [])

            saved_global = json.loads(global_path.read_text(encoding="utf-8"))
            self.assertIs(saved_global["statusCommand"]["enabled"], False)
            self.assertEqual(saved_global["statusCommand"]["trigger"], "#sl")
            self.assertEqual(saved_global["statusCommand"]["cooldownSeconds"], 9)
            self.assertEqual(saved_global["statusCommand"]["custom"], "keep-me")
            self.assertEqual(saved_global["networks"], global_config["networks"])
            self.assertEqual(saved_global["historySync"], {"enabled": False})

            saved_account = json.loads(account_path.read_text(encoding="utf-8"))
            self.assertIs(saved_account["statusCommand"]["enabled"], False)
            self.assertEqual(saved_account["statusCommand"]["x"], 7)
            self.assertEqual(
                saved_account["otherCommand"],
                {"enabled": True, "trigger": "#other"},
            )
            self.assertEqual(global_path.stat().st_mode & 0o777, 0o600)


    def test_status_refresh_migrates_old_files_created_after_manager_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = DeployManager(
                store=Store(root),
                docker=DockerController(root),
            )
            config_dir = root / "data" / "snowluma" / "config"
            config_dir.mkdir(parents=True)
            path = config_dir / "onebot.json"
            path.write_text(
                json.dumps(
                    {
                        "statusCommand": {
                            "enabled": True,
                            "trigger": "#sl",
                            "keep": "yes",
                        },
                        "other": {"keep": True},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                manager.docker,
                "detect",
                return_value=DockerStatus(False, False, False, False, detail="offline"),
            ):
                manager.get_status()
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(saved["statusCommand"]["enabled"], False)
            self.assertEqual(saved["statusCommand"]["trigger"], "#sl")
            self.assertEqual(saved["statusCommand"]["keep"], "yes")
            self.assertEqual(saved["other"], {"keep": True})


class PromptPreviewAndRoundTripTests(unittest.TestCase):
    def test_validate_prompt_preserves_internal_and_edge_newlines(self):
        prompt = "第一行\n第二行\n\n第四行\n"
        self.assertEqual(DeployManager._validate_prompt(prompt), prompt)

    def test_enhancer_config_writer_preserves_multiline_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            astrbot_data = Path(tmp) / "data" / "astrbot"
            prompt = "角色设定\n\n- 规则一\n- 规则二\n"
            DeployManager._write_enhancer_config(astrbot_data, prompt)
            path = astrbot_data / "config" / "qqbot_enhancer_config.json"
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["system_prompt"], prompt)

    def test_prompt_preview_css_preserves_whitespace(self):
        css = (
            Path(__file__).resolve().parent.parent
            / "qqbot_studio"
            / "static"
            / "styles.css"
        ).read_text(encoding="utf-8")
        match = re.search(r"\.prompt-preview\s*\{(?P<body>.*?)\}", css, re.S)
        self.assertIsNotNone(match)
        self.assertIn("white-space: pre-wrap", match.group("body"))


if __name__ == "__main__":
    unittest.main()
