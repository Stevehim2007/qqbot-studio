"""Extraction queue: asynchronous, bounded, and off the reply path."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from _support import AsyncMemoryTestCase, private_event


class QueueTests(AsyncMemoryTestCase):
    async def test_turn_is_extracted_in_the_background(self):
        service = self.make_service()
        context = self.memory.build_context(private_event("1001"), None)
        self.assertTrue(service.enqueue_turn(context, "我最喜欢吃麻辣火锅", "好的，我记住了"))
        # Nothing is written synchronously: the reply path is never blocked.
        self.assertEqual(service.export_all(), [])
        await service.drain()
        self.assertEqual(service.processed_turns, 1)
        self.assertEqual([row["content"] for row in service.export_all()], ["用户喜欢：吃麻辣火锅"])

    async def test_recall_text_boosts_confidence(self):
        service = self.make_service()
        context = self.memory.build_context(private_event("1001"), None)
        service.enqueue_turn(context, "记住：我不吃香菜", "好的，我记住了")
        await service.drain()
        note = next(row for row in service.export_all() if row["key"].startswith("记录."))
        self.assertGreaterEqual(note["confidence"], 0.9)

    async def test_queue_is_bounded_and_drops_instead_of_blocking(self):
        service = self.make_service(queue_size=1)

        async def slow(context, user_text, assistant_text, explicit=()):
            await asyncio.sleep(0.05)

        service._process_turn = slow  # type: ignore[assignment]
        context = self.memory.build_context(private_event("1001"), None)
        accepted = [service.enqueue_turn(context, f"我喜欢火锅 {index}") for index in range(5)]
        self.assertIn(False, accepted)
        self.assertGreater(service.dropped_turns, 0)
        await service.drain()

    async def test_drain_waits_for_pending_turns(self):
        service = self.make_service()
        context = self.memory.build_context(private_event("1001"), None)
        for text in ("我喜欢火锅", "我住在杭州", "我叫小明"):
            self.assertTrue(service.enqueue_turn(context, text))
        await service.drain()
        self.assertEqual(service.processed_turns, 3)
        self.assertGreaterEqual(len(service.export_all()), 3)

    async def test_llm_extractor_is_optional(self):
        service = self.make_service(llm_extract=True)
        calls = []

        async def extractor(text, context):
            calls.append(text)
            from astrbot.core.memory.types import Candidate

            return [Candidate(key="偏好.烧烤", content="用户喜欢烧烤", scope_type="user", scope_id="aiocqhttp:1001")]

        service.set_llm_extractor(extractor)
        context = self.memory.build_context(private_event("1001"), None)
        service.enqueue_turn(context, "随便聊聊烧烤")
        await service.drain()
        self.assertEqual(calls, ["随便聊聊烧烤"])
        self.assertIn("偏好.烧烤", [row["key"] for row in service.export_all()])

    async def test_llm_extractor_is_not_called_by_default(self):
        service = self.make_service()
        called = []

        async def extractor(text, context):
            called.append(text)
            return []

        service.set_llm_extractor(extractor)
        context = self.memory.build_context(private_event("1001"), None)
        service.enqueue_turn(context, "我喜欢麻辣火锅")
        await service.drain()
        self.assertEqual(called, [])

    async def test_storage_failure_keeps_worker_alive(self):
        service = self.make_service()
        service.store.upsert = mock.Mock(side_effect=RuntimeError("disk full"))
        context = self.memory.build_context(private_event("1001"), None)
        service.enqueue_turn(context, "我喜欢麻辣火锅")
        await service.drain()
        self.assertEqual(service.processed_turns, 1)
        service.store.upsert = self.memory.MemoryStore(self.db_path).upsert  # type: ignore[assignment]
        service.enqueue_turn(context, "我住在杭州")
        await service.drain()
        self.assertEqual(service.processed_turns, 2)


if __name__ == "__main__":
    unittest.main()
