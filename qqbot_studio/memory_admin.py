"""Studio-side administration of AstrBot's native long-term memory database.

The memory patch (``integrations/astrbot-memory``) keeps one SQLite sidecar at
``data/astrbot_memory.db`` inside the AstrBot data directory.  This module is
the Studio-side owner of *content management* only: it never edits the core
patch and it never changes the memory policy.

Safety rules (all enforced here, not in the HTTP layer):

* every operation carries an explicit ``(scope_type, scope_id)``; there is no
  "all scopes" query, so a response can never contain another scope's rows;
* read queries use the SQLite read-only URI; writes use one transaction;
* secrets (API keys / passwords / QQ passwords) are rejected on write and
  scrubbed again on read, export included;
* the database path is never returned to the client; error text is masked;
* backups are written atomically with mode 0600 into a private directory.

The host SQLite file is owned by the AstrBot container in a Docker install, so
a permission-denied host read/write falls back to the same statement batch
executed by a tiny read/write runner inside the container (``docker exec``,
fixed argv, no shell).  Both channels execute the exact same statements.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCOPE_TYPES = ("user", "group_member", "group", "conversation", "persona", "global")
"""Stored scope types; ``group_user``/``speaker`` are policy names, not rows."""

KINDS = ("fact", "preference", "identity", "profile", "habit", "note")
DEFAULT_KIND = "fact"
DB_FILE_NAME = "astrbot_memory.db"
CONTAINER_DATA_DIR = "/AstrBot/data"
CONTAINER_DB_PATH = CONTAINER_DATA_DIR + "/" + DB_FILE_NAME

MAX_CONTENT_CHARS = 200
MAX_KEY_CHARS = 64
MAX_SCOPE_ID_CHARS = 96
MAX_SEARCH_CHARS = 64
MAX_LIST_LIMIT = 50
DEFAULT_LIST_LIMIT = 20
MAX_SCOPE_ROWS = 500  # mirrors MemoryStore scope cap
MAX_IMPORT_RECORDS = 500
MAX_EXPORT_ROWS = 5000
DEFAULT_BUSY_TIMEOUT = 8.0

# Mirrors ``astrbot/core/memory/text.py``.  Kept local because Studio must not
# import the patched AstrBot package (it is not installed on the host); the
# behaviours that matter here (key normalisation, fingerprint, FTS tokens,
# secret scrubbing and duplicate similarity) are covered by tests.
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN = re.compile(r"[a-z0-9][a-z0-9_\-.]*")
_STRIP_PARTICLES = re.compile(r"(?:的|了|啊|呀|吧|吗|呢|哦|喔|啦|哈)+$")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "am", "was", "were", "be", "been",
        "i", "me", "my", "you", "your", "we", "our", "it", "its", "this",
        "that", "to", "of", "in", "on", "at", "for", "and", "or", "but",
        "我", "你", "他", "她", "它", "我们", "你们", "他们", "的", "了",
        "是", "在", "和", "就", "都", "也", "很", "有", "没有", "吗", "呢",
        "啊", "呀", "吧", "哦", "这", "那", "一个", "什么", "怎么", "可以",
    }
)

_EN_CREDENTIAL_NOUN = (
    r"(?:password|passwd|pwd|passphrase|passcode"
    r"|api[\s_-]?key|access[\s_-]?key|secret[\s_-]?key|private[\s_-]?key"
    r"|client[\s_-]?secret|auth[\s_-]?token|access[\s_-]?token"
    r"|refresh[\s_-]?token|bearer[\s_-]?token|token|credentials?)"
)
_EN_COPULA = r"(?:is|are|was|were|will\s+be|has\s+been|have\s+been|=|:)"

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\b(?:ak|as|ghp|gho|xox[baprs])[-_][A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\b(?:bearer\s+)[A-Za-z0-9._\-]{12,}\b"),
    re.compile(r"(?i)\b(?:password|passwd|pwd|api[_-]?key|secret|token)\s*[:=]\s*\S{1,60}"),
    re.compile(r"(?:密码|口令|密钥|令牌|token)\s*(?:是|为|:|：)\s*\S{1,60}"),
    re.compile(r"(?i)\b(?:qq|微信|wechat)\s*(?:密码|password)\s*(?:是|为|:|：)?\s*\S+"),
    re.compile(r"\bQQ[:：]?\s*\d{5,12}\s*[/,，]?\s*(?:密码|口令)\s*[:：]?\s*\S+"),
    re.compile(rf"(?i)\b{_EN_CREDENTIAL_NOUN}\s*{_EN_COPULA}\s*\S{{1,60}}"),
    re.compile(rf"(?i)\b{_EN_CREDENTIAL_NOUN}\s+for\s+[^\n]{{1,40}}?\s+{_EN_COPULA}\s*\S{{1,60}}"),
    re.compile(r"(?i)\bpin\s*(?:is|=|:)\s*\d{4,12}\b"),
)
REDACTED = "[已隐去]"
_NEGATION_MARKERS = ("不", "没", "别", "讨厌", "don't", "doesn't", "not ", "hate")
_SCOPE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:@|+\-]{1,%d}$" % MAX_SCOPE_ID_CHARS)


# ----------------------------------------------------------------------
# text helpers (mirror the core memory package)
# ----------------------------------------------------------------------
def tokenize(text: str, *, limit: int = 64) -> list[str]:
    """Latin words plus CJK character bigrams, mirroring the core tokenizer."""

    if not text:
        return []
    lowered = str(text).lower()
    tokens: list[str] = [word for word in _LATIN.findall(lowered) if word not in STOPWORDS]
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if len(token) == 1 and not _CJK.match(token):
            continue
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
        if len(ordered) >= limit:
            break
    return ordered


def normalise_value(value: str, *, limit: int = 32) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.strip("，,。.；;：:！!？?、\"'“”‘’()（）[]【】")
    text = _STRIP_PARTICLES.sub("", text)
    return text.lower()[:limit]


def fingerprint(text: str) -> str:
    import hashlib

    return hashlib.sha1(str(text or "").strip().lower().encode("utf-8")).hexdigest()[:24]


def similarity(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 1.0 if normalise_value(left) == normalise_value(right) else 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def contains_secret(text: str) -> bool:
    return any(pattern.search(str(text or "")) for pattern in SECRET_PATTERNS)


def scrub_secrets(text: str) -> str:
    cleaned = str(text or "")
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    return cleaned


def is_contradiction(left: str, right: str) -> bool:
    left_negative = any(marker in str(left or "") for marker in _NEGATION_MARKERS)
    right_negative = any(marker in str(right or "") for marker in _NEGATION_MARKERS)
    return left_negative != right_negative


def _negation_stem(text: str) -> str:
    stem = str(text or "")
    for marker in _NEGATION_MARKERS:
        stem = stem.replace(marker, "")
    return normalise_value(stem, limit=64)


def _iso(epoch: float | None) -> str:
    if epoch is None:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


# ----------------------------------------------------------------------
# errors
# ----------------------------------------------------------------------
class MemoryAdminError(ValueError):
    """Validation or state error; the HTTP layer maps it to a 4xx."""


class MemoryAdminUnavailable(MemoryAdminError):
    """The database cannot be reached (missing/permission/corrupt)."""

    def __init__(
        self,
        message: str,
        *,
        permission_denied: bool = False,
        empty: bool = False,
        corrupt: bool = False,
        actions: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.permission_denied = bool(permission_denied)
        self.empty = bool(empty)
        self.corrupt = bool(corrupt)
        self.actions = list(actions)


class MemoryRecordNotFound(MemoryAdminError):
    """Record id does not exist inside the requested scope."""


class MemoryConflict(MemoryAdminError):
    """A uniqueness rule (scope/key or scope/fingerprint) would be violated."""


# ----------------------------------------------------------------------
# validation helpers
# ----------------------------------------------------------------------
def validate_scope(scope_type: Any, scope_id: Any) -> tuple[str, str]:
    kind = str(scope_type or "").strip().lower()
    if kind not in SCOPE_TYPES:
        raise MemoryAdminError(
            "scope_type 必须是 %s 之一（当前值：%r）。" % ("/".join(SCOPE_TYPES), scope_type)
        )
    value = str(scope_id or "").strip()
    if not value:
        raise MemoryAdminError("scope_id 不能为空：所有记忆操作都必须显式限定一个 scope。")
    # The core stores platform-prefixed ids (aiocqhttp:1056821876).  Accept the
    # bare group/user number in the admin UI so a user does not have to know
    # the prefix, while keeping platform-qualified ids untouched.
    if kind in ("group", "user") and value.isdigit():
        value = "aiocqhttp:" + value
    elif kind == "group_member" and re.fullmatch(r"\d{4,20}:\d{4,20}", value):
        value = "aiocqhttp:" + value
    if len(value) > MAX_SCOPE_ID_CHARS or not _SCOPE_ID_PATTERN.match(value):
        raise MemoryAdminError(
            "scope_id 非法：只允许字母、数字和 _.:@|+-，最长 %d 个字符。" % MAX_SCOPE_ID_CHARS
        )
    return kind, value


def validate_record_id(raw: Any) -> int:
    text = str(raw if raw is not None else "").strip()
    if not text.isdigit():
        raise MemoryAdminError("记录 id 必须为正整数。")
    value = int(text)
    if value <= 0:
        raise MemoryAdminError("记录 id 必须为正整数。")
    return value


def _validate_search(text: Any) -> str:
    value = str(text or "").strip()
    if len(value) > MAX_SEARCH_CHARS:
        raise MemoryAdminError("搜索关键词过长（最多 %d 个字符）。" % MAX_SEARCH_CHARS)
    if any(ord(char) < 32 for char in value):
        raise MemoryAdminError("搜索关键词不能包含控制字符。")
    return value


def _clamp_int(raw: Any, default: int, minimum: int, maximum: int, field: str) -> int:
    try:
        value = int(raw) if raw is not None and str(raw).strip() != "" else default
    except (TypeError, ValueError):
        raise MemoryAdminError("%s 必须是整数。" % field) from None
    if value < minimum or value > maximum:
        raise MemoryAdminError("%s 超出允许范围（%d-%d）。" % (field, minimum, maximum))
    return value


def _validate_content(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise MemoryAdminError("记忆内容不能为空。")
    if len(text) > MAX_CONTENT_CHARS:
        raise MemoryAdminError("记忆内容过长（最多 %d 个字符）。" % MAX_CONTENT_CHARS)
    if "\x00" in text or any(ord(char) < 9 for char in text):
        raise MemoryAdminError("记忆内容包含非法控制字符。")
    if contains_secret(text):
        raise MemoryAdminError("记忆内容疑似包含密钥/密码，已拒绝写入。请先移除敏感信息。")
    return text


def _parse_epoch(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _import_payload(raw: Mapping[str, Any], now: float) -> dict[str, Any]:
    """Extract writable fields from an exported/imported record object."""

    payload: dict[str, Any] = {}
    key = raw.get("mem_key", raw.get("key"))
    if key is not None:
        payload["mem_key"] = key
    if "content" in raw:
        payload["content"] = raw["content"]
    for field in ("kind", "importance", "confidence"):
        if field in raw:
            payload[field] = raw[field]
    created = _parse_epoch(raw.get("created_at_epoch", raw.get("created_at")))
    if created is not None and 0 < created <= now + 60:
        payload["created_at"] = created
    expires = _parse_epoch(raw.get("expires_at_epoch", raw.get("expires_at")))
    if expires is not None and expires > now:
        payload["expires_at"] = expires
    # An already-expired row is restored without TTL instead of failing the
    # whole file; the preview reports it via "expiry_dropped".
    return payload


def _validate_key(value: Any, content: str) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_KEY_CHARS:
        raise MemoryAdminError("记忆键过长（最多 %d 个字符）。" % MAX_KEY_CHARS)
    if contains_secret(text):
        raise MemoryAdminError("记忆键疑似包含密钥/密码，已拒绝写入。")
    normalised = normalise_value(scrub_secrets(text))
    return normalised or fingerprint(content)


def _validate_kind(value: Any, default: str = DEFAULT_KIND) -> str:
    kind = str(value or "").strip().lower() or default
    if kind not in KINDS:
        raise MemoryAdminError("kind 必须是 %s 之一。" % "/".join(KINDS))
    return kind


def _validate_unit_float(value: Any, field: str, default: float | None = None) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MemoryAdminError("%s 必须是 0 到 1 之间的数字。" % field) from None
    if not 0.0 <= number <= 1.0:
        raise MemoryAdminError("%s 必须在 0 到 1 之间。" % field)
    return number


def _resolve_expiry(payload: Mapping[str, Any], now: float) -> tuple[bool, float | None]:
    """Return ``(provided, expires_at)`` from ``expires_at`` / ``ttl_seconds``."""

    expires_provided = "expires_at" in payload
    ttl_provided = "ttl_seconds" in payload
    if expires_provided and ttl_provided:
        raise MemoryAdminError("expires_at 与 ttl_seconds 不能同时设置。")
    if ttl_provided:
        raw = payload.get("ttl_seconds")
        if raw is None or str(raw).strip() == "":
            return True, None
        try:
            ttl = int(raw)
        except (TypeError, ValueError):
            raise MemoryAdminError("ttl_seconds 必须是整数秒。") from None
        if ttl <= 0:
            raise MemoryAdminError("ttl_seconds 必须大于 0；清除过期时间请使用 expires_at=null。")
        if ttl > 366 * 86400:
            raise MemoryAdminError("ttl_seconds 过大（最多 366 天）。")
        return True, now + ttl
    if expires_provided:
        raw = payload.get("expires_at")
        if raw is None or str(raw).strip() == "":
            return True, None
        try:
            expires_at = float(raw)
        except (TypeError, ValueError):
            raise MemoryAdminError("expires_at 必须是 Unix 时间戳（秒）。") from None
        if expires_at <= now:
            raise MemoryAdminError("expires_at 必须晚于当前时间；清除过期时间请传 null。")
        return True, expires_at
    return False, None


# ----------------------------------------------------------------------
# container runner (same statements as the host channel)
# ----------------------------------------------------------------------
_CONTAINER_RUNNER = (
    "import base64,json,sqlite3,sys\n"
    "db=sys.argv[1]\n"
    "if not db.startswith('/AstrBot/data/') or '..' in db:\n"
    "    raise SystemExit('MEMADMIN bad database path')\n"
    "payload=json.loads(base64.b64decode(sys.argv[2]).decode('utf-8'))\n"
    "write=bool(payload.get('write'))\n"
    "con=sqlite3.connect('file:%s?mode=%s'%(db,'rw' if write else 'ro'),uri=True,timeout=8)\n"
    "con.execute('PRAGMA busy_timeout=8000')\n"
    "out=[]\n"
    "try:\n"
    "    if write:\n"
    "        con.execute('BEGIN IMMEDIATE')\n"
    "    for stmt in payload.get('statements') or []:\n"
    "        sql=str(stmt.get('sql') or '').strip()\n"
    "        low=sql.lower()\n"
    "        if not sql or ';' in sql or low.split(' ')[0] not in ('select','insert','update','delete'):\n"
    "            raise SystemExit('MEMADMIN statement rejected')\n"
    "        if not write and not low.startswith('select'):\n"
    "            raise SystemExit('MEMADMIN readonly connection')\n"
    "        cur=con.execute(sql, stmt.get('params') or [])\n"
    "        if cur.description:\n"
    "            cols=[d[0] for d in cur.description]\n"
    "            out.append([dict(zip(cols,row)) for row in cur.fetchall()])\n"
    "        else:\n"
    "            out.append({'rowcount':cur.rowcount})\n"
    "    if write:\n"
    "        con.commit()\n"
    "except BaseException:\n"
    "    if write:\n"
    "        try:\n"
    "            con.rollback()\n"
    "        except Exception:\n"
    "            pass\n"
    "    raise\n"
    "finally:\n"
    "    con.close()\n"
    "print('MEMADMIN:'+json.dumps(out,ensure_ascii=False))\n"
)

_ALLOWED_FIRST_WORDS = ("select", "insert", "update", "delete")
_PERMISSION_HINTS = (
    "readonly database",
    "unable to open database file",
    "attempt to write a readonly",
    "permission denied",
    "operation not permitted",
)
_LOCKED_HINTS = ("database is locked", "database table is locked")

_ROW_COLUMNS = (
    "id, scope_type, scope_id, mem_key, content, kind, importance, confidence,"
    " hits, source, fingerprint, created_at, updated_at, last_used_at, expires_at"
)


class MemoryAdmin:
    """Scope-scoped CRUD/import/export/reflect over the memory sidecar."""

    def __init__(
        self,
        astrbot_data_dir: Path | str,
        *,
        docker: Any = None,
        container_name: str = "",
        container_db_path: str = CONTAINER_DB_PATH,
        backup_dir: Path | str | None = None,
        clock: Any = None,
        busy_timeout: float = DEFAULT_BUSY_TIMEOUT,
    ) -> None:
        self.astrbot_data_dir = Path(astrbot_data_dir)
        self.db_path = self.astrbot_data_dir / DB_FILE_NAME
        self.docker = docker
        self.container_name = str(container_name or "")
        self.container_db_path = str(container_db_path or CONTAINER_DB_PATH)
        self.backup_dir = Path(backup_dir) if backup_dir else self.astrbot_data_dir.parent / "memory-backups"
        self.clock = clock or time.time
        self.busy_timeout = float(busy_timeout)
        self.channel = "host"  # switches to "container" after a permission error
        self._schema: tuple[bool, bool] | None = None
        self._container_ready: bool | None = None

    # -- public surface -------------------------------------------------
    def stats(self, scope_type: Any, scope_id: Any) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        now = float(self.clock())
        if not self._has_table():
            return self._empty_payload(
                scope, "记忆数据库尚未初始化（AstrBot 还没有写入任何记忆）。"
            ) | {
                "stats": {
                    "total": 0,
                    "expired": 0,
                    "hits": 0,
                    "kinds": {},
                    "last_updated": "",
                    "last_updated_epoch": None,
                    "cap": MAX_SCOPE_ROWS,
                }
            }
        rows = self._rows(
            [
                {
                    "sql": (
                        "SELECT COUNT(*) AS total, COALESCE(SUM(hits),0) AS hits_total,"
                        " MAX(updated_at) AS last_updated,"
                        " SUM(CASE WHEN expires_at IS NOT NULL AND expires_at <= ? THEN 1 ELSE 0 END)"
                        " AS expired FROM astrbot_memory WHERE scope_type=? AND scope_id=?"
                    ),
                    "params": [now, scope["scope_type"], scope["scope_id"]],
                },
                {
                    "sql": (
                        "SELECT kind, COUNT(*) AS count FROM astrbot_memory"
                        " WHERE scope_type=? AND scope_id=? GROUP BY kind ORDER BY count DESC"
                    ),
                    "params": [scope["scope_type"], scope["scope_id"]],
                },
            ]
        )
        summary = rows[0][0] if rows and rows[0] else {}
        kinds = {
            str(item.get("kind") or ""): int(item.get("count") or 0)
            for item in (rows[1] if len(rows) > 1 else [])
        }
        total = int(summary.get("total") or 0)
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "writable": self._writable(),
            "read_channel": self.channel,
            "stats": {
                "total": total,
                "expired": int(summary.get("expired") or 0),
                "hits": int(summary.get("hits_total") or 0),
                "kinds": kinds,
                "last_updated": _iso(summary.get("last_updated")),
                "last_updated_epoch": summary.get("last_updated"),
                "cap": MAX_SCOPE_ROWS,
            },
            "message": "" if total else "该 scope 还没有记忆。",
        }

    def list_records(
        self,
        scope_type: Any,
        scope_id: Any,
        *,
        query: Any = "",
        limit: Any = DEFAULT_LIST_LIMIT,
        offset: Any = 0,
    ) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        search = _validate_search(query)
        page_size = _clamp_int(limit, DEFAULT_LIST_LIMIT, 1, MAX_LIST_LIMIT, "limit")
        start = _clamp_int(offset, 0, 0, 1_000_000, "offset")
        if not self._has_table():
            return self._empty_payload(
                scope, "记忆数据库尚未初始化（AstrBot 还没有写入任何记忆）。", records=[]
            ) | {
                "total": 0,
                "limit": page_size,
                "offset": start,
                "has_more": False,
                "query": search,
            }
        where = " WHERE scope_type=? AND scope_id=?"
        params: list[Any] = [scope["scope_type"], scope["scope_id"]]
        if search:
            like = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where += " AND (content LIKE ? ESCAPE '\\' OR mem_key LIKE ? ESCAPE '\\')"
            params.extend([like, like])
        rows = self._rows(
            [
                {"sql": "SELECT COUNT(*) AS total FROM astrbot_memory" + where, "params": params},
                {
                    "sql": "SELECT " + _ROW_COLUMNS + " FROM astrbot_memory" + where
                    + " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                    "params": [*params, page_size, start],
                },
            ]
        )
        total = int(rows[0][0].get("total") or 0) if rows and rows[0] else 0
        records = [self._present(row) for row in (rows[1] if len(rows) > 1 else [])]
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "writable": self._writable(),
            "read_channel": self.channel,
            "records": records,
            "total": total,
            "limit": page_size,
            "offset": start,
            "has_more": start + len(records) < total,
            "query": search,
            "message": "" if total else "该 scope 还没有记忆。",
        }

    def get_record(self, scope_type: Any, scope_id: Any, record_id: Any) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        identifier = validate_record_id(record_id)
        if not self._has_table():
            raise MemoryRecordNotFound("记录不存在：该 scope 还没有任何记忆。")
        rows = self._rows(
            [
                {
                    "sql": "SELECT " + _ROW_COLUMNS + " FROM astrbot_memory"
                    " WHERE id=? AND scope_type=? AND scope_id=?",
                    "params": [identifier, scope["scope_type"], scope["scope_id"]],
                }
            ]
        )
        if not rows or not rows[0]:
            raise MemoryRecordNotFound("记录不存在，或不属于当前 scope。")
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "writable": self._writable(),
            "record": self._present(rows[0][0]),
        }

    def create_record(self, scope_type: Any, scope_id: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        if not self._has_table():
            raise MemoryAdminUnavailable(
                "记忆数据库尚未初始化，无法新增。请先在聊天中产生一条记忆（或启用长期记忆后重启 AstrBot）。",
                empty=True,
            )
        data = self._normalise_record(payload, now=float(self.clock()), existing=None)
        dedup = self._dedup_mode(payload)
        existing = self._find_key_conflict(scope, data["mem_key"])
        if existing is not None:
            if dedup == "skip":
                return {
                    "ok": True,
                    "available": True,
                    "scope": scope,
                    "action": "skipped",
                    "record": self._present(existing),
                    "message": "已存在同一记忆键的记录，按“跳过”策略未写入。",
                }
            return self.update_record(
                scope["scope_type"], scope["scope_id"], existing["id"], payload
            ) | {"action": "updated"}
        same_fingerprint = self._find_fingerprint(scope, data["fingerprint"])
        if same_fingerprint is not None:
            # Core formation skips cross-key identical content; "overwrite" only
            # replaces a record with the same semantic key.
            return {
                "ok": True,
                "available": True,
                "scope": scope,
                "action": "skipped",
                "record": self._present(same_fingerprint),
                "message": "同一 scope 内已有内容完全相同的记忆，未重复写入。",
            }
        if self._scope_count(scope) >= MAX_SCOPE_ROWS:
            raise MemoryConflict(
                "该 scope 已达到 %d 条上限；请先删除/整理旧记忆再新增。" % MAX_SCOPE_ROWS
            )
        now = float(self.clock())
        statements = [
            {
                "sql": (
                    "INSERT INTO astrbot_memory (scope_type, scope_id, mem_key, content, kind,"
                    " importance, confidence, hits, source, fingerprint, created_at, updated_at,"
                    " expires_at) VALUES (?,?,?,?,?,?,?,0,'studio',?,?,?,?)"
                ),
                "params": [
                    scope["scope_type"],
                    scope["scope_id"],
                    data["mem_key"],
                    data["content"],
                    data["kind"],
                    data["importance"],
                    data["confidence"],
                    data["fingerprint"],
                    now,
                    now,
                    data["expires_at"],
                ],
            }
        ]
        if self._has_fts():
            statements.append(self._fts_row_by_key(scope, data["mem_key"], data["content"]))
        self._write(statements)
        created = self._find_key_conflict(scope, data["mem_key"])
        if created is None:  # pragma: no cover - defensive
            raise MemoryConflict("新增后未能读回记录，请刷新后确认。")
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "action": "created",
            "record": self._present(created),
            "message": "已新增 1 条记忆。",
        }

    def update_record(
        self,
        scope_type: Any,
        scope_id: Any,
        record_id: Any,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        identifier = validate_record_id(record_id)
        existing = self._require_record(scope, identifier)
        current = self._normalise_record(payload, now=float(self.clock()), existing=dict(existing), partial=True)
        if current["mem_key"] != existing["mem_key"]:
            conflict = self._find_key_conflict(scope, current["mem_key"])
            if conflict is not None and int(conflict["id"]) != identifier:
                raise MemoryConflict("同一 scope 内已存在相同记忆键，键名冲突。")
        statements = [
            {
                "sql": (
                    "UPDATE astrbot_memory SET mem_key=?, content=?, kind=?, importance=?,"
                    " confidence=?, fingerprint=?, updated_at=?, expires_at=?"
                    " WHERE id=? AND scope_type=? AND scope_id=?"
                ),
                "params": [
                    current["mem_key"],
                    current["content"],
                    current["kind"],
                    current["importance"],
                    current["confidence"],
                    current["fingerprint"],
                    float(self.clock()),
                    current["expires_at"],
                    identifier,
                    scope["scope_type"],
                    scope["scope_id"],
                ],
            }
        ]
        if self._has_fts():
            statements.append(
                {"sql": "DELETE FROM astrbot_memory_fts WHERE rowid=?", "params": [identifier]}
            )
            statements.append(
                {
                    "sql": "INSERT INTO astrbot_memory_fts (rowid, mem_key, content, tokens) VALUES (?,?,?,?)",
                    "params": [
                        identifier,
                        current["mem_key"],
                        current["content"],
                        " ".join(tokenize(current["mem_key"] + " " + current["content"])),
                    ],
                }
            )
        self._write(statements)
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "action": "updated",
            "record": self.get_record(scope["scope_type"], scope["scope_id"], identifier)["record"],
            "message": "已保存修改。",
        }

    def delete_record(
        self, scope_type: Any, scope_id: Any, record_id: Any, *, confirm: Any = ""
    ) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        identifier = validate_record_id(record_id)
        self._require_confirmation(confirm, str(identifier), "删除单条记忆")
        existing = self._require_record(scope, identifier)
        statements = []
        if self._has_fts():
            statements.append(
                {"sql": "DELETE FROM astrbot_memory_fts WHERE rowid=?", "params": [identifier]}
            )
        statements.append(
            {
                "sql": "DELETE FROM astrbot_memory WHERE id=? AND scope_type=? AND scope_id=?",
                "params": [identifier, scope["scope_type"], scope["scope_id"]],
            }
        )
        self._write(statements)
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "deleted": identifier,
            "record": self._present(existing),
            "message": "已删除 1 条记忆。",
        }

    def forget(self, scope_type: Any, scope_id: Any, *, confirm: Any = "") -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        self._require_confirmation(confirm, scope["scope_id"], "忘记整个 scope")
        if not self._has_table():
            return {
                "ok": True,
                "available": True,
                "scope": scope,
                "deleted": 0,
                "message": "该 scope 没有记忆，无需清理。",
            }
        statements = []
        if self._has_fts():
            statements.append(
                {
                    "sql": (
                        "DELETE FROM astrbot_memory_fts WHERE rowid IN"
                        " (SELECT id FROM astrbot_memory WHERE scope_type=? AND scope_id=?)"
                    ),
                    "params": [scope["scope_type"], scope["scope_id"]],
                }
            )
        statements.append(
            {
                "sql": "DELETE FROM astrbot_memory WHERE scope_type=? AND scope_id=?",
                "params": [scope["scope_type"], scope["scope_id"]],
            }
        )
        results = self._write(statements)
        deleted = int(results["rowcounts"][-1] or 0)
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "deleted": deleted,
            "message": "已忘记该 scope 的 %d 条记忆。" % deleted,
        }

    def export_scope(self, scope_type: Any, scope_id: Any) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        now = float(self.clock())
        if not self._has_table():
            return self._empty_payload(scope, "记忆数据库尚未初始化，导出为空。", records=[]) | {
                "schema_version": 1,
                "exported_at": _iso(now),
                "count": 0,
            }
        rows = self._rows(
            [
                {
                    "sql": "SELECT " + _ROW_COLUMNS + " FROM astrbot_memory"
                    " WHERE scope_type=? AND scope_id=? ORDER BY id ASC LIMIT ?",
                    "params": [scope["scope_type"], scope["scope_id"], MAX_EXPORT_ROWS],
                }
            ]
        )
        records = [self._present(row) for row in (rows[0] if rows else [])]
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "schema_version": 1,
            "exported_at": _iso(now),
            "count": len(records),
            "records": records,
            "message": "" if records else "该 scope 没有可导出的记忆。",
        }

    def import_records(
        self,
        scope_type: Any,
        scope_id: Any,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        apply_changes = bool(payload.get("apply"))
        dedup = self._dedup_mode(payload)
        skip_invalid = bool(payload.get("skip_invalid"))
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raise MemoryAdminError("records 必须是数组。")
        if not raw_records:
            raise MemoryAdminError("records 不能为空。")
        if len(raw_records) > MAX_IMPORT_RECORDS:
            raise MemoryAdminError("一次最多导入 %d 条记忆。" % MAX_IMPORT_RECORDS)
        now = float(self.clock())
        existing_rows = self._scope_rows(scope) if self._has_table() else []
        by_key = {str(row["mem_key"]): row for row in existing_rows}
        by_fingerprint = {str(row["fingerprint"]): row for row in existing_rows}
        valid: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        near_duplicates: list[dict[str, Any]] = []
        seen_keys: dict[str, int] = {}
        would_create = would_update = would_skip = 0
        expiry_dropped: list[int] = []
        for index, raw in enumerate(raw_records):
            if not isinstance(raw, Mapping):
                invalid.append({"index": index, "reason": "记录必须是 JSON 对象。"})
                continue
            payload_fields = _import_payload(raw, now)
            expires_raw = _parse_epoch(raw.get("expires_at_epoch", raw.get("expires_at")))
            if "expires_at" not in payload_fields and expires_raw is not None and expires_raw <= now:
                expiry_dropped.append(index)
            try:
                data = self._normalise_record(payload_fields, now=now, existing=None)
            except MemoryAdminError as exc:
                invalid.append({"index": index, "reason": str(exc)})
                continue
            key = data["mem_key"]
            if key in seen_keys:
                invalid.append(
                    {"index": index, "reason": "文件内重复记忆键（与第 %d 条相同）。" % seen_keys[key]}
                )
                continue
            seen_keys[key] = index
            duplicate = by_key.get(key)
            same_content = by_fingerprint.get(data["fingerprint"])
            if duplicate is not None:
                conflicts.append(
                    {
                        "index": index,
                        "key": key,
                        "kind": "key",
                        "existing_id": int(duplicate["id"]),
                        "existing_content": scrub_secrets(str(duplicate["content"]))[:120],
                    }
                )
                if dedup == "skip":
                    would_skip += 1
                    continue
                would_update += 1
                valid.append({"index": index, "data": data, "update_id": int(duplicate["id"])})
                continue
            if same_content is not None:
                conflicts.append(
                    {
                        "index": index,
                        "key": key,
                        "kind": "fingerprint",
                        "existing_id": int(same_content["id"]),
                        "existing_content": scrub_secrets(str(same_content["content"]))[:120],
                    }
                )
                would_skip += 1
                continue
            for row in existing_rows:
                if similarity(str(row["content"]), data["content"]) >= 0.92:
                    near_duplicates.append(
                        {"index": index, "key": key, "existing_id": int(row["id"])}
                    )
                    break
            would_create += 1
            valid.append({"index": index, "data": data, "update_id": None})
        would_total = len(existing_rows) + sum(1 for item in valid if item["update_id"] is None)
        preview = {
            "total": len(raw_records),
            "valid": len(valid),
            "invalid": invalid,
            "conflicts": conflicts,
            "near_duplicates": near_duplicates,
            "would_create": would_create,
            "would_update": would_update,
            "would_skip": would_skip,
            "expiry_dropped": expiry_dropped,
            "exceeds_scope_cap": would_total > MAX_SCOPE_ROWS,
            "cap": MAX_SCOPE_ROWS,
            "dedup": dedup,
        }
        base = {
            "ok": True,
            "available": True,
            "scope": scope,
            "apply": apply_changes,
            "preview": preview,
        }
        if not apply_changes:
            return base | {"message": "导入预览已生成，尚未写入任何数据。"}
        if not self._has_table():
            raise MemoryAdminUnavailable(
                "记忆数据库尚未初始化，无法导入。请先在聊天中产生一条记忆（或启用长期记忆后重启 AstrBot）。",
                empty=True,
            )
        if invalid and not skip_invalid:
            raise MemoryAdminError(
                "导入文件包含 %d 条非法记录；修正后重试，或显式传 skip_invalid=true 跳过。" % len(invalid)
            )
        if would_total > MAX_SCOPE_ROWS:
            raise MemoryConflict(
                "导入后该 scope 将达到 %d 条，超过 %d 条上限；请先清理或拆分导入。"
                % (would_total, MAX_SCOPE_ROWS)
            )
        self._require_confirmation(payload.get("confirm"), scope["scope_id"], "导入记忆")
        if not valid:
            return base | {
                "apply": False,
                "result": {"created": 0, "updated": 0, "skipped": len(raw_records)},
                "message": "没有需要写入的记录（全部被跳过）。",
            }
        backup_name = self._write_backup(scope, existing_rows, reason="import")
        statements: list[dict[str, Any]] = []
        created = updated = 0
        for item in valid:
            data = item["data"]
            if item["update_id"] is not None:
                statements.append(
                    {
                        "sql": (
                            "UPDATE astrbot_memory SET content=?, kind=?, importance=?, confidence=?,"
                            " source='import', fingerprint=?, updated_at=?, expires_at=?"
                            " WHERE id=? AND scope_type=? AND scope_id=?"
                        ),
                        "params": [
                            data["content"],
                            data["kind"],
                            data["importance"],
                            data["confidence"],
                            data["fingerprint"],
                            now,
                            data["expires_at"],
                            item["update_id"],
                            scope["scope_type"],
                            scope["scope_id"],
                        ],
                    }
                )
                if self._has_fts():
                    statements.append(
                        {
                            "sql": "DELETE FROM astrbot_memory_fts WHERE rowid=?",
                            "params": [item["update_id"]],
                        }
                    )
                    statements.append(self._fts_row(item["update_id"], data["mem_key"], data["content"]))
                updated += 1
            else:
                statements.append(
                    {
                        "sql": (
                            "INSERT INTO astrbot_memory (scope_type, scope_id, mem_key, content, kind,"
                            " importance, confidence, hits, source, fingerprint, created_at, updated_at,"
                            " expires_at) VALUES (?,?,?,?,?,?,?,0,'import',?,?,?,?)"
                        ),
                        "params": [
                            scope["scope_type"],
                            scope["scope_id"],
                            data["mem_key"],
                            data["content"],
                            data["kind"],
                            data["importance"],
                            data["confidence"],
                            data["fingerprint"],
                            data["created_at"],
                            now,
                            data["expires_at"],
                        ],
                    }
                )
                if self._has_fts():
                    statements.append(self._fts_row_by_key(scope, data["mem_key"], data["content"]))
                created += 1
        self._write(statements)
        return {
            "ok": True,
            "available": True,
            "scope": scope,
            "apply": True,
            "preview": preview,
            "backup": backup_name,
            "result": {"created": created, "updated": updated, "skipped": len(raw_records) - len(valid)},
            "message": "已导入 %d 条、更新 %d 条记忆（单事务提交）。" % (created, updated),
        }

    def reflect(
        self,
        scope_type: Any,
        scope_id: Any,
        *,
        apply_changes: bool = False,
        confirm: Any = "",
    ) -> dict[str, Any]:
        """Deterministic, explainable reflection preview/apply for one scope.

        Mirrors the core plan semantics: near-duplicates are merge candidates,
        contradictions are reported but never deleted, expired/low-value rows
        are previewed.  ``apply`` only removes duplicate extras and expired
        rows, after writing a 0600 backup of the scope.
        """

        scope = self._scope(scope_type, scope_id)
        now = float(self.clock())
        if not self._has_table():
            return {
                "ok": True,
                "available": True,
                "scope": scope,
                "apply": False,
                "source": "studio",
                "preview": {
                    "duplicates": [],
                    "contradictions": [],
                    "expired": [],
                    "low_value": [],
                    "counts": {"records": 0, "duplicates": 0, "contradictions": 0, "expired": 0, "low_value": 0},
                },
                "message": "该 scope 还没有记忆，无需整理。",
            }
        rows = [dict(row) for row in self._scope_rows(scope)]
        rows.sort(key=lambda item: (-float(item["confidence"]), -float(item["updated_at"]), -int(item["id"])))
        clusters: list[list[dict[str, Any]]] = []
        for row in rows:
            placed = False
            for cluster in clusters:
                if similarity(str(cluster[0]["content"]), str(row["content"])) >= 0.92:
                    cluster.append(row)
                    placed = True
                    break
            if not placed:
                clusters.append([row])
        duplicates = [
            {
                "keep_id": int(cluster[0]["id"]),
                "ids": [int(item["id"]) for item in cluster],
                "contents": [scrub_secrets(str(item["content"]))[:120] for item in cluster[:4]],
                "reason": "内容近重复（相似度 ≥ 0.92）",
            }
            for cluster in clusters
            if len(cluster) > 1
        ]
        contradictions: list[dict[str, Any]] = []
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                if int(left["id"]) == int(right["id"]):
                    continue
                if (
                    _negation_stem(str(left["content"])) == _negation_stem(str(right["content"]))
                    and _negation_stem(str(left["content"]))
                    and is_contradiction(str(left["content"]), str(right["content"]))
                ):
                    contradictions.append(
                        {
                            "ids": [int(left["id"]), int(right["id"])],
                            "contents": [
                                scrub_secrets(str(left["content"]))[:120],
                                scrub_secrets(str(right["content"]))[:120],
                            ],
                            "reason": "疑似极性矛盾（只报告，不删除）",
                        }
                    )
        expired = [
            self._present(row) for row in rows if row.get("expires_at") is not None and float(row["expires_at"]) <= now
        ]
        cutoff = now - 30 * 86400
        low_value = [
            self._present(row)
            for row in rows
            if int(row.get("hits") or 0) == 0
            and float(row.get("importance") or 0) <= 0.3
            and float(row.get("updated_at") or 0) < cutoff
            and not (row.get("expires_at") is not None and float(row["expires_at"]) <= now)
        ]
        preview = {
            "duplicates": duplicates,
            "contradictions": contradictions,
            "expired": expired,
            "low_value": low_value,
            "counts": {
                "records": len(rows),
                "duplicates": len(duplicates),
                "contradictions": len(contradictions),
                "expired": len(expired),
                "low_value": len(low_value),
            },
        }
        base = {
            "ok": True,
            "available": True,
            "scope": scope,
            "apply": False,
            "source": "studio",
            "preview": preview,
        }
        if not apply_changes:
            return base | {"message": "整理预览已生成；apply 前会先备份当前 scope。"}
        self._require_confirmation(confirm, scope["scope_id"], "应用整理结果")
        remove_ids = [int(item) for cluster in duplicates for item in cluster["ids"][1:]]
        remove_ids.extend(int(item["id"]) for item in expired)
        remove_ids = sorted(set(remove_ids))
        if not remove_ids:
            return base | {
                "apply": True,
                "result": {"deleted": 0, "contradictions": len(contradictions)},
                "message": "没有可自动清理的重复/过期记忆；矛盾只报告，不删除。",
            }
        backup_name = self._write_backup(scope, rows, reason="reflect")
        statements: list[dict[str, Any]] = []
        if self._has_fts():
            for identifier in remove_ids:
                statements.append(
                    {"sql": "DELETE FROM astrbot_memory_fts WHERE rowid=?", "params": [identifier]}
                )
        for identifier in remove_ids:
            statements.append(
                {
                    "sql": "DELETE FROM astrbot_memory WHERE id=? AND scope_type=? AND scope_id=?",
                    "params": [identifier, scope["scope_type"], scope["scope_id"]],
                }
            )
        self._write(statements)
        return base | {
            "apply": True,
            "backup": backup_name,
            "deleted_ids": remove_ids,
            "result": {
                "deleted": len(remove_ids),
                "duplicate_extras": sum(len(cluster["ids"]) - 1 for cluster in duplicates),
                "expired": len(expired),
                "contradictions": len(contradictions),
            },
            "message": "已清理 %d 条重复/过期记忆；矛盾仍保留。" % len(remove_ids),
        }

    # -- internals ------------------------------------------------------
    def _scope(self, scope_type: Any, scope_id: Any) -> dict[str, str]:
        kind, value = validate_scope(scope_type, scope_id)
        return {"scope_type": kind, "scope_id": value}

    @staticmethod
    def _dedup_mode(payload: Mapping[str, Any]) -> str:
        mode = str(payload.get("dedup") or "skip").strip().lower()
        if mode not in ("skip", "overwrite"):
            raise MemoryAdminError("dedup 只支持 skip 或 overwrite。")
        return mode

    @staticmethod
    def _require_confirmation(raw: Any, expected: str, action: str) -> None:
        value = str(raw if raw is not None else "").strip()
        if not value:
            raise MemoryAdminError("%s需要二次确认：请在 confirm 字段填入 %r。" % (action, expected))
        if value != expected:
            raise MemoryAdminError("%s的 confirm 字段必须与 %r 完全一致。" % (action, expected))

    def _normalise_record(
        self,
        payload: Mapping[str, Any],
        *,
        now: float,
        existing: Mapping[str, Any] | None,
        partial: bool = False,
    ) -> dict[str, Any]:
        allowed = {"mem_key", "key", "content", "kind", "importance", "confidence", "expires_at", "ttl_seconds", "created_at", "updated_at"}
        unknown = set(payload) - allowed - {"dedup", "confirm", "apply", "skip_invalid", "records", "scope_type", "scope_id", "query", "limit", "offset"}
        if unknown:
            raise MemoryAdminError("不支持的字段：%s。" % ", ".join(sorted(str(item) for item in unknown)))
        if partial and existing is not None:
            content = _validate_content(payload["content"]) if "content" in payload else str(existing["content"])
            key_raw = payload.get("mem_key", payload.get("key", existing["mem_key"]))
            kind = _validate_kind(payload.get("kind"), str(existing["kind"]))
            importance = _validate_unit_float(payload.get("importance"), "importance", float(existing["importance"]))
            confidence = _validate_unit_float(payload.get("confidence"), "confidence", float(existing["confidence"]))
            provided_expiry, expires_at = _resolve_expiry(payload, now)
            if not provided_expiry:
                expires_at = existing.get("expires_at")
        else:
            if "content" not in payload:
                raise MemoryAdminError("缺少记忆内容 content。")
            content = _validate_content(payload["content"])
            key_raw = payload.get("mem_key", payload.get("key", ""))
            kind = _validate_kind(payload.get("kind"))
            importance = _validate_unit_float(payload.get("importance"), "importance", 0.5)
            confidence = _validate_unit_float(payload.get("confidence"), "confidence", 0.5)
            _, expires_at = _resolve_expiry(payload, now)
        created_at = float(existing["created_at"]) if existing is not None else now
        if "created_at" in payload:
            try:
                created_at = float(payload["created_at"])
            except (TypeError, ValueError):
                raise MemoryAdminError("created_at 必须是 Unix 时间戳（秒）。") from None
            if created_at <= 0 or created_at > now + 60:
                raise MemoryAdminError("created_at 必须是过去的时间戳。")
        return {
            "mem_key": _validate_key(key_raw, content),
            "content": content,
            "kind": kind,
            "importance": importance,
            "confidence": confidence,
            "expires_at": expires_at,
            "fingerprint": fingerprint(content),
            "created_at": created_at,
        }

    def _has_table(self) -> bool:
        has_table, _ = self._schema_state()
        return has_table

    def _has_fts(self) -> bool:
        _, has_fts = self._schema_state()
        return has_fts

    def _schema_state(self) -> tuple[bool, bool]:
        if self._schema is not None:
            return self._schema
        if not self.db_path.exists() and self.channel != "container":
            self._schema = (False, False)
            return self._schema
        try:
            rows = self._rows(
                [
                    {
                        "sql": (
                            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                            " AND name IN ('astrbot_memory','astrbot_memory_fts')"
                        ),
                        "params": [],
                    }
                ]
            )
        except MemoryAdminUnavailable:
            raise
        except sqlite3.Error as exc:
            message = str(exc).lower()
            if "not a database" in message or "malformed" in message:
                raise self._corrupt_error(exc) from exc
            raise MemoryAdminUnavailable(
                self._mask("无法读取记忆数据库结构：%s" % exc),
                actions=["确认实例数据目录可读，或让 AstrBot 容器保持运行。"],
            ) from exc
        names = {str(row.get("name")) for row in (rows[0] if rows else [])}
        self._schema = ("astrbot_memory" in names, "astrbot_memory_fts" in names)
        return self._schema

    def _scope_rows(self, scope: Mapping[str, str]) -> list[Any]:
        rows = self._rows(
            [
                {
                    "sql": "SELECT " + _ROW_COLUMNS + " FROM astrbot_memory"
                    " WHERE scope_type=? AND scope_id=? ORDER BY updated_at DESC, id DESC",
                    "params": [scope["scope_type"], scope["scope_id"]],
                }
            ]
        )
        return rows[0] if rows else []

    def _scope_count(self, scope: Mapping[str, str]) -> int:
        rows = self._rows(
            [
                {
                    "sql": "SELECT COUNT(*) AS total FROM astrbot_memory WHERE scope_type=? AND scope_id=?",
                    "params": [scope["scope_type"], scope["scope_id"]],
                }
            ]
        )
        return int(rows[0][0].get("total") or 0) if rows and rows[0] else 0

    def _find_key_conflict(self, scope: Mapping[str, str], key: str) -> dict[str, Any] | None:
        rows = self._rows(
            [
                {
                    "sql": "SELECT " + _ROW_COLUMNS + " FROM astrbot_memory"
                    " WHERE scope_type=? AND scope_id=? AND mem_key=? LIMIT 1",
                    "params": [scope["scope_type"], scope["scope_id"], key],
                }
            ]
        )
        return dict(rows[0][0]) if rows and rows[0] else None

    def _find_fingerprint(self, scope: Mapping[str, str], value: str) -> dict[str, Any] | None:
        rows = self._rows(
            [
                {
                    "sql": "SELECT " + _ROW_COLUMNS + " FROM astrbot_memory"
                    " WHERE scope_type=? AND scope_id=? AND fingerprint=? LIMIT 1",
                    "params": [scope["scope_type"], scope["scope_id"], value],
                }
            ]
        )
        return dict(rows[0][0]) if rows and rows[0] else None

    def _require_record(self, scope: Mapping[str, str], identifier: int) -> dict[str, Any]:
        if not self._has_table():
            raise MemoryRecordNotFound("记录不存在：该 scope 还没有任何记忆。")
        rows = self._rows(
            [
                {
                    "sql": "SELECT " + _ROW_COLUMNS + " FROM astrbot_memory"
                    " WHERE id=? AND scope_type=? AND scope_id=?",
                    "params": [identifier, scope["scope_type"], scope["scope_id"]],
                }
            ]
        )
        if not rows or not rows[0]:
            raise MemoryRecordNotFound("记录不存在，或不属于当前 scope。")
        return dict(rows[0][0])

    def _present(self, row: Mapping[str, Any]) -> dict[str, Any]:
        now = float(self.clock())
        content = scrub_secrets(str(row.get("content") or ""))
        key = scrub_secrets(str(row.get("mem_key") or ""))
        expires_at = row.get("expires_at")
        expires_value = float(expires_at) if expires_at is not None else None
        return {
            "id": int(row.get("id") or 0),
            "key": key,
            "content": content,
            "kind": str(row.get("kind") or ""),
            "importance": float(row.get("importance") or 0),
            "confidence": float(row.get("confidence") or 0),
            "hits": int(row.get("hits") or 0),
            "source": str(row.get("source") or ""),
            "created_at": _iso(row.get("created_at")),
            "created_at_epoch": row.get("created_at"),
            "updated_at": _iso(row.get("updated_at")),
            "updated_at_epoch": row.get("updated_at"),
            "last_used_at": _iso(row.get("last_used_at")),
            "expires_at": _iso(expires_value),
            "expires_at_epoch": expires_value,
            "expires_in_seconds": int(expires_value - now) if expires_value is not None else None,
            "expired": bool(expires_value is not None and expires_value <= now),
            "redacted": content != str(row.get("content") or "") or key != str(row.get("mem_key") or ""),
        }

    def _empty_payload(self, scope: Mapping[str, str], message: str, *, records: list[Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": True,
            "available": True,
            "scope": scope,
            "writable": False,
            "read_channel": self.channel,
            "records": records or [],
            "total": 0,
            "message": message,
        }
        return payload

    def _writable(self) -> bool:
        # Writes need a host-accessible file or a reachable container channel;
        # a missing database is never created by Studio (the core owns schema).
        return self.db_path.exists() or (self.channel == "container" and self._container_available())

    # -- transports -----------------------------------------------------
    def _rows(self, statements: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        return self._execute(statements, write=False)["rows"]

    def _write(self, statements: list[dict[str, Any]]) -> dict[str, Any]:
        result = self._execute(statements, write=True)
        rowcounts = [
            int(item.get("rowcount") or 0) for item in result["rows"] if isinstance(item, Mapping)
        ]
        return {
            "rowcounts": rowcounts,
            "rowcount": rowcounts[-1] if rowcounts else 0,
            "lastrowid": result.get("lastrowid"),
        }

    def _execute(self, statements: list[dict[str, Any]], *, write: bool) -> dict[str, Any]:
        self._validate_statements(statements, write=write)
        if not self.db_path.exists() and self.channel != "container":
            raise MemoryAdminUnavailable(
                "未找到记忆数据库（AstrBot 尚未创建）。启用长期记忆并在聊天中产生一条记忆后重试。",
                empty=True,
                actions=[
                    "确认实例已启用长期记忆（记忆策略面板）。",
                    "在已开启的群/私聊里发一条会被记住的消息，或重启 AstrBot。",
                    "如自定义了 memory.db_path，Studio 目前只支持默认路径。",
                ],
            )
        if self.channel != "container":
            try:
                return self._execute_host(statements, write=write)
            except _HostPermissionDenied as exc:
                if not self._container_available():
                    raise MemoryAdminUnavailable(
                        self._mask(
                            "本机 Studio 用户无权%s记忆数据库，且 AstrBot 容器不可用：%s"
                            % ("写入" if write else "读取", exc)
                        ),
                        permission_denied=True,
                        actions=[
                            "让 AstrBot 容器保持运行，Studio 会通过 docker exec 在容器内读写。",
                            "或修正实例数据目录权限，使 Studio 用户可读写 data/astrbot/。",
                        ],
                    ) from exc
                self.channel = "container"
        try:
            return self._execute_container(statements, write=write)
        except sqlite3.Error as exc:
            if not write and "database is locked" in str(exc).lower():
                raise MemoryAdminUnavailable(
                    "记忆数据库正被 AstrBot 占用，请稍后重试。", actions=["等待几秒后重试。"]
                ) from exc
            raise

    @staticmethod
    def _validate_statements(statements: Sequence[Mapping[str, Any]], *, write: bool) -> None:
        for statement in statements:
            sql = str(statement.get("sql") or "").strip()
            if not sql or ";" in sql:
                raise MemoryAdminError("内部 SQL 语句非法（不允许分号或多语句）。")
            first = sql.lower().split(" ", 1)[0]
            if first not in _ALLOWED_FIRST_WORDS:
                raise MemoryAdminError("内部 SQL 语句类型不允许：%s。" % first)
            if not write and first != "select":
                raise MemoryAdminError("只读操作不能包含写语句。")

    def _connect_local(self, *, write: bool) -> sqlite3.Connection:
        if write:
            connection = sqlite3.connect(str(self.db_path), timeout=self.busy_timeout, isolation_level=None)
        else:
            uri = "file:%s?mode=ro" % urllib.parse.quote(str(self.db_path))
            connection = sqlite3.connect(uri, uri=True, timeout=self.busy_timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=%d" % int(self.busy_timeout * 1000))
        return connection

    def _execute_host(self, statements: list[dict[str, Any]], *, write: bool) -> dict[str, Any]:
        connection = None
        try:
            connection = self._connect_local(write=write)
            rows: list[Any] = []
            lastrowid: int | None = None
            if write:
                connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    cursor = connection.execute(str(statement["sql"]), list(statement.get("params") or []))
                    if cursor.description:
                        columns = [item[0] for item in cursor.description]
                        rows.append([dict(zip(columns, row)) for row in cursor.fetchall()])
                    else:
                        rows.append({"rowcount": cursor.rowcount})
                        if cursor.lastrowid:
                            lastrowid = int(cursor.lastrowid)
                if write:
                    connection.execute("COMMIT")
            except BaseException:
                if write:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
            return {"rows": rows, "lastrowid": lastrowid}
        except (sqlite3.OperationalError, PermissionError, OSError) as exc:
            message = str(exc).lower()
            if write and any(hint in message for hint in _PERMISSION_HINTS):
                raise _HostPermissionDenied(str(exc)) from exc
            if not write and any(hint in message for hint in _PERMISSION_HINTS):
                raise _HostPermissionDenied(str(exc)) from exc
            if "file is not a database" in message or "malformed" in message:
                raise self._corrupt_error(exc) from exc
            if any(hint in message for hint in _LOCKED_HINTS):
                raise MemoryAdminUnavailable(
                    "记忆数据库正被 AstrBot 占用，请稍后重试。", actions=["等待几秒后重试。"]
                ) from exc
            raise
        finally:
            if connection is not None:
                connection.close()

    def _execute_container(self, statements: list[dict[str, Any]], *, write: bool) -> dict[str, Any]:
        runner = getattr(self.docker, "exec_in_container", None)
        if not callable(runner) or not self.container_name:
            raise MemoryAdminUnavailable(
                "记忆数据库无法在本机访问，且当前实例没有可用的 AstrBot 容器通道。",
                permission_denied=True,
            )
        payload = base64.b64encode(
            json.dumps(
                {"write": bool(write), "statements": list(statements)}, ensure_ascii=False
            ).encode("utf-8")
        ).decode("ascii")
        try:
            result = runner(
                self.container_name,
                ["python3", "-c", _CONTAINER_RUNNER, self.container_db_path, payload],
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - docker client failures are availability
            raise MemoryAdminUnavailable(
                self._mask("无法在 AstrBot 容器内执行记忆数据库命令：%s" % exc),
                permission_denied=True,
            ) from exc
        returncode = int(getattr(result, "returncode", 1) or 0)
        stdout = str(getattr(result, "stdout", "") or "")
        stderr = str(getattr(result, "stderr", "") or "")
        marker = ""
        for line in reversed(stdout.splitlines()):
            if line.startswith("MEMADMIN:"):
                marker = line[len("MEMADMIN:") :]
                break
        if returncode != 0 or not marker:
            detail = self._mask(stderr.strip() or stdout.strip())
            if "UNIQUE constraint" in stderr:
                raise MemoryConflict("该 scope 内已存在相同记忆键或内容。")
            if "locked" in stderr.lower():
                raise MemoryAdminUnavailable("记忆数据库正被 AstrBot 占用，请稍后重试。")
            raise MemoryAdminUnavailable(
                "容器内读写记忆数据库失败：%s" % (detail[:240] or "未知错误"),
                actions=["确认 AstrBot 容器正在运行且镜像内包含 python3。"],
            )
        try:
            parsed = json.loads(marker)
        except ValueError as exc:
            raise MemoryAdminUnavailable("容器内记忆数据库返回格式异常。") from exc
        rows = list(parsed) if isinstance(parsed, list) else []
        lastrowid = None
        return {"rows": rows, "lastrowid": lastrowid}

    def _container_available(self) -> bool:
        if self._container_ready is not None:
            return self._container_ready
        runner = getattr(self.docker, "exec_in_container", None)
        if not callable(runner) or not self.container_name:
            self._container_ready = False
            return False
        running = getattr(self.docker, "is_container_running", None)
        if callable(running):
            try:
                self._container_ready = bool(running(self.container_name))
            except Exception:  # noqa: BLE001 - optional probe
                self._container_ready = False
            return self._container_ready
        try:
            result = runner(self.container_name, ["true"], timeout=8)
        except Exception:  # noqa: BLE001 - optional probe
            self._container_ready = False
            return False
        self._container_ready = int(getattr(result, "returncode", 1)) == 0
        return self._container_ready

    def _corrupt_error(self, exc: BaseException) -> MemoryAdminUnavailable:
        return MemoryAdminUnavailable(
            self._mask("记忆数据库损坏或无法解析：%s" % exc),
            corrupt=True,
            actions=[
                "备份并删除实例 data 目录下的记忆数据库文件（及 -wal/-shm）后重启 AstrBot 会重建空库。",
                "不要手工编辑数据库文件。",
            ],
        )

    def _mask(self, text: str) -> str:
        cleaned = str(text or "")
        for path in (str(self.db_path), str(self.db_path.parent), self.container_db_path, CONTAINER_DATA_DIR):
            if path:
                cleaned = cleaned.replace(path, "记忆数据库")
        return scrub_secrets(cleaned)

    # -- FTS helpers ----------------------------------------------------
    def _fts_row(self, identifier: int, key: str, content: str) -> dict[str, Any]:
        return {
            "sql": "INSERT INTO astrbot_memory_fts (rowid, mem_key, content, tokens) VALUES (?,?,?,?)",
            "params": [identifier, key, content, " ".join(tokenize(key + " " + content))],
        }

    def _fts_row_by_key(self, scope: Mapping[str, str], key: str, content: str) -> dict[str, Any]:
        """Index a row inserted earlier in the same transaction (id via sub-select)."""

        return {
            "sql": (
                "INSERT INTO astrbot_memory_fts (rowid, mem_key, content, tokens)"
                " SELECT id, ?, ?, ? FROM astrbot_memory WHERE scope_type=? AND scope_id=? AND mem_key=?"
            ),
            "params": [
                key,
                content,
                " ".join(tokenize(key + " " + content)),
                scope["scope_type"],
                scope["scope_id"],
                key,
            ],
        }

    # -- backups --------------------------------------------------------
    def _write_backup(self, scope: Mapping[str, str], rows: Iterable[Mapping[str, Any]], *, reason: str) -> str:
        directory = Path(self.backup_dir)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = "memory-%s-%s-%s.json" % (scope["scope_type"], reason, stamp)
        payload = {
            "schema_version": 1,
            "scope": {"scope_type": scope["scope_type"], "scope_id": scope["scope_id"]},
            "created_at": _iso(float(self.clock())),
            "reason": reason,
            "records": [self._present(row) for row in rows],
        }
        target = directory / name
        fd, temporary = tempfile.mkstemp(prefix=".memory-backup-", dir=str(directory))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            if os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return name


class _HostPermissionDenied(Exception):
    """The host Studio user cannot read/write the database file."""
