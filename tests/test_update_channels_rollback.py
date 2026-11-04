"""Update channels, same-major rollback and history (real Git repositories)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from qqbot_studio import updater
from qqbot_studio.gitutil import run_git
from qqbot_studio.updater import (
    UpdateError,
    apply_update,
    list_versions,
    load_history,
    rollback,
    update_status,
)

GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

RELEASES = (
    ("v3.12.0", "3.12.0", 2),
    ("v4.0.0", "4.0.0", 2),
    ("v4.0.4", "4.0.4", 2),
    ("v4.1.0", "4.1.0", 2),
    ("v4.1.1", "4.1.1", 2),
    ("v4.2.0", "4.2.0", 3),
)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


class ReleaseRepoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        self.data = self.base / "data"
        self.data.mkdir()
        for tag, version, schema in RELEASES:
            self._add_release(tag, version, schema)

    def tearDown(self):
        self.tmp.cleanup()

    def _add_release(self, tag: str, version: str, schema: int) -> None:
        package = self.root / "qqbot_studio"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        (package / "store.py").write_text(f"CONFIG_SCHEMA_VERSION = {schema}\n", encoding="utf-8")
        (package / "studio.py").write_text(f"STUDIO_SCHEMA_VERSION = {schema}\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text(f"# {tag}\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", f"release {version}")
        git(self.root, "tag", tag)

    def head_commit(self) -> str:
        return git(self.root, "rev-parse", "HEAD").stdout.strip()

    def tag_commit(self, tag: str) -> str:
        return git(self.root, "rev-parse", f"refs/tags/{tag}").stdout.strip()

    def history(self) -> list[dict]:
        return load_history(self.data)


class UpdateChannelTests(ReleaseRepoTestCase):
    def test_lists_every_formal_tag_and_flags_major(self):
        result = list_versions("stable", root=self.root, data_dir=self.data)
        self.assertTrue(result["ok"])
        self.assertEqual(result["current_major"], 4)
        tags = [item["tag"] for item in result["versions"]]
        self.assertEqual(
            tags, ["v4.2.0", "v4.1.1", "v4.1.0", "v4.0.4", "v4.0.0", "v3.12.0"]
        )
        flags = {item["tag"]: item["same_major"] for item in result["versions"]}
        self.assertTrue(flags["v4.0.0"])
        self.assertFalse(flags["v3.12.0"])
        current = result["versions"][0]
        self.assertTrue(current["current"])
        self.assertEqual(current["version"], "4.2.0")
        self.assertTrue(current["date"])
        self.assertEqual(current["commit"], self.tag_commit("v4.2.0")[:7])

    def test_non_formal_tags_are_not_selectable(self):
        git(self.root, "tag", "v4.1.1-backup-20260101")
        git(self.root, "tag", "nightly")
        result = list_versions("stable", root=self.root, data_dir=self.data)
        tags = {item["tag"] for item in result["versions"]}
        self.assertNotIn("v4.1.1-backup-20260101", tags)
        self.assertNotIn("nightly", tags)
        with self.assertRaises(UpdateError):
            rollback("v4.1.1-backup-20260101", root=self.root, data_dir=self.data, restart=False)

    def test_rollback_accepts_any_same_major_minor(self):
        result = rollback("v4.0.4", root=self.root, data_dir=self.data, restart=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["channel"], "rollback")
        self.assertEqual(self.head_commit(), self.tag_commit("v4.0.4"))
        status = update_status(root=self.root, data_dir=self.data)
        self.assertEqual(status["current"]["tag"], "v4.0.4")
        self.assertEqual(status["current"]["version"], "4.0.4")
        self.assertEqual(status["current"]["major"], 4)
        self.assertFalse(status["dirty"])
        self.assertFalse(status["busy"])
        record = self.history()[0]
        self.assertEqual(record["action"], "rollback")
        self.assertEqual(record["channel"], "rollback")
        self.assertEqual(record["from"]["tag"], "v4.2.0")
        self.assertEqual(record["to"]["tag"], "v4.0.4")
        self.assertEqual(record["result"], "success")
        self.assertTrue(record["at"])
        self.assertTrue(Path(record["backup"]).is_dir())

    def test_second_rollback_uses_new_current_version(self):
        rollback("v4.1.0", root=self.root, data_dir=self.data, restart=False)
        rollback("v4.0.0", root=self.root, data_dir=self.data, restart=False)
        self.assertEqual(self.head_commit(), self.tag_commit("v4.0.0"))
        records = self.history()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["from"]["tag"], "v4.1.0")
        self.assertEqual(records[1]["from"]["tag"], "v4.2.0")

    def test_cross_major_rollback_is_refused(self):
        before = self.head_commit()
        with self.assertRaises(UpdateError) as caught:
            rollback("v3.12.0", root=self.root, data_dir=self.data, restart=False)
        self.assertIn("跨大版本", str(caught.exception))
        self.assertEqual(self.head_commit(), before)
        self.assertEqual(self.history(), [])

    def test_arbitrary_refs_commits_and_urls_are_rejected(self):
        before = self.head_commit()
        for target in (
            "main",
            "origin/main",
            self.tag_commit("v4.0.4")[:7],
            "https://github.com/example/repo",
            "refs/tags/v4.0.4",
            "v4.0.4^{}",
            "v4.0.4; rm -rf /",
            "../v4.0.4",
            "",
        ):
            with self.assertRaises(UpdateError, msg=target):
                rollback(target, root=self.root, data_dir=self.data, restart=False)
        self.assertEqual(self.head_commit(), before)

    def test_missing_same_major_tag_is_reported(self):
        with self.assertRaises(UpdateError):
            rollback("v4.9.9", root=self.root, data_dir=self.data, restart=False)
        self.assertEqual(self.head_commit(), self.tag_commit("v4.2.0"))

    def test_dirty_worktree_blocks_rollback(self):
        target = self.root / "qqbot_studio" / "__init__.py"
        target.write_text(target.read_text(encoding="utf-8") + "# local edit\n", encoding="utf-8")
        with self.assertRaises(UpdateError) as caught:
            rollback("v4.0.4", root=self.root, data_dir=self.data, restart=False)
        self.assertIn("未提交", str(caught.exception))
        self.assertEqual(self.head_commit(), self.tag_commit("v4.2.0"))
        self.assertFalse(updater.lock_active(self.data))

    def test_concurrent_update_and_rollback_are_refused(self):
        (self.data / "update.lock").write_text("pid=1\n", encoding="utf-8")
        with self.assertRaises(UpdateError) as caught:
            rollback("v4.0.4", root=self.root, data_dir=self.data, restart=False)
        self.assertIn("正在运行", str(caught.exception))
        with self.assertRaises(UpdateError):
            apply_update("stable", root=self.root, data_dir=self.data, restart=False)
        self.assertTrue(list_versions("stable", root=self.root, data_dir=self.data)["ok"])

    def test_stale_lock_does_not_block_forever(self):
        lock = self.data / "update.lock"
        lock.write_text("pid=1\n", encoding="utf-8")
        old = 1
        os.utime(lock, (old, old))
        result = rollback("v4.0.4", root=self.root, data_dir=self.data, restart=False)
        self.assertTrue(result["ok"])
        self.assertFalse(lock.exists())


class RollbackSafetyTests(ReleaseRepoTestCase):
    def _seed_data(self) -> dict[str, bytes]:
        (self.data / "studio.json").write_text(
            json.dumps({"schema_version": 2, "bind_port": 8765}), encoding="utf-8"
        )
        instance = self.data / "instances" / "bot-1"
        instance.mkdir(parents=True)
        (instance / "config.json").write_text(
            json.dumps({"schema_version": 2, "qq_account": "123"}), encoding="utf-8"
        )
        (instance / "secrets.json").write_text('{"api_key": "sk-secret-value"}', encoding="utf-8")
        (instance / "instance.json").write_text('{"id": "bot-1"}', encoding="utf-8")
        snapshot = {}
        for path in sorted(self.data.rglob("*")):
            if path.is_file():
                snapshot[str(path.relative_to(self.data))] = path.read_bytes()
        return snapshot

    def test_backup_contains_code_and_instance_config_and_data_is_untouched(self):
        snapshot = self._seed_data()
        result = rollback("v4.0.4", root=self.root, data_dir=self.data, restart=False)
        backup = Path(result["backup"])
        self.assertTrue((backup / "code.bundle").is_file())
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("data/studio.json", manifest["files"])
        self.assertIn("data/instances/bot-1/secrets.json", manifest["files"])
        self.assertIn("data/instances/bot-1/config.json", manifest["files"])
        self.assertGreater(manifest["instance_data"]["bot-1"]["files"], 0)
        self.assertGreater(manifest["instance_data"]["bot-1"]["bytes"], 0)
        for relative, content in snapshot.items():
            self.assertEqual((self.data / relative).read_bytes(), content, relative)
        # Switching code never writes the application data directory.
        self.assertFalse((self.data / "data" / "astrbot").exists())

    def test_failed_switch_restores_original_ref_and_sanitizes_history(self):
        self._seed_data()
        original = self.head_commit()

        def failing_runner(argv, cwd, timeout, extra_env=None):
            if "checkout" in argv and "v4.0.4" in argv:
                return subprocess.CompletedProcess(
                    list(argv),
                    1,
                    "",
                    "fatal: simulated failure token=abcd1234secret https://user:pass@host/x",
                )
            return run_git(argv, cwd, timeout, extra_env)

        with self.assertRaises(UpdateError) as caught:
            rollback(
                "v4.0.4",
                root=self.root,
                data_dir=self.data,
                restart=False,
                runner=failing_runner,
            )
        self.assertIn("已恢复原版本", str(caught.exception))
        self.assertEqual(self.head_commit(), original)
        self.assertEqual(git(self.root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        record = self.history()[0]
        self.assertEqual(record["result"], "restored")
        self.assertNotIn("abcd1234secret", record["message"])
        self.assertNotIn("user:pass", record["message"])
        self.assertTrue(Path(record["backup"]).is_dir())
        self.assertFalse(updater.lock_active(self.data))

    def test_schema_downgrade_requires_explicit_confirmation(self):
        (self.data / "studio.json").write_text(
            json.dumps({"schema_version": 3}), encoding="utf-8"
        )
        before = self.head_commit()
        with self.assertRaises(UpdateError) as caught:
            rollback("v4.0.4", root=self.root, data_dir=self.data, restart=False)
        self.assertIn("schema", str(caught.exception))
        self.assertEqual(self.head_commit(), before)
        result = rollback(
            "v4.0.4",
            root=self.root,
            data_dir=self.data,
            restart=False,
            allow_schema_downgrade=True,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.head_commit(), self.tag_commit("v4.0.4"))

    def test_history_is_capped_and_newest_first(self):
        for tag in ("v4.0.0", "v4.0.4", "v4.1.0"):
            rollback(tag, root=self.root, data_dir=self.data, restart=False)
        records = self.history()
        self.assertEqual([record["to"]["tag"] for record in records], ["v4.1.0", "v4.0.4", "v4.0.0"])
        for record in records:
            for key in ("at", "action", "channel", "from", "to", "result"):
                self.assertIn(key, record)


class ChannelApplyTests(ReleaseRepoTestCase):
    def test_test_channel_switches_allowlisted_branch(self):
        git(self.root, "branch", "feat/plugin-repository-4.3")
        result = list_versions("test", root=self.root, data_dir=self.data)
        self.assertTrue(result["ok"])
        refs = [item["ref"] for item in result["versions"]]
        self.assertIn("origin/feat/plugin-repository-4.3", refs)
        applied = apply_update(
            "test",
            root=self.root,
            data_dir=self.data,
            test_ref="feat/plugin-repository-4.3",
            restart=False,
        )
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["channel"], "test")
        self.assertTrue(updater._head_info(self.root, None)["detached"])
        record = self.history()[0]
        self.assertEqual(record["channel"], "test")

    def test_test_channel_rejects_arbitrary_refs(self):
        for ref in ("main", "origin/main", "https://example.com/repo", "refs/heads/main", "v4.0.4"):
            with self.assertRaises(UpdateError, msg=ref):
                apply_update(
                    "test",
                    root=self.root,
                    data_dir=self.data,
                    test_ref=ref,
                    restart=False,
                )
        with self.assertRaises(UpdateError):
            apply_update("nightly", root=self.root, data_dir=self.data, restart=False)

    def test_stable_channel_fast_forwards_to_origin_main(self):
        git(self.root, "checkout", "-q", "-b", "next")
        (self.root / "NEXT.md").write_text("next\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "next release")
        next_commit = self.head_commit()
        git(self.root, "checkout", "-q", "main")
        git(self.root, "branch", "-D", "next")
        git(self.root, "update-ref", "refs/remotes/origin/main", next_commit)
        result = apply_update("stable", root=self.root, data_dir=self.data, restart=False)
        self.assertTrue(result["ok"])
        self.assertTrue(result["restarting"] is False)
        self.assertEqual(self.head_commit(), next_commit)
        self.assertEqual(self.history()[0]["channel"], "stable")
        self.assertEqual(self.history()[0]["result"], "success")

    def test_stable_channel_refuses_diverged_history(self):
        first_commit = git(self.root, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
        git(self.root, "update-ref", "refs/remotes/origin/main", first_commit)
        with self.assertRaises(UpdateError) as caught:
            apply_update("stable", root=self.root, data_dir=self.data, restart=False)
        self.assertIn("快进", str(caught.exception))
        self.assertEqual(self.head_commit(), self.tag_commit("v4.2.0"))


if __name__ == "__main__":
    unittest.main()
