from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest

from unilark.adapters.sidecars.agy.client import AgyClient, Profile, public_step
from unilark.adapters.sidecars.agy.transport import Endpoint, Process, ProtocolError, Transport


def test_snapshot_defaults_and_background_work() -> None:
    assert AgyClient.idle({"status": "CASCADE_RUN_STATUS_IDLE", "fullyIdle": True})
    assert not AgyClient.idle({"status": "CASCADE_RUN_STATUS_IDLE"})
    assert not AgyClient.idle(
        {
            "status": "CASCADE_RUN_STATUS_IDLE",
            "fullyIdle": True,
            "backgroundTasksUpdate": {"totalLength": 1},
        }
    )
    assert not AgyClient.idle(
        {
            "status": "CASCADE_RUN_STATUS_IDLE",
            "fullyIdle": True,
            "pendingAgentMessagesUpdate": {"totalLength": 1},
        }
    )


def test_projection_excludes_raw_thinking_and_preserves_first_step() -> None:
    step = {
        "plannerResponse": {
            "response": "Visible answer",
            "thinking": "private",
            "rawThinking": "private-raw",
            "signature": "private-signature",
        }
    }
    result = public_step(step, 0)
    assert result["index"] == 0
    assert result["text"] == "Visible answer"
    assert "private" not in repr(result)


async def test_changed_bundle_blocks_write_before_any_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = Transport(Path("/unused"), Path("/unused-profile"))
    requests = []

    async def endpoint() -> Endpoint:
        return Endpoint(1234, "fixture-token", Process(1, 0, "123", ("agy",)))

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"changed bundle")

    monkeypatch.setattr(transport, "current", endpoint)
    await transport.http.aclose()
    transport.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    client = AgyClient(transport, Profile("project", "model"))
    try:
        with pytest.raises(ProtocolError, match="Unverified"):
            await client.create(str(uuid.uuid4()))
        assert [(r.method, r.url.path) for r in requests] == [("GET", "/main.js")]
    finally:
        await client.close()


async def test_waiting_command_is_not_automatically_a_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = Transport(Path("/unused"), Path("/unused-profile"))
    client = AgyClient(transport, Profile("project", "model"))

    async def steps(session: str) -> list[dict[str, object]]:
        return [
            {
                "status": "CORTEX_STEP_STATUS_WAITING",
                "metadata": {"toolCall": {"name": "run_command"}},
                "requestedInteraction": {"runCommand": {}},
            }
        ]

    monkeypatch.setattr(client, "steps", steps)
    try:
        with pytest.raises(ProtocolError, match="permission"):
            await client.resolve_permission(str(uuid.uuid4()), 0, allow=True)
    finally:
        await client.close()
