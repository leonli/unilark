"""Real workspace and structured questions; no Lark interaction is simulated as real."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from unilark.adapters.sidecars.views import Answer
from unilark.onboarding.config import load_agy

pytestmark = pytest.mark.real_agy


@pytest.mark.parametrize("cancel", [False, True])
async def test_real_question_workspace_and_stale_decision(tmp_path, cancel):
    config = os.environ.get("UNILARK_REAL_AGY_CONFIG")
    if not config:
        pytest.skip("Explicit AGY instance required")
    client = load_agy(Path(config))
    native = str(uuid.uuid4())
    try:
        await client.check()
        await client.create(native, workspace=str(tmp_path))
        await client.send(
            native,
            "Integration test. Use ask_question to ask exactly one single-choice question: "
            "Pick Blue or Green. Wait for my response. Use no other tools. After the answer "
            "reply with the answer and your absolute workspace directory. "
            "If cancelled, stop asking.",
            str(uuid.uuid4()),
        )
        async with asyncio.timeout(90):
            while True:
                view = await client.view(native)
                pending = [s for s in view.steps if s.questions and s.status == "waiting"]
                if pending:
                    break
                await asyncio.sleep(0.5)
        step = pending[0]
        answers = () if cancel else (Answer((step.questions[0].options[0][0],)),)
        await client.answer(
            native, step.index, answers, fingerprint=step.fingerprint, cancel=cancel
        )
        with pytest.raises(Exception, match="already answered"):
            await client.answer(
                native, step.index, answers, fingerprint=step.fingerprint, cancel=cancel
            )
        async with asyncio.timeout(90):
            while not (view := await client.view(native)).idle:
                await asyncio.sleep(0.5)
        if not cancel:
            reply = "\n".join(s.text for s in view.steps if s.kind == "assistant")
            assert str(tmp_path) in reply
            assert step.questions[0].options[0][1].lower() in reply.lower()
    finally:
        await client.stop(native)
        await client.close()
