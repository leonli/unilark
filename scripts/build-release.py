#!/usr/bin/env python3
"""Build a local, offline Linux bundle from the exact installed runtime dependencies."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def runtime_requirements() -> list[str]:
    pending = ["httpx", "lark-channel-sdk"]
    found = {}
    while pending:
        name = pending.pop()
        distribution = importlib.metadata.distribution(name)
        canonical = distribution.metadata["Name"].lower().replace("_", "-")
        if canonical in found:
            continue
        found[canonical] = distribution.version
        for raw in distribution.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return [f"{name}=={version}" for name, version in sorted(found.items())]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Only the verified Linux x86_64 bundle may be built")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / f"unilark-{version}-linux-x86_64"
    bundle.mkdir()  # Existing artifacts are immutable; choose a new output directory to rebuild.
    wheels = bundle / "wheelhouse"
    wheels.mkdir()
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels), str(ROOT)],
        check=True,
    )
    requirements = runtime_requirements()
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--only-binary=:all:",
            "--dest",
            str(wheels),
            *requirements,
        ],
        check=True,
    )
    (bundle / "requirements.lock").write_text("\n".join(requirements) + "\n")
    shutil.copyfile(ROOT / "src/unilark/lifecycle/installer.py", bundle / "install.py")
    shutil.copyfile(ROOT / "config.example.toml", bundle / "config.example.toml")
    shutil.copyfile(ROOT / "README.md", bundle / "README.md")
    shutil.copytree(ROOT / "docs", bundle / "docs")
    manifest = {
        "format": 1,
        "version": version,
        "platform": "linux-x86_64",
        "python": list(sys.version_info[:2]),
        "schema_min": 2,
        "schema_max": 2,
        "license": "UNLICENSED",
        "distribution": "local experimental; publication undecided",
        "files": {
            str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(bundle.rglob("*"))
            if p.is_file()
        },
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive = output / (bundle.name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname=bundle.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    print(
        json.dumps(
            {
                "bundle": str(bundle),
                "archive": str(archive),
                "sha256": digest,
                "runtime_packages": len(requirements),
            }
        )
    )


if __name__ == "__main__":
    main()
