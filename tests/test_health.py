from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from unilark.cli import main
from unilark.conversation.channel import Owner
from unilark.lifecycle.status import health, process_start
from unilark.store.gateway import GatewayStore


def test_health_rejects_stale_or_reused_process_identity():
    row = {
        "state": "running",
        "pid": os.getpid(),
        "process_start": process_start(os.getpid()),
        "updated_at": time.time(),
        "fresh_for": 30,
    }
    assert health(row)["state"] == "running"
    assert health({**row, "updated_at": time.time() - 60})["state"] == "stale"
    assert health({**row, "process_start": "wrong-process"})["state"] == "stopped"
    assert not health(None)["running"]


def test_doctor_separates_live_health_from_historical_acceptance(
    tmp_path: Path, monkeypatch, capsys
):
    import unilark.cli

    profile = str(tmp_path / "profile")
    client = SimpleNamespace(
        check=AsyncMock(return_value={"status": "verified"}),
        close=AsyncMock(),
        transport=SimpleNamespace(user_data=Path(profile)),
    )
    monkeypatch.setattr(unilark.cli, "load_agy", lambda path: client)
    credentials = tmp_path / "lark.env"
    credentials.write_text(
        "UNILARK_LARK_EDITION=lark\nUNILARK_LARK_APP_ID=cli_fixture\n"
        "UNILARK_LARK_APP_SECRET=inert-test-value\n"
    )
    credentials.chmod(0o600)
    state = tmp_path / "state.db"
    owner = Owner("lark:cli_fixture", "tenant", "user", "chat")
    store = GatewayStore(state)
    store.set_owner(owner)
    store.record_validation(owner, profile, "text_roundtrip", {"status": "passed"})
    args = [
        "--config",
        str(tmp_path / "unused.toml"),
        "--credentials",
        str(credentials),
        "--state",
        str(state),
        "doctor",
    ]
    try:
        assert main(args) == 2
        before = json.loads(capsys.readouterr().out)
        assert before["acceptance"]["text_roundtrip"]["status"] == "passed"
        assert before["service"]["state"] == "not_observed"
        store.heartbeat(
            owner,
            profile,
            {
                "state": "running",
                "pid": os.getpid(),
                "process_start": process_start(os.getpid()),
                "lark_connected": True,
                "agy_observed": True,
            },
        )
        assert main(args) == 0
        live = json.loads(capsys.readouterr().out)
        assert live["lark"]["connection"] == "connected"
        store.heartbeat(
            owner,
            profile,
            {
                "state": "running",
                "pid": os.getpid(),
                "process_start": "old-process",
                "lark_connected": True,
                "agy_observed": True,
            },
        )
        assert main(args) == 2
        stopped = json.loads(capsys.readouterr().out)
        assert stopped["service"]["state"] == "stopped"
        assert stopped["acceptance"]["text_roundtrip"]["status"] == "passed"
        assert store.validations(owner, profile + "-other") == {}
    finally:
        store.close()
