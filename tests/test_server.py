import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from pathlib import Path

from qqbot_studio.dockerctl import DockerError
from qqbot_studio.server import build_arg_parser, create_server


class FakeManager:
    def __init__(self):
        self.logs_raise = False
        self.status_raise = False
        self.last_deploy = None
        self.memory_mode = "direct"
        self.toggled_plugins = []

    def get_plugins(self):
        return {
            "ok": True,
            "plugins": [
                {
                    "id": "qqbot_enhancer",
                    "name": "QQ Bot 增强回复",
                    "enabled": True,
                    "version": "1.4.1",
                    "description": "测试插件",
                    "source": "installed",
                    "restart_required": False,
                    "error": "",
                }
            ],
            "api_available": True,
            "cached": False,
            "restart_required": False,
            "message": "已读取插件列表。",
            "error": "",
        }

    def set_plugin_enabled(self, plugin_id, enabled):
        self.toggled_plugins.append((plugin_id, bool(enabled)))
        return {
            "ok": True,
            "plugin": {
                "id": plugin_id,
                "name": "QQ Bot 增强回复",
                "enabled": bool(enabled),
                "version": "1.4.1",
                "description": "测试插件",
                "source": "installed",
                "restart_required": False,
                "error": "",
            },
            "restart_required": False,
            "message": "插件状态已保存。",
            "error": "",
        }

    def get_memory_mode(self):
        return {
            "ok": True,
            "mode": self.memory_mode,
            "memory_mode": self.memory_mode,
            "options": ["direct", "astrbot"],
            "memory_scope": "session" if self.memory_mode == "astrbot" else "none",
            "restart_required": False,
            "message": "当前记忆模式。",
            "error": "",
        }

    def set_memory_mode(self, mode):
        if mode not in {"direct", "astrbot"}:
            raise ValueError("记忆模式只能是 direct 或 astrbot")
        self.memory_mode = mode
        return {
            "ok": True,
            "mode": mode,
            "memory_mode": mode,
            "options": ["direct", "astrbot"],
            "memory_scope": "session" if mode == "astrbot" else "none",
            "restart_required": True,
            "message": "记忆模式已保存。",
            "error": "",
        }

    def get_status(self):
        if self.status_raise:
            raise RuntimeError("模拟状态读取失败")
        return {
            "app_version": "2.0.0",
            "requirements": {
                "docker_installed": True,
                "docker_running": True,
                "compose_installed": True,
                "permission_ok": True,
                "os": "Ubuntu 24.04",
            },
            "deployment": {
                "deployed": False,
                "running": False,
                "qq_account": "12345678",
                "model": "deepseek-flash",
                "prompt": "测试提示词",
                "urls": {"novnc": "http://10.0.0.2:6081"},
                "credentials": {},
                "onebot_connected": None,
                "qq_logged_in": False,
                "active_job_id": "job-1",
                "active_job_status": "running",
            },
            "notice": "",
        }

    def get_job(self, job_id):
        if job_id == "job-1":
            return {"status": "running", "step": "拉取镜像", "progress": 30, "message": "ok", "logs": [], "error": ""}
        return None

    def get_logs(self, service, lines):
        if self.logs_raise:
            raise DockerError("Docker 不可用")
        return f"{service}:{lines}\nline 2"

    def test_deepseek_key(self, key):
        return {"ok": key.startswith("sk-"), "message": "测试响应"}

    def start_deploy(self, **kwargs):
        self.last_deploy = kwargs
        assert kwargs["qq_account"] == "12345678"
        assert kwargs["mirror_candidates"] == ["mirror.example/", "m.daocloud.io/docker.io/"]
        return "job-1"

    def container_action(self, action):
        return f"{action} 已执行"

    def update_persona(self, prompt):
        return "提示词已更新"

    def reset_snowluma_password(self):
        return {"ok": True, "username": "admin", "password": "NewSnowPass123", "message": "reset"}


