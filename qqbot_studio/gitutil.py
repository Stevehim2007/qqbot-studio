"""Shared non-interactive Git subprocess runner."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Sequence

GitRunner = Callable[
    [Sequence[str], Path, int, Mapping[str, str] | None],
    subprocess.CompletedProcess[str],
]


def run_git(
    argv: Sequence[str],
    cwd: Path,
    timeout: int,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git without terminal/credential prompts and capture its output."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if os.name != "nt":
        env.setdefault("GIT_ASKPASS", "/bin/true")
        env.setdefault(
            "GIT_SSH_COMMAND",
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            "-o ConnectTimeout=15 -o ControlMaster=no -o ControlPath=none",
        )
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return subprocess.run(
        [str(item) for item in argv],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )
