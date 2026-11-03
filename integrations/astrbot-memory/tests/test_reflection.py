"""Reflection: deterministic dry-run/apply with backups and no silent deletes."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from _support import AsyncMemoryTestCase, group_event

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REFLECTION_FIXTURE = FIXTURES / "reflection_example.json"

SCOPE = ("group", "aiocqhttp:2001")
OTHER_SCOPE = ("group", "aiocqhttp:2002")


class ReflectionTests(AsyncMemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service()

    def _add(self, key, content, scope=SCOPE, **kwargs):
        defaults = dict(kind="fact", importance=0.6, confidence=0.7, source="test")
        defaults.update(kwargs)
        return self.service.add(scope[0], scope[1], key, content, **defaults)[1]

    def _ids(self, scope=SCOPE):
        return {record.id for record in self.service.store.list_scope(scope[0], scope[1], limit=100)}

    def test_dry_run_reports_without_writing_or_backing_up(self):
        self._add("喜好.火锅", "用户喜欢火锅")
        self._add("喜好.火锅2", "用户喜欢火锅。")  # near-duplicate
        self._add("喜好.辣", "用户不喜欢辣")
        self._add("低价值", "随口一句", importance=0.1)
        self._add("过期", "过期事实", ttl_seconds=-60)

        before = self._ids()
        plan = self.service.reflect([SCOPE])
        self.assertTrue(plan["ok"])
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["counts"]["records"], 5)
        self.assertEqual(plan["counts"]["duplicate_groups"], 1)
        self.assertEqual(plan["counts"]["duplicate_rows"], 1)
        self.assertEqual(plan["counts"]["low_value"], 1)
        self.assertEqual(plan["counts"]["expired"], 1)
        self.assertFalse(plan["applied"])
        self.assertEqual(plan["backup_path"], "")
        self.assertEqual(self._ids(), before, "dry-run 不得改动数据库")
        self.assertEqual(list(Path(self._tmp.name).glob("*.bak")), [])

    def test_contradiction_is_reported_but_never_deleted(self):
        self._add("喜好.火锅", "用户喜欢麻辣火锅")
        self._add("喜好.火锅.反", "用户不喜欢麻辣火锅")
        plan = self.service.reflect([SCOPE])
        self.assertEqual(plan["counts"]["contradictions"], 1)
        pair = plan["contradictions"][0]
        self.assertEqual(pair["reason"], "polarity")
        self.assertEqual(pair["action"], "report-only")

        applied = self.service.reflect([SCOPE], apply=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(len(self._ids()), 2, "矛盾记录只能报告，不能被静默删除")

    def test_apply_backs_up_then_merges_duplicates(self):
        keep = self._add("喜好.火锅", "用户喜欢火锅", importance=0.8, confidence=0.9)
        self._add("喜好.火锅.重复", "用户喜欢火锅。", importance=0.3, confidence=0.4)
        self.service.store._mark_used([keep], 1000.0)  # hits=1 on the keeper

        plan = self.service.reflect([SCOPE], apply=True)
        self.assertTrue(plan["applied"])
        self.assertEqual(plan["merged"], 1)
        backup = plan["backup_path"]
        self.assertTrue(Path(backup).is_file(), "apply 前必须先备份")
        with sqlite3.connect(backup) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM astrbot_memory").fetchone()[0], 2
            )
        remaining = self.service.store.list_scope(SCOPE[0], SCOPE[1], limit=100)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].id, keep.id)
        self.assertEqual(remaining[0].hits, 1)
        self.assertGreaterEqual(remaining[0].importance, 0.8)

    def test_low_value_rows_are_preview_only(self):
        self._add("低价值", "随口一句", importance=0.1)
        applied = self.service.reflect([SCOPE], apply=True)
        self.assertEqual(applied["counts"]["low_value"], 1)
        self.assertEqual(len(self._ids()), 1, "低价值只预览，不删除")

    def test_expired_rows_are_purged_on_apply(self):
        self._add("过期", "过期事实", ttl_seconds=-60)
        self._add("有效", "有效事实")
        applied = self.service.reflect([SCOPE], apply=True)
        self.assertEqual(applied["purged"], 1)
        remaining = {record.key for record in self.service.store.list_scope(SCOPE[0], SCOPE[1])}
        self.assertEqual(remaining, {"有效"})

    def test_no_cross_scope_merge_or_delete(self):
        self._add("喜好.火锅", "用户喜欢火锅", scope=SCOPE)
        self._add("喜好.火锅", "用户喜欢火锅", scope=OTHER_SCOPE, importance=0.2)
        before = set(self._ids()) | set(self._ids(OTHER_SCOPE))
        plan = self.service.reflect([SCOPE], apply=True)
        self.assertEqual(plan["merged"], 0)
        # The other scope keeps its own row even though the text is identical.
        self.assertEqual(set(self._ids(OTHER_SCOPE)) & before, self._ids(OTHER_SCOPE))
        self.assertEqual(len(self._ids(OTHER_SCOPE)), 1)

    def test_apply_is_idempotent(self):
        self._add("喜好.火锅", "用户喜欢火锅")
        self._add("喜好.火锅.重复", "用户喜欢火锅。")
        self.service.reflect([SCOPE], apply=True)
        second = self.service.reflect([SCOPE], apply=True)
        self.assertEqual(second["merged"], 0)
        self.assertEqual(second["counts"]["duplicate_groups"], 0)
        self.assertEqual(len(self._ids()), 1)

    def test_reflection_requires_explicit_scopes(self):
        empty = self.service.reflect([])
        self.assertFalse(empty["ok"])
        self.assertIn("作用域", empty["error"])
        invalid = self.service.reflect([("unknown_scope", "x")])
        self.assertFalse(invalid["ok"])

    def test_disabled_service_refuses(self):
        disabled = self.make_service(enable=False)
        self.assertEqual(disabled.reflect([SCOPE])["ok"], False)

    def test_reflect_for_requires_long_term_policy(self):
        self._add("喜好.火锅", "用户喜欢火锅")
        conversation_only = self.make_service(policy={"default_mode": "conversation"})
        gated = conversation_only.reflect_for(self.context(group_event("2001", "1001")))
        self.assertFalse(gated["ok"])
        self.assertGreaterEqual(conversation_only.gated_turns, 1)

        enabled = self.make_service(policy={"default_mode": "long_term"})
        enabled.add(SCOPE[0], SCOPE[1], "喜好.火锅", "用户喜欢火锅")
        plan = enabled.reflect_for(self.context(group_event("2001", "1001")))
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["counts"]["records"], 1)

    def test_reflection_never_calls_the_optional_llm(self):
        self._add("喜好.火锅", "用户喜欢火锅")
        self.service.reflect([SCOPE], apply=True)
        self.assertFalse(self.service.llm_stats()["enabled"])
        self.assertEqual(self.service.llm_stats().get("calls", 0), 0)

    def test_reflection_fixture_matches_the_documented_expectations(self):
        payload = json.loads(REFLECTION_FIXTURE.read_text(encoding="utf-8"))
        for row in payload["rows"]:
            self.service.add(
                row["scope_type"],
                row["scope_id"],
                row["key"],
                row["content"],
                importance=row["importance"],
                confidence=row["confidence"],
                ttl_seconds=row.get("ttl_seconds"),
            )
        plan = self.service.reflect([SCOPE])
        for key, expected in payload["expect"].items():
            self.assertEqual(plan["counts"][key], expected, key)

    def test_backup_dir_config_is_honoured(self):
        self._add("喜好.火锅", "用户喜欢火锅")
        self._add("喜好.火锅.重复", "用户喜欢火锅。")
        backups = Path(self._tmp.name) / "backups"
        service = self.make_service(reflect={"backup_dir": str(backups)})
        service.add(SCOPE[0], SCOPE[1], "喜好.火锅", "用户喜欢火锅", importance=0.8)
        service.add(SCOPE[0], SCOPE[1], "喜好.火锅.重复", "用户喜欢火锅。", importance=0.2)
        plan = service.reflect([SCOPE], apply=True)
        self.assertTrue(plan["backup_path"].startswith(str(backups)))
        self.assertTrue(Path(plan["backup_path"]).is_file())

    def test_backup_failure_aborts_apply(self):
        self._add("喜好.火锅", "用户喜欢火锅")
        self._add("喜好.火锅.重复", "用户喜欢火锅。")
        original = self.service.store.backup
        self.service.store.backup = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            plan = self.service.reflect([SCOPE], apply=True)
        finally:
            self.service.store.backup = original
        self.assertFalse(plan["ok"])
        self.assertFalse(plan["applied"])
        self.assertEqual(len(self._ids()), 2, "备份失败时不得写入")


if __name__ == "__main__":
    unittest.main()
