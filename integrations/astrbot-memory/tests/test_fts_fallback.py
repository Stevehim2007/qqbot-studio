"""Retrieval must work with SQLite FTS5 and with the BM25 fallback."""

from __future__ import annotations

import unittest

from _support import MemoryTestCase, private_event, raw_memory_config, sqlite_has_fts5


class FtsFallbackTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.context = self.memory.build_context(private_event("1001"), None)

    def _seed(self, service):
        service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅和毛肚", confidence=0.9)
        service.add("user", "aiocqhttp:1001", "偏好.咖啡", "用户喜欢手冲咖啡和浅烘焙", confidence=0.9)
        service.add("user", "aiocqhttp:1001", "偏好.运动", "用户习惯每周三次羽毛球", confidence=0.9)

    def test_fts_index_is_used_when_available(self):
        if not sqlite_has_fts5():
            self.skipTest("当前 sqlite3 未编译 FTS5")
        service = self.make_service()
        self._seed(service)
        self.assertTrue(service.fts_enabled)
        _, records = service.retrieve(self.context, "麻辣火锅毛肚")
        self.assertEqual([record.key for record in records], ["偏好.火锅"])

    def test_bm25_fallback_without_fts5(self):
        service = self.memory.MemoryService(
            raw_memory_config(self.db_path),
            store=self.memory.MemoryStore(self.db_path / "fallback.db", enable_fts=False),
        )
        self.addCleanup(service.close)
        self.assertFalse(service.fts_enabled)
        self._seed(service)
        _, records = service.retrieve(self.context, "麻辣火锅毛肚")
        self.assertEqual([record.key for record in records], ["偏好.火锅"])
        self.assertEqual(service.retrieve(self.context, "Python 代码")[1], [])

    def test_fallback_when_fts_index_disappears(self):
        service = self.make_service()
        self._seed(service)
        service.store.fts_enabled = False  # e.g. FTS table dropped/corrupted at runtime
        _, records = service.retrieve(self.context, "手冲咖啡")
        self.assertEqual([record.key for record in records], ["偏好.咖啡"])
        self.assertEqual(service.store.last_error, "")

    def test_both_paths_agree_on_scope_filtering(self):
        for enable_fts in (True, False):
            path = self.db_path / f"scope-{enable_fts}.db"
            store = self.memory.MemoryStore(path, enable_fts=enable_fts)
            service = self.memory.MemoryService(raw_memory_config(path), store=store)
            self.addCleanup(service.close)
            service.add("group", "aiocqhttp:2001", "偏好.火锅", "本群喜欢麻辣火锅", confidence=0.9)
            service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅", confidence=0.9)
            _, group_records = service.retrieve(self.context, "麻辣火锅")
            self.assertEqual(
                [record.scope_id for record in group_records], ["aiocqhttp:1001"], enable_fts
            )


if __name__ == "__main__":
    unittest.main()
