"""Tests for the optional assistant B extension manager.

All git calls are injected fakes; these tests never touch the network or
require a real assistant checkout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from qqbot_studio.assistant_ext import (
    ASSISTANT_ENTRY_FILE,
    ASSISTANT_README_FILE,
    ASSISTANT_REPO_URL,
    AssistantExtensionManager,
)


def result(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


class FakeGitRunner:
    def __init__(
        self,
        *,
        clone_ok=True,
        required_files=True,
        dirty=False,
        remote_is_fast_forward=True,
    ):
        self.clone_ok = clone_ok
        self.required_files = required_files
        self.dirty = dirty
        self.remote_is_fast_forward = remote_is_fast_forward
        self.merged = False
        self.calls = []

    def __call__(self, argv, cwd, timeout, extra_env):
        command = [str(item) for item in argv]
        self.calls.append((command, Path(cwd), int(timeout)))
        assert command[0] == "git"
        subcommand = command[1] if len(command) > 1 else ""

        if subcommand == "clone":
            if not self.clone_ok:
                return result(command, 128, "", "fatal: unable to access GitHub")
            target = Path(command[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir(exist_ok=True)
            if self.required_files:
                (target / ASSISTANT_ENTRY_FILE).write_text("# assistant\n", encoding="utf-8")
                (target / ASSISTANT_README_FILE).write_text("# readme\n", encoding="utf-8")
            return result(command, 0, "Cloning into ...\n")
        if subcommand == "rev-parse" and len(command) > 2 and command[2] == "--short=12":
            return result(command, 0, ("newcommit1234" if self.merged else "oldcommit1234") + "\n")
        if subcommand == "rev-parse" and len(command) > 2 and command[2] == "--abbrev-ref":
            return result(command, 0, "main\n")
        if subcommand == "status":
            return result(command, 0, " M assistant.py\n" if self.dirty else "")
        if subcommand == "fetch":
            return result(command, 0, "")
        if subcommand == "merge-base":
            return result(command, 0 if self.remote_is_fast_forward else 1, "")
        if subcommand == "merge":
            self.merged = True
            return result(command, 0, "Fast-forward\n")
        return result(command, 0, "")


class AssistantExtensionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.expected_path = self.root / "extensions" / "ai-assistant"

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_without_extension_is_graceful(self):
        manager = AssistantExtensionManager(self.root, runner=FakeGitRunner())
        status = manager.status()
        self.assertTrue(status["ok"])
        self.assertFalse(status["installed"])
        self.assertEqual(status["path"], str(self.expected_path))
        self.assertEqual(status["repo"], ASSISTANT_REPO_URL)
        self.assertFalse(status["directory_exists"])
        self.assertIn("尚未安装", status["message"])

    def test_install_clones_fixed_repo_and_validates_files(self):
        runner = FakeGitRunner()
        manager = AssistantExtensionManager(self.root, runner=runner)
        status = manager.install()

        self.assertTrue(status["ok"], status.get("error"))
        self.assertTrue(status["installed"])
        self.assertEqual(status["action"], "installed")
        self.assertEqual(status["commit"], "oldcommit1234")
        self.assertTrue(self.expected_path.is_dir())
        self.assertTrue((self.expected_path / ASSISTANT_ENTRY_FILE).is_file())
        self.assertTrue((self.expected_path / ASSISTANT_README_FILE).is_file())
        clone_call = next(call for call in runner.calls if call[0][1] == "clone")
        self.assertIn("clone", clone_call[0])
        self.assertIn(ASSISTANT_REPO_URL, clone_call[0])
        self.assertIn("--depth", clone_call[0])
        # The clone uses a generated staging directory under the fixed
        # extensions parent; no API/user input participates in the argv.
        clone_target = Path(clone_call[0][-1])
        self.assertEqual(clone_target.parent, self.expected_path.parent)
        self.assertTrue(clone_target.name.startswith(".ai-assistant.install-"))
        self.assertNotIn("sk-", json.dumps(status, ensure_ascii=False))

    def test_install_does_not_leave_partial_clone_on_failure(self):
        manager = AssistantExtensionManager(
            self.root,
            runner=FakeGitRunner(clone_ok=False),
        )
        status = manager.install()
        self.assertFalse(status["ok"])
        self.assertFalse(status["installed"])
        self.assertIn("下载 AI 助手 B 失败", status["message"])
        self.assertFalse(self.expected_path.exists())
        leftover = list((self.root / "extensions").glob(".ai-assistant.install-*"))
        self.assertEqual(leftover, [])

    def test_clone_without_required_files_is_rejected(self):
        manager = AssistantExtensionManager(
            self.root,
            runner=FakeGitRunner(required_files=False),
        )
        status = manager.install()
        self.assertFalse(status["ok"])
        self.assertFalse(status["installed"])
        self.assertFalse(self.expected_path.exists())
        leftover = list((self.root / "extensions").glob(".ai-assistant.install-*"))
        self.assertEqual(leftover, [])

    def test_existing_non_git_directory_is_not_deleted(self):
        self.expected_path.mkdir(parents=True)
        marker = self.expected_path / "user-file.txt"
        marker.write_text("keep", encoding="utf-8")
        manager = AssistantExtensionManager(self.root, runner=FakeGitRunner())
        status = manager.install()
        self.assertFalse(status["ok"])
        self.assertTrue(marker.exists())
        self.assertIn("未删除", status["message"])

    def test_update_accepts_only_clean_fast_forward(self):
        self.expected_path.mkdir(parents=True)
        (self.expected_path / ".git").mkdir()
        (self.expected_path / ASSISTANT_ENTRY_FILE).write_text("# assistant\n", encoding="utf-8")
        (self.expected_path / ASSISTANT_README_FILE).write_text("# readme\n", encoding="utf-8")
        runner = FakeGitRunner()
        manager = AssistantExtensionManager(self.root, runner=runner)

        status = manager.update()
        self.assertTrue(status["ok"], status.get("error"))
        self.assertEqual(status["action"], "updated")
        self.assertTrue(status["updated"])
        self.assertEqual(status["commit"], "newcommit1234")
        commands = [call[0] for call in runner.calls]
        self.assertTrue(any("fetch" in command for command in commands))
        self.assertTrue(any("merge" in command and "--ff-only" in command for command in commands))
        self.assertFalse(any("pull" in command for command in commands))

    def test_update_rejects_dirty_worktree(self):
        self.expected_path.mkdir(parents=True)
        (self.expected_path / ".git").mkdir()
        (self.expected_path / ASSISTANT_ENTRY_FILE).write_text("# assistant\n", encoding="utf-8")
        (self.expected_path / ASSISTANT_README_FILE).write_text("# readme\n", encoding="utf-8")
        runner = FakeGitRunner(dirty=True)
        manager = AssistantExtensionManager(self.root, runner=runner)
        status = manager.update()
        self.assertFalse(status["ok"])
        self.assertIn("未提交修改", status["message"])
        self.assertFalse(any(call[0][1] == "fetch" for call in runner.calls))

    def test_update_rejects_non_fast_forward(self):
        self.expected_path.mkdir(parents=True)
        (self.expected_path / ".git").mkdir()
        (self.expected_path / ASSISTANT_ENTRY_FILE).write_text("# assistant\n", encoding="utf-8")
        (self.expected_path / ASSISTANT_README_FILE).write_text("# readme\n", encoding="utf-8")
        runner = FakeGitRunner(remote_is_fast_forward=False)
        manager = AssistantExtensionManager(self.root, runner=runner)
        status = manager.update()
        self.assertFalse(status["ok"])
        self.assertIn("快进", status["message"])
        self.assertFalse(any(call[0][1] == "merge" for call in runner.calls))

    def test_status_and_update_never_read_or_return_assistant_keys(self):
        self.expected_path.mkdir(parents=True)
        (self.expected_path / ".git").mkdir()
        (self.expected_path / ASSISTANT_ENTRY_FILE).write_text("# assistant\n", encoding="utf-8")
        (self.expected_path / ASSISTANT_README_FILE).write_text("# readme\n", encoding="utf-8")
        secret = "sk-super-secret-test-key-123456"
        config_dir = self.root / "ai-assistant"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(
            json.dumps({"deepseek_api_key": secret, "api_key": secret}),
            encoding="utf-8",
        )
        (self.expected_path / "config.json").write_text(
            json.dumps({"deepseek_api_key": secret}),
            encoding="utf-8",
        )
        runner = FakeGitRunner()
        manager = AssistantExtensionManager(self.root, runner=runner)
        status = manager.status()
        update = manager.update()
        combined = json.dumps([status, update], ensure_ascii=False)
        self.assertNotIn(secret, combined)
        self.assertNotIn("deepseek_api_key", combined)
        self.assertNotIn("api_key", combined)

    def test_git_error_output_redacts_secret_shaped_values(self):
        raw = result(
            ["git", "fetch"],
            128,
            "",
            "fatal: https://user:password@example.test/repo sk-super-secret-123456",
        )
        safe = AssistantExtensionManager._tail_output(raw)
        self.assertNotIn("password", safe)
        self.assertNotIn("sk-super-secret-123456", safe)
        self.assertIn("***", safe)

    def test_missing_git_command_is_actionable(self):
        def no_git(argv, cwd, timeout, extra_env):
            raise FileNotFoundError("git")

        manager = AssistantExtensionManager(self.root, runner=no_git)
        status = manager.install()
        self.assertFalse(status["ok"])
        self.assertIn("sudo apt-get install -y git", status["error"])


class AssistantInstallScriptTests(unittest.TestCase):
    def test_install_script_has_optional_fixed_repo_installer(self):
        script = (Path(__file__).resolve().parent.parent / "install.sh").read_text(encoding="utf-8")
        self.assertIn("QQBOT_INSTALL_AI_ASSISTANT", script)
        self.assertIn("默认不安装", script)
        self.assertIn("extensions/ai-assistant", script)
        self.assertIn(ASSISTANT_REPO_URL, script)
        self.assertIn("git clone --depth 1", script)
        self.assertIn("主项目不会自动启动", script)
        self.assertIn("主项目安装不受影响", script)
        self.assertIn("pull --ff-only", script)
        self.assertNotIn("api_key", script.lower())
        self.assertNotIn("deepseek_api_key", script.lower())

    @unittest.skipUnless(shutil.which("bash"), "bash not available")
    def test_install_script_syntax(self):
        script = Path(__file__).resolve().parent.parent / "install.sh"
        completed = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
