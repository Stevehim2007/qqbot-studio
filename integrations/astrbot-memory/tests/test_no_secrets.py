"""Memory must never persist or inject credentials / API keys / QQ passwords."""

from __future__ import annotations

import json
import unittest

from _support import AsyncMemoryTestCase, FakeRequest, MemoryTestCase, private_event


class NoSecretsTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service()
        self.context = self.memory.build_context(private_event("1001"), None)

    def test_message_with_api_key_is_not_mined(self):
        from astrbot.core.memory.extract import heuristic_extract

        for text in (
            "记住：我的 DeepSeek API key 是 sk-abcdef1234567890abcdef",
            "我的密码是 hunter2，帮我记一下",
            "QQ密码: 123456789",
            "记住 token: ghp_abcdefghijklmnopqrstuvwxyz",
        ):
            self.assertEqual(heuristic_extract(text, self.context), [], text)

    def test_explicit_write_is_scrubbed(self):
        action, record = self.service.add(
            "user", "aiocqhttp:1001", "账号.密码", "用户的登录密码是 hunter2"
        )
        self.assertEqual(action, "created")
        self.assertNotIn("hunter2", record.content)
        self.assertIn("[已隐去]", record.content)

    def test_secret_in_key_is_scrubbed(self):
        self.service.add("user", "aiocqhttp:1001", "密钥.sk-abcdef1234567890", "用户提供过密钥")
        exported = json.dumps(self.service.export_all(), ensure_ascii=False)
        self.assertNotIn("sk-abcdef1234567890", exported)

    def test_stored_and_injected_text_has_no_secret(self):
        self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅")
        self.service.add("user", "aiocqhttp:1001", "账号.令牌", "用户的令牌是 Bearer abcdefghijklmnop")
        from _support import FakeRequest

        event = private_event("1001", "火锅 令牌")
        req = FakeRequest("火锅 令牌", "")
        self.memory.inject_memory(event, req, {}, service=self.service)
        self.assertIn("麻辣火锅", req.system_prompt)
        for marker in ("Bearer", "abcdefghijklmnop", "hunter2", "ghp_", "sk-"):
            self.assertNotIn(marker, req.system_prompt)

    def test_english_credential_messages_are_not_mined(self):
        """P1-1 regression: English copula forms must never produce a draft."""

        from astrbot.core.memory.extract import heuristic_extract

        for text in (
            "my password is hunter2",
            "password is Tr0ub4dor&3",
            "my api key is 9f8e7d6c5b4a",
            "the token is abcdef123456",
            "the password for my NAS is hunter2",
            "pin is 4321",
            "记住 password is hunter2",
        ):
            self.assertEqual(heuristic_extract(text, self.context), [], text)

    def test_english_secrets_are_scrubbed_on_write(self):
        action, record = self.service.add(
            "user", "aiocqhttp:1001", "账号.密码", "my password is hunter2"
        )
        self.assertEqual(action, "created")
        self.assertNotIn("hunter2", record.content)
        self.assertIn("[已隐去]", record.content)
        dumped = json.dumps(self.service.export_all(), ensure_ascii=False)
        self.assertNotIn("hunter2", dumped)
        self.assertNotIn("password is", dumped.lower())

    def test_drafts_with_english_secrets_never_reach_store_or_injection(self):
        from astrbot.core.memory.types import Candidate

        self.service.store_candidates(
            [
                Candidate(key="note.1", content="用户要求记住：password is hunter2", scope_type="user", scope_id="aiocqhttp:1001"),
                Candidate(key="note.2", content="用户要求记住：my api key is 9f8e7d6c5b4a", scope_type="user", scope_id="aiocqhttp:1001"),
            ]
        )
        dumped = json.dumps(self.service.export_all(), ensure_ascii=False)
        for marker in ("hunter2", "9f8e7d6c5b4a", "password is", "api key is"):
            self.assertNotIn(marker, dumped.lower())

        event = private_event("1001", "笔记")
        req = FakeRequest("笔记", "")
        self.memory.inject_memory(event, req, {}, service=self.service)
        for marker in ("hunter2", "9f8e7d6c5b4a"):
            self.assertNotIn(marker, req.system_prompt)

    def test_legit_english_prose_is_preserved(self):
        from astrbot.core.memory.extract import heuristic_extract
        from astrbot.core.memory.text import contains_secret

        for text in (
            "I like hotpot",
            "the secret is the broth",
            "we gave the chef a token of appreciation",
            "token bucket rate limiting is useful",
            "password policy is strict here",
        ):
            self.assertFalse(contains_secret(text), text)
        self.assertTrue(heuristic_extract("I like hotpot", self.context))

    def test_export_contains_no_secret(self):
        self.service.add("user", "aiocqhttp:1001", "账号.密码", "用户密码是 hunter2")
        dumped = json.dumps(self.service.export_all(), ensure_ascii=False)
        self.assertNotIn("hunter2", dumped)


class NoSecretsQueueTests(AsyncMemoryTestCase):
    async def test_queued_english_secret_turn_writes_nothing(self):
        service = self.make_service()
        context = self.memory.build_context(private_event("1001"), None)
        # A credential-looking message is skipped as a whole (conservative), even
        # when it also contains an otherwise rememberable fact.
        self.assertTrue(service.enqueue_turn(context, "my password is hunter2，另外我喜欢麻辣火锅"))
        await service.drain()
        self.assertEqual(service.export_all(), [])
        self.assertEqual(service.retrieve(context, "麻辣火锅"), ("", []))


if __name__ == "__main__":
    unittest.main()
