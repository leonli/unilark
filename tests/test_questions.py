from __future__ import annotations

import time
from dataclasses import replace

import pytest

from test_gateway import OWNER, new, send
from test_gateway import gateway as gateway
from unilark.adapters.sidecars.views import Answer, Question, SessionView, StepView
from unilark.conversation.channel import Action
from unilark.conversation.questions import parse


def test_multiple_question_text_is_mapped_without_guessing():
    questions = (
        Question("color", (("blue", "Blue"), ("green", "Green"))),
        Question("tools", (("a", "One"), ("b", "Two")), True),
        Question("comment"),
    )
    assert parse(questions, "Blue\nOne, Two\nmy words") == (
        Answer(("blue",)),
        Answer(("a", "b")),
        Answer(text="my words"),
    )
    with pytest.raises(ValueError):
        parse(questions, "not enough lines")
    with pytest.raises(ValueError):
        parse(questions, "", "answer:blue")


async def waiting(hub, store, runtime):
    session = await new(hub, store)
    runtime.views[session["native_id"]] = SessionView(
        False,
        "waiting",
        "anchor",
        [
            StepView(
                0,
                "tool",
                "waiting",
                tool="ask_question",
                fingerprint="question-fingerprint",
                questions=(Question("Color?", (("b", "Blue"), ("g", "Green"))),),
            )
        ],
    )
    await hub.tick()
    row = dict(store.db.execute("SELECT * FROM interactions WHERE kind='question'").fetchone())
    return session, row, "om_" + row["card"]


async def test_quoted_question_answer_is_not_a_new_task_and_is_once_only(gateway):
    hub, store, runtime, _ = gateway
    session, interaction, mid = await waiting(hub, store, runtime)
    answered = []

    async def answer(native, index, answers, *, fingerprint, cancel=False):
        answered.append((native, answers, cancel))
        runtime.views[native] = SessionView(
            True,
            "idle",
            "done",
            [StepView(0, "tool", "done", tool="ask_question", fingerprint=fingerprint)],
        )
        return "DONE"

    runtime.answer = answer
    message = await send(hub, "Blue", reply_to=mid)
    await hub.accept(message)
    await hub.tick()
    assert answered == [(session["native_id"], (Answer(("b",)),), False)]
    assert not store.operations(OWNER) and not runtime.sent
    await send(hub, "Green", reply_to=mid)
    await hub.tick()
    assert len(answered) == 1 and not runtime.sent


async def test_question_button_is_bound_to_owner_message_and_original_request(gateway):
    hub, store, runtime, _ = gateway
    _, interaction, mid = await waiting(hub, store, runtime)
    foreign = replace(OWNER, user="different")
    for owner, message in [(foreign, mid), (OWNER, "other-message")]:
        await hub.action(
            Action(owner, "wrong-" + message, message, interaction["token"], "answer:b")
        )
    assert (
        store.db.execute("SELECT status FROM interactions WHERE kind='question'").fetchone()[0]
        == "OPEN"
    )
    runtime.views[next(iter(runtime.views))] = SessionView(
        False,
        "waiting",
        "new",
        [
            StepView(
                0,
                "tool",
                "waiting",
                tool="ask_question",
                fingerprint="new",
                questions=(Question("Other?"),),
            )
        ],
    )
    await hub.action(Action(OWNER, "old-click", mid, interaction["token"], "answer:b"))
    await hub.tick()
    assert (
        store.db.execute("SELECT status FROM inbox WHERE event='old-click'").fetchone()[0]
        == "REJECTED"
    )


async def test_question_expiry_cancels_original_question_without_granting_permission(gateway):
    hub, store, runtime, _ = gateway
    _, interaction, _ = await waiting(hub, store, runtime)
    store.db.execute(
        "UPDATE interactions SET expires=? WHERE token=?", (time.time() - 1, interaction["token"])
    )
    store.db.commit()
    canceled = []

    async def answer(native, index, answers, *, fingerprint, cancel=False):
        canceled.append(cancel)
        runtime.views[native] = SessionView(True, "idle", "done")
        return "DONE"

    runtime.answer = answer
    await hub.tick()
    await hub.tick()
    assert canceled == [True]
    assert not runtime.resolved


async def test_workspace_is_pinned_when_new_session_is_received(gateway, tmp_path):
    hub, store, runtime, _ = gateway
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    await send(hub, "/cwd " + str(one))
    await send(hub, "/new A")
    first = store.target(OWNER)
    await send(hub, "/cwd " + str(two))
    await send(hub, "/new B")
    second = store.target(OWNER)
    assert store.journal.context(first)["workspace"] == str(one)
    assert store.journal.context(second)["workspace"] == str(two)
    await hub.tick()
    assert store.journal.context(first)["workspace"] == str(one)
