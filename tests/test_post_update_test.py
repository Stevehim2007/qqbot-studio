"""Static contract: updates run verification before restarting the server."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PostUpdateTestContract(unittest.TestCase):
    def test_update_script_has_fail_closed_post_test_gate(self):
        text = (ROOT / "scripts" / "auto-update.sh").read_text(encoding="utf-8")
        for token in (
            "scripts/post-update-test.sh",
            "QQBOT_STUDIO_AUTO_TEST",
            "post-update tests failed",
            "old server left running",
        ):
            self.assertIn(token, text)

    def test_post_update_script_runs_repo_and_patch_checks(self):
        text = (ROOT / "scripts" / "post-update-test.sh").read_text(encoding="utf-8")
        for token in (
            "post-update-verification.md",
            "manual acceptance checklist",
            "verify-patch.sh",
            "ASTRBOT_SRC_TGZ",
            "node --check qqbot_studio/static/console.js",
            "post-update tests PASS",
        ):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
