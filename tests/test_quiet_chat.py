"""Chat-visible message counts and turn/control behavior, through the full Hub."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from test_gateway import OWNER
from test_rooms import create, gateway, group_send  # noqa: F401
from unilark.adapters.sidecars.views import Answer, Question, SessionView, StepView
from unilark.conversation.channel import Action
from unilark.conversation.hub import Hub
from unilark.policy.redact import Redactor
from unilark.projection.cards import card

# The imported pytest fixture intentionally shares the parameter name.
# ruff: noqa: F811


def creates(channel, chat):
    return [p for c, p, _, mid in channel.sent if c == chat and mid is None]


def final_cards(store, chat):
    return [
        json.loads(r[0])
        for r in store.db.execute(
            "SELECT payload FROM cards c JOIN card_chats d ON d.card=c.id WHERE d.chat=?", (chat,)
        )
    ]


async def test_many_tool_updates_use_one_progress_then_one_final_message(gateway):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    hub.turns.redactor = Redactor(("fixture-secret",))
    await group_send(hub, room["chat"], "hello")
    await hub.tick()
    native = session["native_id"]
    user = runtime.views[native].steps[0]
    for index in range(1, 20):
        steps = [
            user,
            StepView(index, "assistant", "done", "I will investigate" if index % 2 else ""),
            StepView(
                index + 1, "tool", "running", tool="run_command", command="echo fixture-secret"
            ),
        ]
        runtime.views[native] = SessionView(False, "running", user.operation_ids[0], steps)
        await hub.tick()
    assert len(creates(channel, room["chat"])) == 1
    progress = json.dumps(final_cards(store, room["chat"]), ensure_ascii=False)
    assert "正在执行命令" in progress and "echo" in progress and "fixture-secret" not in progress
    runtime.views[native] = SessionView(
        True,
        "idle",
        user.operation_ids[0],
        [
            user,
            StepView(20, "tool", "done", tool="run_command"),
            StepView(21, "assistant", "done", "# 最终答复\n\n**已完成**。"),
        ],
    )
    await hub.tick()
    assert len(creates(channel, room["chat"])) == 2
    payloads = final_cards(store, room["chat"])
    assert len(payloads) == 2 and all("header" not in p for p in payloads)
    assert payloads[-1]["schema"] == "2.0"
    encoded = json.dumps(payloads, ensure_ascii=False)
    assert "最终答复" in encoded
    for noise in ("ACCEPTED", "请求 ", "I will investigate", "AGY 回复", "项目：", "本地队列"):
        assert noise not in encoded
    await hub.tick()
    assert len(creates(channel, room["chat"])) == 2


async def test_queue_is_compact_and_next_turn_does_not_overwrite_first_answer(gateway):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    await group_send(hub, room["chat"], "one")
    await hub.tick()
    native = session["native_id"]
    user = runtime.views[native].steps[0]
    await group_send(hub, room["chat"], "two")
    await group_send(hub, room["chat"], "three")
    await hub.tick()
    assert len(creates(channel, room["chat"])) == 1
    assert "2 条消息排队" in json.dumps(final_cards(store, room["chat"]), ensure_ascii=False)
    runtime.views[native] = SessionView(
        True, "idle", "one", [user, StepView(1, "assistant", "done", "answer one")]
    )
    await hub.tick()
    assert "answer one" in json.dumps(final_cards(store, room["chat"]))
    assert len(runtime.sent) == 2
    # Preserve real history in this fixture when the second input reaches the native timeline.
    second = replace(runtime.views[native].steps[0], index=2)
    runtime.views[native] = SessionView(
        False, "running", "two", [user, StepView(1, "assistant", "done", "answer one"), second]
    )
    await hub.tick()
    assert len(creates(channel, room["chat"])) == 3
    assert "answer one" in json.dumps(final_cards(store, room["chat"]))


async def test_restart_reuses_live_card_and_long_result_only_adds_result_parts(gateway):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    await group_send(hub, room["chat"], "long answer")
    await hub.tick()
    await hub.tick()
    before = len(creates(channel, room["chat"]))
    restored = Hub(store, runtime, channel, OWNER, "profile", hub.redactor, enable_rooms=True)
    store.recover_gateway()
    await restored.tick()
    assert len(creates(channel, room["chat"])) == before == 1
    user = runtime.views[session["native_id"]].steps[0]
    runtime.views[session["native_id"]] = SessionView(
        True, "idle", "done", [user, StepView(1, "assistant", "done", "line\n" * 2000)]
    )
    await restored.tick()
    cards = [p for p in final_cards(store, room["chat"]) if p.get("schema") == "2.0"]
    assert len(cards) > 1 and all("header" not in p for p in cards)
    assert all(p["schema"] == "2.0" for p in cards)
    total = len(creates(channel, room["chat"]))
    await restored.tick()
    assert len(creates(channel, room["chat"])) == total


async def test_stop_control_belongs_to_live_turn_and_is_not_reported_as_success(gateway):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    await group_send(hub, room["chat"], "run")
    await hub.tick()
    await hub.tick()
    row = store.db.execute(
        "SELECT c.message_id,i.token FROM cards c JOIN interactions i ON i.card=c.id "
        "WHERE i.kind='stop' AND i.status='OPEN'"
    ).fetchone()
    await hub.action(
        Action(OWNER, "stop-click", row["message_id"], row["token"], "stop", chat=room["chat"])
    )
    await hub.tick()
    assert runtime.stopped == [session["native_id"]]
    assert len(creates(channel, room["chat"])) == 1
    assert store.session(OWNER, session["id"])["queue_state"] == "PAUSED"
    output = json.dumps(final_cards(store, room["chat"]), ensure_ascii=False)
    assert "已完成" not in output and "本轮已停止" in output


async def test_completed_tool_plan_is_not_misrepresented_as_final_answer(gateway):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    await group_send(hub, room["chat"], "task")
    await hub.tick()
    user = runtime.views[session["native_id"]].steps[0]
    runtime.views[session["native_id"]] = SessionView(
        True,
        "idle",
        "done",
        [
            user,
            StepView(1, "assistant", "done", "I will write a document"),
            StepView(2, "tool", "error", tool="run_command"),
        ],
    )
    await hub.tick()
    text = json.dumps(final_cards(store, room["chat"]), ensure_ascii=False)
    assert "I will write" not in text and "任务遇到问题" in text


async def test_upgrade_preserves_legacy_history_without_replaying_it(gateway):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    binding, native = session["id"], session["native_id"]
    history = [StepView(0, "user", "done", "old"), StepView(1, "assistant", "done", "OLD ANSWER")]
    store.journal.observe(binding, SessionView(True, "idle", "old", history))
    hub.save_parts("room:" + room["chat"] + ":state:" + binding, binding, [card("AGY", "idle")])
    hub.save_parts(
        "room:" + room["chat"] + ":step:" + binding + ":1", binding, [card("AGY", "OLD ANSWER")]
    )
    store.db.execute("DELETE FROM chat_layouts WHERE binding=?", (binding,))
    runtime.views[native] = SessionView(True, "idle", "old", history)
    await hub.tick()
    before = len(creates(channel, room["chat"]))
    assert not hub.turns.rows(binding)
    runtime.views[native] = SessionView(
        True,
        "idle",
        "new",
        history
        + [StepView(2, "user", "done", "new"), StepView(3, "assistant", "done", "NEW ANSWER")],
    )
    await hub.tick()
    assert len(creates(channel, room["chat"])) == before + 1
    assert "OLD ANSWER" not in json.dumps(creates(channel, room["chat"])[-1])
    assert "NEW ANSWER" in json.dumps(creates(channel, room["chat"])[-1])


@pytest.mark.parametrize("question", [False, True])
async def test_required_interaction_stays_actionable_without_receipt_noise(gateway, question):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    await group_send(hub, room["chat"], "task")
    await hub.tick()
    native = session["native_id"]
    user = runtime.views[native].steps[0]
    tool = StepView(
        1,
        "tool",
        "waiting",
        tool="ask_question" if question else "run_command",
        permission=not question,
        fingerprint="decision",
        resource="printf hello",
        questions=(Question("选哪个？", (("a", "第一项"),)),) if question else (),
    )
    runtime.views[native] = SessionView(False, "waiting", user.operation_ids[0], [user, tool])
    await hub.tick()
    assert len(creates(channel, room["chat"])) == 2
    row = store.db.execute(
        "SELECT i.token,c.message_id FROM interactions i JOIN cards c ON c.id=i.card "
        "WHERE i.kind=?",
        ("question" if question else "permission",),
    ).fetchone()
    answered = []

    async def answer(native, index, answers, *, fingerprint, cancel=False):
        answered.append(answers)
        return "DONE"

    runtime.answer = answer
    await hub.action(
        Action(
            OWNER,
            "decision-click",
            row["message_id"],
            row["token"],
            "answer:a" if question else "allow",
            chat=room["chat"],
        )
    )
    await hub.tick()
    assert (
        answered == [(Answer(("a",)),)]
        if question
        else runtime.resolved == [(native, 1, True, "decision")]
    )
    runtime.views[native] = SessionView(
        True,
        "idle",
        user.operation_ids[0],
        [user, replace(tool, status="done"), StepView(2, "assistant", "done", "RESULT")],
    )
    await hub.tick()
    assert len(creates(channel, room["chat"])) == 3
    assert "RESULT" in json.dumps(final_cards(store, room["chat"]))


async def test_old_stop_cannot_interrupt_next_turn(gateway):
    hub, store, runtime, channel = gateway
    session, room = await create(hub, store)
    await group_send(hub, room["chat"], "first")
    await hub.tick()
    await hub.tick()
    row = store.db.execute(
        "SELECT i.token,c.message_id FROM interactions i JOIN cards c ON c.id=i.card "
        "WHERE i.kind='stop'"
    ).fetchone()
    native = session["native_id"]
    user = runtime.views[native].steps[0]
    runtime.views[native] = SessionView(
        True,
        "idle",
        user.operation_ids[0],
        [user, StepView(1, "assistant", "done", "first answer")],
    )
    await hub.tick()
    runtime.views[native] = SessionView(
        False,
        "running",
        "second-anchor",
        [
            user,
            StepView(1, "assistant", "done", "first answer"),
            StepView(2, "user", "done", "second"),
        ],
    )
    await hub.tick()
    await hub.action(
        Action(OWNER, "late-stop", row["message_id"], row["token"], "stop", chat=room["chat"])
    )
    await hub.tick()
    assert not runtime.stopped
