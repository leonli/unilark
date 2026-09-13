"""Real Lark card delivery/readback. No WebSocket consumer or simulated user clicks.

Explicitly set both environment paths to opt in. Sends three temporary cards to the
already paired owner and recalls only those test messages. Does not alter live state.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from unilark.adapters.sidecars.agy.capabilities import CAPABILITIES
from unilark.adapters.sidecars.views import SessionView
from unilark.conversation.hub import Hub
from unilark.onboarding.credentials import load_credentials
from unilark.policy.redact import Redactor
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


async def test_real_lark_accepts_session_commands_and_new_form(tmp_path):
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
    hub = Hub(store, runtime, SimpleNamespace(), owner, "test", Redactor((credentials.app_secret,)))
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
