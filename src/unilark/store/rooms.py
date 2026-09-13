"""Stable conversation destinations and durable, reconciled group provisioning."""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any

from unilark.conversation.channel import Owner


class RoomStore:
    def __init__(self, db: sqlite3.Connection, *, initialize: bool = True) -> None:
        self.db = db
        if initialize:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS session_rooms (
                    binding TEXT PRIMARY KEY REFERENCES bindings(id), owner TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE, chat TEXT UNIQUE,
                    status TEXT NOT NULL DEFAULT 'QUEUED', created REAL NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', baseline INTEGER NOT NULL DEFAULT -1,
                    first_task TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0,
                    source_chat TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS event_chats (
                    owner TEXT NOT NULL, event TEXT NOT NULL, chat TEXT NOT NULL,
                    PRIMARY KEY(owner,event)
                );
                CREATE TABLE IF NOT EXISTS card_chats (
                    card TEXT PRIMARY KEY REFERENCES cards(id), chat TEXT NOT NULL
                );
            """)

    def get(self, owner: Owner, binding: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM session_rooms WHERE owner=? AND binding=?", (owner.key, binding)
        ).fetchone()
        return dict(row) if row else None

    def all(self, owner: Owner) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM session_rooms WHERE owner=? ORDER BY created", (owner.key,)
            )
        ]

    def add(self, owner: Owner, binding: str, first_task: str = "", source_chat: str = "") -> None:
        """Part of the caller's transaction, including initial input and native binding."""
        self.db.execute(
            "INSERT OR IGNORE INTO session_rooms"
            "(binding,owner,request_id,created,first_task,source_chat) VALUES(?,?,?,?,?,?)",
            (
                binding,
                owner.key,
                str(uuid.uuid4()),
                time.time(),
                first_task,
                source_chat or owner.chat,
            ),
        )

    def state(self, binding: str, status: str, reason: str = "", **values: Any) -> None:
        if set(values) - {"chat", "baseline", "retry_at", "first_task"}:
            raise ValueError("Invalid room fields")
        fields = {"status": status, "reason": reason, **values}
        with self.db:
            self.db.execute(
                "UPDATE session_rooms SET "  # noqa: S608 -- field names allowlisted above
                + ",".join(k + "=?" for k in fields)
                + " WHERE binding=?",
                (*fields.values(), binding),
            )

    def binding(self, owner: Owner, chat: str, *, ready: bool = True) -> str | None:
        row = self.db.execute(
            "SELECT binding FROM session_rooms WHERE owner=? AND chat=? "
            "AND (?=0 OR status='READY')",
            (owner.key, chat, ready),
        ).fetchone()
        return str(row[0]) if row else None

    def remember(self, owner: Owner, event: str, chat: str) -> bool:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO event_chats VALUES(?,?,?)", (owner.key, event, chat)
            )
        return self.event_chat(owner, event) == chat

    def event_chat(self, owner: Owner, event: str) -> str:
        row = self.db.execute(
            "SELECT chat FROM event_chats WHERE owner=? AND event=?", (owner.key, event)
        ).fetchone()
        return str(row[0]) if row else owner.chat

    def copy_event(self, owner: Owner, source: str, target: str) -> None:
        self.remember(owner, target, self.event_chat(owner, source))

    def card_chat(self, owner: Owner, card_id: str) -> str:
        row = self.db.execute("SELECT chat FROM card_chats WHERE card=?", (card_id,)).fetchone()
        return str(row[0]) if row else owner.chat

    def set_card(self, card_id: str, chat: str) -> None:
        # Existing messages never silently change destination.
        self.db.execute("INSERT OR IGNORE INTO card_chats VALUES(?,?)", (card_id, chat))

    def output_chat(self, owner: Owner, binding: str | None) -> str:
        room = self.get(owner, binding) if binding else None
        return str(room["chat"]) if room and room["chat"] else owner.chat

    def paused(self, owner: Owner, binding: str) -> bool:
        room = self.get(owner, binding)
        return room is not None and room["status"] != "READY"
