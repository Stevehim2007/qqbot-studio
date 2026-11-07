"""Fixed-RPC control bridge authentication/replay/transport tests."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
import urllib.request
from pathlib import Path

from qqbot_studio.plugin_control import (
    CONTROL_PROTOCOL_VERSION,
    ControlActorError,
    ControlAuthError,
    ControlBridge,
    ControlClient,
    ControlExpiredError,
    ControlInstanceError,
    ControlMethodError,
    ControlPolicy,
    ControlReplayError,
    ControlScopeError,
    ControlSecretStore,
    ControlServer,
    ControlUnavailable,
    DockerExecTransport,
    FIXED_RPC_METHODS,
    HttpControlTransport,
    LoopbackEndpoint,
    build_request,
    generate_control_secret,
    start_control_http_server,
)


def _server(secret: str, seen: list[str] | None = None, **policy_kwargs):
    seen = seen if seen is not None else []

    def handler(request):
        seen.append(request.method)
        return {"method": request.method, "instance_id": request.instance_id}

    policy = ControlPolicy.single("bot-1", "admin", **policy_kwargs)
    return ControlServer(secret, policy, {"tool.catalog": handler, "food.query": handler}), seen


class PluginControlTests(unittest.TestCase):
    def setUp(self):
        self.secret = generate_control_secret()

    def _request(self, *, instance="bot-1", actor="admin", scope=("admin", "global"), method="tool.catalog", body=None, **kwargs):
        return build_request(
            self.secret,
            instance_id=instance,
            actor_id=actor,
            scope=scope,
            method=method,
            body=body,
            **kwargs,
        )

    def test_fixed_method_table_contains_only_contract_methods(self):
        self.assertIn("food.query", FIXED_RPC_METHODS)
        self.assertIn("confirmation.confirm", FIXED_RPC_METHODS)
        self.assertIn("memory.confirm_clear", FIXED_RPC_METHODS)
        # No generic escape hatches are registered.
        for forbidden in ("exec", "shell", "sql", "eval", "request", "control.call", "../tool.catalog"):
            self.assertNotIn(forbidden, FIXED_RPC_METHODS)

    def test_unknown_method_is_rejected_before_handler(self):
        server, seen = _server(self.secret)
        with self.assertRaises(ControlMethodError):
            build_request(
                self.secret,
                instance_id="bot-1",
                actor_id="admin",
                scope=("admin",),
                method="os.system",
            )
        self.assertEqual(seen, [])

    def test_direct_signed_request_succeeds_and_response_is_signed(self):
        server, seen = _server(self.secret)
        request = self._request()
        response = server.handle_request(request)
        self.assertTrue(response["ok"])
        self.assertEqual(response["version"], CONTROL_PROTOCOL_VERSION)
        self.assertEqual(response["body"]["method"], "tool.catalog")
        self.assertEqual(len(response["signature"]), 64)
        self.assertEqual(seen, ["tool.catalog"])

    def test_replay_of_a_valid_nonce_is_rejected(self):
        server, seen = _server(self.secret)
        request = self._request()
        server.handle_request(request)
        with self.assertRaises(ControlReplayError):
            server.handle_request(request)
        self.assertEqual(seen, ["tool.catalog"])

    def test_expired_timestamp_is_rejected(self):
        server, seen = _server(self.secret)
        request = self._request(clock=lambda: 1000.0)
        with self.assertRaises(ControlExpiredError):
            server.handle_request(request)
        # Signature/timestamp failure must not consume a nonce or call a handler.
        self.assertEqual(seen, [])

    def test_wrong_instance_actor_and_scope_are_rejected(self):
        server, seen = _server(self.secret)
        with self.assertRaises(ControlInstanceError):
            server.handle_request(self._request(instance="bot-2"))
        with self.assertRaises(ControlActorError):
            server.handle_request(self._request(actor="eve"))
        with self.assertRaises(ControlScopeError):
            server.handle_request(self._request(scope=("root",)))
        self.assertEqual(seen, [])

    def test_tampered_body_or_signature_is_rejected(self):
        server, seen = _server(self.secret)
        request = self._request(body={"x": 1})
        tampered = dict(request)
        tampered["body"] = {"x": 2}
        with self.assertRaises(ControlAuthError):
            server.handle_request(tampered)
        tampered2 = dict(request)
        tampered2["signature"] = "0" * 64
        with self.assertRaises(ControlAuthError):
            server.handle_request(tampered2)
        self.assertEqual(seen, [])

    def test_unknown_envelope_field_is_rejected(self):
        server, seen = _server(self.secret)
        request = self._request()
        request["url"] = "http://evil.invalid"
        with self.assertRaises(ControlAuthError):
            server.handle_request(request)
        self.assertEqual(seen, [])

    def test_direct_http_transport_and_client_round_trip(self):
        server, _ = _server(self.secret)
        http_server, thread, endpoint = start_control_http_server(server)
        try:
            transport = HttpControlTransport(endpoint)
            client = ControlClient(
                self.secret,
                transport,
                instance_id="bot-1",
                actor_id="admin",
            )
            result = client.call("tool.catalog", scope=("admin", "global"), body={})
            self.assertEqual(result["method"], "tool.catalog")
        finally:
            http_server.shutdown()
            http_server.server_close()
            thread.join(3)

    def test_http_endpoint_rejects_non_loopback_host(self):
        with self.assertRaises(Exception):
            LoopbackEndpoint(host="0.0.0.0", port=6198)
        with self.assertRaises(Exception):
            LoopbackEndpoint(host="127.0.0.1", port=6198, path="/evil")

    def test_client_does_not_allow_unregistered_method_or_scope_escalation(self):
        server, _ = _server(self.secret)
        client = ControlClient(
            self.secret,
            lambda _payload: b"{}",
            instance_id="bot-1",
            actor_id="admin",
            allowed_scopes=("global",),
        )
        with self.assertRaises(ControlMethodError):
            client.call("os.system")
        with self.assertRaises(ControlScopeError):
            client.call("tool.catalog", scope=("admin",))

    def test_client_reports_bridge_unavailable_without_fake_success(self):
        client = ControlClient(
            self.secret,
            lambda _payload: (_ for _ in ()).throw(OSError("down")),
            instance_id="bot-1",
            actor_id="admin",
        )
        with self.assertRaises(ControlUnavailable):
            client.call("tool.catalog", scope=("admin", "global"))

    def test_secret_store_is_0600_atomic_and_does_not_expose_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = Path(tmp) / "bot-1"
            instance.mkdir()
            store = ControlSecretStore(instance)
            secret = store.ensure()
            self.assertGreaterEqual(len(secret), 32)
            self.assertTrue(store.path.exists())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            self.assertEqual(store.read(), secret)
            self.assertNotIn(secret.hex(), repr(store))
            bridge = ControlBridge("bot-1", store, enabled=False)
            status = bridge.status()
            self.assertFalse(status["ready"])
            self.assertNotIn(secret.hex(), json.dumps(status))

    def test_docker_exec_transport_uses_fixed_arguments(self):
        calls = []

        def runner(args, payload, timeout):
            calls.append((args, payload, timeout))
            return type("Result", (), {"returncode": 0, "stdout": b'{"ok":true}', "stderr": b""})()

        transport = DockerExecTransport("qqbot-bot-1-astrbot", runner=runner)
        output = transport.send(b'{"x":1}')
        self.assertEqual(output, b'{"ok":true}')
        args, payload, timeout = calls[0]
        self.assertEqual(args[:4], ["docker", "exec", "-i", "qqbot-bot-1-astrbot"])
        self.assertIn("python3", args)
        self.assertIn("-c", args)
        self.assertNotIn("shell=True", " ".join(args))
        self.assertEqual(payload, b'{"x":1}')
        self.assertLessEqual(timeout, 60)

    def test_invalid_container_name_is_rejected(self):
        with self.assertRaises(Exception):
            DockerExecTransport("qqbot-1; rm -rf /")


if __name__ == "__main__":
    unittest.main()
