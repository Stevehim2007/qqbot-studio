"""Memory formation: create / update / skip with semantic keys and dedup."""

from __future__ import annotations

import unittest

from _support import MemoryTestCase, private_event


class FormationTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service()
        self.context = self.memory.build_context(private_event("1001"), None)

    def rows(self):
        return self.service.export("user", "aiocqhttp:1001")

    def test_duplicate_turn_is_skipped(self):
        first = self.remember(self.service, self.context, "我最喜欢吃麻辣火锅")
        second = self.remember(self.service, self.context, "我最喜欢吃麻辣火锅")
        self.assertEqual(first, {"created": 1, "updated": 0, "skipped": 0})
        self.assertEqual(second, {"created": 0, "updated": 0, "skipped": 1})
        self.assertEqual(len(self.rows()), 1)

    def test_near_duplicate_paraphrase_is_skipped(self):
        self.remember(self.service, self.context, "我最喜欢吃麻辣火锅")
        action = self.remember(self.service, self.context, "我最喜欢吃麻辣火锅呀")
        self.assertEqual(action, {"created": 0, "updated": 0, "skipped": 1})
        self.assertEqual(len(self.rows()), 1)

    def test_contradiction_updates_in_place(self):
        self.remember(self.service, self.context, "我喜欢吃香菜")
        result = self.remember(self.service, self.context, "我不喜欢吃香菜")
        self.assertEqual(result, {"created": 0, "updated": 1, "skipped": 0})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("不喜欢", rows[0]["content"])

    def test_new_value_replaces_old_value(self):
        self.remember(self.service, self.context, "我住在杭州")
        result = self.remember(self.service, self.context, "我住在上海")
        self.assertEqual(result, {"created": 0, "updated": 1, "skipped": 0})
        self.assertEqual([row["content"] for row in self.rows()], ["用户居住地：上海"])

    def test_identical_content_under_another_key_is_dropped(self):
        created, _ = self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢吃火锅")
        self.assertEqual(created, "created")
        duplicate, _ = self.service.add("user", "aiocqhttp:1001", "偏好.麻辣火锅", "用户喜欢吃火锅")
        self.assertEqual(duplicate, "skipped")
        self.assertEqual(len(self.rows()), 1)

    def test_scope_cap_keeps_row_count_bounded(self):
        service = self.make_service(max_records_per_scope=20)
        for index in range(40):
            service.add("user", "aiocqhttp:1001", f"偏好.网红店{index}", f"用户喜欢网红店{index}")
        self.assertLessEqual(len(self.rows()), 20)

    def test_ttl_and_purge(self):
        now = [1000.0]
        service = self.memory.MemoryService(
            self.memory.normalise_config({"memory": {"enable": True, "db_path": str(self.db_path)}}),
            clock=lambda: now[0],
        )
        self.addCleanup(service.close)
        service.add("user", "aiocqhttp:1001", "临时.排队号码", "用户当前排队号码是 A12", ttl_seconds=60)
        self.assertEqual(len(service.export()), 1)
        now[0] += 61
        self.assertEqual(service.purge_expired(), 1)
        self.assertEqual(service.export(), [])

    def test_expired_rows_are_not_retrieved(self):
        self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅", ttl_seconds=1)
        import time

        time.sleep(1.1)
        _, records = self.service.retrieve(self.context, "麻辣火锅")
        self.assertEqual(records, [])

    def test_skip_does_not_write(self):
        self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅")
        before = self.rows()[0]["updated_at"]
        action, _ = self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅")
        self.assertEqual(action, "skipped")
        self.assertEqual(self.rows()[0]["updated_at"], before)


if __name__ == "__main__":
    unittest.main()
