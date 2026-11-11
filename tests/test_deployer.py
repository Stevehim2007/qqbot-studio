import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from qqbot_studio import astrbot_api
from qqbot_studio.astrbot_api import AstrBotAPIError, AstrBotTransientError
from qqbot_studio.deployer import DeployManager, DeploymentError, Job, PERSONA_ID
from qqbot_studio.dockerctl import CommandResult, DockerError, DockerStatus
from qqbot_studio.store import Store


class FakeDocker:
    ASTRBOT_CONTAINER = "qqbot-astrbot"

    def __init__(self):
        self.status = DockerStatus(True, True, True, True)
        self.actions = []
        self.logs_by_service = {
            "astrbot": "WebUI 已启动。\n",
        }

    def detect(self, force=False):
        return self.status

    def require_ready(self):
        return self.status

    def pull(self, on_line=None, images=None):
        if on_line:
            on_line("pulling layer 1/2")
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

    def container_health(self, container_name):
        return "healthy"

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


class OfflineApi(FakeApi):
    def wait_until_ready(self, timeout=180):
        raise RuntimeError("AstrBot 不可达")


class FakePluginApi(FakeApi):
    """In-memory stand-in for AstrBot's v4.29 plugin/config routes."""

    def __init__(self):
        super().__init__()
        self.plugin_config = {
            "memory_mode": "direct",
            "reply_probability": 0.12,
            "custom_user_value": "keep-me",
        }
        self.plugins = [
            {
                "name": "qqbot_enhancer",
                "display_name": "QQ Bot 增强回复",
                "desc": "测试插件",
                "version": "1.4.1",
                "reserved": False,
                "activated": True,
            },
            {
                "name": "builtin_help",
                "display_name": "内置帮助",
                "desc": "保留插件",
                "version": "4.29.3",
                "reserved": True,
                "activated": False,
            },
        ]
        self.toggled = []
        self.updated_configs = []

    def login(self):
        return "fake-token"

    def _request(self, method, path, *, payload=None, token=None, timeout=None):
        assert token == "fake-token"
        if method == "GET" and path == "/api/v1/plugins":
            return {
                "status": "ok",
                "message": "",
                "data": [dict(item) for item in self.plugins],
            }
        if method == "GET" and path.startswith("/api/v1/plugins/config?"):
            plugin_id = "qqbot_enhancer"
            if "qqbot_core_reply" in path:
                plugin_id = "qqbot_core_reply"
            return {
                "status": "ok",
                "data": {"plugin_name": plugin_id, "config": dict(self.plugin_config)},
            }
        if method == "PUT" and path == "/api/v1/plugins/config":
            assert payload["plugin_id"] in {"qqbot_enhancer", "qqbot_core_reply"}
            self.plugin_config = dict(payload["config"])
            self.updated_configs.append(dict(payload["config"]))
            return {"status": "ok", "message": "插件配置已保存并热重载。", "data": {}}
        if method == "PATCH" and path == "/api/v1/plugins/enabled":
            plugin_id = payload["plugin_id"]
            enabled = bool(payload["enabled"])
            self.toggled.append((plugin_id, enabled))
            for item in self.plugins:
                if item.get("name") == plugin_id:
                    item["activated"] = enabled
            return {"status": "ok", "message": "操作成功。", "data": {}}
        raise AstrBotAPIError("not found", status_code=404)


BROKEN_PIPE_MESSAGE = (
    "无法连接 AstrBot：[Errno 32] Broken pipe。"
    "AstrBot 可能正在启动或重启，或连接被中断，请稍后重试。"
)


class FlakyPluginApi(FakePluginApi):
    """Simulates an AstrBot that drops the connection ``failures`` times."""

    def __init__(self, failures=1, action="login"):
        super().__init__()
        self.failures = failures
        self.action = action
        self.login_calls = 0
        self.invalidations = 0
        self.list_calls = 0

    def invalidate_token(self):
        self.invalidations += 1

    def login(self, force=False):
        self.login_calls += 1
        if force:
            self.invalidations += 1
        if self.action == "login" and self.failures > 0:
            self.failures -= 1
            raise AstrBotTransientError("AstrBot 登录失败：" + BROKEN_PIPE_MESSAGE)
        return "fake-token"

    def _request(self, method, path, *, payload=None, token=None, timeout=None):
        if method == "GET" and path == "/api/v1/plugins":
            self.list_calls += 1
            if self.action == "list" and self.failures > 0:
                self.failures -= 1
                raise AstrBotTransientError(BROKEN_PIPE_MESSAGE)
        return super()._request(
            method, path, payload=payload, token=token, timeout=timeout
        )


