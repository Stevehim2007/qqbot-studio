"""Owner state v2, CAS revision and owner epoch tests."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from qqbot_studio.plugin_state import (
    DuplicateOwnerError,
    PluginRevisionConflict,
    PluginStateCorruptError,
    PluginStatePathError,
    PluginStateStore,
    StaleOwnerEpochError,
    OwnerStateValidationError,
    validate_owner_state,
)


class OwnerEpochTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.instance = Path(self.tmp.name) / "bot-1"
        self.instance.mkdir()
        self.store = PluginStateStore(self.instance)

    def test_initial_state_is_legacy_and_has_no_invented_final_owner(self):
        snapshot = self.store.snapshot()
        self.assertFalse(snapshot["exists"])
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["revision"], 0)
        self.assertEqual(snapshot["phase"], "legacy")
        self.assertTrue(snapshot["legacy_load_allowed"])
        self.assertEqual(snapshot["legacy_runtime_dependencies"], [])
        self.assertTrue(all(owner == "" for owner in snapshot["owners"].values()))

    def test_claim_owner_bumps_epoch_and_cas_revision(self):
        state = self.store.claim_owner("reply", "qqbot_core_reply", expected_revision=0)
        self.assertEqual(state.revision, 1)
        self.assertEqual(state.owner_for("reply"), "qqbot_core_reply")
        self.assertEqual(state.epoch_for("reply"), 1)
        self.store.assert_epoch("reply", 1)
        with self.assertRaises(StaleOwnerEpochError):
            self.store.assert_epoch("reply", 0)
        with self.assertRaises(StaleOwnerEpochError):
            # Client cannot self-report a future epoch.
            self.store.assert_epoch("reply", 2)

    def test_stale_expected_revision_is_rejected(self):
        self.store.claim_owner("reply", "qqbot_core_reply", expected_revision=0)
        with self.assertRaises(PluginRevisionConflict):
            self.store.claim_owner("marker", "qqbot_safe_tools", expected_revision=0)
        # The conflict must not have partially written the second owner.
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["owners"]["marker"], "")
        self.assertEqual(snapshot["revision"], 1)

    def test_duplicate_new_owner_is_rejected(self):
        self.store.claim_owner("reply", "qqbot_core_reply", expected_revision=0)
        with self.assertRaises(DuplicateOwnerError):
            self.store.claim_owner("marker", "qqbot_core_reply", expected_revision=1)

    def test_concurrent_cas_only_one_writer_wins(self):
        self.store.claim_owner("reply", "qqbot_core_reply", expected_revision=0)
        results: list[str] = []
        barrier = threading.Barrier(2)

        def worker(owner: str) -> None:
            barrier.wait()
            try:
                self.store.claim_owner("food", owner, expected_revision=1)
                results.append("ok")
            except PluginRevisionConflict:
                results.append("conflict")

        threads = [threading.Thread(target=worker, args=(name,)) for name in ("qqbot_food", "qqbot_food_v2")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(results), ["conflict", "ok"])
        self.assertEqual(self.store.snapshot()["revision"], 2)

    def test_final_phase_gate_rejects_v2_claim(self):
        self.store.claim_owner("reply", "qqbot_core_reply", expected_revision=0)
        with self.assertRaises(OwnerStateValidationError):
            self.store.set_phase(
                "final", expected_revision=1, legacy_load_allowed=False
            )

    def test_final_phase_is_accepted_only_when_all_owners_and_epochs_are_committed(self):
        owners = {
            "reply": "qqbot_core_reply",
            "marker": "qqbot_safe_tools",
            "web_reader": "qqbot_web_reader",
            "image_search": "qqbot_image_search",
            "food": "qqbot_food",
            "stickers": "qqbot_stickers",
            "memory_commands": "qqbot_memory_commands",
        }
        state = self.store.apply_owner_plan(
            expected_revision=0,
            owners=owners,
            phase="final",
            legacy_load_allowed=False,
            legacy_runtime_dependencies=[],
        )
        self.assertTrue(state.is_final_ready())
        self.assertEqual(state.phase, "final")
        self.assertTrue(all(state.owner_epochs[key] > 0 for key in state.owners))

    def test_corrupt_state_is_fail_closed_and_not_reset(self):
        self.store.ensure_initialized()
        self.store.state_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(PluginStateCorruptError):
            self.store.load()
        # The corrupt bytes must remain for an operator; load must not overwrite.
        self.assertIn("not json", self.store.state_path.read_text(encoding="utf-8"))

    def test_unknown_fields_and_secret_fields_are_rejected(self):
        self.store.ensure_initialized()
        raw = json.loads(self.store.state_path.read_text(encoding="utf-8"))
        raw["api_key"] = "sk-should-never-be-stored"
        with self.assertRaises(OwnerStateValidationError):
            validate_owner_state(raw)
        raw.pop("api_key")
        raw["unexpected"] = {"x": 1}
        with self.assertRaises(PluginStateCorruptError):
            validate_owner_state(raw)

    def test_path_traversal_and_symlink_state_are_rejected(self):
        with self.assertRaises(PluginStatePathError):
            PluginStateStore(self.instance, state_dir_name="../outside")
        state_dir = self.instance / "studio-plugins"
        state_dir.mkdir()
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            self.store.state_path.symlink_to(outside)
        except (OSError, NotImplementedError):  # pragma: no cover - windows
            self.skipTest("symlink unavailable")
        with self.assertRaises(PluginStatePathError):
            self.store.load()

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_persistence_is_atomic_private_and_no_secret(self):
        state = self.store.claim_owner("reply", "qqbot_core_reply", expected_revision=0)
        mode = stat.S_IMODE(self.store.state_path.stat().st_mode)
        self.assertEqual(mode, 0o600)
        text = self.store.state_path.read_text(encoding="utf-8")
        self.assertNotIn("api_key", text)
        self.assertNotIn("password", text)
        # The serialized file is valid JSON and contains the committed revision.
        self.assertEqual(json.loads(text)["revision"], state.revision)


if __name__ == "__main__":
    unittest.main()
