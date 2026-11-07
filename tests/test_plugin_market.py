"""Security and persistence tests for the v4.3 plugin market foundation."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import io
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from qqbot_studio.plugin_config_migration import (
    FieldMapError,
    load_field_map,
    preview_config_migration,
)
from qqbot_studio.plugin_market import (
    ArchiveValidationError,
    ConfirmationRequiredError,
    GroupError,
    IdempotencyConflictError,
    ManifestError,
    PluginConflictError,
    PluginGroupStore,
    PluginMarket,
    PluginNotFoundError,
    PluginOperationFailed,
    PluginStateError,
    UnsafePathError,
    extract_plugin_zip,
    inspect_plugin_zip,
    sha256_file,
    validate_manifest,
    verify_sha256,
    _validate_remote_url,
)


def make_zip(path: Path, root: str, files: dict[str, bytes | str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/", b"")
        for name, content in files.items():
            zf.writestr(f"{root}/{name}", content.encode("utf-8") if isinstance(content, str) else content)
    return path


def make_manifest(plugin_id: str, version: str, archive_name: str, sha: str, **extra):
    payload = {
        "id": plugin_id,
        "name": f"{plugin_id} display",
        "version": version,
        "sha256": sha,
        "download": archive_name,
        "description": "test plugin",
    }
    payload.update(extra)
    return payload


class PluginMarketTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plugin_root = self.root / "data" / "astrbot" / "plugins"
        self.market_dir = self.root / "studio-plugins" / "market"
        self.market = PluginMarket(self.root, plugin_root=self.plugin_root)

    def write_catalog(self, manifests: list[dict]) -> Path:
        path = self.market_dir / "catalog.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "plugins": manifests}), encoding="utf-8")
        return path

    def add_market_plugin(
        self,
        plugin_id: str = "demo_plugin",
        version: str = "1.0.0",
        files: dict[str, bytes | str] | None = None,
    ) -> tuple[Path, dict]:
        archive = make_zip(self.market_dir / f"{plugin_id}-{version}.zip", plugin_id, files or {"main.py": "VALUE = 1\n"})
        manifest = make_manifest(plugin_id, version, archive.name, sha256_file(archive))
        return archive, manifest

    def install_plugin(self, manifest: dict) -> dict:
        self.write_catalog([manifest])
        self.market.refresh_market()
        preview = self.market.preview_operation(
            {"operation": "install", "plugin_id": manifest["id"], "confirm": False}
        )
        self.assertTrue(preview["ok"], preview)
        return self.market.execute_operation(
            {
                "operation": "install",
                "plugin_id": manifest["id"],
                "preview_token": preview["preview_token"],
                "confirm": True,
                "idempotency_key": f"install-{manifest['id']}-{manifest['version']}",
            }
        )


class ManifestAndZipTests(PluginMarketTestBase):
    def test_manifest_validation_preserves_unknown_fields_and_normalises_sha(self):
        digest = "A" * 64
        result = validate_manifest(
            {
                "id": "demo_plugin",
                "name": "Demo",
                "version": "1.2.3",
                "sha256": digest,
                "download": "demo.zip",
                "root": "demo_plugin",
                "custom_manifest_key": {"keep": True},
            }
        )
        self.assertEqual(result["id"], "demo_plugin")
        self.assertEqual(result["sha256"], "a" * 64)
        self.assertEqual(result["extra"]["custom_manifest_key"], {"keep": True})

    def test_manifest_rejects_bad_sha_root_and_id(self):
        base = {
            "id": "demo_plugin",
            "version": "1.0.0",
            "sha256": "0" * 64,
            "download": "demo.zip",
        }
        with self.assertRaises(ManifestError):
            validate_manifest({**base, "sha256": "not-a-sha"})
        with self.assertRaises(ManifestError):
            validate_manifest({**base, "root": "other_root"})
        with self.assertRaises(ManifestError):
            validate_manifest({**base, "id": "../escape"})

    def test_sha256_verification(self):
        archive, manifest = self.add_market_plugin()
        self.assertEqual(verify_sha256(archive, manifest["sha256"]), manifest["sha256"])
        with self.assertRaises(PluginConflictError):
            verify_sha256(archive, "0" * 64)

    def test_valid_zip_requires_exact_manifest_root(self):
        archive, _ = self.add_market_plugin(files={"main.py": "print('ok')\n", "nested/a.txt": "a"})
        info = inspect_plugin_zip(archive, "demo_plugin")
        self.assertEqual(info.root_dir, "demo_plugin")
        self.assertEqual(info.file_count, 2)
        destination = self.root / "staging"
        extracted = extract_plugin_zip(archive, destination, "demo_plugin")
        self.assertEqual(extracted, destination / "demo_plugin")
        self.assertTrue((extracted / "nested" / "a.txt").is_file())

    def test_zip_rejects_path_traversal_absolute_and_backslash(self):
        for index, unsafe_name in enumerate(("../escape.py", "/absolute.py", "nested\\evil.py")):
            with self.subTest(unsafe_name=unsafe_name):
                archive = self.root / f"unsafe-{index}.zip"
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("demo_plugin/main.py", "ok")
                    zf.writestr(f"demo_plugin/{unsafe_name}", "bad")
                with self.assertRaises(ArchiveValidationError):
                    inspect_plugin_zip(archive, "demo_plugin")

    def test_zip_rejects_wrong_or_multiple_roots_and_rootless_layout(self):
        cases = {
            "wrong.zip": [("other/main.py", "x")],
            "rootless.zip": [("main.py", "x")],
            "multiroot.zip": [("demo_plugin/main.py", "x"), ("other/main.py", "y")],
        }
        for name, entries in cases.items():
            with self.subTest(name=name):
                archive = self.root / name
                with zipfile.ZipFile(archive, "w") as zf:
                    for entry, content in entries:
                        zf.writestr(entry, content)
                with self.assertRaises(ArchiveValidationError):
                    inspect_plugin_zip(archive, "demo_plugin")

    def test_zip_rejects_symlink_and_duplicate_names(self):
        symlink_archive = self.root / "symlink.zip"
        with zipfile.ZipFile(symlink_archive, "w") as zf:
            zf.writestr("demo_plugin/main.py", "x")
            info = zipfile.ZipInfo("demo_plugin/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "main.py")
        with self.assertRaises(UnsafePathError):
            inspect_plugin_zip(symlink_archive, "demo_plugin")

        duplicate_archive = self.root / "duplicate.zip"
        with zipfile.ZipFile(duplicate_archive, "w") as zf:
            zf.writestr("demo_plugin/main.py", "one")
            zf.writestr("demo_plugin/main.py", "two")
        with self.assertRaises(ArchiveValidationError):
            inspect_plugin_zip(duplicate_archive, "demo_plugin")

    def test_remote_sources_require_https_and_trusted_hosts(self):
        self.market_dir.mkdir(parents=True, exist_ok=True)
        for url in (
            "http://trusted.example/catalog.json",
            "https://untrusted.example/catalog.json",
            "https://user:password@trusted.example/catalog.json",
        ):
            with self.subTest(url=url):
                with self.assertRaises((UnsafePathError, ManifestError)):
                    self.market.refresh_market(url)
        with self.assertRaises(UnsafePathError):
            _validate_remote_url("https://untrusted.example/catalog.json", ("trusted.example",))
        with self.assertRaises(UnsafePathError):
            _validate_remote_url("http://trusted.example/catalog.json", ("trusted.example",))
        self.assertTrue(
            _validate_remote_url("https://plugins.example/catalog.json", ("plugins.example",))
        )

    def test_trusted_https_catalog_and_relative_archive_install(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("remote_plugin/", b"")
            zf.writestr("remote_plugin/main.py", "REMOTE = True\n")
        archive_bytes = archive_buffer.getvalue()
        digest = hashlib.sha256(archive_bytes).hexdigest()
        catalog = json.dumps(
            {
                "schema_version": 1,
                "plugins": [
                    {
                        "id": "remote_plugin",
                        "name": "Remote",
                        "version": "1.2.3",
                        "sha256": digest,
                        "download": "remote_plugin-1.2.3.zip",
                    }
                ],
            }
        ).encode("utf-8")
        trusted = PluginMarket(
            self.root,
            plugin_root=self.plugin_root,
            trusted_hosts=("plugins.example",),
        )

        def fake_get(url, **kwargs):
            if url == "https://plugins.example/catalog.json":
                return catalog, url
            if url == "https://plugins.example/remote_plugin-1.2.3.zip":
                return archive_bytes, url
            raise AssertionError(url)

        with mock.patch("qqbot_studio.plugin_market._http_get_bytes", side_effect=fake_get):
            refreshed = trusted.refresh_market("https://plugins.example/catalog.json")
            self.assertEqual(refreshed["count"], 1)
            installed = trusted.install_plugin("remote_plugin")
        self.assertTrue(installed["ok"], installed)
        self.assertEqual(
            (self.plugin_root / "remote_plugin" / "main.py").read_text(encoding="utf-8"),
            "REMOTE = True\n",
        )

    def test_fixed_source_config_allows_trusted_url_without_request_body(self):
        self.market_dir.mkdir(parents=True, exist_ok=True)
        (self.market_dir / "trusted-hosts.json").write_text(
            json.dumps({"hosts": ["plugins.example"]}), encoding="utf-8"
        )
        (self.market_dir / "source.json").write_text(
            json.dumps({"url": "https://plugins.example/catalog.json"}), encoding="utf-8"
        )
        with mock.patch(
            "qqbot_studio.plugin_market._http_get_bytes",
            return_value=(b'{"plugins": []}', "https://plugins.example/catalog.json"),
        ) as getter:
            result = self.market.refresh_market()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 0)
        getter.assert_called_once()

    def test_local_catalog_and_manifest_paths_are_confined(self):
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(UnsafePathError):
            self.market.refresh_market(str(outside))
        with self.assertRaises(UnsafePathError):
            self.market.refresh_market("../outside.json")
        with self.assertRaises(ManifestError):
            validate_manifest(
                {
                    "id": "demo_plugin",
                    "version": "1.0.0",
                    "sha256": "0" * 64,
                    "download": "http://untrusted.example/demo.zip",
                }
            )


class PluginInstallDeleteTests(PluginMarketTestBase):
    def test_install_registers_exact_directory_and_does_not_touch_user_data(self):
        archive, manifest = self.add_market_plugin()
        config_dir = self.root / "data" / "astrbot" / "config"
        plugin_data = self.root / "data" / "astrbot" / "plugin_data" / "demo_plugin"
        sessions = self.root / "data" / "astrbot" / "sessions"
        config_dir.mkdir(parents=True)
        plugin_data.mkdir(parents=True)
        sessions.mkdir(parents=True)
        config = config_dir / "demo_plugin_config.json"
        config.write_text('{"keep": true}', encoding="utf-8")
        (plugin_data / "data.bin").write_bytes(b"data")
        (sessions / "session.json").write_text("{}", encoding="utf-8")

        result = self.install_plugin(manifest)
        self.assertTrue(result["ok"], result)
        target = self.plugin_root / "demo_plugin"
        self.assertTrue((target / "main.py").is_file())
        registry = json.loads(self.market.registry_path.read_text(encoding="utf-8"))
        self.assertEqual(registry["plugins"]["demo_plugin"]["version"], "1.0.0")
        self.assertEqual(config.read_text(encoding="utf-8"), '{"keep": true}')
        self.assertEqual((plugin_data / "data.bin").read_bytes(), b"data")

        delete_preview = self.market.preview_operation(
            {"operation": "delete", "plugin_id": "demo_plugin"}
        )
        deleted = self.market.execute_operation(
            {
                "operation": "delete",
                "plugin_id": "demo_plugin",
                "preview_token": delete_preview["preview_token"],
                "confirm": True,
            }
        )
        self.assertTrue(deleted["ok"], deleted)
        self.assertFalse(target.exists())
        self.assertEqual(config.read_text(encoding="utf-8"), '{"keep": true}')
        self.assertEqual((plugin_data / "data.bin").read_bytes(), b"data")
        self.assertTrue((sessions / "session.json").is_file())
        registry = json.loads(self.market.registry_path.read_text(encoding="utf-8"))
        self.assertNotIn("demo_plugin", registry["plugins"])

    def test_update_makes_backup_before_replacing_code(self):
        _, manifest_v1 = self.add_market_plugin(version="1.0.0", files={"main.py": "OLD = True\n"})
        self.install_plugin(manifest_v1)
        config = self.root / "data" / "astrbot" / "config" / "demo_plugin_config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{"user": "kept"}', encoding="utf-8")

        _, manifest_v2 = self.add_market_plugin(version="2.0.0", files={"main.py": "OLD = False\n", "new.py": "NEW = True\n"})
        self.write_catalog([manifest_v2])
        self.market.refresh_market()
        preview = self.market.preview_operation({"operation": "update", "plugin_id": "demo_plugin"})
        self.assertTrue(preview["plan"]["backup_required"])
        result = self.market.execute_operation(
            {
                "operation": "update",
                "plugin_id": "demo_plugin",
                "preview_token": preview["preview_token"],
                "confirm": True,
            }
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("backup_name", result["operation"])
        self.assertTrue(result["operation"]["backup_name"])
        backups = list((self.root / "studio-plugins" / "backups" / "plugins" / "demo_plugin").glob("*.zip"))
        self.assertEqual(len(backups), 1)
        with zipfile.ZipFile(backups[0]) as zf:
            self.assertEqual(zf.read("demo_plugin/main.py"), b"OLD = True\n")
        self.assertEqual((self.plugin_root / "demo_plugin" / "main.py").read_text(encoding="utf-8"), "OLD = False\n")
        self.assertEqual(config.read_text(encoding="utf-8"), '{"user": "kept"}')

    def test_install_rejects_unregistered_existing_directory_and_reserved_plugin(self):
        target = self.plugin_root / "demo_plugin"
        target.mkdir(parents=True)
        (target / "owned-by-user.txt").write_text("keep", encoding="utf-8")
        _, manifest = self.add_market_plugin()
        self.write_catalog([manifest])
        self.market.refresh_market()
        with self.assertRaises(PluginConflictError):
            self.market.preview_operation({"operation": "install", "plugin_id": "demo_plugin"})
        self.assertTrue((target / "owned-by-user.txt").is_file())

        reserved_archive = make_zip(self.market_dir / "qqbot_enhancer.zip", "qqbot_enhancer", {"main.py": "x"})
        reserved = make_manifest("qqbot_enhancer", "99.0.0", reserved_archive.name, sha256_file(reserved_archive))
        self.write_catalog([reserved])
        self.market.refresh_market()
        with self.assertRaises(PluginConflictError):
            self.market.preview_operation({"operation": "install", "plugin_id": "qqbot_enhancer"})

    def test_delete_refuses_unregistered_and_reserved_plugins(self):
        self.plugin_root.mkdir(parents=True, exist_ok=True)
        target = self.plugin_root / "unregistered"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(PluginNotFoundError):
            self.market.preview_operation({"operation": "delete", "plugin_id": "unregistered"})
        self.disk_path = target / "keep.txt"
        self.assertTrue(self.disk_path.is_file())

        enhancer = self.plugin_root / "qqbot_enhancer"
        enhancer.mkdir()
        (enhancer / "main.py").write_text("marker", encoding="utf-8")
        self.market._write_registry(
            {
                "schema_version": 1,
                "plugins": {
                    "qqbot_enhancer": {
                        "id": "qqbot_enhancer",
                        "version": "4.3.0",
                        "reserved": True,
                        "installed_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
        )
        with self.assertRaises(PluginConflictError):
            self.market.preview_operation({"operation": "delete", "plugin_id": "qqbot_enhancer"})
        self.assertEqual((enhancer / "main.py").read_text(encoding="utf-8"), "marker")

    def test_operation_requires_confirmation_and_is_idempotent(self):
        _, manifest = self.add_market_plugin()
        self.write_catalog([manifest])
        self.market.refresh_market()
        with self.assertRaises(ConfirmationRequiredError):
            self.market.execute_operation({"operation": "install", "plugin_id": "demo_plugin"})
        preview = self.market.preview_operation(
            {"operation": "install", "plugin_id": "demo_plugin"}
        )
        payload = {
            "operation": "install",
            "plugin_id": "demo_plugin",
            "preview_token": preview["preview_token"],
            "confirm": True,
            "idempotency_key": "same-operation",
        }
        first = self.market.execute_operation(payload)
        second = self.market.execute_operation(payload)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["operation"]["id"], second["operation"]["id"])
        self.assertTrue((self.plugin_root / "demo_plugin" / "main.py").is_file())
        with self.assertRaises(IdempotencyConflictError):
            self.market.execute_operation({**payload, "plugin_id": "demo_plugin_other"})

    def test_direct_operation_replay_is_idempotent_without_reusing_preview(self):
        _, manifest = self.add_market_plugin()
        self.write_catalog([manifest])
        self.market.refresh_market()
        payload = {
            "operation": "install",
            "plugin_id": "demo_plugin",
            "confirm": True,
            "idempotency_key": "direct-replay",
            "version": "1.0.0",
        }
        first = self.market.execute_operation(payload)
        second = self.market.execute_operation(payload)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["operation"]["id"], second["operation"]["id"])
        self.assertTrue((self.plugin_root / "demo_plugin" / "main.py").is_file())

    def test_concurrent_install_keeps_registry_valid_and_only_one_writer(self):
        _, manifest = self.add_market_plugin()
        self.write_catalog([manifest])
        self.market.refresh_market()
        previews = [
            self.market.preview_operation({"operation": "install", "plugin_id": "demo_plugin"})
            for _ in range(2)
        ]
        barrier = threading.Barrier(2)

        def run(index: int):
            barrier.wait(timeout=5)
            return self.market.execute_operation(
                {
                    "operation": "install",
                    "plugin_id": "demo_plugin",
                    "preview_token": previews[index]["preview_token"],
                    "confirm": True,
                }
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, index) for index in range(2)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(("ok", future.result()))
                except PluginOperationFailed as exc:
                    outcomes.append(("error", exc))
        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1)
        registry = json.loads(self.market.registry_path.read_text(encoding="utf-8"))
        self.assertIn("demo_plugin", registry["plugins"])
        self.assertTrue((self.plugin_root / "demo_plugin" / "main.py").is_file())

    def test_traversal_archive_is_rejected_without_creating_target(self):
        self.market_dir.mkdir(parents=True, exist_ok=True)
        archive = self.market_dir / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("demo_plugin/main.py", "ok")
            zf.writestr("demo_plugin/../../escape.py", "evil")
        manifest = make_manifest("demo_plugin", "1.0.0", archive.name, sha256_file(archive))
        self.write_catalog([manifest])
        self.market.refresh_market()
        with self.assertRaises(ArchiveValidationError):
            self.market.preview_operation({"operation": "install", "plugin_id": "demo_plugin"})
        self.assertFalse((self.plugin_root / "demo_plugin").exists())
        self.assertFalse((self.root / "escape.py").exists())

    def test_registry_corruption_is_not_overwritten(self):
        self.market.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.market.registry_path.write_text("{broken", encoding="utf-8")
        before = self.market.registry_path.read_bytes()
        with self.assertRaises(PluginStateError):
            self.market.market_status()
        self.assertEqual(self.market.registry_path.read_bytes(), before)


class PluginGroupTests(PluginMarketTestBase):
    def _preview_apply(self, actions, key="group-key"):
        preview = self.market.groups.preview({"actions": actions})
        return self.market.groups.apply(
            {
                "preview_token": preview["preview_token"],
                "actions": actions,
                "confirm": True,
                "idempotency_key": key,
            }
        )

    def test_group_crud_and_many_to_many_membership(self):
        created = self._preview_apply([{"op": "create", "name": "QQ Bot"}], key="create-1")
        group_id = created["groups"][0]["id"]
        added = self._preview_apply(
            [
                {"op": "add", "group_id": group_id, "plugin_id": "plugin_a"},
                {"op": "add", "group_id": group_id, "plugin_id": "plugin_b"},
            ],
            key="add-1",
        )
        self.assertEqual(added["groups"][0]["plugin_ids"], ["plugin_a", "plugin_b"])
        second = self._preview_apply([{"op": "create", "name": "Second"}], key="create-2")
        other_id = next(group["id"] for group in second["groups"] if group["name"] == "Second")
        self._preview_apply([{"op": "add", "group_id": other_id, "plugin_id": "plugin_a"}], key="add-2")
        groups = self.market.groups.list_groups()["groups"]
        by_name = {group["name"]: group for group in groups}
        self.assertIn("plugin_a", by_name["QQ Bot"]["plugin_ids"])
        self.assertIn("plugin_a", by_name["Second"]["plugin_ids"])
        renamed = self._preview_apply(
            [{"op": "rename", "group_id": group_id, "name": "Renamed"}], key="rename-1"
        )
        self.assertTrue(any(group["name"] == "Renamed" for group in renamed["groups"]))
        removed = self._preview_apply(
            [{"op": "remove", "group_id": group_id, "plugin_id": "plugin_a"}], key="remove-1"
        )
        renamed_group = next(group for group in removed["groups"] if group["id"] == group_id)
        self.assertEqual(renamed_group["plugin_ids"], ["plugin_b"])
        deleted = self._preview_apply(
            [{"op": "delete", "group_id": other_id}], key="delete-1"
        )
        self.assertFalse(any(group["id"] == other_id for group in deleted["groups"]))

    def test_delete_group_performs_zero_plugin_file_operations(self):
        self._preview_apply([{"op": "create", "name": "Only Metadata"}], key="create")
        group_id = self.market.groups.list_groups()["groups"][0]["id"]
        plugin_dir = self.plugin_root / "plugin_a"
        config = self.root / "data" / "astrbot" / "config" / "plugin_a_config.json"
        plugin_dir.mkdir(parents=True)
        config.parent.mkdir(parents=True)
        (plugin_dir / "main.py").write_text("keep", encoding="utf-8")
        config.write_text('{"keep": true}', encoding="utf-8")
        before = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in (plugin_dir, config.parent)
            for path in ([path] if path.is_file() else path.rglob("*"))
            if path.is_file()
        }
        with mock.patch("qqbot_studio.plugin_market.shutil.rmtree", side_effect=AssertionError("filesystem delete forbidden")):
            deleted = self._preview_apply([{"op": "delete", "group_id": group_id}], key="delete")
        self.assertEqual(deleted["groups"], [])
        after = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in (plugin_dir, config.parent)
            for path in ([path] if path.is_file() else path.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_delete_plugin_cleans_group_relations_and_preserves_config(self):
        _, manifest = self.add_market_plugin(files={"main.py": "x"})
        self.install_plugin(manifest)
        self._preview_apply([{"op": "create", "name": "Group"}], key="create")
        group_id = self.market.groups.list_groups()["groups"][0]["id"]
        self._preview_apply(
            [{"op": "add", "group_id": group_id, "plugin_id": "demo_plugin"}], key="add"
        )
        config = self.root / "data" / "astrbot" / "config" / "demo_plugin_config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{"keep": true}', encoding="utf-8")
        preview = self.market.preview_operation({"operation": "delete", "plugin_id": "demo_plugin"})
        result = self.market.execute_operation(
            {
                "operation": "delete",
                "plugin_id": "demo_plugin",
                "preview_token": preview["preview_token"],
                "confirm": True,
            }
        )
        self.assertTrue(result["ok"], result)
        group = self.market.groups.list_groups()["groups"][0]
        self.assertEqual(group["plugin_ids"], [])
        self.assertEqual(config.read_text(encoding="utf-8"), '{"keep": true}')

    def test_direct_group_crud_helpers_and_plugin_install_convenience(self):
        created = self.market.groups.create_group("Direct Group")
        group_id = created["group"]["id"]
        added = self.market.groups.add_plugin(group_id, "plugin_a")
        self.assertEqual(added["group"]["plugin_ids"], ["plugin_a"])
        renamed = self.market.groups.rename_group(group_id, "Direct Renamed")
        self.assertEqual(renamed["group"]["name"], "Direct Renamed")
        removed = self.market.groups.remove_plugin(group_id, "plugin_a")
        self.assertEqual(removed["group"]["plugin_ids"], [])
        deleted = self.market.groups.delete_group(group_id)
        self.assertTrue(deleted["deleted"])

        _, manifest = self.add_market_plugin()
        self.write_catalog([manifest])
        self.market.refresh_market()
        installed = self.market.install_plugin(
            manifest,
            idempotency_key="convenience-install",
        )
        self.assertTrue(installed["ok"], installed)
        self.assertTrue((self.plugin_root / "demo_plugin" / "main.py").is_file())

    def test_group_apply_requires_confirmation_and_is_idempotent(self):
        actions = [{"op": "create", "name": "Idempotent"}]
        preview = self.market.groups.preview(actions)
        with self.assertRaises(ConfirmationRequiredError):
            self.market.groups.apply({"preview_token": preview["preview_token"], "actions": actions})
        payload = {
            "preview_token": preview["preview_token"],
            "actions": actions,
            "confirm": True,
            "idempotency_key": "group-idem",
        }
        first = self.market.groups.apply(payload)
        second = self.market.groups.apply(payload)
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(first["groups"]), len(second["groups"]))
        self.assertEqual(len(self.market.groups.list_groups()["groups"]), 1)

    def test_duplicate_memberships_are_noop_and_conflicts_are_rejected(self):
        self._preview_apply([{"op": "create", "name": "Dup"}], key="create")
        group_id = self.market.groups.list_groups()["groups"][0]["id"]
        actions = [{"op": "add", "group_id": group_id, "plugin_id": "plugin_a"}]
        self._preview_apply(actions, key="add-a")
        preview = self.market.groups.preview(actions)
        result = self.market.groups.apply(
            {
                "preview_token": preview["preview_token"],
                "actions": actions,
                "confirm": True,
            }
        )
        self.assertFalse(result["applied"][0]["will_change"])
        self.assertEqual(self.market.groups.list_groups()["groups"][0]["plugin_ids"], ["plugin_a"])

    def test_group_database_corruption_is_not_replaced(self):
        db = self.root / "studio-plugins" / "state.sqlite3"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"not a sqlite database")
        before = db.read_bytes()
        with self.assertRaises(PluginStateError):
            PluginGroupStore(db).list_groups()
        self.assertEqual(db.read_bytes(), before)

    def test_concurrent_group_adds_do_not_duplicate(self):
        self._preview_apply([{"op": "create", "name": "Concurrent"}], key="create")
        group_id = self.market.groups.list_groups()["groups"][0]["id"]
        actions = [{"op": "add", "group_id": group_id, "plugin_id": "plugin_x"}]

        def run(index: int):
            preview = self.market.groups.preview(actions)
            return self.market.groups.apply(
                {
                    "preview_token": preview["preview_token"],
                    "actions": actions,
                    "confirm": True,
                    "idempotency_key": f"add-{index}",
                }
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, range(4)))
        group = self.market.groups.list_groups()["groups"][0]
        self.assertEqual(group["plugin_ids"], ["plugin_x"])


class ConfigMigrationPreviewTests(unittest.TestCase):
    def test_field_map_and_preview_preserve_unknown_fields_and_existing_values(self):
        field_map = {
            "schema_version": 1,
            "description": "do not move 264 fields yet",
            "custom_top_level": {"keep": True},
            "fields": {
                "reply_probability": {
                    "target_plugin": "qqbot_core_reply",
                    "target_key": "reply_probability",
                    "transform": "copy",
                    "custom_entry_key": "keep",
                },
                "enabled": {
                    "target_plugin": "qqbot_core_reply",
                    "target_key": "enabled",
                    "transform": "bool",
                },
            },
        }
        source = {"reply_probability": 0.3, "enabled": "yes", "undocumented": {"nested": 1}}
        existing = {"qqbot_core_reply": {"reply_probability": 0.9, "target_only": "keep"}}
        preview = preview_config_migration(field_map, source, existing_configs=existing)
        self.assertTrue(preview["dry_run"])
        self.assertFalse(preview["writes_files"])
        self.assertEqual(preview["unknown_fields"]["undocumented"], {"nested": 1})
        self.assertEqual(preview["preserved_target_keys"]["qqbot_core_reply"], ["target_only"])
        by_source = {move["source_key"]: move for move in preview["moves"]}
        self.assertTrue(by_source["reply_probability"]["conflict"])
        self.assertFalse(by_source["reply_probability"]["would_write"])
        self.assertTrue(by_source["enabled"]["would_write"])
        self.assertEqual(existing["qqbot_core_reply"]["reply_probability"], 0.9)
        self.assertEqual(preview["field_map_extensions"]["custom_top_level"], {"keep": True})
        loaded = load_field_map(field_map)
        self.assertEqual(loaded["fields"][0]["metadata"]["custom_entry_key"], "keep")

    def test_field_map_compact_string_form_and_old_new_aliases(self):
        compact = load_field_map(
            {
                "target_plugin": "target_plugin",
                "fields": {"old_value": "new_value"},
            }
        )
        self.assertEqual(compact["fields"][0]["source_key"], "old_value")
        self.assertEqual(compact["fields"][0]["target_plugin"], "target_plugin")
        self.assertEqual(compact["fields"][0]["target_key"], "new_value")
        aliases = load_field_map(
            {
                "mapping": [
                    {"old": "legacy", "new": "modern", "target": "target_plugin"},
                ]
            }
        )
        self.assertEqual(aliases["fields"][0]["source_key"], "legacy")
        self.assertEqual(aliases["fields"][0]["target_key"], "modern")

    def test_field_map_validation_is_strict_and_never_executes_code(self):
        with self.assertRaises(FieldMapError):
            load_field_map(
                {
                    "fields": [
                        {
                            "source_key": "x",
                            "target_plugin": "valid_plugin",
                            "target_key": "y",
                            "transform": "__import__('os').system('echo bad')",
                        }
                    ]
                }
            )
        with self.assertRaises(FieldMapError):
            load_field_map({"fields": [{"source_key": "x", "target_plugin": "../bad", "target_key": "y"}]})
        with self.assertRaises(FieldMapError):
            load_field_map(
                {
                    "fields": [
                        {"source_key": "x", "target_plugin": "valid_plugin", "target_key": "a"},
                        {"source_key": "x", "target_plugin": "valid_plugin", "target_key": "b"},
                    ]
                }
            )

    def test_preview_is_read_only_for_real_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            field_map_path = root / "field-map.json"
            source_path = root / "old.json"
            field_map_path.write_text(
                json.dumps(
                    {
                        "fields": [
                            {
                                "source_key": "value",
                                "target_plugin": "target_plugin",
                                "target_key": "value",
                            },
                            {
                                "source_key": "required_missing",
                                "target_plugin": "target_plugin",
                                "target_key": "required",
                                "required": True,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            source_path.write_text('{"value": 1, "unknown": "keep"}', encoding="utf-8")
            before_map = field_map_path.read_bytes()
            before_source = source_path.read_bytes()
            preview = preview_config_migration(
                load_field_map(field_map_path),
                json.loads(source_path.read_text(encoding="utf-8")),
            )
            self.assertEqual(field_map_path.read_bytes(), before_map)
            self.assertEqual(source_path.read_bytes(), before_source)
            self.assertEqual(preview["unknown_fields"]["unknown"], "keep")
            self.assertEqual(preview["constraints"][0]["source_key"], "required_missing")


if __name__ == "__main__":
    unittest.main()
