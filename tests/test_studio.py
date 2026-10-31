import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.dockerctl import CommandResult, DockerStatus
from qqbot_studio.studio import (
    DEFAULT_BIND_HOST,
    DEFAULT_BIND_PORT,
    StudioManager,
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

    def test_new_instances_get_distinct_ports(self):
        first = self.studio.create_instance("一号机器人")
        second = self.studio.create_instance("二号机器人")
        first_config = json.loads(
            (Path(self.tmp.name) / "instances" / first["id"] / "config.json").read_text(encoding="utf-8")
        )
        second_config = json.loads(
            (Path(self.tmp.name) / "instances" / second["id"] / "config.json").read_text(encoding="utf-8")
        )
        first_ports = [
            first_config["ports"]["astrbot_webui"],
            first_config["ports"]["snowluma_webui"],
            first_config["ports"]["novnc"],
        ]
        second_ports = [
            second_config["ports"]["astrbot_webui"],
            second_config["ports"]["snowluma_webui"],
            second_config["ports"]["novnc"],
        ]
        self.assertEqual(first_ports, [first_ports[0], first_ports[0] + 1, first_ports[0] + 2])
        self.assertEqual(second_ports, [second_ports[0], second_ports[0] + 1, second_ports[0] + 2])
        self.assertGreaterEqual(first_ports[0], 5000)
        self.assertLessEqual(second_ports[2], 10000)
        self.assertTrue(set(first_ports).isdisjoint(second_ports))

    def test_delete_instance_removes_its_directory(self):
        meta = self.studio.create_instance("待删除机器人")
        instance_dir = Path(self.tmp.name) / "instances" / meta["id"]
        (instance_dir / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        (instance_dir / ".env").write_text("X=1\n", encoding="utf-8")
        fake_manager = mock.MagicMock()
        fake_manager._active_job_id = ""
        fake_manager.docker.down.return_value = None
        with mock.patch.object(self.studio, "get_manager", return_value=fake_manager):
            deleted = self.studio.delete_instance(meta["id"])
        self.assertEqual(deleted["id"], meta["id"])
        self.assertFalse(instance_dir.exists())
        fake_manager.docker.down.assert_called_once()

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


class FakeStatsDocker:
    ASTRBOT_CONTAINER = "qqbot-bot-1-astrbot"
    SNOWLUMA_CONTAINER = "qqbot-bot-1-snowluma"

    def __init__(self):
        self.calls = []

    def detect(self, force=False):
        return DockerStatus(True, True, True, True)

    def _docker(self, args, timeout=60, extra_env=None):
        self.calls.append(list(args))
        lines = [
            json.dumps(
                {
                    "Name": self.ASTRBOT_CONTAINER,
                    "CPUPerc": "1.25%",
                    "MemUsage": "100MiB / 1GiB",
                    "MemPerc": "9.77%",
                }
            ),
            json.dumps(
                {
                    "Name": self.SNOWLUMA_CONTAINER,
                    "CPUPerc": "2.50%",
                    "MemUsage": "200MiB / 2GiB",
                    "MemPerc": "9.77%",
                }
            ),
        ]
        return CommandResult(tuple(args), 0, "\n".join(lines), "")


class FakeStatsManager:
    def __init__(self, instance_id="bot-1"):
        self.instance_id = instance_id
        self.docker = FakeStatsDocker()


class StudioV3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = StudioManager(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stats_report_disk_host_memory_and_docker_values(self):
        meta = self.studio.create_instance("统计机器人")
        instance_dir = Path(self.tmp.name) / "instances" / meta["id"]
        payload_file = instance_dir / "data" / "astrbot" / "sample.bin"
        payload_file.parent.mkdir(parents=True, exist_ok=True)
        payload_file.write_bytes(b"x" * 1536)
        fake_manager = FakeStatsManager(meta["id"])
        with mock.patch.object(self.studio, "get_manager", return_value=fake_manager):
            stats = self.studio.get_stats(meta["id"], force_refresh=True)

        self.assertTrue(stats["ok"])
        self.assertEqual(stats["scope"], "instance")
        instance = stats["instances"][0]
        self.assertEqual(instance["instance_id"], meta["id"])
        self.assertGreaterEqual(instance["data_dir_size_bytes"], 1536)
        self.assertGreaterEqual(instance["file_count"], 1)
        self.assertEqual(len(stats["docker"]), 2)
        astrbot_stats = next(item for item in stats["docker"] if item["service"] == "astrbot")
        self.assertEqual(astrbot_stats["memory_usage_bytes"], 100 * 1024 * 1024)
        self.assertEqual(astrbot_stats["memory_limit_bytes"], 1024 * 1024 * 1024)
        self.assertEqual(astrbot_stats["cpu_percent"], 1.25)
        self.assertEqual(astrbot_stats["memory_percent"], 9.77)
        self.assertIn("total_bytes", stats["host_memory"])
        self.assertIn("disk", stats["global"])
        self.assertIn("instances_total_bytes", stats["global"])
        self.assertTrue(any("stats" in call for call in fake_manager.docker.calls))

    def test_stats_degrade_when_docker_is_unavailable(self):
        meta = self.studio.create_instance("无 Docker 机器人")

        class OfflineDocker:
            def detect(self, force=False):
                return DockerStatus(False, False, False, False, detail="Docker 不可用")

        class OfflineManager:
            docker = OfflineDocker()

        with mock.patch.object(self.studio, "get_manager", return_value=OfflineManager()):
            stats = self.studio.get_stats(meta["id"], force_refresh=True)
        self.assertTrue(stats["ok"])
        self.assertEqual(len(stats["docker"]), 2)
        self.assertTrue(all(item["error"] == "Docker 不可用" for item in stats["docker"]))
        self.assertIn("Docker 不可用", " ".join(stats["errors"]))

    def test_studio_wires_optional_assistant_status_without_network(self):
        status = self.studio.assistant_status()
        self.assertTrue(status["ok"])
        self.assertFalse(status["installed"])
        self.assertEqual(
            status["path"],
            str(Path(self.tmp.name) / "extensions" / "ai-assistant"),
        )
        self.assertNotIn("api_key", json.dumps(status, ensure_ascii=False).lower())
        config = self.studio.assistant_config()
        self.assertTrue(config["ok"])
        self.assertFalse(config["installed"])
        self.assertFalse(config["configured"])
        self.assertFalse(config["running"])
        saved = self.studio.save_assistant_config({"api_key": "sk-not-installed-123456"})
        self.assertFalse(saved["ok"])
        self.assertNotIn("sk-not-installed-123456", json.dumps(saved, ensure_ascii=False))
        started = self.studio.start_assistant()
        self.assertFalse(started["ok"])
        self.assertFalse(started["running"])
        diagnosis = self.studio.assistant_diagnose()
        self.assertTrue(diagnosis["ok"])
        self.assertFalse(diagnosis["healthy"])
        self.assertEqual(diagnosis["error_code"], "not_installed")
        self.assertIn("listener", diagnosis)
        self.assertIn("public_probe", diagnosis)
        self.assertIn("suggestions", diagnosis)
        self.assertNotIn("api_key", json.dumps(diagnosis, ensure_ascii=False).lower())
        self.assertNotIn("sk-", json.dumps(diagnosis, ensure_ascii=False))

    def test_v1_root_layout_is_not_auto_migrated_and_current_v4_stays_safe(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            (root / "data" / "astrbot" / "config").mkdir(parents=True)
            old_config = json.dumps(
                {
                    "schema_version": 1,
                    "qq_account": "12345678",
                    "prompt": "旧提示词",
                    "ports": {
                        "astrbot_webui": 16185,
                        "snowluma_webui": 15099,
                        "novnc": 16081,
                    },
                    "deployed": True,
                }
            )
            (root / "config.json").write_text(old_config, encoding="utf-8")
            old_compose = (
                "services:\n"
                "  astrbot:\n"
                "    container_name: qqbot-astrbot\n"
                "  snowluma:\n"
                "    container_name: qqbot-snowluma\n"
                "networks:\n"
                "  qqbot-studio-net:\n"
                "    name: qqbot-studio-net\n"
            )
            (root / "compose.yml").write_text(old_compose, encoding="utf-8")
            (root / ".env").write_text("VNC_PASSWD=oldpass1\n", encoding="utf-8")

            studio = StudioManager(root)
            # V1 root layout is intentionally unsupported and must not be
            # moved/rewritten automatically.
            self.assertFalse((root / "instances" / "bot-1").exists())
            self.assertEqual((root / "config.json").read_text(encoding="utf-8"), old_config)
            self.assertEqual((root / "compose.yml").read_text(encoding="utf-8"), old_compose)
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "VNC_PASSWD=oldpass1\n")

            # Current v4 creation remains safe and isolated from the old files.
            created = studio.create_instance("v4")
            self.assertEqual(created["id"], "bot-1")
            self.assertTrue((root / "instances" / "bot-1" / "instance.json").exists())
            self.assertEqual((root / "config.json").read_text(encoding="utf-8"), old_config)
            self.assertNotEqual(studio.get_instance_meta("bot-1")["project_name"], "qqbot-studio")

            reopened = StudioManager(root)
            self.assertEqual([item["id"] for item in reopened.list_instance_metas()], ["bot-1"])
            self.assertEqual((root / "config.json").read_text(encoding="utf-8"), old_config)


if __name__ == "__main__":
    unittest.main()
