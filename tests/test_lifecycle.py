from __future__ import annotations

import argparse
import json
import subprocess
from unittest.mock import AsyncMock

import pytest

from test_gateway import OWNER
from unilark.lifecycle import releases, service
from unilark.lifecycle.migration import migrate_copy
from unilark.store.gateway import GatewayStore


def test_candidate_migrates_only_new_copy_and_preserves_m1_owner(tmp_path):
    source = tmp_path / "source.db"
    store = GatewayStore(source)
    store.set_owner(OWNER)
    for table in ("observations", "preferences", "recovery_audit", "session_context"):
        store.db.execute("DROP TABLE " + table)
    store.db.execute("PRAGMA user_version=1")
    store.close()
    target = tmp_path / "candidate.db"
    assert migrate_copy(source, target) == 3
    original = GatewayStore(source, readonly=True)
    migrated = GatewayStore(target, readonly=True)
    assert original.db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert original.owner(OWNER.account) == migrated.owner(OWNER.account) == OWNER
    assert migrated.db.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    original.close()
    migrated.close()


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    prefix = tmp_path / "install"
    target = prefix / "releases/0.0.2"
    target.mkdir(parents=True)
    (target / "release.json").write_text(json.dumps({"schema_min": 3, "schema_max": 3}))
    (prefix / "current").symlink_to(target)
    record = {
        "owner_uid": __import__("os").getuid(),
        "version": "0.0.2",
        "releases": ["0.0.2"],
        "prefix": str(prefix),
    }
    (prefix / "install.json").write_text(json.dumps(record))
    state = tmp_path / "state.db"
    store = GatewayStore(state)
    store.set_owner(OWNER)
    store.close()
    args = argparse.Namespace(
        prefix=prefix,
        command="upgrade",
        bundle=tmp_path / "bundle",
        config=tmp_path / "config",
        credentials=tmp_path / "credentials",
        state=state,
        system=False,
    )
    monkeypatch.setattr(releases, "safe_point", AsyncMock())
    monkeypatch.setattr(service, "managed", lambda path: None)
    monkeypatch.setattr(
        service, "call", lambda *a, **kw: subprocess.CompletedProcess([], 0, "inactive", "")
    )

    def prepare(bundle, install):
        candidate = install / "releases/0.0.3"
        candidate.mkdir()
        manifest = {"version": "0.0.3", "schema_min": 3, "schema_max": 3, "schema_input_min": 1}
        (candidate / "release.json").write_text(json.dumps(manifest))
        return candidate, manifest

    monkeypatch.setattr(releases.installer, "prepare", prepare)
    return args, prefix


async def test_failed_candidate_does_not_change_active_version_and_can_retry(
    deployment, monkeypatch
):
    args, prefix = deployment
    monkeypatch.setattr(
        releases.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 2, "{}", "")
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="preflight"):
            await releases.run(args)
        assert not (prefix / "releases/0.0.3").exists()
        assert (prefix / "current").resolve().name == "0.0.2"


async def test_upgrade_then_rollback_preserves_data_accepted_after_upgrade(deployment, monkeypatch):
    args, prefix = deployment
    report = json.dumps({"agy": {"status": "verified"}, "lark": {"owner": "paired"}})
    monkeypatch.setattr(
        releases.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, report, "")
    )
    assert await releases.run(args) == 0
    store = GatewayStore(args.state)
    store.audit("accepted_after_upgrade")
    store.close()
    args.command = "rollback"
    assert await releases.run(args) == 0
    assert (prefix / "current").resolve().name == "0.0.2"
    store = GatewayStore(args.state, readonly=True)
    assert store.db.execute("SELECT reason FROM audit").fetchone()[0] == "accepted_after_upgrade"
    store.close()


def test_m1_is_allowed_for_upgrade_but_not_unsafe_program_rollback(deployment):
    args, prefix = deployment
    store = GatewayStore(args.state)
    store.db.execute("PRAGMA user_version=1")
    store.close()
    target = prefix / "releases/0.0.2"
    (target / "release.json").write_text(
        json.dumps({"schema_min": 3, "schema_max": 3, "schema_input_min": 1})
    )
    assert releases.compatible(target, args.state, migrate=True)
    assert not releases.compatible(target, args.state)


def test_foreground_process_does_not_pass_service_handoff(tmp_path, monkeypatch):
    args = argparse.Namespace(
        system=False,
        config=tmp_path / "config",
        credentials=tmp_path / "credentials",
        state=tmp_path / "state.db",
    )
    monkeypatch.setattr(
        service,
        "call",
        lambda *a, **kw: subprocess.CompletedProcess(
            [], 0, "ActiveState=active\nMainPID=99\nUnitFileState=enabled\n", ""
        ),
    )
    assert (
        service.handoff(args, {"pid": 100, "running": True, "state": "running"})["status"]
        == "pending"
    )


async def test_candidate_startup_failure_rolls_program_back_without_restoring_data(
    deployment, monkeypatch
):
    args, prefix = deployment
    actions = []

    def manager(arguments, system, **kwargs):
        actions.append(arguments[0])
        return subprocess.CompletedProcess([], 0, "active" if arguments[0] == "show" else "", "")

    monkeypatch.setattr(service, "call", manager)
    monkeypatch.setattr(releases.asyncio, "sleep", AsyncMock())
    count = 0

    def candidate(*a, **kw):
        nonlocal count
        count += 1
        if count == 1:
            return subprocess.CompletedProcess(
                [], 0, json.dumps({"agy": {"status": "verified"}, "lark": {"owner": "paired"}}), ""
            )
        if count == 2:
            store = GatewayStore(args.state)
            store.audit("accepted_before_failed_health_check")
            store.close()
        return subprocess.CompletedProcess([], 2, "{}", "")

    monkeypatch.setattr(releases.subprocess, "run", candidate)
    with pytest.raises(RuntimeError, match="healthy"):
        await releases.run(args)
    assert (prefix / "current").resolve().name == "0.0.2"
    assert actions == ["show", "stop", "start", "stop", "start"]
    store = GatewayStore(args.state, readonly=True)
    assert (
        store.db.execute("SELECT reason FROM audit").fetchone()[0]
        == "accepted_before_failed_health_check"
    )
    store.close()


async def test_malformed_compatibility_metadata_does_not_strand_staging(deployment, monkeypatch):
    args, prefix = deployment

    def fail(*a, **kw):
        raise ValueError("malformed metadata")

    monkeypatch.setattr(releases, "compatible", fail)
    for _ in range(2):
        with pytest.raises(ValueError, match="malformed"):
            await releases.run(args)
        assert not (prefix / "releases/0.0.3").exists()
