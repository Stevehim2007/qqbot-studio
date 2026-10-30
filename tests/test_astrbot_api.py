import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qqbot_studio.astrbot_api import AstrBotClient


class FakeAstrBotHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    setup_calls = 0
    login_calls = 0
    create_calls = 0
    update_calls = 0
    last_update_payload = None
    last_auth = ""

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/api/auth/setup-status", "/api/v1/auth/setup-status"):
            type(self).setup_calls += 1
            self._send(200, {"status": "ok", "data": {"setup_required": False}})
        else:
            self._send(404, {"status": "error", "message": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body or b"{}")
        if self.path in ("/api/v1/auth/login", "/api/auth/login"):
            type(self).login_calls += 1
            assert payload["username"] == "admin"
            assert payload["password"] == "Secret123"
            self._send(200, {"status": "ok", "data": {"token": "jwt-test-token"}})
            return
        if self.path == "/api/v1/personas":
            type(self).create_calls += 1
            type(self).last_auth = self.headers.get("Authorization", "")
            self._send(400, {"status": "error", "message": "persona already exists"})
            return
        if self.path == "/api/persona/update":
            type(self).update_calls += 1
            type(self).last_update_payload = payload
            type(self).last_auth = self.headers.get("Authorization", "")
            self._send(200, {"status": "ok", "data": {}})
            return
        self._send(404, {"status": "error", "message": "not found"})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body or b"{}")
        if self.path == "/api/v1/personas/by-id":
            type(self).update_calls += 1
            type(self).last_update_payload = payload
            type(self).last_auth = self.headers.get("Authorization", "")
            self._send(200, {"status": "ok", "data": {}})
            return
        self._send(404, {"status": "error", "message": "not found"})


class AstrBotClientTests(unittest.TestCase):
    def setUp(self):
        FakeAstrBotHandler.setup_calls = 0
        FakeAstrBotHandler.login_calls = 0
        FakeAstrBotHandler.create_calls = 0
        FakeAstrBotHandler.update_calls = 0
        FakeAstrBotHandler.last_update_payload = None
        FakeAstrBotHandler.last_auth = ""
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeAstrBotHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.client = AstrBotClient(f"http://{host}:{port}", "admin", "Secret123", timeout=3)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def test_ready_login_and_persona_update(self):
        self.client.wait_until_ready(timeout=5)
        self.assertEqual(FakeAstrBotHandler.setup_calls, 1)
        self.client.upsert_persona("qqbot-assistant", "你是测试机器人。")
        self.assertEqual(FakeAstrBotHandler.login_calls, 1)
        self.assertEqual(FakeAstrBotHandler.create_calls, 1)
        self.assertEqual(FakeAstrBotHandler.update_calls, 1)
        self.assertEqual(FakeAstrBotHandler.last_auth, "Bearer jwt-test-token")
        self.assertEqual(FakeAstrBotHandler.last_update_payload["persona_id"], "qqbot-assistant")
        self.assertEqual(FakeAstrBotHandler.last_update_payload["system_prompt"], "你是测试机器人。")

    def test_invalid_persona_is_rejected_locally(self):
        with self.assertRaises(ValueError):
            self.client.upsert_persona("", "prompt")
        with self.assertRaises(ValueError):
            self.client.upsert_persona("persona", "  ")


if __name__ == "__main__":
    unittest.main()
