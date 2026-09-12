"""Explicit Linux systemd service management with absolute, quoted paths."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MARKER = "# Managed by Unilark; owner_uid="
UNIT = "unilark.service"


def quote(value: str, *, command: bool = False) -> str:
    if any(ord(c) < 32 for c in value):
        raise ValueError("Control characters are not allowed in a service path")
    value = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def unit_text(executable: Path, config: Path, state: Path, credentials: Path, system: bool) -> str:
    paths = [p.absolute() for p in (executable, config, state, credentials)]
    if any(p.is_symlink() for p in (state, credentials)):
        raise ValueError("State and credentials must not be symlinks")
    args = [
        str(paths[0]),
        "--config",
        str(paths[1]),
        "--state",
        str(paths[2]),
        "--credentials",
        str(paths[3]),
        "run",
    ]
    user = pwd.getpwuid(os.getuid()).pw_name
    return (
        MARKER + str(os.getuid()) + "\n[Unit]\nDescription=Unilark local session gateway\n"
        "Wants=network-online.target\nAfter=network-online.target\n"
        "StartLimitIntervalSec=300\nStartLimitBurst=10\n\n[Service]\nType=simple\n"
        + (f"User={user}\nGroup={pwd.getpwuid(os.getuid()).pw_gid}\n" if system else "")
        + "ExecStart="
        + " ".join(quote(a, command=True) for a in args)
        + "\n"
        + "Restart=on-failure\nRestartSec=15\nTimeoutStopSec=45\nUMask=0077\n"
        "Environment=PYTHONUNBUFFERED=1\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n"
        "NoNewPrivileges=true\n"
        + (
            "ProtectSystem=strict\nProtectHome=read-only\nReadWritePaths="
            + quote(str(paths[2].parent))
            + "\nPrivateTmp=true\n"
            if system
            else ""
        )
        + "StandardOutput=journal\nStandardError=journal\n\n[Install]\nWantedBy="
        + ("multi-user.target" if system else "default.target")
        + "\n"
    )


def call(
    arguments: list[str], system: bool, *, mutate: bool = False
) -> subprocess.CompletedProcess[str]:
    command = ["systemctl"] + ([] if system else ["--user"]) + arguments
    if mutate and system and os.geteuid() != 0:
        command = ["sudo", "-n"] + command
    return subprocess.run(command, text=True, capture_output=True, check=False, timeout=60)  # noqa: S603


def managed(path: Path, *, adopt: bool = False) -> None:
    if path.is_symlink():
        raise ValueError("Refusing a symlink service unit")
    if not path.exists():
        return
    text = path.read_text()
    if text.startswith(MARKER + str(os.getuid()) + "\n"):
        return
    # One-time adoption of the verified, manually installed M1 unit on this machine.
    if (
        adopt
        and "Description=Unilark Lark to local AGY gateway\n" in text
        and ("User=" + pwd.getpwuid(os.getuid()).pw_name + "\n") in text
    ):
        return
    raise ValueError("Existing unit is not owned by this Unilark installation")


def run(args: argparse.Namespace) -> int:
    if sys.platform != "linux" or not shutil.which("systemctl"):
        raise ValueError("This release supports Linux systemd; other platforms are not verified")
    path = (
        Path("/etc/systemd/system") if args.system else Path.home() / ".config/systemd/user"
    ) / UNIT
    action = args.service_action
    if action == "status":
        result = call(
            [
                "show",
                UNIT,
                "-p",
                "LoadState",
                "-p",
                "ActiveState",
                "-p",
                "MainPID",
                "-p",
                "NRestarts",
            ],
            args.system,
        )
        print(result.stdout.strip() or "用户服务总线不可用；此环境可显式选择 --system。")
        return 0 if result.returncode == 0 and "ActiveState=active" in result.stdout else 2
    managed(path, adopt=getattr(args, "adopt_existing", False))
    if action == "install":
        if args.config is None or not args.config.is_file():
            raise ValueError("service install requires --config pointing to a verified instance")
        executable = (
            Path(args.executable) if args.executable else Path(sys.executable).parent / "unilark"
        )
        if not executable.is_file():
            raise ValueError("Installed unilark executable not found")
        content = unit_text(executable, args.config, args.state, args.credentials, args.system)
        if args.system:
            fd, name = tempfile.mkstemp(prefix="unilark-unit-")
            try:
                with os.fdopen(fd, "w") as file:
                    file.write(content)
                command = (["sudo", "-n"] if os.geteuid() != 0 else []) + [
                    "install",
                    "-m",
                    "0644",
                    name,
                    str(path),
                ]
                result = subprocess.run(  # noqa: S603
                    command, capture_output=True, text=True, check=False, timeout=30
                )
                if result.returncode:
                    raise RuntimeError(
                        "System service installation needs explicit administrator access"
                    )
            finally:
                Path(name).unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            path.chmod(0o600)
        result = call(["daemon-reload"], args.system, mutate=True)
        if result.returncode:
            raise RuntimeError(
                "Service file saved, but no user service bus; repair or select --system explicitly"
            )
        result = call(["enable", UNIT], args.system, mutate=True)
    elif action == "uninstall":
        result = call(["disable", "--now", UNIT], args.system, mutate=True)
        if result.returncode:
            raise RuntimeError("Could not stop owned service; it has not been removed")
        if args.system:
            command = (["sudo", "-n"] if os.geteuid() != 0 else []) + ["rm", "--", str(path)]
            subprocess.run(command, check=True, capture_output=True, timeout=30)  # noqa: S603
        else:
            path.unlink(missing_ok=True)
        result = call(["daemon-reload"], args.system, mutate=True)
    else:
        result = call([action, UNIT], args.system, mutate=True)
    if result.returncode:
        raise RuntimeError("systemd operation failed; inspect service status and access")
    print(
        json.dumps(
            {
                "service": UNIT,
                "scope": "system" if args.system else "user",
                "action": action,
                "data_preserved": True,
            }
        )
    )
    return 0


def parsers(commands: Any) -> None:
    command = commands.add_parser("service", help="管理自有后台服务；默认用户级，系统级须显式选择")
    command.add_argument(
        "service_action", choices=("install", "start", "restart", "status", "stop", "uninstall")
    )
    command.add_argument("--system", action="store_true")
    command.add_argument("--executable", type=Path)
    command.add_argument(
        "--adopt-existing", action="store_true", help="接管本机已验证的手工 M1 unit"
    )
