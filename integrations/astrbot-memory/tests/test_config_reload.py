"""Hot config reload: cached services must follow memory config changes."""

from __future__ import annotations

import unittest

from _support import MemoryTestCase, group_event, memory_config, private_event


class ConfigReloadTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.config = memory_config(
            self.db_path,
            policy={"default_mode": "long_term", "groups": {"2001": {"mode": "long_term", "scope": "group"}}},
        )
        self.service = self.memory.get_memory_service(self.config)
        self.addCleanup(self.memory.reset_memory_services)
        self.private = self.memory.build_context(private_event("1001"), None)
        self.group = self.memory.build_context(group_event("2001"), None)
        self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅", confidence=0.9)
        self.service.add("group", "aiocqhttp:2001", "偏好.火锅", "本群喜欢麻辣火锅", confidence=0.9)

    def _reload(self):
        return self.memory.get_memory_service(self.config)

    def test_enable_toggles_without_restart(self):
        self.assertNotEqual(self.service.retrieve(self.private, "麻辣火锅")[1], [])
        self.config["memory"]["enable"] = False
        self.assertIs(self._reload(), self.service)
        self.assertEqual(self.service.retrieve(self.private, "麻辣火锅"), ("", []))
        self.assertFalse(self.service.enqueue_turn(self.private, "我喜欢麻辣火锅"))
        self.config["memory"]["enable"] = True
        self._reload()
        self.assertNotEqual(self.service.retrieve(self.private, "麻辣火锅")[1], [])

    def test_min_confidence_reload_changes_retrieval(self):
        self.service.add("user", "aiocqhttp:1001", "偏好.烧烤", "用户喜欢烧烤", confidence=0.42)
        self.assertEqual(len(self.service.retrieve(self.private, "烧烤")[1]), 1)
        self.config["memory"]["min_confidence"] = 0.9
        self._reload()
        self.assertEqual(self.service.retrieve(self.private, "烧烤"), ("", []))

    def test_read_scope_flags_reload(self):
        self.assertEqual(len(self.service.retrieve(self.group, "麻辣火锅")[1]), 1)
        self.config["memory"]["read_group"] = False
        self._reload()
        self.assertEqual(self.service.retrieve(self.group, "麻辣火锅"), ("", []))
        self.config["memory"]["read_group"] = True
        self._reload()
        self.assertEqual(len(self.service.retrieve(self.group, "麻辣火锅")[1]), 1)

    def test_policy_reload_is_immediate(self):
        self.assertEqual(self.service.policy_for(self.group).mode, "long_term")
        self.config["memory"]["policy"] = {"default_mode": "conversation"}
        self._reload()
        self.assertEqual(self.service.policy_for(self.group).mode, "conversation")
        self.assertEqual(self.service.retrieve(self.group, "麻辣火锅"), ("", []))

    def test_max_records_per_scope_reload_applies_to_new_writes(self):
        self.config["memory"]["max_records_per_scope"] = 20
        self._reload()
        for index in range(25):
            self.service.add("user", "aiocqhttp:1001", f"笔记.{index}", f"第 {index} 条笔记", confidence=0.9)
        self.assertEqual(len(self.service.export("user", "aiocqhttp:1001")), 20)
        self.config["memory"]["max_records_per_scope"] = 500
        self._reload()
        self.service.add("user", "aiocqhttp:1001", "笔记.extra", "新的一条笔记", confidence=0.9)
        self.assertEqual(len(self.service.export("user", "aiocqhttp:1001")), 21)


class DeadConfigKeysTests(MemoryTestCase):
    def test_write_scope_keys_are_gone_from_defaults(self):
        defaults = self.memory.DEFAULT_MEMORY_CONFIG
        self.assertNotIn("write_private_scope", defaults)
        self.assertNotIn("write_group_scope", defaults)

    def test_old_keys_in_user_config_are_ignored_and_policy_wins(self):
        service = self.make_service(
            write_private_scope="none",
            write_group_scope="none",
            policy={"default_mode": "long_term", "groups": {"2001": {"mode": "long_term", "scope": "group"}}},
        )
        self.assertNotIn("write_private_scope", service.config)
        self.assertNotIn("write_group_scope", service.config)
        context = self.context(group_event("2001"))
        self.assertEqual(service._write_scope(context, service.policy_for(context)), ("group", "aiocqhttp:2001"))


if __name__ == "__main__":
    unittest.main()
