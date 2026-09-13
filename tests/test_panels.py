"""User journeys through actual rendered buttons, persistent intents and Hub scheduling."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

import pytest

from test_gateway import OWNER, Channel, Runtime, new, send
from unilark.adapters.sidecars.views import SessionView, StepView
from unilark.conversation.channel import Action
from unilark.conversation.hub import Hub
from unilark.policy.redact import Redactor
from unilark.store.gateway import GatewayStore


@pytest.fixture
def gateway(tmp_path):
    store = GatewayStore(tmp_path / "state.db")
    store.set_owner(OWNER)
    runtime, channel = Runtime(), Channel()
    hub = Hub(store, runtime, channel, OWNER, "profile", Redactor(("fixture-app-secret",)))
    yield hub, store, runtime, channel
    store.close()


def rendered(store, *, mode, binding=None):
    row = store.db.execute(
        "SELECT c.* FROM cards c JOIN ui_panels p ON p.id=c.id "
        "WHERE p.mode=? AND p.binding IS ? ORDER BY p.rowid DESC LIMIT 1",
        (mode, binding),
    ).fetchone()
    assert row is not None
    return dict(row)


def click(store, card, label, *, binding=None, fields=None, event=None):
    def buttons(value):
        if isinstance(value, dict):
            if value.get("tag") == "button":
                yield value
            for child in value.values():
                yield from buttons(child)
        elif isinstance(value, list):
            for child in value:
                yield from buttons(child)

    for button in buttons(json.loads(card["payload"])):
        if button["text"]["content"] != label:
            continue
        token = button["value"]["token"]
        intent = json.loads(
            store.db.execute("SELECT body FROM ui_actions WHERE token=?", (token,)).fetchone()[0]
        )
        if binding and intent.get("binding") != binding:
            continue
        return Action(
            OWNER,
            event or "action:" + uuid.uuid4().hex,
            card["message_id"],
            token,
            "ui",
            fields or {},
        )
    raise AssertionError(f"Button not found: {label}")


async def test_list_switch_preserves_original_queue_and_panel_has_no_quote_target(gateway):
    hub, store, runtime, _ = gateway
    first = await new(hub, store)
    await send(hub, "first task")
    await hub.tick()
    second = await new(hub, store)
    await send(hub, "/list")
    await hub.tick()
    panel = rendered(store, mode="sessions")
    assert panel["binding"] is None
    assert panel["payload"].count("● 当前输入") == 1
    action = click(store, panel, "切换到此会话", binding=first["id"])
    await hub.action(action)
    await hub.tick()
    assert store.target(OWNER) == first["id"]
    await send(hub, "next for first")
    await hub.action(action)  # Replay must not create another intention.
    await hub.tick()
    assert [o["binding_id"] for o in store.operations(OWNER)] == [first["id"], first["id"]]
    assert len(runtime.sent) == 1
    await send(hub, "ambiguous quote", reply_to=panel["message_id"])
    assert len(store.operations(OWNER)) == 2
    assert first["id"] != second["id"]


async def test_commands_form_captures_workspace_and_is_one_shot_after_restart(gateway, tmp_path):
    hub, store, runtime, channel = gateway
    directory = tmp_path / "first"
    directory.mkdir()
    await send(hub, f"/cwd {directory}")
    for command in ("/", "/help", "/li", "/unknown"):
        await send(hub, command)
    await hub.tick()
    assert not runtime.sent and not store.operations(OWNER)
    await hub.action(click(store, rendered(store, mode="commands"), "新建会话"))
    await hub.tick()
    form = rendered(store, mode="new")
    submission = click(store, form, "创建并切换", fields={"title": "交互卡会话"})
    other = tmp_path / "second"
    other.mkdir()
    await send(hub, f"/cwd {other}")
    await hub.tick()
    assert rendered(store, mode="new")["payload"] == form["payload"]
    await hub.action(submission)
    await hub.action(replace(submission, event_id="different-double-click"))
    # Simulate restart after accepting callback but before processing its durable intent.
    store.recover_gateway()
    restored = Hub(store, runtime, channel, OWNER, "profile", hub.redactor)
    await restored.tick()
    await restored.tick()
    sessions = store.sessions(OWNER)
    assert len(sessions) == 1 and sessions[0]["title"] == "交互卡会话"
    assert store.journal.context(sessions[0]["id"])["workspace"] == str(directory)
    assert "此表单已提交" in rendered(store, mode="new")["payload"]
    await restored.action(submission)
    await restored.tick()
    assert len(store.sessions(OWNER)) == 1


async def test_cancel_button_keeps_original_session_after_switch(gateway):
    hub, store, _, _ = gateway
    first = await new(hub, store)
    await send(hub, "running")
    await hub.tick()
    await send(hub, "cancel me")
    await send(hub, "/status")
    await hub.tick()
    detail = rendered(store, mode="detail", binding=first["id"])
    cancel = click(store, detail, "撤销这项输入")
    # Switch without refreshing the old card, then click its original action.
    await send(hub, "/new second")
    second = store.target(OWNER)
    await hub.action(cancel)
    await hub.tick()
    operations = store.operations(OWNER)
    assert operations[0]["state"] == "ACCEPTED"
    assert operations[1]["state"] == "REJECTED"
    assert operations[1]["binding_id"] == first["id"]
    assert store.target(OWNER) == second


async def test_wrong_identity_message_value_and_expired_actions_do_not_switch(gateway):
    hub, store, _, _ = gateway
    first = await new(hub, store)
    second = await new(hub, store)
    await send(hub, "/list")
    await hub.tick()
    action = click(store, rendered(store, mode="sessions"), "切换到此会话", binding=first["id"])
    for changed in (
        replace(action, owner=replace(OWNER, user="stranger")),
        replace(action, message_id="another-message"),
        replace(action, decision="allow"),
        replace(action, fields={"binding": first["id"]}),
    ):
        await hub.action(changed)
    with store.db:
        store.db.execute("UPDATE ui_actions SET expires=0 WHERE token=?", (action.token,))
    await hub.action(action)
    await hub.tick()
    assert store.target(OWNER) == second["id"]
    assert not [r for r in store.requests(OWNER) if r["action"] == "panel"]


async def test_stop_button_revalidates_native_turn_then_stops_original_session(gateway):
    hub, store, runtime, _ = gateway
    first = await new(hub, store)
    await send(hub, "task one")
    await hub.tick()
    await send(hub, "/status")
    await hub.tick()
    action = click(store, rendered(store, mode="detail", binding=first["id"]), "停止并暂停队列")
    runtime.views[first["native_id"]] = SessionView(False, "running", "new-turn")
    await hub.action(action)
    await hub.tick()
    await hub.tick()
    assert runtime.stopped == []
    assert store.session(OWNER, first["id"])["queue_state"] == "PAUSED"
    current = click(store, rendered(store, mode="detail", binding=first["id"]), "停止并暂停队列")
    await send(hub, "/new other")
    selected = store.target(OWNER)
    await hub.action(current)
    await hub.tick()
    await hub.tick()
    assert runtime.stopped == [first["native_id"]]
    assert store.target(OWNER) == selected


async def test_paging_filters_stability_and_queued_reason(gateway):
    hub, store, runtime, channel = gateway
    sessions = []
    for index in range(8):
        binding = store.add_session(OWNER, "profile", str(uuid.uuid4()), "ACTIVE", f"会话{index}")
        session = store.session(OWNER, binding)
        sessions.append(session)
        runtime.views[session["native_id"]] = SessionView(True, "idle", "-1")
    store.archive(OWNER, sessions[0]["id"])
    await send(hub, "/list")
    await hub.tick()
    panel = rendered(store, mode="sessions")
    assert "第 1 / 2 页" in panel["payload"]
    count = len(channel.sent)
    await hub.tick()
    assert len(channel.sent) == count  # Stable state must not continuously update cards.
    await hub.action(click(store, panel, "下一页"))
    await hub.tick()
    panel = rendered(store, mode="sessions")
    assert "第 2 / 2 页" in panel["payload"]
    assert panel["binding"] is None
    await hub.action(click(store, panel, "已归档"))
    await hub.tick()
    panel = rendered(store, mode="sessions")
    assert "1 个会话" in panel["payload"] and "恢复会话" in panel["payload"]
    runtime.views[sessions[1]["native_id"]] = SessionView(False, "running", "external-turn")
    await send(hub, "waiting for other session")
    await send(hub, "/status")
    await hub.tick()
    detail = rendered(store, mode="detail", binding=store.target(OWNER))
    assert "等待其他会话结束" in detail["payload"]


async def test_secret_in_form_is_never_saved_and_missing_title_can_be_corrected(gateway):
    hub, store, _, _ = gateway
    await send(hub, "/")
    await hub.tick()
    await hub.action(click(store, rendered(store, mode="commands"), "新建会话"))
    await hub.tick()
    form = rendered(store, mode="new")
    action = click(store, form, "创建并切换")
    await hub.action(replace(action, fields={"title": "fixture-app-secret"}))
    await hub.action(replace(action, event_id="missing", fields={"title": " "}))
    assert not store.sessions(OWNER)
    assert "fixture-app-secret" not in "\n".join(store.db.iterdump())
    await hub.action(replace(action, event_id="valid", fields={"title": "safe title"}))
    await hub.tick()
    await hub.tick()
    assert len(store.sessions(OWNER)) == 1


async def test_archive_resume_and_continue_buttons_round_trip(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    runtime.views[session["native_id"]] = SessionView(
        True, "idle", "finished", [StepView(0, "assistant", "done", "completed history")]
    )
    await send(hub, "/status")
    await hub.tick()
    for label, expected in (("归档会话", "ARCHIVED"), ("恢复会话", "ACTIVE")):
        detail = rendered(store, mode="detail", binding=session["id"])
        await hub.action(click(store, detail, label))
        await hub.tick()
        await hub.tick()
        assert store.session(OWNER, session["id"])["state"] == expected
        assert store.session(OWNER, session["id"])["queue_state"] == "PAUSED"
    assert store.target(OWNER) == session["id"]
    detail = rendered(store, mode="detail", binding=session["id"])
    await hub.action(click(store, detail, "继续队列"))
    await hub.tick()
    await hub.tick()
    assert store.session(OWNER, session["id"])["queue_state"] == "OPEN"
