"""Speaker dimension: the opt-in ``group_member`` scope.

``group_user`` keeps its documented semantics (read group+sender, write the
group).  ``group_member`` is a separate scope that reads *and* writes only the
speaker's rows in that one group -- never another member's, never another
group's, never a private chat.
"""

from __future__ import annotations

import unittest

from _support import AsyncMemoryTestCase, MemoryTestCase, group_event, private_event

POLICY = {
    "default_mode": "conversation",
    "groups": {
        "2001": {"mode": "long_term", "scope": "group_member"},
        "2002": {"mode": "long_term", "scope": "group_user"},
        "2003": {"mode": "long_term", "scope": "group"},
        "2004": {"mode": "long_term", "scope": "group_member", "extract": False},
    },
    "private": {"mode": "long_term", "scope": "user"},
}


class SpeakerScopeContextTests(MemoryTestCase):
    def test_group_member_scope_embeds_group_and_sender(self):
        context = self.context(group_event("2001", "1001"))
        self.assertEqual(context.group_member_scope, ("group_member", "aiocqhttp:2001:1001"))

    def test_private_chat_has_no_group_member_scope(self):
        self.assertIsNone(self.context(private_event("1001")).group_member_scope)

    def test_group_member_scope_requires_opt_in_when_listing_readable_scopes(self):
        context = self.context(group_event("2001", "1001"))
        default = context.readable_scopes()
        self.assertNotIn(("group_member", "aiocqhttp:2001:1001"), default)
        opted_in = context.readable_scopes(group_members=True)
        self.assertIn(("group_member", "aiocqhttp:2001:1001"), opted_in)


class SpeakerScopePolicyTests(AsyncMemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service(policy=POLICY)

    async def _remember(self, group_id: str, user_id: str, text: str):
        context = self.context(group_event(group_id, user_id))
        self.assertTrue(self.service.enqueue_turn(context, text))
        await self.service.drain()
        return context

    async def test_group_member_write_creates_speaker_rows(self):
        await self._remember("2001", "1001", "我喜欢麻辣火锅")
        rows = self.service.export("group_member")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["scope_id"], "aiocqhttp:2001:1001")
        self.assertEqual(row["sender_id"], "1001")
        # No copy is written to the shared group scope (single write).
        self.assertEqual(self.service.export("group", "aiocqhttp:2001"), [])

    async def test_group_member_reads_only_that_speaker(self):
        await self._remember("2001", "1001", "我喜欢麻辣火锅")
        own = self.service.search(self.context(group_event("2001", "1001")), "麻辣火锅")
        self.assertEqual(len(own), 1)
        other = self.service.search(self.context(group_event("2001", "1002")), "麻辣火锅")
        self.assertEqual(other, [])
        block, records = self.service.retrieve(
            self.context(group_event("2001", "1002")), "麻辣火锅"
        )
        self.assertEqual((block, records), ("", []))

    async def test_speaker_rows_never_cross_groups_or_private_chat(self):
        await self._remember("2001", "1001", "我喜欢麻辣火锅")
        # Same user id, different group: isolated.
        self.assertEqual(
            self.service.search(self.context(group_event("2006", "1001")), "麻辣火锅"), []
        )
        # Same user id in a private chat: never reads group speaker rows.
        self.assertEqual(
            self.service.search(self.context(private_event("1001")), "麻辣火锅"), []
        )
        # Another group's policy cannot see it either.
        self.assertEqual(
            self.service.search(self.context(group_event("2002", "1001")), "麻辣火锅"), []
        )

    async def test_group_user_write_semantics_are_unchanged(self):
        await self._remember("2002", "1001", "我们群里喜欢麻辣火锅")
        self.assertEqual(self.service.export("group_member"), [])
        group_rows = self.service.export("group", "aiocqhttp:2002")
        self.assertEqual(len(group_rows), 1)
        self.assertEqual(group_rows[0]["sender_id"], "1001")
        # group_user reads both scopes for the speaker and the group.
        own = self.service.search(self.context(group_event("2002", "1001")), "麻辣火锅")
        self.assertEqual(len(own), 1)
        same_group_other = self.service.search(
            self.context(group_event("2002", "1002")), "麻辣火锅"
        )
        self.assertEqual(len(same_group_other), 1)

    async def test_extract_false_never_writes_speaker_rows(self):
        context = self.context(group_event("2004", "1001"))
        self.assertFalse(self.service.enqueue_turn(context, "我喜欢麻辣火锅"))
        await self.service.drain()
        self.assertEqual(self.service.export("group_member"), [])
        self.assertEqual(self.service.gated_turns, 1)

    async def test_group_scope_policy_does_not_read_speaker_rows(self):
        await self._remember("2001", "1001", "我喜欢麻辣火锅")
        # The same group with a plain scope="group" policy sees nothing of it.
        self.assertEqual(
            self.service.search(self.context(group_event("2003", "1001")), "麻辣火锅"), []
        )

    async def test_delete_and_export_match_the_exact_scope(self):
        await self._remember("2001", "1001", "我喜欢麻辣火锅")
        await self._remember("2001", "1002", "我喜欢烤肉")
        self.assertEqual(len(self.service.export("group_member", "aiocqhttp:2001:1001")), 1)
        self.assertEqual(len(self.service.export("group_member", "aiocqhttp:2001:1002")), 1)
        self.assertEqual(self.service.export("group_member", "aiocqhttp:2001:1003"), [])
        removed = self.service.forget("group_member", "aiocqhttp:2001:1001")
        self.assertEqual(removed, 1)
        self.assertEqual(self.service.export("group_member", "aiocqhttp:2001:1001"), [])
        self.assertEqual(len(self.service.export("group_member", "aiocqhttp:2001:1002")), 1)

    async def test_export_requires_an_explicit_scope_type(self):
        with self.assertRaises(ValueError):
            self.service.export("")


class SpeakerScopeConfigTests(MemoryTestCase):
    def test_private_policy_rejects_group_member_fails_safe(self):
        table = self.memory.parse_policy(
            {"private": {"mode": "long_term", "scope": "group_member"}}
        )
        self.assertTrue(table.errors)
        policy = table.entry_for(is_private=True)
        self.assertEqual(policy.mode, "conversation")
        self.assertIn("私聊只支持 scope=user", policy.error)

    def test_group_member_is_an_allowed_policy_scope(self):
        table = self.memory.parse_policy(
            {"groups": {"2001": {"mode": "long_term", "scope": "group_member"}}}
        )
        self.assertEqual(table.errors, ())
        self.assertEqual(
            table.entry_for(group_id="2001", is_private=False).scope, "group_member"
        )

    def test_invalid_scope_still_degrades_to_conversation(self):
        table = self.memory.parse_policy(
            {"groups": {"2001": {"mode": "long_term", "scope": "group_speaker"}}}
        )
        self.assertTrue(table.errors)
        self.assertEqual(
            table.entry_for(group_id="2001", is_private=False).mode, "conversation"
        )


if __name__ == "__main__":
    unittest.main()
