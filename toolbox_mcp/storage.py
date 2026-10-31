"""JSON storage with path safety and automatic backups.

Everything is plain JSON on disk so a human can open the file in any
editor. The storage layer enforces a few safety rules so a confused
client (or hostile prompt) cannot trick the server into reading or
writing files outside of its designated data directory:

* The store path must end in ``.json`` with a normal file name.
* The resolved path must stay inside the allowed data directory
  (symlinks and ``..`` are resolved and re-checked, so traversal fails).
* The backup directory is protected too: you cannot name your store so
  that it lands inside ``backups/``.
* Before every write, the previous contents are copied to
  ``backups/<name>-<timestamp>.json`` and only the newest ``max_backups``
  backups are kept.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional


class UnsafePathError(ValueError):
    """Raised when a store path tries to escape the data directory."""


_SAFE_NAME_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "-_. "
)


def _assert_plain_name(path: Path) -> None:
    """Reject anything that is not a simple trailing file name."""
    name = path.name
    if not name or name in {".", ".."}:
        raise UnsafePathError("store file name is empty")
    if not name.endswith(".json"):
        raise UnsafePathError(f"store file name must end with .json: {name!r}")
    stem = name[:-5]
    if not stem or set(stem) - _SAFE_NAME_CHARS:
        raise UnsafePathError(f"store file name has unsafe characters: {name!r}")
    if any(part in ("", ".", "..") for part in path.parts[:-1]):
        raise UnsafePathError(f"store path has unsafe segments: {name!r}")


class JsonStore:
    """A single JSON document with path safety and auto-backup."""

    def __init__(
        self,
        path: str | Path,
        base_dir: Optional[str | Path] = None,
        max_backups: int = 10,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.base_dir = self.base_dir.resolve()
        candidate = Path(path)
        _assert_plain_name(candidate)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.base_dir / candidate).resolve()
        inside = resolved == self.base_dir or self.base_dir in resolved.parents
        if not inside:
            raise UnsafePathError(
                f"store path escapes data directory: {path!r}"
            )
        backups_dir = (self.base_dir / "backups").resolve()
        if resolved == backups_dir or backups_dir in resolved.parents:
            raise UnsafePathError("store path may not be inside backups/")
        self.path = resolved
        self.max_backups = max_backups
        self._stamp = clock or (lambda: "")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- public API ---------------------------------------------------

    def load(self, default: Any = None) -> Any:
        if not self.path.exists():
            return default
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError:
            # Corrupt store: prefer the newest backup, else start fresh.
            backup = self._newest_backup()
            if backup is not None:
                try:
                    with backup.open("r", encoding="utf-8") as fh:
                        return json.load(fh)
                except (json.JSONDecodeError, OSError):
                    pass
            return default

    def save(self, document: Any) -> None:
        """Back up the current file (if any), then write atomically."""
        if self.path.exists() and self.path.stat().st_size > 0:
            self._make_backup()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(document, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        self._prune_backups()

    def exists(self) -> bool:
        return self.path.exists()

    # -- backups ------------------------------------------------------

    @property
    def backups_dir(self) -> Path:
        return self.base_dir / "backups"

    def _make_backup(self) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._stamp()
        target = self.backups_dir / f"{self.path.name}.{stamp or 'bak'}.bak.json"
        shutil.copy2(self.path, target)

    @property
    def backup_files(self) -> list[Path]:
        if not self.backups_dir.is_dir():
            return []
        pattern = f"{self.path.name}.*"
        return sorted(self.backups_dir.glob(pattern))

    def _prune_backups(self) -> None:
        backups = self.backup_files
        excess = max(0, len(backups) - self.max_backups)
        for stale in backups[:excess]:
            try:
                stale.unlink()
            except OSError:
                pass

    def _newest_backup(self) -> Optional[Path]:
        backups = self.backup_files
        return backups[-1] if backups else None
