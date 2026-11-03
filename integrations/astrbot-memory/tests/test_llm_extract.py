"""Optional LLM extraction: stub transport only (no real, paid request).

The client must be: opt-in, background-only (never on the reply path),
budgeted, secret-shy and fail-open to the heuristic extractor.
"""

from __future__ import annotations

import json
import logging
import unittest

from _support import AsyncMemoryTestCase, MemoryTestCase, group_event, private_event


class StubTransport:
    """Records requests and returns a canned chat-completions body."""

    def __init__(self, content: str = '{"facts":[]}', status: int = 200, raise_exc=None):
        self.content = content
        self.status = status
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        payload = json.loads(body.decode("utf-8"))
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        document = {"choices": [{"message": {"content": self.content}}]}
        return self.status, json.dumps(document).encode("utf-8")


def llm_section(**overrides) -> dict:
    section = {
        "enable": True,
        "endpoint": "http://stub.invalid/v1/chat/completions",
        "model": "stub-model",
        "timeout_s": 2.0,
        "max_input_chars": 120,
        "max_candidates": 3,
        "max_calls": 5,
    }
    section.update(overrides)
    return section


FACTS = json.dumps(
    {
        "facts": [
            {"key": "偏好.烧烤", "content": "用户喜欢烧烤", "kind": "preference",
             "importance": 0.7, "confidence": 0.8},
            {"key": "居住地", "content": "用户住在长春", "kind": "profile"},
        ]
    }
)


class LLMSettingsTests(MemoryTestCase):
    def test_default_section_is_disabled(self):
        settings, errors = self.memory.LLMSettings.from_mapping({})
        self.assertFalse(settings.enable)
        self.assertEqual(errors, [])

    def test_invalid_values_are_reported_and_clamped(self):
        settings, errors = self.memory.LLMSettings.from_mapping(
            {"enable": True, "endpoint": "ftp://nope", "timeout_s": 900, "max_calls": -3}
        )
        self.assertTrue(errors)
        self.assertEqual(settings.endpoint, "")
        self.assertLessEqual(settings.timeout_s, 60.0)
        self.assertEqual(settings.max_calls, 0)

    def test_builder_refuses_when_endpoint_is_missing(self):
        client, errors = self.memory.build_llm_extractor({"enable": True})
        self.assertIsNone(client)
        self.assertTrue(errors)

    def test_enabled_service_without_endpoint_stays_heuristic_only(self):
        service = self.make_service(llm_extract=True, llm={"enable": True})
        self.assertFalse(service.llm_stats()["enabled"])
        self.assertTrue(service.llm_stats()["errors"])


