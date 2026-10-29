"""Static and cross-platform checks for the initial Windows port.

This environment does not have PowerShell, so these tests validate the pieces
that are mechanically checkable on Linux: file presence, PowerShell entry-point
wiring, required operational tokens, documentation disclaimers and the small
Python compatibility branches used on Windows.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path, PosixPath
from unittest import mock

from qqbot_studio import configgen, dockerctl, store

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "windows"


class WindowsPortStaticTests(unittest.TestCase):
    def test_required_windows_files_exist(self):
        for name in ("install.ps1", "start.ps1", "console.ps1", "common.ps1", "studio_helper.py", "README.md"):
            self.assertTrue((WINDOWS / name).is_file(), name)

    def test_powershell_files_are_ascii_and_dot_source_common(self):
        for name in ("install.ps1", "start.ps1", "console.ps1", "common.ps1"):
            (WINDOWS / name).read_text(encoding="ascii")
        for name in ("install.ps1", "start.ps1", "console.ps1"):
            text = (WINDOWS / name).read_text(encoding="ascii")
            self.assertIn("#requires -Version 5.1", text, name)
            self.assertIn("common.ps1", text)
            self.assertTrue("$PSScriptRoot" in text or "$PSCommandPath" in text)

    def test_background_service_contract_is_present(self):
        common = (WINDOWS / "common.ps1").read_text(encoding="ascii")
        for expected in (
            "Start-Process",
            "-WindowStyle Hidden",
            "server.pid",
            "studio-server.log",
            "studio-server.err.log",
            "healthz",
            "server-runtime.json",
            "Taskkill",
        ):
            self.assertIn(expected.lower(), common.lower(), expected)
        self.assertNotIn("screen -dmS", common)
        self.assertNotIn("systemctl", common)
        self.assertNotIn("sudo", common)

    def test_install_checks_prerequisites_and_persists_config(self):
        text = (WINDOWS / "install.ps1").read_text(encoding="ascii")
        for expected in (
            "Python 3.10",
            "Find-PythonInfo",
            "Docker Desktop",
            "Test-WslAvailable",
            "Initialize-StudioConfig",
            "studio.json",
            "New-ManagementFirewallRule",
            "Ensure-DockerDesktopEngine",
        ):
            self.assertIn(expected, text, expected)
        common = (WINDOWS / "common.ps1").read_text(encoding="ascii")
        self.assertIn("compose version", common)
        self.assertIn("first_run_complete", common)
        self.assertIn("$script:DefaultBindHost = '0.0.0.0'", common)

    def test_start_creates_instance_and_warns_about_qr_login(self):
        text = (WINDOWS / "start.ps1").read_text(encoding="ascii")
        self.assertIn("create-instance", text)
        self.assertIn("Ensure-ManagementServer", text)
        self.assertIn("noVNC", text)
        self.assertIn("Scan the official QQ login QR code", text)
        self.assertIn("Every run creates a NEW isolated instance", text)

    def test_console_does_not_create_a_new_instance(self):
        text = (WINDOWS / "console.ps1").read_text(encoding="ascii")
        self.assertIn("prepare-console", text)
        self.assertIn("Ensure-ManagementServer", text)
        self.assertNotIn("'create-instance'", text)
        self.assertNotIn("Create bot instance", text)

    def test_windows_docs_cover_setup_security_and_unverified_items(self):
        text = (WINDOWS / "README.md").read_text(encoding="utf-8")
        for expected in (
            "Windows 10",
            "WSL2",
            "Docker Desktop",
            "扫码",
            "noVNC",
            "Windows Defender 防火墙",
            "云安全组",
            "0.0.0.0",
            "未完成真实 Windows",
            "没有真实联调通过",
            "Windows 初始版没有运行 Linux",
        ):
            self.assertIn(expected, text, expected)


class WindowsCompatibilityCodeTests(unittest.TestCase):
    def test_compose_omits_linux_localtime_mount_on_windows(self):
        linux = configgen._compose_yaml(include_localtime=True)
        windows = configgen._compose_yaml(include_localtime=False)
        self.assertIn("/etc/localtime:/etc/localtime:ro", linux)
        self.assertNotIn("/etc/localtime", windows)
        self.assertIn("TZ: Asia/Shanghai", windows)

    def test_windows_default_data_dir_uses_localappdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store.os, "name", "nt"), mock.patch.object(
                store, "Path", PosixPath
            ), mock.patch.dict(
                store.os.environ,
                {"QQBOT_STUDIO_DATA_DIR": "", "LOCALAPPDATA": tmp},
                clear=False,
            ):
                result = store.default_data_dir()
        self.assertEqual(result, (PosixPath(tmp) / "QQBotStudio" / "data").resolve())

    def test_fchmod_fallback_tolerates_missing_windows_attribute(self):
        with mock.patch.object(store.os, "fchmod", None):
            store.fchmod_best_effort(123, 0o600)

    def test_docker_detection_does_not_try_sudo_on_windows(self):
        calls = []

        def runner(argv, cwd, timeout, extra_env):
            command = list(argv)
            calls.append(command)
            if command == ["docker", "--version"]:
                return subprocess.CompletedProcess(command, 0, "Docker version 27.0.0\n", "")
            if command[:3] == ["docker", "compose", "version"]:
                return subprocess.CompletedProcess(command, 0, "Docker Compose version v2.30.0\n", "")
            if command[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(command, 1, "", "permission denied while trying to connect")
            return subprocess.CompletedProcess(command, 1, "", "unexpected")

        with tempfile.TemporaryDirectory() as tmp:
            controller = dockerctl.DockerController(tmp, runner=runner)
            with mock.patch.object(dockerctl.os, "name", "nt"):
                status = controller.detect(force=True)
        self.assertTrue(status.installed)
        self.assertTrue(status.compose_installed)
        self.assertFalse(status.daemon_running)
        self.assertIn("Docker Desktop", status.detail)
        self.assertFalse(any(call and call[0] == "sudo" for call in calls))


if __name__ == "__main__":
    unittest.main()
