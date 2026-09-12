"""One-owner M1 gateway: durable intentions, snapshot reconciliation, safe delivery."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any

from unilark.adapters.sidecars.views import Busy, Runtime, SessionView
from unilark.conversation.channel import Action, Channel, Message, Owner
from unilark.policy.redact import Redactor
from unilark.projection.cards import card, chunks
from unilark.store.gateway import GatewayStore

HELP = (
    "/new [标题] · /attach 原生UUID · /sessions · /switch 会话ID\n"
    "/status · /stop · /continue · /steer 补充要求 · /cancel 请求ID\n"
    "普通文本排为下一项任务；引用卡片固定投给卡片原会话。"
)


class Hub:
    def __init__(
        self,
        store: GatewayStore,
        runtime: Runtime,
        channel: Channel,
        owner: Owner,
        profile: str,
        redactor: Redactor,
    ) -> None:
        self.store, self.runtime, self.channel = store, runtime, channel
        self.owner, self.profile, self.redactor = owner, profile, redactor
        self.views: dict[str, SessionView | None] = {}
        self.stopping = asyncio.Event()
        self.report_health: Callable[[], None] | None = None

    def notify(self, key: str, binding: str | None, title: str, text: str) -> None:
        safe = self.redactor.text(text)
        self.save_parts(key, binding, [card(self.redactor.text(title), p) for p in chunks(safe)])

    def save_parts(self, key: str, binding: str | None, payloads: list[dict[str, Any]]) -> None:
        previous = self.store.group_size(self.owner, key, len(payloads))
        for index, payload in enumerate(payloads):
            self.store.put_card(key + f":{index}", self.owner, binding, payload)
        for index in range(len(payloads), previous):
            self.store.put_card(
                key + f":{index}",
                self.owner,
                binding,
                card("内容已更新", "请查看同一回复的首张卡片。"),
            )

    async def accept(self, message: Message) -> None:
        if message.owner != self.owner or self.store.owner(self.owner.account) != self.owner:
            self.store.audit("unauthorized_message")
            return
        if not message.event_id or not (-60 <= time.time() - message.created_at <= 1800):
            self.store.audit("stale_or_invalid_message")
            return
        if self.store.seen(self.owner, message.event_id):
            return
        event = message.event_id
        if not message.supported:
            self.store.receive(self.owner, event, "noop", None, "")
            self.store.request_state(self.owner, event, "DONE")
            self.notify("reply:" + event, None, "暂不支持该消息", "当前实验版支持私聊纯文本。")
            return
        text = message.text.strip()
        if not text or len(text) > 100_000:
            self.store.audit("invalid_input_size")
            return
        binding: str | None = None
        try:
            binding = self.store.target(self.owner, message.reply_to)
            command, _, body = text.partition(" ")
            if not text.startswith("/"):
                command, body = "input", message.text
            else:
                command = command[1:]
            if command in ("help", "whoami"):
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                self.notify(
                    "reply:" + event,
                    binding,
                    "Unilark",
                    HELP if command == "help" else self.owner.user,
                )
            elif command in ("new", "attach"):
                native = str(uuid.uuid4()) if command == "new" else str(uuid.UUID(body.strip()))
                binding = self.store.accept_session(
                    self.owner,
                    event,
                    self.profile,
                    native,
                    "create" if command == "new" else "attach",
                    self.redactor.text(body or "新会话")[:80],
                )
                self.notify(
                    "reply:" + event,
                    binding,
                    "会话准备中",
                    f"会话 {binding}\n准备成功后自动提交排队输入。",
                )
            elif command in ("sessions", "status"):
                self.store.receive(self.owner, event, command, binding, "")
            elif command == "switch":
                self.store.switch(self.owner, body.strip())
                binding = body.strip()
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                self.notify("reply:" + event, binding, "已切换会话", binding)
            elif command == "cancel":
                ok = self.store.cancel_queued(self.owner, body.strip())
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                self.notify(
                    "reply:" + event,
                    binding,
                    "队列操作",
                    "已撤销尚未提交的输入。" if ok else "未撤销：请求不在待提交队列。",
                )
            elif command in ("input", "steer", "stop", "continue"):
                if not binding:
                    raise ValueError("请先 /new 或 /attach 原生会话UUID。")
                session = self.store.session(self.owner, binding)
                if session["profile"] != self.profile:
                    raise ValueError("会话属于另一个 AGY 实例，不能重绑。")
                if command in ("input", "steer"):
                    if not body.strip():
                        raise ValueError("/steer 后需要补充内容。")
                    self.store.accept_input(self.owner, event, binding, body, command)
                    self.notify(
                        "reply:" + event, binding, "输入已保存", "已进入本地队列；尚未开始执行。"
                    )
                else:
                    self.store.receive(self.owner, event, command, binding, "")
                    self.notify(
                        "reply:" + event,
                        binding,
                        "正在停止" if command == "stop" else "队列操作",
                        "停止意图已保存，队列已暂停；等待 AGY 确认。"
                        if command == "stop"
                        else "正在检查状态后继续本地队列。",
                    )
            else:
                raise ValueError("不支持该命令。\n" + HELP)
        except (ValueError, KeyError) as error:
            self.store.receive(self.owner, event, "noop", binding, "")
            self.store.request_state(self.owner, event, "DONE")
            self.notify("reply:" + event, binding, "未提交任务", str(error))

    async def action(self, action: Action) -> None:
        if action.owner != self.owner or not self.store.consume(
            self.owner, action.token, action.message_id, action.decision, action.event_id
        ):
            self.store.audit("rejected_or_expired_action")

    async def requests(self) -> None:
        for request in self.store.requests(self.owner):
            event, binding, action = request["event"], request["binding"], request["action"]
            self.store.request_state(self.owner, event, "SUBMITTING")
            writing = False
            try:
                if action in ("sessions", "status"):
                    sessions = self.store.sessions(self.owner)
                    text = "\n".join(
                        f"{s['id']} · {s['title']} · {s['state']} · 队列 {s['queue_state']}"
                        for s in sessions
                    )
                    text += "\n投递状态：" + json.dumps(
                        self.store.delivery_health(self.owner), ensure_ascii=False
                    )
                    self.notify(
                        "reply:" + event, binding, "会话状态", text or "暂无会话；发送 /new。"
                    )
                elif binding and action != "noop":
                    session = self.store.session(self.owner, binding)
                    if session["profile"] != self.profile:
                        raise ValueError("Configured runtime differs from this binding")
                    native = session["native_id"]
                    if action == "create":
                        writing = True
                        await self.runtime.create(native)
                        self.store.session_state(binding, "ACTIVE")
                        self.notify(
                            "reply:" + event,
                            binding,
                            "会话已建立",
                            f"会话 {binding}\n原生会话 {native}",
                        )
                    elif action == "attach":
                        view = await self.runtime.view(native)
                        if not view.steps:
                            raise ValueError("Empty history cannot prove an existing conversation")
                        self.store.session_state(binding, "ACTIVE")
                        self.notify("reply:" + event, binding, "已连接原会话", binding)
                    elif action == "continue":
                        if session["state"] != "ACTIVE":
                            raise ValueError("会话尚未确认可用。")
                        if self.store.blocked(binding):
                            raise ValueError("先处理状态待确认的控制操作。")
                        self.store.resume(binding)
                        self.notify("reply:" + event, binding, "队列已继续", "空闲后提交下一项。")
                    elif action == "stop":
                        if request["body"]:
                            intent = json.loads(request["body"])
                            if (await self.runtime.view(native)).anchor != intent["fingerprint"]:
                                raise ValueError("Old stop card belongs to an earlier turn")
                        writing = True
                        confirmed = await self.runtime.stop(native)
                        if not confirmed:
                            raise RuntimeError("Stop has not yet been confirmed")
                        self.notify(
                            "reply:" + event,
                            binding,
                            "已确认空闲",
                            "停止操作已确认，队列保持暂停。",
                        )
                    elif action == "resolve":
                        intent = json.loads(request["body"])
                        view = await self.runtime.view(native)
                        if not any(
                            s.index == intent["step"]
                            and s.permission
                            and s.status == "waiting"
                            and s.fingerprint == intent["fingerprint"]
                            for s in view.steps
                        ):
                            raise ValueError("权限请求已改变或已结束，旧按钮不再有效。")
                        writing = True
                        observed = await self.runtime.resolve_permission(
                            native,
                            intent["step"],
                            allow=intent["decision"] == "allow",
                            fingerprint=intent["fingerprint"],
                        )
                        self.notify(
                            "reply:" + event,
                            binding,
                            "决议已提交",
                            f"当前步骤状态：{observed}。执行结果以 AGY 后续状态为准。",
                        )
                self.store.request_state(self.owner, event, "DONE")
            except Exception as error:
                if not writing and isinstance(error, (ValueError, KeyError)):
                    self.store.request_state(self.owner, event, "REJECTED")
                    self.notify("reply:" + event, binding, "操作未执行", str(error))
                    if binding and action == "attach":
                        self.store.session_state(binding, "UNAVAILABLE")
                    continue
                self.store.request_state(self.owner, event, "UNKNOWN" if writing else "QUEUED")
                if binding:
                    self.store.pause(binding)
                    if action == "create" and writing:
                        self.store.session_state(binding, "UNKNOWN")
                self.notify(
                    "reply:" + event,
                    binding,
                    "状态待确认",
                    f"未得到完整确认（{type(error).__name__}）；队列已暂停。"
                    + ("外部写入不会自动重试。" if writing else "稍后重试读取状态。"),
                )

    async def reconcile_controls(self) -> None:
        for request in self.store.unknown_requests(self.owner):
            binding, action = request["binding"], request["action"]
            if not binding:
                self.store.request_state(self.owner, request["event"], "QUEUED")
                continue
            session = self.store.session(self.owner, binding)
            if session["profile"] != self.profile:
                continue
            if action not in ("create", "attach", "stop", "resolve"):
                self.store.request_state(self.owner, request["event"], "QUEUED")
                continue
            try:
                view = await self.runtime.view(session["native_id"])
            except Exception:
                self.store.audit("control_reconcile_read_failed")
                continue
            confirmed = False
            if action in ("create", "attach"):
                # Snapshot alone can be empty for an unknown UUID; history proves existence.
                confirmed = bool(view.steps)
                if confirmed:
                    self.store.session_state(binding, "ACTIVE")
            elif action == "stop":
                confirmed = view.idle
            elif action == "resolve":
                intent = json.loads(request["body"])
                current = [s for s in view.steps if s.index == intent["step"]]
                confirmed = bool(current) and (
                    current[0].fingerprint != intent["fingerprint"]
                    or current[0].status != "waiting"
                )
            if confirmed:
                self.store.request_state(self.owner, request["event"], "DONE")
                self.notify(
                    "reply:" + request["event"],
                    binding,
                    "状态已核对",
                    "原等待状态已结束；不推断审批或执行成功。队列保持暂停，/continue 可继续。",
                )

    def project(self, session: dict[str, Any], view: SessionView) -> None:
        binding = session["id"]
        own = {
            op["request_id"]: op
            for op in self.store.operations(self.owner)
            if op["binding_id"] == binding
        }
        hits = {
            rid: [s.index for s in view.steps if s.kind == "user" and rid in s.operation_ids]
            for rid in own
        }
        ambiguous = [rid for rid, indices in hits.items() if len(indices) > 1]
        if ambiguous:
            self.store.ambiguous_operations(ambiguous, binding)
        for rid, indices in hits.items():
            if len(indices) == 1 and own[rid]["state"] in ("UNKNOWN", "SUBMITTING"):
                self.store.finish(rid, "ACCEPTED", indices[0])
        live = {s.fingerprint for s in view.steps if s.permission and s.status == "waiting"}
        if not view.idle:
            live.add(view.anchor)
        self.store.close_interactions(binding, live)
        for step in view.steps:
            matched = [rid for rid in step.operation_ids if rid in own]
            if step.kind == "user" and matched:
                continue  # Its receipt already appears in Lark.
            if step.kind == "tool" and not (step.tool or step.status == "waiting"):
                continue
            text = step.text if step.kind != "tool" else step.tool
            title = {"user": "来自 AGY 的输入", "assistant": "AGY 回复", "tool": "AGY 工具"}.get(
                step.kind, "AGY"
            )
            title += " · " + step.status
            key = f"step:{binding}:{step.index}"
            safe = self.redactor.text(text)
            payloads = []
            for part, content in enumerate(chunks(safe)):
                card_key = key + f":{part}"
                payload = card(title, content + "\n\n会话 " + binding)
                cid = self.store.card_id(self.owner, card_key)
                if step.status == "waiting" and step.permission and part == 0:
                    interaction = self.store.interaction(
                        self.owner, binding, cid, "permission", step.fingerprint, step.index
                    )
                    buttons = []
                    if interaction["status"] == "OPEN" and interaction["expires"] > time.time():
                        buttons = [
                            ("仅本次允许", interaction["token"], "allow"),
                            ("拒绝", interaction["token"], "deny"),
                        ]
                    elif interaction["status"] == "OPEN":
                        self.store.expire_permission(self.owner, interaction)
                    payload = card(
                        "AGY 请求批准",
                        self.redactor.text(step.resource)[:2800]
                        + (
                            "\n仅本次生效；5 分钟未处理将提交拒绝。\n会话 "
                            if buttons
                            else "\n已处理或已到期，等待 AGY 状态更新。\n会话 "
                        )
                        + binding,
                        buttons,
                    )
                payloads.append(payload)
            self.save_parts(key, binding, payloads)
        state_key = "state:" + binding
        payload = card(
            "AGY · " + view.status, "会话 " + binding + "\n本地队列：" + session["queue_state"]
        )
        cid = self.store.card_id(self.owner, state_key + ":0")
        if not view.idle:
            interaction = self.store.interaction(self.owner, binding, cid, "stop", view.anchor, -1)
            if interaction["status"] == "OPEN" and interaction["expires"] > time.time():
                payload = card(
                    "AGY · " + view.status,
                    "会话 " + binding + "\n本地队列：" + session["queue_state"],
                    [("停止并暂停队列", interaction["token"], "stop")],
                )
        self.save_parts(state_key, binding, [payload])

    async def tick(self) -> None:
        await self.reconcile_controls()
        await self.requests()
        sessions = [
            s
            for s in self.store.sessions(self.owner)
            if s["state"] == "ACTIVE" and s["profile"] == self.profile
        ]
        for session in sessions:
            try:
                view = await self.runtime.view(session["native_id"])
                self.views[session["id"]] = view
                self.project(session, view)
            except Exception:
                self.views[session["id"]] = None
                self.notify(
                    "state:" + session["id"],
                    session["id"],
                    "AGY 连接降级",
                    "无法取得当前状态；不会自动提交新任务或创建替代会话。",
                )
        operations = self.store.operations(self.owner)
        uncertain = any(o["state"] in ("UNKNOWN", "SUBMITTING") for o in operations)
        uncertain |= any(
            self.store.blocked(s["id"]) or s["state"] in ("UNKNOWN", "CREATING", "ATTACHING")
            for s in self.store.sessions(self.owner)
            if s["profile"] == self.profile
        )
        for op in sorted(operations, key=lambda o: (o["kind"] != "steer", o["ordinal"])):
            binding = op["binding_id"]
            self.notify(
                "reply:" + op["source_id"],
                binding,
                "输入 · " + op["state"],
                f"请求 {op['request_id']}\n{self.redactor.text(op['content'])}",
            )
            if op["state"] != "QUEUED" or uncertain:
                continue
            session = self.store.session(self.owner, binding)
            current_view = self.views.get(binding)
            if session["state"] != "ACTIVE" or current_view is None:
                continue
            if op["kind"] == "input" and any(
                v is None or not v.idle for v in [self.views.get(s["id"]) for s in sessions]
            ):
                continue
            if not self.store.claim(op["request_id"]):
                continue
            try:
                await self.runtime.send(
                    session["native_id"],
                    op["content"],
                    op["request_id"],
                    steer=op["kind"] == "steer",
                )
                self.store.finish(op["request_id"], "ACCEPTED")
            except Busy:
                self.store.requeue_unsent(op["request_id"])
            except Exception:
                self.store.finish(op["request_id"], "UNKNOWN")
                self.store.pause(binding)
            updated = self.store.get(op["request_id"])
            self.notify(
                "reply:" + op["source_id"],
                binding,
                "输入 · " + updated["state"],
                f"请求 {op['request_id']}\n{self.redactor.text(op['content'])}",
            )
            break  # Refresh runtime state before submitting any further input.
        await self.flush()

    async def flush(self) -> None:
        for row in self.store.dirty_cards(self.owner):
            self.store.delivery_started(row["id"])
            try:
                result = await self.channel.deliver(
                    self.owner.chat, json.loads(row["payload"]), row["id"], row["message_id"]
                )
                state = result.state
                if state == "RETRY" and row["attempts"] >= 8:
                    state = "BLOCKED"
                self.store.delivery_result(
                    row["id"],
                    row["revision"],
                    state,
                    result.message_id,
                    max(result.retry_after, min(300, 2 ** min(row["attempts"], 8)))
                    + secrets.randbelow(1000) / 1000,
                )
            except Exception:
                self.store.delivery_result(
                    row["id"], row["revision"], "RETRY" if row["message_id"] else "UNKNOWN", None
                )

    async def run(self) -> None:
        while not self.stopping.is_set():
            await self.tick()
            if self.report_health:
                self.report_health()
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=2)
            except TimeoutError:
                continue
