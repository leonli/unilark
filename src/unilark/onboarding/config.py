"""Explicit, non-secret instance selection. Credentials are never loaded here."""

from __future__ import annotations

import tomllib
from pathlib import Path

from unilark.adapters.sidecars.agy.client import AgyClient, Profile
from unilark.adapters.sidecars.agy.transport import Transport


def load_agy(path: Path) -> AgyClient:
    data = tomllib.loads(path.read_text())["agy"]
    required = ("executable", "user_data", "project_id", "model")
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in required):
        raise ValueError("AGY config requires executable, user_data, project_id and model")
    paths = [Path(data[key]).expanduser() for key in ("executable", "user_data")]
    if any(not p.is_absolute() or not p.exists() for p in paths):
        raise ValueError("AGY executable and profile must be existing absolute paths")
    return AgyClient(
        Transport(*paths),
        Profile(data["project_id"], data["model"], data.get("source_label", "Lark · Unilark")),
    )
