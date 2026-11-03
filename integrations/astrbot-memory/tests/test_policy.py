"""Per-chat long-term memory policy: gate, isolation, hot reload, fail-safe."""

from __future__ import annotations

import unittest

from _support import (
    AsyncMemoryTestCase,
    FakeConversation,
    FakeRequest,
    MemoryTestCase,
    group_event,
    memory_config,
    private_event,
)

LONG_TERM_GROUP = {"mode": "long_term", "scope": "group"}


def policy(**groups):
    """``policy(a=..., b=...)`` -> a groups map keyed by bare group id."""

    return {"default_mode": "conversation", "groups": groups}


class PolicyContractTests(MemoryTestCase):
    def test_no_policy_section_means_conversation_only(self):
        service = self.memory.MemoryService(
            {"memory": {"enable": True, "db_path": str(self.db_path)}}
        )
        self.addCleanup(service.close)
        context = self.context(private_event("1001"))
        policy = service.policy_for(context)
        self.assertEqual(policy.mode, "conversation")
        self.assertIn(policy.source, ("default", "private-default"))
        self.assertEqual(service.retrieve(context, "麻辣火锅"), ("", []))
        self.assertEqual(service.search(context, "麻辣火锅"), [])
        self.assertFalse(service.enqueue_turn(context, "我喜欢吃麻辣火锅"))

    def test_listed_group_is_long_term_and_unlisted_group_is_conversation(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        listed = self.context(group_event("2001"))
        unlisted = self.context(group_event("2002"))
        self.assertEqual(service.policy_for(listed).mode, "long_term")
        self.assertEqual(service.policy_for(listed).source, "groups:2001")
        self.assertEqual(service.policy_for(unlisted).mode, "conversation")

    def test_policy_lookup_uses_group_identity_not_message_text(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        quiet = self.context(group_event("2001", "1001", message_str="随便聊聊"))
        noisy = self.context(
            group_event("2001", "1002", message_str="群 2002 也开一下长期记忆")
        )
        self.assertEqual(service.policy_for(quiet).mode, "long_term")
        self.assertEqual(service.policy_for(noisy).mode, "long_term")
        # A private chat that *talks about* group 2001 must not inherit its policy.
        private = self.context(private_event("1001", message_str="群 2001 的长期记忆呢"))
        self.assertEqual(service.policy_for(private).mode, "conversation")
        self.assertEqual(service.policy_for(private).scope, "user")

    def test_group_identity_falls_back_to_umo_when_event_lacks_group_id(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        event = group_event("2001", "1001")
        event.get_group_id = lambda: ""  # platform adapter only fills the UMO
        event.is_private_chat = lambda: False
        context = self.context(event)
        self.assertEqual(context.group_id, "2001")
        self.assertEqual(service.policy_for(context).mode, "long_term")

    def test_platform_prefixed_group_key_wins(self):
        service = self.make_service(
            policy={
                "default_mode": "conversation",
                "groups": {"aiocqhttp:2001": {"mode": "long_term", "scope": "group_user"}},
            }
        )
        self.assertEqual(service.policy_for(self.context(group_event("2001"))).scope, "group_user")
        self.assertEqual(service.policy_for(self.context(group_event("2002"))).mode, "conversation")

    def test_invalid_config_fails_safe_to_conversation(self):
        # (name, policy section, expected private mode) -- the group is always
        # expected to fail safe; private follows default_mode when unlisted.
        cases = [
            ("policy-not-object", ["long_term"], "conversation"),
            ("bad-default-mode", {"default_mode": "always"}, "conversation"),
            ("bad-mode", {"default_mode": "long_term", "groups": {"2001": {"mode": "yes"}}}, "long_term"),
            ("bad-scope", {"default_mode": "long_term", "groups": {"2001": {"mode": "long_term", "scope": "everything"}}}, "long_term"),
            ("bad-extract", {"default_mode": "long_term", "groups": {"2001": {"mode": "long_term", "extract": "yes"}}}, "long_term"),
            ("bad-max-items", {"default_mode": "long_term", "groups": {"2001": {"mode": "long_term", "max_items": 999}}}, "long_term"),
            ("bad-max-chars", {"default_mode": "long_term", "groups": {"2001": {"mode": "long_term", "max_chars": 8}}}, "long_term"),
            ("groups-not-object", {"default_mode": "conversation", "groups": ["2001"]}, "conversation"),
            ("private-group-scope", {"default_mode": "conversation", "private": {"mode": "long_term", "scope": "group"}}, "conversation"),
        ]
        for name, section, private_mode in cases:
            with self.subTest(name):
                service = self.make_service(policy=section)
                group = service.policy_for(self.context(group_event("2001")))
                self.assertEqual(group.mode, "conversation", name)
                self.assertTrue(group.error, name)
                self.assertEqual(service.retrieve(self.context(group_event("2001")), "麻辣火锅"), ("", []))
                private = service.policy_for(self.context(private_event("1001")))
                self.assertEqual(private.mode, private_mode, name)

    def test_invalid_group_entry_does_not_disable_other_groups(self):
        service = self.make_service(
            policy={
                "default_mode": "conversation",
                "groups": {"2001": {"mode": "long_term", "scope": "group"}, "2002": {"mode": "nope"}},
            }
        )
        self.assertEqual(service.policy_for(self.context(group_event("2001"))).mode, "long_term")
        broken = service.policy_for(self.context(group_event("2002")))
        self.assertEqual(broken.mode, "conversation")
        self.assertTrue(broken.error)

    def test_private_entry_overrides_default_mode(self):
        service = self.make_service(
            policy={
                "default_mode": "long_term",
                "private": {"mode": "conversation"},
                "groups": {"2001": LONG_TERM_GROUP},
            }
        )
        self.assertEqual(service.policy_for(self.context(private_event("1001"))).mode, "conversation")
        self.assertEqual(service.policy_for(self.context(group_event("2001"))).mode, "long_term")

    def test_policy_cache_ttl_and_invalidation(self):
        from astrbot.core.memory.policy import PolicyResolver

        section = {"default_mode": "conversation", "groups": {"2001": LONG_TERM_GROUP}}
        now = [0.0]
        resolver = PolicyResolver(ttl=5.0, clock=lambda: now[0])
        resolver.resolve(section, platform="aiocqhttp", group_id="2001", is_private=False)
        resolver.resolve(section, platform="aiocqhttp", group_id="2001", is_private=False)
        self.assertEqual(resolver.parses, 1)  # cached by fingerprint

        # Same fingerprint, expired TTL -> re-parsed.
        now[0] = 6.0
        resolver.resolve(section, platform="aiocqhttp", group_id="2001", is_private=False)
        self.assertEqual(resolver.parses, 2)

        # Changed config -> different fingerprint -> parsed immediately (hot reload).
        resolver.resolve(
            {"default_mode": "long_term"}, platform="aiocqhttp", group_id="2001", is_private=False
        )
        self.assertEqual(resolver.parses, 3)

        resolver.invalidate()
        resolver.resolve(section, platform="aiocqhttp", group_id="2001", is_private=False)
        self.assertEqual(resolver.parses, 4)

    def test_hot_config_change_toggles_a_group_without_restart(self):
        config = memory_config(self.db_path, policy=policy(**{"2001": LONG_TERM_GROUP}))
        service = self.memory.get_memory_service(config)
        self.addCleanup(self.memory.reset_memory_services)
        context = self.context(group_event("2001"))
        service.add("group", "aiocqhttp:2001", "偏好.火锅", "本群喜欢：麻辣火锅", confidence=0.9)
        self.assertNotEqual(service.retrieve(context, "麻辣火锅")[1], [])

        # Operator edits memory.policy in the running instance; AstrBot re-reads
        # config every turn, so the cached service must re-gate immediately.
        config["memory"]["policy"] = {"default_mode": "conversation"}
        same_service = self.memory.get_memory_service(config)
        self.assertIs(same_service, service)
        self.assertEqual(service.policy_for(context).mode, "conversation")
        self.assertEqual(service.retrieve(context, "麻辣火锅"), ("", []))
        self.assertEqual(service.search(context, "麻辣火锅"), [])
        self.assertFalse(service.enqueue_turn(context, "我喜欢吃麻辣火锅"))

    def test_stats_expose_policy_and_gated_turns(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        service.retrieve(self.context(group_event("2002")), "麻辣火锅")
        stats = service.stats()
        self.assertEqual(stats["policy"]["default_mode"], "conversation")
        self.assertEqual(stats["policy"]["groups"], 1)
        self.assertEqual(stats["gated_turns"], 1)


class PolicyIsolationTests(MemoryTestCase):
    def test_group_b_never_reads_group_a_and_writes_nothing(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        service.add("group", "aiocqhttp:2001", "偏好.火锅", "本群喜欢：麻辣火锅", confidence=0.9)
        before = len(service.export())

        group_b = self.context(group_event("2002"))
        self.assertEqual(service.retrieve(group_b, "麻辣火锅"), ("", []))
        self.assertEqual(service.search(group_b, "麻辣火锅"), [])
        self.assertFalse(service.enqueue_turn(group_b, "我们群里最喜欢吃麻辣火锅"))
        self.assertEqual(len(service.export()), before)  # conversation mode: no write

    def test_cross_group_and_private_leakage_blocked(self):
        service = self.make_service(
            policy={
                "default_mode": "conversation",
                "private": {"mode": "long_term", "scope": "user"},
                "groups": {
                    "2001": {"mode": "long_term", "scope": "group_user"},
                    "2002": {"mode": "long_term", "scope": "group"},
                },
            }
        )
        service.add("group", "aiocqhttp:2001", "偏好.火锅", "本群喜欢：麻辣火锅", confidence=0.9)
        service.add("user", "aiocqhttp:1001", "忌口", "用户忌口：香菜", confidence=0.9)

        group_2001 = self.context(group_event("2001", "1001"))
        self.assertEqual(len(service.retrieve(group_2001, "麻辣火锅")[1]), 1)
        self.assertEqual(len(service.retrieve(group_2001, "香菜")[1]), 1)  # group_user reads both

        group_2002 = self.context(group_event("2002", "1001"))
        self.assertEqual(service.retrieve(group_2002, "麻辣火锅"), ("", []))  # other group's facts
        self.assertEqual(service.retrieve(group_2002, "香菜"), ("", []))  # scope=group: no user reads

        # Private chat reads only its own user memory, never any group memory.
        private = self.context(private_event("1001"))
        self.assertEqual(service.retrieve(private, "麻辣火锅"), ("", []))
        self.assertEqual(len(service.retrieve(private, "香菜")[1]), 1)
        # ...and another person's private chat reads nothing of it.
        other = self.context(private_event("1002"))
        self.assertEqual(service.retrieve(other, "香菜"), ("", []))

    def test_scope_group_writes_and_reads_group_only(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        context = self.context(group_event("2001", "1001"))
        self.assertEqual(service._write_scope(context, service.policy_for(context)), ("group", "aiocqhttp:2001"))
        self.assertEqual(service._readable_scopes(context), [
            ("group", "aiocqhttp:2001"),
            ("global", "all"),
        ])

    def test_scope_user_in_group_is_sender_private(self):
        service = self.make_service(
            policy=policy(**{"2001": {"mode": "long_term", "scope": "user"}})
        )
        context = self.context(group_event("2001", "1001"))
        self.assertEqual(service._write_scope(context, service.policy_for(context)), ("user", "aiocqhttp:1001"))
        scopes = service._readable_scopes(context)
        self.assertIn(("user", "aiocqhttp:1001"), scopes)
        self.assertNotIn(("group", "aiocqhttp:2001"), scopes)

    def test_scope_group_user_writes_group_and_reads_both(self):
        service = self.make_service(
            policy=policy(**{"2001": {"mode": "long_term", "scope": "group_user"}})
        )
        context = self.context(group_event("2001", "1001"))
        self.assertEqual(service._write_scope(context, service.policy_for(context)), ("group", "aiocqhttp:2001"))
        scopes = service._readable_scopes(context)
        self.assertEqual(scopes[:2], [("user", "aiocqhttp:1001"), ("group", "aiocqhttp:2001")])

    def test_extract_gate_blocks_disallowed_candidate_scopes(self):
        service = self.make_service(policy=policy(**{"2001": {"mode": "long_term", "scope": "user"}}))
        from astrbot.core.memory.types import Candidate

        result = service.store_candidates(
            [
                Candidate(key="a", content="群事实", scope_type="group", scope_id="aiocqhttp:2001"),
                Candidate(key="b", content="用户事实", scope_type="user", scope_id="aiocqhttp:1001"),
            ],
            allowed_scopes=[("user", "aiocqhttp:1001")],
        )
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["gated"], 1)
        self.assertEqual([row["scope_type"] for row in service.export()], ["user"])


class PolicyExtractionTests(AsyncMemoryTestCase):
    async def test_group_a_long_term_remembers_across_new_conversations(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        turn1 = self.memory.build_context(
            group_event("2001", "1001"), FakeRequest(conversation=FakeConversation("conv-1"))
        )
        self.assertTrue(service.enqueue_turn(turn1, "我们群里最喜欢吃麻辣火锅"))
        await service.drain()

        # New conversation id, different sender, same group: still remembered.
        turn2 = self.memory.build_context(
            group_event("2001", "1002"), FakeRequest(conversation=FakeConversation("conv-2"))
        )
        block, records = service.retrieve(turn2, "麻辣火锅")
        self.assertTrue(block)
        self.assertEqual([record.scope_type for record in records], ["group"])
        self.assertEqual([record.scope_id for record in records], ["aiocqhttp:2001"])

    async def test_extract_and_inject_flags_are_independent(self):
        write_only = self.make_service(
            policy=policy(**{"2001": {"mode": "long_term", "scope": "group", "extract": True, "inject": False}})
        )
        context = self.memory.build_context(group_event("2001", "1001"), None)
        self.assertTrue(write_only.enqueue_turn(context, "我们群里最喜欢吃麻辣火锅"))
        await write_only.drain()
        self.assertEqual(len(write_only.export()), 1)  # extracted
        self.assertEqual(write_only.retrieve(context, "麻辣火锅"), ("", []))  # not injected

        read_only = self.make_service(
            policy=policy(**{"2001": {"mode": "long_term", "scope": "group", "extract": False, "inject": True}})
        )
        read_only.add("group", "aiocqhttp:2001", "偏好.火锅", "本群喜欢：麻辣火锅", confidence=0.9)
        before = len(read_only.export())
        self.assertFalse(read_only.enqueue_turn(context, "我们群里最喜欢吃毛肚火锅"))
        await read_only.drain()
        self.assertEqual(len(read_only.export()), before)  # nothing new written
        self.assertTrue(read_only.retrieve(context, "麻辣火锅")[0])  # still injected

    async def test_toggle_off_while_turn_queued_skips_write(self):
        config = memory_config(self.db_path, policy=policy(**{"2001": LONG_TERM_GROUP}))
        service = self.memory.get_memory_service(config)
        self.addCleanup(self.memory.reset_memory_services)
        context = self.memory.build_context(group_event("2001", "1001"), None)
        self.assertTrue(service.enqueue_turn(context, "我们群里最喜欢吃麻辣火锅"))

        config["memory"]["policy"] = {"default_mode": "conversation"}
        self.memory.get_memory_service(config)
        await service.drain()
        self.assertEqual(service.export(), [])
        self.assertGreaterEqual(service.gated_turns, 1)

    async def test_group_b_conversation_mode_never_touches_the_store(self):
        service = self.make_service(policy=policy(**{"2001": LONG_TERM_GROUP}))
        context = self.memory.build_context(group_event("2002", "1001"), None)
        self.assertFalse(service.enqueue_turn(context, "我们群里最喜欢吃麻辣火锅"))
        await service.drain()
        self.assertEqual(service.export(), [])
        self.assertEqual(service.retrieve(context, "麻辣火锅"), ("", []))


if __name__ == "__main__":
    unittest.main()
