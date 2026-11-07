"""Deterministic, stdlib-only source inventory helpers.

This module is deliberately offline: it parses source text with :mod:`ast`,
reads JSON schemas and hashes package trees.  It never imports AstrBot or any
plugin package, so it is safe to run from the main project test suite.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# <repo>/migrations/modular-v2-v3/tools/inventory_lib.py
ROOT = Path(__file__).resolve().parents[3]
MIGRATION_DIR = ROOT / "migrations" / "modular-v2-v3"
SNAPSHOT_DIR = MIGRATION_DIR / "snapshots"

FINAL_PLUGIN_IDS: tuple[str, ...] = (
    "qqbot_core_reply",
    "qqbot_safe_tools",
    "qqbot_web_reader",
    "qqbot_image_search",
    "qqbot_food",
    "qqbot_stickers",
    "qqbot_memory_commands",
)
LEGACY_PLUGIN_IDS: tuple[str, ...] = ("qqbot_enhancer", "qqbot_core_full")
ALL_PACKAGE_IDS: tuple[str, ...] = LEGACY_PLUGIN_IDS + FINAL_PLUGIN_IDS

TEXT_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".md", ".txt", ".sh"})
EXCLUDED_DIR_NAMES = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache"})


def read_json(path: str | os.PathLike[str]) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str | os.PathLike[str], value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    target.write_text(text, encoding="utf-8")


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_package_files(root: str | os.PathLike[str]) -> list[Path]:
    base = Path(root)
    files: list[Path] = []
    for path in base.rglob("*"):
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                continue
        except OSError:
            continue
        if path.suffix == ".pyc":
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(base).as_posix())


def tree_digest(root: str | os.PathLike[str]) -> str:
    """Hash relative paths plus bytes so the digest is machine-independent."""

    base = Path(root)
    digest = hashlib.sha256()
    for path in iter_package_files(base):
        relative = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def line_count(path: str | os.PathLike[str]) -> int:
    with Path(path).open("rb") as handle:
        return sum(1 for _ in handle)


def _read_metadata_version(root: Path) -> str:
    metadata = root / "metadata.yaml"
    if not metadata.is_file():
        return ""
    for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^version:\s*['\"]?([^'\"\s#]+)", line.strip())
        if match:
            return match.group(1)
    return ""


def _read_version_file(root: Path) -> str:
    version = root / "VERSION"
    if not version.is_file():
        return ""
    return version.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0].strip()


def package_stats(root: str | os.PathLike[str], *, kind: str) -> dict[str, Any]:
    base = Path(root)
    files = iter_package_files(base)
    text_files = [path for path in files if path.suffix in TEXT_SUFFIXES]
    python_files = [path for path in files if path.suffix == ".py"]
    main_py = base / "main.py"
    schema = base / "_conf_schema.json"
    stats: dict[str, Any] = {
        "source_path": str(base),
        "package_kind": kind,
        "version_file": _read_version_file(base),
        "metadata_version": _read_metadata_version(base),
        "file_count": len(files),
        "text_file_count": len(text_files),
        "python_file_count": len(python_files),
        "text_line_count": sum(line_count(path) for path in text_files),
        "python_line_count": sum(line_count(path) for path in python_files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "tree_sha256": tree_digest(base),
        "main_py": {
            "present": main_py.is_file(),
            "lines": line_count(main_py) if main_py.is_file() else 0,
            "bytes": main_py.stat().st_size if main_py.is_file() else 0,
            "sha256": sha256_file(main_py) if main_py.is_file() else "",
        },
        "conf_schema": {
            "present": schema.is_file(),
            "lines": line_count(schema) if schema.is_file() else 0,
            "bytes": schema.stat().st_size if schema.is_file() else 0,
            "sha256": sha256_file(schema) if schema.is_file() else "",
            "entries": len(read_json(schema)) if schema.is_file() else 0,
        },
        "files": [
            {
                "path": path.relative_to(base).as_posix(),
                "bytes": path.stat().st_size,
                "lines": line_count(path) if path.suffix in TEXT_SUFFIXES else None,
                "sha256": sha256_file(path),
            }
            for path in files
        ],
    }
    return stats


def _literal(node: ast.AST | None, constants: Mapping[str, Any]) -> Any:
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = [_literal(item, constants) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(values)
        return values
    return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        target = node.func
        if isinstance(target, ast.Attribute):
            return target.attr
        if isinstance(target, ast.Name):
            return target.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _keyword(call: ast.Call, name: str) -> Any:
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def module_constants(tree: ast.Module) -> dict[str, Any]:
    constants: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = _literal(node.value, constants)
        if value is not None:
            constants[target.id] = value
    return constants


def _decorator_category(call: ast.Call) -> str:
    name = _call_name(call)
    if name == "command":
        return "command"
    if name == "event_message_type":
        return "event_listener"
    if name == "on_decorating_result":
        return "result_hook"
    if name == "on_llm_request":
        return "llm_request_hook"
    if name == "platform_adapter_type":
        return "platform_condition"
    return "other"


def extract_decorated_handlers(main_path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    path = Path(main_path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants = module_constants(tree)
    handlers: list[dict[str, Any]] = []
    for class_node in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
        for function in class_node.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not function.decorator_list:
                continue
            decorators: list[dict[str, Any]] = []
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call):
                    decorators.append({"name": _call_name(decorator), "raw": ast.unparse(decorator), "category": "other"})
                    continue
                item: dict[str, Any] = {
                    "name": _call_name(decorator),
                    "raw": ast.unparse(decorator),
                    "category": _decorator_category(decorator),
                }
                if item["category"] == "command":
                    item["command"] = _literal(decorator.args[0], constants) if decorator.args else None
                    alias_node = _keyword(decorator, "alias")
                    aliases = _literal(alias_node, constants)
                    if aliases is None:
                        aliases = []
                    elif isinstance(aliases, str):
                        aliases = [aliases]
                    elif isinstance(aliases, (set, list, tuple)):
                        aliases = [str(value) for value in aliases if value]
                    else:
                        aliases = []
                    item["aliases"] = sorted(dict.fromkeys(str(value) for value in aliases))
                    priority = _literal(_keyword(decorator, "priority"), constants)
                    item["priority"] = priority
                elif item["category"] in {"event_listener", "result_hook"}:
                    priority = _literal(_keyword(decorator, "priority"), constants)
                    item["priority"] = priority
                decorators.append(item)
            categories = {item["category"] for item in decorators}
            if not categories.intersection({"command", "event_listener", "result_hook", "llm_request_hook"}):
                continue
            if "command" in categories:
                kind = "command"
            elif "event_listener" in categories:
                kind = "event_listener"
            elif "result_hook" in categories:
                kind = "result_hook"
            else:
                kind = "llm_request_hook"
            command_decorator = next((item for item in decorators if item["category"] == "command"), None)
            platform_decorator = next((item for item in decorators if item["category"] == "platform_condition"), None)
            event_decorator = next((item for item in decorators if item["category"] == "event_listener"), None)
            hook_decorator = next((item for item in decorators if item["category"] in {"result_hook", "llm_request_hook"}), None)
            handlers.append({
                "kind": kind,
                "class": class_node.name,
                "symbol": function.name,
                "symbol_line": function.lineno,
                "end_line": getattr(function, "end_lineno", function.lineno),
                "registration_lines": [decorator.lineno for decorator in function.decorator_list],
                "decorators": decorators,
                "command": command_decorator.get("command") if command_decorator else None,
                "aliases": command_decorator.get("aliases", []) if command_decorator else [],
                "event": event_decorator.get("raw") if event_decorator else None,
                "hook": hook_decorator.get("raw") if hook_decorator else None,
                "platform_condition": platform_decorator.get("raw") if platform_decorator else None,
                "priority": (
                    command_decorator.get("priority")
                    if command_decorator
                    else event_decorator.get("priority")
                    if event_decorator
                    else hook_decorator.get("priority")
                    if hook_decorator
                    else None
                ),
            })
    return sorted(handlers, key=lambda item: (item["symbol_line"], item["symbol"]))


def function_config_references(
    path: str | os.PathLike[str],
    schema_keys: Iterable[str],
) -> dict[str, list[int]]:
    """Return schema keys referenced by string literal inside functions.

    Module-level ``DEFAULTS`` dictionaries are intentionally ignored: declaring
    a default is not evidence that the behaviour is wired.
    """

    keys = set(schema_keys)
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    result: dict[str, list[int]] = {key: [] for key in keys}
    for node in [item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value in result:
                result[child.value].append(getattr(child, "lineno", node.lineno))
    return {key: sorted(set(lines)) for key, lines in result.items()}


def task_side_effects(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    found: list[dict[str, Any]] = []
    patterns = (
        "asyncio.create_task",
        "asyncio.ensure_future",
        "threading.Thread",
        "Timer",
        "add_job",
        "AsyncIOScheduler",
        "BackgroundScheduler",
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if any(name == pattern or name.endswith("." + pattern.split(".")[-1]) for pattern in patterns):
            found.append({"line": getattr(node, "lineno", 0), "call": name})
    return sorted(found, key=lambda item: (item["line"], item["call"]))


def function_index(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    items: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append({
                "symbol": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "async": isinstance(node, ast.AsyncFunctionDef),
            })
    return sorted(items, key=lambda item: (item["line"], item["symbol"]))


def schema_entries_with_lines(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    schema_path = Path(path)
    schema = read_json(schema_path)
    raw_lines = schema_path.read_text(encoding="utf-8").splitlines()
    key_lines: dict[str, int] = {}
    for index, line in enumerate(raw_lines, start=1):
        match = re.match(r'^\s{2}"([^"]+)":\s*\{', line)
        if match and match.group(1) not in key_lines:
            key_lines[match.group(1)] = index
    entries: list[dict[str, Any]] = []
    for key, definition in schema.items():
        if not isinstance(definition, dict):
            continue
        entries.append({
            "key": str(key),
            "type": definition.get("type"),
            "default": definition.get("default"),
            "description": definition.get("description"),
            "source_line": key_lines.get(str(key), 0),
            "slider": definition.get("slider"),
            "items": definition.get("items"),
            "options": definition.get("options"),
        })
    return entries


def extract_lifecycle(path: str | os.PathLike[str]) -> dict[str, Any]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    result: dict[str, Any] = {"initialize": None, "terminate": None, "constructor": None}
    for class_node in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
        for function in class_node.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if function.name in {"__init__", "initialize", "terminate"}:
                result[{"__init__": "constructor"}.get(function.name, function.name)] = {
                    "symbol": function.name,
                    "class": class_node.name,
                    "line": function.lineno,
                    "end_line": getattr(function, "end_lineno", function.lineno),
                    "async": isinstance(function, ast.AsyncFunctionDef),
                }
    result["task_calls"] = task_side_effects(path)
    return result



def package_runtime_references(
    root: str | os.PathLike[str],
    schema_keys: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    """Collect config-key literals used inside functions across a package.

    Test files and module-level DEFAULTS declarations are excluded.  A small
    amount of indirection is resolved safely: module-level literal maps/sets
    (for example safe_tools ``_ALLOW_KEYS``) are counted only when a function
    actually loads that container name.  Generic ``DEFAULTS``/``SCHEMA``
    containers are deliberately ignored so declaring a default never counts as
    proof that a behaviour is wired.
    """

    base = Path(root)
    keys = set(schema_keys)
    result: dict[str, list[dict[str, Any]]] = {key: [] for key in keys}
    for path in sorted(base.rglob("*.py")):
        if "tests" in path.relative_to(base).parts or path.name == "config.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue

        # Local literal containers that may bind a group of config keys.
        constant_maps: dict[str, set[str]] = {}
        for node in tree.body:
            target = None
            value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target = node.targets[0].id
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target = node.target.id
                value = node.value
            if not target or value is None:
                continue
            upper = target.upper()
            if "DEFAULT" in upper or "SCHEMA" in upper:
                continue
            found: set[str] = set()
            if isinstance(value, ast.Dict):
                for item in value.values:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in keys:
                        found.add(item.value)
            elif isinstance(value, (ast.Set, ast.List, ast.Tuple)):
                for item in value.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in keys:
                        found.add(item.value)
            if found:
                constant_maps[target] = found

        for node in [item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            seen: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value in result:
                    seen.add(child.value)
                elif isinstance(child, ast.Name) and child.id in constant_maps:
                    seen.update(constant_maps[child.id])
            for key in sorted(seen):
                result[key].append({
                    "file": path.relative_to(base).as_posix(),
                    "line": node.lineno,
                    "function": node.name,
                })
    for key in result:
        unique = {
            (item["file"], item["line"], item["function"]): item
            for item in result[key]
        }
        result[key] = [unique[item] for item in sorted(unique)]
    return result


def package_snapshot(root: str | os.PathLike[str], *, kind: str) -> dict[str, Any]:
    base = Path(root)
    main_py = base / "main.py"
    schema = base / "_conf_schema.json"
    stats = package_stats(base, kind=kind)
    decorated = extract_decorated_handlers(main_py) if main_py.is_file() else []
    schema_data = schema_entries_with_lines(schema) if schema.is_file() else []
    references = (
        function_config_references(main_py, [item["key"] for item in schema_data])
        if main_py.is_file() and schema_data
        else {}
    )
    runtime_references = (
        package_runtime_references(base, [item["key"] for item in schema_data])
        if schema_data
        else {}
    )
    return {
        "package_id": base.name,
        "package_kind": kind,
        "stats": stats,
        "decorated_handlers": decorated,
        "schema": schema_data,
        "function_config_references": references,
        "runtime_config_references": runtime_references,
        "functions": function_index(main_py) if main_py.is_file() else [],
        "lifecycle": extract_lifecycle(main_py) if main_py.is_file() else {
            "initialize": None,
            "terminate": None,
            "constructor": None,
            "task_calls": [],
        },
    }


def find_staging_root() -> Path | None:
    env = os.environ.get("QQBOT_STAGING_ROOT", "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.extend([
        ROOT.parent.parent / ".qqbot-plugins-staging",
        ROOT.parent / ".qqbot-plugins-staging",
        ROOT.parent.parent.parent / ".qqbot-plugins-staging",
    ])
    for candidate in candidates:
        if (candidate / "plugins").is_dir():
            return candidate.resolve()
    return None


def git_head(repo: str | os.PathLike[str]) -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        return ""


def short_hash(path: str | os.PathLike[str], length: int = 12) -> str:
    return sha256_file(path)[:length]