class UnauthorizedPluginApi(FakePluginApi):
    """AstrBot rejects the dashboard password: must never be retried."""

    def __init__(self):
        super().__init__()
        self.login_calls = 0

    def login(self, force=False):
        self.login_calls += 1
        raise AstrBotAPIError("AstrBot 登录失败：用户名或密码错误", status_code=401)


class FallbackDocker(FakeDocker):
    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.pull_calls = 0
        self.env_images = []

    def pull(self, on_line=None, images=None):
        self.pull_calls += 1
        env = (self.data_dir / ".env").read_text(encoding="utf-8")
        match = re.search(r"^ASTRBOT_IMAGE=(.+)$", env, re.MULTILINE)
        self.env_images.append(match.group(1) if match else "")
        if self.pull_calls == 1:
            raise DockerError("mirror-a 无法连接")
        return super().pull(on_line=on_line)


class DeployerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store.ensure_secrets()
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

    def test_runtime_dependency_check_and_python_install(self):
        requirements = {
            "python": [{"name": "Pillow", "spec": ">=10", "module": "PIL", "description": "图片"}],
            "system": [],
        }
        installed = {"value": False}
        calls = []

        def fake_exec(container, argv, timeout=60):
            calls.append(tuple(argv))
            if argv[:2] == ["python3", "-c"] and "importlib.util" in argv[2]:
                return CommandResult(tuple(argv), 0, "[]" if installed["value"] else '["PIL"]', "")
            if argv[:4] == ["python3", "-m", "pip", "install"]:
                installed["value"] = True
                return CommandResult(tuple(argv), 0, "Successfully installed Pillow", "")
            return CommandResult(tuple(argv), 0, "", "")

        self.manager._astrbot_container_available = lambda: (True, "")
        self.manager.plugin_market_service.preview_plan = lambda token: {"runtime_requirements": requirements}
        with mock.patch.object(self.docker, "exec_in_container", create=True, side_effect=fake_exec):
            before = self.manager.check_plugin_runtime_requirements(requirements)
            self.assertFalse(before["ok"])
            self.assertEqual(before["missing"][0]["name"], "Pillow")
            result = self.manager.install_plugin_runtime_dependencies(
                {"preview_token": "preview-1", "confirm": True}
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["installed"], ["Pillow>=10"])
            self.assertTrue(any(call[:4] == ("python3", "-m", "pip", "install") for call in calls))

    def test_runtime_dependency_check_and_apt_install(self):
        requirements = {
            "python": [],
            "system": [{"kind": "package", "target": "fonts-noto-cjk", "description": "字体包"}],
        }
        present = {"value": False}
        calls = []

        def fake_exec(container, argv, timeout=60):
            calls.append(tuple(argv))
            if argv[:2] == ["dpkg", "-s"]:
                return CommandResult(tuple(argv), 0 if present["value"] else 1, "", "")
            if argv[:2] == ["apt-get", "update"]:
                return CommandResult(tuple(argv), 0, "updated", "")
            if argv[:2] == ["apt-get", "install"]:
                present["value"] = True
                return CommandResult(tuple(argv), 0, "installed", "")
            return CommandResult(tuple(argv), 0, "", "")

        self.manager._astrbot_container_available = lambda: (True, "")
        self.manager.plugin_market_service.preview_plan = lambda token: {"runtime_requirements": requirements}
        with mock.patch.object(self.docker, "exec_in_container", create=True, side_effect=fake_exec):
            before = self.manager.check_plugin_runtime_requirements(requirements)
            self.assertFalse(before["ok"])
            result = self.manager.install_plugin_runtime_dependencies(
                {"preview_token": "preview-1", "confirm": True}
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["installed"], ["apt:fonts-noto-cjk"])
            self.assertTrue(any(call[:4] == ("apt-get", "install", "-y", "--no-install-recommends") for call in calls))

    def test_validation_rejects_bad_inputs(self):
        with self.assertRaises(DeploymentError):
            self.manager.start_deploy(
                official_appid="abc",
                official_secret="official-secret",
                deepseek_api_key="sk-test",
                prompt="prompt",
                accept_risk=True,
            )
        with self.assertRaises(DeploymentError):
            self.manager.start_deploy(
                official_appid="12345678",
                official_secret="official-secret",
                deepseek_api_key="sk-test",
                prompt="",
                accept_risk=True,
            )
        with self.assertRaises(DeploymentError):
            self.manager.start_deploy(
                official_appid="12345678",
                official_secret="official-secret",
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
                official_appid="12345678",
                official_secret="official-secret",
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
            official_appid="12345678",
            official_secret="official-secret",
            deepseek_api_key="sk-test-secret",
            prompt="你是测试 QQ 机器人。",
            use_mirror=True,
            accept_risk=True,
        )
        job = self._wait_job(job_id)
        self.assertEqual(job["status"], "success", job.get("error"))
        config = self.store.load_config()
        self.assertTrue(config["deployed"])
        self.assertEqual(config["platform_mode"], "official")
        self.assertEqual(config["qq_official_appid"], "12345678")
        self.assertNotIn("qq_account", config)
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertTrue((Path(self.tmp.name) / "compose.yml").exists())
        self.assertTrue((Path(self.tmp.name) / "data" / "astrbot" / "cmd_config.json").exists())
        self.assertFalse((Path(self.tmp.name) / "data" / "snowluma").exists())
        self.assertEqual(self.api.persona_ids, [PERSONA_ID])
        self.assertEqual(self.api.prompts, ["你是测试 QQ 机器人。"])
        raw_astrbot = (Path(self.tmp.name) / "data" / "astrbot" / "cmd_config.json").read_text(encoding="utf-8")
        self.assertIn("sk-test-secret", raw_astrbot)
        self.assertNotIn("sk-test-secret", json.dumps(job, ensure_ascii=False))
        parsed_astrbot = json.loads(raw_astrbot)
        self.assertIs(parsed_astrbot["platform_settings"]["unique_session"], False)
        self.assertFalse(
            (Path(self.tmp.name) / "data" / "astrbot" / "plugins" / "qqbot_enhancer").exists()
        )
        reply_config = Path(self.tmp.name) / "data" / "astrbot" / "config" / "qqbot_core_reply_config.json"
        self.assertEqual(
            json.loads(reply_config.read_text(encoding="utf-8"))["system_prompt"],
            "你是测试 QQ 机器人。",
        )
        self.assertTrue(any("插件市场" in line for line in job["logs"]))

        status = self.manager.get_status()
        deployment = status["deployment"]
        self.assertTrue(deployment["deployed"])
        self.assertTrue(deployment["running"])
        self.assertEqual(deployment["model"], "deepseek-flash")
        self.assertEqual(deployment["platform_mode"], "official")
        self.assertEqual(deployment["qq_official_appid"], "12345678")
        self.assertIn("astrbot_webui", deployment["urls"])
        self.assertIn("astrbot_password", deployment["credentials"])
        self.assertFalse(deployment["platform_connected"])
        self.assertEqual(deployment["onebot_ws_port"], 0)
        self.assertEqual(deployment["onebot_ws_url"], "")
        self.assertEqual(deployment["qq_account"], "")
        for absent in ("onebot_connected", "qq_logged_in"):
            self.assertNotIn(absent, deployment)
        for absent in ("snowluma_username", "snowluma_password", "vnc_password"):
            self.assertNotIn(absent, deployment["credentials"])
        self.assertNotIn("snowluma_webui", deployment["urls"])
        self.assertNotIn("novnc", deployment["urls"])


    def test_onebot_deployment_success_persists_token_and_ws_port(self):
        job_id = self.manager.start_deploy(
            platform_mode="onebot",
            onebot_ws_port=6201,
            qq_account="12345678",
            deepseek_api_key="sk-onebot-test-secret",
            prompt="你是测试 QQ 机器人。",
            use_mirror=False,
            accept_risk=True,
        )
        job = self._wait_job(job_id)
        self.assertEqual(job["status"], "success", job.get("error"))
        config = self.store.load_config()
        self.assertEqual(config["platform_mode"], "onebot")
        self.assertEqual(config["qq_account"], "12345678")
        self.assertEqual(config["onebot_ws_port"], 6201)
        token = self.store.load_secrets()["onebot_token"]
        self.assertGreaterEqual(len(token), 24)
        compose = (Path(self.tmp.name) / "compose.yml").read_text(encoding="utf-8")
        self.assertIn("ASTRBOT_ONEBOT_PORT", compose)
        self.assertIn("6199", compose)
        astrbot = json.loads((Path(self.tmp.name) / "data" / "astrbot" / "cmd_config.json").read_text(encoding="utf-8"))
        self.assertEqual(astrbot["platform"][0]["type"], "aiocqhttp")
        self.assertEqual(astrbot["platform"][0]["ws_reverse_token"], token)

    def test_onebot_deployment_requires_qq_account(self):
        with self.assertRaises(DeploymentError):
            self.manager.start_deploy(
                platform_mode="onebot",
                onebot_ws_port=6201,
                qq_account="",
                deepseek_api_key="sk-onebot-test-secret",
                prompt="你是测试 QQ 机器人。",
                accept_risk=True,
            )

    def test_onebot_deployment_status_exposes_ws_endpoint(self):
        config = self.store.load_config()
        config.update(
            {
                "deployed": True,
                "platform_mode": "onebot",
                "qq_account": "12345678",
                "onebot_ws_port": 6201,
                "ports": {"astrbot_webui": 6185},
            }
        )
        self.store.save_config(config)
        secrets_data = self.store.load_secrets()
        secrets_data["onebot_token"] = "onebot-status-token"
        self.store.save_secrets(secrets_data)
        self.docker.logs_by_service["astrbot"] = "aiocqhttp 适配器已连接。\n"
        deployment = self.manager.get_status()["deployment"]
        self.assertEqual(deployment["platform_mode"], "onebot")
        self.assertEqual(deployment["qq_account"], "12345678")
        self.assertEqual(deployment["onebot_ws_port"], 6201)
        self.assertEqual(deployment["onebot_ws_url"], "ws://127.0.0.1:6201/ws")
        self.assertEqual(deployment["onebot_token"], "onebot-status-token")
        self.assertTrue(deployment["platform_connected"])
        self.assertIn("onebot_ws", deployment["urls"])

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
        token = self.store.load_secrets()["astrbot_jwt_secret"]
        self.docker.logs_by_service["astrbot"] = (
            "provider key sk-log-secret-marker-12345\n"
            f"jwt secret {token}\n"
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
            official_appid="12345678",
            official_secret="official-secret",
            deepseek_api_key="sk-test-secret",
            prompt="prompt",
            accept_risk=True,
        )
        job = self._wait_job(job_id)
        self.assertEqual(job["status"], "error")
        self.assertIn("secrets.json", job["error"])
        self.assertEqual(self.manager._active_job_id, "")



    def test_state_surfaces_active_and_interrupted_jobs(self):
        job = Job(job_id="job-active")
        self.manager._jobs[job.job_id] = job
        self.manager._active_job_id = job.job_id
        state = self.manager.get_status()
        self.assertEqual(state["deployment"]["active_job_id"], "job-active")
        self.assertEqual(state["deployment"]["active_job_status"], "running")

        with self.manager._lock:
            self.manager._jobs.clear()
            self.manager._active_job_id = ""
        config = self.store.load_config()
        config["last_job_id"] = "job-interrupted"
        self.store.save_config(config)
        interrupted = {
            "status": "error",
            "step": "拉取镜像",
            "progress": 20,
            "message": "interrupted",
            "logs": [],
            "error": "服务中断",
            "job_id": "job-interrupted",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        (self.manager.jobs_dir / "job-interrupted.json").write_text(
            json.dumps(interrupted), encoding="utf-8"
        )
        state = self.manager.get_status()
        self.assertEqual(state["deployment"]["active_job_id"], "job-interrupted")
        self.assertEqual(state["deployment"]["active_job_status"], "error")

    def test_repair_containers_runs_up_when_container_stopped(self):
        config = self.store.load_config()
        config["deployed"] = True
        self.store.save_config(config)
        states = {"qqbot-astrbot": "exited"}

        def container_status(name):
            return states.get(name)

        self.docker.container_status = container_status
        self.docker.container_health = lambda name: "healthy"

        def fake_up(*args, **kwargs):
            states[self.docker.ASTRBOT_CONTAINER] = "running"
            self.docker.actions.append("up")
            return CommandResult(("up",), 0, "", "")

        self.docker.up = fake_up
        result = self.manager.repair_containers()
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "repaired")
        self.assertIn("up", self.docker.actions)

    def _mark_deployed(self):
        config = self.store.load_config()
        config["deployed"] = True
        config["ports"] = {"astrbot_webui": 16185}
        self.store.save_config(config)

    def _install_plugin_schema(self, extra: dict | None = None) -> Path:
        schema_source = (
            Path(__file__).resolve().parent.parent
            / "plugins"
            / "qqbot_enhancer"
            / "_conf_schema.json"
        )
        schema = json.loads(schema_source.read_text(encoding="utf-8"))
        if extra:
            schema.update(extra)
        plugin_dir = Path(self.tmp.name) / "data" / "astrbot" / "plugins" / "qqbot_enhancer"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "_conf_schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return plugin_dir

    def test_memory_mode_preserves_user_values_and_migrates_group_session(self):
        self._mark_deployed()
        plugin_path = Path(self.tmp.name) / "data" / "astrbot" / "config" / "qqbot_core_reply_config.json"
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        plugin_path.write_text(
            json.dumps(
                {
                    "memory_mode": "direct",
                    "reply_probability": 0.33,
                    "custom_user_value": "must-stay",
                }
            ),
            encoding="utf-8",
        )
        cmd_path = Path(self.tmp.name) / "data" / "astrbot" / "cmd_config.json"
        cmd_path.write_text(
            json.dumps(
                {
                    "provider": "keep-provider",
                    "platform_settings": {"unique_session": True, "keep": 7},
                }
            ),
            encoding="utf-8",
        )
        fake_api = FakePluginApi()
        fake_api.plugin_config = json.loads(plugin_path.read_text(encoding="utf-8"))
        self.manager._api_client_factory = lambda base, user, password: fake_api

        result = self.manager.set_memory_mode("astrbot")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["mode"], "astrbot")
        self.assertTrue(result["applied_live"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(fake_api.plugin_config["memory_mode"], "astrbot")
        self.assertEqual(fake_api.plugin_config["reply_probability"], 0.33)
        self.assertEqual(fake_api.plugin_config["custom_user_value"], "must-stay")
        saved_plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_plugin["memory_mode"], "astrbot")
        self.assertEqual(saved_plugin["custom_user_value"], "must-stay")
        saved_cmd = json.loads(cmd_path.read_text(encoding="utf-8"))
        self.assertIs(saved_cmd["platform_settings"]["unique_session"], False)
        self.assertEqual(saved_cmd["platform_settings"]["keep"], 7)
        self.assertEqual(saved_cmd["provider"], "keep-provider")

        direct = self.manager.set_memory_mode("direct")
        self.assertTrue(direct["ok"], direct.get("error"))
        self.assertEqual(direct["mode"], "direct")
        self.assertFalse(direct["restart_required"])
        self.assertEqual(fake_api.plugin_config["memory_mode"], "direct")
        self.assertEqual(fake_api.plugin_config["custom_user_value"], "must-stay")

    def test_core_reply_config_preserves_unknown_values_without_legacy_defaults(self):
        plugin_dir = Path(self.tmp.name) / "data" / "astrbot" / "plugins" / "qqbot_core_reply"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        config_path = Path(self.tmp.name) / "data" / "astrbot" / "config" / "qqbot_core_reply_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"memory_mode": "direct", "custom_user_value": "keep-me"}),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        DeployManager(
            store=Store(self.tmp.name),
            docker=self.docker,
            api_client_factory=lambda base, user, password: self.api,
        )
        migrated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["memory_mode"], "direct")
        self.assertEqual(migrated["custom_user_value"], "keep-me")
        self.assertNotIn("reply_probability", migrated)
        self.assertNotIn("system_prompt", migrated)
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_memory_mode_default_is_direct_for_empty_core_configs(self):
        plugin_path = Path(self.tmp.name) / "data" / "astrbot" / "config" / "qqbot_core_reply_config.json"
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        original = {"reply_probability": 0.2, "custom_user_value": "old"}
        plugin_path.write_text(json.dumps(original), encoding="utf-8")
        payload = self.manager.get_memory_mode()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "direct")
        self.assertEqual(payload["memory_scope"], "none")
        self.assertEqual(json.loads(plugin_path.read_text(encoding="utf-8")), original)
        # Reading the mode must not turn an explicit direct into astrbot or
        # rewrite/lose unknown plugin fields.
        self.assertEqual(json.loads(plugin_path.read_text(encoding="utf-8")), original)
        self.assertEqual(self.store.load_config()["memory_mode"], "direct")

    def test_plugin_inventory_and_toggle_use_real_api_contract(self):
        self._mark_deployed()
        plugin_dir = Path(self.tmp.name) / "data" / "astrbot" / "plugins" / "qqbot_enhancer"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "metadata.yaml").write_text(
            "name: qqbot_enhancer\ndisplay_name: QQ Bot 增强回复\nversion: 1.4.1\n",
            encoding="utf-8",
        )
        fake_api = FakePluginApi()
        self.manager._api_client_factory = lambda base, user, password: fake_api

        listing = self.manager.get_plugins()
        self.assertTrue(listing["ok"], listing.get("error"))
        self.assertTrue(listing["api_available"])
        self.assertEqual([item["id"] for item in listing["plugins"]], ["qqbot_enhancer", "builtin_help"])
        self.assertEqual(listing["plugins"][1]["source"], "builtin")
        self.assertFalse(listing["plugins"][1]["enabled"])

        toggled = self.manager.set_plugin_enabled("qqbot_enhancer", False)
        self.assertTrue(toggled["ok"])
        self.assertFalse(toggled["restart_required"])
        self.assertEqual(fake_api.toggled, [("qqbot_enhancer", False)])
        self.assertFalse(toggled["plugin"]["enabled"])

    def test_plugin_inventory_degrades_when_astrbot_is_offline(self):
        self._mark_deployed()
        plugin_dir = Path(self.tmp.name) / "data" / "astrbot" / "plugins" / "qqbot_enhancer"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "metadata.yaml").write_text("name: qqbot_enhancer\nversion: 1.4.1\n", encoding="utf-8")

        self.manager._api_client_factory = lambda base, user, password: OfflineApi()
        listing = self.manager.get_plugins()
        self.assertFalse(listing["ok"])
        self.assertFalse(listing["api_available"])
        self.assertIn("降级", listing["message"])
        self.assertEqual(listing["plugins"][0]["id"], "qqbot_enhancer")
        with self.assertRaises(DeploymentError):
            self.manager.set_plugin_enabled("qqbot_enhancer", True)

    def _write_local_plugin(self):
        plugin_dir = Path(self.tmp.name) / "data" / "astrbot" / "plugins" / "qqbot_enhancer"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "metadata.yaml").write_text(
            "name: qqbot_enhancer\ndisplay_name: QQ Bot 增强回复\nversion: 1.4.1\n",
            encoding="utf-8",
        )

    def test_plugin_list_retries_login_broken_pipe(self):
        self._mark_deployed()
        self._write_local_plugin()
        api = FlakyPluginApi(failures=1, action="login")
        self.manager._api_client_factory = lambda base, user, password: api

        with mock.patch.object(astrbot_api, "RETRY_DELAYS", ()):
            listing = self.manager.get_plugins()

        self.assertTrue(listing["ok"], listing.get("error"))
        self.assertTrue(listing["api_available"])
        self.assertEqual(listing["route"], "/api/v1/plugins")
        self.assertEqual(
            [item["id"] for item in listing["plugins"]], ["qqbot_enhancer", "builtin_help"]
        )
        self.assertEqual(api.list_calls, 1)
        # The stale token was dropped before the retry re-authenticated.
        self.assertGreaterEqual(api.invalidations, 1)
        # A retried read must never write plugin state again.
        self.assertEqual(api.updated_configs, [])
        self.assertEqual(api.toggled, [])

    def test_plugin_list_retries_mid_request_disconnect(self):
        self._mark_deployed()
        self._write_local_plugin()
        api = FlakyPluginApi(failures=1, action="list")
        self.manager._api_client_factory = lambda base, user, password: api

        with mock.patch.object(astrbot_api, "RETRY_DELAYS", ()):
            listing = self.manager.get_plugins()

        self.assertTrue(listing["ok"], listing.get("error"))
        self.assertEqual(api.list_calls, 2)
        self.assertEqual(api.updated_configs, [])
        self.assertEqual(api.toggled, [])

    def test_plugin_list_persistent_disconnect_is_bounded_and_actionable(self):
        self._mark_deployed()
        self._write_local_plugin()
        api = FlakyPluginApi(failures=99, action="login")
        self.manager._api_client_factory = lambda base, user, password: api

        with mock.patch.object(astrbot_api, "RETRY_DELAYS", ()):
            listing = self.manager.get_plugins()

        self.assertFalse(listing["ok"])
        self.assertFalse(listing["api_available"])
        # Bounded retries: one extra attempt, never an infinite loop.
        self.assertEqual(api.login_calls, 2)
        self.assertEqual(api.list_calls, 0)
        # The persistent error is surfaced with an actionable hint, not hidden.
        self.assertIn("Broken pipe", listing["error"])
        self.assertIn("重启", listing["error"])
        self.assertIn("降级", listing["message"])
        # Local plugin fallback still works.
        self.assertEqual([item["id"] for item in listing["plugins"]], ["qqbot_enhancer"])
        self.assertEqual(api.updated_configs, [])
        self.assertEqual(api.toggled, [])

    def test_plugin_list_auth_failure_is_not_retried(self):
        self._mark_deployed()
        self._write_local_plugin()
        api = UnauthorizedPluginApi()
        self.manager._api_client_factory = lambda base, user, password: api

        with mock.patch.object(astrbot_api, "RETRY_DELAYS", ()):
            listing = self.manager.get_plugins()

        self.assertFalse(listing["ok"])
        self.assertEqual(api.login_calls, 1)
        self.assertIn("用户名或密码错误", listing["error"])

    def test_plugin_writes_are_not_repeated_after_disconnect(self):
        self._mark_deployed()
        self._write_local_plugin()

        class DropWriteApi(FakePluginApi):
            def __init__(self):
                super().__init__()
                self.write_calls = []

            def _request(self, method, path, *, payload=None, token=None, timeout=None):
                if method in ("PUT", "PATCH", "POST") and path != "/api/v1/personas":
                    self.write_calls.append((method, path))
                    raise AstrBotTransientError(BROKEN_PIPE_MESSAGE)
                return super()._request(
                    method, path, payload=payload, token=token, timeout=timeout
                )

        api = DropWriteApi()
        with self.assertRaises(RuntimeError) as caught:
            self.manager._save_plugin_config_via_api(api, "qqbot_enhancer", {"reply_probability": 0.3})
        # Exactly one write attempt: the legacy route is not tried with the
        # application's outcome still unknown.
        self.assertEqual(api.write_calls, [("PUT", "/api/v1/plugins/config")])
        self.assertIn("Broken pipe", str(caught.exception))

        with self.assertRaises(RuntimeError):
            self.manager._set_plugin_enabled_via_api(api, "qqbot_enhancer", False)
        self.assertEqual(
            api.write_calls,
            [("PUT", "/api/v1/plugins/config"), ("PATCH", "/api/v1/plugins/enabled")],
        )

    def test_plugin_fallback_reads_persisted_disabled_state(self):
        self._mark_deployed()
        plugin_dir = Path(self.tmp.name) / "data" / "astrbot" / "plugins" / "qqbot_enhancer"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "metadata.yaml").write_text(
            "name: qqbot_enhancer\ndisplay_name: QQ Bot 增强回复\nversion: 1.4.1\n",
            encoding="utf-8",
        )
        db_path = Path(self.tmp.name) / "data" / "astrbot" / "data_v4.db"
        connection = sqlite3.connect(str(db_path))
        try:
            connection.execute(
                "CREATE TABLE preferences (scope TEXT, scope_id TEXT, key TEXT, value TEXT)"
            )
            connection.execute(
                "INSERT INTO preferences VALUES (?, ?, ?, ?)",
                (
                    "global",
                    "global",
                    "inactivated_plugins",
                    json.dumps({"val": ["data.plugins.qqbot_enhancer.main"]}),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self.manager._api_client_factory = lambda base, user, password: OfflineApi()
        listing = self.manager.get_plugins()
        self.assertFalse(listing["ok"])
        plugin = next(item for item in listing["plugins"] if item["id"] == "qqbot_enhancer")
        self.assertIs(plugin["enabled"], False)
        self.assertEqual(plugin["enabled_source"], "shared_preferences")
        self.assertEqual(plugin["name"], "QQ Bot 增强回复")

    def test_plugin_config_get_live_merge_and_safe_save(self):
        self._mark_deployed()
        self._install_plugin_schema()
        config_path = (
            Path(self.tmp.name)
            / "data"
            / "astrbot"
            / "config"
            / "qqbot_enhancer_config.json"
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        local_config = {
            "reply_probability": 0.2,
            "custom_user_value": "keep-local-unknown",
        }
        config_path.write_text(json.dumps(local_config), encoding="utf-8")

        fake_api = FakePluginApi()
        fake_api.plugin_config = {
            "reply_probability": 0.18,
            "memory_mode": "direct",
            "live_only_value": "keep-live-unknown",
            "api_key": "sk-plugin-internal-secret",
        }
        self.manager._api_client_factory = lambda base, user, password: fake_api

        result = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["api_available"])
        self.assertEqual(result["source"], "astrbot")
        self.assertIn("reply_probability", result["schema"])
        self.assertEqual(result["schema"]["reply_probability"]["type"], "float")
        self.assertEqual(result["values"]["reply_probability"], 0.18)
        self.assertEqual(result["values"]["image_search_timeout_seconds"], 12)
        self.assertNotIn("api_key", result["schema"])
        self.assertNotIn("api_key", result["values"])
        self.assertNotIn("sk-plugin-internal-secret", json.dumps(result, ensure_ascii=False))

        saved = self.manager.set_plugin_config(
            "qqbot_enhancer", {"reply_probability": 0.5}
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        self.assertTrue(saved["api_available"])
        self.assertFalse(saved["restart_required"])
        self.assertEqual(saved["values"]["reply_probability"], 0.5)
        self.assertNotIn("api_key", saved["values"])
        self.assertNotIn("sk-plugin-internal-secret", json.dumps(saved, ensure_ascii=False))
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

        stored = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["reply_probability"], 0.5)
        self.assertEqual(stored["custom_user_value"], "keep-local-unknown")
        self.assertIn("live_only_value", fake_api.updated_configs[-1])
        self.assertEqual(fake_api.updated_configs[-1]["reply_probability"], 0.5)

    def test_plugin_config_schema_defaults_without_local_or_api(self):
        self._install_plugin_schema()
        result = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(result["api_available"])
        self.assertEqual(result["source"], "schema")
        self.assertEqual(result["values"]["reply_probability"], 0.12)
        self.assertEqual(result["values"]["memory_mode"], "direct")
        self.assertFalse(self.manager.get_plugin_config("missing_plugin")["ok"])

    def test_plugin_config_offline_save_and_type_validation(self):
        config = self.store.load_config()
        config["deployed"] = True
        config["ports"] = {"astrbot_webui": 16185}
        self.store.save_config(config)
        self._install_plugin_schema(
            extra={
                "api_key": {
                    "description": "应被过滤的敏感字段",
                    "type": "string",
                    "default": "sk-schema-default-secret",
                }
            }
        )
        config_path = (
            Path(self.tmp.name)
            / "data"
            / "astrbot"
            / "config"
            / "qqbot_enhancer_config.json"
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "reply_probability": 0.2,
                    "api_key": "sk-local-config-secret",
                    "custom_user_value": "preserve-me",
                }
            ),
            encoding="utf-8",
        )

        self.manager._api_client_factory = lambda base, user, password: OfflineApi()
        loaded = self.manager.get_plugin_config("qqbot_enhancer")
        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertFalse(loaded["api_available"])
        self.assertEqual(loaded["source"], "local")
        self.assertNotIn("api_key", loaded["schema"])
        self.assertNotIn("api_key", loaded["values"])
        self.assertNotIn("sk-local-config-secret", json.dumps(loaded, ensure_ascii=False))
        self.assertNotIn("sk-schema-default-secret", json.dumps(loaded, ensure_ascii=False))

        saved = self.manager.set_plugin_config(
            "qqbot_enhancer", {"reply_probability": 0.5}
        )
        self.assertTrue(saved["ok"], saved.get("error"))
        self.assertFalse(saved["api_available"])
        self.assertTrue(saved["restart_required"])
        stored = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["reply_probability"], 0.5)
        self.assertEqual(stored["custom_user_value"], "preserve-me")
        self.assertEqual(stored["api_key"], "sk-local-config-secret")
        self.assertNotIn("sk-local-config-secret", json.dumps(saved, ensure_ascii=False))

        typed = self.manager.set_plugin_config(
            "qqbot_enhancer",
            {
                "image_search_enabled": False,
                "image_search_timeout_seconds": 12,
                "reply_probability": 0.5,
                "image_search_safe_search": "moderate",
                "image_search_success_prefix": "找到啦：",
                "ignore_command_prefixes": ["/", "!"],
            },
        )
        self.assertTrue(typed["ok"], typed.get("error"))
        self.assertFalse(typed["values"]["image_search_enabled"])
        self.assertEqual(typed["values"]["image_search_timeout_seconds"], 12)
        self.assertEqual(typed["values"]["reply_probability"], 0.5)
        self.assertEqual(typed["values"]["image_search_safe_search"], "moderate")
        self.assertEqual(typed["values"]["ignore_command_prefixes"], ["/", "!"])

        invalid_cases = [
            ("reply_probability", "0.5", "数字"),
            ("reply_probability", 1.5, "不能大于"),
            ("reply_probability", -0.1, "不能小于"),
            ("image_search_enabled", 1, "布尔"),
            ("image_search_timeout_seconds", "12", "整数"),
            ("image_search_timeout_seconds", 2, "不能小于"),
            ("image_search_safe_search", "unsafe", "允许选项"),
            ("image_search_success_prefix", 123, "字符串"),
            ("ignore_command_prefixes", "not-a-list", "数组"),
            ("ignore_command_prefixes", ["/", 123], "字符串"),
            ("does_not_exist", True, "不在插件 schema"),
            ("api_key", "sk-attempted-secret", "敏感字段"),
        ]
        before = config_path.read_text(encoding="utf-8")
        for field, value, expected in invalid_cases:
            rejected = self.manager.set_plugin_config(
                "qqbot_enhancer", {field: value}
            )
            self.assertFalse(rejected["ok"], (field, value, rejected))
            self.assertIn(expected, rejected["error"])
            self.assertNotIn("sk-attempted-secret", json.dumps(rejected, ensure_ascii=False))
        self.assertEqual(config_path.read_text(encoding="utf-8"), before)

        unsafe = self.manager.get_plugin_config("../other-plugin")
        self.assertFalse(unsafe["ok"])
        self.assertIn("plugin_id", unsafe["error"])
        unsafe_set = self.manager.set_plugin_config("../other-plugin", {"x": 1})
        self.assertFalse(unsafe_set["ok"])
        missing = self.manager.get_plugin_config("missing_plugin")
        self.assertFalse(missing["ok"])
        self.assertIn("schema", missing["error"])

if __name__ == "__main__":
    unittest.main()
