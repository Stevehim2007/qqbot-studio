import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio import launcher
from qqbot_studio.studio import StudioManager


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = StudioManager(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_interactive_config_is_persisted(self):
        args = argparse.Namespace(ip=None, port=None, public_host=None)
        with mock.patch.object(launcher.sys.stdin, "isatty", return_value=True), mock.patch(
            "builtins.input", side_effect=["0.0.0.0", "9123", "bot.example.com"]
        ):
            settings = launcher._configure(self.studio, args)
        self.assertTrue(settings["first_run_complete"])
        self.assertEqual(settings["bind_host"], "0.0.0.0")
        self.assertEqual(settings["bind_port"], 9123)
        self.assertEqual(settings["public_host"], "bot.example.com")
        self.assertEqual(launcher._base_url(self.studio), "http://bot.example.com:9123")

    def test_existing_settings_are_reused_and_arguments_override(self):
        self.studio.update_network(bind_host="0.0.0.0", bind_port=8765, public_host="old.example")
        args = argparse.Namespace(ip="0.0.0.0", port=9124, public_host="new.example")
        with mock.patch.object(launcher.sys.stdin, "isatty", return_value=False):
            settings = launcher._configure(self.studio, args)
        self.assertEqual(settings["bind_port"], 9124)
        self.assertEqual(settings["public_host"], "new.example")


    def test_deploy_mode_creates_a_new_instance(self):
        self.studio.update_network(bind_host="0.0.0.0", bind_port=9123, public_host="bot.example.com")
        self.studio.repair_instances = lambda: []
        args = argparse.Namespace(name="测试一号", no_open=True)
        with mock.patch.object(launcher, "_docker_check"), mock.patch("builtins.print") as fake_print:
            rc = launcher._run_deploy(self.studio, args, self.studio.load_settings())
        self.assertEqual(rc, 0)
        metas = self.studio.list_instance_metas()
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["name"], "测试一号")
        output = "\n".join(str(call.args[0]) for call in fake_print.call_args_list if call.args)
        self.assertIn("/wizard?instance=bot-1", output)
        self.assertIn("/console", output)

    def test_console_mode_prints_console_url(self):
        self.studio.update_network(bind_host="0.0.0.0", bind_port=9123, public_host="bot.example.com")
        self.studio.repair_instances = lambda: []
        args = argparse.Namespace(name=None, no_open=True)
        with mock.patch.object(launcher, "_docker_check"), mock.patch("builtins.print") as fake_print:
            rc = launcher._run_console(self.studio, args, self.studio.load_settings())
        self.assertEqual(rc, 0)
        output = "\n".join(str(call.args[0]) for call in fake_print.call_args_list if call.args)
        self.assertIn("http://bot.example.com:9123/console", output)

    def test_server_command_contains_saved_bind_and_data_dir(self):
        command = launcher._server_command(self.studio, "0.0.0.0", 9123)
        self.assertIn("python3 -m qqbot_studio", command)
        self.assertIn("--host 0.0.0.0", command)
        self.assertIn("--port 9123", command)
        self.assertIn(str(self.studio.root), command)
        self.assertIn("--no-browser", command)


    def test_docker_check_works_with_real_subprocess_and_existing_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then echo 'Docker version 29.9.2'; exit 0; fi\n"
                "if [ \"$1\" = \"compose\" ]; then echo 'Docker Compose version v5.6.1'; exit 0; fi\n"
                "if [ \"$1\" = \"info\" ]; then echo '29.9.2'; exit 0; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            studio = StudioManager(Path(tmp) / "studio-root")
            with mock.patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}):
                launcher._docker_check(studio)

    def test_docker_check_uses_injected_controller(self):
        fake = mock.MagicMock()
        fake.require_ready.return_value = mock.MagicMock()
        with mock.patch.object(launcher, "DockerController", return_value=fake):
            launcher._docker_check(self.studio)
        fake.require_ready.assert_called_once()


if __name__ == "__main__":
    unittest.main()
