"""Durable, message-bound UI actions. Optional tables remain compatible with schema 2."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from unilark.conversation.channel import Action, Owner
from unilark.store.gateway import GatewayStore


class PanelStore:
    def __init__(self, store: GatewayStore) -> None:
        self.store, self.db = store, store.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS ui_panels (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, key TEXT NOT NULL,
                mode TEXT NOT NULL, binding TEXT, page INTEGER NOT NULL DEFAULT 0,
                filter TEXT NOT NULL DEFAULT 'active', expires REAL NOT NULL,
                digest TEXT NOT NULL DEFAULT '', submitted INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ui_actions (
                token TEXT PRIMARY KEY, card TEXT NOT NULL, owner TEXT NOT NULL,
                body TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS ui_actions_card ON ui_actions(card);
        """)

    def open(
        self,
        owner: Owner,
        key: str,
        mode: str,
        binding: str | None = None,
        *,
        page: int = 0,
        filter: str = "active",
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO ui_panels(id,owner,key,mode,binding,page,filter,expires) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    self.store.card_id(owner, key),
                    owner.key,
                    key,
                    mode,
                    binding,
                    page,
                    filter,
                    time.time() + 86400,
                ),
            )

    def panels(self, owner: Owner) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM ui_panels WHERE owner=? AND expires>? ORDER BY rowid DESC LIMIT 12",
                (owner.key, time.time()),
            )
        ]

    def render(self, owner: Owner, panel: dict[str, Any], template: dict[str, Any]) -> None:
        encoded = json.dumps(template, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        if digest == panel["digest"]:
            return
        payload = json.loads(encoded)
        with self.db:
            # Retire actions before replacing the card. Delayed callbacks cannot execute new work.
            self.db.execute("UPDATE ui_actions SET used=1 WHERE card=?", (panel["id"],))

            def bind(value: Any) -> None:
                if isinstance(value, dict):
                    if "_intent" in value:
                        intent = value.pop("_intent")
                        token = "ui_" + secrets.token_urlsafe(24)
                        self.db.execute(
                            "INSERT INTO ui_actions VALUES(?,?,?,?,?,0)",
                            (token, panel["id"], owner.key, json.dumps(intent), panel["expires"]),
                        )
                        value["value"] = {"token": token, "decision": "ui"}
                    for child in value.values():
                        bind(child)
                elif isinstance(value, list):
                    for child in value:
                        bind(child)

            bind(payload)
            # No nested GatewayStore transaction: payload and tokens commit together.
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            self.db.execute(
                "INSERT INTO cards(id,owner,binding,payload) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,revision=cards.revision+1",
                (panel["id"], owner.key, panel["binding"], body),
            )
            self.db.execute("UPDATE ui_panels SET digest=? WHERE id=?", (digest, panel["id"]))

    def consume(self, action: Action) -> bool:
        if (
            action.decision != "ui"
            or not action.event_id
            or self.store.seen(action.owner, action.event_id)
        ):
            return False
        with self.db:
            row = self.db.execute(
                "SELECT a.*,p.binding,p.mode FROM ui_actions a "
                "JOIN cards c ON c.id=a.card JOIN ui_panels p ON p.id=a.card "
                "WHERE a.token=? AND a.owner=? AND c.owner=? AND c.message_id=? "
                "AND a.used=0 AND a.expires>?",
                (action.token, action.owner.key, action.owner.key, action.message_id, time.time()),
            ).fetchone()
            if row is None:
                return False
            intent = json.loads(row["body"])
            if intent["op"] == "create":
                title = action.fields.get("title", "").strip()
                if set(action.fields) != {"title"} or not title or len(title) > 80:
                    raise ValueError("请填写 1–80 字的会话标题，然后重新提交。")
                intent["title"] = title
            elif action.fields:
                raise ValueError("此按钮不接受表单内容。")
            self.db.execute("UPDATE ui_actions SET used=1 WHERE token=?", (action.token,))
            self.db.execute(
                "INSERT INTO inbox(owner,event,action,binding,body) VALUES(?,?,'panel',?,?)",
                (action.owner.key, action.event_id, intent.get("binding"), json.dumps(intent)),
            )
            self.db.execute("UPDATE ui_panels SET digest='' WHERE id=?", (row["card"],))
            if intent["op"] == "create":
                # A form can create at most one session, including different callback event IDs.
                self.db.execute("UPDATE ui_panels SET submitted=1 WHERE id=?", (row["card"],))
                self.db.execute("UPDATE ui_actions SET used=1 WHERE card=?", (row["card"],))
        return True

    def switch(self, owner: Owner, event: str, binding: str) -> None:
        if self.store.session(owner, binding)["state"] != "ACTIVE":
            raise ValueError("会话已归档或暂不可用，请刷新会话面板。")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO selection VALUES(?,?)", (owner.key, binding))
            self.db.execute(
                "UPDATE inbox SET status='DONE' WHERE owner=? AND event=?", (owner.key, event)
            )

    def page(self, owner: Owner, card: str, page: int, filter: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE ui_panels SET page=?,filter=?,digest='' WHERE id=? AND owner=?",
                (page, filter, card, owner.key),
            )
