"""Snapshot observations and recovery decisions, without message bodies or secrets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from unilark.adapters.sidecars.views import SessionView


class Journal:
    def __init__(self, db: sqlite3.Connection, *, initialize: bool = True) -> None:
        self.db = db
        if not initialize:
            return
        db.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                binding TEXT PRIMARY KEY REFERENCES bindings(id), observed REAL NOT NULL,
                status TEXT NOT NULL, steps INTEGER NOT NULL, digest TEXT NOT NULL,
                gap_since REAL, gap_until REAL, error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS recovery_audit (
                id INTEGER PRIMARY KEY, at REAL NOT NULL, owner TEXT NOT NULL,
                entity TEXT NOT NULL, reference TEXT NOT NULL, decision TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_context (
                binding TEXT PRIMARY KEY REFERENCES bindings(id), workspace TEXT NOT NULL,
                project_id TEXT NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS preferences (
                owner TEXT NOT NULL, profile TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(owner,profile,name)
            );
        """)

    def observation(self, binding: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM observations WHERE binding=?", (binding,)).fetchone()
        return dict(row) if row else None

    def observe(self, binding: str, view: SessionView) -> dict[str, Any]:
        previous = self.observation(binding)
        now = time.time()
        digest = hashlib.sha256(
            json.dumps(
                [(s.index, s.kind, s.status, s.text, s.fingerprint) for s in view.steps]
            ).encode()
        ).hexdigest()
        gap = previous["gap_since"] if previous else None
        until = previous["gap_until"] if previous else None
        # A snapshot can reconcile current state, but never prove complete event replay.
        if previous and (previous["error"] or now - previous["observed"] > 60):
            gap = gap or previous["observed"]
            until = now
        if previous and len(view.steps) < previous["steps"]:
            gap, until = gap or previous["observed"], now
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?,?,?)",
                (binding, now, view.status, len(view.steps), digest, gap, until, ""),
            )
        return self.observation(binding) or {}

    def unavailable(self, binding: str) -> None:
        previous = self.observation(binding)
        now = time.time()
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?,?,?)",
                (
                    binding,
                    previous["observed"] if previous else now,
                    "unavailable",
                    previous["steps"] if previous else 0,
                    previous["digest"] if previous else "",
                    (previous["gap_since"] or previous["observed"]) if previous else now,
                    None,
                    "runtime_unavailable",
                ),
            )

    def context(self, binding: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM session_context WHERE binding=?", (binding,)
        ).fetchone()
        return dict(row) if row else {"workspace": "", "project_id": "", "updated": 0}

    def set_context(self, binding: str, workspace: str, project: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO session_context VALUES(?,?,?,?)",
                (binding, workspace, project, time.time()),
            )

    def preference(self, owner: str, profile: str, name: str, default: str = "") -> str:
        row = self.db.execute(
            "SELECT value FROM preferences WHERE owner=? AND profile=? AND name=?",
            (owner, profile, name),
        ).fetchone()
        return str(row[0]) if row else default

    def set_preference(self, owner: str, profile: str, name: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO preferences VALUES(?,?,?,?)", (owner, profile, name, value)
            )

    def decision(self, owner: str, entity: str, reference: str, decision: str) -> None:
        self.db.execute(
            "INSERT INTO recovery_audit(at,owner,entity,reference,decision) VALUES(?,?,?,?,?)",
            (time.time(), owner, entity, reference, decision),
        )
