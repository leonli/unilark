"""Process and heartbeat identity; a reused PID is not the same gateway."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def process_start(pid: int) -> str | None:
    try:
        path = Path("/proc") / str(pid)
        if pid <= 0 or path.stat().st_uid != os.getuid():
            return None
        fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError, ValueError):
        return None


def health(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"state": "not_observed", "running": False}
    result = dict(row)
    age = max(0, time.time() - float(row["updated_at"]))
    started = process_start(int(row.get("pid", 0)))
    result["running"] = started is not None and started == row.get("process_start")
    result["age_seconds"] = round(age, 1)
    if not result["running"]:
        result["state"] = "stopped"
    elif age > float(row.get("fresh_for", 30)):
        result["state"] = "stale"
    return result
