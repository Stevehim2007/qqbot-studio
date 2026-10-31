import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.gitutil import run_git
from qqbot_studio.updater import check_update


def make_result(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


class FakeGit:
    def __init__(self, *, dirty=False, fetch_ok=True, behind=2, ahead=0):
        self.dirty = dirty
        self.fetch_ok = fetch_ok
        self.behind = behind
        self.ahead = ahead
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        command = list(argv)
        self.calls.append(command)
        if "status" in command:
            return make_result(command, stdout=" M file.py\n" if self.dirty else "")
        if "fetch" in command:
            return make_result(command, 0 if self.fetch_ok else 1, "", "" if self.fetch_ok else "network error")
        if "rev-list" in command:
            return make_result(command, stdout=f"{self.ahead}\t{self.behind}\n")
        if "rev-parse" in command and "--abbrev-ref" in command:
            return make_result(command, stdout="main\n")
        if "rev-parse" in command and "origin/main" in command:
            return make_result(command, stdout="def5678\n")
        if "rev-parse" in command and "HEAD" in command:
            return make_result(command, stdout="abc1234\n")
        return make_result(command, stdout="")


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_fast_forward_update(self):
        result = check_update(self.root, runner=FakeGit(behind=3))
        self.assertTrue(result["ok"])
        self.assertTrue(result["can_update"])
        self.assertEqual(result["behind"], 3)
        self.assertEqual(result["current"], "abc1234")
        self.assertEqual(result["remote"], "def5678")

    def test_dirty_tree_is_not_updated(self):
        result = check_update(self.root, runner=FakeGit(dirty=True))
        self.assertFalse(result["ok"])
        self.assertFalse(result["can_update"])
        self.assertIn("未提交", result["message"])

    def test_fetch_failure_is_reported(self):
        result = check_update(self.root, runner=FakeGit(fetch_ok=False))
        self.assertFalse(result["ok"])
        self.assertIn("无法从 origin", result["message"])

    def test_up_to_date_is_not_updateable(self):
        result = check_update(self.root, runner=FakeGit(behind=0))
        self.assertTrue(result["ok"])
        self.assertFalse(result["can_update"])
        self.assertIn("最新", result["message"])

    def test_non_git_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = check_update(tmp)
        self.assertFalse(result["ok"])
        self.assertIn("不是 Git 仓库", result["message"])

    def test_shared_git_runner_is_noninteractive_and_captures_output(self):
        completed = subprocess.CompletedProcess(["git", "status"], 0, "clean", "")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "qqbot_studio.gitutil.subprocess.run", return_value=completed
        ) as run:
            result = run_git(["git", "status"], self.root, 12, {"EXTRA": "1"})
        self.assertEqual(result.stdout, "clean")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["git", "status"])
        self.assertEqual(kwargs["cwd"], str(self.root))
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["env"]["EXTRA"], "1")
        if os.name != "nt":
            self.assertEqual(kwargs["env"]["GIT_ASKPASS"], "/bin/true")
            self.assertIn("BatchMode=yes", kwargs["env"]["GIT_SSH_COMMAND"])


if __name__ == "__main__":
    unittest.main()
