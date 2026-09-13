"""Real Hub → AGY → projection with a local recorder, NOT a Lark E2E test."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import pytest

from unilark.conversation.channel import Delivery, Message, Owner
from unilark.conversation.hub import Hub
from unilark.onboarding.config import load_agy
from unilark.policy.redact import Redactor
from unilark.store.gateway import GatewayStore

pytestmark = [
    pytest.mark.real_agy,
    pytest.mark.skipif(
        not os.environ.get("UNILARK_REAL_AGY_CONFIG"), reason="No explicitly selected real AGY"
    ),
]


class Recorder:
    def __init__(self):
        self.cards = {}
        self.chats = {}
        self.room_api = self

    async def create(self, title, request_id):
        return "local_room_" + request_id

    async def configure(self, chat):
        pass

    async def verify(self, chat, request_id):
        return True

    async def deliver(self, chat, card, request_id, message_id=None):
        self.cards[request_id] = card
        self.chats[request_id] = chat
        return Delivery("SENT", message_id or "local_" + request_id)


@pytest.mark.parametrize("room_mode", [False, True])
async def test_real_hub_projects_response_and_recovers_binding(tmp_path, room_mode):
    client = load_agy(Path(os.environ["UNILARK_REAL_AGY_CONFIG"]))
    store = GatewayStore(tmp_path / "gateway.db")
    owner = Owner("recorder", "local", "local", "local")
    store.set_owner(owner)
    recorder = Recorder()
    profile = str(client.transport.user_data.resolve())
    hub = Hub(store, client, recorder, owner, profile, Redactor(), enable_rooms=room_mode)
    session = None
    marker = "M1-HUB-" + uuid.uuid4().hex[:12]

    def is_reply(payload):
        if room_mode:
            return payload.get("schema") == "2.0" and "header" not in payload
        return "AGY 回复" in payload.get("header", {}).get("title", {}).get("content", "")

    try:
        await hub.accept(Message(owner, "new", "/new hub smoke", time.time()))
        await hub.tick()
        session = store.session(owner, store.target(owner))
        room = store.rooms.get(owner, session["id"])
        chat = room["chat"] if room else owner.chat
        await hub.accept(
            Message(
                owner, "input", "Do not call tools. Reply exactly " + marker, time.time(), chat=chat
            )
        )
        await hub.tick()
        assert store.operations(owner)[0]["state"] == "ACCEPTED"
        deadline = asyncio.get_running_loop().time() + 60
        while asyncio.get_running_loop().time() < deadline:
            await hub.tick()
            projected = [v for v in recorder.cards.values() if is_reply(v)]
            if marker in json.dumps(projected) and (await client.view(session["native_id"])).idle:
                assert all(p["schema"] == "2.0" for p in projected)
                assert all(
                    recorder.chats[k] == chat for k, v in recorder.cards.items() if is_reply(v)
                )
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail("No real response projected through Hub")
        store.close()
        store = GatewayStore(tmp_path / "gateway.db")
        store.recover_gateway()
        hub = Hub(store, client, recorder, owner, profile, Redactor(), enable_rooms=room_mode)
        await hub.accept(
            Message(owner, "input", "redelivery after restart", time.time(), chat=chat)
        )
        await hub.tick()
        assert len(store.operations(owner)) == 1
        assert len([s for s in await client.steps(session["native_id"]) if "userInput" in s]) == 1
        print(
            f"\nReal Hub session {session['native_id']}: projected response, restart binding, "
            "duplicate input suppressed. Transport was a local recorder, not Lark."
        )
    finally:
        if session:
            await client.stop(session["native_id"])
        store.close()
        await client.close()
