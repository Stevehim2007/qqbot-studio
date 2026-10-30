"""Local authorized-song repository helpers (no network, no AstrBot imports)."""

from __future__ import annotations

from pathlib import Path

SONG_SUFFIXES = frozenset({".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"})


def resolve_song_file(
    root: Path | str,
    filename: str,
    *,
    suffixes: frozenset[str] = SONG_SUFFIXES,
) -> Path | None:
    if not filename:
        return None
    try:
        base = Path(root).resolve()
        raw_candidate = base / filename
        if raw_candidate.is_symlink():
            return None
        candidate = raw_candidate.resolve()
        if candidate.parent != base:
            return None
        if candidate.suffix.lower() not in suffixes:
            return None
        if not candidate.is_file():
            return None
        return candidate
    except (OSError, ValueError):
        return None


def list_song_files(
    root: Path | str,
    *,
    max_bytes: int,
    suffixes: frozenset[str] = SONG_SUFFIXES,
) -> tuple[list[Path], list[Path]]:
    """Return (allowed files, oversized files), sorted case-insensitively."""

    try:
        base = Path(root).resolve()
        entries = sorted(base.iterdir(), key=lambda path: path.name.casefold())
    except (OSError, ValueError):
        return [], []
    song_files: list[Path] = []
    oversized: list[Path] = []
    for entry in entries:
        song_file = resolve_song_file(base, entry.name, suffixes=suffixes)
        if song_file is None:
            continue
        try:
            size = song_file.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        if size > max_bytes:
            oversized.append(song_file)
            continue
        song_files.append(song_file)
    return song_files, oversized


class LocalSongRepository:
    """Small repository adapter usable independently of AstrBot."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def resolve(self, filename: str) -> Path | None:
        return resolve_song_file(self.root, filename)

    def list_tracks(self, *, max_bytes: int | None = None) -> list[Path]:
        if max_bytes is None:
            max_bytes = 1 << 62
        files, _ = list_song_files(self.root, max_bytes=max_bytes)
        return files
