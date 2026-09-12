"""Read a restricted data file. It is never sourced or evaluated as shell code."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    edition: str
    app_id: str
    app_secret: str = field(repr=False)

    @property
    def domain(self) -> str:
        return "https://open.larksuite.com" if self.edition == "lark" else "https://open.feishu.cn"

    @property
    def account(self) -> str:
        return self.edition + ":" + self.app_id


def load_credentials(path: Path) -> Credentials:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Credential file must be owned by this user and mode 0600")
        values = {}
        for line in stream:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.rstrip("\n").partition("=")
            if not separator or key in values:
                raise ValueError("Invalid or duplicate credential field")
            values[key] = value.strip()
    edition = values.get("UNILARK_LARK_EDITION", "")
    app = values.get("UNILARK_LARK_APP_ID", "")
    secret = values.get("UNILARK_LARK_APP_SECRET", "")
    if edition not in ("lark", "feishu") or not app.startswith("cli_") or not secret:
        raise ValueError("Missing edition, App ID or App Secret")
    return Credentials(edition, app, secret)
