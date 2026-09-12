"""Candidate migration probe. Only a new copy is migrated; source stays untouched."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from unilark.lifecycle.diagnostics import collect
from unilark.lifecycle.files import backup_database
from unilark.store.gateway import GatewayStore


def migrate_copy(source: Path, output: Path) -> int:
    backup_database(source, output)
    store = GatewayStore(output)
    try:
        if store.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Candidate database integrity check failed")
        if store.db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Candidate database references are inconsistent")
        return int(store.db.execute("PRAGMA user_version").fetchone()[0])
    finally:
        store.close()


async def run(args: argparse.Namespace) -> int:
    schema = migrate_copy(args.source, args.output)
    report, _ = await collect(args.config, args.output, args.credentials)
    report["migration"] = {"schema": schema, "source_unchanged": True}
    print(json.dumps(report))
    return (
        0
        if (report["agy"].get("status") == "verified" and report["lark"].get("owner") == "paired")
        else 2
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("source", "output", "config", "credentials"):
        parser.add_argument("--" + name, type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
