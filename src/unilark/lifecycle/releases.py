"""Safe program switches: consistent backup, no rollback of accepted business data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from unilark.lifecycle import installer, service
from unilark.lifecycle.files import backup_database, private_directory
from unilark.lifecycle.lock import InstanceLock
from unilark.onboarding.config import load_agy
from unilark.onboarding.credentials import load_credentials


def installation(prefix: Path) -> dict[str, Any]:
    if prefix.is_symlink():
        raise ValueError("Unsafe install prefix")
    record: dict[str, Any] = json.loads((prefix / "install.json").read_text())
    if record.get("owner_uid") != os.getuid() or Path(record["prefix"]) != prefix:
        raise ValueError("Installation is not owned by this user")
    if any(
        not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", v)
        for v in record["releases"] + [record["version"]]
    ):
        raise ValueError("Invalid installed release paths")
    current = prefix / "current"
    if not current.is_symlink() or current.resolve() != prefix / "releases" / record["version"]:
        raise ValueError("Installation activation is inconsistent; repair it before switching")
    return record


async def safe_point(args: argparse.Namespace) -> None:
    credentials = load_credentials(args.credentials)
    store = sqlite3.connect(args.state.resolve().as_uri() + "?mode=ro", uri=True)
    store.row_factory = sqlite3.Row
    client = load_agy(args.config)
    try:
        row = store.execute(
            "SELECT data FROM owner WHERE account=?", (credentials.account,)
        ).fetchone()
        if row is None:
            raise ValueError("Owner not paired")
        owner = row[0]
        if (
            store.execute(
                "SELECT 1 FROM operations WHERE source_scope=? "
                "AND state IN ('QUEUED','SUBMITTING','UNKNOWN')",
                (owner,),
            ).fetchone()
            or store.execute(
                "SELECT 1 FROM inbox WHERE owner=? AND status IN ('QUEUED','SUBMITTING','UNKNOWN')",
                (owner,),
            ).fetchone()
        ):
            raise ValueError("Handle queued or uncertain operations before switching versions")
        for session in store.execute(
            "SELECT b.*,s.state FROM bindings b JOIN session_meta s ON s.binding=b.id "
            "WHERE s.owner=?",
            (owner,),
        ):
            if session["state"] == "ACTIVE":
                if session["profile"] != str(client.transport.user_data.resolve()):
                    raise ValueError("Another runtime must be checked before switching")
                if not (await client.view(session["native_id"])).idle:
                    raise ValueError("Wait for active AGY work to finish before switching versions")
    finally:
        store.close()
        await client.close()


def compatible(target: Path, state: Path, *, migrate: bool = False) -> bool:
    data = json.loads((target / "release.json").read_text())
    db = sqlite3.connect(state.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        schema = db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()
    minimum = data.get("schema_input_min", data["schema_min"]) if migrate else data["schema_min"]
    return bool(minimum <= schema <= data["schema_max"])


async def run(args: argparse.Namespace) -> int:
    prefix = args.prefix.absolute()
    record = installation(prefix)
    if args.config is None:
        raise ValueError("Lifecycle operations require --config")
    await safe_point(args)
    unit = (
        Path("/etc/systemd/system") if args.system else Path.home() / ".config/systemd/user"
    ) / service.UNIT
    service.managed(unit)
    status = service.call(["show", service.UNIT, "-p", "ActiveState", "--value"], args.system)
    was_active = status.returncode == 0 and status.stdout.strip() == "active"
    if (
        unit.exists()
        and str(prefix / "current/.venv/bin/unilark").replace("%", "%%") not in unit.read_text()
    ):
        raise ValueError("Install the service with the stable current executable before upgrading")
    if args.command == "uninstall":
        if unit.exists():
            service.run(argparse.Namespace(**{**vars(args), "service_action": "uninstall"}))
        with InstanceLock(args.state.with_suffix(".lock")):
            launcher = Path(record["launcher"])
            if launcher.is_symlink() or not launcher.read_text().startswith(
                "#!/bin/sh\n" + installer.LAUNCHER_MARKER
            ):
                raise ValueError("Launcher ownership changed; refusing removal")
            for version in record["releases"]:
                target = prefix / "releases" / version
                if (
                    target.is_symlink()
                    or target.parent != prefix / "releases"
                    or not (target / "release.json").is_file()
                ):
                    raise ValueError("Unsafe recorded release path")
            launcher.unlink()
            (prefix / "current").unlink()
            for version in record["releases"]:
                shutil.rmtree(prefix / "releases" / version)
            (prefix / "install.json").unlink()
        print(
            json.dumps(
                {
                    "uninstalled": True,
                    "retained_state": str(args.state.parent),
                    "external_agy_preserved": True,
                }
            )
        )
        return 0
    previous = record["version"]
    if args.command == "upgrade":
        target, manifest = installer.prepare(args.bundle.resolve(), prefix)
        selected = manifest["version"]
    else:
        selected = record.get("previous")
        if not selected:
            raise ValueError("No previous installed version")
        target = prefix / "releases" / selected
    stopped = False
    switched = False
    try:
        if not compatible(target, args.state, migrate=args.command == "upgrade"):
            raise ValueError(
                "No verified compatible schema rollback; "
                "database will not be restored over new data"
            )
        if was_active:
            result = service.call(["stop", service.UNIT], args.system, mutate=True)
            if result.returncode:
                raise RuntimeError("Could not stop gateway for switch")
            stopped = True
        with InstanceLock(args.state.with_suffix(".lock")):
            # Recheck after stopping inbound to close the preflight race.
            await safe_point(args)
            backups = private_directory(args.state.parent / "backups")
            backup = backups / f"before-{selected}-{time.time_ns()}.db"
            backup_database(args.state, backup)
            probe = subprocess.run(  # noqa: S603
                [
                    str(target / ".venv/bin/python"),
                    "-m",
                    "unilark.lifecycle.migration",
                    "--config",
                    str(args.config),
                    "--credentials",
                    str(args.credentials),
                    "--source",
                    str(backup),
                    "--output",
                    str(backup.with_suffix(".candidate.db")),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )
            report = json.loads(probe.stdout)
            if (
                probe.returncode != 0
                or report.get("agy", {}).get("status") != "verified"
                or report.get("lark", {}).get("owner") != "paired"
            ):
                raise ValueError(
                    "Candidate preflight failed; active program and live database unchanged"
                )
            installer.activate(prefix, selected)
            switched = True
            record.update(
                version=selected,
                previous=previous,
                releases=list(dict.fromkeys(record["releases"] + [selected])),
            )
            installer.atomic_json(prefix / "install.json", record)
        if was_active:
            result = service.call(["start", service.UNIT], args.system, mutate=True)
            if result.returncode:
                raise RuntimeError(
                    "Candidate service did not start; inspect doctor before accepting traffic"
                )
            for _ in range(20):
                await asyncio.sleep(2)
                health = subprocess.run(  # noqa: S603
                    [
                        str(target / ".venv/bin/unilark"),
                        "--config",
                        str(args.config),
                        "--credentials",
                        str(args.credentials),
                        "--state",
                        str(args.state),
                        "doctor",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                if health.returncode == 0:
                    break
            else:
                raise RuntimeError(
                    "Candidate did not become healthy; attempting compatible program rollback"
                )
        print(
            json.dumps(
                {
                    "version": selected,
                    "previous": previous,
                    "backup": str(backup),
                    "database_restored": False,
                    "service_started": was_active,
                }
            )
        )
        return 0
    except BaseException:
        if switched and was_active and compatible(prefix / "releases" / previous, args.state):
            stopped_result = service.call(["stop", service.UNIT], args.system, mutate=True)
            if stopped_result.returncode == 0:
                with InstanceLock(args.state.with_suffix(".lock")):
                    installer.activate(prefix, previous)
                    record.update(version=previous, previous=selected)
                    installer.atomic_json(prefix / "install.json", record)
                service.call(["start", service.UNIT], args.system, mutate=True)
        elif not switched and stopped:
            service.call(["start", service.UNIT], args.system, mutate=True)
        raise
    finally:
        if args.command == "upgrade" and selected not in record["releases"]:
            shutil.rmtree(target)


def parsers(commands: Any) -> None:
    default = Path.home() / ".local/share/unilark"
    for name in ("upgrade", "rollback", "uninstall"):
        parser = commands.add_parser(name, help="受控程序维护；默认保留会话和外部 AGY")
        parser.add_argument("--prefix", type=Path, default=default)
        parser.add_argument("--system", action="store_true")
        if name == "upgrade":
            parser.add_argument("bundle", type=Path)
