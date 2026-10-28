"""Minimal AstrBot Dashboard API client used for persona bootstrapping."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class AstrBotAPIError(RuntimeError):
    """AstrBot returned an error or could not be reached."""

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class AstrBotClient:
    """Talk to a local AstrBot instance without third-party dependencies."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._token = ""
        # Local dashboard calls must bypass HTTP(S)_PROXY inherited from the
        # shell; a proxy would commonly refuse loopback addresses.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ------------------------------------------------------------------
    # HTTP primitives
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "QQBotStudio/1.0",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
                status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            message = self._error_message(body) or f"AstrBot 返回 HTTP {exc.code}"
            raise AstrBotAPIError(message, status_code=int(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise AstrBotAPIError(f"无法连接 AstrBot：{reason}") from exc

        try:
            parsed = json.loads(body) if body.strip() else {}
        except ValueError as exc:
            raise AstrBotAPIError("AstrBot 返回了无法解析的 JSON") from exc
        if not isinstance(parsed, dict):
            raise AstrBotAPIError("AstrBot 返回格式异常")
        if parsed.get("status") == "error":
            message = str(parsed.get("message") or "AstrBot 操作失败")
            raise AstrBotAPIError(message, status_code=status or 400)
        return parsed

    @staticmethod
    def _error_message(body: str) -> str:
        try:
            parsed = json.loads(body)
        except ValueError:
            return body.strip()[:500]
        if isinstance(parsed, dict):
            for key in ("message", "error", "detail"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    # ------------------------------------------------------------------
    # Lifecycle and authentication
    # ------------------------------------------------------------------
    def wait_until_ready(self, timeout: float = 180.0, *, poll_interval: float = 2.0) -> None:
        """Wait until the AstrBot dashboard API is accepting requests."""

        deadline = time.monotonic() + max(10.0, timeout)
        last_error = ""
        while time.monotonic() < deadline:
            for path in ("/api/auth/setup-status", "/api/v1/auth/setup-status"):
                try:
                    self._request("GET", path, timeout=4)
                    return
                except AstrBotAPIError as exc:
                    last_error = str(exc)
                    if exc.status_code and exc.status_code < 500 and exc.status_code not in (401, 403):
                        # A 404 means the server is up but this endpoint version
                        # differs; try the fallback path and otherwise continue.
                        continue
            time.sleep(poll_interval)
        raise AstrBotAPIError(
            "等待 AstrBot WebUI 启动超时。请查看 AstrBot 日志，确认端口未被占用且容器仍在运行。"
            + (f" 最后错误：{last_error}" if last_error else "")
        )

    def login(self, *, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        last_error: AstrBotAPIError | None = None
        for path in ("/api/v1/auth/login", "/api/auth/login"):
            try:
                response = self._request(
                    "POST",
                    path,
                    payload={"username": self.username, "password": self.password},
                    timeout=25,
                )
            except AstrBotAPIError as exc:
                last_error = exc
                # Fall back to the legacy endpoint only when the v1 route is
                # unavailable.  Authentication failures are not retried across
                # endpoint prefixes.
                if exc.status_code in (401, 403) or "密码" in str(exc) or "password" in str(exc).lower():
                    break
                continue
            data = response.get("data")
            token = data.get("token") if isinstance(data, dict) else None
            if isinstance(token, str) and token:
                self._token = token
                return token
            last_error = AstrBotAPIError("AstrBot 登录响应中没有 token")
        if last_error:
            raise AstrBotAPIError(f"AstrBot 登录失败：{last_error}", status_code=last_error.status_code)
        raise AstrBotAPIError("AstrBot 登录失败：没有可用的登录接口")

    def upsert_persona(self, persona_id: str, system_prompt: str) -> None:
        """Create or update an AstrBot persona with the given system prompt."""

        if not persona_id or not system_prompt.strip():
            raise ValueError("人格 ID 和提示词不能为空")
        token = self.login()
        create_payload = {
            "persona_id": persona_id,
            "system_prompt": system_prompt,
            "begin_dialogs": [],
            "tools": None,
            "skills": None,
        }
        try:
            self._request("POST", "/api/v1/personas", payload=create_payload, token=token)
            return
        except AstrBotAPIError as exc:
            if exc.status_code == 404:
                # Older AstrBot release: use the legacy dashboard endpoint.
                self._create_legacy_persona(create_payload, token)
                return
            if exc.status_code not in (400, 409):
                raise
        # Persona already exists (or the create endpoint rejects duplicates).
        self._update_persona(persona_id, system_prompt, token)

    def _create_legacy_persona(self, payload: dict[str, Any], token: str) -> None:
        try:
            self._request("POST", "/api/persona/create", payload=payload, token=token)
            return
        except AstrBotAPIError as exc:
            if "exist" not in str(exc).lower() and exc.status_code not in (400, 409):
                raise
        self._update_persona(str(payload["persona_id"]), str(payload["system_prompt"]), token)

    def _update_persona(self, persona_id: str, system_prompt: str, token: str) -> None:
        payload = {"persona_id": persona_id, "system_prompt": system_prompt}
        first_error: AstrBotAPIError | None = None
        try:
            self._request("PUT", "/api/v1/personas/by-id", payload=payload, token=token)
            return
        except AstrBotAPIError as exc:
            if exc.status_code in (401, 403):
                raise
            first_error = exc
        # Older dashboards expose the same operation under /api/persona/update.
        try:
            self._request("POST", "/api/persona/update", payload=payload, token=token)
        except AstrBotAPIError:
            if first_error is not None:
                raise first_error
            raise

    def test_connection(self) -> dict[str, Any]:
        """Return setup status, raising a readable error when unreachable."""

        return self._request("GET", "/api/auth/setup-status", timeout=10)