class LLMExtractionTests(AsyncMemoryTestCase):
    def _service_with_stub(self, stub, **overrides):
        service = self.make_service(llm_extract=True, **overrides)
        client, errors = self.memory.build_llm_extractor(llm_section(**overrides.pop("llm", {})),
                                                         transport=stub)
        self.assertIsNotNone(client, errors)
        service.set_llm_extractor(client)
        return service, client

    async def test_facts_are_stored_from_the_background_worker(self):
        stub = StubTransport(FACTS)
        service, client = self._service_with_stub(stub)
        context = self.context(private_event("1001"))
        self.assertTrue(service.enqueue_turn(context, "随便聊聊吃喝"))
        self.assertEqual(stub.calls, [], "回复路径不得同步调用模型")
        await service.drain()
        self.assertEqual(len(stub.calls), 1)
        keys = {row["key"] for row in service.export("user", "aiocqhttp:1001")}
        self.assertIn("偏好.烧烤", keys)
        sources = {row["source"] for row in service.export("user", "aiocqhttp:1001")}
        self.assertIn("llm", sources)

    async def test_model_candidates_get_the_policy_scope_and_speaker(self):
        stub = StubTransport(FACTS)
        service, _ = self._service_with_stub(
            stub, policy={"default_mode": "long_term", "groups": {"2001": {"mode": "long_term", "scope": "group_member"}}}
        )
        context = self.context(group_event("2001", "1001"))
        self.assertTrue(service.enqueue_turn(context, "随便聊聊吃喝"))
        await service.drain()
        rows = service.export("group_member", "aiocqhttp:2001:1001")
        self.assertTrue(rows)
        self.assertTrue(all(row["sender_id"] == "1001" for row in rows))

    async def test_secret_message_is_never_sent(self):
        stub = StubTransport(FACTS)
        service, _ = self._service_with_stub(stub)
        context = self.context(private_event("1001"))
        service.enqueue_turn(context, "记住 password is hunter2")
        await service.drain()
        self.assertEqual(stub.calls, [], "含密钥的轮次不得发给第三方端点")
        self.assertEqual(service.export_all(), [])

    async def test_secrets_in_model_output_are_dropped(self):
        stub = StubTransport(
            json.dumps({"facts": [{"key": "pw", "content": "my api key is abc123456789"}]})
        )
        service, _ = self._service_with_stub(stub)
        service.enqueue_turn(self.context(private_event("1001")), "聊聊别的事")
        await service.drain()
        self.assertEqual(service.export_all(), [])

    async def test_http_error_falls_back_to_heuristic(self):
        stub = StubTransport(FACTS, status=500)
        service, client = self._service_with_stub(stub)
        service.enqueue_turn(self.context(private_event("1001")), "我喜欢麻辣火锅")
        await service.drain()
        keys = {row["key"] for row in service.export_all()}
        self.assertTrue(any("偏好" in key for key in keys), "heuristic 仍应写入")
        self.assertTrue(client.last_error)
        self.assertEqual(service.processed_turns, 1)

    async def test_transport_exception_falls_back(self):
        stub = StubTransport(FACTS, raise_exc=TimeoutError("slow"))
        service, client = self._service_with_stub(stub)
        service.enqueue_turn(self.context(private_event("1001")), "我喜欢麻辣火锅")
        await service.drain()
        self.assertIn("TimeoutError", client.last_error)
        self.assertTrue(service.export_all())

    async def test_malformed_model_output_is_ignored(self):
        stub = StubTransport("这不是 JSON")
        service, client = self._service_with_stub(stub)
        service.enqueue_turn(self.context(private_event("1001")), "我喜欢麻辣火锅")
        await service.drain()
        self.assertEqual(len(service.export_all()), 1)  # heuristic row only
        self.assertTrue(client.last_error)

    async def test_call_budget_stops_extra_calls(self):
        stub = StubTransport(FACTS)
        service, client = self._service_with_stub(stub, llm={"max_calls": 2})
        for index in range(4):
            service.enqueue_turn(self.context(private_event(str(1000 + index))), "我喜欢麻辣火锅")
        await service.drain()
        self.assertEqual(len(stub.calls), 2)
        self.assertTrue(client.budget_exhausted)

    async def test_input_is_truncated_before_sending(self):
        stub = StubTransport(FACTS)
        service, _ = self._service_with_stub(stub)
        service.enqueue_turn(self.context(private_event("1001")), "我喜欢" + "火锅" * 200)
        await service.drain()
        sent = stub.calls[0]["payload"]["messages"][-1]["content"]
        self.assertLessEqual(len(sent), 120)

    async def test_logs_never_contain_the_message(self):
        stub = StubTransport(FACTS, status=503)
        service, _ = self._service_with_stub(stub)
        messages: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: messages.append(record.getMessage())
        logger = logging.getLogger("astrbot.memory")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        service.enqueue_turn(self.context(private_event("1001")), "密码是hunter2")
        await service.drain()
        self.assertFalse(
            [message for message in messages if "hunter2" in message],
            "日志中不得出现消息正文/密钥",
        )


if __name__ == "__main__":
    unittest.main()
