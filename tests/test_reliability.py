from __future__ import annotations

import json

import pytest

from test_gateway import OWNER, new, send
from test_gateway import gateway as gateway
from unilark.adapters.sidecars.interface import CapabilitySnapshot
from unilark.adapters.sidecars.views import Rejected, SessionView, StepView
from unilark.store.gateway import GatewayStore


async def test_late_stop_cannot_be_undone_by_an_earlier_queued_continue(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    store.pause(session["id"])
    await send(hub, "must stay queued")
    await send(hub, "/continue")
    await send(hub, "/stop")
    await hub.tick()
    assert not runtime.sent
    assert store.session(OWNER, session["id"])["queue_state"] == "PAUSED"


async def test_unsupported_runtime_cannot_steer_or_expose_control_buttons(gateway):
    hub, store, runtime, channel = gateway
    session = await new(hub, store)
    hub.capabilities = CapabilitySnapshot("restricted", "test", "test")
    await send(hub, "/steer no")
    await send(hub, "/stop")
    runtime.views[session["native_id"]] = SessionView(
        False,
        "waiting",
        "anchor",
        [StepView(0, "tool", "waiting", tool="run_command", permission=True)],
    )
    await hub.tick()
    assert not runtime.sent and not runtime.stopped
    assert store.db.execute("SELECT count(*) FROM interactions").fetchone()[0] == 0
    assert "不支持" in json.dumps(channel.sent, ensure_ascii=False)


async def test_shutdown_during_observation_keeps_saved_input_unsubmitted(gateway):
    hub, store, runtime, _ = gateway
    await new(hub, store)
    await send(hub, "next boot")
    original = runtime.view

    async def stop_during_view(native):
        hub.stopping.set()
        return await original(native)

    runtime.view = stop_during_view
    await hub.tick()
    assert not runtime.sent
    assert store.operations(OWNER)[0]["state"] == "QUEUED"


async def test_explicit_runtime_rejection_is_not_unknown(gateway):
    hub, store, runtime, _ = gateway
    await new(hub, store)

    async def reject(*args, **kwargs):
        raise Rejected("explicit rejection")

    runtime.send = reject
    await send(hub, "rejected task")
    await hub.tick()
    assert store.operations(OWNER)[0]["state"] == "REJECTED"


async def test_acknowledge_unknown_does_not_resend_or_claim_no_execution(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    await send(hub, "unknown")
    op = store.operations(OWNER)[0]
    store.claim(op["request_id"])
    store.recover_gateway()
    store.acknowledge_unknown(OWNER, "input", op["request_id"])
    await hub.tick()
    assert store.get(op["request_id"])["state"] == "DISMISSED"
    assert store.session(OWNER, session["id"])["queue_state"] == "PAUSED"
    assert not runtime.sent
    assert store.db.execute("SELECT count(*) FROM recovery_audit").fetchone()[0] == 1
    with pytest.raises(ValueError):
        store.acknowledge_unknown(OWNER, "input", op["request_id"])


async def test_unknown_new_card_cannot_be_retried_after_manual_dismissal(gateway):
    hub, store, _, _ = gateway
    session = await new(hub, store)
    cid = store.put_card("lost", OWNER, session["id"], {"safe": "body"})
    store.delivery_started(cid)
    store.recover_gateway()
    with pytest.raises(ValueError):
        store.retry_delivery(OWNER, cid)
    store.acknowledge_unknown(OWNER, "card", cid)
    store.put_card("lost", OWNER, session["id"], {"safe": "new body"})
    assert cid not in [r["id"] for r in store.dirty_cards(OWNER)]


async def test_observation_gap_survives_recovery_without_storing_bodies(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    runtime.views[session["native_id"]] = SessionView(
        True, "idle", "a", [StepView(0, "user", "done", "private observation body")]
    )
    await hub.tick()
    store.journal.unavailable(session["id"])
    await hub.tick()
    record = store.journal.observation(session["id"])
    assert record["gap_since"] and record["gap_until"]
    assert record["error"] == ""
    assert "private observation body" not in json.dumps(record)


async def test_archive_rejects_quoted_input_and_resume_keeps_original_native(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    runtime.views[session["native_id"]] = SessionView(
        True, "idle", "a", [StepView(0, "assistant", "done", "done")]
    )
    await hub.tick()
    cid = store.card_id(OWNER, f"step:{session['id']}:0:0")
    await send(hub, "/archive")
    await hub.tick()
    assert store.target(OWNER) is None
    await send(hub, "do not execute", reply_to="om_" + cid)
    assert not store.operations(OWNER)
    await send(hub, "/resume " + session["id"])
    await hub.tick()
    restored = store.session(OWNER, store.target(OWNER))
    assert restored["native_id"] == session["native_id"]
    assert restored["queue_state"] == "PAUSED"


async def test_application_credential_never_enters_input_ledger(gateway):
    hub, store, _, _ = gateway
    await new(hub, store)
    await send(hub, "my fixture-app-secret")
    assert not store.operations(OWNER)
    assert all("fixture-app-secret" not in r[0] for r in store.db.execute("SELECT body FROM inbox"))


def test_schema_upgrade_preserves_v1_data_and_blocks_old_code(tmp_path):
    path = tmp_path / "state.db"
    store = GatewayStore(path)
    store.set_owner(OWNER)
    store.db.execute("PRAGMA user_version=1")
    store.close()
    store = GatewayStore(path)
    assert store.owner(OWNER.account) == OWNER
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
    store.close()
