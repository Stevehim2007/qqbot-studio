"""Memory must never persist or inject credentials / API keys / QQ passwords."""

from __future__ import annotations

import json
import unittest

from _support import MemoryTestCase, private_event


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
        exported = json.dumps(self.service.export(), ensure_ascii=False)
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

    def test_export_contains_no_secret(self):
        self.service.add("user", "aiocqhttp:1001", "账号.密码", "用户密码是 hunter2")
        dumped = json.dumps(self.service.export(), ensure_ascii=False)
        self.assertNotIn("hunter2", dumped)


if __name__ == "__main__":
    unittest.main()
