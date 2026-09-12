from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from unilark.adapters.sidecars.views import SessionView, StepView
from unilark.conversation.channel import Delivery, Message, Owner
from unilark.conversation.hub import Hub
from unilark.policy.redact import Redactor
from unilark.store.gateway import GatewayStore

OWNER = Owner("lark:cli_fixture", "tenant", "ou_owner", "oc_private")


class Runtime:
    from unilark.adapters.sidecars.agy.capabilities import CAPABILITIES as capabilities

    def __init__(self):
        self.views = {}
        self.sent = []
        self.resolved = []
        self.stopped = []
        self.fail_send = False
        self.fail_stop = False

    async def create(self, session_id, *, workspace=""):
        self.views[session_id] = SessionView(True, "idle", "-1")

    async def view(self, session_id):
        return self.views[session_id]

    async def send(self, session_id, text, request_id, *, steer=False):
        self.sent.append((session_id, text, request_id, steer))
        self.views[session_id] = SessionView(
            False,
            "running",
            request_id,
            [StepView(0, "user", "done", text, operation_ids=(request_id,))],
        )
        if self.fail_send:
            raise TimeoutError("ack lost")

    async def stop(self, session_id):
        self.stopped.append(session_id)
        if self.fail_stop:
            raise TimeoutError("stop ack lost")
        self.views[session_id] = replace(self.views[session_id], idle=True, status="idle")
        return True

    async def resolve_permission(self, session_id, index, *, allow, fingerprint=None):
        self.resolved.append((session_id, index, allow, fingerprint))
        return "CORTEX_STEP_STATUS_ERROR"


class Channel:
    def __init__(self):
        self.sent = []
        self.result = None

    async def deliver(self, chat, card, request_id, message_id=None):
        self.sent.append((chat, card, request_id, message_id))
        return self.result or Delivery("SENT", message_id or "om_" + request_id)


@pytest.fixture
def gateway(tmp_path):
    store = GatewayStore(tmp_path / "state.db")
    store.set_owner(OWNER)
    runtime, channel = Runtime(), Channel()
    hub = Hub(store, runtime, channel, OWNER, "profile", Redactor(("fixture-app-secret",)))
    yield hub, store, runtime, channel
    store.close()


async def send(hub, text, event=None, reply_to=None):
    message = Message(OWNER, event or uuid.uuid4().hex, text, time.time(), reply_to)
    await hub.accept(message)
    return message


async def new(hub, store):
    await send(hub, "/new")
    await hub.tick()
    return store.session(OWNER, store.target(OWNER))


async def test_duplicate_and_quoted_reply_keep_original_binding(gateway):
    hub, store, runtime, _ = gateway
    first = await new(hub, store)
    message = await send(hub, "first task")
    second = await new(hub, store)
    await hub.accept(message)
    original_card = store.card_id(OWNER, "reply:" + message.event_id + ":0")
    await send(hub, "quoted task", reply_to="om_" + original_card)
    operations = store.operations(OWNER)
    assert len(operations) == 2
    assert all(o["binding_id"] == first["id"] for o in operations)
    assert store.target(OWNER) == second["id"]
    await send(hub, "unknown quote", reply_to="unrelated")
    assert len(store.operations(OWNER)) == 2
    assert len(runtime.sent) == 1


