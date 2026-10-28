import json
import os
import re
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from qqbot_studio.deployer import DeployManager, DeploymentError, PERSONA_ID
from qqbot_studio.dockerctl import CommandResult, DockerError, DockerStatus
from qqbot_studio.store import Store


class FakeDocker:
    ASTRBOT_CONTAINER = "qqbot-astrbot"
    SNOWLUMA_CONTAINER = "qqbot-snowluma"

    def __init__(self):
        self.status = DockerStatus(True, True, True, True)
        self.actions = []
        self.logs_by_service = {
            "astrbot": "aiocqhttp(OneBot v11) 适配器已连接。\n",
            "snowluma": "initial credentials: user=admin password=SnowPass123\n",
        }

    def detect(self, force=False):
        return self.status

    def require_ready(self):
        return self.status

    def pull(self):
        return CommandResult(("docker", "compose", "pull"), 0, "pulled", "")

    def up(self):
        self.actions.append("up")
        return CommandResult(("docker", "compose", "up"), 0, "started", "")

    def stop(self):
        self.actions.append("stop")
        return CommandResult(("docker", "compose", "stop"), 0, "stopped", "")

    def restart(self):
        self.actions.append("restart")
        return CommandResult(("docker", "compose", "restart"), 0, "restarted", "")

    def logs(self, service, lines=200):
        return self.logs_by_service.get(service, "")

    def _docker(self, args, timeout=60, extra_env=None):
        if "inspect" in args:
            return CommandResult(tuple(args), 0, "running\n", "")
        return CommandResult(tuple(args), 0, "", "")

    @staticmethod
    def _tail(text, limit=1800):
        return text[-limit:]


class FakeApi:
    def __init__(self):
        self.prompts = []
        self.persona_ids = []

    def wait_until_ready(self, timeout=180):
        return None

    def upsert_persona(self, persona_id, prompt):
        self.persona_ids.append(persona_id)
        self.prompts.append(prompt)



class FallbackDocker(FakeDocker):
    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.pull_calls = 0
        self.env_images = []

    def pull(self):
        self.pull_calls += 1
        env = (self.data_dir / ".env").read_text(encoding="utf-8")
        match = re.search(r"^ASTRBOT_IMAGE=(.+)$", env, re.MULTILINE)
        self.env_images.append(match.group(1) if match else "")
        if self.pull_calls == 1:
            raise DockerError("mirror-a 无法连接")
        return super().pull()


class DeployerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        secrets_data = self.store.ensure_secrets()
        secrets_data["snowluma_password"] = "SnowPass123"
        self.store.save_secrets(secrets_data)
        self.docker = FakeDocker()
        self.api = FakeApi()
        self.manager = DeployManager(
            store=self.store,
            docker=self.docker,
            api_client_factory=lambda base, user, password: self.api,
        )
        self.manager.probe_deepseek_key = lambda key: (True, "Key 校验通过", 200)

    def tearDown(self):
        self.tmp.cleanup()

    def _wait_job(self, job_id, timeout=10, manager=None):
        active_manager = manager or self.manager
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = active_manager.get_job(job_id)
            if job and job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("deployment job did not finish")

    def test_validation_rejects_bad_inputs(self):
        with self.assertRaises(DeploymentError):
            self.manager.start_deploy(
                qq_account="abc",
                deepseek_api_key="sk-test",
                prompt="prompt",
                accept_risk=True,
            )
        with self.assertRaises(DeploymentError):
            self.manager.start_deploy(
                qq_account="12345678",
                deepseek_api_key="sk-test",
                prompt="",
                accept_risk=True,
            )
        with self.assertRaises(DeploymentError):
            self.manager.start_deploy(
                qq_account="12345678",
                deepseek_api_key="sk-test",
                prompt="prompt",
                accept_risk=False,
            )


    def test_mirror_pull_falls_back_to_next_source(self):
        docker = FallbackDocker(self.tmp.name)
        manager = DeployManager(
            store=self.store,
            docker=docker,
            api_client_factory=lambda base, user, password: self.api,
        )
        manager.probe_deepseek_key = lambda key: (True, "ok", 200)
        with mock.patch.dict(
            os.environ,
            {"QQBOT_IMAGE_MIRROR_PREFIXES": "mirror-a.example/,mirror-b.example/"},
            clear=False,
        ):
            job_id = manager.start_deploy(
                qq_account="12345678",
                deepseek_api_key="sk-test-secret",
                prompt="prompt",
                use_mirror=True,
                accept_risk=True,
            )
            job = self._wait_job(job_id, manager=manager)
        self.assertEqual(job["status"], "success", job.get("error"))
        self.assertEqual(docker.pull_calls, 2)
        self.assertEqual(
            docker.env_images,
            [
                "mirror-a.example/soulter/astrbot:latest",
                "mirror-b.example/soulter/astrbot:latest",
            ],
        )
        self.assertEqual(self.store.load_config().get("docker_mirror"), "mirror-b.example/")
        self.assertTrue(any("自动切换" in line for line in job["logs"]))

    def test_full_deployment_success_and_status(self):
        job_id = self.manager.start_deploy(
            qq_account="12345678",
            deepseek_api_key="sk-test-secret",
            prompt="你是测试 QQ 机器人。",
            use_mirror=True,
            accept_risk=True,
        )
        job = self._wait_job(job_id)
        self.assertEqual(job["status"], "success", job.get("error"))
        config = self.store.load_config()
        self.assertTrue(config["deployed"])
        self.assertEqual(config["qq_account"], "12345678")
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertTrue((Path(self.tmp.name) / "compose.yml").exists())
        self.assertTrue((Path(self.tmp.name) / "data" / "astrbot" / "cmd_config.json").exists())
        self.assertTrue((Path(self.tmp.name) / "data" / "snowluma" / "config" / "onebot.json").exists())
        self.assertEqual(self.api.persona_ids, [PERSONA_ID])
        self.assertEqual(self.api.prompts, ["你是测试 QQ 机器人。"])
        raw_astrbot = (Path(self.tmp.name) / "data" / "astrbot" / "cmd_config.json").read_text(encoding="utf-8")
        self.assertIn("sk-test-secret", raw_astrbot)
        self.assertNotIn("sk-test-secret", json.dumps(job, ensure_ascii=False))

        qq_config_dir = Path(self.tmp.name) / "data" / "snowluma" / "config"
        (qq_config_dir / "onebot_12345678.json").write_text("{}", encoding="utf-8")
        status = self.manager.get_status()
        self.assertTrue(status["deployment"]["deployed"])
        self.assertTrue(status["deployment"]["running"])
        self.assertEqual(status["deployment"]["model"], "deepseek-flash")
        self.assertTrue(status["deployment"]["onebot_connected"])
        self.assertTrue(status["deployment"]["qq_logged_in"])
        self.assertEqual(status["deployment"]["credentials"]["snowluma_password"], "SnowPass123")
        self.assertTrue(status["deployment"]["urls"]["novnc"].startswith("http://127.0.0.1:"))

    def test_container_actions(self):
        config = self.store.load_config()
        config["deployed"] = True
        self.store.save_config(config)
        self.manager.container_action("start")
        self.manager.container_action("restart")
        self.manager.container_action("stop")
        self.assertEqual(self.docker.actions, ["up", "restart", "stop"])
        with self.assertRaises(DeploymentError):
            self.manager.container_action("delete")


    def test_deepseek_probe_uses_models_endpoint_and_does_not_log_key(self):
        fake_response = mock.MagicMock()
        fake_response.__enter__.return_value = fake_response
        fake_response.__exit__.return_value = False
        fake_response.status = 200
        fake_response.read.return_value = json.dumps(
            {"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]}
        ).encode("utf-8")
        mock_urlopen = mock.MagicMock()
        mock_urlopen.return_value.__enter__.return_value = fake_response
        probe_manager = DeployManager(store=self.store, docker=self.docker)
        with mock.patch("urllib.request.urlopen", mock_urlopen):
            ok, message, status = probe_manager.probe_deepseek_key("sk-unit-test-key")
        self.assertTrue(ok, message)
        self.assertEqual(status, 200)
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-unit-test-key")
        self.assertNotIn("sk-unit-test-key", message)


    def test_logs_api_redacts_keys_and_tokens(self):
        config_dir = Path(self.tmp.name) / "data" / "astrbot"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "cmd_config.json").write_text(
            json.dumps({"provider": [{"key": ["sk-log-secret-marker-12345"]}]}),
            encoding="utf-8",
        )
        token = self.store.load_secrets()["onebot_token"]
        self.docker.logs_by_service["astrbot"] = (
            "provider key sk-log-secret-marker-12345\n"
            f"onebot token {token}\n"
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
            "url ?access_token=another-secret-token\n"
            "unseen sk-unknownsecret123456789\n"
        )
        logs = self.manager.get_logs("astrbot", 20)
        self.assertNotIn("sk-log-secret-marker-12345", logs)
        self.assertNotIn(token, logs)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", logs)
        self.assertNotIn("another-secret-token", logs)
        self.assertNotIn("sk-unknownsecret123456789", logs)
        self.assertIn("***", logs)

    def test_early_failure_marks_job_error_and_clears_active_lock(self):
        secrets_path = Path(self.tmp.name) / "secrets.json"
        secrets_path.write_text("{broken-json", encoding="utf-8")
        job_id = self.manager.start_deploy(
            qq_account="12345678",
            deepseek_api_key="sk-test-secret",
            prompt="prompt",
            accept_risk=True,
        )
        job = self._wait_job(job_id)
        self.assertEqual(job["status"], "error")
        self.assertIn("secrets.json", job["error"])
        self.assertEqual(self.manager._active_job_id, "")

    def test_snowluma_password_parser(self):
        secrets_data = self.store.load_secrets()
        secrets_data["snowluma_password"] = ""
        self.store.save_secrets(secrets_data)
        self.docker.logs_by_service["snowluma"] = "\x1b[32m临时密码: Abcd1234\x1b[0m\n"
        # Avoid the deliberate two-second settle delay in production.
        import qqbot_studio.deployer as deployer_module
        original_sleep = deployer_module.time.sleep
        deployer_module.time.sleep = lambda *_: None
        try:
            parsed = self.manager._capture_snowluma_password()
        finally:
            deployer_module.time.sleep = original_sleep
        self.assertEqual(parsed, "Abcd1234")
        self.assertEqual(self.store.load_secrets()["snowluma_password"], "Abcd1234")


if __name__ == "__main__":
    unittest.main()
