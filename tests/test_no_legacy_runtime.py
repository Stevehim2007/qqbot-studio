"""Final-release gate: the runtime must not install/import legacy plugins."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.deployer import DeployManager
from qqbot_studio.plugin_final_migration import FINAL_PLUGIN_IDS, LEGACY_PLUGIN_IDS

ROOT = Path(__file__).resolve().parents[1]
LEGACY = {"qqbot_enhancer", "qqbot_core_full"}


class FinalPluginSetTests(unittest.TestCase):
    def test_exact_seven_plugin_ids(self):
        self.assertEqual(
            FINAL_PLUGIN_IDS,
            (
                "qqbot_core_reply",
                "qqbot_safe_tools",
                "qqbot_web_reader",
                "qqbot_image_search",
                "qqbot_food",
                "qqbot_stickers",
                "qqbot_memory_commands",
            ),
        )
        self.assertEqual(set(LEGACY_PLUGIN_IDS), LEGACY)
        self.assertFalse(set(FINAL_PLUGIN_IDS) & LEGACY)

    def test_no_runtime_python_imports_legacy_plugins(self):
        allowed = {"plugin_final_migration.py"}
        for path in (ROOT / "qqbot_studio").glob("*.py"):
            if path.name in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertFalse(name in LEGACY or name.startswith("qqbot_enhancer."), path.name)
                    self.assertFalse(name.startswith("qqbot_core_full."), path.name)

    def test_deploy_scripts_never_copy_legacy_plugin_code(self):
        for relative in ("scripts/auto-update.sh", "scripts/post-update-test.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                self.assertNotIn("qqbot_enhancer", stripped, relative)
                self.assertNotIn("qqbot_core_full", stripped, relative)

    def test_fresh_deployment_seeds_reply_config_without_legacy_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data" / "astrbot"
            logs: list[str] = []
            DeployManager._seed_final_reply_config(data, "final prompt", logs.append)
            self.assertFalse((data / "plugins" / "qqbot_enhancer").exists())
            self.assertFalse((data / "plugins" / "qqbot_core_full").exists())
            seeded = json.loads(
                (data / "config" / "qqbot_core_reply_config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(seeded["system_prompt"], "final prompt")
            self.assertTrue(any("插件市场" in line for line in logs))

    def test_seed_does_not_install_legacy_when_redeployed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data" / "astrbot"
            config = data / "config" / "qqbot_core_reply_config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"system_prompt": "old", "enabled": True}), encoding="utf-8")
            logs: list[str] = []
            with mock.patch("qqbot_studio.deployer.shutil.copytree") as copytree:
                DeployManager._seed_final_reply_config(data, "new", logs.append)
            copytree.assert_not_called()
            value = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(value["system_prompt"], "new")
            self.assertTrue(value["enabled"])


if __name__ == "__main__":
    unittest.main()
