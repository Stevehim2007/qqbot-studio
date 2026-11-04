"""Static packaging checks: Dockerfile COPY sources vs the documented context.

No Docker daemon is needed (and none is claimed): every COPY source is resolved
against the build context the README documents, so a wrong prefix fails here
instead of at ``docker build`` time.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "integrations" / "astrbot-memory" / "Dockerfile.memory"
README = REPO_ROOT / "integrations" / "astrbot-memory" / "README.md"
EXPECTED_DOCKERFILE = "integrations/astrbot-memory/Dockerfile.memory"


def copy_sources(text: str) -> list[str]:
    """COPY sources taken from the build context (stage-internal copies skipped)."""

    sources: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        while line.endswith("\\"):
            index += 1
            line = line[:-1].rstrip() + " " + lines[index].strip()
        tokens = line.split()
        if tokens and tokens[0].upper() == "COPY":
            if any(token.startswith("--from=") for token in tokens[1:]):
                index += 1
                continue
            arguments = [token for token in tokens[1:] if not token.startswith("--")]
            sources.extend(arguments[:-1])
        index += 1
    return sources


class DockerfilePackagingTests(unittest.TestCase):
    def setUp(self):
        self.dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.readme = README.read_text(encoding="utf-8")

    def test_every_copy_source_exists_in_the_documented_context(self):
        sources = copy_sources(self.dockerfile)
        self.assertTrue(sources, "Dockerfile.memory 里没有 COPY 源")
        for source in sources:
            self.assertFalse(source.startswith(("http://", "https://")), source)
            self.assertTrue(
                (REPO_ROOT / source).exists(),
                f"COPY 源在仓库根 build context 下不存在：{source}",
            )

    def test_copy_sources_are_repo_root_relative(self):
        for source in copy_sources(self.dockerfile):
            self.assertTrue(
                source.startswith("integrations/astrbot-memory/"),
                f"COPY 源必须是仓库根相对路径：{source}",
            )

    def test_readme_build_command_matches_that_context(self):
        match = re.search(
            r"docker build -f (\S+)\s*\\\s*\n\s*-t (\S+) (\S+)", self.readme
        )
        self.assertIsNotNone(match, "README 缺少 docker build 命令")
        dockerfile, _tag, context = match.groups()
        self.assertEqual(dockerfile, EXPECTED_DOCKERFILE)
        resolved = (REPO_ROOT / context).resolve()
        self.assertEqual(resolved, REPO_ROOT.resolve(), f"build context 必须是仓库根，收到 {context!r}")
        self.assertTrue((REPO_ROOT / dockerfile).is_file())
        for source in copy_sources(self.dockerfile):
            self.assertTrue((REPO_ROOT / source).exists(), source)

    def test_readme_does_not_claim_a_docker_build_was_run(self):
        self.assertIn("未构建", self.readme)


if __name__ == "__main__":
    unittest.main()
