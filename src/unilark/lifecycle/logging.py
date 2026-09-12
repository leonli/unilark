"""Do not let third-party exception payloads print credentials or message bodies."""

from __future__ import annotations

import logging

from unilark.policy.redact import Redactor


class SafeFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        if "lark" in record.name.lower():
            return f"{record.levelname} Lark SDK: diagnostic details omitted."
        return f"{record.levelname} {self.redactor.text(record.getMessage())}"


def configure(redactor: Redactor) -> None:
    handler = logging.StreamHandler()
    handler.setLevel(logging.WARNING)
    handler.setFormatter(SafeFormatter(redactor))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    for item in logging.Logger.manager.loggerDict.values():
        if isinstance(item, logging.Logger):
            item.handlers.clear()
            item.propagate = True
            item.setLevel(logging.WARNING)
