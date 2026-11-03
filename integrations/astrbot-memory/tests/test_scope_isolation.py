"""Scope isolation: no cross-group / cross-private leakage."""

from __future__ import annotations

import unittest

from _support import MemoryTestCase, group_event, private_event


class ScopeIsolationTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service()

    def test_private_memory_never_reaches_a_group(self):
        alice_private = self.context(private_event("1001"))
        self.remember(self.service, alice_private, "我最喜欢吃麻辣火锅")

        group_view = self.context(group_event("2001", "1001"))
        block, records = self.service.retrieve(group_view, "麻辣火锅")
        self.assertEqual(records, [])
        self.assertEqual(block, "")

    def test_group_memory_never_reaches_private_chat(self):
        group_context = self.context(group_event("2001", "1001"))
        self.remember(self.service, group_context, "我们群里最喜欢点麻辣烫外卖")

        private_view = self.context(private_event("1001"))
        block, records = self.service.retrieve(private_view, "麻辣烫外卖")
        self.assertEqual(records, [])
        self.assertEqual(block, "")

    def test_groups_do_not_leak_into_each_other(self):
        self.remember(self.service, self.context(group_event("2001")), "我们群里最喜欢吃麻辣火锅")
        other_group = self.context(group_event("2002"))
        _, records = self.service.retrieve(other_group, "麻辣火锅")
        self.assertEqual(records, [])
        own_group = self.context(group_event("2001"))
        _, own_records = self.service.retrieve(own_group, "麻辣火锅")
        self.assertEqual(len(own_records), 1)

    def test_other_users_do_not_see_my_private_memories(self):
        self.remember(self.service, self.context(private_event("1001")), "我喜欢吃麻辣火锅")
        _, records = self.service.retrieve(self.context(private_event("1002")), "麻辣火锅")
        self.assertEqual(records, [])

    def test_conversation_scope_binds_to_conversation_id(self):
        event = private_event("1001")
        context = self.memory.build_context(event, None)
        conversation = self.memory.MemoryContext(
            platform=context.platform,
            user_id=context.user_id,
            is_private=True,
            conversation_id="conv-a",
        )
        self.service.add("conversation", "conv-a", "话题.火锅", "本次会话在讨论火锅预算", confidence=0.9)
        self.service.add("conversation", "conv-b", "话题.火锅", "另一个会话在讨论火锅设备", confidence=0.9)

        _, records = self.service.retrieve(conversation, "火锅预算")
        self.assertEqual([record.scope_id for record in records], ["conv-a"])

    def test_persona_and_global_scopes_are_readable_where_bound(self):
        self.service.add("persona", "chef", "偏好.语气", "人格设定偏好简短回答", confidence=0.9)
        self.service.add("global", "all", "规则.忌口", "全局规则：不要推荐含花生的菜", confidence=0.9)
        event = private_event("1001")
        context = self.memory.MemoryContext(
            platform=event.platform,
            user_id=event.user_id,
            is_private=True,
            persona_id="chef",
        )
        _, records = self.service.retrieve(context, "简短回答 花生 忌口")
        scopes = {record.scope_type for record in records}
        self.assertEqual(scopes, {"persona", "global"})

        no_persona = self.memory.MemoryContext(
            platform=event.platform, user_id=event.user_id, is_private=True
        )
        _, records = self.service.retrieve(no_persona, "简短回答 花生 忌口")
        self.assertEqual({record.scope_type for record in records}, {"global"})

    def test_export_and_forget_stay_inside_the_scope(self):
        self.service.add("group", "aiocqhttp:2001", "偏好.辣", "群里偏辣", confidence=0.9)
        self.service.add("group", "aiocqhttp:2002", "偏好.辣", "另一个群偏辣", confidence=0.9)
        self.service.add("user", "aiocqhttp:1001", "偏好.辣", "用户偏辣", confidence=0.9)

        exported = self.service.export("group", "aiocqhttp:2001")
        self.assertEqual([item["content"] for item in exported], ["群里偏辣"])
        self.assertEqual(self.service.forget("group", "aiocqhttp:2001"), 1)
        self.assertEqual(len(self.service.export("group", "aiocqhttp:2002")), 1)
        self.assertEqual(len(self.service.export("user", "aiocqhttp:1001")), 1)


if __name__ == "__main__":
    unittest.main()
