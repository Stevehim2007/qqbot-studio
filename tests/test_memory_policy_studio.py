"""Studio API tests for the per-group long-term memory policy editor."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import qqbot_studio.deployer as deployer_module
from qqbot_studio.dockerctl import CommandResult, DockerController
from qqbot_studio.deployer import (
    MEMORY_POLICY_MAX_GROUPS,
    DeploymentError,
    DeployManager,
    MemoryPolicyUnsupportedError,
    MemoryPolicyWriteUnavailableError,
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

    # -- t26 P2: invalid rows must be reported, never silently normalized ----
    def _policy_state(self, policy: dict) -> tuple[dict, str]:
        path = _write_config(self.store, {"memory": {"enable": True, "policy": policy}})
        return self.manager.get_memory_policy(), path.read_text(encoding="utf-8")

    def test_private_scope_group_is_reported_not_rewritten(self) -> None:
        state, raw_before = self._policy_state(
            {
                "default_mode": "conversation",
                "private": {"mode": "long_term", "scope": "group"},
                "groups": {},
            }
        )
        private = state["private"]
        # The stored values are preserved verbatim: no silent scope rewrite.
        self.assertEqual(private["mode"], "long_term")
        self.assertEqual(private["scope"], "group")
        self.assertTrue(private["invalid"])
        joined = " ".join(state["warnings"])
        self.assertIn("memory.policy.private.scope", joined)
        self.assertIn("私聊只允许 scope=user", joined)
        self.assertIn("私聊只允许 scope=user", private["errors"][0])
        self.assertIn("降级为 conversation", joined)
        # AstrBot degrades the entry, so Studio must not claim long_term is live.
        self.assertEqual(private["effective_mode"], "conversation")
        self.assertEqual(private["effective_scope"], "user")
        self.assertEqual(state["invalid_rows"], 1)
        self.assertIn("不是 long_term 生效", state["message"])
        # Read-only: GET never rewrites the stored config.
        self.assertEqual(
            (_cmd_config_path(self.store)).read_text(encoding="utf-8"), raw_before
        )

    def test_private_long_term_without_scope_is_valid(self) -> None:
        state, _raw = self._policy_state({"private": {"mode": "long_term"}})
        self.assertFalse(state["private"]["invalid"])
        self.assertEqual(state["private"]["effective_mode"], "long_term")
        self.assertEqual(state["private"]["scope"], "user")
        self.assertEqual(state["invalid_rows"], 0)

    def test_unconfigured_private_follows_default_mode(self) -> None:
        state, _raw = self._policy_state({"default_mode": "long_term", "private": {}})
        self.assertFalse(state["private"]["configured"])
        self.assertFalse(state["private"]["invalid"])
        # AstrBot ignores a falsy private entry -> it follows default_mode.
        self.assertEqual(state["private"]["effective_mode"], "long_term")

    def test_max_items_out_of_range_is_reported(self) -> None:
        state, _raw = self._policy_state(
            {
                "groups": {
                    "111111": {"mode": "long_term", "max_items": 999},
                    "222222": {"mode": "long_term", "max_chars": 10},
                }
            }
        )
        rows = {row["group_id"]: row for row in state["groups"]}
        self.assertTrue(rows["111111"]["invalid"])
        self.assertTrue(rows["222222"]["invalid"])
        self.assertEqual(rows["111111"]["effective_mode"], "conversation")
        self.assertEqual(rows["222222"]["effective_mode"], "conversation")
        self.assertEqual(state["invalid_rows"], 2)
        joined = " ".join(state["warnings"])
        self.assertIn("memory.policy.groups.111111.max_items：必须在 1..50 之间，收到 999", joined)
        self.assertIn("memory.policy.groups.222222.max_chars：必须在 80..8000 之间，收到 10", joined)
        self.assertIn("降级为 conversation", joined)

    def test_unknown_row_keys_are_warned(self) -> None:
        state, _raw = self._policy_state(
            {"groups": {"111111": {"mode": "long_term", "max_item": 5, "scop": "user"}}}
        )
        row = state["groups"][0]
        self.assertEqual(row["unknown_fields"], ["max_item", "scop"])
        self.assertFalse(row["invalid"])
        self.assertEqual(row["effective_mode"], "long_term")
        joined = " ".join(state["warnings"])
        self.assertIn("未知字段 max_item, scop", joined)
        self.assertIn("AstrBot 忽略", joined)

    def test_duplicate_group_ids_are_reported_as_unknown(self) -> None:
        state, _raw = self._policy_state(
            {
                "groups": {
                    "333333": {"mode": "long_term"},
                    " 333333": {"mode": "conversation"},
                }
            }
        )
        self.assertEqual(len(state["groups"]), 2)
        for row in state["groups"]:
            self.assertTrue(row["invalid"])
            self.assertIsNone(row["effective_mode"])
        self.assertEqual(state["invalid_rows"], 2)
        joined = " ".join(state["warnings"])
        self.assertIn("群号 333333 重复", joined)
        self.assertIn("无法确定哪一个生效", joined)

    def test_invalid_scope_and_type_are_reported(self) -> None:
        state, _raw = self._policy_state(
            {
                "groups": {
                    "111111": {"mode": "long_term", "scope": "everyone"},
                    "222222": {"mode": "long_term", "extract": "yes"},
                    "333333": {"mode": "never"},
                    "444444": "not-an-object",
                }
            }
        )
        rows = {row["group_id"]: row for row in state["groups"]}
        for group_id in ("111111", "222222", "333333", "444444"):
            self.assertTrue(rows[group_id]["invalid"], group_id)
            self.assertEqual(rows[group_id]["effective_mode"], "conversation", group_id)
            self.assertTrue(rows[group_id]["errors"], group_id)
        joined = " ".join(state["warnings"])
        for needle in (
            "只允许 group / user / group_user",
            "必须是 true/false",
            "只允许 conversation / long_term",
            "条目不是对象",
        ):
            self.assertIn(needle, joined)

    def test_valid_rows_still_report_effective_long_term(self) -> None:
        state, _raw = self._policy_state(
            {"groups": {"111111": {"mode": "long_term", "scope": "group_user", "max_items": 8}}}
        )
        row = state["groups"][0]
        self.assertFalse(row["invalid"])
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["effective_mode"], "long_term")
        self.assertEqual(row["effective_scope"], "group_user")
        self.assertEqual(state["invalid_rows"], 0)
        self.assertNotIn("非法策略", state["message"])

    def test_warnings_survive_the_http_response(self) -> None:
        policy = {
            "private": {"mode": "long_term", "scope": "group"},
            "groups": {"111111": {"mode": "long_term", "max_items": 999}},
        }
        _write_config(self.store, {"memory": {"enable": True, "policy": policy}})
        status, body = self._http_get()
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["invalid_rows"], 2)
        self.assertTrue(body["warnings"])
        self.assertIn("私聊只允许 scope=user", " ".join(body["warnings"]))
        self.assertEqual(body["private"]["effective_mode"], "conversation")

    def test_saving_the_same_invalid_values_is_rejected(self) -> None:
        _write_config(self.store, {"memory": {"enable": True}})
        for payload in (
            {"default_mode": "conversation", "private": {"mode": "long_term", "scope": "group"}},
            {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "max_items": 999}],
            },
            {
                "default_mode": "conversation",
                "groups": [{"group_id": "111111", "mode": "long_term", "unknown": 1}],
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(DeploymentError):
                    self.manager.set_memory_policy(payload)

    def _http_get(self) -> tuple[int, dict]:
        """GET the policy endpoint over a real local HTTP server."""

        studio = StudioManager(root=self.tmp.name)
        meta = studio.create_instance("warn-bot")
        # Move the config we just wrote into the instance data dir.
        target = (
            Path(self.tmp.name) / "instances" / str(meta["id"]) / "data" / "astrbot" / "cmd_config.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_cmd_config_path(self.store).read_text(encoding="utf-8"), encoding="utf-8")
        server = create_server(studio, host="127.0.0.1", port=0, token="warn-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request(
                f"http://{host}:{port}/api/instances/{meta['id']}/memory-policy"
            )
            request.add_header("X-QB-Token", "warn-token")
            with opener.open(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

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

class _FakeDocker:
    """Docker client double: only the container IO surface used by Studio."""

    ASTRBOT_CONTAINER = "qqbot-astrbot"

    def __init__(
        self,
        *,
        running: bool = True,
        files: dict[str, str] | None = None,
        write_error: str = "",
    ) -> None:
        self.running = running
        self.files = dict(files or {})
        self.write_error = write_error
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def is_container_running(self, name: str) -> bool:
        return self.running and name == self.ASTRBOT_CONTAINER

    def exec_in_container(self, container: str, argv, *, timeout: int = 60) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.calls.append((container, command))
        if command[:1] == ("cat",):
            path = command[1]
            if path in self.files:
                return CommandResult(command, 0, self.files[path], "")
            return CommandResult(command, 1, "", f"cat: {path}: No such file or directory")
        if command[:1] == ("python3",):
            if self.write_error:
                return CommandResult(command, 13, "", self.write_error)
            payload = base64.b64decode(command[4]).decode("utf-8")
            json.loads(payload)
            self.files[command[3]] = payload
            return CommandResult(command, 0, "ok\n", "")
        return CommandResult(command, 0, "", "")


class MemoryConfigPermissionTests(unittest.TestCase):
    """t30: cmd_config.json owned by the container must stay editable."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.tmp_dirs: list[tempfile.TemporaryDirectory] = [self.tmp]

    def tearDown(self) -> None:
        for item in self.tmp_dirs:
            item.cleanup()

    # -- helpers -------------------------------------------------------
    def _manager(self, docker) -> DeployManager:
        return DeployManager(store=self.store, docker=docker)

    def _write_host_config(self, payload: dict):
        return _write_config(self.store, payload)

    def _deny(self, path: Path, *, read: bool = True, write: bool = True):
        """Make host access raise EACCES for ``path`` only."""

        real_read = Path.read_text

        def fake_read(inner, *args, **kwargs):
            if Path(inner) == path:
                raise PermissionError(13, "Permission denied", str(inner))
            return real_read(inner, *args, **kwargs)

        class _Patches:
            def __enter__(self):
                self._patches = []
                if read:
                    self._patches.append(mock.patch.object(Path, "read_text", fake_read))
                if write:
                    real_write = DeployManager._write_private_json

                    def fake_write(inner_path, value):
                        if Path(inner_path) == path:
                            raise PermissionError(13, "Permission denied", str(inner_path))
                        return real_write(inner_path, value)

                    self._patches.append(
                        mock.patch.object(
                            DeployManager, "_write_private_json", staticmethod(fake_write)
                        )
                    )
                for item in self._patches:
                    item.start()
                return self

            def __exit__(self, *exc_info):
                for item in reversed(self._patches):
                    item.stop()
                return False

        return _Patches()

    def _container_path(self) -> str:
        return DeployManager.ASTRBOT_CONFIG_CONTAINER_PATH

    # -- read fallback -------------------------------------------------
    def test_permission_denied_read_uses_container(self) -> None:
        path = self._write_host_config({"memory": {"enable": True}, "platform_settings": {}})
        docker = _FakeDocker(files={
            self._container_path(): json.dumps(
                {"memory": {"enable": True, "policy": {"groups": {"111111": {"mode": "long_term"}}}},
                 "platform_settings": {"unique_session": False}},
                ensure_ascii=False,
            )
        })
        manager = self._manager(docker)
        with self._deny(path):
            state = manager.get_memory_policy()
        self.assertTrue(state["available"])
        self.assertTrue(state["supported"])
        self.assertFalse(state["permission_denied"])
        self.assertEqual(state["read_channel"], "container")
        self.assertEqual(state["write_channel"], "container")
        self.assertTrue(state["writable"])
        self.assertEqual(state["groups"][0]["group_id"], "111111")
        self.assertEqual(state["groups"][0]["effective_mode"], "long_term")
        self.assertEqual(docker.calls[0][1][:2], ("cat", self._container_path()))

    def test_container_unavailable_gives_actionable_status(self) -> None:
        path = self._write_host_config({"memory": {"enable": True}})
        docker = _FakeDocker(running=False)
        manager = self._manager(docker)
        with self._deny(path):
            state = manager.get_memory_policy()
        # Not "unsupported": the panel must stay usable and explain the fix.
        self.assertTrue(state["supported"])
        self.assertFalse(state["available"])
        self.assertTrue(state["permission_denied"])
        self.assertFalse(state["writable"])
        self.assertFalse(state["container_running"])
        joined = " ".join(state["actions"])
        self.assertIn("启动", joined)
        self.assertIn("chmod", joined)
        self.assertNotIn("不支持", state["message"])
        self.assertEqual(state["groups"], [])

    def test_read_only_host_without_container_reports_not_writable(self) -> None:
        path = self._write_host_config({"memory": {"enable": True}})
        manager = self._manager(_FakeDocker(running=False))
        # Simulate a container-owned 0600 file: readable here, not writable.
        real_access = os.access

        def fake_access(target, mode, **kwargs):
            if Path(target) == path and mode == os.W_OK:
                return False
            return real_access(target, mode, **kwargs)

        with mock.patch.object(deployer_module.os, "access", fake_access), self._deny(
            path, read=False
        ):
            state = manager.get_memory_policy()
            self.assertTrue(state["available"])
            self.assertTrue(state["readable"])
            self.assertFalse(state["writable"])
            self.assertEqual(state["read_channel"], "host")
            self.assertTrue(state["actions"])
            with self.assertRaises(MemoryPolicyWriteUnavailableError) as caught:
                manager.set_memory_policy(_long_term_payload())
        self.assertIn("不会伪造已保存的值", str(caught.exception))
        self.assertTrue(caught.exception.actions)

    # -- write fallback ------------------------------------------------
    def test_permission_denied_write_goes_through_container_atomically(self) -> None:
        path = self._write_host_config(
            {"memory": {"enable": True, "max_items": 6}, "platform_settings": {"unique_session": False}}
        )
        docker = _FakeDocker(files={
            self._container_path(): json.dumps(
                {"memory": {"enable": True, "max_items": 6},
                 "platform_settings": {"unique_session": False}}
            )
        })
        manager = self._manager(docker)
        with self._deny(path):
            saved = manager.set_memory_policy(_long_term_payload())
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["write_channel"], "container")
        self.assertEqual(saved["groups"][0]["group_id"], "838796241")
        # The container write is an argv (no shell) with an atomic script.
        write_call = [call for call in docker.calls if call[1][:1] == ("python3",)][0]
        argv = write_call[1]
        self.assertEqual(argv[0], "python3")
        self.assertEqual(argv[1], "-c")
        self.assertIn("os.replace(tmp,path)", argv[2])
        self.assertIn("os.chmod(tmp,0o600)", argv[2])
        self.assertEqual(argv[3], self._container_path())
        self.assertNotIn("sh", argv)
        self.assertNotIn("-c shell", argv)
        written = json.loads(docker.files[self._container_path()])
        self.assertEqual(written["memory"]["policy"]["groups"]["838796241"]["mode"], "long_term")
        # Unrelated settings are preserved on the container side too.
        self.assertEqual(written["platform_settings"], {"unique_session": False})
        self.assertEqual(written["memory"]["enable"], True)
        self.assertEqual(written["memory"]["max_items"], 6)

    def test_container_write_failure_never_fakes_saved_state(self) -> None:
        path = self._write_host_config({"memory": {"enable": True}})
        docker = _FakeDocker(
            files={self._container_path(): json.dumps({"memory": {"enable": True}})},
            write_error="Permission denied",
        )
        manager = self._manager(docker)
        with self._deny(path):
            with self.assertRaises(MemoryPolicyWriteUnavailableError) as caught:
                manager.set_memory_policy(_long_term_payload())
        message = str(caught.exception)
        self.assertIn("策略未保存", message)
        self.assertIn("不会伪造已保存的值", message)
        self.assertNotIn("saved", message.lower())

    def test_oversized_config_is_refused_without_partial_write(self) -> None:
        path = self._write_host_config({"memory": {"enable": True}})
        docker = _FakeDocker(files={self._container_path(): json.dumps({"memory": {"enable": True}})})
        manager = self._manager(docker)
        with self._deny(path):
            original = manager._CONTAINER_CONFIG_MAX_PAYLOAD
            manager._CONTAINER_CONFIG_MAX_PAYLOAD = 10
            try:
                with self.assertRaises(MemoryPolicyWriteUnavailableError) as caught:
                    manager.set_memory_policy(_long_term_payload())
            finally:
                manager._CONTAINER_CONFIG_MAX_PAYLOAD = original
        self.assertIn("过大", str(caught.exception))
        self.assertNotIn(self._container_path(), [call[1][1] for call in docker.calls if call[1][:1] == ("python3",)] and [])
        self.assertEqual(docker.files[self._container_path()], json.dumps({"memory": {"enable": True}}))

    def test_host_config_with_utf8_bom_is_accepted(self) -> None:
        path = _cmd_config_path(self.store)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"memory": {"enable": True}}).encode("utf-8"))
        state = self._manager(_FakeDocker(running=False)).get_memory_policy()
        self.assertTrue(state["available"])
        self.assertTrue(state["supported"])
        self.assertEqual(state["read_channel"], "host")

    def test_container_config_with_utf8_bom_is_accepted(self) -> None:
        path = self._write_host_config({"memory": {"enable": True}})
        container_payload = json.dumps({"memory": {"enable": True}}, ensure_ascii=False)
        docker = _FakeDocker(files={self._container_path(): "\ufeff" + container_payload})
        with self._deny(path):
            state = self._manager(docker).get_memory_policy()
        self.assertTrue(state["available"])
        self.assertTrue(state["supported"])
        self.assertEqual(state["read_channel"], "container")

    # -- safety ---------------------------------------------------------
    def test_no_secret_is_echoed_from_container_errors(self) -> None:
        path = self._write_host_config({"memory": {"enable": True}})
        docker = _FakeDocker(
            files={self._container_path(): json.dumps({"memory": {"enable": True}})},
            write_error="failed for sk-very-secret-token",
        )
        manager = self._manager(docker)
        with self._deny(path):
            state = manager.get_memory_policy()
            self.assertNotIn("sk-very-secret-token", json.dumps(state, ensure_ascii=False))
            with self.assertRaises(MemoryPolicyWriteUnavailableError) as caught:
                manager.set_memory_policy(_long_term_payload())
        self.assertNotIn("sk-very-secret-token", str(caught.exception))

    def test_poisoned_container_name_is_refused(self) -> None:
        class _RecordingDocker(DockerController):
            pass

        calls: list[list[str]] = []

        def runner(command, cwd, timeout, extra_env):
            calls.append(list(command))
            raise AssertionError("runner must not be called for an invalid container name")

        docker = DockerController(self.store.data_dir, runner=runner)
        result = docker.exec_in_container("qqbot-astrbot; rm -rf /", ["cat", "/AstrBot/data/cmd_config.json"])
        self.assertEqual(result.returncode, 125)
        self.assertIn("非法容器名", result.stderr)
        self.assertEqual(calls, [])
        # And a poisoned instance config cannot reach docker at all.
        poisoned = _FakeDocker(files={})
        poisoned.ASTRBOT_CONTAINER = "--privileged"
        manager = self._manager(poisoned)
        self.assertFalse(manager._astrbot_container_available()[0])

    def test_container_argv_avoids_shell_interpretation(self) -> None:
        calls: list[list[str]] = []

        def runner(command, cwd, timeout, extra_env):
            calls.append(list(command))
            return CommandResult(tuple(command), 0, "{}", "")

        docker = DockerController(self.store.data_dir, runner=runner)
        docker.exec_in_container("qqbot-astrbot", ["cat", "/AstrBot/data/cmd_config.json"])
        self.assertEqual(calls[0][:4], ["docker", "exec", "qqbot-astrbot", "cat"])
        self.assertIsInstance(calls[0], list)
        self.assertNotIn("-c", calls[0])

    # -- HTTP surface ---------------------------------------------------
    def test_http_permission_denied_is_503_not_unsupported(self) -> None:
        studio = StudioManager(root=self.tmp.name)
        meta = studio.create_instance("perm-bot")
        instance_id = str(meta["id"])
        config = (
            Path(self.tmp.name) / "instances" / instance_id / "data" / "astrbot" / "cmd_config.json"
        )
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"memory": {"enable": True}}), encoding="utf-8")

        class _NoDockerStudio(StudioManager):
            pass

        manager = studio.get_manager(instance_id)
        manager.docker = _FakeDocker(running=False)
        real_read = Path.read_text

        def fake_read(inner, *args, **kwargs):
            if Path(inner) == config:
                raise PermissionError(13, "Permission denied", str(inner))
            return real_read(inner, *args, **kwargs)

        server = create_server(studio, host="127.0.0.1", port=0, token="perm-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://{host}:{port}"
            with mock.patch.object(Path, "read_text", fake_read):
                request = urllib.request.Request(
                    f"{base}/api/instances/{instance_id}/memory-policy"
                )
                request.add_header("X-QB-Token", "perm-token")
                with opener.open(request, timeout=5) as response:
                    state = json.loads(response.read().decode("utf-8"))
                self.assertTrue(state["supported"])
                self.assertFalse(state["available"])
                self.assertTrue(state["actions"])
                self.assertNotIn("不支持", state["message"])

                body = json.dumps(_long_term_payload()).encode("utf-8")
                post = urllib.request.Request(
                    f"{base}/api/instances/{instance_id}/memory-policy",
                    data=body,
                    method="POST",
                )
                post.add_header("Content-Type", "application/json")
                post.add_header("X-QB-Token", "perm-token")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(post, timeout=5)
                self.assertEqual(caught.exception.code, 503)
                payload = json.loads(caught.exception.read().decode("utf-8"))
                self.assertFalse(payload["saved"])
                self.assertTrue(payload["supported"])
                self.assertTrue(payload["actions"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
