from __future__ import annotations

import json

import pytest

from test_gateway import OWNER, new, send
from test_gateway import gateway as gateway
from unilark.adapters.sidecars.views import SessionView, StepView
from unilark.onboarding.acceptance import contains_text, evidence


async def test_acceptance_requires_native_receipt_and_delivered_reply(gateway):
    hub, store, runtime, channel = gateway
    session = await new(hub, store)
    await send(hub, "acceptance input")
    await hub.tick()
    before = runtime.views[session["native_id"]]
    view = SessionView(
        True,
        "idle",
        "done",
        before.steps + [StepView(1, "assistant", "done", "line one\nline two")],
    )
    with pytest.raises(ValueError, match="delivery"):
        evidence(store, OWNER, session["id"], view, "text_roundtrip")
    runtime.views[session["native_id"]] = view
    await hub.tick()
    proof = evidence(store, OWNER, session["id"], view, "text_roundtrip")
    assert proof["message_id"]
    with pytest.raises(ValueError, match="input"):
        evidence(store, OWNER, session["id"], view, "desktop_ui_relay")
    assert contains_text({"content": [{"text": "line one\nline two"}]}, proof["text"])
    with pytest.raises(ValueError, match="Allow"):
        evidence(store, OWNER, session["id"], view, "lark_permission_and_stop")


async def test_idle_stop_is_not_running_stop_acceptance(gateway):
    hub, store, runtime, _ = gateway
    session = await new(hub, store)
    view = SessionView(
        True,
        "idle",
        "done",
        [StepView(0, "tool", "done"), StepView(1, "assistant", "done", "finished")],
    )
    runtime.views[session["native_id"]] = view
    store.receive(
        OWNER,
        "allow-proof",
        "resolve",
        session["id"],
        json.dumps({"decision": "allow", "step": 0, "applied_at": 1}),
    )
    store.request_state(OWNER, "allow-proof", "DONE")
    store.receive(
        OWNER,
        "stop-proof",
        "stop",
        session["id"],
        json.dumps({"stop_was_busy": False, "stop_idle_confirmed": True, "stop_observed_at": 2}),
    )
    store.request_state(OWNER, "stop-proof", "DONE")
    await hub.tick()
    with pytest.raises(ValueError, match="Allow"):
        evidence(store, OWNER, session["id"], view, "lark_permission_and_stop")
    store.control_observation(OWNER, "stop-proof", {"stop_was_busy": True})
    assert evidence(store, OWNER, session["id"], view, "lark_permission_and_stop")["message_id"]
