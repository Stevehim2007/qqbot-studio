"""Optional capabilities in the ``memory`` config: defaults, reload, stats."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from _support import MemoryTestCase, private_event

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class OptionalConfigDefaultsTests(MemoryTestCase):
    def test_shipped_defaults_keep_every_optional_feature_off(self):
        defaults = self.memory.DEFAULT_MEMORY_CONFIG
        self.assertFalse(defaults["llm_extract"])
        self.assertFalse(defaults["llm"]["enable"])
        self.assertFalse(defaults["semantic"]["enable"])
        self.assertTrue(defaults["reflect"]["backup"])
        self.assertEqual(defaults["semantic"]["provider"], "local")

    def test_example_config_fixture_is_valid_and_off_by_default(self):
        payload = json.loads((FIXTURES / "memory_config.example.json").read_text(encoding="utf-8"))
        section = payload["memory"]
        self.assertFalse(section["llm_extract"])
        self.assertFalse(section["llm"]["enable"])
        self.assertFalse(section["semantic"]["enable"])
        self.assertTrue(section["reflect"]["backup"])
        merged = self.memory.normalise_config(payload)
        self.assertEqual(merged["llm"]["max_calls"], 50)

    def test_nested_sections_are_deep_copied(self):
        config = {
            "memory": {
                "enable": True,
                "semantic": {"enable": True, "provider": "local", "dim": 16},
                "llm": {"enable": False},
                "reflect": {"backup": False},
            }
        }
        first = self.memory.normalise_config(config)
        first["semantic"]["dim"] = 999
        second = self.memory.normalise_config(config)
        self.assertEqual(second["semantic"]["dim"], 16)

    def test_invalid_optional_sections_do_not_break_the_service(self):
        service = self.make_service(
            llm="not-an-object", semantic=["nope"], reflect=42
        )
        self.assertTrue(service.enabled)
        self.assertFalse(service.llm_stats()["enabled"])
        self.assertFalse(service.semantic_stats()["enabled"])
        block, records = service.retrieve(self.context(private_event("1001")), "任意")
        self.assertEqual((block, records), ("", []))

    def test_stats_expose_optional_state_without_secrets(self):
        service = self.make_service(
            llm={"enable": False, "endpoint": "", "api_key_env": "ASTRBOT_MEMORY_LLM_API_KEY"},
            semantic={"enable": True, "provider": "local", "dim": 16},
        )
        stats = service.stats()
        self.assertIn("llm", stats)
        self.assertIn("semantic", stats)
        self.assertFalse(stats["llm"]["enabled"])
        self.assertTrue(stats["semantic"]["enabled"])
        serialized = json.dumps(stats, ensure_ascii=False)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("ASTRBOT_MEMORY_LLM_API_KEY", serialized)


class OptionalConfigReloadTests(MemoryTestCase):
    def test_refresh_config_enables_semantic_without_restart(self):
        service = self.make_service()
        self.assertFalse(service.semantic_stats()["enabled"])
        changed = service.refresh_config(
            {
                "memory": {
                    "enable": True,
                    "db_path": str(self.db_path),
                    "policy": {"default_mode": "long_term"},
                    "semantic": {"enable": True, "provider": "local", "dim": 16},
                }
            }
        )
        self.assertTrue(changed)
        self.assertTrue(service.semantic_stats()["enabled"])
        self.assertEqual(service.semantic_stats()["provider"], "local")

    def test_refresh_config_can_turn_semantic_off_again(self):
        service = self.make_service(
            semantic={"enable": True, "provider": "local", "dim": 16}
        )
        self.assertTrue(service.semantic_stats()["enabled"])
        service.refresh_config({"memory": {"enable": True, "db_path": str(self.db_path)}})
        self.assertFalse(service.semantic_stats()["enabled"])

    def test_explicit_extractor_survives_a_config_reload(self):
        service = self.make_service(llm_extract=True)
        calls = []

        async def extractor(text, context):
            calls.append(text)
            return []

        service.set_llm_extractor(extractor)
        service.refresh_config(
            {"memory": {"enable": True, "db_path": str(self.db_path), "llm_extract": False}}
        )
        self.assertIs(service._llm_extractor, extractor)

    def test_reflect_settings_are_clamped_from_config(self):
        service = self.make_service(
            reflect={"similarity": 5, "low_value_importance": -1, "backup": "yes"}
        )
        settings = self.memory.ReflectSettings.from_mapping(service.config["reflect"])
        self.assertLessEqual(settings.similarity, 1.0)
        self.assertGreaterEqual(settings.low_value_importance, 0.0)
        self.assertTrue(settings.backup, "非布尔 backup 必须回退到安全默认（先备份）")


if __name__ == "__main__":
    unittest.main()
