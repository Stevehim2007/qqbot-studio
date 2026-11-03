"""Docs must match the code: test counts and the confidence-update wording."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from _support import MemoryTestCase, astrbot_src

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "integrations" / "astrbot-memory" / "README.md"
CONTRACT = REPO_ROOT / "integrations" / "astrbot-memory" / "CONTRACT.md"

COUNT_PATTERNS = (
    r"(\d+)\s*个兼容性测试",
    r"(\d+)\s*个测试",
    r"（(\d+)\s*tests）",
)


def count_tests(suite: unittest.TestSuite) -> int:
    total = 0
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            total += count_tests(item)
        else:
            total += 1
    return total


class ReadmeConsistencyTests(MemoryTestCase):
    def test_readme_test_count_matches_the_suite(self):
        here = Path(__file__).resolve().parent
        suite = unittest.TestLoader().discover(str(here), pattern="test_*.py", top_level_dir=str(here))
        expected = count_tests(suite)
        readme = README.read_text(encoding="utf-8")
        declared = set()
        for pattern in COUNT_PATTERNS:
            declared.update(int(value) for value in re.findall(pattern, readme))
        self.assertTrue(declared, "README 里没有声明测试数")
        self.assertEqual(declared, {expected}, f"README 声明的测试数与实际 {expected} 不一致：{declared}")

    def test_readme_confidence_wording_matches_store_code(self):
        readme = README.read_text(encoding="utf-8")
        self.assertIn("极性翻转", readme)
        self.assertIn("取新值", readme)

        store_code = (astrbot_src() / "astrbot/core/memory/store.py").read_text(encoding="utf-8")
        contradiction = store_code.split("if is_contradiction", 1)[1].split("else:", 1)[0]
        self.assertIn("merged_confidence = float(candidate.confidence)", contradiction)
        ordinary = store_code.split("merged_confidence = min(", 1)[1]
        self.assertIn("+ 0.05", ordinary.split("else:", 1)[0] if "else:" in ordinary else ordinary)

    def test_readme_and_contract_document_the_scoped_export(self):
        for path in (README, CONTRACT):
            text = path.read_text(encoding="utf-8")
            self.assertIn("export_all", text, path.name)
            self.assertIn("export(scope_type", text, path.name)


if __name__ == "__main__":
    unittest.main()
