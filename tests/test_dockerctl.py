import subprocess
import tempfile
import unittest
from pathlib import Path

from qqbot_studio.dockerctl import DockerController, DockerError


class FakeDockerRunner:
    def __init__(self, *, installed=True, direct_info=False, sudo_info=True):
        self.installed = installed
        self.direct_info = direct_info
        self.sudo_info = sudo_info
        self.calls = []

    def __call__(self, argv, cwd, timeout, extra_env):
        command = list(argv)
        self.calls.append(command)
        joined = " ".join(command)
        if command == ["docker", "--version"]:
            if not self.installed:
                raise FileNotFoundError("docker")
            return subprocess.CompletedProcess(command, 0, "Docker version 27.0.0\n", "")
        if command[:3] == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(command, 0, "Docker Compose version v2.30.0\n", "")
        if command[:2] == ["docker", "info"]:
            if self.direct_info:
                return subprocess.CompletedProcess(command, 0, "27.0.0\n", "")
            return subprocess.CompletedProcess(command, 1, "", "permission denied while trying to connect")
        if command[:3] == ["sudo", "-n", "docker"] and command[3:5] == ["info", "--format"]:
            if self.sudo_info:
                return subprocess.CompletedProcess(command, 0, "27.0.0\n", "")
            return subprocess.CompletedProcess(command, 1, "", "sudo: a password is required")
        if command[:3] == ["sudo", "-n", "docker"] and "inspect" in command:
            return subprocess.CompletedProcess(command, 0, "running\n", "")
        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "running\n", "")
        if "logs" in command:
            return subprocess.CompletedProcess(command, 0, "log line\n", "")
        if "pull" in command:
            return subprocess.CompletedProcess(command, 0, "pulled\n", "")
        if "up" in command:
            return subprocess.CompletedProcess(command, 0, "started\n", "")
        return subprocess.CompletedProcess(command, 0, "ok\n", "")


class DockerControllerTests(unittest.TestCase):
    def _controller(self, tmp, runner):
        root = Path(tmp)
        (root / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        (root / ".env").write_text("X=1\n", encoding="utf-8")
        return DockerController(root, runner=runner)

    def test_detects_sudo_fallback_and_runs_compose_through_sudo(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeDockerRunner()
            controller = self._controller(tmp, runner)
            status = controller.detect(force=True)
            self.assertTrue(status.installed)
            self.assertTrue(status.compose_installed)
            self.assertTrue(status.daemon_running)
            self.assertTrue(status.permission_ok)
            self.assertTrue(status.uses_sudo)
            result = controller.pull()
            self.assertEqual(result.stdout, "pulled\n")
            compose_calls = [call for call in runner.calls if "compose" in call and "pull" in call]
            self.assertTrue(compose_calls)
            self.assertEqual(compose_calls[-1][:3], ["sudo", "-n", "docker"])
            self.assertIn("--env-file", compose_calls[-1])

    def test_missing_docker_is_reported_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeDockerRunner(installed=False)
            controller = self._controller(tmp, runner)
            status = controller.detect(force=True)
            self.assertFalse(status.installed)
            self.assertFalse(status.permission_ok)
            with self.assertRaises(DockerError):
                controller.require_ready()

    def test_container_status_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeDockerRunner()
            controller = self._controller(tmp, runner)
            self.assertEqual(controller.container_status("qqbot-astrbot"), "running")
            self.assertIn("log line", controller.logs("astrbot", 20))


if __name__ == "__main__":
    unittest.main()
