from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys

import pytest

from unilark.lifecycle.files import backup_database
from unilark.lifecycle.installer import read_manifest
from unilark.lifecycle.logging import PrivateRotatingHandler, SafeFormatter
from unilark.lifecycle.service import managed, unit_text
from unilark.policy.redact import Redactor
from unilark.store.gateway import GatewayStore


def test_backup_includes_committed_wal_and_preserves_original(tmp_path):
    source = tmp_path / "state.db"
    target = tmp_path / "backups" / "state.db"
    store = GatewayStore(source)
    store.db.execute("CREATE TABLE test_wal(value TEXT)")
    store.db.execute("INSERT INTO test_wal VALUES('committed')")
    store.db.commit()
    backup_database(source, target)
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT value FROM test_wal").fetchone()[0] == "committed"
    copy.close()
    assert target.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        backup_database(source, target)
    assert store.db.execute("SELECT value FROM test_wal").fetchone()[0] == "committed"
    store.close()


def test_service_paths_cannot_inject_directives_or_expand_tokens(tmp_path):
    weird = tmp_path / "space $USER %n"
    text = unit_text(weird, weird / "config", weird / "state.db", weird / "lark.env", False)
    assert "$$USER" in text and "%%n" in text
    assert "User=" not in text
    with pytest.raises(ValueError):
        unit_text(tmp_path / "x\nExecStart=/bin/false", weird, weird, weird, True)
    path = tmp_path / "unilark.service"
    path.write_text("[Service]\nExecStart=/usr/bin/other")
    with pytest.raises(ValueError):
        managed(path)


def test_rotated_logs_remain_private_and_omit_sdk_payloads(tmp_path):
    log = tmp_path / "gateway.log"
    handler = PrivateRotatingHandler(log, maxBytes=120, backupCount=2)
    handler.setFormatter(SafeFormatter(Redactor(("known-test-secret",))))
    for _ in range(8):
        handler.emit(
            logging.LogRecord(
                "lark.sdk", logging.ERROR, "", 0, "known-test-secret and raw body", (), None
            )
        )
    handler.close()
    files = list(tmp_path.glob("gateway.log*"))
    assert len(files) > 1
    assert all(p.stat().st_mode & 0o077 == 0 for p in files)
    assert all(
        "raw body" not in p.read_text() and "known-test-secret" not in p.read_text() for p in files
    )


def test_bundle_checksums_and_path_traversal_are_rejected(tmp_path):
    wheels = tmp_path / "wheelhouse"
    wheels.mkdir()
    wheel = wheels / "unilark-0.0.2-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    data = {
        "format": 1,
        "version": "0.0.2",
        "platform": "linux-x86_64",
        "python": list(sys.version_info[:2]),
        "files": {"wheelhouse/" + wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest()},
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data))
    assert read_manifest(tmp_path)["version"] == "0.0.2"
    wheel.write_bytes(b"altered")
    with pytest.raises(ValueError, match="checksum"):
        read_manifest(tmp_path)
    data["files"] = {"../outside": hashlib.sha256(b"fixture").hexdigest()}
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsafe"):
        read_manifest(tmp_path)
