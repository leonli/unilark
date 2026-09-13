"""Real Lark card delivery/readback. No WebSocket consumer or simulated user clicks.

Explicitly set both environment paths to opt in. Sends three temporary cards to the
already paired owner and recalls only those test messages. Does not alter live state.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from unilark.adapters.lark.channel import LarkChannel
from unilark.adapters.sidecars.agy.capabilities import CAPABILITIES
from unilark.adapters.sidecars.views import SessionView
from unilark.conversation.hub import Hub
from unilark.onboarding.credentials import load_credentials
from unilark.policy.redact import Redactor
from unilark.projection.cards import card
from unilark.projection.rich_text import reply_cards
from unilark.store.gateway import GatewayStore

pytestmark = [
    pytest.mark.real_lark,
    pytest.mark.skipif(
        not (
            os.environ.get("UNILARK_REAL_LARK_CREDENTIALS")
            and os.environ.get("UNILARK_REAL_LARK_STATE")
        ),
        reason="No explicitly selected paired Lark environment",
    ),
]


async def test_real_settings_menu_delivers_card(tmp_path):
    """Drive the settings menu boundary through Hub and real HTTP delivery, then recall."""
    credentials = load_credentials(Path(os.environ["UNILARK_REAL_LARK_CREDENTIALS"]))
    live = GatewayStore(Path(os.environ["UNILARK_REAL_LARK_STATE"]), readonly=True)
    try:
        owner = live.owner(credentials.account)
        assert owner is not None
    finally:
        live.close()
    store = GatewayStore(tmp_path / "settings.db")
    store.set_owner(owner)
    channel = LarkChannel(credentials, owner)
    channel.connected = True  # Enable Hub HTTP outbox; deliberately do not open another WebSocket.
    runtime = SimpleNamespace(capabilities=CAPABILITIES)
    hub = Hub(store, runtime, channel, owner, "test", Redactor(), enable_rooms=True)
    channel.on_message = hub.accept
    try:
        await channel.menu(
            {
                "header": {
                    "app_id": credentials.app_id,
                    "tenant_key": owner.tenant,
                    "event_id": str(uuid.uuid4()),
                },
                "event": {
                    "operator": {"operator_id": {"open_id": owner.user}},
                    "event_key": "unilark.settings",
                    "timestamp": str(int(time.time())),
                },
            }
        )
        await hub.tick()
        rows = store.db.execute(
            "SELECT p.mode,c.message_id FROM ui_panels p JOIN cards c ON c.id=p.id"
        ).fetchall()
        assert len(rows) == 1 and rows[0]["mode"] == "settings" and rows[0]["message_id"]
        data = await channel.room_api.request("GET", "/im/v1/messages/" + rows[0]["message_id"])
        item = data["items"][0]
        assert item["chat_id"] == owner.chat and item["msg_type"] == "interactive"
        assert "设置" in item["body"]["content"]
    finally:
        try:
            for row in store.db.execute(
                "SELECT message_id FROM cards WHERE message_id IS NOT NULL"
            ):
                await channel.room_api.request("DELETE", "/im/v1/messages/" + row[0])
        finally:
            await channel.disconnect()
            store.close()


@pytest.mark.parametrize("room_mode", [False, True])
async def test_real_lark_accepts_session_commands_and_new_form(tmp_path, room_mode):
    credentials = load_credentials(Path(os.environ["UNILARK_REAL_LARK_CREDENTIALS"]))
    with_state = GatewayStore(Path(os.environ["UNILARK_REAL_LARK_STATE"]), readonly=True)
    try:
        owner = with_state.owner(credentials.account)
        assert owner is not None
    finally:
        with_state.close()
    store = GatewayStore(tmp_path / "test-cards.db")
    store.set_owner(owner)
    runtime = SimpleNamespace(capabilities=CAPABILITIES)
    hub = Hub(
        store,
        runtime,
        SimpleNamespace(),
        owner,
        "test",
        Redactor((credentials.app_secret,)),
        enable_rooms=room_mode,
    )
    for name in ("验收会话 A", "验收会话 B"):
        binding = store.add_session(owner, "test", str(uuid.uuid4()), "ACTIVE", name)
        hub.views[binding] = SessionView(True, "idle", "-1")
    sent = []
    try:
        async with httpx.AsyncClient(base_url=credentials.domain, timeout=20) as http:
            auth = (
                await http.post(
                    "/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": credentials.app_id,
                        "app_secret": credentials.app_secret,
                    },
                )
            ).json()
            assert auth.get("code") == 0, "Lark authentication failed"
            http.headers["Authorization"] = "Bearer " + auth["tenant_access_token"]
            try:
                for mode in ("sessions", "commands", "new"):
                    hub.panels.open("test-" + mode, mode)
                hub.panels.render()
                for row in store.db.execute("SELECT payload FROM cards"):
                    payload = json.loads(row[0])
                    payload["header"]["title"]["content"] = "UE 自动验收 · 无需操作 · 将自动清理"
                    result = (
                        await http.post(
                            "/open-apis/im/v1/messages",
                            params={"receive_id_type": "chat_id"},
                            json={
                                "receive_id": owner.chat,
                                "msg_type": "interactive",
                                "content": json.dumps(payload, ensure_ascii=False),
                                "uuid": str(uuid.uuid4()),
                            },
                        )
                    ).json()
                    assert result.get("code") == 0, f"Card rejected: code {result.get('code')}"
                    message = result["data"]["message_id"]
                    sent.append(message)
                    received = (await http.get("/open-apis/im/v1/messages/" + message)).json()
                    assert received.get("code") == 0
                    item = received["data"]["items"][0]
                    assert item["msg_type"] == "interactive"
                    assert "UE 自动验收" in item["body"]["content"]
            finally:
                for message in sent:
                    result = (await http.delete("/open-apis/im/v1/messages/" + message)).json()
                    assert result.get("code") == 0, "Could not recall a test card"
    finally:
        store.close()


async def test_real_lark_markdown_image_and_upgrade_existing_card():
    """Real SDK image upload/send/update and API readback; never opens a WebSocket."""
    credentials = load_credentials(Path(os.environ["UNILARK_REAL_LARK_CREDENTIALS"]))
    store = GatewayStore(Path(os.environ["UNILARK_REAL_LARK_STATE"]), readonly=True)
    try:
        owner = store.owner(credentials.account)
        assert owner is not None
    finally:
        store.close()
    channel = LarkChannel(credentials, owner)
    sent = []
    content = """# Markdown 验收

