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


class ConfigPathAndRestartDocsTests(unittest.TestCase):
    """The config path and restart semantics must match AstrBot v4.29.3 + the panel."""

    DOCS = {
        "README": REPO_ROOT / "integrations" / "astrbot-memory" / "README.md",
        "Dockerfile": REPO_ROOT / "integrations" / "astrbot-memory" / "Dockerfile.memory",
        "memory-policy": REPO_ROOT / "docs" / "memory-policy.md",
    }

    def _read(self, name: str) -> str:
        return self.DOCS[name].read_text(encoding="utf-8")

    def test_docs_point_at_cmd_config_only(self):
        for name in self.DOCS:
            with self.subTest(name):
                text = self._read(name)
                self.assertNotIn("astrbot_config.json", text, f"{name} 仍指向错误的配置文件名")
                self.assertIn("cmd_config.json", text, f"{name} 缺少正确的 cmd_config.json 路径")

    def test_readme_states_the_actual_config_path_constant(self):
        readme = self._read("README")
        self.assertIn("ASTRBOT_CONFIG_PATH", readme)
        self.assertIn("data/cmd_config.json", readme)

    def test_restart_wording_is_consistent_with_patch_behaviour(self):
        readme = self._read("README")
        # File edits: AstrBot loads cmd_config.json at startup -> restart required.
        self.assertIn("直接改文件需要重启 AstrBot", readme)
        self.assertIn("restart_required=true", readme)
        # In-memory config updates: the patch's refresh applies on the next turn.
        self.assertIn("下一轮", readme)
        self.assertIn("refresh_config()", readme)
        # The old, contradictory claims must be gone.
        self.assertNotIn("下一轮读取就会生效", readme)
        self.assertNotIn("不必重启进程", readme)
        self.assertNotIn("重启 AstrBot：", readme)

    def test_panel_docs_and_implementation_agree(self):
        panel_docs = self._read("memory-policy")
        self.assertIn("restart_required=true", panel_docs)
        self.assertIn("hot_reload=false", panel_docs)
        self.assertIn("cmd_config.json", panel_docs)
        self.assertIn("refresh_config()", panel_docs)

        studio_code = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted((REPO_ROOT / "qqbot_studio").rglob("*.py"))
        )
        self.assertIn("cmd_config.json", studio_code, "Studio 应读写 AstrBot 的 cmd_config.json")
        self.assertIn("memory.policy", studio_code)


if __name__ == "__main__":
    unittest.main()
