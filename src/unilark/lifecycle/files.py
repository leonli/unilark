"""Restricted atomic state writes and SQLite-consistent backups."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("State directory must be owned by this user and mode 0700")
    return path


def write_json(path: Path, data: Any) -> None:
    if path.is_symlink():
        raise ValueError("Refusing a symlink output")
    fd, temporary = tempfile.mkstemp(prefix=".unilark-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def backup_database(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError("Backup requires an existing regular database")
    private_directory(target.parent)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    origin = sqlite3.connect(source)
    destination = sqlite3.connect(target)
    try:
        origin.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed")
    finally:
        destination.close()
        origin.close()
