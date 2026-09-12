"""Opt-in real desktop smoke; creates one harmless conversation, no tools.

UNILARK_REAL_AGY_CONFIG=/private/config.toml pytest -m real_agy -q
This verifies only AGY. It is not a substitute for AGY → Lark → AGY acceptance.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from unilark.adapters.sidecars.agy.client import VERIFIED_BUNDLE_SHA256, public_step
from unilark.onboarding.config import load_agy
from unilark.store.ledger import Ledger

pytestmark = [
    pytest.mark.real_agy,
    pytest.mark.skipif(
        not os.environ.get("UNILARK_REAL_AGY_CONFIG"),
        reason="No explicitly selected real AGY instance",
    ),
]


async def test_native_label_body_tag_and_reconciliation(tmp_path: Path) -> None:
    client = load_agy(Path(os.environ["UNILARK_REAL_AGY_CONFIG"]))
    store = Ledger(tmp_path / "smoke.db")
    session = str(uuid.uuid4())
    marker = "UNILARK-M1-" + uuid.uuid4().hex[:12]
    text = f"连通性测试：不要调用任何工具，只回复 {marker}。"
    try:
        assert (await client.check())["bundle_sha256"] == VERIFIED_BUNDLE_SHA256
        await client.create(session)
        binding = store.bind("real-smoke", session)
        op = store.enqueue(binding, "real-smoke", marker, text)
        assert store.claim(op["request_id"])
        await client.send(session, text, op["request_id"])
        # Deliberately lose the local acknowledgement after a real runtime send.
        assert store.recover() == 1
        assert store.get(op["request_id"])["state"] == "UNKNOWN"
        deadline = asyncio.get_running_loop().time() + 60
        steps = []
        while asyncio.get_running_loop().time() < deadline:
            steps = await client.steps(session)
            index = await client.locate(session, op["request_id"])
            if index is not None:
                store.finish(op["request_id"], "ACCEPTED", index)
                break
            await asyncio.sleep(0.3)
        assert store.get(op["request_id"])["state"] == "ACCEPTED"
        assert store.claim(op["request_id"]) is None
        while asyncio.get_running_loop().time() < deadline:
            steps = await client.steps(session)
            answers = [
                public_step(s, i).get("text", "")
                for i, s in enumerate(steps)
                if s.get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
                and s.get("status") == "CORTEX_STEP_STATUS_DONE"
            ]
            if any(marker in answer for answer in answers):
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail("Real AGY did not finish the text-only probe within 60 seconds")
        users = [s["userInput"] for s in steps if "userInput" in s]
        assert len(users) == 1
        assert users[0]["userResponse"] == text
        assert users[0]["userIdentity"]["username"] == "Lark · Unilark"
        assert store.get(op["request_id"])["native_step"] == 0
        print(
            f"\nReal AGY conversation: {session}; one input; unchanged body; native label; "
            "lost local acknowledgement reconciled by tag without resend."
        )
    finally:
        store.close()
        await client.close()