class FakeStudio:
    def __init__(self):
        self.manager = FakeManager()
        self.root = Path(tempfile.gettempdir()) / "qqbot-studio-fake-test"
        self.settings = {
            "bind_host": "127.0.0.1",
            "bind_port": 8765,
            "public_host": "10.0.0.2",
            "first_run_complete": True,
            "selected_mirror_id": "custom",
        }
        self.instances = [
            {
                "id": "bot-1",
                "name": "测试机器人",
                "created_at": "2026-01-01T00:00:00+00:00",
                "deployed": False,
                "running": False,
                "qq_account": "",
                "urls": {},
                "active_job_id": "job-1",
                "active_job_status": "running",
            }
        ]

    def load_settings(self):
        return dict(self.settings)

    def public_host(self):
        return "10.0.0.2"

    def latest_instance_id(self):
        return "bot-1"

    def get_instance_meta(self, instance_id):
        if instance_id != "bot-1":
            raise ValueError("实例不存在")
        return {"id": "bot-1", "name": "测试机器人"}

    def get_manager(self, instance_id):
        if instance_id != "bot-1":
            raise ValueError("实例不存在")
        return self.manager

    def list_instance_summaries(self):
        return self.instances

    def create_instance(self, name=None):
        meta = {"id": "bot-2", "name": name or "QQ 机器人 2", "created_at": "2026-01-02T00:00:00+00:00"}
        self.instances.append({**meta, "deployed": False, "running": None, "qq_account": "", "urls": {}, "active_job_id": "", "active_job_status": None})
        return meta

    def list_mirror_nodes(self):
        return [
            {"id": "daocloud", "name": "DaoCloud", "prefix": "m.daocloud.io/docker.io/", "custom": False, "selected": False, "latency_ms": 30, "status": "ok"},
            {"id": "custom", "name": "我的镜像", "prefix": "mirror.example/", "custom": True, "selected": True, "latency_ms": 12, "status": "ok"},
        ]

    def add_mirror_node(self, name, prefix):
        if not name or not prefix:
            raise ValueError("缺少参数")
        return {"id": "custom-2", "name": name, "prefix": prefix, "custom": True}

    def delete_mirror_node(self, node_id):
        if node_id == "daocloud":
            raise ValueError("内置节点不能删除")

    def select_mirror(self, node_id):
        if node_id not in {"daocloud", "custom"}:
            raise ValueError("镜像节点不存在")
        self.settings["selected_mirror_id"] = node_id

    def test_mirrors(self, ids=None):
        return [{"id": "custom", "latency_ms": 12, "status": "ok", "error": ""}]

    def candidate_mirror_prefixes(self):
        return ["mirror.example/", "m.daocloud.io/docker.io/"]

    def has_active_deployments(self):
        return False

    def get_stats(self, instance_id=None, *, force_refresh=False):
        return {
            "ok": True,
            "scope": "instance" if instance_id else "global",
            "instance_id": instance_id or "",
            "host_memory": {"total_bytes": 1024, "available_bytes": 512, "used_bytes": 512, "used_percent": 50.0},
            "instances": [
                {
                    "instance_id": instance_id or "bot-1",
                    "data_dir_size_bytes": 1234,
                    "file_count": 3,
                    "errors": [],
                }
            ],
            "docker": [
                {
                    "instance_id": instance_id or "bot-1",
                    "service": "astrbot",
                    "cpu_percent": 1.5,
                    "memory_usage_bytes": 100,
                    "memory_limit_bytes": 1000,
                    "memory_percent": 10.0,
                    "error": "",
                }
            ],
            "global": {"data_dir_size_bytes": 5000, "instances_total_bytes": 1234, "disk": {"free_bytes": 9000}},
            "errors": [],
            "error": "",
        }

    def assistant_status(self):
        return {
            "ok": True,
            "installed": False,
            "path": "/tmp/qqbot-studio/extensions/ai-assistant",
            "repo": "https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git",
            "commit": "",
            "entry": "/tmp/qqbot-studio/extensions/ai-assistant/assistant.py",
            "readme": "/tmp/qqbot-studio/extensions/ai-assistant/README.md",
            "message": "AI 助手 B 尚未安装。A 离线助手不受影响。",
            "error": "",
        }

    def install_assistant(self):
        return {
            **self.assistant_status(),
            "ok": True,
            "installed": True,
            "commit": "c15fb5b",
            "action": "installed",
            "message": "AI 助手 B 已安装。",
        }

    def update_assistant(self):
        return {
            **self.assistant_status(),
            "ok": True,
            "installed": True,
            "commit": "newcommit",
            "action": "updated",
            "updated": True,
            "message": "AI 助手 B 已更新。",
        }

    def delete_instance(self, instance_id):
        if instance_id != "bot-1":
            raise ValueError("实例不存在")
        return {"id": instance_id, "name": "测试机器人"}


