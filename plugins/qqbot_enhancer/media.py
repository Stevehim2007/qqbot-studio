"""Safe media helpers shared by search, collection and sticker services.

No AstrBot imports: callers pass component data/references in, which keeps this
module independently testable.
"""

from __future__ import annotations

import base64
import html as html_lib
import ipaddress
import os
import urllib.parse
from pathlib import Path
from typing import Any

IMAGE_PATH_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".avif",
)
IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}


def detect_image_magic(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"BM"):
        return "image/bmp", ".bmp"
    if len(data) >= 12 and data[4:12] in {b"ftypavif", b"ftypavis"}:
        return "image/avif", ".avif"
    return None


def image_dimensions(data: bytes, mime: str) -> tuple[int, int] | None:
    try:
        if mime == "image/png" and len(data) >= 24:
            if data[12:16] != b"IHDR":
                return None
            return (
                int.from_bytes(data[16:20], "big"),
                int.from_bytes(data[20:24], "big"),
            )
        if mime == "image/gif" and len(data) >= 10:
            return (
                int.from_bytes(data[6:8], "little"),
                int.from_bytes(data[8:10], "little"),
            )
        if mime == "image/bmp" and len(data) >= 26:
            width = int.from_bytes(data[18:22], "little", signed=True)
            height = int.from_bytes(data[22:26], "little", signed=True)
            return abs(width), abs(height)
        if mime == "image/jpeg":
            offset = 2
            sof_markers = {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }
            while offset + 9 < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                marker = data[offset + 1]
                offset += 2
                if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                    continue
                if offset + 2 > len(data):
                    return None
                segment_length = int.from_bytes(data[offset : offset + 2], "big")
                if segment_length < 2:
                    return None
                if marker in sof_markers and offset + 7 <= len(data):
                    height = int.from_bytes(
                        data[offset + 3 : offset + 5],
                        "big",
                    )
                    width = int.from_bytes(
                        data[offset + 5 : offset + 7],
                        "big",
                    )
                    return width, height
                offset += segment_length
            return None
        if mime == "image/webp" and len(data) >= 30:
            chunk_type = data[12:16]
            if chunk_type == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return width, height
            if chunk_type == b"VP8 " and len(data) >= 30:
                width = int.from_bytes(data[26:28], "little") & 0x3FFF
                height = int.from_bytes(data[28:30], "little") & 0x3FFF
                return width, height
        return None
    except (IndexError, ValueError):
        return None


def decode_base64_payload(raw: Any) -> bytes | None:
    if isinstance(raw, bytes):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    encoded = raw.strip()
    if encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[-1]
    encoded = encoded.removeprefix("base64://")
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception:
        try:
            return base64.b64decode(encoded)
        except Exception:
            return None


def is_media_ref(ref: str) -> bool:
    value = ref.strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith(
        ("http://", "https://", "base64://", "data:image/", "file://")
    ):
        return True
    return os.path.exists(value)


def local_image_ref_path(ref: Any) -> Path | None:
    if not isinstance(ref, str) or not ref.strip():
        return None
    value = html_lib.unescape(ref).strip()
    if value.startswith("file://"):
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme != "file":
            return None
        return Path(urllib.parse.unquote(parsed.path))
    try:
        scheme = urllib.parse.urlsplit(value).scheme.lower()
    except ValueError:
        return None
    if scheme in {"http", "https", "data", "base64"}:
        return None
    return Path(value)


def sanitize_http_url(
    value: Any,
    *,
    require_image_extension: bool,
    extensions: tuple[str, ...] = IMAGE_PATH_EXTENSIONS,
) -> str:
    if not isinstance(value, str):
        return ""
    raw = html_lib.unescape(value).strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    if "\\" in raw or any(ord(char) < 32 for char in raw):
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and not (0 < port <= 65535):
        return ""
    host = parsed.hostname.rstrip(".").lower()
    if not host:
        return ""
    if host in {"localhost", "metadata.google.internal"} or host.endswith(
        (".localhost", ".local", ".internal", ".lan", ".home")
    ):
        return ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            return ""
    else:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return ""
    if require_image_extension:
        path = parsed.path.lower()
        if not any(path.endswith(extension) for extension in extensions):
            return ""
    return urllib.parse.urlunsplit(
        (scheme, parsed.netloc, parsed.path, parsed.query, "")
    )
