from __future__ import annotations

import logging
import time
from dataclasses import replace

import pytest

from unilark.conversation.channel import Message, Owner
from unilark.lifecycle.lock import InstanceLock
from unilark.lifecycle.logging import SafeFormatter
from unilark.onboarding.credentials import load_credentials
from unilark.onboarding.pairing import Pairing
from unilark.policy.redact import Redactor
from unilark.projection.cards import chunks


def test_credentials_are_data_and_require_owner_only_permissions(tmp_path):
    path = tmp_path / "credentials.env"
    path.write_text(
        "UNILARK_LARK_EDITION=lark\nUNILARK_LARK_APP_ID=cli_fixture\n"
        "UNILARK_LARK_APP_SECRET=$(never-evaluate-this)\n"
    )
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        load_credentials(path)
    path.chmod(0o600)
    credentials = load_credentials(path)
    assert credentials.domain == "https://open.larksuite.com"
    assert credentials.app_secret == "$(never-evaluate-this)"  # noqa: S105 - inert fixture
    assert "never-evaluate" not in repr(credentials)
    link = tmp_path / "symlink"
    link.symlink_to(path)
    with pytest.raises(OSError):
        load_credentials(link)


def test_instance_lock_is_exclusive_and_releases(tmp_path):
    path = tmp_path / "run.lock"
    with InstanceLock(path), pytest.raises(RuntimeError, match="Another"):
        InstanceLock(path)
    with InstanceLock(path):
        assert path.stat().st_mode & 0o777 == 0o600


async def test_remote_nonce_needs_local_confirmation_and_expires():
    owner = Owner("app", "tenant", "user", "chat")
    window = Pairing("app")
    message = Message(owner, "id", "/pair " + window.code, time.time())
    await window.receive(replace(message, text="/new"))
    await window.receive(replace(message, text="你好"))
    assert window.candidates.empty()
    await window.receive(message)
    await window.receive(message)
    assert window.confirmed is None
    assert window.candidates.qsize() == 1
    with pytest.raises(ValueError):
        window.confirm(owner, "wrong-local-user")
    assert window.confirm(owner, owner.user) == owner
    with pytest.raises(ValueError):
        window.confirm(owner, owner.user)
    expired = Pairing("app", lifetime=-1)
    await expired.receive(replace(message, text="/pair " + expired.code))
    assert expired.candidates.empty()


def test_redaction_precedes_chunking_and_sdk_logs_omit_payloads():
    redactor = Redactor(("known-app-secret",))
    text = (
        "a" * 3490
        + "-----BEGIN PRIVATE KEY-----\n"
        + "private" * 1000
        + "\n-----END PRIVATE KEY-----\nAuthorization: Bearer abcdefgh\n"
        'api_key="unknown-secret" ENV_VALUE=hidden known-app-secret'
    )
    result = "".join(chunks(redactor.text(text)))
    for secret in ("private", "abcdefgh", "unknown-secret", "hidden", "known-app-secret"):
        assert secret not in result
    record = logging.LogRecord("lark", logging.ERROR, "sdk", 1, text, (), None)
    assert "PRIVATE KEY" not in SafeFormatter(redactor).format(record)
