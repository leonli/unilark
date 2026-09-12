"""Standard-library installer, also shipped verbatim as the bundle's install.py.

Only verified local bundle directories are accepted. Installing an existing deployment
must go through the running version's upgrade command, which owns the state transition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any

LAUNCHER_MARKER = "# Managed Unilark launcher\n"


def read_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / "manifest.json"
    if path.is_symlink():
        raise ValueError("Manifest must be a regular file")
    data: dict[str, Any] = json.loads(path.read_text())
    if data.get("format") != 1 or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", data.get("version", "")
    ):
        raise ValueError("Unsupported release manifest")
    if (
        data.get("platform") != "linux-x86_64"
        or sys.platform != "linux"
        or platform.machine() != "x86_64"
    ):
        raise ValueError("This artifact was verified only on Linux x86_64")
    if sys.version_info[:2] != tuple(data["python"]):
        raise ValueError("Use the Python minor version recorded in this offline artifact")
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Missing package checksums")
    for name, checksum in files.items():
        relative = Path(name)
        candidate = bundle / relative
        if relative.is_absolute() or ".." in relative.parts or candidate.is_symlink():
            raise ValueError("Unsafe release file")
        if not candidate.resolve().is_relative_to(bundle.resolve()) or not candidate.is_file():
            raise ValueError("Release file escaped bundle")
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != checksum:
            raise ValueError("Release checksum mismatch")
    if not any(n.startswith("wheelhouse/unilark-") and n.endswith(".whl") for n in files):
        raise ValueError("Unilark wheel is missing")
    return data


def atomic_json(path: Path, value: Any) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".install-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def activate(prefix: Path, version: str) -> None:
    target = prefix / "releases" / version
    if not target.is_dir() or target.is_symlink():
        raise ValueError("Prepared release is missing")
    current = prefix / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("Refusing to replace an unrelated directory")
    temporary = prefix / (".current-" + os.urandom(8).hex())
    temporary.symlink_to(target)
    try:
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(bundle: Path, prefix: Path) -> tuple[Path, dict[str, Any]]:
    manifest = read_manifest(bundle)
    if prefix.is_symlink():
        raise ValueError("Installation prefix must not be a symlink")
    prefix.mkdir(parents=True, mode=0o700, exist_ok=True)
    if prefix.stat().st_uid != os.getuid() or prefix.stat().st_mode & 0o077:
        raise ValueError("Installation prefix must be owned by this user and mode 0700")
    releases = prefix / "releases"
    if releases.is_symlink():
        raise ValueError("Release directory must not be a symlink")
    releases.mkdir(mode=0o700, exist_ok=True)
    target = releases / manifest["version"]
    target.mkdir(mode=0o700)  # Never overwrite an existing program version.
    try:
        venv.EnvBuilder(with_pip=True).create(target / ".venv")
        wheelhouse = target / "wheelhouse"
        wheelhouse.mkdir(mode=0o700)
        for name in manifest["files"]:
            if name.startswith("wheelhouse/") and name.endswith(".whl"):
                shutil.copyfile(bundle / name, wheelhouse / Path(name).name)
        python = target / ".venv/bin/python"
        command = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "unilark[lark]==" + manifest["version"],
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)  # noqa: S603
        if result.returncode:
            raise RuntimeError("Offline dependency installation failed")
        result = subprocess.run(  # noqa: S603
            [str(target / ".venv/bin/unilark"), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        if result.stdout.strip() != "unilark " + manifest["version"]:
            raise ValueError("Installed version does not match manifest")
        atomic_json(target / "release.json", manifest)
        return target, manifest
    except BaseException:
        shutil.rmtree(target)
        raise


def install(bundle: Path, prefix: Path, bin_dir: Path) -> dict[str, Any]:
    if (prefix / "install.json").exists():
        raise ValueError("Existing installation: use unilark upgrade to preserve running state")
    launcher = bin_dir / "unilark"
    if launcher.exists() or launcher.is_symlink():
        raise ValueError("Refusing to overwrite an existing unilark command")
    target, manifest = prepare(bundle, prefix)
    activate(prefix, manifest["version"])
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = str(prefix / "current/.venv/bin/unilark")
    # shlex.quote is shell escaping; JSON quoting is not.
    import shlex

    text = "#!/bin/sh\n" + LAUNCHER_MARKER + "exec " + shlex.quote(executable) + ' "$@"\n'
    fd = os.open(launcher, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o700)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    record = {
        "owner_uid": os.getuid(),
        "version": manifest["version"],
        "previous": None,
        "prefix": str(prefix),
        "launcher": str(launcher),
        "releases": [manifest["version"]],
    }
    atomic_json(prefix / "install.json", record)
    return {
        "installed": manifest["version"],
        "command": str(launcher),
        "next": "unilark setup --non-interactive",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a verified local Unilark bundle")
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/share/unilark")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                install(args.bundle.resolve(), args.prefix.absolute(), args.bin_dir.absolute())
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Installation failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
