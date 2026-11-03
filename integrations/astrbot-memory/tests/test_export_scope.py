"""Export must stay inside a scope: no accidental whole-DB dump."""

from __future__ import annotations

import inspect
import unittest

from _support import MemoryTestCase


class ExportScopeTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service()
        self.service.add("user", "aiocqhttp:1001", "偏好.火锅", "用户喜欢麻辣火锅", confidence=0.9)
        self.service.add("user", "aiocqhttp:1002", "偏好.烧烤", "用户喜欢烧烤", confidence=0.9)
        self.service.add("group", "aiocqhttp:2001", "偏好.火锅", "本群喜欢麻辣火锅", confidence=0.9)

    def test_export_requires_an_explicit_scope(self):
        self.assertRaises(TypeError, self.service.export)
        self.assertRaises(TypeError, self.service.store.export)
        self.assertRaises(ValueError, self.service.store.export, "")

    def test_export_signature_has_no_default_scope(self):
        scope = inspect.signature(self.service.export).parameters["scope_type"]
        self.assertIs(scope.default, inspect.Parameter.empty)
        store_scope = inspect.signature(self.service.store.export).parameters["scope_type"]
        self.assertIs(store_scope.default, inspect.Parameter.empty)

    def test_export_is_limited_to_one_scope(self):
        mine = self.service.export("user", "aiocqhttp:1001")
        self.assertEqual([row["content"] for row in mine], ["用户喜欢麻辣火锅"])
        group_rows = self.service.export("group", "aiocqhttp:2001")
        self.assertEqual([row["scope_type"] for row in group_rows], ["group"])

    def test_export_by_scope_type_stays_inside_that_type(self):
        rows = self.service.export("user")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["scope_type"] for row in rows}, {"user"})

    def test_export_all_is_the_explicit_operator_path(self):
        self.assertEqual(len(self.service.export_all()), 3)


if __name__ == "__main__":
    unittest.main()
