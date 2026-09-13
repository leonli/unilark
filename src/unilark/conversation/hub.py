"""One-owner M1 gateway: durable intentions, snapshot reconciliation, safe delivery."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from unilark.adapters.sidecars.interface import Capability, CapabilityLevel, CapabilitySnapshot
from unilark.adapters.sidecars.views import Busy, Rejected, Runtime, SessionView
from unilark.conversation.channel import Action, Channel, Message, Owner
from unilark.conversation.panels import Panels
from unilark.conversation.questions import parse as parse_answers
from unilark.policy.redact import Redactor
from unilark.projection.cards import card, chunks
from unilark.projection.rich_text import reply_cards
from unilark.store.gateway import GatewayStore

HELP = (
    "/new [标题] · /attach 原生UUID · /sessions · /switch 会话ID\n"
    "/status · /stop · /continue · /steer 补充要求 · /cancel 请求ID\n"
    "/archive · /resume 会话ID · /cwd 绝对目录 · /capabilities\n"
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
        default_workspace: str = "",
    ) -> None:
        self.store, self.runtime, self.channel = store, runtime, channel
        self.owner, self.profile, self.redactor = owner, profile, redactor
        self.views: dict[str, SessionView | None] = {}
        self.stopping = asyncio.Event()
        self.report_health: Callable[[], None] | None = None
        self.default_workspace = default_workspace
        self.capabilities = getattr(runtime, "capabilities", CapabilitySnapshot("unknown", "", ""))
        self.panels = Panels(
            store, owner, profile, redactor, self.views, self.capabilities, default_workspace
        )

    def require(self, capability: Capability) -> None:
        if self.capabilities.level(capability) == CapabilityLevel.UNSUPPORTED:
            raise ValueError(
                "当前实例不支持该操作；可在原生桌面处理。修改任务前请先明确停止并确认空闲。"
            )

    def session_label(self, session: dict[str, Any]) -> str:
        return self.panels.label(session)

    def notify(self, key: str, binding: str | None, title: str, text: str) -> None:
        safe = self.redactor.text(text)
        if binding:
            label = self.session_label(self.store.session(self.owner, binding))
            if label not in safe:
                safe += "\n\n" + label
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
        if any(secret in message.text for secret in self.redactor.secrets):
            self.store.receive(self.owner, event, "noop", None, "")
            self.store.request_state(self.owner, event, "DONE")
            self.store.audit("known_credential_in_input")
            self.notify("reply:" + event, None, "输入未提交", "消息含应用凭据，已拒绝保存和执行。")
            return
        if not text or len(text) > 100_000:
            self.store.audit("invalid_input_size")
            return
        binding: str | None = None
        try:
            binding = self.store.target(self.owner, message.reply_to)
            if message.reply_to and not text.startswith("/"):
                question = self.store.quoted_question(self.owner, message.reply_to)
                if question:
                    view = self.views.get(question["binding"])
                    if view is None:
                        raise ValueError("暂未取得问题的最新状态，请稍后再答。")
                    step = next(
                        (
                            s
                            for s in view.steps
                            if s.index == question["step"]
                            and s.fingerprint == question["fingerprint"]
                        ),
                        None,
                    )
                    if step is None or not step.questions:
                        raise ValueError("问题已改变；请查看最新卡片。")
                    parse_answers(step.questions, message.text)
                    self.store.answer_text(self.owner, message.reply_to, event, message.text)
                    self.notify(
                        "reply:" + event,
                        binding,
                        "回答已保存",
                        "将核对原问题后提交；不会启动新的任务。",
                    )
                    return
            command, _, body = text.partition(" ")
            if not text.startswith("/"):
                command, body = "input", message.text
            else:
                command = command[1:]
            if command in ("", "help", "tasks") or (
                command in ("switch", "resume", "cancel") and not body.strip()
            ):
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                mode = {
                    "tasks": "tasks",
                    "switch": "sessions",
                    "resume": "sessions",
                    "cancel": "detail",
                }.get(command, "commands")
                self.panels.open(
                    event,
                    mode if mode != "detail" or binding else "sessions",
                    binding if mode == "detail" else None,
                    filter="archived" if command == "resume" else "active",
                )
            elif command in ("whoami", "capabilities"):
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                self.notify(
                    "reply:" + event,
                    binding,
                    "Unilark",
                    self.owner.user
                    if command == "whoami"
                    else "\n".join(
                        f"{c.capability}: {c.level} · {c.detail}" for c in self.capabilities.claims
                    )
                    + "\n完整事件重放、请求原生幂等、独立工具沙箱：不支持。",
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
                    self.store.journal.preference(
                        self.owner.key, self.profile, "workspace", self.default_workspace
                    ),
                )
                self.notify(
                    "reply:" + event,
                    binding,
                    "会话准备中",
                    f"会话 {binding}\n准备成功后自动提交排队输入。",
                )
                if command == "new":
                    workspace = self.store.journal.preference(
                        self.owner.key, self.profile, "workspace"
                    )
                    if workspace:
                        self.store.journal.set_context(binding, workspace, "")
            elif command == "cwd":
                directory = Path(body.strip()).expanduser()
                if not body.strip() or not directory.is_absolute() or not directory.is_dir():
                    raise ValueError("/cwd 后需要已存在的绝对目录；只影响后续新会话。")
                self.store.journal.set_preference(
                    self.owner.key, self.profile, "workspace", str(directory.resolve())
                )
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                self.notify(
                    "reply:" + event,
                    binding,
                    "新会话工作目录已设置",
                    str(directory.resolve())
                    + "\n已绑定会话保持原目录。工具权限仍由 AGY 审批决定。",
                )
            elif command in ("sessions", "status", "list"):
                command = "sessions" if command == "list" else command
                self.store.receive(self.owner, event, command, binding, "")
            elif command in ("archive", "resume"):
                target = body.strip() if command == "resume" else binding
                if not target:
                    raise ValueError("请指定或选择会话。")
                session = self.store.session(self.owner, target)
                if session["profile"] != self.profile:
                    raise ValueError("会话属于另一个实例。")
                self.store.receive(self.owner, event, command, target, "")
            elif command == "switch":
                self.store.switch(self.owner, body.strip())
                binding = body.strip()
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                self.panels.open(event, "detail", binding)
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
                    if command == "steer":
                        self.require(Capability.STEER)
                    if not body.strip():
                        raise ValueError("/steer 后需要补充内容。")
                    self.store.accept_input(self.owner, event, binding, body, command)
                    self.notify(
                        "reply:" + event, binding, "输入已保存", "已进入本地队列；尚未开始执行。"
                    )
                else:
                    if command == "stop":
                        self.require(Capability.INTERRUPT)
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
                self.store.receive(self.owner, event, "noop", binding, "")
                self.store.request_state(self.owner, event, "DONE")
                self.panels.open(event, "commands")
        except (ValueError, KeyError) as error:
            self.store.receive(self.owner, event, "noop", binding, "")
            self.store.request_state(self.owner, event, "DONE")
            self.notify("reply:" + event, binding, "未提交任务", str(error))

    async def action(self, action: Action) -> None:
        if (
            action.owner == self.owner
            and self.store.owner(self.owner.account) == self.owner
            and action.token.startswith("ui_")
        ):
            if self.store.seen(self.owner, action.event_id):
                return
            try:
                if any(
                    secret in value
                    for value in action.fields.values()
                    for secret in self.redactor.secrets
                ):
                    raise ValueError("输入含应用凭据，未保存或提交。")
                if not self.panels.state.consume(action):
                    raise ValueError("卡片已更新或操作已处理，请发送 /list 获取最新面板。")
            except ValueError as error:
                self.store.audit("rejected_panel_action")
                self.notify("reply:" + action.event_id, None, "请查看最新卡片", str(error))
            return
        if action.owner != self.owner or not self.store.consume(
            self.owner, action.token, action.message_id, action.decision, action.event_id
        ):
            self.store.audit("rejected_or_expired_action")

    async def requests(self) -> None:
        for request in self.store.requests(self.owner):
            if self.stopping.is_set():
                break
            event, binding, action = request["event"], request["binding"], request["action"]
            self.store.request_state(self.owner, event, "SUBMITTING")
            writing = False
            try:
                if action == "panel":
                    self.panels.perform(request)
                elif action in ("sessions", "status"):
                    self.panels.open(
                        event,
                        "detail" if action == "status" and binding else "sessions",
                        binding if action == "status" else None,
                    )
                elif binding and action != "noop":
                    session = self.store.session(self.owner, binding)
                    if session["profile"] != self.profile:
                        raise ValueError("Configured runtime differs from this binding")
                    native = session["native_id"]
                    if action == "create":
                        context = self.store.journal.context(binding)
                        workspace = context["workspace"]
                        if not workspace and hasattr(self.runtime, "workspace"):
                            workspace = await self.runtime.workspace()
                            self.store.journal.set_context(binding, workspace, "")
                        writing = True
                        await self.runtime.create(native, workspace=workspace)
                        self.store.session_state(binding, "ACTIVE")
                        self.notify(
                            "reply:" + event,
                            binding,
                            "会话已建立",
                            "准备成功，可以继续发送任务。/status 查看本会话，/list 切换会话。",
                        )
                    elif action == "archive":
                        if not (await self.runtime.view(native)).idle:
                            raise ValueError("运行中的会话不能归档；先停止并确认空闲。")
                        self.store.archive(self.owner, binding)
                        self.notify(
                            "reply:" + event,
                            binding,
                            "会话已归档",
                            "已暂停本地同步和队列；原生历史保留。/resume 会话ID 可恢复。",
                        )
                    elif action == "resume":
                        if session["state"] != "ARCHIVED":
                            raise ValueError("只有已归档会话可用 /resume 恢复。")
                        view = await self.runtime.view(native)
                        if not view.steps:
                            raise ValueError("无法验证原生历史，未恢复会话。")
                        self.store.session_state(binding, "ACTIVE")
                        self.store.switch(self.owner, binding)
                        self.notify(
                            "reply:" + event,
                            binding,
                            "已恢复原会话",
                            "继续同步同一原生历史；队列保持暂停，发送 /continue 可继续。",
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
                        self.require(Capability.INTERRUPT)
                        before_stop = await self.runtime.view(native)
                        if request["body"]:
                            intent = json.loads(request["body"])
                            if (
                                intent.get("fingerprint")
                                and before_stop.anchor != intent["fingerprint"]
                            ):
                                raise ValueError("Old stop card belongs to an earlier turn")
                        self.store.control_observation(
                            self.owner,
                            event,
                            {
                                "stop_observed_at": time.time(),
                                "stop_was_busy": not before_stop.idle,
                                "stop_anchor": before_stop.anchor,
                            },
                        )
                        writing = True
                        confirmed = await self.runtime.stop(native)
                        if not confirmed:
                            raise RuntimeError("Stop has not yet been confirmed")
                        self.store.control_observation(
                            self.owner, event, {"stop_idle_confirmed": True}
                        )
                        self.notify(
                            "reply:" + event,
                            binding,
                            "已确认空闲",
                            "停止操作已确认，队列保持暂停。",
                        )
                    elif action == "answer":
                        self.require(Capability.INTERACTION)
                        intent = json.loads(request["body"])
                        view = await self.runtime.view(native)
                        current = next(
                            (
                                s
                                for s in view.steps
                                if s.index == intent["step"]
                                and s.fingerprint == intent["fingerprint"]
                                and s.status == "waiting"
                                and s.questions
                            ),
                            None,
                        )
                        if current is None:
                            raise ValueError("问题已结束或改变，旧回答未提交。")
                        cancel = intent["decision"] == "cancel"
                        answers = (
                            ()
                            if cancel
                            else parse_answers(
                                current.questions, intent.get("text", ""), intent["decision"]
                            )
                        )
                        writing = True
                        await self.runtime.answer(
                            native,
                            intent["step"],
                            answers,
                            fingerprint=intent["fingerprint"],
                            cancel=cancel,
                        )
                        self.notify(
                            "reply:" + event,
                            binding,
                            "已取消问题" if cancel else "回答已提交",
                            "后续结果以 AGY 当前状态为准。",
                        )
                    elif action == "resolve":
                        self.require(Capability.INTERACTION)
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
                        self.store.control_observation(
                            self.owner, event, {"applied_at": time.time()}
                        )
                        self.notify(
                            "reply:" + event,
                            binding,
                            "决议已提交",
                            f"当前步骤状态：{observed}。执行结果以 AGY 后续状态为准。",
                        )
                self.store.request_state(self.owner, event, "DONE")
            except Exception as error:
                if isinstance(error, Rejected) or (
                    not writing and isinstance(error, (ValueError, KeyError))
                ):
                    self.store.request_state(self.owner, event, "REJECTED")
                    self.notify("reply:" + event, binding, "操作未执行", str(error))
                    if binding and action in ("attach", "create"):
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
            if action not in ("create", "attach", "stop", "resolve", "answer"):
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
            elif action in ("resolve", "answer"):
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
        observation = self.store.journal.observe(binding, view)
        label = self.session_label(session)
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
        live = {
            s.fingerprint
            for s in view.steps
            if (s.permission or s.questions) and s.status == "waiting"
        }
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
                payload = card(title, content + "\n\n" + label)
                cid = self.store.card_id(self.owner, card_key)
                if (
                    step.status == "waiting"
                    and step.permission
                    and part == 0
                    and self.capabilities.level(Capability.INTERACTION)
                    != CapabilityLevel.UNSUPPORTED
                ):
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
                            "\n仅本次生效；5 分钟未处理将提交拒绝。\n"
                            if buttons
                            else "\n已处理或已到期，等待 AGY 状态更新。\n"
                        )
                        + label,
                        buttons,
                    )
                payloads.append(payload)
            if step.kind == "assistant" and not step.permission and not step.questions:
                payloads = reply_cards(title, safe, label)
            if (
                step.questions
                and step.status == "waiting"
                and self.capabilities.level(Capability.INTERACTION) != CapabilityLevel.UNSUPPORTED
            ):
                cid = self.store.card_id(self.owner, key + ":0")
                interaction = self.store.interaction(
                    self.owner, binding, cid, "question", step.fingerprint, step.index
                )
                question_text = "\n\n".join(
                    str(i + 1) + ". " + q.text + "\n" + "、".join(label for _, label in q.options)
                    for i, q in enumerate(step.questions)
                )
                buttons = []
                if interaction["status"] == "OPEN" and interaction["expires"] > time.time():
                    if (
                        len(step.questions) == 1
                        and not step.questions[0].multi
                        and len(step.questions[0].options) <= 5
                    ):
                        buttons = [
                            (
                                self.redactor.text(label)[:40],
                                interaction["token"],
                                "answer:" + identity,
                            )
                            for identity, label in step.questions[0].options
                        ]
                    buttons.append(("取消问题", interaction["token"], "cancel"))
                    question_text += (
                        "\n\n可引用此卡片回复，每题一行；多选用逗号分隔。5 分钟未处理将取消问题。"
                    )
                elif interaction["status"] == "OPEN":
                    self.store.expire_permission(self.owner, interaction)
                question_text += "\n\n" + label
                payloads = [card("AGY 等待回答", self.redactor.text(question_text)[:3300], buttons)]
            self.save_parts(key, binding, payloads)
        state_key = "state:" + binding
        context = label + "\n本地队列：" + session["queue_state"]
        if observation.get("gap_since"):
            context += "\n存在离线或历史变化区间；已核对当前快照，不能保证补齐全部中间事件。"
        payload = card("AGY · " + view.status, context)
        cid = self.store.card_id(self.owner, state_key + ":0")
        if (
            not view.idle
            and self.capabilities.level(Capability.INTERRUPT) != CapabilityLevel.UNSUPPORTED
        ):
            interaction = self.store.interaction(self.owner, binding, cid, "stop", view.anchor, -1)
            if interaction["status"] == "OPEN" and interaction["expires"] > time.time():
                payload = card(
                    "AGY · " + view.status,
                    context,
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
            if self.stopping.is_set():
                break
            try:
                view = await self.runtime.view(session["native_id"])
                self.views[session["id"]] = view
                self.project(session, view)
            except Exception:
                self.views[session["id"]] = None
                self.store.journal.unavailable(session["id"])
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
            if self.stopping.is_set():
                break
            binding = op["binding_id"]
            self.notify(
                "reply:" + op["source_id"],
                binding,
                "输入 · " + op["state"],
                f"请求 {op['request_id']}\n{self.redactor.text(op['content'])}"
                + (
                    "\n队列位置："
                    + str(
                        sum(
                            o["binding_id"] == binding
                            and o["state"] == "QUEUED"
                            and o["ordinal"] <= op["ordinal"]
                            for o in operations
                        )
                    )
                    if op["state"] == "QUEUED"
                    else ""
                )
                + (
                    "\n已由本机确认不再跟踪；不表示原生任务未执行。"
                    if op["state"] == "DISMISSED"
                    else ""
                ),
            )
            if op["state"] != "QUEUED" or uncertain:
                continue
            if (
                op["kind"] == "steer"
                and self.capabilities.level(Capability.STEER) == CapabilityLevel.UNSUPPORTED
            ):
                self.store.cancel_queued(self.owner, op["request_id"])
                self.notify(
                    "reply:" + op["source_id"],
                    binding,
                    "补充未提交",
                    "当前实例不支持 steer；请先停止并确认空闲，再发送新的任务。",
                )
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
            except Rejected:
                self.store.finish(op["request_id"], "REJECTED")
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
        self.panels.render()
        await self.flush()

    async def flush(self) -> None:
        if not getattr(self.channel, "connected", True):
            return
        shutdown_deadline = time.monotonic() + 5
        for row in self.store.dirty_cards(self.owner):
            if not getattr(self.channel, "connected", True):
                break
            if self.stopping.is_set() and time.monotonic() > shutdown_deadline:
                break
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
