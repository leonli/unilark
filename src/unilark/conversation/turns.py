"""One live progress message per turn; final answers arrive as regular new messages."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from unilark.adapters.sidecars.views import SessionView, StepView
from unilark.conversation.channel import Owner
from unilark.policy.redact import Redactor
from unilark.projection.cards import card
from unilark.projection.rich_text import reply_cards
from unilark.store.gateway import GatewayStore


class Turns:
    def __init__(
        self,
        store: GatewayStore,
        owner: Owner,
        redactor: Redactor,
        save: Callable[[str, str | None, list[dict[str, Any]]], None],
    ) -> None:
        self.store, self.owner, self.redactor, self.save = store, owner, redactor, save
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_layouts (
                binding TEXT PRIMARY KEY REFERENCES bindings(id), baseline INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_turns (
                id TEXT PRIMARY KEY, binding TEXT NOT NULL REFERENCES bindings(id),
                request TEXT, start INTEGER, status TEXT NOT NULL DEFAULT 'OPEN',
                created REAL NOT NULL, UNIQUE(binding,request)
            );
        """)

    def baseline(self, binding: str, room: dict[str, Any]) -> int:
        row = self.store.db.execute(
            "SELECT baseline FROM chat_layouts WHERE binding=?", (binding,)
        ).fetchone()
        if row:
            return int(row[0])
        baseline = int(room["baseline"])
        # Upgrade at an idle safe point. Existing chat history is left where it is.
        legacy = self.store.db.execute(
            "SELECT 1 FROM card_groups WHERE owner=? AND name=?",
            (self.owner.key, "room:" + room["chat"] + ":state:" + binding),
        ).fetchone()
        previous = self.store.journal.observation(binding)
        if legacy and previous:
            baseline = max(baseline, int(previous["steps"]) - 1)
        with self.store.db:
            self.store.db.execute("INSERT INTO chat_layouts VALUES(?,?)", (binding, baseline))
        return baseline

    def rows(self, binding: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.store.db.execute(
                "SELECT * FROM chat_turns WHERE binding=? ORDER BY created,rowid", (binding,)
            )
        ]

    def ensure(self, binding: str, identity: str, request: str | None, start: int | None) -> None:
        with self.store.db:
            self.store.db.execute(
                "INSERT OR IGNORE INTO chat_turns(id,binding,request,start,created) "
                "VALUES(?,?,?,?,?)",
                (identity, binding, request, start, time.time()),
            )
            if start is not None:
                self.store.db.execute(
                    "UPDATE chat_turns SET start=? WHERE id=? AND status='OPEN'", (start, identity)
                )

    @staticmethod
    def key(turn: dict[str, Any]) -> str:
        return "chat-turn:" + str(turn["id"])

    def progress(
        self,
        turn: dict[str, Any],
        title: str,
        detail: str = "",
        buttons: list[tuple[str, str, str]] | None = None,
    ) -> None:
        payload = card(title, "", buttons)
        payload.pop("header")
        payload["elements"][0] = {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": title + ("\n" + self.redactor.text(detail) if detail else ""),
            },
        }
        self.save(self.key(turn), turn["binding"], [payload])

    def finish(self, turn: dict[str, Any], text: str, *, answer: bool = True) -> None:
        if answer:
            # A new final message appears at the bottom of the conversation, even if the
            # user sent more input while this turn was working. Do not rely on card edits
            # being treated by the client as a newly received answer.
            if self.store.db.execute(
                "SELECT 1 FROM cards WHERE id=?",
                (self.store.card_id(self.owner, self.key(turn) + ":0"),),
            ).fetchone():
                self.progress(turn, "本轮结束")
            self.save(
                self.key(turn) + ":answer",
                turn["binding"],
                reply_cards("", self.redactor.text(text), "", minimal=True),
            )
        else:
            self.progress(turn, text)
        with self.store.db:
            self.store.db.execute("UPDATE chat_turns SET status='FINAL' WHERE id=?", (turn["id"],))

    def unavailable(self, binding: str) -> None:
        for turn in self.rows(binding):
            if turn["status"] == "OPEN":
                self.progress(turn, "连接暂时中断", "正在重新连接，任务是否结束尚未确认。")

    def project(
        self, session: dict[str, Any], room: dict[str, Any], view: SessionView, *, can_stop: bool
    ) -> None:
        binding = session["id"]
        baseline = self.baseline(binding, room)
        operations = {
            o["request_id"]: o
            for o in self.store.operations(self.owner)
            if o["binding_id"] == binding
        }
        starts: list[tuple[StepView, str]] = []
        for step in view.steps:
            if step.index <= baseline or step.kind != "user":
                continue
            own = next((operations[r] for r in step.operation_ids if r in operations), None)
            if own and own["kind"] == "steer":
                continue
            identity = (
                own["request_id"]
                if own
                else hashlib.sha256(
                    json.dumps([binding, step.index, step.text]).encode()
                ).hexdigest()
            )
            self.ensure(binding, identity, own["request_id"] if own else None, step.index)
            starts.append((step, identity))
        visible = [s for s in view.steps if s.index > baseline]
        rows = self.rows(binding)
        if visible and not starts and not any(r["status"] == "FINAL" for r in rows):
            open_turn = next((r for r in rows if r["status"] == "OPEN"), None)
            identity = (
                open_turn["id"]
                if open_turn
                else hashlib.sha256(f"{binding}:partial:{baseline}".encode()).hexdigest()
            )
            self.ensure(
                binding,
                identity,
                open_turn["request"] if open_turn else None,
                min(s.index for s in visible) - 1,
            )
            rows = self.rows(binding)
        # Some providers temporarily omit user steps; maintain a stable key for their input tag.
        pending = [
            o
            for o in operations.values()
            if o["kind"] == "input"
            and o["state"] in ("QUEUED", "SUBMITTING", "UNKNOWN", "ACCEPTED")
            and not any(r["request"] == o["request_id"] for r in rows)
        ]
        if pending and not any(r["status"] == "OPEN" for r in rows):
            # Do not surface historical accepted inputs when upgrading an existing room.
            pending = [o for o in pending if o["state"] != "ACCEPTED"]
            if pending:
                first = min(pending, key=lambda o: o["ordinal"])
                self.ensure(binding, first["request_id"], first["request_id"], None)
        queued = sum(o["state"] == "QUEUED" for o in operations.values())
        for turn in self.rows(binding):
            if turn["status"] != "OPEN":
                continue
            op = operations.get(turn["request"])
            if turn["start"] is None:
                state = op["state"] if op else "UNKNOWN"
                if state in ("REJECTED", "CANCELLED", "CANCELED", "DISMISSED"):
                    self.finish(turn, "这条任务没有继续提交。可在 /status 查看详情。", answer=False)
                elif state in ("UNKNOWN", "SUBMITTING"):
                    self.progress(turn, "正在确认任务状态", "为避免重复执行，暂缓后续任务。")
                else:
                    self.progress(
                        turn,
                        "已收到，等待开始" if state == "QUEUED" else "正在思考…",
                        f"还有 {max(0, queued - 1)} 条消息排队。" if queued > 1 else "",
                    )
                continue
            end = next((s.index for s, _ in starts if s.index > turn["start"]), None)
            steps = [
                s for s in view.steps if s.index > turn["start"] and (end is None or s.index < end)
            ]
            finished = end is not None or view.idle
            if finished:
                last_tool = max((s.index for s in steps if s.kind == "tool"), default=turn["start"])
                answers = [
                    s.text
                    for s in steps
                    if s.kind == "assistant"
                    and s.text.strip()
                    and s.index > last_tool
                    and s.status == "done"
                ]
                if answers:
                    self.finish(turn, "\n\n".join(answers))
                else:
                    stopped = any(
                        s.status in ("canceled", "cancelled", "interrupted") for s in steps
                    )
                    if end is None:
                        for row in self.store.db.execute(
                            "SELECT body FROM inbox WHERE binding=? AND action='stop' "
                            "AND status='DONE'",
                            (binding,),
                        ):
                            control = json.loads(row[0]) if row[0] else {}
                            stopped |= bool(
                                control.get("stop_idle_confirmed")
                                and control.get("stop_was_busy")
                                and control.get("stop_anchor") == view.anchor
                            )
                    failed = any(s.status == "error" for s in steps)
                    self.finish(
                        turn,
                        "本轮已停止。"
                        if stopped
                        else "任务遇到问题，暂未收到最终答复。"
                        if failed
                        else "本轮已结束，未收到最终答复。",
                        answer=False,
                    )
                continue
            waiting = next(
                (
                    s
                    for s in reversed(steps)
                    if s.status == "waiting" and (s.permission or s.questions)
                ),
                None,
            )
            active = next(
                (s for s in reversed(steps) if s.status in ("running", "generating", "waiting")),
                None,
            )
            title = "等待你确认" if waiting else "正在思考…"
            if active and active.kind == "tool" and not waiting:
                title = {
                    "run_command": "正在执行命令…",
                    "write_to_file": "正在写文件…",
                    "replace_file_content": "正在修改文件…",
                    "multi_replace_file_content": "正在修改文件…",
                    "view_file": "正在查看文件…",
                    "grep_search": "正在查找内容…",
                    "search_web": "正在查找资料…",
                    "read_url_content": "正在阅读资料…",
                }.get(active.tool, "正在处理…")
            detail = f"还有 {queued} 条消息排队。" if queued else ""
            if session["queue_state"] == "PAUSED":
                detail = "后续队列已暂停。"
            if active and active.command and not waiting:
                preview = " ".join(self.redactor.text(active.command).split())
                preview = preview[:177] + "…" if len(preview) > 180 else preview
                detail = preview + ("\n" + detail if detail else "")
            buttons = []
            if can_stop:
                cid = self.store.card_id(self.owner, self.key(turn) + ":0")
                interaction = self.store.interaction(
                    self.owner, binding, cid, "stop", view.anchor, -1
                )
                if interaction["status"] == "OPEN" and interaction["expires"] > time.time():
                    buttons = [("停止", interaction["token"], "stop")]
            self.progress(turn, title, detail, buttons)
