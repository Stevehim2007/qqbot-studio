"""Stdlib-only reader for public web pages behind the safe tool protocol.

Hard rules: HTTP(S) GET only, no cookies/authorization/API keys, public IP
targets only (localhost/private/link-local/metadata and DNS rebinding blocked),
every redirect hop re-validated, bounded time/size/content types, and HTML is
reduced to visible text. No browser, JavaScript, files or commands in here.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

WEB_FETCH_MAX_URL_CHARS = 2048
WEB_FETCH_MAX_CHARS_HARD_LIMIT = 8000
WEB_FETCH_MAX_REDIRECTS_HARD_LIMIT = 5
WEB_FETCH_ALLOWED_SCHEMES = frozenset({"http", "https"})
WEB_FETCH_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
WEB_FETCH_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "text/xml",
        "application/xml",
        "application/json",
    }
)
WEB_FETCH_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)
WEB_FETCH_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".corp",
    ".home.arpa",
)
_HOSTNAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
WEB_FETCH_USER_AGENT = (
    "Mozilla/5.0 (compatible; qqbot_enhancer/4.0; +https://food.dlsz.top)"
)
WEB_FETCH_HEADERS = {
    "User-Agent": WEB_FETCH_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,text/plain,application/json,"
        "application/xml;q=0.9,*/*;q=0.1"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Connection": "close",
}

REASON_TEXT = {
    "invalid_url": "只支持普通 http/https 公开网页链接。",
    "blocked_host": "目标地址不是公开网页。",
    "blocked_ip": "目标地址不是公开网页。",
    "dns_failed": "无法解析该网页域名。",
    "network": "连接网页失败或超时。",
    "too_many_redirects": "网页跳转次数过多。",
    "bad_redirect": "网页跳转到了不安全的地址。",
    "http_error": "网页返回了错误状态。",
    "too_large": "网页内容超过大小限制。",
    "bad_content_type": "网页不是可读取的文本内容。",
    "bad_encoding": "网页压缩格式不受支持。",
}


class WebFetchError(Exception):
    """Failure with a stable reason code; message text stays internal."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def safe_error_message(reason: str) -> str:
    """Chinese user-facing text; never include URLs, paths or exceptions."""

    return REASON_TEXT.get(str(reason or ""), "无法读取该网页。")


def is_public_ip(value: object) -> bool:
    try:
        ip = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global)


def validate_fetch_url(raw: object) -> str:
    """Return a normalized public HTTP(S) URL or raise WebFetchError."""

    value = str(raw or "").strip()
    if not value or len(value) > WEB_FETCH_MAX_URL_CHARS:
        raise WebFetchError("invalid_url")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise WebFetchError("invalid_url")
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise WebFetchError("invalid_url") from exc
    scheme = parts.scheme.lower()
    if scheme not in WEB_FETCH_ALLOWED_SCHEMES:
        raise WebFetchError("invalid_url")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise WebFetchError("invalid_url")
    if parts.username is not None or parts.password is not None:
        raise WebFetchError("invalid_url")
    if port is not None and not 1 <= port <= 65535:
        raise WebFetchError("invalid_url")
    if host in WEB_FETCH_BLOCKED_HOSTS or host.endswith(
        WEB_FETCH_BLOCKED_HOST_SUFFIXES
    ):
        raise WebFetchError("blocked_host")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None:
        if not is_public_ip(ip):
            raise WebFetchError("blocked_ip")
    else:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise WebFetchError("invalid_url") from exc
        if not _HOSTNAME_RE.fullmatch(host):
            raise WebFetchError("invalid_url")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return urllib.parse.urlunsplit(
        (scheme, netloc, parts.path or "/", parts.query, "")
    )


def resolve_public_addresses(host: str, port: int) -> list[str]:
    """Resolve once and require every answer to be a public address."""

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise WebFetchError("dns_failed") from exc
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if not is_public_ip(address):
            raise WebFetchError("blocked_ip")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise WebFetchError("dns_failed")
    return addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Connect to an already-validated IP, so DNS cannot rebind mid-flight."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        pinned_ip: str,
        timeout: float,
    ) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        *,
        pinned_ip: str,
        timeout: float,
    ) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw_sock = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
        )
        try:
            self.sock = self._context.wrap_socket(
                raw_sock,
                server_hostname=self.host,
            )
        except Exception:
            raw_sock.close()
            raise


def is_allowed_content_type(raw: object) -> bool:
    value = str(raw or "").split(";", 1)[0].strip().lower()
    if not value:
        return False
    if value in WEB_FETCH_ALLOWED_CONTENT_TYPES:
        return True
    return value.endswith("+json") or value.endswith("+xml")


_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w.+-]+)", re.IGNORECASE)
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?([\w.+-]+)",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HORIZONTAL_SPACE_RE = re.compile(r"[ \t\xa0\u3000]+")


def decode_body(data: bytes, content_type: str) -> str:
    charset = ""
    match = _CHARSET_RE.search(str(content_type or ""))
    if match:
        charset = match.group(1)
    if not charset:
        match = _META_CHARSET_RE.search(data[:2048])
        if match:
            charset = match.group(1).decode("ascii", "ignore")
    if not charset:
        charset = "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def normalize_text(raw: str) -> str:
    text = _CONTROL_RE.sub("", str(raw or "").replace("\r\n", "\n").replace("\r", "\n"))
    lines = [
        _HORIZONTAL_SPACE_RE.sub(" ", line).strip()
        for line in text.split("\n")
    ]
    joined = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