async def test_busy_queue_steer_stop_and_continue(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    await send(hub, "task 1")
    await hub.tick()
    await send(hub, "task 2")
    await hub.tick()
    assert len(runtime.sent) == 1
    await send(hub, "/steer refine task 1")
    await hub.tick()
    assert runtime.sent[-1][-1] is True
    await send(hub, "/stop")
    await hub.tick()
    assert store.session(OWNER, session["id"])["queue_state"] == "PAUSED"
    assert len(runtime.sent) == 2
    await send(hub, "/continue")
    await hub.tick()
    assert len(runtime.sent) == 3
    assert runtime.sent[-1][1] == "task 2"


async def test_unknown_send_reconciles_without_resending(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    runtime.fail_send = True
    await send(hub, "one task")
    await hub.tick()
    assert store.operations(OWNER)[0]["state"] == "UNKNOWN"
    await hub.tick()
    assert store.operations(OWNER)[0]["state"] == "ACCEPTED"
    assert len(runtime.sent) == 1
    assert store.session(OWNER, session["id"])["queue_state"] == "PAUSED"


async def test_duplicate_native_tags_block_all_workspace_submissions(gateway):
    hub, store, runtime, _ = gateway
    first = await new(hub, store)
    await send(hub, "one")
    await hub.tick()
    second = await new(hub, store)
    rid = store.operations(OWNER)[0]["request_id"]
    runtime.views[first["native_id"]] = SessionView(
        True, "idle", "x", [StepView(i, "user", "done", operation_ids=(rid,)) for i in range(2)]
    )
    await send(hub, "two")
    await hub.tick()
    assert store.operations(OWNER)[0]["state"] == "UNKNOWN"
    assert len(runtime.sent) == 1
    assert second["id"] != first["id"]


async def test_cards_stable_permissions_bound_and_expiry_denies_once(gateway):
    hub, store, runtime, channel = gateway
    session = await new(hub, store)
    step = StepView(
        1,
        "tool",
        "waiting",
        tool="run_command",
        permission=True,
        fingerprint="request-one",
        resource="SECRET_TOKEN=hidden " + "x" * 5000,
    )
    runtime.views[session["native_id"]] = SessionView(False, "waiting", "turn-one", [step])
    await hub.tick()
    count = len(channel.sent)
    await hub.tick()
    assert len(channel.sent) == count
    interaction = dict(
        store.db.execute("SELECT * FROM interactions WHERE kind='permission'").fetchone()
    )
    assert "hidden" not in json.dumps(channel.sent)
    with store.db:
        store.db.execute("UPDATE interactions SET expires=0 WHERE token=?", (interaction["token"],))
    assert not store.consume(
        OWNER, interaction["token"], "om_" + interaction["card"], "allow", "expired"
    )
    await hub.tick()
    await hub.tick()
    await hub.tick()
    assert runtime.resolved == [(session["native_id"], 1, False, "request-one")]


async def test_action_replay_wrong_owner_chat_message_and_changed_request(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    step = StepView(2, "tool", "waiting", tool="run_command", permission=True, fingerprint="one")
    runtime.views[session["native_id"]] = SessionView(False, "waiting", "turn", [step])
    await hub.tick()
    interaction = dict(
        store.db.execute("SELECT * FROM interactions WHERE kind='permission'").fetchone()
    )
    token, mid = interaction["token"], "om_" + interaction["card"]
    assert not store.consume(replace(OWNER, user="stranger"), token, mid, "allow", "evil")
    assert not store.consume(replace(OWNER, chat="group"), token, mid, "allow", "evil")
    assert not store.consume(OWNER, token, "different-message", "allow", "evil")
    store.receive(OWNER, "already-seen", "noop", None, "")
    assert not store.consume(OWNER, token, mid, "allow", "already-seen")
    assert store.consume(OWNER, token, mid, "allow", "good")
    assert not store.consume(OWNER, token, mid, "deny", "duplicate")
    runtime.views[session["native_id"]] = SessionView(
        False, "waiting", "turn", [replace(step, fingerprint="different-request")]
    )
    await hub.tick()
    assert runtime.resolved == []
    assert (
        store.db.execute("SELECT status FROM inbox WHERE event='good'").fetchone()[0] == "REJECTED"
    )


async def test_ambiguous_stop_only_observes_and_never_retries(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    runtime.fail_stop = True
    runtime.views[session["native_id"]] = SessionView(False, "running", "turn")
    await send(hub, "/stop", event="stop")
    await hub.tick()
    await send(hub, "/continue", event="continue")
    await hub.tick()
    assert (
        store.db.execute("SELECT status FROM inbox WHERE event='continue'").fetchone()[0]
        == "REJECTED"
    )
    runtime.views[session["native_id"]] = SessionView(True, "idle", "turn")
    await hub.tick()
    assert len(runtime.stopped) == 1
    assert not store.unknown_requests(OWNER)
    assert store.session(OWNER, session["id"])["queue_state"] == "PAUSED"


async def test_outbox_ambiguous_create_retained_and_shrinking_chunks_retired(gateway):
    hub, store, _, channel = gateway
    channel.result = Delivery("UNKNOWN")
    hub.notify("long", None, "test", "x" * 8000)
    await hub.flush()
    assert len(channel.sent) == 3
    await hub.flush()
    assert len(channel.sent) == 3
    assert store.delivery_health(OWNER) == {"UNKNOWN": 3}
    hub.notify("long", None, "test", "short")
    rows = store.db.execute("SELECT payload FROM cards").fetchall()
    assert sum("内容已更新" in r[0] for r in rows) == 2


def test_recovery_is_durable_and_does_not_change_owner(tmp_path: Path):
    path = tmp_path / "state.db"
    store = GatewayStore(path)
    store.set_owner(OWNER)
    binding = store.add_session(OWNER, "profile", str(uuid.uuid4()), "ACTIVE", "task")
    store.accept_input(OWNER, "message", binding, "text", "input")
    op = store.operations(OWNER)[0]
    store.claim(op["request_id"])
    store.receive(OWNER, "stop", "stop", binding, "")
    store.request_state(OWNER, "stop", "SUBMITTING")
    cid = store.put_card("create", OWNER, binding, {"x": 1})
    store.delivery_started(cid)
    store.close()
    store = GatewayStore(path)
    try:
        store.recover_gateway()
        assert store.get(op["request_id"])["state"] == "UNKNOWN"
        assert store.session(OWNER, binding)["queue_state"] == "PAUSED"
        assert store.unknown_requests(OWNER)[0]["event"] == "stop"
        assert store.delivery_health(OWNER) == {"UNKNOWN": 1}
        assert store.claim(op["request_id"]) is None
        with pytest.raises(ValueError):
            store.set_owner(replace(OWNER, user="replacement"))
    finally:
        store.close()
