"""Do not let third-party exception payloads print credentials or message bodies."""

from __future__ import annotations

import logging
import os
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from unilark.policy.redact import Redactor


class SafeFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        if "lark" in record.name.lower():
            return f"{record.levelname} Lark SDK: diagnostic details omitted."
        return f"{record.levelname} {self.redactor.text(record.getMessage())}"


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self) -> TextIOWrapper[Any]:
        return open(
            self.baseFilename,
            "a",
            encoding="utf-8",
            opener=lambda path, flags: os.open(path, flags | os.O_NOFOLLOW, 0o600),
        )


def configure(redactor: Redactor, log: Path | None = None) -> None:
    handler: logging.Handler = (
        PrivateRotatingHandler(log, maxBytes=5_000_000, backupCount=3)
        if log
        else logging.StreamHandler()
    )
    handler.setLevel(logging.WARNING)
    handler.setFormatter(SafeFormatter(redactor))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    for item in logging.Logger.manager.loggerDict.values():
        if isinstance(item, logging.Logger):
            item.handlers.clear()
            item.propagate = True
            item.setLevel(logging.WARNING)
