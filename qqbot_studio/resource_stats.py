"""Platform-neutral resource measurement helpers.

Extracted from ``studio.py`` as the first low-risk modularization slice.  The
old ``studio._directory_usage`` etc. names remain as compatibility aliases, but
new code can depend on :class:`ResourceStats` and inject a fake implementation.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .contracts import ResourceStats


def directory_usage(path: Path | str) -> tuple[int, int, list[str]]:
    """Return ``(bytes, files, errors)`` without following symbolic links."""

    root = Path(path)
    total = 0
    files = 0
    errors: list[str] = []
    if not root.exists():
        return 0, 0, errors
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            errors.append(f"无法读取 {current}：{exc}")
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += int(entry.stat(follow_symlinks=False).st_size)
                    files += 1
            except OSError as exc:
                errors.append(f"无法统计 {entry.path}：{exc}")
    return total, files, errors[:20]


def disk_usage(path: Path | str) -> dict[str, Any]:
    """Return filesystem capacity with a Windows/Linux-safe fallback."""

    try:
        usage = shutil.disk_usage(str(path))
        used_percent = round((usage.used / usage.total) * 100, 2) if usage.total else None
        return {
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "free_bytes": int(usage.free),
            "used_percent": used_percent,
            "error": "",
        }
    except (OSError, AttributeError) as exc:
        try:
            stat = os.statvfs(str(path))
            total = int(stat.f_frsize * stat.f_blocks)
            free = int(stat.f_frsize * stat.f_bavail)
            used = max(0, total - free)
            return {
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "used_percent": round((used / total) * 100, 2) if total else None,
                "error": "",
            }
        except (AttributeError, OSError):
            return {
                "total_bytes": None,
                "used_bytes": None,
                "free_bytes": None,
                "used_percent": None,
                "error": f"磁盘容量读取失败：{exc}",
            }


def host_memory_info() -> dict[str, Any]:
    """Read physical host memory using only the standard library."""

    result: dict[str, Any] = {
        "total_bytes": None,
        "available_bytes": None,
        "used_bytes": None,
        "used_percent": None,
        "source": "",
        "error": "",
    }
    if os.name == "nt":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.ullTotalPhys)
                available = int(status.ullAvailPhys)
                used = max(0, total - available)
                result.update(
                    {
                        "total_bytes": total,
                        "available_bytes": available,
                        "used_bytes": used,
                        "used_percent": round((used / total) * 100, 2) if total else None,
                        "source": "GlobalMemoryStatusEx",
                    }
                )
                return result
            result["error"] = "Windows GlobalMemoryStatusEx 读取失败。"
        except (AttributeError, OSError, ValueError) as exc:
            result["error"] = f"Windows 内存读取失败：{exc}"
        return result

    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0]) * 1024
        total = int(values.get("MemTotal", 0))
        if total > 0:
            available = int(
                values.get("MemAvailable", 0)
                or values.get("MemFree", 0)
                + values.get("Buffers", 0)
                + values.get("Cached", 0)
            )
            available = max(0, min(total, available))
            used = total - available
            result.update(
                {
                    "total_bytes": total,
                    "available_bytes": available,
                    "used_bytes": used,
                    "used_percent": round((used / total) * 100, 2),
                    "source": "/proc/meminfo",
                }
            )
            return result
    except (OSError, ValueError) as exc:
        result["error"] = f"/proc/meminfo 读取失败：{exc}"

    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        total = page_size * total_pages
        available = page_size * available_pages
        used = max(0, total - available)
        result.update(
            {
                "total_bytes": total,
                "available_bytes": available,
                "used_bytes": used,
                "used_percent": round((used / total) * 100, 2) if total else None,
                "source": "os.sysconf",
            }
        )
        if total <= 0:
            result["error"] = "宿主机内存总量为 0 或不可用。"
    except (AttributeError, OSError, ValueError) as exc:
        result["error"] = result["error"] or f"宿主机内存读取失败：{exc}"
    return result


def parse_size_bytes(value: Any) -> int | None:
    """Parse Docker's human-readable memory values (for example ``123.4MiB``)."""

    text = str(value or "").strip().replace(" ", "")
    if not text or text.lower() in {"n/a", "0b"}:
        return 0 if text.lower() == "0b" else None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]+)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    factors = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    factor = factors.get(unit)
    return int(number * factor) if factor is not None else None


def parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().rstrip("%")
    if not text or text.lower() in {"n/a", "--"}:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def parse_docker_stats_output(stdout: str) -> dict[str, dict[str, Any]]:
    """Parse either Docker JSON-per-line stats or tab-separated test output."""

    parsed: dict[str, dict[str, Any]] = {}
    for line in str(stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        record: dict[str, Any] = {}
        if text.startswith("{"):
            try:
                raw = json.loads(text)
                if isinstance(raw, dict):
                    record = raw
            except ValueError:
                continue
        else:
            parts = text.split("\t")
            if len(parts) >= 4:
                record = {
                    "Name": parts[0],
                    "CPUPerc": parts[1],
                    "MemUsage": parts[2],
                    "MemPerc": parts[3],
                }
        name = str(record.get("Name") or record.get("name") or "").strip().lstrip("/")
        if not name:
            continue
        memory_usage = record.get("MemUsage") or record.get("mem_usage") or ""
        pieces = str(memory_usage).split("/", 1)
        usage_bytes = parse_size_bytes(pieces[0]) if pieces else None
        limit_bytes = parse_size_bytes(pieces[1]) if len(pieces) > 1 else None
        parsed[name] = {
            "cpu_percent": parse_percent(record.get("CPUPerc") or record.get("cpu_perc")),
            "memory_usage_bytes": usage_bytes,
            "memory_limit_bytes": limit_bytes,
            "memory_percent": parse_percent(record.get("MemPerc") or record.get("mem_perc")),
        }
    return parsed


class DefaultResourceStats:
    """Standard-library implementation of :class:`ResourceStats`."""

    def directory_usage(self, path: Path | str) -> tuple[int, int, list[str]]:
        return directory_usage(path)

    def disk_usage(self, path: Path | str) -> dict[str, Any]:
        return disk_usage(path)

    def host_memory(self) -> dict[str, Any]:
        return host_memory_info()

    def parse_docker_stats(self, stdout: str) -> dict[str, dict[str, Any]]:
        return parse_docker_stats_output(stdout)


# Explicit adapter alias for composition roots.
DefaultResourceStatsProvider = DefaultResourceStats

__all__ = [
    "DefaultResourceStats",
    "DefaultResourceStatsProvider",
    "directory_usage",
    "disk_usage",
    "host_memory_info",
    "parse_docker_stats_output",
    "parse_percent",
    "parse_size_bytes",
]