**粗体**、`inline code`、[链接](https://example.com)

> 引用说明

- 列表 A
- 列表 B

| 组件 | 状态 |
| --- | --- |
| 标题与表格 | 可读 |

```python
print("hello")
```

```mermaid
flowchart TD
    A[手机 Lark] --> B[会话网关]
    B --> C[本地 Agent]
    C --> D[回复与架构图]
```
"""
    async with httpx.AsyncClient(base_url=credentials.domain, timeout=20) as http:
        auth = (
            await http.post(
                "/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": credentials.app_id,
                    "app_secret": credentials.app_secret,
                },
            )
        ).json()
        assert auth.get("code") == 0, "Lark authentication failed"
        http.headers["Authorization"] = "Bearer " + auth["tenant_access_token"]
        try:
            initial = await channel.deliver(
                owner.chat, card("UE 自动验收 · 将自动清理", "旧卡"), str(uuid.uuid4())
            )
            assert initial.state == "SENT" and initial.message_id
            sent.append(initial.message_id)
            payload = reply_cards(
                "UE 自动验收 · 无需操作 · 将自动清理", content, "测试会话 · Markdown / Mermaid"
            )[0]
            prepared = await channel.rich_media.prepare(payload)
            images = [e for e in prepared["body"]["elements"] if e["tag"] == "img"]
            assert len(images) == 1, "Local rendering / Lark image upload failed"
            # Existing JSON 1.0 cards must migrate without losing their original message ID.
            updated = await channel.deliver(
                owner.chat, payload, str(uuid.uuid4()), initial.message_id
            )
            assert updated.state == "SENT" and updated.message_id == initial.message_id
            created = await channel.deliver(owner.chat, payload, str(uuid.uuid4()))
            assert created.state == "SENT" and created.message_id
            sent.append(created.message_id)
            assert len(channel.rich_media.cache) == 1
            for message in sent:
                received = (await http.get("/open-apis/im/v1/messages/" + message)).json()
                assert received.get("code") == 0
                body = received["data"]["items"][0]["body"]["content"]
                # Lark GET flattens JSON 2.0 into a compatibility placeholder, even
                # for a plain Markdown-only card. It cannot prove rendered body text.
                assert received["data"]["items"][0]["chat_id"] == owner.chat
                assert "UE 自动验收" in body
        finally:
            for message in sent:
                result = (await http.delete("/open-apis/im/v1/messages/" + message)).json()
                assert result.get("code") == 0, "Could not recall a test card"
            await channel.disconnect()
