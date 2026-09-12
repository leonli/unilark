from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from unilark.store.ledger import Ledger


def test_replayed_input_keeps_original_session_after_switch(tmp_path: Path) -> None:
    store = Ledger(tmp_path / "state.db")
    a, b = (store.bind("profile", str(uuid.uuid4())) for _ in range(2))
    original = store.enqueue(a, "app/tenant", "message-1", "first")
    replay = store.enqueue(b, "app/tenant", "message-1", "first")
    assert replay == original
    assert replay["binding_id"] == a
    assert store.enqueue(b, "other-app/tenant", "message-1", "first") != original
    store.close()


def test_claim_is_atomic_across_two_connections_and_pause_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    one, two = Ledger(path), Ledger(path)
    binding = one.bind("profile", str(uuid.uuid4()))
    operation = one.enqueue(binding, "app/tenant", "message-1", "first")["request_id"]
    assert one.claim(operation)
    assert two.claim(operation) is None
    one.finish(operation, "ACCEPTED", 0)
    one.pause(binding)
    queued = one.enqueue(binding, "app/tenant", "message-2", "second")["request_id"]
    assert two.claim(queued) is None
    one.close()
    two.close()
    restarted = Ledger(path)
    assert restarted.bindings()[0]["queue_state"] == "PAUSED"
    assert restarted.get(operation)["native_step"] == 0
    assert restarted.claim(queued) is None
    restarted.close()


def test_crash_after_claim_becomes_unknown_and_blocks_further_submission(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = Ledger(path)
    binding = store.bind("profile", str(uuid.uuid4()))
    request = store.enqueue(binding, "app", "message-1", "first")["request_id"]
    later = store.enqueue(binding, "app", "message-2", "second")["request_id"]
    store.close()
    source = Path(__file__).resolve().parents[1] / "src"
    child = subprocess.run(  # noqa: S603 - fixed interpreter/program, paths passed as argv
        [
            sys.executable,
            "-c",
            "import os,sys; from pathlib import Path; from unilark.store.ledger import Ledger; "
            "s=Ledger(Path(sys.argv[1])); assert s.claim(sys.argv[2]); os._exit(23)",
            str(path),
            request,
        ],
        env={**os.environ, "PYTHONPATH": str(source)},
        check=False,
    )
    assert child.returncode == 23
    restarted = Ledger(path)
    assert restarted.recover() == 1
    assert restarted.get(request)["state"] == "UNKNOWN"
    assert restarted.claim(request) is None
    assert restarted.claim(later) is None
    with pytest.raises(ValueError, match="UNKNOWN"):
        restarted.resume(binding)
    # A found native tag can confirm acceptance. An absent tag does nothing.
    restarted.finish(request, "ACCEPTED", 0)
    assert restarted.claim(later)
    restarted.close()
