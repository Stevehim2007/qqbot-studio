"""Interface conformance and extracted resource-statistics tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqbot_studio.assistant_ext import AssistantExtensionManager
from qqbot_studio.astrbot_api import AstrBotClient
from qqbot_studio.contracts import (
    AstrBotClient as AstrBotClientContract,
    ConfigStore,
    DeploymentProvider,
    ExtensionManager,
    ResourceStats,
    RuntimeBackend,
    StateProvider,
    UIComponentDescriptor,
    UIComponentProvider,
)
from qqbot_studio.deployer import DeployManager
from qqbot_studio.dockerctl import CommandResult, DockerController, DockerStatus
from qqbot_studio.resource_stats import (
    DefaultResourceStatsProvider,
    directory_usage,
    disk_usage,
    host_memory_info,
    parse_docker_stats_output,
    parse_percent,
    parse_size_bytes,
)
from qqbot_studio.store import Store
from qqbot_studio.studio import StudioManager


class _StaticDeployment:
    """Minimal mock deployment provider used to verify the protocol seam."""

    def get_status(self):
        return {"deployment": {"deployed": False}}

    def start_deploy(self, **kwargs):
        return "job-mock"

    def get_job(self, job_id):
        return {"status": "success", "job_id": job_id}

    def update_persona(self, prompt):
        return "ok"

    def container_action(self, action):
        return "ok"

    def get_logs(self, service, lines=200):
        return ""


class _StaticUIProvider:
    def __init__(self):
        self.items = [
            UIComponentDescriptor(
                component_id="console",
                title="实例控制台",
                route="/console",
                api_prefixes=("/api/instances",),
                capabilities={"refresh": "manual"},
            )
        ]

    def ui_components(self):
        return list(self.items)


class _FakeStatsProvider:
    def __init__(self):
        self.directory_calls = []
        self.host_calls = 0
        self.disk_calls = []
        self.docker_calls = 0
        self.docker_result = {}

    def directory_usage(self, path):
        self.directory_calls.append(Path(path))
        return 4096, 7, []

    def disk_usage(self, path):
        self.disk_calls.append(Path(path))
        return {
            "total_bytes": 100000,
            "used_bytes": 40000,
            "free_bytes": 60000,
            "used_percent": 40.0,
            "error": "",
        }

    def host_memory(self):
        self.host_calls += 1
        return {
            "total_bytes": 8192,
            "available_bytes": 4096,
            "used_bytes": 4096,
            "used_percent": 50.0,
            "source": "mock",
            "error": "",
        }

    def parse_docker_stats(self, stdout):
        self.docker_calls += 1
        return dict(self.docker_result)


class _FakeStatsDocker:
    ASTRBOT_CONTAINER = "qqbot-bot-1-astrbot"
    SNOWLUMA_CONTAINER = "qqbot-bot-1-snowluma"

    def detect(self, force=False):
        return DockerStatus(True, True, True, True)

    def _docker(self, args, timeout=60, extra_env=None):
        return CommandResult(tuple(args), 0, "mocked docker stats", "")


class _FakeStatsManager:
    def __init__(self):
        self.docker = _FakeStatsDocker()


class ResourceStatsModuleTests(unittest.TestCase):
    def test_default_provider_conforms_and_parses_docker_json_and_tabs(self):
        provider = DefaultResourceStatsProvider()
        self.assertIsInstance(provider, ResourceStats)
        self.assertEqual(parse_size_bytes("100MiB"), 100 * 1024 * 1024)
        self.assertEqual(parse_size_bytes("1GiB"), 1024 * 1024 * 1024)
        self.assertEqual(parse_percent("9.77%"), 9.77)
        output = "\n".join(
            [
                json.dumps(
                    {
                        "Name": "qqbot-bot-1-astrbot",
                        "CPUPerc": "1.25%",
                        "MemUsage": "100MiB / 1GiB",
                        "MemPerc": "9.77%",
                    }
                ),
                "qqbot-bot-1-snowluma\t2.50%\t200MiB / 2GiB\t9.77%",
            ]
        )
        parsed = parse_docker_stats_output(output)
        self.assertEqual(parsed["qqbot-bot-1-astrbot"]["cpu_percent"], 1.25)
        self.assertEqual(
            parsed["qqbot-bot-1-astrbot"]["memory_usage_bytes"], 100 * 1024 * 1024
        )
        self.assertEqual(
            parsed["qqbot-bot-1-snowluma"]["memory_limit_bytes"], 2 * 1024 * 1024 * 1024
        )

    def test_directory_usage_skips_symlinks_and_reports_errors_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"a" * 100)
            sub = root / "sub"
            sub.mkdir()
            (sub / "b.bin").write_bytes(b"b" * 200)
            try:
                os.symlink(root / "a.txt", root / "link.txt")
            except (OSError, NotImplementedError):
                pass
            total, files, errors = directory_usage(root)
            self.assertEqual(total, 300)
            self.assertEqual(files, 2)
            self.assertEqual(errors, [])
            disk = disk_usage(root)
            self.assertIn("total_bytes", disk)
            self.assertIn("error", disk)
            memory = host_memory_info()
            for key in ("total_bytes", "available_bytes", "used_bytes", "used_percent", "error"):
                self.assertIn(key, memory)

    def test_studio_manager_uses_injected_resource_stats(self):
        provider = _FakeStatsProvider()
        with tempfile.TemporaryDirectory() as tmp:
            studio = StudioManager(tmp, resource_stats=provider)
            meta = studio.create_instance("统计接口测试")
            provider.docker_result = {
                meta["astrbot_container"]: {
                    "cpu_percent": 3.5,
                    "memory_usage_bytes": 123,
                    "memory_limit_bytes": 1000,
                    "memory_percent": 12.3,
                },
                meta["snowluma_container"]: {
                    "cpu_percent": 1.5,
                    "memory_usage_bytes": 456,
                    "memory_limit_bytes": 2000,
                    "memory_percent": 22.8,
                },
            }
            root_path = Path(tmp)
            instance_path = root_path / "instances" / meta["id"]
            fake_manager = _FakeStatsManager()
            with mock.patch.object(studio, "get_manager", return_value=fake_manager):
                stats = studio.get_stats(meta["id"], force_refresh=True)

        self.assertTrue(stats["ok"])
        self.assertEqual(stats["host_memory"]["source"], "mock")
        self.assertEqual(stats["instances"][0]["data_dir_size_bytes"], 4096)
        self.assertEqual(stats["instances"][0]["file_count"], 7)
        self.assertIn(instance_path, provider.directory_calls)
        self.assertIn(root_path, provider.directory_calls)
        self.assertGreaterEqual(provider.host_calls, 1)
        self.assertGreaterEqual(provider.docker_calls, 1)
        astrbot = next(item for item in stats["docker"] if item["service"] == "astrbot")
        self.assertEqual(astrbot["memory_usage_bytes"], 123)
        self.assertEqual(astrbot["cpu_percent"], 3.5)
        self.assertEqual(stats["global"]["disk"]["used_percent"], 40.0)

    def test_legacy_private_stat_helpers_remain_importable(self):
        import qqbot_studio.studio as studio_module

        self.assertIs(studio_module._directory_usage, directory_usage)
        self.assertTrue(callable(studio_module._host_memory_info))
        self.assertTrue(callable(studio_module._parse_size_bytes))
        self.assertTrue(callable(studio_module._parse_percent))


class ContractConformanceTests(unittest.TestCase):
    def test_existing_concrete_adapters_satisfy_runtime_protocols(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            docker = DockerController(tmp)
            deployment = DeployManager(store=store, docker=docker)
            extension = AssistantExtensionManager(tmp)
            astrbot = AstrBotClient("http://127.0.0.1:1", "admin", "password")

            self.assertIsInstance(store, ConfigStore)
            self.assertIsInstance(store, StateProvider)
            self.assertIsInstance(docker, RuntimeBackend)
            self.assertIsInstance(deployment, DeploymentProvider)
            self.assertIsInstance(extension, ExtensionManager)
            self.assertIsInstance(astrbot, AstrBotClientContract)

    def test_deployment_and_ui_protocol_mocks_are_replaceable(self):
        deployment = _StaticDeployment()
        self.assertIsInstance(deployment, DeploymentProvider)
        self.assertEqual(deployment.start_deploy(qq_account="1"), "job-mock")
        self.assertEqual(deployment.get_job("job-1")["status"], "success")

        provider = _StaticUIProvider()
        self.assertIsInstance(provider, UIComponentProvider)
        descriptor = provider.ui_components()[0]
        self.assertEqual(descriptor.route, "/console")
        payload = descriptor.to_dict()
        self.assertEqual(payload["id"], "console")
        self.assertTrue(payload["requires_token"])
        self.assertEqual(payload["api_prefixes"], ["/api/instances"])


if __name__ == "__main__":
    unittest.main()
