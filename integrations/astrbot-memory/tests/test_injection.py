"""Injection: bounded memory block lands in the LLM request exactly once."""

from __future__ import annotations

import unittest

from _support import (
    FakeConversation,
    FakeEvent,
    FakeRequest,
    MemoryTestCase,
    group_event,
    private_event,
    raw_memory_config,
)


class InjectionTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service(max_chars=220, max_items=2)
        self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅，不吃香菜", confidence=0.9)
        self.service.add("user", "aiocqhttp:1001", "偏好.咖啡", "用户喜欢手冲咖啡", confidence=0.9)

    def test_block_is_appended_to_the_system_prompt(self):
        event = private_event("1001", "推荐一家火锅店")
        req = FakeRequest("推荐一家火锅店", "# Persona\n你是点餐助手")
        injected = self.memory.inject_memory(event, req, {}, service=self.service)
        self.assertGreater(injected, 0)
        self.assertTrue(req.system_prompt.startswith("# Persona"))
        self.assertIn("<long_term_memory", req.system_prompt)
        self.assertIn("麻辣火锅", req.system_prompt)
        self.assertEqual(self.memory.integration.last_injected_ids(event)[0], self.service.export_all()[0]["id"])

    def test_injection_happens_once_per_turn(self):
        event = private_event("1001", "推荐一家火锅店")
        req = FakeRequest("推荐一家火锅店", "")
        first = self.memory.inject_memory(event, req, {}, service=self.service)
        second = self.memory.inject_memory(event, req, {}, service=self.service)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(req.system_prompt.count("<long_term_memory"), 1)

    def test_budget_is_enforced_in_the_prompt(self):
        event = private_event("1001", "火锅 咖啡 香菜")
        req = FakeRequest("火锅 咖啡 香菜", "")
        self.memory.inject_memory(event, req, {}, service=self.service)
        block = req.system_prompt[req.system_prompt.index("<long_term_memory"):]
        self.assertLessEqual(len(block), 220)
        self.assertLessEqual(block.count("- ["), 2)

    def test_event_message_str_is_used_when_prompt_is_empty(self):
        event = private_event("1001", "我想吃麻辣火锅")
        req = FakeRequest("", "")
        self.assertGreater(self.memory.inject_memory(event, req, {}, service=self.service), 0)
        self.assertIn("麻辣火锅", req.system_prompt)

    def test_group_turn_does_not_receive_private_memories(self):
        event = group_event("2001", "1001", "推荐火锅")
        req = FakeRequest("推荐火锅", "")
        self.assertEqual(self.memory.inject_memory(event, req, {}, service=self.service), 0)
        self.assertEqual(req.system_prompt, "")

    def test_conversation_and_persona_ids_are_bound_from_the_request(self):
        conversation = FakeConversation(cid="conv-42", persona_id="chef")
        self.service.add("conversation", "conv-42", "话题.预算", "本次会话预算 80 元", confidence=0.9)
        self.service.add("persona", "chef", "偏好.语气", "人格偏好简短回答", confidence=0.9)
        event = FakeEvent(user_id="1001", message_str="预算和语气")
        req = FakeRequest("预算 语气", "", conversation=conversation)
        self.memory.inject_memory(event, req, {}, service=self.service)
        self.assertIn("80 元", req.system_prompt)
        self.assertIn("简短回答", req.system_prompt)

    def test_disabled_memory_leaves_the_prompt_untouched(self):
        disabled = self.make_service(enable=False)
        event = private_event("1001", "火锅")
        req = FakeRequest("火锅", "原系统提示")
        self.assertEqual(self.memory.inject_memory(event, req, {}, service=disabled), 0)
        self.assertEqual(req.system_prompt, "原系统提示")

    def test_singleton_service_reads_the_config_section(self):
        config = raw_memory_config(self.db_path, max_chars=200)
        event = private_event("1001", "火锅")
        req = FakeRequest("火锅", "")
        self.memory.reset_memory_services()
        injected = self.memory.inject_memory(event, req, config)
        self.assertGreater(injected, 0)
        cached = self.memory.get_memory_service(config)
        self.assertIs(cached, self.memory.get_memory_service(config))


if __name__ == "__main__":
    unittest.main()
