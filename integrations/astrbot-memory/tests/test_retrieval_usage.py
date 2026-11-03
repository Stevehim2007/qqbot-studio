"""Retrieval must record usage (hits/last_used_at) without breaking the read path."""

from __future__ import annotations

import unittest

from _support import FakeRequest, MemoryTestCase, group_event, private_event


class RetrievalUsageTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service()
        self.context = self.memory.build_context(private_event("1001"), None)
        self.record, _ = self.service.add(
            "user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅和毛肚", confidence=0.9
        )
        self.service.add("user", "aiocqhttp:1001", "偏好.咖啡", "用户喜欢手冲咖啡", confidence=0.9)

    def test_hits_and_last_used_at_are_updated_by_retrieval(self):
        _, records = self.service.retrieve(self.context, "麻辣火锅")
        self.assertEqual([record.key for record in records], ["偏好.火锅"])
        self.assertEqual(records[0].hits, 1)
        self.assertIsNotNone(records[0].last_used_at)
        stored = self.service.export("user", "aiocqhttp:1001")
        row = next(item for item in stored if item["key"] == "偏好.火锅")
        self.assertEqual(row["hits"], 1)
        self.assertIsNotNone(row["last_used_at"])

        _, records = self.service.retrieve(self.context, "麻辣火锅")
        self.assertEqual(records[0].hits, 2)

    def test_search_also_records_usage(self):
        self.service.search(self.context, "手冲咖啡")
        row = next(item for item in self.service.export("user", "aiocqhttp:1001") if item["key"] == "偏好.咖啡")
        self.assertEqual(row["hits"], 1)
        self.assertIsNotNone(row["last_used_at"])

    def test_non_matching_turn_does_not_touch_counters(self):
        self.service.retrieve(self.context, "Python 代码")
        row = next(item for item in self.service.export("user", "aiocqhttp:1001") if item["key"] == "偏好.火锅")
        self.assertEqual(row["hits"], 0)
        self.assertIsNone(row["last_used_at"])

    def test_gated_or_empty_queries_do_not_record_usage(self):
        self.assertEqual(self.service.retrieve(self.context, "   "), ("", []))
        event, req = private_event("1001", "火锅"), FakeRequest("火锅", "")
        self.memory.inject_memory(event, req, {}, service=self.service)
        row = next(item for item in self.service.export("user", "aiocqhttp:1001") if item["key"] == "偏好.火锅")
        self.assertEqual(row["hits"], 1)  # only the injection retrieval counted

    def test_usage_survives_repair_of_the_fts_index(self):
        self.service.retrieve(self.context, "麻辣火锅")
        self.service.store.fts_enabled = False
        _, records = self.service.retrieve(self.context, "麻辣火锅")
        self.assertEqual(records[0].hits, 2)


if __name__ == "__main__":
    unittest.main()
