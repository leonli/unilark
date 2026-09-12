"""One foreground/pairing/service consumer for a state directory."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class InstanceLock:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise RuntimeError("Another Unilark process owns this state directory") from None

    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        os.close(self.fd)
