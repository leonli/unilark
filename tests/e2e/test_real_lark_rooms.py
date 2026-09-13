"""Real group API acceptance in an explicitly recorded, owner-only probe group.

No WebSocket connection, user impersonation, group creation or live DB mutation.
Only temporary test cards are sent and recalled. Menu/inbound journeys require a user.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from unilark.adapters.lark.channel import LarkChannel
from unilark.onboarding.credentials import load_credentials
from unilark.projection.cards import card
from unilark.projection.rich_text import reply_cards
from unilark.store.gateway import GatewayStore

pytestmark = [
    pytest.mark.real_lark,
    pytest.mark.skipif(
        not all(
            os.environ.get(k)
            for k in (
                "UNILARK_REAL_LARK_CREDENTIALS",
                "UNILARK_REAL_LARK_STATE",
                "UNILARK_REAL_LARK_ROOM_RECORD",
            )
        ),
        reason="No explicitly selected private Lark probe group",
    ),
]


async def test_real_private_group_members_markdown_mermaid_and_update():
    credentials = load_credentials(Path(os.environ["UNILARK_REAL_LARK_CREDENTIALS"]))
    store = GatewayStore(Path(os.environ["UNILARK_REAL_LARK_STATE"]), readonly=True)
    try:
        owner = store.owner(credentials.account)
        assert owner is not None
    finally:
        store.close()
    record = json.loads(Path(os.environ["UNILARK_REAL_LARK_ROOM_RECORD"]).read_text())
    chat, request = record["chat"], record["request_id"]
    channel = LarkChannel(credentials, owner)
    api = channel.room_api
    messages = []
    try:
        await api.check_permissions()
        assert await api.verify(chat, request), "Probe group membership/settings not verified"
        assert await api.reconcile(request) == [chat], "Probe group marker is not unique"

        async def guard(destination):
            return destination == chat and await api.verify(chat, request)

        channel.group_guard = guard
        initial = await channel.deliver(
            chat, card("群验收 · 将自动清理", "无需操作"), str(uuid.uuid4())
        )
        assert initial.state == "SENT" and initial.message_id
        messages.append(initial.message_id)
        payload = reply_cards(
            "群验收 · 将自动清理",
            """# 群内 Markdown

**粗体**、`code`、列表与表格：

- 会话 A
- 会话 B

| 群 | 会话 |
| --- | --- |
| A | A |
| B | B |

```mermaid
flowchart LR
    A[独立会话群] --> B[固定原生会话]
    B --> C[回复和架构图]
```
""",
            "自动接口验收，无需回复",
        )[0]
        prepared = await channel.rich_media.prepare(payload)
        assert any(e["tag"] == "img" for e in prepared["body"]["elements"])
        updated = await channel.deliver(chat, payload, str(uuid.uuid4()), initial.message_id)
        assert updated.state == "SENT" and updated.message_id == initial.message_id
        created = await channel.deliver(chat, payload, str(uuid.uuid4()))
        assert created.state == "SENT" and created.message_id
        messages.append(created.message_id)
        for message in messages:
            data = await api.request("GET", "/im/v1/messages/" + message)
            item = data["items"][0]
            assert item["chat_id"] == chat and item["msg_type"] == "interactive"
            # JSON2 GET is a compatibility representation, not the rendered Markdown body.
            assert "群验收" in item["body"]["content"]
    finally:
        try:
            for message in messages:
                await api.request("DELETE", "/im/v1/messages/" + message)
        finally:
            await channel.disconnect()
