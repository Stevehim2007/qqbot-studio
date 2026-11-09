from __future__ import annotations

import stat
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update.sh"
AUTO = ROOT / "scripts" / "auto-update.sh"


class ReleaseUpdateScriptContractTests(unittest.TestCase):
    def test_hook_is_executable_and_has_no_network_downloads(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        text = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("curl ", "wget ", "git clone", "http://", "https://"):
            self.assertNotIn(forbidden, text)
        self.assertIn("set -Eeuo pipefail", text)
        self.assertIn("QQBOT_UPDATE_DATA_DIR", text)

    def test_auto_update_bootstrap_path_runs_hook_once(self):
        text = AUTO.read_text(encoding="utf-8")
        self.assertIn("QQBOT_UPDATE_SCRIPT_ALREADY_RAN", text)
        self.assertIn("scripts/update.sh", text)
        self.assertIn("timeout 600", text)
