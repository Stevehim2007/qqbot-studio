#!/usr/bin/env python3
"""E2E-only entrypoint for QQ Bot Studio.

The real deployment flow performs an external/paid DeepSeek key preflight.  The
E2E smoke test has no real key and must never invent one, so this wrapper stubs
*only* that external preflight.  All Docker/Compose/container behaviour remains
the production code path.

``QQBOT_E2E_EXEC_ASTRBOT=1`` additionally installs a test-only AstrBot API
transport that executes localhost HTTP requests inside the AstrBot container via
``docker exec``.  This is used only on hosts where Docker Desktop publishes
container ports inside its Linux VM but not on the Windows host (WSL
integration disabled).  It does not change application code or shipped defaults.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from qqbot_studio.astrbot_api import AstrBotAPIError, AstrBotClient  # noqa: E402
from qqbot_studio.deployer import DeployManager  # noqa: E402


def _stub_probe(self, api_key: str):  # noqa: ANN001, ANN201
    # Never print or persist the supplied value here.
    return True, "E2E: 已跳过外部 DeepSeek 预检（不调用真实 API）", 200


DeployManager.probe_deepseek_key = _stub_probe  # type: ignore[method-assign]


if os.environ.get("QQBOT_E2E_EXEC_ASTRBOT") == "1":
    class ExecAstrBotClient(AstrBotClient):
        """AstrBot API client that tunnels HTTP through ``docker exec``."""

        def __init__(self, container: str, base_url: str, username: str, password: str) -> None:
            super().__init__(base_url, username, password)
            self.container = container

        def _request(
            self,
            method: str,
            path: str,
            *,
            payload: dict[str, Any] | None = None,
            token: str | None = None,
            timeout: float | None = None,
        ) -> dict[str, Any]:
            spec = {
                "method": method,
                "path": path,
                "payload": payload,
                "token": token,
                "timeout": float(timeout or self.timeout),
            }
            env = os.environ.copy()
            env["QB_E2E_ASTRBOT_REQUEST"] = json.dumps(spec, ensure_ascii=False)
            docker = shutil.which("docker")
            if not docker:
                raise AstrBotAPIError("E2E exec bridge：找不到 docker 命令")
            inner = r'''
import json, os, urllib.error, urllib.request
spec = json.loads(os.environ["QB_E2E_ASTRBOT_REQUEST"])
url = "http://127.0.0.1:6185" + spec["path"]
headers = {"Accept": "application/json", "User-Agent": "QQBotStudio-E2E/1.0"}
data = None
if spec.get("payload") is not None:
    data = json.dumps(spec["payload"], ensure_ascii=False).encode("utf-8")
    headers["Content-Type"] = "application/json"
if spec.get("token"):
    headers["Authorization"] = "Bearer " + spec["token"]
request = urllib.request.Request(url, data=data, headers=headers, method=spec["method"])
try:
    with urllib.request.urlopen(request, timeout=spec.get("timeout") or 20) as response:
        status = int(getattr(response, "status", 200))
        body = response.read().decode("utf-8", "replace")
except urllib.error.HTTPError as exc:
    status = int(exc.code)
    body = exc.read().decode("utf-8", "replace")
print(json.dumps({"status": status, "body": body}, ensure_ascii=False))
'''
            try:
                completed = subprocess.run(
                    [docker, "exec", "-i", "-e", "QB_E2E_ASTRBOT_REQUEST", self.container, "python", "-"],
                    input=inner,
                    text=True,
                    capture_output=True,
                    timeout=float(timeout or self.timeout) + 20,
                    env=env,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise AstrBotAPIError(f"E2E exec bridge 失败：{exc}") from exc
            if completed.returncode != 0:
                detail = (completed.stdout + "\n" + completed.stderr).strip()
                raise AstrBotAPIError(f"E2E exec bridge 容器命令失败：{detail[-500:]}")
            try:
                envelope = json.loads(completed.stdout.strip().splitlines()[-1])
                status = int(envelope.get("status") or 0)
                body = str(envelope.get("body") or "")
                parsed = json.loads(body) if body.strip() else {}
            except (ValueError, IndexError, TypeError) as exc:
                raise AstrBotAPIError("E2E exec bridge 返回格式异常") from exc
            if not isinstance(parsed, dict):
                raise AstrBotAPIError("E2E exec bridge：AstrBot 返回格式异常")
            if parsed.get("status") == "error":
                message = str(parsed.get("message") or "AstrBot 操作失败")
                raise AstrBotAPIError(message, status_code=status or 400)
            return parsed

    _original_init = DeployManager.__init__

    def _patched_init(self, *args, **kwargs):  # noqa: ANN001, ANN201
        _original_init(self, *args, **kwargs)
        container = str(getattr(self, "astrbot_container", ""))

        def factory(base_url: str, username: str, password: str) -> ExecAstrBotClient:
            return ExecAstrBotClient(container, base_url, username, password)

        self._api_client_factory = factory

    DeployManager.__init__ = _patched_init  # type: ignore[method-assign]


from qqbot_studio.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
