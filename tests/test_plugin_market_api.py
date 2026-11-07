"""HTTP contract tests for plugin-market/group/operation routes."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from qqbot_studio.plugin_market import PluginMarket, sha256_file
from qqbot_studio.server import create_server

from test_plugin_market import make_manifest, make_zip


class FakeMarketManager:
    def __init__(self, root: Path) -> None:
        self.market = PluginMarket(root, plugin_root=root / "data" / "astrbot" / "plugins")

    def get_plugin_market(self):
        return self.market.market_status()

    def refresh_plugin_market(self, source=None):
        return self.market.refresh_market(source)

    def get_plugin_groups(self):
        return self.market.list_plugin_groups()

    def preview_plugin_groups(self, payload):
        return self.market.preview_plugin_groups(payload)

    def apply_plugin_groups(self, payload):
        return self.market.apply_plugin_groups(payload)

    def execute_plugin_group_action(self, payload):
        return self.market.execute_plugin_group_action(payload)

    def preview_plugin_operation(self, payload):
        return self.market.preview_operation(payload)

    def execute_plugin_operation(self, payload):
        return self.market.execute_operation(payload)

    def get_plugin_operation(self, operation_id):
        return self.market.get_operation(operation_id)


class FakeStudio:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manager = FakeMarketManager(root)

    def get_instance_meta(self, instance_id: str):
        return {"id": instance_id, "name": instance_id}

    def get_manager(self, instance_id: str):
        return self.manager


class PluginMarketApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text(
            '<!doctype html><script>window.QB_TOKEN = "{{TOKEN}}";</script>',
            encoding="utf-8",
        )
        self.token = "plugin-api-token"
        self.studio = FakeStudio(self.root / "instance")
        self.server = create_server(
            self.studio,
            static_dir=static,
            host="127.0.0.1",
            port=0,
            token=self.token,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, payload=None, *, token=True):
        headers = {"X-QB-Token": self.token} if token else {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            return exc.code, json.loads(body)

    def _catalog(self):
        market_dir = self.root / "instance" / "studio-plugins" / "market"
        market_dir.mkdir(parents=True, exist_ok=True)
        archive = make_zip(market_dir / "demo_plugin.zip", "demo_plugin", {"main.py": "VALUE=1\n"})
        manifest = make_manifest("demo_plugin", "1.0.0", archive.name, sha256_file(archive))
        (market_dir / "catalog.json").write_text(
            json.dumps({"schema_version": 1, "plugins": [manifest]}),
            encoding="utf-8",
        )
        return manifest

    def test_market_refresh_preview_install_get_operation_and_delete(self):
        self._catalog()
        status, market = self.request("GET", "/api/instances/bot-1/plugin-market")
        self.assertEqual(status, 200, market)
        self.assertEqual(market["entries"], [])
        status, refreshed = self.request(
            "POST", "/api/instances/bot-1/plugin-market/refresh", {}
        )
        self.assertEqual(status, 200, refreshed)
        self.assertEqual(refreshed["count"], 1)
        self.assertEqual(refreshed["entries"][0]["status"], "available")

        status, preview = self.request(
            "POST",
            "/api/instances/bot-1/plugin-operations/preview",
            {"operation": "install", "plugin_id": "demo_plugin"},
        )
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview["requires_confirmation"])
        self.assertIn("preview_token", preview)

        status, result = self.request(
            "POST",
            "/api/instances/bot-1/plugin-operations",
            {
                "operation": "install",
                "plugin_id": "demo_plugin",
                "preview_token": preview["preview_token"],
                "confirm": True,
                "idempotency_key": "api-install-1",
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"], result)
        operation_id = result["operation"]["id"]
        self.assertEqual(result["operation"]["status"], "completed")
        status, fetched = self.request(
            "GET", f"/api/instances/bot-1/plugin-operations/{operation_id}"
        )
        self.assertEqual(status, 200, fetched)
        self.assertEqual(fetched["operation"]["status"], "completed")

        # Idempotent replay over HTTP must not create another operation.
        status, replay = self.request(
            "POST",
            "/api/instances/bot-1/plugin-operations",
            {
                "operation": "install",
                "plugin_id": "demo_plugin",
                "preview_token": preview["preview_token"],
                "confirm": True,
                "idempotency_key": "api-install-1",
            },
        )
        self.assertEqual(status, 200, replay)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["operation"]["id"], operation_id)

        delete_preview_status, delete_preview = self.request(
            "POST",
            "/api/instances/bot-1/plugin-operations/preview",
            {"operation": "delete", "plugin_id": "demo_plugin"},
        )
        self.assertEqual(delete_preview_status, 200, delete_preview)
        status, deleted = self.request(
            "POST",
            "/api/instances/bot-1/plugin-operations",
            {
                "operation": "delete",
                "plugin_id": "demo_plugin",
                "preview_token": delete_preview["preview_token"],
                "confirm": True,
            },
        )
        self.assertEqual(status, 200, deleted)
        self.assertTrue((self.root / "instance" / "studio-plugins" / "registry.json").is_file())

    def test_group_api_preview_apply_and_confirmation(self):
        status, groups = self.request("GET", "/api/instances/bot-1/plugin-groups")
        self.assertEqual(status, 200, groups)
        self.assertEqual(groups["groups"], [])
        actions = [{"op": "create", "name": "API Group"}]
        status, preview = self.request(
            "POST", "/api/instances/bot-1/plugin-groups/preview", {"actions": actions}
        )
        self.assertEqual(status, 200, preview)
        status, rejected = self.request(
            "POST",
            "/api/instances/bot-1/plugin-groups/apply",
            {"preview_token": preview["preview_token"], "actions": actions},
        )
        self.assertEqual(status, 400, rejected)
        self.assertFalse(rejected["ok"])
        status, applied = self.request(
            "POST",
            "/api/instances/bot-1/plugin-groups/apply",
            {
                "preview_token": preview["preview_token"],
                "actions": actions,
                "confirm": True,
                "idempotency_key": "api-group-create",
            },
        )
        self.assertEqual(status, 200, applied)
        self.assertEqual(len(applied["groups"]), 1)
        group_id = applied["groups"][0]["id"]
        status, post_groups = self.request(
            "POST",
            "/api/instances/bot-1/plugin-groups",
            {
                "action": "add",
                "group_id": group_id,
                "plugin_id": "demo_plugin",
                "confirm": True,
                "idempotency_key": "api-group-add",
            },
        )
        self.assertEqual(status, 200, post_groups)
        status, groups = self.request("GET", "/api/instances/bot-1/plugin-groups")
        self.assertEqual(groups["groups"][0]["plugin_ids"], ["demo_plugin"])

        delete_actions = [{"op": "delete", "group_id": group_id}]
        status, delete_preview = self.request(
            "POST", "/api/instances/bot-1/plugin-groups/preview", {"actions": delete_actions}
        )
        self.assertEqual(status, 200, delete_preview)
        status, deleted = self.request(
            "POST",
            "/api/instances/bot-1/plugin-groups/apply",
            {
                "preview_token": delete_preview["preview_token"],
                "actions": delete_actions,
                "confirm": True,
            },
        )
        self.assertEqual(status, 200, deleted)
        self.assertEqual(deleted["groups"], [])

    def test_plugin_api_requires_token_and_sanitizes_bad_sources(self):
        status, payload = self.request(
            "GET", "/api/instances/bot-1/plugin-market", token=False
        )
        self.assertEqual(status, 403)
        self.assertFalse(payload["ok"])
        secret = "sk-this-must-not-leak-123456"
        status, payload = self.request(
            "POST",
            "/api/instances/bot-1/plugin-market/refresh",
            {"source": f"/tmp/{secret}/catalog.json"},
        )
        self.assertEqual(status, 400, payload)
        self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))

    def test_console_has_market_and_group_management_entrypoints(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "qqbot_studio" / "static" / "console.js").read_text(encoding="utf-8")
        styles = (root / "qqbot_studio" / "static" / "console.css").read_text(encoding="utf-8")
        for token in (
            "插件市场与分组",
            "/plugin-market",
            "/plugin-market/refresh",
            "/plugin-groups/preview",
            "/plugin-groups/apply",
            "创建分组",
            "改名",
            "删除分组",
            "加入分组",
            "从分组移出",
            "删除插件",
            "确认${verb}插件",
            "idempotency_key",
        ):
            self.assertIn(token, script)
        self.assertIn(".plugin-market-panel", styles)
        self.assertIn(".plugin-group-card", styles)
        self.assertNotRegex(script, r"\balert\s*\(|\bconfirm\s*\(")

    def test_unknown_operation_and_missing_confirmation_are_client_errors(self):
        status, payload = self.request(
            "GET", "/api/instances/bot-1/plugin-operations/op-0000000000000000"
        )
        self.assertEqual(status, 404, payload)
        status, payload = self.request(
            "POST",
            "/api/instances/bot-1/plugin-operations",
            {"operation": "install", "plugin_id": "missing_plugin"},
        )
        self.assertEqual(status, 400, payload)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
