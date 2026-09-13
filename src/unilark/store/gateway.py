"""Durable routing, control intentions, interactions and card delivery."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from unilark.conversation.channel import Owner
from unilark.store.journal import Journal
from unilark.store.ledger import Ledger
from unilark.store.rooms import RoomStore


class GatewayStore(Ledger):
    def __init__(self, path: Path, *, readonly: bool = False) -> None:
        super().__init__(path, readonly=readonly)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            self.close()
            raise ValueError("Unsupported database schema; use the matching Unilark version")
        if readonly:
            self.journal = Journal(self.db, initialize=False)
            self.rooms = RoomStore(self.db, initialize=False)
            return
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS owner (account TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS session_meta (
                binding TEXT PRIMARY KEY REFERENCES bindings(id), owner TEXT NOT NULL,
                state TEXT NOT NULL, title TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS selection (owner TEXT PRIMARY KEY, binding TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox (
                owner TEXT NOT NULL, event TEXT NOT NULL, action TEXT NOT NULL,
                binding TEXT, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED',
                PRIMARY KEY(owner,event)
            );
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, binding TEXT,
                payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                delivered INTEGER NOT NULL DEFAULT 0, message_id TEXT UNIQUE,
                state TEXT NOT NULL DEFAULT 'READY', attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS interactions (
                token TEXT PRIMARY KEY, owner TEXT NOT NULL, binding TEXT NOT NULL,
                card TEXT NOT NULL, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                step INTEGER NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
                UNIQUE(binding,kind,fingerprint)
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, at REAL NOT NULL, reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gateway_health (
                owner TEXT NOT NULL, profile TEXT NOT NULL, updated REAL NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(owner,profile)
            );
            CREATE TABLE IF NOT EXISTS validation (
                owner TEXT NOT NULL, profile TEXT NOT NULL, capability TEXT NOT NULL,
                updated REAL NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(owner,profile,capability)
            );
            CREATE TABLE IF NOT EXISTS card_groups (
                owner TEXT NOT NULL, name TEXT NOT NULL, parts INTEGER NOT NULL,
                PRIMARY KEY(owner,name)
            );
        """)
        self.journal = Journal(self.db)
        self.rooms = RoomStore(self.db)
        # Old writers must not update group cards without destination/member checks.
        with self.db:
            self.db.execute("PRAGMA user_version=3")

    def accept_session(
        self,
        owner: Owner,
        event: str,
        profile: str,
        native: str,
        action: str,
        title: str,
        workspace: str = "",
        *,
        room: bool = False,
        first_task: str = "",
    ) -> str:
        """Binding, selection and intent survive or roll back together."""
        uuid.UUID(native)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO bindings(id,profile,native_id) VALUES(?,?,?)",
                (str(uuid.uuid4()), profile, native),
            )
            binding = str(
                self.db.execute(
                    "SELECT id FROM bindings WHERE profile=? AND native_id=?", (profile, native)
                ).fetchone()["id"]
            )
            prior = self.db.execute(
                "SELECT owner,state FROM session_meta WHERE binding=?", (binding,)
            ).fetchone()
            if prior and prior["owner"] != owner.key:
                raise ValueError("Binding belongs to another owner")
            if prior and prior["state"] != "ACTIVE":
                raise ValueError("Existing session is not active; use /resume before attaching")
            self.db.execute(
                "INSERT OR IGNORE INTO session_meta VALUES(?,?,?,?)",
                (binding, owner.key, "CREATING" if action == "create" else "ATTACHING", title),
            )
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO inbox(owner,event,action,binding,body) VALUES(?,?,?,?,?)",
                (owner.key, event, action, binding, ""),
            )
            if inserted.rowcount:
                if room:
                    self.rooms.add(owner, binding, first_task, self.rooms.event_chat(owner, event))
                if action == "create":
                    self.db.execute(
                        "INSERT OR IGNORE INTO session_context VALUES(?,?,?,?)",
                        (binding, workspace, "", time.time()),
                    )
                self.db.execute(
                    "INSERT OR REPLACE INTO selection VALUES(?,?)", (owner.key, binding)
                )
        return binding

    def set_owner(self, owner: Owner) -> None:
        if not all((owner.account, owner.tenant, owner.user, owner.chat)):
            raise ValueError("Incomplete owner")
        with self.db:
            prior = self.db.execute(
                "SELECT data FROM owner WHERE account=?", (owner.account,)
            ).fetchone()
            if prior and prior["data"] != owner.key:
                raise ValueError("Owner replacement requires an explicit migration")
            self.db.execute("INSERT OR IGNORE INTO owner VALUES(?,?)", (owner.account, owner.key))

    def heartbeat(self, owner: Owner, profile: str, payload: dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO gateway_health VALUES(?,?,?,?)",
                (owner.key, profile, time.time(), json.dumps(payload, sort_keys=True)),
            )

    def health(self, owner: Owner, profile: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT updated,payload FROM gateway_health WHERE owner=? AND profile=?",
            (owner.key, profile),
        ).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = json.loads(row["payload"])
        result["updated_at"] = row["updated"]
        return result

    def record_validation(
        self, owner: Owner, profile: str, capability: str, evidence: dict[str, Any]
    ) -> None:
        """Explicit acceptance evidence. A heartbeat must never mark tests as passed."""
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO validation VALUES(?,?,?,?,?)",
                (owner.key, profile, capability, time.time(), json.dumps(evidence)),
            )

    def validations(self, owner: Owner, profile: str) -> dict[str, Any]:
        return {
            r["capability"]: {"checked_at": r["updated"], **json.loads(r["payload"])}
            for r in self.db.execute(
                "SELECT * FROM validation WHERE owner=? AND profile=?", (owner.key, profile)
            )
        }

    def owner(self, account: str) -> Owner | None:
        row = self.db.execute("SELECT data FROM owner WHERE account=?", (account,)).fetchone()
        return Owner(*json.loads(row["data"])) if row else None

    def audit(self, reason: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO audit(at,reason) VALUES(?,?)", (time.time(), reason))

    def add_session(self, owner: Owner, profile: str, native: str, state: str, title: str) -> str:
        binding = self.bind(profile, native)
        with self.db:
            old = self.db.execute(
                "SELECT owner,state FROM session_meta WHERE binding=?", (binding,)
            ).fetchone()
            if old and old["owner"] != owner.key:
                raise ValueError("Binding already belongs to another owner")
            if old and old["state"] != "ACTIVE":
                raise ValueError("Existing session is not active; use /resume before attaching")
            self.db.execute(
                "INSERT OR IGNORE INTO session_meta VALUES(?,?,?,?)",
                (binding, owner.key, state, title),
            )
            self.db.execute("INSERT OR REPLACE INTO selection VALUES(?,?)", (owner.key, binding))
        return binding

    def session(self, owner: Owner, binding: str) -> dict[str, Any]:
        row = self.db.execute(
            """SELECT b.*, m.state,m.title,m.owner FROM bindings b
            JOIN session_meta m ON m.binding=b.id WHERE b.id=? AND m.owner=?""",
            (binding, owner.key),
        ).fetchone()
        if not row:
            raise ValueError("Unknown session for this owner")
        return dict(row)

    def sessions(self, owner: Owner) -> list[dict[str, Any]]:
        return [
            self.session(owner, r["binding"])
            for r in self.db.execute(
                "SELECT binding FROM session_meta WHERE owner=? ORDER BY rowid", (owner.key,)
            )
        ]

    def target(self, owner: Owner, reply_to: str | None = None) -> str | None:
        if reply_to:
            row = self.db.execute(
                "SELECT binding FROM cards WHERE owner=? AND message_id=?", (owner.key, reply_to)
            ).fetchone()
            if not row or not row["binding"]:
                raise ValueError("Unknown quoted message; refuse to guess a session")
        else:
            row = self.db.execute(
                "SELECT binding FROM selection WHERE owner=?", (owner.key,)
            ).fetchone()
        return str(row["binding"]) if row else None

    def switch(self, owner: Owner, binding: str) -> None:
        if self.session(owner, binding)["state"] != "ACTIVE":
            raise ValueError("Session is not active")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO selection VALUES(?,?)", (owner.key, binding))

    def session_state(self, binding: str, state: str) -> None:
        with self.db:
            self.db.execute("UPDATE session_meta SET state=? WHERE binding=?", (state, binding))

    def receive(
        self, owner: Owner, event: str, action: str, binding: str | None, body: str
    ) -> bool:
        with self.db:
            row = self.db.execute(
                "INSERT OR IGNORE INTO inbox(owner,event,action,binding,body) VALUES(?,?,?,?,?)",
                (owner.key, event, action, binding, body),
            )
            if action == "stop" and row.rowcount == 1:
                self.db.execute("UPDATE bindings SET queue_state='PAUSED' WHERE id=?", (binding,))
                self.db.execute(
                    "UPDATE inbox SET status='REJECTED' WHERE binding=? "
                    "AND action='continue' AND status='QUEUED'",
                    (binding,),
                )
        return row.rowcount == 1

    def seen(self, owner: Owner, event: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM inbox WHERE owner=? AND event=?", (owner.key, event)
            ).fetchone()
            is not None
        )

    def accept_input(self, owner: Owner, event: str, binding: str, text: str, kind: str) -> None:
        if self.session(owner, binding)["state"] not in ("ACTIVE", "CREATING", "ATTACHING"):
            raise ValueError("会话已归档或不可用；先显式恢复会话。")
        if kind not in ("input", "steer") or not text.strip():
            raise ValueError("Invalid input")
        with self.db:
            inserted = self.db.execute(
                """INSERT OR IGNORE INTO inbox(owner,event,action,binding,body,status)
                VALUES(?,?,?,?,?,'DONE')""",
                (owner.key, event, kind, binding, text),
            )
            if not inserted.rowcount:
                return
            self.db.execute(
                """INSERT OR IGNORE INTO operations
                (request_id,binding_id,source_scope,source_id,content,kind) VALUES(?,?,?,?,?,?)""",
                (str(uuid.uuid4()), binding, owner.key, event, text, kind),
            )

    def operations(self, owner: Owner) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM operations WHERE source_scope=? ORDER BY ordinal", (owner.key,)
            )
        ]

    def requeue_unsent(self, request: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE operations SET state='QUEUED' WHERE request_id=? AND state='SUBMITTING'",
                (request,),
            )

    def cancel_queued(self, owner: Owner, request: str) -> bool:
        with self.db:
            changed = self.db.execute(
                "UPDATE operations SET state='REJECTED' WHERE request_id=? "
                "AND source_scope=? AND state='QUEUED'",
                (request, owner.key),
            )
        return changed.rowcount == 1

    def blocked(self, binding: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM inbox WHERE binding=? AND status IN ('UNKNOWN','SUBMITTING') "
                "AND action IN ('create','attach','stop','resolve','answer')",
                (binding,),
            ).fetchone()
            is not None
        )

    def delivery_health(self, owner: Owner) -> dict[str, int]:
        return {
            r["state"]: r["n"]
            for r in self.db.execute(
                "SELECT state,count(*) AS n FROM cards WHERE owner=? "
                "AND revision>delivered GROUP BY state",
                (owner.key,),
            )
        }

    def requests(self, owner: Owner) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM inbox WHERE owner=? AND status='QUEUED' "
                "ORDER BY (action IN ('stop','resolve','answer')) DESC,rowid",
                (owner.key,),
            )
        ]

    def control_observation(self, owner: Owner, event: str, values: dict[str, Any]) -> None:
        with self.db:
            row = self.db.execute(
                "SELECT body FROM inbox WHERE owner=? AND event=?", (owner.key, event)
            ).fetchone()
            body = json.loads(row[0]) if row and row[0] else {}
            body.update(values)
            self.db.execute(
                "UPDATE inbox SET body=? WHERE owner=? AND event=?",
                (json.dumps(body), owner.key, event),
            )

    def request_state(self, owner: Owner, event: str, state: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE inbox SET status=? WHERE owner=? AND event=?", (state, owner.key, event)
            )

    def put_card(
        self,
        key: str,
        owner: Owner,
        binding: str | None,
        payload: dict[str, Any],
        *,
        chat: str | None = None,
    ) -> str:
        card_id = self.card_id(owner, key)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.db:
            existed = self.db.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone()
            self.db.execute(
                """INSERT INTO cards(id,owner,binding,payload) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,revision=cards.revision+1
                WHERE cards.payload!=excluded.payload""",
                (card_id, owner.key, binding, encoded),
            )
            self.rooms.set_card(card_id, owner.chat if existed else (chat or owner.chat))
        return card_id

    @staticmethod
    def card_id(owner: Owner, key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, owner.key + ":" + key))

    def close_interactions(self, binding: str, live: set[str]) -> None:
        with self.db:
            for row in self.db.execute(
                "SELECT token,fingerprint FROM interactions WHERE binding=? AND status='OPEN'",
                (binding,),
            ).fetchall():
                if row["fingerprint"] not in live:
                    self.db.execute(
                        "UPDATE interactions SET status='CLOSED' WHERE token=?", (row["token"],)
                    )

    def ambiguous_operations(self, requests: list[str], binding: str) -> None:
        with self.db:
            self.db.executemany(
                "UPDATE operations SET state='UNKNOWN' WHERE request_id=?", [(r,) for r in requests]
            )
            self.db.execute("UPDATE bindings SET queue_state='PAUSED' WHERE id=?", (binding,))

    def unknown_requests(self, owner: Owner) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM inbox WHERE owner=? AND status='UNKNOWN'", (owner.key,)
            )
        ]

    def dirty_cards(self, owner: Owner) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.execute(
                """SELECT c.* FROM cards c
            LEFT JOIN card_chats cc ON cc.card=c.id
            LEFT JOIN session_rooms r ON r.chat=cc.chat AND r.owner=c.owner
            WHERE c.owner=? AND c.revision>c.delivered
            AND c.state NOT IN ('UNKNOWN','BLOCKED','SENDING','DISMISSED') AND c.retry_at<=?
            AND (cc.chat IS NULL OR cc.chat=? OR r.status='READY')
            ORDER BY EXISTS(SELECT 1 FROM interactions i WHERE i.card=c.id
                            AND i.kind='permission' AND i.status='OPEN') DESC, c.rowid LIMIT 30""",
                (owner.key, time.time(), owner.chat),
            )
        ]

    def group_size(self, owner: Owner, name: str, parts: int) -> int:
        with self.db:
            old = self.db.execute(
                "SELECT parts FROM card_groups WHERE owner=? AND name=?", (owner.key, name)
            ).fetchone()
            self.db.execute(
                "INSERT OR REPLACE INTO card_groups VALUES(?,?,?)", (owner.key, name, parts)
            )
        return int(old["parts"]) if old else 0

    def delivery_started(self, card: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE cards SET state='SENDING',attempts=attempts+1 WHERE id=?", (card,)
            )

    def delivery_result(
        self, card: str, revision: int, state: str, message_id: str | None, delay: float = 5
    ) -> None:
        with self.db:
            if state == "SENT" and message_id:
                self.db.execute(
                    "UPDATE cards SET state='READY',message_id=?,delivered=?,attempts=0 WHERE id=?",
                    (message_id, revision, card),
                )
            else:
                self.db.execute(
                    "UPDATE cards SET state=?,retry_at=? WHERE id=?",
                    (state, time.time() + delay, card),
                )

    def interaction(
        self, owner: Owner, binding: str, card: str, kind: str, fingerprint: str, step: int
    ) -> dict[str, Any]:
        with self.db:
            if kind == "stop":
                self.db.execute(
                    "DELETE FROM interactions WHERE binding=? AND kind='stop' "
                    "AND fingerprint=? AND status='OPEN' AND expires<=?",
                    (binding, fingerprint, time.time()),
                )
            self.db.execute(
                """INSERT OR IGNORE INTO interactions
                (token,owner,binding,card,kind,fingerprint,step,expires) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    owner.key,
                    binding,
                    card,
                    kind,
                    fingerprint,
                    step,
                    time.time() + 300,
                ),
            )
        return dict(
            self.db.execute(
                "SELECT * FROM interactions WHERE binding=? AND kind=? AND fingerprint=?",
                (binding, kind, fingerprint),
            ).fetchone()
        )

    def consume(self, owner: Owner, token: str, message: str, decision: str, event: str) -> bool:
        with self.db:
            if not event or self.seen(owner, event):
                return False
            row = self.db.execute(
                """SELECT i.* FROM interactions i JOIN cards c ON c.id=i.card
                WHERE i.token=? AND i.owner=? AND c.message_id=?
                AND i.status='OPEN' AND i.expires>?""",
                (token, owner.key, message, time.time()),
            ).fetchone()
            if not row or (row["kind"] == "permission" and decision not in ("allow", "deny")):
                return False
            if row["kind"] == "stop" and decision != "stop":
                return False
            if row["kind"] == "question" and not (
                decision.startswith("answer:") or decision == "cancel"
            ):
                return False
            self.db.execute("UPDATE interactions SET status='CLAIMED' WHERE token=?", (token,))
            action = {"permission": "resolve", "question": "answer", "stop": "stop"}[row["kind"]]
            self.db.execute(
                """INSERT OR IGNORE INTO inbox(owner,event,action,binding,body)
                VALUES(?,?,?,?,?)""",
                (
                    owner.key,
                    event,
                    action,
                    row["binding"],
                    json.dumps(
                        {
                            "token": token,
                            "step": row["step"],
                            "fingerprint": row["fingerprint"],
                            "decision": decision,
                        }
                    ),
                ),
            )
            if action == "stop":
                self.db.execute(
                    "UPDATE bindings SET queue_state='PAUSED' WHERE id=?", (row["binding"],)
                )
                self.db.execute(
                    "UPDATE inbox SET status='REJECTED' WHERE binding=? "
                    "AND action='continue' AND status='QUEUED'",
                    (row["binding"],),
                )
        return True

    def expire_permission(self, owner: Owner, interaction: dict[str, Any]) -> None:
        with self.db:
            changed = self.db.execute(
                "UPDATE interactions SET status='EXPIRED' WHERE token=? AND owner=? "
                "AND kind IN ('permission','question') AND status='OPEN' AND expires<=?",
                (interaction["token"], owner.key, time.time()),
            )
            if changed.rowcount:
                self.db.execute(
                    "INSERT OR IGNORE INTO inbox(owner,event,action,binding,body) "
                    "VALUES(?,?,?,?,?)",
                    (
                        owner.key,
                        "expiry:" + interaction["token"],
                        "answer" if interaction["kind"] == "question" else "resolve",
                        interaction["binding"],
                        json.dumps(
                            {
                                "fingerprint": interaction["fingerprint"],
                                "step": interaction["step"],
                                "decision": "cancel"
                                if interaction["kind"] == "question"
                                else "deny",
                            }
                        ),
                    ),
                )

    def recover_gateway(self) -> None:
        self.recover()
        with self.db:
            self.db.execute("UPDATE inbox SET status='UNKNOWN' WHERE status='SUBMITTING'")
            self.db.execute(
                "UPDATE bindings SET queue_state='PAUSED' WHERE id IN "
                "(SELECT binding FROM inbox WHERE status='UNKNOWN' UNION "
                "SELECT binding_id FROM operations WHERE state='UNKNOWN')"
            )
            self.db.execute(
                "UPDATE session_meta SET state='UNKNOWN' WHERE state IN ('CREATING','ATTACHING') "
                "AND binding IN (SELECT binding FROM inbox WHERE status='UNKNOWN')"
            )
            self.db.execute(
                "UPDATE cards SET state=CASE WHEN message_id IS NULL "
                "THEN 'UNKNOWN' ELSE 'READY' END "
                "WHERE state='SENDING'"
            )

    def archive(self, owner: Owner, binding: str) -> None:
        session = self.session(owner, binding)
        if session["state"] != "ACTIVE" or self.blocked(binding):
            raise ValueError("会话不可归档；先处理待确认操作。")
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM operations WHERE binding_id=? AND state IN "
                "('QUEUED','SUBMITTING','UNKNOWN')",
                (binding,),
            ).fetchone():
                raise ValueError("会话仍有排队或待确认输入，先处理这些输入。")
            self.db.execute("UPDATE session_meta SET state='ARCHIVED' WHERE binding=?", (binding,))
            self.db.execute("UPDATE bindings SET queue_state='PAUSED' WHERE id=?", (binding,))
            self.db.execute(
                "DELETE FROM selection WHERE owner=? AND binding=?", (owner.key, binding)
            )
            self.db.execute(
                "UPDATE interactions SET status='CLOSED' WHERE binding=? AND status='OPEN'",
                (binding,),
            )

    def acknowledge_unknown(self, owner: Owner, entity: str, reference: str) -> None:
        """Operator accepts uncertainty; never claims that execution did not happen."""
        with self.db:
            if entity == "input":
                row = self.db.execute(
                    "SELECT binding_id FROM operations WHERE request_id=? AND source_scope=? "
                    "AND state='UNKNOWN'",
                    (reference, owner.key),
                ).fetchone()
                if not row:
                    raise ValueError("No UNKNOWN input for this owner")
                binding = str(row[0])
                self.db.execute(
                    "UPDATE operations SET state='DISMISSED' WHERE request_id=?", (reference,)
                )
            elif entity == "control":
                row = self.db.execute(
                    "SELECT binding,action FROM inbox WHERE event=? AND owner=? "
                    "AND status='UNKNOWN'",
                    (reference, owner.key),
                ).fetchone()
                if not row:
                    raise ValueError("No UNKNOWN control for this owner")
                binding = str(row[0])
                self.db.execute(
                    "UPDATE inbox SET status='DISMISSED' WHERE event=? AND owner=?",
                    (reference, owner.key),
                )
                if row[1] in ("create", "attach"):
                    self.db.execute(
                        "UPDATE session_meta SET state='UNAVAILABLE' WHERE binding=?", (binding,)
                    )
            elif entity == "card":
                row = self.db.execute(
                    "SELECT binding FROM cards WHERE id=? AND owner=? AND state='UNKNOWN'",
                    (reference, owner.key),
                ).fetchone()
                if not row:
                    raise ValueError("No UNKNOWN card for this owner")
                binding = str(row[0] or "")
                self.db.execute("UPDATE cards SET state='DISMISSED' WHERE id=?", (reference,))
            else:
                raise ValueError("Invalid recovery entity")
            self.db.execute("UPDATE bindings SET queue_state='PAUSED' WHERE id=?", (binding,))
            self.journal.decision(
                owner.key, entity, reference, "acknowledged_possible_execution_or_delivery"
            )

    def retry_delivery(self, owner: Owner, reference: str) -> None:
        with self.db:
            row = self.db.execute(
                "UPDATE cards SET state='READY',attempts=0,retry_at=0 WHERE id=? AND owner=? "
                "AND state='BLOCKED' AND message_id IS NOT NULL",
                (reference, owner.key),
            )
            if row.rowcount != 1:
                raise ValueError("Only a BLOCKED update with a known message ID may be retried")
            self.journal.decision(owner.key, "card", reference, "retry_known_message_update")

    def quoted_question(self, owner: Owner, message_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT i.* FROM interactions i JOIN cards c ON i.card=c.id "
            "WHERE c.message_id=? AND i.owner=? AND i.kind='question' "
            "ORDER BY i.rowid DESC LIMIT 1",
            (message_id, owner.key),
        ).fetchone()
        return dict(row) if row else None

    def answer_text(self, owner: Owner, message_id: str, event: str, text: str) -> bool:
        interaction = self.quoted_question(owner, message_id)
        if interaction is None:
            return False
        with self.db:
            if self.seen(owner, event):
                return True
            changed = self.db.execute(
                "UPDATE interactions SET status='CLAIMED' WHERE token=? AND owner=? "
                "AND status='OPEN' AND expires>?",
                (interaction["token"], owner.key, time.time()),
            )
            if changed.rowcount != 1:
                raise ValueError("该问题已处理或到期，回答未作为新任务执行。")
            self.db.execute(
                "INSERT INTO inbox(owner,event,action,binding,body) VALUES(?,?,?,?,?)",
                (
                    owner.key,
                    event,
                    "answer",
                    interaction["binding"],
                    json.dumps(
                        {
                            "step": interaction["step"],
                            "fingerprint": interaction["fingerprint"],
                            "decision": "text",
                            "text": text,
                        }
                    ),
                ),
            )
        return True
