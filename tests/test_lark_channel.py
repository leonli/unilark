"""Installed SDK envelope, normalization, safety queue and background thread contract.

No network is connected. These tests are not Lark end-to-end acceptance.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from lark_channel.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_channel.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

from unilark.adapters.lark.channel import LarkChannel
from unilark.conversation.channel import Owner
from unilark.onboarding.credentials import Credentials
from unilark.store.gateway import GatewayStore

CREDS = Credentials("lark", "cli_fixture", "fixture-not-a-real-secret")
OWNER = Owner(CREDS.account, "tenant", "ou_owner", "oc_chat")


def envelope(**changes):
    data = dict(
        app="cli_fixture",
        tenant="tenant",
        user="ou_owner",
        chat="oc_chat",
        kind="text",
        sender="user",
        chat_type="p2p",
        text="hello",
    )
    data.update(changes)
    mid = "om_" + uuid.uuid4().hex
    return {
        "schema": "2.0",
        "header": {
            "app_id": data["app"],
            "tenant_key": data["tenant"],
            "event_id": "ev_" + mid,
            "event_type": "im.message.receive_v1",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": data["user"]},
                "sender_type": data["sender"],
                "tenant_key": data["tenant"],
            },
            "message": {
                "message_id": mid,
                "chat_id": data["chat"],
                "chat_type": data["chat_type"],
                "message_type": data["kind"],
                "create_time": str(int(time.time() * 1000)),
                "content": json.dumps({"text": data["text"]}),
                "parent_id": "om_original",
            },
        },
    }


@pytest.fixture
async def channel():
    adapter = LarkChannel(CREDS, OWNER)
    adapter.loop = asyncio.get_running_loop()
    adapter.sdk._ensure_bg_loop()
    yield adapter
    await adapter.disconnect()


async def dispatch(channel, data, action=False):
    method = channel.sdk._handle_interaction_event if action else channel.sdk._handle_message_event
    obj = P2CardActionTrigger(data) if action else P2ImMessageReceiveV1(data)
    await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(method(obj), channel.sdk._bg_loop))

    # Safety pipeline serializes dispatch asynchronously on the background loop.
    async def drained():
        await asyncio.sleep(0.06)

    await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(drained(), channel.sdk._bg_loop))


async def test_real_sdk_thread_to_sqlite_and_no_text_merging(channel, tmp_path):
    store = GatewayStore(tmp_path / "state.db")
    main_thread = threading.get_ident()
    messages = []

    async def received(msg):
        assert threading.get_ident() == main_thread
        store.audit("received")  # SQLite would throw on the SDK background thread.
        messages.append(msg)

    channel.on_message = received
    one, two = envelope(text="one"), envelope(text="two")
    try:
        await dispatch(channel, one)
        await dispatch(channel, two)
        await dispatch(channel, one)
        assert [m.text for m in messages] == ["one", "two"]
        assert all(m.owner == OWNER for m in messages)
        assert messages[0].reply_to == "om_original"
        assert store.db.execute("SELECT count(*) FROM audit").fetchone()[0] == 2
    finally:
        store.close()


@pytest.mark.parametrize(
    "changed",
    [
        {"app": "cli_other"},
        {"tenant": "other"},
        {"user": "ou_other"},
        {"chat": "oc_other"},
        {"sender": "app"},
        {"chat_type": "group"},
    ],
)
async def test_envelope_and_normalized_identity_are_both_required(channel, changed):
    received = AsyncMock()
    channel.on_message = received
    await dispatch(channel, envelope(**changed))
    received.assert_not_awaited()


async def test_owner_unsupported_media_yields_notice_only(channel):
    received = AsyncMock()
    channel.on_message = received
    await dispatch(channel, envelope(kind="image"))
    received.assert_awaited_once()
    assert not received.call_args.args[0].supported


async def test_real_sdk_card_envelope_keeps_tenant_and_origin(channel):
    received = AsyncMock()
    channel.on_action = received
    data = {
        "schema": "2.0",
        "header": {
            "app_id": CREDS.app_id,
            "tenant_key": "tenant",
            "event_id": "click-one",
            "event_type": "card.action.trigger",
        },
        "event": {
            "operator": {"open_id": OWNER.user, "tenant_key": "tenant"},
            "context": {"open_chat_id": OWNER.chat, "open_message_id": "om_card"},
            "action": {"tag": "button", "value": {"token": "once", "decision": "allow"}},
        },
    }
    await dispatch(channel, data, action=True)
    await dispatch(channel, data, action=True)
    received.assert_awaited_once()
    action = received.call_args.args[0]
    assert (action.owner, action.event_id, action.message_id, action.token) == (
        OWNER,
        "action:click-one",
        "om_card",
        "once",
    )


async def test_real_sdk_form_reaches_hub_and_creates_one_named_session(channel, tmp_path):
    from test_gateway import Channel, Runtime
    from test_panels import click, rendered
    from unilark.conversation.hub import Hub
    from unilark.policy.redact import Redactor

    store = GatewayStore(tmp_path / "form.db")
    store.set_owner(OWNER)
    hub = Hub(store, Runtime(), Channel(), OWNER, "profile", Redactor())
    channel.on_action = hub.action
    try:
        hub.panels.open("form", "new")
        await hub.tick()
        form = rendered(store, mode="new")
        action = click(store, form, "创建并切换")
        data = {
            "schema": "2.0",
            "header": {
                "app_id": CREDS.app_id,
                "tenant_key": OWNER.tenant,
                "event_id": "form-submission",
                "event_type": "card.action.trigger",
            },
            "event": {
                "operator": {"open_id": OWNER.user, "tenant_key": OWNER.tenant},
                "context": {"open_chat_id": OWNER.chat, "open_message_id": form["message_id"]},
                "action": {
                    "tag": "button",
                    "value": {"token": action.token, "decision": "ui"},
                    "form_value": {"title": "来自真实 SDK 的表单值"},
                },
            },
        }
        await dispatch(channel, data, action=True)
        await dispatch(channel, data, action=True)
        await hub.tick()
        await hub.tick()
        assert [s["title"] for s in store.sessions(OWNER)] == ["来自真实 SDK 的表单值"]
    finally:
        store.close()


@pytest.mark.parametrize(
    ("code", "retryable", "message_id", "expected"),
    [
        ("rate_limited", True, None, "RETRY"),
        ("unknown", True, None, "UNKNOWN"),
        ("unknown", True, "om_known", "RETRY"),
        ("permission_denied", False, None, "BLOCKED"),
        ("format_error", False, "om_known", "BLOCKED"),
    ],
)
async def test_delivery_distinguishes_ambiguous_create_from_update(
    channel, code, retryable, message_id, expected, monkeypatch
):
    error = SimpleNamespace(
        code=SimpleNamespace(value=code), retryable=retryable, retry_after_seconds=3
    )
    result = SimpleNamespace(success=False, message_id=None, error=error)
    sender = AsyncMock(return_value=result)
    monkeypatch.setattr(channel.sdk, "send", sender)
    monkeypatch.setattr(channel.sdk, "update_card", sender)
    assert (await channel.deliver(OWNER.chat, {}, "request", message_id)).state == expected
    assert sender.await_count == 1


async def test_pair_mode_cannot_deliver_or_dispatch_actions():
    adapter = LarkChannel(CREDS)
    assert (await adapter.deliver(OWNER.chat, {}, "request")).state == "BLOCKED"
    adapter.on_action = AsyncMock()
    await adapter.action(None)
    adapter.on_action.assert_not_awaited()


async def test_real_sdk_unmentioned_group_keeps_canonical_owner_and_actual_chat(channel):
    channel.group_guard = AsyncMock(return_value=True)
    received = AsyncMock()
    channel.on_message = received
    await dispatch(channel, envelope(chat_type="group", chat="oc_room", text="group input"))
    received.assert_awaited_once()
    msg = received.call_args.args[0]
    assert msg.owner == OWNER and msg.chat == "oc_room"
    # Hub performs the DB-backed guard on its own loop; the raw SDK thread never touches SQLite.
    channel.group_guard.assert_not_awaited()
    await dispatch(channel, envelope(chat_type="group", chat="oc_room", user="ou_other"))
    assert received.await_count == 1


@pytest.mark.parametrize(
    ("event_key", "mode"),
    [("unilark.new", "new"), ("unilark.sessions", "sessions"), ("unilark.settings", "settings")],
)
async def test_menu_events_authenticate_timestamp_and_deduplicate_on_hub(
    channel, tmp_path, event_key, mode
):
    from test_gateway import Channel, Runtime
    from unilark.conversation.hub import Hub
    from unilark.policy.redact import Redactor

    store = GatewayStore(tmp_path / "menu.db")
    store.set_owner(OWNER)
    hub = Hub(store, Runtime(), Channel(), OWNER, "profile", Redactor(), enable_rooms=True)
    channel.on_message = hub.accept
    payload = {
        "header": {"app_id": CREDS.app_id, "tenant_key": OWNER.tenant, "event_id": "menu-one"},
        "event": {
            "operator": {"operator_id": {"open_id": OWNER.user}},
            "event_key": event_key,
            "timestamp": str(int(time.time())),
        },
    }

    async def invoke():
        # Exercise the SDK raw-event handler registry, including its dictionary coercion.
        channel.sdk._raw_events._dispatcher_for("application.bot.menu_v6")(payload)
        await asyncio.sleep(0.08)

    try:
        for _ in range(2):
            await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(invoke(), channel.sdk._bg_loop)
            )
        await hub.tick()
        assert store.db.execute("SELECT count(*) FROM ui_panels").fetchone()[0] == 1
        panel = store.db.execute(
            "SELECT p.mode,c.message_id,c.revision,c.delivered "
            "FROM ui_panels p JOIN cards c ON c.id=p.id"
        ).fetchone()
        assert panel["mode"] == mode and panel["message_id"]
        assert panel["revision"] == panel["delivered"]
        payload["header"]["event_id"] = "stale"
        payload["event"]["timestamp"] = "1"
        await channel.menu(payload)
        payload["header"]["event_id"] = "foreign"
        payload["event"]["timestamp"] = str(int(time.time()))
        payload["event"]["operator"]["operator_id"]["open_id"] = "ou_other"
        await channel.menu(payload)
        assert store.db.execute("SELECT count(*) FROM ui_panels").fetchone()[0] == 1
    finally:
        store.close()


async def test_membership_change_during_render_is_retryable_without_sending(channel):
    channel.group_guard = AsyncMock(side_effect=[True, False])
    channel.rich_media.prepare = AsyncMock(return_value={})
    channel.sdk.send = AsyncMock()
    assert (await channel.deliver("oc_room", {}, "r")).state == "RETRY"
    channel.sdk.send.assert_not_awaited()
