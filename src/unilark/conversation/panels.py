"""Session navigation and queue cards, independent of transport and native execution."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from unilark.adapters.sidecars.interface import Capability, CapabilityLevel, CapabilitySnapshot
from unilark.adapters.sidecars.views import SessionView
from unilark.conversation.channel import Owner
from unilark.policy.redact import Redactor
from unilark.projection.cards import card
from unilark.store.gateway import GatewayStore
from unilark.store.panels import PanelStore

PAGE_SIZE = 5


def text(content: str) -> dict[str, Any]:
    # User titles and snippets never become Markdown mentions or links.
    return {"tag": "div", "text": {"tag": "plain_text", "content": content}}


def button(label: str, op: str, **values: Any) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "default",
        "_intent": {"op": op, **values},
    }


def actions(*items: dict[str, Any]) -> dict[str, Any]:
    return {"tag": "action", "actions": list(items)}


class Panels:
    def __init__(
        self,
        store: GatewayStore,
        owner: Owner,
        profile: str,
        redactor: Redactor,
        views: dict[str, SessionView | None],
        capabilities: CapabilitySnapshot,
        default_workspace: str = "",
    ) -> None:
        self.store, self.owner, self.profile = store, owner, profile
        self.redactor, self.views, self.capabilities = redactor, views, capabilities
        self.default_workspace = default_workspace
        self.state = PanelStore(store)

    def name(self, session: dict[str, Any]) -> str:
        return self.redactor.text(session["title"])[:60] + " · " + str(session["id"])[:6]

    def label(self, session: dict[str, Any]) -> str:
        workspace = self.store.journal.context(session["id"])["workspace"]
        project = Path(workspace).name if workspace else "原生项目"
        return self.name(session) + "\n项目：" + self.redactor.text(project)

    def open(
        self, event: str, mode: str, binding: str | None = None, *, filter: str = "active"
    ) -> None:
        self.state.open(self.owner, "reply:" + event + ":0", mode, binding, filter=filter)

    def queued(self, binding: str) -> list[dict[str, Any]]:
        return [
            o
            for o in self.store.operations(self.owner)
            if o["binding_id"] == binding and o["state"] == "QUEUED"
        ]

    def status(self, session: dict[str, Any]) -> str:
        binding = session["id"]
        state = session["state"]
        if state != "ACTIVE":
            return {
                "ARCHIVED": "已归档",
                "CREATING": "准备中",
                "ATTACHING": "连接中",
                "UNKNOWN": "状态待确认",
                "UNAVAILABLE": "暂不可用",
            }.get(state, "状态待确认")
        if self.store.blocked(binding) or any(
            o["state"] in ("UNKNOWN", "SUBMITTING") and o["binding_id"] == binding
            for o in self.store.operations(self.owner)
        ):
            return "状态待确认"
        view = self.views.get(binding)
        if view is None:
            return "状态待更新"
        if any(s.status == "waiting" and (s.permission or s.questions) for s in view.steps):
            return "等待你处理"
        if not view.idle:
            return "正在运行"
        if session["queue_state"] == "PAUSED":
            return "队列已暂停"
        return "排队中" if self.queued(binding) else "空闲"

    def reason(self, session: dict[str, Any]) -> str:
        if session["queue_state"] == "PAUSED":
            return "队列已暂停；处理原因后点「继续队列」。"
        if self.status(session) in ("状态待确认", "状态待更新", "准备中", "连接中"):
            return "等待确认会话及执行状态。"
        if self.status(session) in ("正在运行", "等待你处理"):
            return "等待当前任务结束；普通消息排为后续任务。"
        sessions = [s for s in self.store.sessions(self.owner) if s["profile"] == self.profile]
        if any(
            self.store.blocked(s["id"])
            or self.status(s) in ("状态待确认", "状态待更新", "准备中", "连接中")
            for s in sessions
            if s["state"] != "ARCHIVED"
        ):
            return "实例中有状态待确认的会话，暂缓新提交。"
        if any(self.status(s) in ("正在运行", "等待你处理") for s in sessions):
            return "等待其他会话结束；本版同时执行上限为 1。"
        return "等待下一次调度检查。"

    def navigation(self) -> dict[str, Any]:
        return actions(
            button("我的会话", "open", mode="sessions"),
            button("新建会话", "open", mode="new"),
            button("全部任务", "open", mode="tasks"),
        )

    def row(self, session: dict[str, Any], selected: str | None) -> list[dict[str, Any]]:
        binding = session["id"]
        description = ("● 当前输入 → " if binding == selected else "") + self.label(session)
        description += "\n" + self.status(session) + f" · 排队 {len(self.queued(binding))} 项"
        if session["queue_state"] == "PAUSED" and self.status(session) != "队列已暂停":
            description += " · 队列暂停"
        if self.queued(binding):
            description += "\n" + self.reason(session)
        controls = [button("查看任务", "open", mode="detail", binding=binding)]
        if session["state"] == "ACTIVE" and binding != selected:
            controls.insert(0, button("切换到此会话", "switch", binding=binding))
        if session["state"] == "ARCHIVED":
            controls.insert(0, button("恢复会话", "resume", binding=binding))
        return [{"tag": "hr"}, text(description), actions(*controls)]

    def render(self) -> None:
        for panel in self.state.panels(self.owner):
            if panel["mode"] == "new" and panel["digest"] and not panel["submitted"]:
                continue  # Preserve drafts and the workspace captured when the form was opened.
            payload = self.build(panel)
            self.state.render(self.owner, panel, payload)

    def build(self, panel: dict[str, Any]) -> dict[str, Any]:
        mode, binding = panel["mode"], panel["binding"]
        selected = self.store.target(self.owner)
        sessions = [s for s in self.store.sessions(self.owner) if s["profile"] == self.profile]
        current = next((s for s in sessions if s["id"] == selected), None)
        current_text = "当前输入 → " + (self.name(current) if current else "尚未选择会话")
        title = {
            "sessions": "我的会话",
            "tasks": "全部任务",
            "detail": "会话与任务",
            "commands": "命令面板",
            "new": "新建会话",
        }[mode]
        elements = ([text(current_text)] if mode != "new" else []) + [self.navigation()]
        if mode in ("sessions", "tasks"):
            filtered = [
                s for s in sessions if (s["state"] == "ARCHIVED") == (panel["filter"] == "archived")
            ]
            if mode == "tasks" or panel["filter"] == "attention":
                filtered = [s for s in filtered if self.status(s) not in ("空闲", "已归档")]
            filtered.sort(
                key=lambda s: (s["id"] != selected, self.status(s) != "等待你处理", s["id"])
            )
            pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(max(0, panel["page"]), pages - 1)
            elements.append(
                text(
                    f"{len(filtered)} 个会话 · 同时执行上限 1\n"
                    "切换只改变下一条普通消息的目标；后台任务继续运行。"
                )
            )
            if mode == "sessions":
                elements.append(
                    actions(
                        *[
                            button(label, "page", panel=panel["id"], page=0, filter=value)
                            for label, value in (
                                ("活动会话", "active"),
                                ("待处理/运行", "attention"),
                                ("已归档", "archived"),
                            )
                        ]
                    )
                )
            for session in filtered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
                elements.extend(self.row(session, selected))
            if not filtered:
                elements.append(text("这里暂无会话或任务。可点「新建会话」，或切换筛选。"))
            paging = [button("刷新", "page", panel=panel["id"], page=page, filter=panel["filter"])]
            for label, offset in (("上一页", -1), ("下一页", 1)):
                if 0 <= page + offset < pages:
                    paging.append(
                        button(
                            label,
                            "page",
                            panel=panel["id"],
                            page=page + offset,
                            filter=panel["filter"],
                        )
                    )
            elements.extend([text(f"第 {page + 1} / {pages} 页"), actions(*paging)])
        elif mode == "detail" and binding:
            session = self.store.session(self.owner, binding)
            title = self.name(session)
            elements.extend(
                [
                    text(
                        self.label(session)
                        + "\n"
                        + self.status(session)
                        + " · 队列"
                        + ("暂停" if session["queue_state"] == "PAUSED" else "开放")
                    )
                ]
            )
            view = self.views.get(binding)
            controls = []
            if session["state"] == "ACTIVE":
                if binding != selected:
                    controls.append(button("切换到此会话", "switch", binding=binding))
                if (
                    view is not None
                    and not view.idle
                    and self.capabilities.level(Capability.INTERRUPT) != CapabilityLevel.UNSUPPORTED
                ):
                    controls.append(
                        button("停止并暂停队列", "stop", binding=binding, fingerprint=view.anchor)
                    )
                if session["queue_state"] == "PAUSED":
                    controls.append(button("继续队列", "continue", binding=binding))
                if view is not None and view.idle and not self.queued(binding):
                    archive = button("归档会话", "archive", binding=binding)
                    archive["confirm"] = {
                        "title": {"tag": "plain_text", "content": "归档会话"},
                        "text": {
                            "tag": "plain_text",
                            "content": self.name(session) + "将暂停同步，原生历史保留。",
                        },
                    }
                    controls.append(archive)
            elif session["state"] == "ARCHIVED":
                controls.append(button("恢复会话", "resume", binding=binding))
            if controls:
                elements.append(actions(*controls))
            if view is not None:
                recent = next((s for s in reversed(view.steps) if s.text), None)
                if recent:
                    elements.append(text("最近内容：\n" + self.redactor.text(recent.text)[:500]))
                if self.status(session) == "等待你处理":
                    elements.append(text("请在该会话的审批/问题卡中回答；无需先切换会话。"))
            queue = self.queued(binding)
            elements.append(text(f"后续队列：{len(queue)} 项"))
            if queue:
                elements.append(text(self.reason(session)))
            pages = max(1, (len(queue) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(max(0, panel["page"]), pages - 1)
            for index, op in enumerate(
                queue[page * PAGE_SIZE : (page + 1) * PAGE_SIZE], page * PAGE_SIZE + 1
            ):
                elements.extend(
                    [
                        text(f"{index}. " + self.redactor.text(op["content"])[:350]),
                        actions(
                            button(
                                "撤销这项输入", "cancel", binding=binding, request=op["request_id"]
                            )
                        ),
                    ]
                )
            paging = [button("刷新", "page", panel=panel["id"], page=page, filter="active")]
            for label, offset in (("上一页", -1), ("下一页", 1)):
                if 0 <= page + offset < pages:
                    paging.append(
                        button(
                            label, "page", panel=panel["id"], page=page + offset, filter="active"
                        )
                    )
            elements.extend(
                [
                    actions(*paging),
                    text(
                        "引用本卡回复会固定发往此会话。\n工作目录："
                        + self.redactor.text(
                            self.store.journal.context(binding)["workspace"] or "由原生项目管理"
                        )
                    ),
                ]
            )
        elif mode == "new":
            workspace = self.store.journal.preference(
                self.owner.key, self.profile, "workspace", self.default_workspace
            )
            elements.append(text("工作目录：" + self.redactor.text(workspace or "由原生项目管理")))
            if panel["submitted"]:
                elements.append(text("创建请求已保存，请查看会话准备回执。此表单已提交。"))
            else:
                submit = button("创建并切换", "create", workspace=workspace)
                submit.update(action_type="form_submit", name="create", type="primary")
                elements.append(
                    {
                        "tag": "form",
                        "name": "new_session",
                        "elements": [
                            {
                                "tag": "input",
                                "name": "title",
                                "required": True,
                                "placeholder": {
                                    "tag": "plain_text",
                                    "content": "会话标题（1–80 字）",
                                },
                            },
                            submit,
                        ],
                    }
                )
                elements.append(text("也可发送 /new 标题 快速创建。/cwd 绝对目录 设置后续项目。"))
        else:
            elements.extend(
                [
                    text(
                        "点按钮进入操作，无需复制会话编号。\n"
                        "发送 / 打开本面板；这不是输入时自动补全。"
                    ),
                    actions(
                        button("当前会话 / 队列", "open", mode="detail", binding=selected),
                        button("恢复归档会话", "open", mode="sessions", filter="archived"),
                    ),
                    text(
                        "快捷命令：\n/new 标题 · /list · /tasks · /status\n"
                        "/steer 补充要求 · /stop · /continue\n"
                        "/attach 原生UUID · /cwd 绝对目录\n"
                        "/whoami · /capabilities\n"
                        "普通文本排为下一项任务；引用卡片固定原会话。"
                    ),
                ]
            )
        payload = card(title, "")
        payload["elements"] = elements
        return payload

    def perform(self, request: dict[str, Any]) -> None:
        intent = json.loads(request["body"])
        op, binding, event = intent["op"], intent.get("binding"), request["event"]
        source = "ui:" + event
        if binding and self.store.session(self.owner, binding)["profile"] != self.profile:
            raise ValueError("会话属于其他实例。")
        if (
            op in ("stop", "continue", "archive")
            and self.store.session(self.owner, binding)["state"] != "ACTIVE"
        ):
            raise ValueError("会话已归档或暂不可用，请刷新会话面板。")
        if op == "open":
            mode = intent["mode"]
            if mode == "detail" and not binding:
                mode = "sessions"
            self.open(
                source,
                mode,
                binding if mode == "detail" else None,
                filter=intent.get("filter", "active"),
            )
        elif op == "page":
            self.state.page(self.owner, intent["panel"], intent["page"], intent["filter"])
        elif op == "switch":
            self.state.switch(self.owner, event, binding)
            self.open(source, "detail", binding)
        elif op == "create":
            if not self.store.seen(self.owner, source):
                binding = self.store.accept_session(
                    self.owner,
                    source,
                    self.profile,
                    str(uuid.uuid4()),
                    "create",
                    self.redactor.text(intent["title"]),
                    intent["workspace"],
                )
                self.open(source + ":detail", "detail", binding)
        elif op == "cancel":
            item = self.store.get(intent["request"])
            if item["binding_id"] != binding or not self.store.cancel_queued(
                self.owner, intent["request"]
            ):
                raise ValueError("未撤销：该输入已不在待提交队列，请刷新。")
        elif op in ("stop", "continue", "archive", "resume"):
            self.store.receive(
                self.owner,
                source,
                op,
                binding,
                json.dumps({"fingerprint": intent["fingerprint"]}) if op == "stop" else "",
            )
            self.open(source + ":detail", "detail", binding)
        else:
            raise ValueError("此卡片操作不可用，请重新发送 /list。")
