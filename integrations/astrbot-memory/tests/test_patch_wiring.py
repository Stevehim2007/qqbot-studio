"""Structural compatibility checks against the patched AstrBot v4.29.3 tree.

These do not import AstrBot (its dependency tree is not needed to verify a
patch); they assert that the two pipeline hooks are present at the expected
places, so an upstream upgrade that moves the call sites fails loudly instead
of silently disabling memory.
"""

from __future__ import annotations

import ast
import json
import subprocess
import unittest
from pathlib import Path

from _support import astrbot_src

PINNED_VERSION = "4.29.3"
PINNED_COMMIT = "f61a7f4"


def _tree(relative: str) -> ast.Module:
    path = astrbot_src() / relative
    if not path.is_file():
        raise unittest.SkipTest(f"未找到 {path}（设置 ASTRBOT_SRC）")
    return ast.parse(path.read_text(encoding="utf-8-sig")), path


def _calls(module: ast.Module, function: str) -> list[ast.Call]:
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return [
                item
                for item in ast.walk(node)
                if isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
            ]
    return []


class PatchWiringTests(unittest.TestCase):
    def test_pinned_version_matches_upstream_tag(self):
        version_file = astrbot_src() / "astrbot" / "__init__.py"
        if not version_file.is_file():
            self.skipTest("AstrBot 源码不可用")
        text = version_file.read_text(encoding="utf-8")
        self.assertIn(f'__version__ = "{PINNED_VERSION}"', text)

    def test_retrieval_hook_is_called_when_decorating_the_request(self):
        module, _ = _tree("astrbot/core/astr_main_agent.py")
        names = {call.func.id for call in _calls(module, "_decorate_llm_request")}
        self.assertIn("inject_memory_block", names)
        # retrieval must happen after persona binding (which fills system_prompt)
        source = (astrbot_src() / "astrbot/core/astr_main_agent.py").read_text(encoding="utf-8-sig")
        self.assertLess(
            source.index("await _ensure_persona_and_skills"),
            source.index("inject_memory_block(event, req"),
        )

    def test_extraction_hook_runs_after_the_final_reply(self):
        module, path = _tree(
            "astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py"
        )
        names = {
            node.func.id
            for node in ast.walk(module)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("enqueue_extraction", names)
        source = path.read_text(encoding="utf-8-sig")
        self.assertLess(
            source.index("final_resp = agent_runner.get_final_llm_resp()"),
            source.index("enqueue_extraction("),
        )
        # extraction is fire-and-forget: no await on the hook call
        self.assertNotIn("await enqueue_extraction(", source)

    def test_memory_defaults_are_present_and_disabled_by_default(self):
        source = (astrbot_src() / "astrbot/core/config/default.py").read_text(encoding="utf-8-sig")
        self.assertIn('"memory": {', source)
        module = ast.parse(source)
        for node in module.body:
            if not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Dict)
                and any(
                    isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG"
                    for target in node.targets
                )
            ):
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and key.value == "memory":
                    self.assertIsInstance(value, ast.Dict)
                    values = {
                        item_key.value: item_value.value
                        for item_key, item_value in zip(value.keys, value.values)
                        if isinstance(item_key, ast.Constant) and isinstance(item_value, ast.Constant)
                    }
                    self.assertIs(values.get("enable"), False)
                    self.assertEqual(values.get("max_chars"), 800)
                    self.assertEqual(values.get("max_items"), 6)
                    json.dumps(values)
                    return
            self.fail("DEFAULT_CONFIG 中缺少 memory 配置段")
        self.fail("DEFAULT_CONFIG not found")

    def test_memory_package_imports_only_the_standard_library(self):
        package = astrbot_src() / "astrbot/core/memory"
        if not package.is_dir():
            self.skipTest("AstrBot 源码不可用")
        allowed = {
            "asyncio", "collections", "copy", "dataclasses", "hashlib", "json", "logging",
            "__future__", "math", "os", "pathlib", "re", "sqlite3", "threading", "time", "typing",
        }
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            nested = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for child in ast.walk(node):
                        nested.add(id(child))
            for node in ast.walk(tree):
                # imports inside a function are lazy on purpose (e.g. the
                # astrbot_path helper); only module-level imports are pinned.
                if id(node) in nested:
                    continue
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    modules = [(node.module or "").split(".")[0]]
                else:
                    continue
                for module in modules:
                    self.assertIn(module, allowed, f"{path.name} imports {module}")

    def test_shipped_config_has_no_dead_write_scope_keys(self):
        """v1.0's write_private_scope/write_group_scope were dead: removed in v1.2."""

        path = astrbot_src() / "astrbot/core/config/default.py"
        if not path.is_file():
            self.skipTest("AstrBot 源码不可用")
        text = path.read_text(encoding="utf-8-sig")
        memory_block = text.split('"memory": {', 1)[1].split('"platform_settings"', 1)[0]
        self.assertNotIn("write_private_scope", memory_block)
        self.assertNotIn("write_group_scope", memory_block)

    def test_patch_file_matches_the_working_tree(self):
        repo_root = Path(__file__).resolve().parents[3]
        patch = repo_root / "integrations" / "astrbot-memory" / "patches" / "0001-native-memory.patch"
        if not patch.is_file():
            self.skipTest("补丁文件尚未生成")
        text = patch.read_text(encoding="utf-8")
        self.assertIn(PINNED_VERSION, text)
        for path in (
            "astrbot/core/memory/__init__.py",
            "astrbot/core/memory/store.py",
            "astrbot/core/memory/integration.py",
            "astrbot/core/memory/policy.py",
            "astrbot/core/astr_main_agent.py",
            "astrbot/core/config/default.py",
        ):
            self.assertIn(path, text)


if __name__ == "__main__":
    unittest.main()
