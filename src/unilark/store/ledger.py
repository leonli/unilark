"""Durable local bindings and input intentions; no runtime exactly-once claim.

Only QUEUED rows may be submitted. A process dying after claim leaves UNKNOWN
on recovery. Native tags can prove acceptance, but an absent tag cannot prove
non-execution; it never authorizes an automatic retry.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from unilark.lifecycle.files import private_directory


class Ledger:
    def __init__(self, path: Path, *, readonly: bool = False) -> None:
        if readonly:
            if path.is_symlink():
                raise ValueError("Ledger must not be a symlink")
            self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            self.db.row_factory = sqlite3.Row
            if self.db.execute("PRAGMA user_version").fetchone()[0] not in (1, 2, 3):
                self.db.close()
                raise ValueError("Unsupported database schema")
            return
        private_directory(path.parent)
        if path.is_symlink():
            raise ValueError("Ledger must not be a symlink")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2, 3):
            self.db.close()
            raise ValueError("Unsupported database schema; no migration was attempted")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS bindings (
                id TEXT PRIMARY KEY, profile TEXT NOT NULL, native_id TEXT NOT NULL,
                queue_state TEXT NOT NULL DEFAULT 'OPEN',
                UNIQUE(profile, native_id)
            );
            CREATE TABLE IF NOT EXISTS operations (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                binding_id TEXT NOT NULL REFERENCES bindings(id),
                source_scope TEXT NOT NULL, source_id TEXT NOT NULL,
                content TEXT NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'QUEUED', native_step INTEGER,
                UNIQUE(source_scope, source_id)
            );
        """)

    def bind(self, profile: str, native_id: str) -> str:
        uuid.UUID(native_id)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO bindings(id,profile,native_id) VALUES(?,?,?)",
                (str(uuid.uuid4()), profile, native_id),
            )
        row = self.db.execute(
            "SELECT id FROM bindings WHERE profile=? AND native_id=?", (profile, native_id)
        ).fetchone()
        return str(row["id"])

    def bindings(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM bindings ORDER BY rowid")]

    def enqueue(
        self, binding_id: str, scope: str, source_id: str, text: str, *, kind: str = "input"
    ) -> dict[str, Any]:
        if kind not in ("input", "steer") or not text.strip() or not scope or not source_id:
            raise ValueError("Invalid input intention")
        with self.db:
            self.db.execute(
                """INSERT OR IGNORE INTO operations
                (request_id,binding_id,source_scope,source_id,content,kind) VALUES(?,?,?,?,?,?)""",
                (str(uuid.uuid4()), binding_id, scope, source_id, text, kind),
            )
        row = self.db.execute(
            "SELECT * FROM operations WHERE source_scope=? AND source_id=?", (scope, source_id)
        ).fetchone()
        # A replay keeps its original binding even if the current selection changed.
        return dict(row)

    def claim(self, request_id: str) -> dict[str, Any] | None:
        with self.db:
            result = self.db.execute(
                """UPDATE operations SET state='SUBMITTING'
                WHERE request_id=? AND state='QUEUED'
                AND EXISTS(SELECT 1 FROM bindings b WHERE b.id=operations.binding_id
                           AND b.queue_state='OPEN')
                AND NOT EXISTS(SELECT 1 FROM operations blocker
                    WHERE blocker.binding_id=operations.binding_id
                    AND blocker.state IN ('SUBMITTING','UNKNOWN'))
                AND (kind='steer' OR NOT EXISTS(SELECT 1 FROM operations earlier
                    WHERE earlier.binding_id=operations.binding_id
                    AND earlier.kind='input' AND earlier.state='QUEUED'
                    AND earlier.ordinal<operations.ordinal))""",
                (request_id,),
            )
            if result.rowcount != 1:
                return None
        return self.get(request_id)

    def get(self, request_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM operations WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return dict(row)

    def finish(self, request_id: str, state: str, native_step: int | None = None) -> None:
        if state not in ("ACCEPTED", "UNKNOWN", "REJECTED"):
            raise ValueError("Invalid submission result")
        with self.db:
            result = self.db.execute(
                """UPDATE operations SET state=?,native_step=?
                WHERE request_id=? AND state IN ('SUBMITTING','UNKNOWN')""",
                (state, native_step, request_id),
            )
            if result.rowcount != 1:
                raise ValueError("Operation is not awaiting a submission result")

    def recover(self) -> int:
        """Call only at daemon startup while holding its exclusive instance lock."""
        with self.db:
            result = self.db.execute(
                "UPDATE operations SET state='UNKNOWN' WHERE state='SUBMITTING'"
            )
        return result.rowcount

    def pause(self, binding_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE bindings SET queue_state='PAUSED' WHERE id=?", (binding_id,))

    def resume(self, binding_id: str) -> None:
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM operations WHERE binding_id=? AND state='UNKNOWN'", (binding_id,)
            ).fetchone():
                raise ValueError("Resolve UNKNOWN operations before resuming")
            self.db.execute("UPDATE bindings SET queue_state='OPEN' WHERE id=?", (binding_id,))

    def close(self) -> None:
        self.db.close()
