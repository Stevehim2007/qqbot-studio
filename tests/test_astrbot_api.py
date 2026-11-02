import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from qqbot_studio import astrbot_api
from qqbot_studio.astrbot_api import AstrBotAPIError, AstrBotClient, AstrBotTransientError


class FakeAstrBotHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    setup_calls = 0
    login_calls = 0
    create_calls = 0
    update_calls = 0
    last_update_payload = None
    last_auth = ""
    login_status = 200
    drop_login_once = False
    dropped_logins = 0
    plugin_list_calls = 0
    drop_plugin_list_once = False
    dropped_plugin_lists = 0

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drop_connection(self):
        """Behave like a restarting AstrBot: accept, then close silently."""

        self.close_connection = True
        try:
            self.connection.close()
        except OSError:
            pass

    def do_GET(self):
        if self.path in ("/api/auth/setup-status", "/api/v1/auth/setup-status"):
            type(self).setup_calls += 1
            self._send(200, {"status": "ok", "data": {"setup_required": False}})
        elif self.path == "/api/v1/plugins":
            type(self).plugin_list_calls += 1
            if type(self).drop_plugin_list_once:
                type(self).drop_plugin_list_once = False
                type(self).dropped_plugin_lists += 1
                self._drop_connection()
                return
            if self.headers.get("Authorization") != "Bearer jwt-test-token":
                self._send(401, {"status": "error", "message": "未授权"})
                return
            self._send(
                200,
                {
                    "status": "ok",
                    "message": "",
                    "data": [
                        {
                            "name": "qqbot_enhancer",
                            "display_name": "QQ Bot 增强回复",
                            "version": "1.4.1",
                            "reserved": False,
                            "activated": True,
                        }
                    ],
                },
            )
        else:
            self._send(404, {"status": "error", "message": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body or b"{}")
        if self.path in ("/api/v1/auth/login", "/api/auth/login"):
            if type(self).drop_login_once:
                type(self).drop_login_once = False
                type(self).dropped_logins += 1
                self._drop_connection()
                return
            if type(self).login_status != 200:
                self._send(type(self).login_status, {"status": "error", "message": "用户名或密码错误"})
                return
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


class BrokenPipeOpener:
    """Fails the first ``failures`` open() calls with the reported EPIPE."""

    def __init__(self, inner, failures=1):
        self.inner = inner
        self.failures = failures
        self.calls = 0
        self.closed = 0

    def open(self, request, timeout=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise BrokenPipeError(32, "Broken pipe")
        return self.inner.open(request, timeout=timeout)

    def close(self):
        self.closed += 1


class AstrBotClientTests(unittest.TestCase):
    def setUp(self):
        FakeAstrBotHandler.setup_calls = 0
        FakeAstrBotHandler.login_calls = 0
        FakeAstrBotHandler.create_calls = 0
        FakeAstrBotHandler.update_calls = 0
        FakeAstrBotHandler.last_update_payload = None
        FakeAstrBotHandler.last_auth = ""
        FakeAstrBotHandler.login_status = 200
        FakeAstrBotHandler.drop_login_once = False
        FakeAstrBotHandler.dropped_logins = 0
        FakeAstrBotHandler.plugin_list_calls = 0
        FakeAstrBotHandler.drop_plugin_list_once = False
        FakeAstrBotHandler.dropped_plugin_lists = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeAstrBotHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.client = AstrBotClient(f"http://{host}:{port}", "admin", "Secret123", timeout=3)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    # -- helpers -------------------------------------------------------
    def _use_flaky_opener(self, failures=1):
        """Make every (re)built opener fail ``failures`` times with EPIPE."""

        opener = BrokenPipeOpener(self.client._opener, failures=failures)
        patcher = mock.patch.object(
            AstrBotClient, "_build_opener", staticmethod(lambda: opener)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client._opener = opener
        return opener

    def _no_retry_delay(self):
        patcher = mock.patch.object(astrbot_api, "RETRY_DELAYS", ())
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- existing behaviour --------------------------------------------
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

    # -- transient disconnect recovery ---------------------------------
    def test_login_retries_broken_pipe_then_succeeds(self):
        self._no_retry_delay()
        opener = self._use_flaky_opener(failures=1)
        self.assertEqual(self.client.login(), "jwt-test-token")
        self.assertEqual(opener.calls, 2)
        # The half-closed opener was closed and rebuilt before the retry.
        self.assertGreaterEqual(opener.closed, 1)
        self.assertEqual(FakeAstrBotHandler.login_calls, 1)

    def test_login_force_drops_stale_token_and_recovers(self):
        self._no_retry_delay()
        opener = self._use_flaky_opener(failures=1)
        self.client._token = "stale-token-from-before-restart"
        self.assertEqual(self.client.login(force=True), "jwt-test-token")
        self.assertEqual(self.client._token, "jwt-test-token")
        self.assertEqual(opener.calls, 2)
        self.assertEqual(FakeAstrBotHandler.login_calls, 1)

    def test_force_login_failure_leaves_no_stale_token(self):
        self._no_retry_delay()
        opener = self._use_flaky_opener(failures=99)
        self.client._token = "stale-token-from-before-restart"
        with self.assertRaises(AstrBotTransientError) as caught:
            self.client.login(force=True)
        # Bounded: two retries only, and the legacy route is not hammered with
        # the same dead connection.
        self.assertEqual(opener.calls, 3)
        self.assertEqual(self.client._token, "")
        self.assertIn("[Errno 32] Broken pipe", str(caught.exception))
        self.assertIn("重启", str(caught.exception))

    def test_login_recovers_when_server_closes_connection_once(self):
        self._no_retry_delay()
        FakeAstrBotHandler.drop_login_once = True
        self.assertEqual(self.client.login(), "jwt-test-token")
        self.assertEqual(FakeAstrBotHandler.dropped_logins, 1)
        self.assertEqual(FakeAstrBotHandler.login_calls, 1)

    def test_auth_failure_is_not_retried(self):
        self._no_retry_delay()
        FakeAstrBotHandler.login_status = 401
        with self.assertRaises(AstrBotAPIError) as caught:
            self.client.login()
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("密码", str(caught.exception))
        self.assertEqual(FakeAstrBotHandler.login_calls, 0)

    def test_plugin_list_read_retries_broken_pipe_then_succeeds(self):
        self._no_retry_delay()
        token = self.client.login()
        opener = self._use_flaky_opener(failures=1)
        listing = astrbot_api.call_with_retry(
            lambda: self.client._request("GET", "/api/v1/plugins", token=token)
        )
        self.assertEqual(opener.calls, 2)
        self.assertEqual(FakeAstrBotHandler.plugin_list_calls, 1)
        self.assertEqual(listing["data"][0]["name"], "qqbot_enhancer")

    def test_plugin_list_read_recovers_when_server_drops_once(self):
        self._no_retry_delay()
        token = self.client.login()
        FakeAstrBotHandler.drop_plugin_list_once = True
        listing = astrbot_api.call_with_retry(
            lambda: self.client._request("GET", "/api/v1/plugins", token=token)
        )
        self.assertEqual(FakeAstrBotHandler.dropped_plugin_lists, 1)
        self.assertEqual(listing["data"][0]["name"], "qqbot_enhancer")

    def test_persistent_disconnect_is_bounded_and_reported(self):
        self._no_retry_delay()
        opener = self._use_flaky_opener(failures=99)
        outcomes = 0
        for _ in range(2):
            with self.assertRaises(AstrBotTransientError):
                astrbot_api.call_with_retry(lambda: self.client._request("GET", "/api/v1/plugins"))
            outcomes += 1
        self.assertEqual(outcomes, 2)
        self.assertEqual(opener.calls, 6)  # 3 attempts per call, never unbounded


if __name__ == "__main__":
    unittest.main()
