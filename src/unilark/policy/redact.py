"""Redact before persisting projections; never include raw runtime objects."""

from __future__ import annotations

import re


class Redactor:
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        self.secrets = tuple(s for s in secrets if len(s) >= 4)

    def text(self, value: object) -> str:
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
            "[REDACTED PRIVATE KEY]",
            text,
            flags=re.S,
        )
        text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/_.=:-]+", r"\1 [REDACTED]", text)
        text = re.sub(
            r"\b(?:AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,})\b",
            "[REDACTED]",
            text,
        )
        text = re.sub(
            r"""(?im)([\w.-]*(?:token|secret|password|api[_-]?key|authorization|cookie)[\w.-]*["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;\n]+)""",
            r"\1[REDACTED]",
            text,
        )
        # Shell environment assignments are tool parameters, not a safe display format.
        text = re.sub(
            r'\b[A-Z][A-Z0-9_]{2,}=(?:"[^"\n]*"|\x27[^\x27\n]*\x27|[^\s;]+)', "[ENV REDACTED]", text
        )
        text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)
        return text.replace("<at", "&lt;at")
