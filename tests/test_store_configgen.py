import json
import os
import re
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from qqbot_studio.configgen import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    allocate_instance_port_block,
    choose_ports,
    generate_artifacts,
    image_names_for_prefix,
    md5_password,
    mirror_prefixes,
    set_image_names,
)
from qqbot_studio.store import DEFAULT_CONFIG, Store, generate_password

LEGACY_WORDS = ("snowluma", "novnc", "onebot", "vnc")


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
            self.assertIn("onebot_token", first)
            self.assertGreaterEqual(len(first["onebot_token"]), 24)
            for key in (
                "snowluma_http_token",
                "snowluma_ws_token",
                "snowluma_password",
                "vnc_password",
            ):
                self.assertNotIn(key, first)

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
            self.assertNotIn("qq_account", raw)
            loaded = store.load_config()
            self.assertNotIn("deepseek_api_key", loaded)
            self.assertNotIn("qq_account", loaded)
            self.assertEqual(loaded["platform_mode"], "official")
            self.assertEqual(loaded["nested"]["safe"], "yes")
            self.assertEqual(os.stat(store.config_path).st_mode & 0o777, 0o600)

    def test_onebot_preserves_qq_account_and_official_purges_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.save_config({**DEFAULT_CONFIG, "platform_mode": "onebot", "qq_account": "12345678"})
            self.assertEqual(store.load_config()["qq_account"], "12345678")
            store.save_config({**store.load_config(), "platform_mode": "official"})
            self.assertNotIn("qq_account", store.load_config())

    def test_legacy_secrets_are_purged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.save_secrets(fake_secrets())
            secrets = store.ensure_secrets()
            self.assertIn("onebot_token", secrets)
            self.assertEqual(secrets["onebot_token"], "onebot-test-token-abcdefghijklmnopqrstuvwxyz")
            for key in (
                "snowluma_http_token",
                "snowluma_ws_token",
                "snowluma_password",
                "vnc_password",
            ):
                self.assertNotIn(key, secrets)
            self.assertIn("astrbot_username", secrets)
            self.assertIn("qq_official_secret", secrets)

    def test_generated_password_meets_astrbot_policy(self):
        password = generate_password(24)
        self.assertGreaterEqual(len(password), 8)
        self.assertRegex(password, r"[A-Z]")
        self.assertRegex(password, r"[a-z]")
        self.assertRegex(password, r"\d")


