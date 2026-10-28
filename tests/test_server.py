import json
import os
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

from qqbot_studio.dockerctl import DockerError
from qqbot_studio.server import build_arg_parser, create_server


class FakeManager:
    def get_status(self):
        return {
            "app_version": "1.0.0",
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
                "urls": {},
                "credentials": {},
                "onebot_connected": None,
                "qq_logged_in": False,
            },
            "notice": "",
        }

    def get_job(self, job_id):
        if job_id == "job-1":
            return {"status": "success", "step": "完成", "progress": 100, "message": "ok", "logs": [], "error": ""}
        return None

    def get_logs(self, service, lines):
        return f"{service}:{lines}\nline 2"

    def test_deepseek_key(self, key):
        return {"ok": key.startswith("sk-"), "message": "测试响应"}

    def start_deploy(self, **kwargs):
        assert kwargs["qq_account"] == "12345678"
        return "job-1"

    def container_action(self, action):
        return f"{action} 已执行"

    def update_persona(self, prompt):
        return "提示词已更新"



class CliTests(unittest.TestCase):
    def test_port_can_come_from_environment(self):
        with mock.patch.dict(os.environ, {"QQBOT_STUDIO_PORT": "8765"}, clear=False):
            args = build_arg_parser().parse_args([])
        self.assertEqual(args.port, 8765)
        self.assertEqual(build_arg_parser().parse_args(["--port", "9000"]).port, 9000)

    def test_invalid_environment_port_falls_back_to_random(self):
        with mock.patch.dict(os.environ, {"QQBOT_STUDIO_PORT": "not-a-port"}, clear=False):
            args = build_arg_parser().parse_args([])
        self.assertEqual(args.port, 0)

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeManager()
        self.server = create_server(self.manager, port=0, token="test-token-123")
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

    def test_index_contains_injected_token_and_no_placeholder(self):
        status, html = self._get("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn("test-token-123", html)
        self.assertNotIn('window.QB_TOKEN = "{{TOKEN}}"' , html)

    def test_api_state_requires_token(self):
        status, body = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["deployment"]["model"], "deepseek-flash")

    def test_api_state_without_token_is_forbidden(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/state", token=False)
        self.assertEqual(ctx.exception.code, 403)

    def test_post_operations_and_job_contract(self):
        status, body = self._post("/api/test-key", {"deepseek_api_key": "sk-test"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, body = self._post(
            "/api/deploy",
            {
                "qq_account": "12345678",
                "deepseek_api_key": "sk-test",
                "prompt": "prompt",
                "use_mirror": False,
                "accept_risk": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["job_id"], "job-1")
        status, body = self._get("/api/job/job-1")
        self.assertEqual(json.loads(body)["status"], "success")
        status, body = self._get("/api/logs?service=astrbot&lines=50")
        self.assertTrue(json.loads(body)["ok"])

    def test_post_without_token_is_forbidden(self):
        status, body = self._post("/api/test-key", {"deepseek_api_key": "sk-test"}, token=False)
        self.assertEqual(status, 403)
        self.assertFalse(json.loads(body)["ok"])


    def test_docker_errors_map_to_503_json(self):
        original = self.manager.get_logs
        self.manager.get_logs = lambda service, lines: (_ for _ in ()).throw(DockerError("Docker 不可用"))
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._get("/api/logs?service=astrbot&lines=10")
        finally:
            self.manager.get_logs = original
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

    def test_unknown_job_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/job/missing")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
