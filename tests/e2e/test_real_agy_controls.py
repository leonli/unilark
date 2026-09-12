"""Opt-in harmless real runtime controls. Never contacts Lark."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from unilark.adapters.sidecars.agy.transport import ProtocolError
from unilark.onboarding.config import load_agy

pytestmark = [
    pytest.mark.real_agy,
    pytest.mark.skipif(
        not os.environ.get("UNILARK_REAL_AGY_CONFIG"), reason="No explicitly selected real AGY"
    ),
]


async def waiting(client, session):
    deadline = asyncio.get_running_loop().time() + 90
    while asyncio.get_running_loop().time() < deadline:
        view = await client.view(session)
        for step in view.steps:
            if step.permission and step.status == "waiting":
                return step
        await asyncio.sleep(0.5)
    pytest.fail("Runtime did not produce the requested permission within 90 seconds")


async def idle(client, session):
    deadline = asyncio.get_running_loop().time() + 45
    while asyncio.get_running_loop().time() < deadline:
        if (await client.view(session)).idle:
            return
        await asyncio.sleep(0.5)
    pytest.fail("Runtime did not become authoritatively idle")


@pytest.mark.parametrize("allow", [False, True])
async def test_real_permission_deny_allow_and_stale_fingerprint(allow, tmp_path):
    client = load_agy(Path(os.environ["UNILARK_REAL_AGY_CONFIG"]))
    session = str(uuid.uuid4())
    target = tmp_path / "permission-target"
    try:
        await client.check()
        await client.create(session)
        await client.send(
            session,
            f"Run exactly this command with BypassSandbox true: touch {target}. "
            "This tests an explicit approval. Ask for permission, do not work around denial, "
            "and report the result. Do not call other tools.",
            str(uuid.uuid4()),
        )
        step = await waiting(client, session)
        assert str(target) in step.resource
        with pytest.raises(ProtocolError, match="changed"):
            await client.resolve_permission(session, step.index, allow=True, fingerprint="stale")
        assert not target.exists()
        observed = await client.resolve_permission(
            session, step.index, allow=allow, fingerprint=step.fingerprint
        )
        await idle(client, session)
        assert target.exists() == allow
        with pytest.raises(ProtocolError):
            await client.resolve_permission(
                session, step.index, allow=allow, fingerprint=step.fingerprint
            )
        print(
            f"\nReal permission session {session}: allow={allow}, "
            f"file_exists={target.exists()}, immediate_status={observed}; stale/duplicate rejected."
        )
    finally:
        await client.stop(session)
        await client.close()
        target.unlink(missing_ok=True)


async def test_real_stop_running_command():
    client = load_agy(Path(os.environ["UNILARK_REAL_AGY_CONFIG"]))
    session = str(uuid.uuid4())
    try:
        await client.check()
        await client.create(session)
        await client.send(
            session,
            "Run exactly sleep 30 with BypassSandbox true. After approval, "
            "wait for completion. This tests stopping a harmless command. Do not call other tools.",
            str(uuid.uuid4()),
        )
        step = await waiting(client, session)
        assert "sleep 30" in step.resource
        await client.resolve_permission(
            session, step.index, allow=True, fingerprint=step.fingerprint
        )
        await asyncio.sleep(1)
        assert not (await client.view(session)).idle
        immediate = await client.stop(session)
        await idle(client, session)
        print(f"\nReal stop session {session}: immediate_idle={immediate}; final_idle=True.")
    finally:
        await client.stop(session)
        await client.close()
