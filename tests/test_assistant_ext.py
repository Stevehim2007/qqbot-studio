"""Tests for the optional assistant B extension manager.

All git calls are injected fakes; these tests never touch the network or
require a real assistant checkout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class AssistantConfigStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.expected_ext = self.root / "extensions" / "ai-assistant"
        self.expected_config = self.root / "ai-assistant" / "config.json"
        (self.root / "studio.json").write_text(
            json.dumps({"bind_port": 8765}), encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _make_installed_manager(self, health_checker=None, **kwargs):
        self.expected_ext.mkdir(parents=True)
        (self.expected_ext / ".git").mkdir()
        (self.expected_ext / ASSISTANT_ENTRY_FILE).write_text(
            "# assistant entry\n", encoding="utf-8"
        )
        (self.expected_ext / ASSISTANT_README_FILE).write_text(
            "# readme\n", encoding="utf-8"
        )
        checker = health_checker or (lambda url, timeout: False)
        return AssistantExtensionManager(
            self.root,
            runner=FakeGitRunner(),
            health_checker=checker,
            **kwargs,
        )

    def _assert_no_key(self, text, secret):
        serialized = json.dumps(text, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("deepseek_api_key", serialized)

    def test_config_before_install_is_safe_and_not_500(self):
        manager = AssistantExtensionManager(
            self.root,
            runner=FakeGitRunner(),
            health_checker=lambda url, timeout: False,
        )
        status = manager.get_config()
        self.assertTrue(status["ok"])
        self.assertFalse(status["installed"])
        self.assertFalse(status["configured"])
        self.assertFalse(status["running"])
        self.assertEqual(status["configure_url"], "/api/assistant/config")
        self.assertIn("access_note", status)
        self.assertIn("ssh_tunnel", status)

        saved = manager.save_config(
            {"key_source": "independent", "api_key": "sk-should-not-save"}
        )
        self.assertFalse(saved["ok"])
        self.assertFalse(saved["installed"])
        self.assertFalse(self.expected_config.exists())

    def test_save_independent_key_is_private_and_never_echoed(self):
        manager = self._make_installed_manager()
        secret = "sk-independent-secret-123456789"
        result = manager.save_config(
            {
                "key_source": "independent",
                "api_key": secret,
                "instance_id": "ignored-for-independent",
                "port": 8766,
                "host": "127.0.0.1",
            }
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["configured"])
        self.assertTrue(result["key_present"])
        self.assertEqual(result["key_source"], "independent")
        self.assertEqual(result["port"], 8766)
        self.assertEqual(result["host"], "127.0.0.1")
        self.assertIn("SSH", result["access_note"])
        self._assert_no_key(result, secret)

        self.assertTrue(self.expected_config.exists())
        self.assertEqual(self.expected_config.stat().st_mode & 0o777, 0o600)
        stored = json.loads(self.expected_config.read_text(encoding="utf-8"))
        self.assertEqual(stored["api_key"], secret)
        self.assertEqual(stored["instance_id"], "")

        # Port-only updates preserve the hidden independent key.
        second = manager.save_config({"port": 8767})
        self.assertTrue(second["ok"], second.get("error"))
        stored = json.loads(self.expected_config.read_text(encoding="utf-8"))
        self.assertEqual(stored["api_key"], secret)
        self.assertEqual(stored["port"], 8767)
        self._assert_no_key(second, secret)

    def test_instance_mode_checks_key_but_does_not_copy_it(self):
        manager = self._make_installed_manager()
        instance_secret = "sk-instance-key-never-copy-999"
        instance_dir = self.root / "instances" / "bot-1" / "data" / "astrbot"
        instance_dir.mkdir(parents=True)
        (instance_dir / "cmd_config.json").write_text(
            json.dumps({"provider": [{"key": [instance_secret]}]}),
            encoding="utf-8",
        )
        result = manager.save_config(
            {
                "key_source": "instance",
                "instance_id": "bot-1",
                "api_key": "sk-request-key-must-be-cleared",
                "port": 8768,
            }
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["configured"])
        self.assertTrue(result["key_present"])
        self.assertEqual(result["instance_id"], "bot-1")
        self._assert_no_key(result, instance_secret)

        stored_text = self.expected_config.read_text(encoding="utf-8")
        stored = json.loads(stored_text)
        self.assertEqual(stored["key_source"], "instance")
        self.assertEqual(stored["api_key"], "")
        self.assertNotIn(instance_secret, stored_text)
        self.assertNotIn("sk-request-key-must-be-cleared", stored_text)

    def test_config_rejects_unsafe_host_ports_and_instance_id(self):
        manager = self._make_installed_manager()
        base = {
            "key_source": "independent",
            "api_key": "sk-port-validation-123456",
            "host": "127.0.0.1",
        }
        # Management service port.
        result = manager.save_config({**base, "port": 8765})
        self.assertFalse(result["ok"])
        self.assertIn("已被管理服务或现有机器人实例占用", result["error"])
        # Existing instance port.
        instance_dir = self.root / "instances" / "bot-2"
        instance_dir.mkdir(parents=True)
        (instance_dir / "config.json").write_text(
            json.dumps({"ports": {"astrbot_webui": 6000, "snowluma_webui": 6001, "novnc": 6002}}),
            encoding="utf-8",
        )
        result = manager.save_config({**base, "port": 6000})
        self.assertFalse(result["ok"])
        self.assertIn("占用", result["error"])
        # Range boundaries.
        for port in (4999, 10001):
            result = manager.save_config({**base, "port": port})
            self.assertFalse(result["ok"])
            self.assertIn("5000-10000", result["error"])
        # Public bind is refused explicitly.
        result = manager.save_config({**base, "port": 8769, "host": "0.0.0.0"})
        self.assertFalse(result["ok"])
        self.assertIn("0.0.0.0", result["error"])
        self.assertIn("SSH", result["error"])
        # Unsafe instance id and key-source.
        result = manager.save_config(
            {"key_source": "instance", "instance_id": "../escape", "port": 8770}
        )
        self.assertFalse(result["ok"])
        result = manager.save_config(
            {"key_source": "unknown", "api_key": "sk-x-12345678", "port": 8771}
        )
        self.assertFalse(result["ok"])
        self.assertIn("key_source", result["error"])

    def test_default_port_avoids_management_port(self):
        manager = self._make_installed_manager()
        result = manager.save_config(
            {"key_source": "independent", "api_key": "sk-default-port-123456"}
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertGreaterEqual(result["port"], 5000)
        self.assertLessEqual(result["port"], 10000)
        self.assertNotEqual(result["port"], 8765)

    def test_start_is_detached_idempotent_and_uses_current_python(self):
        health = {"running": False}
        processes = []

        class FakeProcess:
            pid = 43210

            def __init__(self):
                self.terminated = False
                self.return_code = None

            def poll(self):
                return self.return_code

            def terminate(self):
                self.terminated = True
                self.return_code = -15

            def wait(self, timeout=None):
                return self.return_code

            def kill(self):
                self.return_code = -9

        def factory(command, **kwargs):
            health["running"] = True
            process = FakeProcess()
            processes.append((list(command), dict(kwargs), process))
            return process

        manager = self._make_installed_manager(
            health_checker=lambda url, timeout: health["running"],
            popen_factory=factory,
            start_timeout=0.2,
            poll_interval=0.01,
        )
        secret = "sk-start-secret-abcdefghijklmnop"
        configured = manager.save_config(
            {
                "key_source": "independent",
                "api_key": secret,
                "port": 8772,
                "host": "127.0.0.1",
            }
        )
        self.assertTrue(configured["ok"], configured.get("error"))

        started = manager.start()
        self.assertTrue(started["ok"], started.get("error"))
        self.assertTrue(started["running"])
        self.assertEqual(started["action"], "started")
        self.assertEqual(started["pid"], 43210)
        self.assertEqual(len(processes), 1)
        command, kwargs, _process = processes[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], str(self.expected_ext / ASSISTANT_ENTRY_FILE))
        self.assertEqual(command[2], "--config")
        self.assertEqual(command[3], str(self.expected_config))
        self.assertIn("QQBOT_STUDIO_HOME", kwargs["env"])
        self.assertNotIn(secret, json.dumps(kwargs["env"], ensure_ascii=False))
        self.assertEqual(self.expected_config.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.root / "ai-assistant" / "assistant.pid").exists())
        self.assertTrue((self.root / "ai-assistant" / "assistant.log").exists())
        self._assert_no_key(started, secret)

        again = manager.start()
        self.assertTrue(again["ok"], again.get("error"))
        self.assertEqual(again["action"], "already_running")
        self.assertEqual(len(processes), 1)

    def test_start_failure_is_clear_and_cleans_pid(self):
        health = {"running": False}

        class StuckProcess:
            pid = 43211

            def __init__(self):
                self.terminated = False
                self.return_code = None

            def poll(self):
                return self.return_code

            def terminate(self):
                self.terminated = True
                self.return_code = -15

            def wait(self, timeout=None):
                return self.return_code

            def kill(self):
                self.return_code = -9

        process = StuckProcess()

        def factory(command, **kwargs):
            return process

        manager = self._make_installed_manager(
            health_checker=lambda url, timeout: health["running"],
            popen_factory=factory,
            start_timeout=0.04,
            poll_interval=0.01,
        )
        manager.save_config(
            {
                "key_source": "independent",
                "api_key": "sk-failing-start-123456",
                "port": 8773,
            }
        )
        result = manager.start()
        self.assertFalse(result["ok"])
        self.assertIn("启动失败或未就绪", result["message"])
        self.assertTrue(process.terminated)
        self.assertFalse((self.root / "ai-assistant" / "assistant.pid").exists())
        self._assert_no_key(result, "sk-failing-start-123456")

    def test_start_requires_an_available_key(self):
        manager = self._make_installed_manager()
        configured = manager.save_config({"port": 8775, "key_source": "independent"})
        self.assertTrue(configured["ok"], configured.get("error"))
        self.assertFalse(configured["configured"])
        result = manager.start()
        self.assertFalse(result["ok"])
        self.assertFalse(result["running"])
        self.assertIn("API Key", result["message"])

    def test_stop_uses_tracked_pid_and_is_clean(self):
        health = {"running": False}

        class FakeProcess:
            pid = 43212

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        def factory(command, **kwargs):
            health["running"] = True
            return FakeProcess()

        manager = self._make_installed_manager(
            health_checker=lambda url, timeout: health["running"],
            popen_factory=factory,
            start_timeout=0.2,
            poll_interval=0.01,
        )
        manager.save_config(
            {
                "key_source": "independent",
                "api_key": "sk-stop-test-123456789",
                "port": 8774,
            }
        )
        started = manager.start()
        self.assertTrue(started["ok"], started.get("error"))
        self.assertTrue((self.root / "ai-assistant" / "assistant.pid").exists())

        def fake_kill(pid, sig):
            health["running"] = False

        with mock.patch("qqbot_studio.assistant_ext.os.kill", side_effect=fake_kill):
            stopped = manager.stop()
        self.assertTrue(stopped["ok"], stopped.get("error"))
        self.assertFalse(stopped["running"])
        self.assertEqual(stopped["action"], "stopped")
        self.assertFalse((self.root / "ai-assistant" / "assistant.pid").exists())
        self._assert_no_key(stopped, "sk-stop-test-123456789")

    def test_start_without_config_is_rejected(self):
        manager = self._make_installed_manager()
        result = manager.start()
        self.assertFalse(result["ok"])
        self.assertFalse(result["running"])
        self.assertIn("尚未配置", result["message"])

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