class _VisibleTextParser(HTMLParser):
    SKIP_TAGS = frozenset(
        {
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "math",
            "iframe",
            "object",
            "embed",
            "canvas",
        }
    )
    BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "form",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "option",
            "p",
            "pre",
            "section",
            "table",
            "tbody",
            "td",
            "tfoot",
            "th",
            "thead",
            "tr",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in self.BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.lower() in self.BLOCK_TAGS and not self._skip_depth:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in self.BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.chunks.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, visible body text) from best-effort HTML parsing."""

    parser = _VisibleTextParser()
    try:
        parser.feed(str(html or ""))
        parser.close()
    except Exception:  # Best-effort extraction; never fail on malformed HTML.
        pass
    title = neutralize_tool_markers(
        normalize_text("".join(parser.title_parts))[:200]
    )
    return title, neutralize_tool_markers(
        normalize_text("".join(parser.chunks))
    )


TOOL_MARKER_NEUTRALIZE_RE = re.compile(r"\[\s*\[")
TOOL_MARKER_NEUTRALIZE_MAX = 2000


def neutralize_tool_markers(text: object) -> str:
    """Break ``[[tool:`` sequences in fetched text so they can never be re-parsed.

    Fetched pages are untrusted input; a page could otherwise smuggle a
    ``[[tool:submit_food_review ...]]`` marker into the reply and have the
    plugin execute it. Only double opening brackets are rewritten (``[ [`).
    """

    value = str(text or "")
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        if count > TOOL_MARKER_NEUTRALIZE_MAX:
            return match.group(0)
        return "[ ["

    return TOOL_MARKER_NEUTRALIZE_RE.sub(_replace, value)


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    limit = max(1, int(max_chars or 1))
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True


@dataclass(slots=True)
class PageResult:
    url: str
    title: str
    text: str
    truncated: bool
    content_type: str


def build_page_result(
    url: str,
    raw_text: str,
    content_type: str,
    *,
    max_chars: int,
) -> PageResult:
    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    if declared in {"text/html", "application/xhtml+xml"}:
        title, body = html_to_text(raw_text)
    else:
        title, body = "", normalize_text(raw_text)
    text, truncated = truncate_text(body, max_chars)
    return PageResult(
        url=url,
        title=title,
        text=text,
        truncated=truncated,
        content_type=declared,
    )


def format_page_text(page: PageResult) -> str:
    header = f"网页：{neutralize_tool_markers(page.title)}" if page.title else (
        f"网页：{page.url}"
    )
    body = neutralize_tool_markers(page.text) or "（网页没有可读正文）"
    parts = [header, "", body]
    if page.truncated:
        parts.extend(["", "（内容已截断）"])
    return "\n".join(parts)


def _request_once(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
) -> tuple[int, str, str, bytes]:
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    addresses = resolve_public_addresses(host, port)
    if scheme == "https":
        conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
            host,
            port,
            pinned_ip=addresses[0],
            timeout=timeout,
        )
    else:
        conn = _PinnedHTTPConnection(
            host,
            port,
            pinned_ip=addresses[0],
            timeout=timeout,
        )
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    try:
        try:
            conn.request("GET", path, headers=WEB_FETCH_HEADERS)
            response = conn.getresponse()
            status = int(response.status)
            if status in WEB_FETCH_REDIRECT_STATUSES:
                location = str(response.headers.get("Location", "") or "").strip()
                return status, location, "", b""
            if status != 200:
                return status, "", "", b""
            raw_type = str(response.headers.get("Content-Type", "") or "")
            if not is_allowed_content_type(raw_type):
                raise WebFetchError("bad_content_type")
            encoding = str(
                response.headers.get("Content-Encoding", "") or ""
            ).strip().lower()
            if encoding not in {"", "identity"}:
                raise WebFetchError("bad_encoding")
            length = str(response.headers.get("Content-Length", "") or "").strip()
            if length:
                try:
                    if int(length) > max_bytes:
                        raise WebFetchError("too_large")
                except ValueError:
                    pass
            data = response.read(max_bytes + 1) or b""
            if len(data) > max_bytes:
                raise WebFetchError("too_large")
            return status, "", raw_type, bytes(data)
        except WebFetchError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise WebFetchError("network") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_page_text(
    url: object,
    *,
    timeout: float = 10,
    max_bytes: int = 262144,
    max_chars: int = 4000,
    max_redirects: int = 3,
) -> PageResult:
    """Fetch one public text page; raise WebFetchError with a reason code."""

    max_chars = max(1, min(int(max_chars or 1), WEB_FETCH_MAX_CHARS_HARD_LIMIT))
    max_bytes = max(1024, int(max_bytes or 1024))
    max_redirects = max(
        0,
        min(int(max_redirects or 0), WEB_FETCH_MAX_REDIRECTS_HARD_LIMIT),
    )
    timeout = max(1.0, float(timeout or 1.0))
    deadline = time.monotonic() + timeout
    current = validate_fetch_url(url)
    for hop in range(max_redirects + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0.1:
            raise WebFetchError("network")
        status, location, content_type, data = _request_once(
            current,
            timeout=min(remaining, timeout),
            max_bytes=max_bytes,
        )
        if status in WEB_FETCH_REDIRECT_STATUSES:
            if hop >= max_redirects:
                raise WebFetchError("too_many_redirects")
            if not location:
                raise WebFetchError("bad_redirect")
            try:
                current = validate_fetch_url(
                    urllib.parse.urljoin(current, location)
                )
            except WebFetchError as exc:
                raise WebFetchError("bad_redirect") from exc
            continue
        if status != 200:
            raise WebFetchError("http_error")
        raw_text = decode_body(data, content_type)
        return build_page_result(
            current,
            raw_text,
            content_type,
            max_chars=max_chars,
        )
    raise WebFetchError("too_many_redirects")