class CliTests(unittest.TestCase):
    def test_port_can_come_from_environment(self):
        with mock.patch.dict(os.environ, {"QQBOT_STUDIO_PORT": "8765"}, clear=False):
            args = build_arg_parser().parse_args([])
        self.assertEqual(args.port, 8765)
        self.assertEqual(build_arg_parser().parse_args(["--port", "9000"]).port, 9000)

    def test_invalid_environment_port_uses_default(self):
        with mock.patch.dict(os.environ, {"QQBOT_STUDIO_PORT": "not-a-port"}, clear=False):
            args = build_arg_parser().parse_args([])
        self.assertEqual(args.port, 8765)

    def test_default_host_is_all_interfaces(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            args = build_arg_parser().parse_args([])
        self.assertEqual(args.host, "0.0.0.0")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.studio = FakeStudio()
        self.server = create_server(self.studio, host="127.0.0.1", port=0, token="test-token-123")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _get(self, path, *, token=True):
        request = urllib.request.Request(self.base + path)
        if token:
            request.add_header("X-QB-Token", "test-token-123")
        with self.opener.open(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")

    def _post(self, path, payload, *, token=True):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-QB-Token", "test-token-123")
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def _delete(self, path, *, token=True):
        request = urllib.request.Request(self.base + path, method="DELETE")
        if token:
            request.add_header("X-QB-Token", "test-token-123")
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_wizard_and_console_inject_token(self):
        status, html = self._get("/wizard?instance=bot-1", token=False)
        self.assertEqual(status, 200)
        self.assertIn("test-token-123", html)
        self.assertNotIn('window.QB_TOKEN = "{{TOKEN}}"', html)
        status, html = self._get("/console", token=False)
        self.assertEqual(status, 200)
        self.assertIn("test-token-123", html)


    def test_remote_bind_accepts_external_host_header(self):
        server = create_server(self.studio, host="0.0.0.0", port=0, token="remote-token-123")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _, port = server.server_address[:2]
            request = urllib.request.Request(f"http://127.0.0.1:{port}/console")
            request.add_header("Host", f"203.0.113.10:{port}")
            with self.opener.open(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("remote-token-123", response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_console_redirect_and_api_state(self):
        # urllib follows redirects; / should end up on the console page.
        status, body = self._get("/", token=False)
        self.assertEqual(status, 200)
        status, body = self._get("/api/state?instance=bot-1")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["deployment"]["model"], "deepseek-flash")
        self.assertEqual(data["deployment"]["active_job_id"], "job-1")
        self.assertEqual(data["instance"]["id"], "bot-1")
        status, body = self._get("/api/instances")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["instances"])

    def test_api_state_without_token_is_forbidden(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/state?instance=bot-1", token=False)
        self.assertEqual(ctx.exception.code, 403)

    def test_assistant_status_install_and_update_contract(self):
        status, body = self._get("/api/assistant/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["installed"])
        for field in ("ok", "installed", "path", "repo", "commit", "entry", "readme", "message", "error"):
            self.assertIn(field, payload)

        status, body = self._post("/api/assistant/install", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["installed"])
        self.assertEqual(payload["action"], "installed")
        self.assertEqual(payload["commit"], "c15fb5b")

        status, body = self._post("/api/assistant/update", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["updated"])
        self.assertEqual(payload["action"], "updated")
        self.assertNotIn("sk-", body)
        self.assertNotIn("api_key", body.lower())

    def test_assistant_install_accepts_empty_json_post_body(self):
        request = urllib.request.Request(
            self.base + "/api/assistant/install",
            data=b"",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-QB-Token": "test-token-123",
                "Content-Length": "0",
            },
        )
        with self.opener.open(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["installed"])

    def test_assistant_status_requires_token(self):
        request = urllib.request.Request(self.base + "/api/assistant/status")
        try:
            with self.opener.open(request, timeout=5) as response:
                status = response.status
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read().decode("utf-8")
        self.assertEqual(status, 403)
        self.assertFalse(json.loads(body)["ok"])
        status, body = self._post("/api/assistant/install", {}, token=False)
        self.assertEqual(status, 403)

    def test_v3_plugin_memory_and_stats_routes(self):
        status, body = self._get("/api/instances/bot-1/plugins")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plugins"][0]["id"], "qqbot_enhancer")

        status, body = self._post(
            "/api/instances/bot-1/plugins",
            {"plugin_id": "qqbot_enhancer", "enabled": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["plugin"]["enabled"], False)
        self.assertEqual(self.studio.manager.toggled_plugins, [("qqbot_enhancer", False)])

        status, body = self._get("/api/instances/bot-1/memory")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "direct")
        status, body = self._post("/api/instances/bot-1/memory", {"mode": "astrbot"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["restart_required"])
        self.assertEqual(self.studio.manager.memory_mode, "astrbot")

        status, body = self._get("/api/stats?instance=bot-1")
        self.assertEqual(status, 200)
        stats = json.loads(body)
        self.assertEqual(stats["host_memory"]["used_percent"], 50.0)
        self.assertEqual(stats["instances"][0]["data_dir_size_bytes"], 1234)

        status, body = self._get("/api/instances/bot-1/stats")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["docker"][0]["cpu_percent"], 1.5)

    def test_v3_memory_and_plugin_legacy_query_aliases(self):
        status, body = self._get("/api/memory?instance=bot-1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "direct")
        status, body = self._post("/api/memory?instance=bot-1", {"mode": "astrbot"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "astrbot")
        status, body = self._post(
            "/api/plugins?instance=bot-1",
            {"plugin_id": "qqbot_enhancer", "enabled": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["plugin"]["enabled"])

    def test_v3_plugin_toggle_rejects_non_boolean_enabled(self):
        status, body = self._post(
            "/api/instances/bot-1/plugins",
            {"plugin_id": "qqbot_enhancer", "enabled": "false"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)["ok"])

    def test_post_deploy_and_job_contract(self):
        status, body = self._post("/api/test-key?instance=bot-1", {"deepseek_api_key": "sk-test"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, body = self._post(
            "/api/deploy?instance=bot-1",
            {
                "qq_account": "12345678",
                "deepseek_api_key": "sk-test",
                "prompt": "prompt",
                "accept_risk": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["job_id"], "job-1")
        self.assertEqual(self.studio.manager.last_deploy["mirror_candidates"], ["mirror.example/", "m.daocloud.io/docker.io/"])
        status, body = self._get("/api/job/job-1?instance=bot-1")
        self.assertEqual(json.loads(body)["status"], "running")
        status, body = self._get("/api/logs?instance=bot-1&service=astrbot&lines=50")
        self.assertTrue(json.loads(body)["ok"])

    def test_post_without_token_is_forbidden(self):
        status, body = self._post("/api/test-key?instance=bot-1", {"deepseek_api_key": "sk-test"}, token=False)
        self.assertEqual(status, 403)
        self.assertFalse(json.loads(body)["ok"])

    def test_instance_creation_and_mirror_management(self):
        status, body = self._post("/api/instances", {"name": "新机器人"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["instance"]["id"], "bot-2")
        status, body = self._get("/api/mirrors")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["nodes"]), 2)
        status, body = self._post("/api/mirrors", {"name": "新节点", "prefix": "registry.example/"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["node"]["id"], "custom-2")
        status, body = self._post("/api/mirrors/test", {"ids": ["custom"]})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["results"])
        status, body = self._post("/api/mirrors/select", {"id": "custom"})
        self.assertEqual(status, 200)
        status, body = self._delete("/api/mirrors/custom-2")
        self.assertEqual(status, 200)
        status, body = self._delete("/api/mirrors/daocloud")
        self.assertEqual(status, 400)


    def test_snowluma_reset_contract(self):
        status, body = self._post("/api/snowluma-reset?instance=bot-1", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["username"], "admin")
        self.assertEqual(payload["password"], "NewSnowPass123")

    def test_update_check_and_apply_contract(self):
        with mock.patch(
            "qqbot_studio.server.check_update",
            return_value={"ok": True, "can_update": True, "behind": 2, "message": "2 commits"},
        ) as check:
            status, body = self._get("/api/update/check")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["can_update"])
        check.assert_called_once()

        with mock.patch(
            "qqbot_studio.server.start_update",
            return_value={"ok": True, "restarting": True, "message": "started"},
        ) as start:
            status, body = self._post("/api/update/apply", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["restarting"])
        start.assert_called_once()


    def test_state_failure_returns_diagnosable_200(self):
        self.studio.manager.status_raise = True
        status, body = self._get("/api/state?instance=bot-1")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("模拟状态读取失败", payload["status_error"])
        self.assertEqual(payload["instance"]["id"], "bot-1")

    def test_docker_errors_map_to_503_json(self):
        self.studio.manager.logs_raise = True
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/logs?instance=bot-1&service=astrbot&lines=10")
        self.assertEqual(ctx.exception.code, 503)
        payload = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertIn("Docker", payload["message"])

    def test_options_returns_json_405_without_cors(self):
        request = urllib.request.Request(self.base + "/api/state", method="OPTIONS")
        try:
            with self.opener.open(request, timeout=5) as response:
                self.fail("OPTIONS should not be accepted")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 405)
            self.assertEqual(exc.headers.get("Allow"), "GET, HEAD, POST")
            self.assertIsNone(exc.headers.get("Access-Control-Allow-Origin"))
            payload = json.loads(exc.read().decode("utf-8"))
            self.assertFalse(payload["ok"])


    def test_delete_instance_contract(self):
        status, body = self._delete("/api/instances/bot-1")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["instance"]["id"], "bot-1")
        status, body = self._delete("/api/instances/missing")
        self.assertEqual(status, 400)

    def test_unknown_job_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/job/missing?instance=bot-1")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
