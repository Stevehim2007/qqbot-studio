import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from qqbot_studio.configgen import (
    choose_ports,
    generate_artifacts,
    md5_password,
)
from qqbot_studio.store import DEFAULT_CONFIG, Store, generate_password


def fake_secrets():
    return {
        "astrbot_username": "admin",
        "astrbot_password": "Abcdef1234567890xyzQ",
        "astrbot_jwt_secret": "j" * 64,
        "onebot_token": "onebot-test-token-abcdefghijklmnopqrstuvwxyz",
        "snowluma_http_token": "http-token-abcdefghijklmnopqrstuvwxyz",
        "snowluma_ws_token": "ws-token-abcdefghijklmnopqrstuvwxyz",
        "vnc_password": "Ab12Cd34",
        "snowluma_password": "",
    }


class StoreTests(unittest.TestCase):
    def test_secrets_are_private_and_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            first = store.ensure_secrets()
            second = store.ensure_secrets()
            self.assertEqual(first["astrbot_password"], second["astrbot_password"])
            self.assertEqual(os.stat(store.secrets_path).st_mode & 0o777, 0o600)
            self.assertTrue(re.search(r"[A-Z]", first["astrbot_password"]))
            self.assertTrue(re.search(r"[a-z]", first["astrbot_password"]))
            self.assertTrue(re.search(r"\d", first["astrbot_password"]))
            self.assertEqual(len(first["vnc_password"]), 8)

    def test_config_never_persists_sensitive_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.save_config(
                {
                    **DEFAULT_CONFIG,
                    "qq_account": "12345678",
                    "deepseek_api_key": "sk-should-not-persist",
                    "nested": {"password": "secret", "api_key": "also-secret", "safe": "yes"},
                }
            )
            raw = store.config_path.read_text(encoding="utf-8")
            self.assertNotIn("sk-should-not-persist", raw)
            self.assertNotIn("also-secret", raw)
            loaded = store.load_config()
            self.assertNotIn("deepseek_api_key", loaded)
            self.assertEqual(loaded["nested"]["safe"], "yes")
            self.assertEqual(os.stat(store.config_path).st_mode & 0o777, 0o600)

    def test_generated_password_meets_astrbot_policy(self):
        password = generate_password(24)
        self.assertGreaterEqual(len(password), 8)
        self.assertRegex(password, r"[A-Z]")
        self.assertRegex(password, r"[a-z]")
        self.assertRegex(password, r"\d")


class ConfigGenerationTests(unittest.TestCase):
    def test_all_generated_files_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_data = fake_secrets()
            ports = choose_ports(probe=False)
            api_key = "sk-test-very-secret-key"
            prompt = "你是测试机器人。"
            result = generate_artifacts(
                tmp,
                api_key=api_key,
                prompt=prompt,
                qq_account="12345678",
                use_mirror=False,
                secrets_data=secrets_data,
                ports=ports,
                host_uid=1000,
                host_gid=1000,
            )
            self.assertNotIn(api_key, json.dumps({k: str(v) for k, v in result.items()}, ensure_ascii=False))

            astrbot = json.loads(result["astrbot_config"].read_text(encoding="utf-8"))
            provider = astrbot["provider"][0]
            self.assertEqual(provider["model"], "deepseek-flash")
            self.assertEqual(provider["api_base"], "https://api.deepseek.com/v1")
            self.assertEqual(provider["key"], [api_key])
            self.assertEqual(astrbot["agent_runner"]["config"]["model"]["provider_id"], "deepseek-flash")
            self.assertEqual(astrbot["agent_runner"]["config"]["persona"]["persona_id"], "qqbot-assistant")
            self.assertEqual(astrbot["platform"][0]["type"], "aiocqhttp")
            self.assertEqual(astrbot["platform"][0]["ws_reverse_port"], 6199)
            self.assertEqual(astrbot["platform"][0]["ws_reverse_token"], secrets_data["onebot_token"])
            self.assertEqual(astrbot["dashboard"]["password"], md5_password(secrets_data["astrbot_password"]))
            self.assertTrue(astrbot["dashboard"]["pbkdf2_password"].startswith("pbkdf2_sha256$"))
            self.assertFalse(astrbot["dashboard"]["password_change_required"])
            self.assertTrue(astrbot["dashboard"]["password_storage_upgraded"])

            snow = json.loads(result["snowluma_config"].read_text(encoding="utf-8"))
            clients = snow["networks"]["wsClients"]
            self.assertEqual(len(clients), 1)
            self.assertEqual(clients[0]["url"], "ws://astrbot:6199/ws")
            self.assertEqual(clients[0]["role"], "Universal")
            self.assertEqual(clients[0]["accessToken"], secrets_data["onebot_token"])
            self.assertEqual(clients[0]["messageFormat"], "array")

            compose = result["compose"].read_text(encoding="utf-8")
            for fragment in ("SYS_PTRACE", "seccomp=unconfined", "shm_size: 1gb", "127.0.0.1:"):
                self.assertIn(fragment, compose)
            self.assertNotIn(api_key, compose)

            env = result["env"].read_text(encoding="utf-8")
            self.assertIn("VNC_PASSWD=", env)
            self.assertNotIn(api_key, env)
            self.assertEqual(os.stat(result["env"]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(result["astrbot_config"]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(result["snowluma_config"]).st_mode & 0o777, 0o600)

    def test_mirror_image_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_artifacts(
                tmp,
                api_key="sk-test",
                prompt="test prompt",
                qq_account="12345678",
                use_mirror=True,
                secrets_data=fake_secrets(),
                ports=choose_ports(probe=False),
                host_uid=1000,
                host_gid=1000,
            )
            env = result["env"].read_text(encoding="utf-8")
            self.assertIn("m.daocloud.io/docker.io/soulter/astrbot:latest", env)
            self.assertIn("m.daocloud.io/docker.io/motricseven7/snowluma:latest", env)
            compose = result["compose"].read_text(encoding="utf-8")
            self.assertIn("${ASTRBOT_IMAGE}", compose)
            self.assertIn("${SNOWLUMA_IMAGE}", compose)


if __name__ == "__main__":
    unittest.main()
