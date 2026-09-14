"""Private-group user journeys, destination isolation and provisioning crash recovery."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from test_gateway import OWNER, Channel, Runtime, send
from test_panels import click, rendered
from unilark.adapters.lark.rooms import RoomApiError
from unilark.adapters.sidecars.views import SessionView, StepView
from unilark.conversation.channel import Message
from unilark.conversation.hub import Hub
from unilark.policy.redact import Redactor
from unilark.store.gateway import GatewayStore


class RoomAPI:
    def __init__(self):
        self.created = []
        self.configured = []
        self.safe = True
        self.failure = None
        self.matches = []

    async def create(self, title, request):
        self.created.append(request)
        if self.failure:
            raise self.failure
        return "oc_" + request

    async def configure(self, chat):
        self.configured.append(chat)

    async def verify(self, chat, request):
        return self.safe

    async def reconcile(self, request):
        return self.matches


@pytest.fixture
def gateway(tmp_path):
    store = GatewayStore(tmp_path / "state.db")
    store.set_owner(OWNER)
    runtime, channel = Runtime(), Channel()
    channel.room_api = RoomAPI()
    hub = Hub(store, runtime, channel, OWNER, "profile", Redactor(), enable_rooms=True)
    yield hub, store, runtime, channel
    store.close()


async def create(hub, store, title="test"):
    await send(hub, "/new " + title)
    await hub.tick()
    session = store.session(OWNER, store.target(OWNER))
    return session, store.rooms.get(OWNER, session["id"])


async def group_send(hub, chat, text, **kw):
    msg = Message(OWNER, uuid.uuid4().hex, text, time.time(), chat=chat, **kw)
    await hub.accept(msg)
    return msg


async def test_two_groups_route_independently_and_dm_is_navigation(gateway):
    hub, store, runtime, channel = gateway
    a, ar = await create(hub, store, "A")
    b, br = await create(hub, store, "B")
    assert ar["chat"] != br["chat"]
    assert store.target(OWNER) == b["id"]
    msg = await group_send(hub, ar["chat"], "task A")
    await hub.accept(msg)
    await hub.tick()
    await group_send(hub, br["chat"], "task B")
    await send(hub, "where am I")
    await hub.tick()
    assert [(o["binding_id"], o["content"]) for o in store.operations(OWNER)] == [
        (a["id"], "task A"),
        (b["id"], "task B"),
    ]
    assert len(runtime.sent) == 1
    runtime.views[a["native_id"]] = SessionView(
        True, "idle", "a-done", [StepView(1, "assistant", "done", "**answer A**")]
    )
    await hub.tick()
    assert runtime.sent[-1][0] == b["native_id"]
    assert any(c == ar["chat"] and "**answer A**" in json.dumps(p) for c, p, _, _ in channel.sent)
    assert not any(
        c == br["chat"] and "**answer A**" in json.dumps(p) for c, p, _, _ in channel.sent
    )
    panel = rendered(store, mode="sessions")
    assert "进入会话" in panel["payload"] and "切换到此会话" not in panel["payload"]
    assert store.rooms.card_chat(OWNER, panel["id"]) == OWNER.chat


async def test_new_form_project_first_task_once_after_restart(gateway, tmp_path):
    hub, store, runtime, channel = gateway
    store.journal.set_preference(OWNER.key, "profile", "workspace", str(tmp_path))
    hub.panels.open("new", "new")
    await hub.tick()
    form = rendered(store, mode="new")
    action = click(
        store, form, "创建会话群", fields={"title": "计划", "project": "0", "task": "first task"}
    )
    channel.room_api.safe = False
    await hub.action(action)
    await hub.action(replace(action, event_id="another-event"))
    await hub.tick()
    await hub.tick()
    assert len(store.sessions(OWNER)) == 1 and not runtime.sent
    session = store.sessions(OWNER)[0]
    assert store.journal.context(session["id"])["workspace"] == str(tmp_path)
    room = store.rooms.get(OWNER, session["id"])
    assert room["first_task"] == "first task"
    channel.room_api.safe = True
    store.rooms.state(session["id"], "BLOCKED", retry_at=0)
    restarted = Hub(store, runtime, channel, OWNER, "profile", Redactor(), enable_rooms=True)
    store.recover_gateway()
    await restarted.tick()
    await restarted.tick()
    assert len(runtime.sent) == 1 and runtime.sent[0][1] == "first task"
    assert len(channel.room_api.created) == 1
    assert store.rooms.get(OWNER, session["id"])["first_task"] == ""


async def test_invalid_project_does_not_consume_form(gateway):
    hub, store, _, _ = gateway
    hub.panels.open("new", "new")
    await hub.tick()
    form = rendered(store, mode="new")
    await hub.action(click(store, form, "创建会话群", fields={"title": "x", "project": "/etc"}))
    await hub.tick()
    assert not store.sessions(OWNER)
    await hub.action(click(store, form, "创建会话群", fields={"title": "x", "project": "0"}))
    await hub.tick()
    await hub.tick()
    assert len(store.sessions(OWNER)) == 1


async def test_foreign_group_quote_and_control_rejected(gateway):
    hub, store, runtime, _ = gateway
    a, ar = await create(hub, store, "A")
    _, br = await create(hub, store, "B")
    await group_send(hub, ar["chat"], "/status")
    await hub.tick()
    detail = rendered(store, mode="detail", binding=a["id"])
    assert store.rooms.card_chat(OWNER, detail["id"]) == ar["chat"]
    await group_send(hub, br["chat"], "wrong quote", reply_to=detail["message_id"])
    await group_send(hub, "oc_unregistered", "unknown group")
    action = click(store, detail, "归档会话")
    await hub.action(replace(action, chat=br["chat"]))
    await hub.tick()
    assert not runtime.sent and store.session(OWNER, a["id"])["state"] == "ACTIVE"
    await hub.action(replace(action, event_id="correct", chat=ar["chat"]))
    await hub.tick()
    await hub.tick()
    assert store.session(OWNER, a["id"])["state"] == "ARCHIVED"


async def test_membership_change_blocks_input_and_output_then_recovers(gateway):
    hub, store, runtime, channel = gateway
    a, room = await create(hub, store)
    channel.sent.clear()
    channel.room_api.safe = False
    hub.rooms.verified.clear()
    runtime.views[a["native_id"]] = SessionView(
        True, "idle", "done", [StepView(0, "assistant", "done", "private result")]
    )
    await group_send(hub, room["chat"], "must reject")
    await hub.tick()
    assert not runtime.sent
    assert not any(c == room["chat"] for c, _, _, _ in channel.sent)
    assert not any("private result" in json.dumps(p) for _, p, _, _ in channel.sent)
    assert store.rooms.get(OWNER, a["id"])["status"] == "BLOCKED"
    channel.room_api.safe = True
    store.rooms.state(a["id"], "BLOCKED", retry_at=0)
    await hub.tick()
    assert any(
        c == room["chat"] and "private result" in json.dumps(p) for c, p, _, _ in channel.sent
    )


@pytest.mark.parametrize("ambiguous", [False, True])
async def test_create_error_and_ambiguous_result_never_blind_retry(gateway, ambiguous):
    hub, store, _, channel = gateway
    channel.room_api.failure = RoomApiError(500, ambiguous=ambiguous)
    a, room = await create(hub, store)
    assert room["status"] == ("UNKNOWN" if ambiguous else "ERROR")
    channel.room_api.failure = None
    store.rooms.state(a["id"], room["status"], retry_at=0)
    await hub.tick()
    assert len(channel.room_api.created) == 1
    if ambiguous:
        channel.room_api.matches = ["oc_recovered"]
        store.rooms.state(a["id"], "UNKNOWN", retry_at=0)
        await hub.tick()
        assert store.rooms.get(OWNER, a["id"])["chat"] == "oc_recovered"
    else:
        store.receive(OWNER, "explicit-retry", "room", a["id"], "")
        await hub.tick()
        assert len(channel.room_api.created) == 2


async def test_existing_session_waits_idle_preserves_dm_cards_no_history_replay(gateway):
    hub, store, runtime, channel = gateway
    binding = store.add_session(OWNER, "profile", str(uuid.uuid4()), "ACTIVE", "existing")
    native = store.session(OWNER, binding)["native_id"]
    old = StepView(0, "assistant", "done", "OLD HISTORY")
    runtime.views[native] = SessionView(False, "running", "old", [old])
    await hub.tick()
    old_card = store.card_id(OWNER, f"step:{binding}:0:0")
    old_mid = store.db.execute("SELECT message_id FROM cards WHERE id=?", (old_card,)).fetchone()[0]
    store.receive(OWNER, "entrance", "room", binding, "")
    await hub.tick()
    assert not channel.room_api.created
    runtime.views[native] = SessionView(True, "idle", "old", [old])
    await hub.tick()
    room = store.rooms.get(OWNER, binding)
    assert room["baseline"] == 0
    runtime.views[native] = SessionView(
        True, "idle", "new", [old, StepView(1, "assistant", "done", "NEW REPLY")]
    )
    await hub.tick()
    assert store.rooms.card_chat(OWNER, old_card) == OWNER.chat
    assert (
        store.db.execute("SELECT message_id FROM cards WHERE id=?", (old_card,)).fetchone()[0]
        == old_mid
    )
    assert not any(
        c == room["chat"] and "OLD HISTORY" in json.dumps(p) for c, p, _, _ in channel.sent
    )
    assert any(c == room["chat"] and "NEW REPLY" in json.dumps(p) for c, p, _, _ in channel.sent)


async def test_new_from_group_entry_returns_to_origin(gateway):
    hub, store, _, channel = gateway
    _, ar = await create(hub, store, "A")
    await group_send(hub, ar["chat"], "/new B")
    await hub.tick()
    b = store.session(OWNER, store.target(OWNER))
    br = store.rooms.get(OWNER, b["id"])
    assert br["source_chat"] == ar["chat"]
    assert any(c == ar["chat"] and br["chat"] in json.dumps(p) for c, p, _, _ in channel.sent)


def test_schema3_rejects_previous_writer_without_touching_data(tmp_path):
    import subprocess

    path = tmp_path / "state.db"
    store = GatewayStore(path)
    store.set_owner(OWNER)
    store.close()
    # Execute the actual 0.0.4 Ledger constructor extracted from its committed source.
    old = subprocess.run(
        ["/usr/bin/git", "show", "38ae26b:src/unilark/store/ledger.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    scope = {"__name__": "old_ledger"}
    exec(compile(old, "old_ledger.py", "exec"), scope)  # noqa: S102 -- pinned repository code
    with pytest.raises(ValueError, match="schema"):
        scope["Ledger"](path)
    store = GatewayStore(path, readonly=True)
    assert store.owner(OWNER.account) == OWNER
    store.close()


async def test_blocked_group_backlog_does_not_starve_control_dm(gateway):
    hub, store, _, channel = gateway
    a, room = await create(hub, store)
    channel.sent.clear()
    for index in range(35):
        hub.save_parts(
            f"backlog:{index}",
            a["id"],
            [{"header": {"title": {"content": "private"}}}],
            chat=room["chat"],
        )
    store.rooms.state(a["id"], "BLOCKED", retry_at=time.time() + 15)
    hub.save_parts("notice", None, [{"header": {"title": {"content": "notice"}}}], chat=OWNER.chat)
    await hub.flush()
    assert [p["header"]["title"]["content"] for _, p, _, _ in channel.sent] == ["notice"]


async def test_idle_groups_do_not_poll_but_next_input_rechecks_members(gateway, monkeypatch):
    hub, store, _, channel = gateway
    _, room = await create(hub, store)
    verify = AsyncMock(return_value=True)
    channel.room_api.verify = verify
    clock = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: clock)
    # Simulate a full idle minute across multiple cache expirations, through the real Hub.
    for _ in range(10):
        clock += 6
        await hub.tick()
    verify.assert_not_awaited()
    verify.return_value = False
    await group_send(hub, room["chat"], "must not execute in an unverified group")
    verify.assert_awaited_once()
    assert not store.operations(OWNER)


async def test_quota_exhaustion_does_not_claim_permissions_changed(gateway):
    hub, store, _, channel = gateway
    session, room = await create(hub, store)
    hub.rooms.verified.clear()
    channel.room_api.verify = AsyncMock(side_effect=RoomApiError(99991403))
    assert not await hub.rooms.allowed(room["chat"])
    blocked = store.rooms.get(OWNER, session["id"])
    assert "额度" in blocked["reason"] and "99991403" in blocked["reason"]
    assert blocked["retry_at"] >= time.time() + 3500
    for _ in range(3):
        await hub.tick()
    channel.room_api.verify.assert_awaited_once()


async def test_explicit_quota_denial_preserves_pending_group_creation(gateway):
    hub, store, _, channel = gateway
    channel.room_api.failure = RoomApiError(99991403)
    session, room = await create(hub, store)
    assert room["status"] == "QUEUED" and "额度" in room["reason"]
    for _ in range(3):
        await hub.tick()
    assert len(channel.room_api.created) == 1
    channel.room_api.failure = None
    store.rooms.state(session["id"], "QUEUED", retry_at=0)
    await hub.tick()
    assert store.rooms.get(OWNER, session["id"])["status"] == "READY"
    assert channel.room_api.created == [room["request_id"], room["request_id"]]