class ConfigGenerationTests(unittest.TestCase):

    def test_mirror_prefix_order_and_custom_override(self):
        with mock.patch.dict(
            "os.environ",
            {"QQBOT_IMAGE_MIRROR_PREFIXES": "custom.example/ , docker.1ms.run"},
            clear=False,
        ):
            prefixes = mirror_prefixes()
        self.assertEqual(prefixes[0], "custom.example/")
        self.assertEqual(prefixes[1], "docker.1ms.run/")
        self.assertIn("m.daocloud.io/docker.io/", prefixes)
        image = image_names_for_prefix("docker.1ms.run")
        self.assertEqual(image, "docker.1ms.run/soulter/astrbot:latest")

    def test_update_env_images_preserves_other_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "ASTRBOT_IMAGE=old/astrbot:latest\n"
                "BIND_ADDRESS=127.0.0.1\n",
                encoding="utf-8",
            )
            set_image_names(env_path, "new/astrbot:latest")
            content = env_path.read_text(encoding="utf-8")
            self.assertIn("ASTRBOT_IMAGE=new/astrbot:latest", content)
            self.assertIn("BIND_ADDRESS=127.0.0.1", content)
            self.assertEqual(os.stat(env_path).st_mode & 0o777, 0o600)

    def test_instance_port_blocks_are_single_and_bounded(self):
        first = allocate_instance_port_block(set(), host="127.0.0.1", probe=False)
        second = allocate_instance_port_block(set(first.values()), host="127.0.0.1", probe=False)
        for block in (first, second):
            self.assertEqual(list(block), ["astrbot_webui"])
            self.assertGreaterEqual(block["astrbot_webui"], PORT_RANGE_START)
            self.assertLessEqual(block["astrbot_webui"], PORT_RANGE_END)
        self.assertTrue(set(first.values()).isdisjoint(second.values()))
        self.assertEqual(second["astrbot_webui"], first["astrbot_webui"] + 1)

    def test_official_platform_generates_single_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_artifacts(
                tmp,
                api_key="sk-official-test",
                prompt="官方机器人",
                use_mirror=False,
                secrets_data=fake_secrets(),
                ports=choose_ports(probe=False),
                official_appid="102091266",
                official_secret="official-secret",
            )
            self.assertEqual(result["platform_mode"], "official")
            self.assertNotIn("snowluma_config", result)
            compose = result["compose"].read_text(encoding="utf-8")
            env = result["env"].read_text(encoding="utf-8")
            for text in (compose, env):
                lowered = text.lower()
                for word in LEGACY_WORDS:
                    self.assertNotIn(word, lowered)
            self.assertNotIn("SNOWLUMA_IMAGE", env)
            astrbot = json.loads(result["astrbot_config"].read_text(encoding="utf-8"))
            self.assertEqual(astrbot["platform"][0]["type"], "qq_official")
            self.assertEqual(astrbot["platform"][0]["appid"], "102091266")
            self.assertEqual(astrbot["platform"][0]["secret"], "official-secret")
            self.assertFalse((result["root"] / "data" / "snowluma").exists())
            self.assertEqual(list(result["ports"]), ["astrbot_webui"])

    def test_onebot_platform_generates_single_container_and_ws_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_artifacts(
                tmp,
                api_key="sk-onebot-test",
                prompt="OneBot",
                use_mirror=False,
                secrets_data=fake_secrets(),
                ports=choose_ports(probe=False),
                platform_mode="onebot",
                onebot_ws_port=6201,
                onebot_token="onebot-token-for-test",
            )
            self.assertEqual(result["platform_mode"], "onebot")
            self.assertEqual(result["onebot_ws_port"], 6201)
            compose = result["compose"].read_text(encoding="utf-8")
            env = result["env"].read_text(encoding="utf-8")
            self.assertIn("${BIND_ADDRESS}:${ASTRBOT_ONEBOT_PORT}:6199", compose)
            self.assertIn("ASTRBOT_ONEBOT_PORT=6201", env)
            astrbot = json.loads(result["astrbot_config"].read_text(encoding="utf-8"))
            platform = astrbot["platform"][0]
            self.assertEqual(platform["type"], "aiocqhttp")
            self.assertEqual(platform["ws_reverse_port"], 6199)
            self.assertEqual(platform["ws_reverse_token"], "onebot-token-for-test")
            self.assertNotIn("qq_official", json.dumps(platform))
            self.assertFalse((result["root"] / "data" / "snowluma").exists())

    def test_builtin_onebot_gateway_generates_napcat_service_and_client_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_artifacts(
                tmp,
                api_key="sk-onebot-builtin",
                prompt="内置 OneBot",
                use_mirror=False,
                secrets_data=fake_secrets(),
                ports=choose_ports(probe=False),
                platform_mode="onebot",
                onebot_gateway="builtin",
                qq_account="12345678",
                onebot_token="onebot-token-for-test",
                napcat_webui_port=6202,
                napcat_webui_token="napcat-webui-token",
            )
            self.assertEqual(result["onebot_gateway"], "builtin")
            self.assertEqual(result["qq_account"], "12345678")
            self.assertEqual(result["napcat_webui_port"], 6202)
            compose = result["compose"].read_text(encoding="utf-8")
            env = result["env"].read_text(encoding="utf-8")
            self.assertIn("napcat:", compose)
            self.assertIn("mlikiowa/napcat-docker", env)
            self.assertIn("NAPCAT_WEBUI_PORT=6202", env)
            self.assertIn("NAPCAT_WEBUI_TOKEN=napcat-webui-token", env)
            self.assertIn("NAPCAT_ACCOUNT=12345678", env)
            self.assertIn("ACCOUNT: ${NAPCAT_ACCOUNT}", compose)
            self.assertNotIn("ASTRBOT_ONEBOT_PORT", env)
            document = json.loads(
                (result["root"] / "data" / "napcat" / "config" / "onebot11_12345678.json").read_text(encoding="utf-8")
            )
            client = document["network"]["websocketClients"][0]
            global_doc = json.loads((result["root"] / "data" / "napcat" / "config" / "onebot11.json").read_text(encoding="utf-8"))
            self.assertEqual(global_doc["network"]["websocketClients"][0]["token"], "onebot-token-for-test")
            self.assertEqual(client["url"], "ws://astrbot:6199/ws")
            self.assertEqual(client["token"], "onebot-token-for-test")
            self.assertTrue(client["enable"])
            astrbot = json.loads(result["astrbot_config"].read_text(encoding="utf-8"))
            self.assertEqual(astrbot["platform"][0]["type"], "aiocqhttp")

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
                use_mirror=False,
                secrets_data=secrets_data,
                ports=ports,
            )
            self.assertNotIn(api_key, json.dumps({k: str(v) for k, v in result.items()}, ensure_ascii=False))

            astrbot_text = result["astrbot_config"].read_text(encoding="utf-8")
            astrbot = json.loads(astrbot_text)
            provider = astrbot["provider"][0]
            self.assertEqual(provider["model"], "deepseek-flash")
            self.assertEqual(provider["api_base"], "https://api.deepseek.com/v1")
            self.assertEqual(provider["key"], [api_key])
            self.assertEqual(astrbot["agent_runner"]["config"]["model"]["provider_id"], "deepseek-flash")
            self.assertEqual(astrbot["agent_runner"]["config"]["persona"]["persona_id"], "qqbot-assistant")
            self.assertEqual(astrbot["platform"][0]["type"], "qq_official")
            self.assertEqual(astrbot["dashboard"]["password"], md5_password(secrets_data["astrbot_password"]))
            self.assertTrue(astrbot["dashboard"]["pbkdf2_password"].startswith("pbkdf2_sha256$"))
            self.assertFalse(astrbot["dashboard"]["password_change_required"])
            self.assertTrue(astrbot["dashboard"]["password_storage_upgraded"])
            lowered_config = astrbot_text.lower()
            for word in LEGACY_WORDS:
                self.assertNotIn(word, lowered_config)

            compose = result["compose"].read_text(encoding="utf-8")
            for fragment in ("127.0.0.1:", "${ASTRBOT_WEBUI_PORT}:6185", "container_name:"):
                self.assertIn(fragment, compose)
            self.assertNotIn(api_key, compose)

            env = result["env"].read_text(encoding="utf-8")
            self.assertIn("ASTRBOT_IMAGE=soulter/astrbot:latest", env)
            self.assertIn("ASTRBOT_WEBUI_PORT=", env)
            self.assertNotIn(api_key, env)
            for word in LEGACY_WORDS:
                self.assertNotIn(word, env.lower())
            self.assertEqual(os.stat(result["env"]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(result["astrbot_config"]).st_mode & 0o777, 0o600)

    def test_instance_specific_names_and_remote_bind(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_artifacts(
                tmp,
                api_key="sk-test",
                prompt="prompt",
                secrets_data=fake_secrets(),
                ports=choose_ports(probe=False),
                bind_address="0.0.0.0",
                project_name="qqbot-studio-bot-1",
                astrbot_container="qqbot-bot-1-astrbot",
                network_name="qqbot-studio-bot-1-net",
            )
            compose = result["compose"].read_text(encoding="utf-8")
            self.assertIn("container_name: qqbot-bot-1-astrbot", compose)
            self.assertIn("qqbot-studio-bot-1-net", compose)
            self.assertIn("${BIND_ADDRESS}:${ASTRBOT_WEBUI_PORT}:6185", compose)
            env = result["env"].read_text(encoding="utf-8")
            self.assertIn("BIND_ADDRESS=0.0.0.0", env)
            astrbot_config = json.loads(result["astrbot_config"].read_text(encoding="utf-8"))
            self.assertEqual(astrbot_config["dashboard"]["host"], "0.0.0.0")

    def test_mirror_image_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_artifacts(
                tmp,
                api_key="sk-test",
                prompt="test prompt",
                use_mirror=True,
                secrets_data=fake_secrets(),
                ports=choose_ports(probe=False),
            )
            env = result["env"].read_text(encoding="utf-8")
            self.assertIn("m.daocloud.io/docker.io/soulter/astrbot:latest", env)
            compose = result["compose"].read_text(encoding="utf-8")
            self.assertIn("${ASTRBOT_IMAGE}", compose)
            for word in LEGACY_WORDS:
                self.assertNotIn(word, (env + compose).lower())


if __name__ == "__main__":
    unittest.main()
