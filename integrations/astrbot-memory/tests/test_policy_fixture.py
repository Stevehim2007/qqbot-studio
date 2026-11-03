"""Frozen expectation: fixtures/memory_policy.example.json resolves as documented."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from _support import MemoryTestCase, group_event, private_event

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "memory_policy.example.json"


class PolicyFixtureTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.section = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.service = self.make_service(policy=self.section)

    def policy_for_group(self, group_id, platform="aiocqhttp"):
        event = group_event(group_id)
        event.platform = platform
        return self.service.policy_for(self.context(event))

    def policy_for_private(self):
        return self.service.policy_for(self.context(private_event("1001")))

    def test_private_entry(self):
        policy = self.policy_for_private()
        self.assertEqual((policy.mode, policy.scope, policy.source), ("long_term", "user", "private"))

    def test_bare_and_prefixed_group_keys(self):
        bare = self.policy_for_group("838796241")
        self.assertEqual((bare.mode, bare.scope), ("long_term", "group"))
        prefixed = self.policy_for_group("12345")
        self.assertEqual((prefixed.mode, prefixed.scope), ("long_term", "group_user"))
        self.assertTrue(prefixed.extract)
        self.assertFalse(prefixed.inject)  # 只写不读是合法配置

    def test_per_group_budget_is_frozen(self):
        policy = self.policy_for_group("999999")
        self.assertEqual((policy.scope, policy.extract, policy.inject), ("user", False, True))
        self.assertEqual((policy.max_items, policy.max_chars), (3, 400))

    def test_unlisted_group_and_invalid_entry_fail_safe(self):
        self.assertEqual(self.policy_for_group("2001").mode, "conversation")
        broken = self.policy_for_group("2002")
        self.assertEqual(broken.mode, "conversation")
        self.assertTrue(broken.error)

    def test_unknown_top_level_keys_are_ignored(self):
        self.assertEqual(self.policy_for_group("838796241").mode, "long_term")


if __name__ == "__main__":
    unittest.main()
