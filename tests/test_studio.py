import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.studio import (
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
    StudioManager,
    detect_public_host,
)


class StudioManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = StudioManager(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_settings_and_first_run(self):
        settings = self.studio.load_settings()
        self.assertEqual(settings["bind_host"], DEFAULT_BIND_HOST)
        self.assertEqual(settings["bind_port"], DEFAULT_BIND_PORT)
        self.assertFalse(settings["first_run_complete"])
        updated = self.studio.update_network(
            bind_host="0.0.0.0",
            bind_port=9000,
            public_host="example.test",
        )
        self.assertTrue(updated["first_run_complete"])
        self.assertEqual(updated["bind_port"], 9000)
        self.assertEqual(self.studio.public_host(), "example.test")
        self.assertEqual(self.studio.public_base_url(), "http://example.test:9000")
        self.assertEqual(os.stat(self.studio.settings_path).st_mode & 0o777, 0o600)

    def test_invalid_port_rejected(self):
        with self.assertRaises(ValueError):
            self.studio.update_network(bind_port=70000)

    def test_instances_are_sequential_and_isolated(self):
        first = self.studio.create_instance("一号机器人")
        second = self.studio.create_instance("二号机器人")
        self.assertEqual(first["id"], "bot-1")
        self.assertEqual(second["id"], "bot-2")
        self.assertNotEqual(first["project_name"], second["project_name"])
        self.assertNotEqual(first["astrbot_container"], second["astrbot_container"])
        self.assertTrue((Path(self.tmp.name) / "instances" / "bot-1" / "instance.json").exists())
        metas = self.studio.list_instance_metas()
        self.assertEqual([item["id"] for item in metas], ["bot-1", "bot-2"])
        manager = self.studio.get_manager("bot-1")
        self.assertEqual(manager.project_name, first["project_name"])
        self.assertEqual(manager.docker.project_name, first["project_name"])
        self.assertEqual(manager.docker.ASTRBOT_CONTAINER, first["astrbot_container"])
        self.assertEqual(manager.docker.SNOWLUMA_CONTAINER, first["snowluma_container"])
        self.assertEqual(manager.bind_address, "0.0.0.0")

    def test_instance_id_validation(self):
        with self.assertRaises(ValueError):
            self.studio.get_instance_meta("../bad")
        with self.assertRaises(ValueError):
            self.studio.get_instance_meta("does-not-exist")

    def test_mirror_add_select_delete_and_candidate_order(self):
        nodes = self.studio.list_mirror_nodes()
        self.assertGreaterEqual(len(nodes), 4)
        node = self.studio.add_mirror_node("我的镜像", "registry.example.com/proxy/")
        self.assertTrue(node["custom"])
        self.assertEqual(node["prefix"], "registry.example.com/proxy/")
        self.studio.select_mirror(node["id"])
        prefixes = self.studio.candidate_mirror_prefixes()
        self.assertEqual(prefixes[0], "registry.example.com/proxy/")
        self.assertIn("m.daocloud.io/docker.io/", prefixes)
        self.studio.delete_mirror_node(node["id"])
        self.assertNotIn(node["id"], [item["id"] for item in self.studio.list_mirror_nodes()])
        with self.assertRaises(ValueError):
            self.studio.add_mirror_node("bad", "https://example.com/")
        with self.assertRaises(ValueError):
            self.studio.delete_mirror_node("daocloud")

    def test_mirror_test_url(self):
        self.assertEqual(
            self.studio._mirror_test_url("m.daocloud.io/docker.io/"),
            "https://m.daocloud.io/v2/",
        )

    def test_recover_interrupted_jobs_is_handled_by_manager(self):
        meta = self.studio.create_instance()
        manager = self.studio.get_manager(meta["id"])
        jobs_dir = manager.jobs_dir
        job = {
            "status": "running",
            "step": "拉取镜像",
            "progress": 30,
            "message": "running",
            "logs": ["line"],
            "error": "",
            "job_id": "job-old",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        (jobs_dir / "job-old.json").write_text(json.dumps(job), encoding="utf-8")
        # A new manager simulates a server restart.
        from qqbot_studio.deployer import DeployManager
        from qqbot_studio.store import Store
        restarted = DeployManager(
            store=Store(manager.data_dir),
            docker=manager.docker,
            public_host=manager.public_host,
        )
        recovered = restarted.get_job("job-old")
        self.assertEqual(recovered["status"], "error")
        self.assertIn("中断", recovered["error"])


if __name__ == "__main__":
    unittest.main()
