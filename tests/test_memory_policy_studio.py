"""Studio API tests for the per-group long-term memory policy editor."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from qqbot_studio.deployer import (
    MEMORY_POLICY_MAX_GROUPS,
    DeploymentError,
    DeployManager,
    MemoryPolicyUnsupportedError,
)
from qqbot_studio.server import create_server
from qqbot_studio.store import Store
from qqbot_studio.studio import StudioManager


def _manager(store: Store) -> DeployManager:
    return DeployManager(store=store, docker=mock.MagicMock())


def _cmd_config_path(store: Store) -> Path:
    return Path(store.data_dir) / "data" / "astrbot" / "cmd_config.json"


def _write_config(store: Store, payload: dict) -> Path:
    path = _cmd_config_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _long_term_payload(**overrides) -> dict:
    payload = {
        "default_mode": "conversation",
        "private": {"mode": "conversation", "scope": "user", "extract": True, "inject": True},
        "groups": [
            {
                "group_id": "838796241",
                "mode": "long_term",
                "scope": "group_user",
                "extract": True,
                "inject": True,
                "max_items": 8,
                "max_chars": 1500,
            }
        ],
    }
    payload.update(overrides)
    return payload


class MemoryPolicyBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.manager = _manager(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -- default fallback / unsupported state --------------------------
    def test_undeployed_instance_is_explicitly_unsupported(self) -> None:
        state = self.manager.get_memory_policy()
        self.assertTrue(state["ok"])
        self.assertFalse(state["available"])
        self.assertFalse(state["supported"])
        self.assertEqual(state["groups"], [])
        self.assertIn("不支持", state["message"])
        self.assertIn("cmd_config.json", state["message"])
        # Nothing is fabricated and nothing is written for an instance that
        # has no AstrBot config yet.
        with self.assertRaises(MemoryPolicyUnsupportedError):
            self.manager.set_memory_policy(_long_term_payload())
        self.assertFalse(_cmd_config_path(self.store).exists())

    def test_missing_policy_section_falls_back_to_conversation(self) -> None:
        _write_config(self.store, {"platform_settings": {"unique_session": False}})
        state = self.manager.get_memory_policy()
        self.assertTrue(state["available"])
        self.assertTrue(state["supported"])
        self.assertEqual(state["default_mode"], "conversation")
        self.assertEqual(state["private"]["mode"], "conversation")
        self.assertEqual(state["private"]["scope"], "user")
        self.assertEqual(state["groups"], [])
        self.assertEqual(state["warnings"], [])
        self.assertEqual(
            [option["value"] for option in state["mode_options"]],
            ["conversation", "long_term"],
        )
        self.assertEqual(
            [option["value"] for option in state["scope_options"]],
            ["group", "user", "group_user"],
        )

    def test_ui_warning_covers_selected_groups_only(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        state = self.manager.get_memory_policy()
        self.assertIn("只对下面列出的群生效", state["warning"])
        self.assertIn("conversation", state["warning"])
        self.assertIn("未列出的群", state["message"])

    def test_disabled_master_switch_is_reported_not_faked(self) -> None:
        _write_config(self.store, {"memory": {"enable": False}})
        state = self.manager.get_memory_policy()
        self.assertIs(state["memory_enabled"], False)
        self.assertIn("memory.enable", state["message"])
        saved = self.manager.set_memory_policy(_long_term_payload())
        self.assertIn("memory.enable", saved["message"])

    # -- save / load ---------------------------------------------------
    def test_save_and_load_roundtrip_preserves_other_settings(self) -> None:
        original = {
            "config_version": 5,
            "timezone": "Asia/Shanghai",
            "platform_settings": {"unique_session": False, "rate_limit": {"a": 1}},
            "memory": {
                "enable": True,
                "max_items": 6,
                "policy": {"default_mode": "conversation"},
            },
            "provider": [{"id": "deepseek", "key": ["sk-should-stay-out-of-ui"]}],
        }
        path = _write_config(self.store, original)

        saved = self.manager.set_memory_policy(_long_term_payload())
        self.assertTrue(saved["saved"])
        self.assertTrue(saved["changed"])
        self.assertTrue(saved["available"])
        self.assertEqual(saved["groups"][0]["group_id"], "838796241")
        self.assertEqual(saved["groups"][0]["scope"], "group_user")
        self.assertEqual(saved["groups"][0]["max_items"], 8)
        self.assertEqual(saved["groups"][0]["max_chars"], 1500)
        self.assertIn("重启", saved["message"])
        self.assertIs(saved["restart_required"], True)
        self.assertIs(saved["hot_reload"], False)

        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written["memory"]["policy"]["default_mode"], "conversation")
        self.assertEqual(
            written["memory"]["policy"]["groups"]["838796241"],
            {
                "mode": "long_term",
                "scope": "group_user",
                "extract": True,
                "inject": True,
                "max_items": 8,
                "max_chars": 1500,
            },
        )
        # Every unrelated AstrBot setting survives the write untouched.
        for key in ("config_version", "timezone", "platform_settings", "provider"):
            self.assertEqual(written[key], original[key])
        self.assertEqual(written["memory"]["enable"], True)
        self.assertEqual(written["memory"]["max_items"], 6)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        reloaded = self.manager.get_memory_policy()
        self.assertEqual(reloaded["groups"][0]["mode"], "long_term")
        self.assertEqual(reloaded["groups"][0]["scope"], "group_user")
        self.assertTrue(saved["message"])

    def test_save_response_contains_no_secret_values(self) -> None:
        _write_config(
            self.store,
            {"memory": {"enable": True}, "provider": {"key": ["sk-very-secret"]}},
        )
        state = self.manager.set_memory_policy(_long_term_payload())
        blob = json.dumps(state, ensure_ascii=False)
        self.assertNotIn("sk-very-secret", blob)

    def test_group_toggle_add_and_remove_rows(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        two_groups = _long_term_payload()
        two_groups["groups"] = [
            {"group_id": "111111", "mode": "long_term", "scope": "group"},
            {"group_id": "222222", "mode": "long_term", "scope": "group"},
        ]
        saved = self.manager.set_memory_policy(two_groups)
        self.assertEqual([row["group_id"] for row in saved["groups"]], ["111111", "222222"])

        # Toggle one group back to conversation: it stays listed but stops
        # remembering, which is exactly what the UI promises.
        two_groups["groups"][1] = {"group_id": "222222", "mode": "conversation"}
        saved = self.manager.set_memory_policy(two_groups)
        self.assertEqual(saved["groups"][1]["mode"], "conversation")
        self.assertEqual(saved["groups"][1]["scope"], "group")

        # Deleting the row removes the override entirely.
        one_group = _long_term_payload()
        one_group["groups"] = [{"group_id": "111111", "mode": "long_term", "scope": "group"}]
        saved = self.manager.set_memory_policy(one_group)
        self.assertEqual([row["group_id"] for row in saved["groups"]], ["111111"])

    def test_platform_prefixed_group_key_is_accepted(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        payload = _long_term_payload()
        payload["groups"] = [{"group_id": "aiocqhttp:838796241", "mode": "long_term"}]
        saved = self.manager.set_memory_policy(payload)
        self.assertEqual(saved["groups"][0]["group_id"], "aiocqhttp:838796241")

    def test_private_policy_roundtrip(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        payload = _long_term_payload()
        payload["private"] = {
            "mode": "long_term",
            "scope": "user",
            "extract": True,
            "inject": False,
            "max_items": 3,
        }
        saved = self.manager.set_memory_policy(payload)
        self.assertEqual(saved["private"]["mode"], "long_term")
        self.assertEqual(saved["private"]["scope"], "user")
        self.assertIs(saved["private"]["inject"], False)
        self.assertEqual(saved["private"]["max_items"], 3)

    # -- stored-config tolerance ---------------------------------------
    def test_invalid_stored_policy_is_reported_and_not_rewritten(self) -> None:
        path = _write_config(
            self.store,
            {
                "memory": {
                    "enable": True,
                    "policy": {
                        "default_mode": "sometimes",
                        "groups": {
                            "333333": {"mode": "forever", "extract": "yes", "max_items": -1},
                        },
                    },
                }
            },
        )
        before = path.read_text(encoding="utf-8")
        state = self.manager.get_memory_policy()
        self.assertEqual(state["default_mode"], "conversation")
        self.assertEqual(state["groups"][0]["mode"], "conversation")
        self.assertTrue(state["warnings"])
        self.assertIn("default_mode", " ".join(state["warnings"]))
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_memory_database_content_is_never_exposed(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        db = Path(self.store.data_dir) / "data" / "astrbot" / "astrbot_memory.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"SQLite format 3\x00private-memory-sentinel")
        state = self.manager.get_memory_policy()
        self.assertNotIn("private-memory-sentinel", json.dumps(state, ensure_ascii=False))
        saved = self.manager.set_memory_policy(_long_term_payload())
        self.assertNotIn("private-memory-sentinel", json.dumps(saved, ensure_ascii=False))
        self.assertEqual(db.read_bytes(), b"SQLite format 3\x00private-memory-sentinel")

    # -- validation ----------------------------------------------------
    def test_validation_rejects_unsafe_payloads(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        cases = {
            "not-an-object": ["default_mode"],
            "bad default mode": {"default_mode": "always", "groups": []},
            "missing default mode": {"groups": []},
            "groups not a list": {"default_mode": "conversation", "groups": {"1": {}}},
            "row not an object": {"default_mode": "conversation", "groups": ["111111"]},
            "non numeric group": {
                "default_mode": "conversation",
                "groups": [{"group_id": "group-one", "mode": "long_term"}],
            },
            "path traversal group": {
                "default_mode": "conversation",
                "groups": [{"group_id": "../../etc/passwd", "mode": "long_term"}],
            },
            "url group": {
                "default_mode": "conversation",
                "groups": [{"group_id": "http://evil", "mode": "long_term"}],
            },
            "short group": {
                "default_mode": "conversation",
                "groups": [{"group_id": "12", "mode": "long_term"}],
            },
            "bool group": {
                "default_mode": "conversation",
                "groups": [{"group_id": True, "mode": "long_term"}],
            },
            "bad mode": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "on"}],
            },
            "bad scope": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "scope": "everyone"}],
            },
            "string extract": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "extract": "yes"}],
            },
            "string inject": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "inject": 1}],
            },
            "max_items too low": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "max_items": 0}],
            },
            "max_items too high": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "max_items": 51}],
            },
            "max_items bool": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "max_items": True}],
            },
            "max_items float": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "max_items": 2.5}],
            },
            "max_chars too low": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "max_chars": 79}],
            },
            "max_chars too high": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "max_chars": 8001}],
            },
            "duplicate group": {
                "default_mode": "conversation",
                "groups": [
                    {"group_id": "111111", "mode": "long_term"},
                    {"group_id": "111111", "mode": "conversation"},
                ],
            },
            "unknown row field": {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "exec": "/bin/sh"}],
            },
            "unknown payload field": {"default_mode": "conversation", "groups": [], "path": "/etc"},
            "private scope group": {
                "default_mode": "conversation",
                "groups": [],
                "private": {"mode": "long_term", "scope": "group"},
            },
            "private mode missing": {
                "default_mode": "conversation",
                "groups": [],
                "private": {"scope": "user"},
            },
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(DeploymentError):
                    self.manager.set_memory_policy(payload)
                # A rejected payload never touches the stored config.
                self.assertFalse(
                    json.loads(_cmd_config_path(self.store).read_text(encoding="utf-8"))["memory"].get("policy")
                )

    def test_validation_rejects_too_many_groups(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        payload = {
            "default_mode": "conversation",
            "groups": [
                {"group_id": str(1_000_000 + index), "mode": "long_term"}
                for index in range(MEMORY_POLICY_MAX_GROUPS + 1)
            ],
        }
        with self.assertRaises(DeploymentError):
            self.manager.set_memory_policy(payload)

    def test_optional_budgets_are_omitted_when_blank(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        payload = _long_term_payload()
        payload["groups"] = [
            {
                "group_id": "111111",
                "mode": "long_term",
                "scope": "group",
                "max_items": None,
                "max_chars": None,
            }
        ]
        saved = self.manager.set_memory_policy(payload)
        self.assertIsNone(saved["groups"][0]["max_items"])
        written = json.loads(_cmd_config_path(self.store).read_text(encoding="utf-8"))
        self.assertNotIn("max_items", written["memory"]["policy"]["groups"]["111111"])
        self.assertEqual(
            set(written["memory"]["policy"]["groups"]["111111"]),
            {"mode", "scope", "extract", "inject"},
        )

    # -- isolation -----------------------------------------------------
    def test_no_cross_instance_contamination(self) -> None:
        other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(other_tmp.cleanup)
        other_store = Store(other_tmp.name)
        other_manager = _manager(other_store)
        _write_config(self.store, {"memory": {"enable": True}})
        _write_config(other_store, {"memory": {"enable": True}})

        self.manager.set_memory_policy(_long_term_payload())
        other_state = other_manager.get_memory_policy()
        self.assertEqual(other_state["groups"], [])
        self.assertEqual(other_state["default_mode"], "conversation")
        self.assertNotEqual(
            other_state["config_path"], self.manager.get_memory_policy()["config_path"]
        )

        other_manager.set_memory_policy({"default_mode": "long_term", "groups": []})
        self.assertEqual(
            self.manager.get_memory_policy()["default_mode"], "conversation"
        )
        self.assertEqual(other_manager.get_memory_policy()["default_mode"], "long_term")


class MemoryPolicyHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = StudioManager(root=self.tmp.name)
        self.meta = self.studio.create_instance("记忆策略机器人")
        self.instance_id = str(self.meta["id"])
        self.config_path = (
            Path(self.tmp.name) / "instances" / self.instance_id / "data" / "astrbot" / "cmd_config.json"
        )
        self.server = create_server(
            self.studio, host="127.0.0.1", port=0, token="policy-token-123"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def _request(self, path: str, *, payload: dict | None = None, token: str = "policy-token-123"):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data, method="POST" if payload is not None else "GET"
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-QB-Token", token)
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, {"raw": body}

    def _write_config(self, payload: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_nested_get_and_flat_get_agree(self) -> None:
        self._write_config({"memory": {"enable": True}})
        status, nested = self._request(f"/api/instances/{self.instance_id}/memory-policy")
        self.assertEqual(status, 200)
        self.assertTrue(nested["available"])
        self.assertEqual(nested["default_mode"], "conversation")
        status, flat = self._request(f"/api/memory-policy?instance={self.instance_id}")
        self.assertEqual(status, 200)
        self.assertEqual(flat["default_mode"], nested["default_mode"])
        self.assertEqual(flat["config_path"], nested["config_path"])

    def test_post_saves_policy_and_is_readable_afterwards(self) -> None:
        self._write_config({"memory": {"enable": True}, "platform_settings": {"unique_session": False}})
        status, saved = self._request(
            f"/api/instances/{self.instance_id}/memory-policy", payload=_long_term_payload()
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["saved"])
        written = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(written["platform_settings"], {"unique_session": False})
        self.assertEqual(
            written["memory"]["policy"]["groups"]["838796241"]["mode"], "long_term"
        )
        _, reloaded = self._request(f"/api/instances/{self.instance_id}/memory-policy")
        self.assertEqual(reloaded["groups"][0]["group_id"], "838796241")

    def test_invalid_payload_returns_400_and_writes_nothing(self) -> None:
        self._write_config({"memory": {"enable": True}})
        status, body = self._request(
            f"/api/instances/{self.instance_id}/memory-policy",
            payload={"default_mode": "conversation", "groups": [{"group_id": "../x", "mode": "long_term"}]},
        )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        written = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("policy", written["memory"])

    def test_undeployed_instance_post_returns_409_unsupported(self) -> None:
        status, body = self._request(
            f"/api/instances/{self.instance_id}/memory-policy", payload=_long_term_payload()
        )
        self.assertEqual(status, 409)
        self.assertFalse(body["supported"])
        self.assertIn("无法保存记忆策略", body["message"])
        self.assertFalse(self.config_path.exists())

    def test_undeployed_instance_get_reports_unsupported_state(self) -> None:
        status, body = self._request(f"/api/instances/{self.instance_id}/memory-policy")
        self.assertEqual(status, 200)
        self.assertFalse(body["available"])
        self.assertFalse(body["supported"])
        self.assertEqual(body["groups"], [])

    def test_missing_token_is_still_rejected(self) -> None:
        status, body = self._request(
            f"/api/instances/{self.instance_id}/memory-policy", token=""
        )
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])

    def test_unknown_instance_is_rejected(self) -> None:
        status, body = self._request("/api/instances/does-not-exist/memory-policy")
        self.assertGreaterEqual(status, 400)
        self.assertFalse(body.get("ok", False))


class MemoryPolicyUnsupportedBackendTests(unittest.TestCase):
    """A backend without the policy API must report unsupported, not fake it."""

    class _BareManager:
        pass

    class _BareStudio:
        def __init__(self) -> None:
            self.manager = MemoryPolicyUnsupportedBackendTests._BareManager()

        def get_instance_meta(self, instance_id):
            return {"id": instance_id}

        def get_manager(self, instance_id):
            return self.manager

    def setUp(self) -> None:
        self.server = create_server(
            self._BareStudio(), host="127.0.0.1", port=0, token="bare-token"
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_missing_endpoint_is_not_faked(self) -> None:
        request = urllib.request.Request(self.base + "/api/instances/x/memory-policy")
        request.add_header("X-QB-Token", "bare-token")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(request, timeout=5)
        self.assertEqual(caught.exception.code, 501)
        body = json.loads(caught.exception.read().decode("utf-8"))
        self.assertFalse(body["available"])
        self.assertFalse(body["supported"])
        self.assertIn("未提供记忆策略接口", body["error"])


if __name__ == "__main__":
    unittest.main()
