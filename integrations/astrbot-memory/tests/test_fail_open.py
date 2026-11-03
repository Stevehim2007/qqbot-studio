"""Fail-open: a broken memory DB or extractor must never break a chat reply."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from _support import (
    AsyncMemoryTestCase,
    FakeRequest,
    MemoryTestCase,
    private_event,
    raw_memory_config,
)


class FailOpenTests(MemoryTestCase):
    def test_unwritable_db_path_degrades_to_no_memory(self):
        blocker = self.db_path.parent / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        service = self.memory.MemoryService(raw_memory_config(blocker / "mem.db"))
        self.addCleanup(service.close)
        context = self.memory.build_context(private_event("1001"), None)
        self.assertEqual(service.retrieve(context, "麻辣火锅"), ("", []))
        self.assertTrue(service.last_error)
        self.memory.reset_memory_services()

    def test_corrupt_db_file_does_not_raise(self):
        self.db_path.write_bytes(b"this is not a sqlite database")
        service = self.memory.MemoryService(raw_memory_config(self.db_path))
        self.addCleanup(service.close)
        context = self.memory.build_context(private_event("1001"), None)
        self.assertEqual(service.retrieve(context, "麻辣火锅"), ("", []))
        self.assertEqual(service.search(context, "麻辣火锅"), [])

    def test_injection_failure_returns_zero(self):
        service = self.make_service()
        service.store.search = mock.Mock(side_effect=RuntimeError("disk gone"))
        event, req = private_event("1001", "我喜欢麻辣火锅"), FakeRequest("我喜欢麻辣火锅", "系统提示")
        injected = self.memory.inject_memory(event, req, {}, service=service)
        self.assertEqual(injected, 0)
        self.assertEqual(req.system_prompt, "系统提示")

    def test_injection_survives_event_without_optional_methods(self):
        """Events missing platform/sender helpers must not raise (global scope only)."""

        service = self.make_service()
        service.add("global", "all", "偏好.火锅", "用户喜欢麻辣火锅", confidence=0.9)

        class MinimalEvent:
            message_str = "麻辣火锅"
            _extras: dict = {}

            def get_extra(self, key, default=None):
                return self._extras.get(key, default)

            def set_extra(self, key, value):
                self._extras[key] = value

        req = FakeRequest("麻辣火锅", "")
        injected = self.memory.inject_memory(MinimalEvent(), req, {}, service=service)
        self.assertGreater(injected, 0)
        self.assertIn("麻辣火锅", req.system_prompt)

    def test_disabled_service_is_inert(self):
        service = self.make_service(enable=False)
        event, req = private_event("1001", "我喜欢麻辣火锅"), FakeRequest("我喜欢麻辣火锅", "系统提示")
        self.assertEqual(self.memory.inject_memory(event, req, {}, service=service), 0)
        self.assertFalse(self.memory.enqueue_extraction(event, req, "好的", {}, service=service))
        self.assertEqual(req.system_prompt, "系统提示")
        self.assertEqual(service.processed_turns, 0)

    def test_enqueue_without_running_loop_returns_false(self):
        service = self.make_service()
        context = self.memory.build_context(private_event("1001"), None)
        self.assertFalse(service.enqueue_turn(context, "我喜欢麻辣火锅", ""))

    def test_store_error_during_formation_is_swallowed(self):
        from astrbot.core.memory.extract import heuristic_extract

        service = self.make_service()
        service.store.upsert = mock.Mock(side_effect=RuntimeError("db is gone"))
        context = self.memory.build_context(private_event("1001"), None)
        candidates = heuristic_extract("我喜欢麻辣火锅", context)
        self.assertTrue(candidates)
        result = service.store_candidates(candidates)
        self.assertEqual(result, {"created": 0, "updated": 0, "skipped": 0})
        self.assertTrue(service.last_error)


class AsyncFailOpenTests(AsyncMemoryTestCase):
    async def test_broken_extractor_does_not_kill_the_queue(self):
        service = self.make_service(llm_extract=True)

        async def broken(text, context):
            raise RuntimeError("model exploded")

        service.set_llm_extractor(broken)
        context = self.memory.build_context(private_event("1001"), None)
        self.assertTrue(service.enqueue_turn(context, "我喜欢麻辣火锅"))
        await service.drain()
        self.assertEqual(service.processed_turns, 1)
        self.assertEqual(len(service.export_all()), 1)
        self.assertTrue(service.enqueue_turn(context, "我住在杭州"))
        await service.drain()
        self.assertEqual(service.processed_turns, 2)

    async def test_worker_survives_storage_error(self):
        service = self.make_service()
        service.store.upsert = mock.Mock(side_effect=RuntimeError("disk full"))
        context = self.memory.build_context(private_event("1001"), None)
        self.assertTrue(service.enqueue_turn(context, "我喜欢麻辣火锅"))
        await service.drain()
        self.assertTrue(service.last_error)
        self.assertEqual(service.processed_turns, 1)
        self.assertTrue(service.enqueue_turn(context, "我住在杭州"))
        await service.drain()
        self.assertEqual(service.processed_turns, 2)


if __name__ == "__main__":
    unittest.main()
