"""Product events; SDK objects and credentials stop at the channel adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Owner:
    account: str
    tenant: str
    user: str
    chat: str

    @property
    def key(self) -> str:
        return json.dumps([self.account, self.tenant, self.user, self.chat], separators=(",", ":"))


@dataclass(frozen=True)
class Message:
    owner: Owner
    event_id: str
    text: str
    created_at: float
    reply_to: str | None = None
    supported: bool = True
    chat: str = ""  # Actual destination; owner.chat remains the paired control DM.


@dataclass(frozen=True)
class Action:
    owner: Owner
    event_id: str
    message_id: str
    token: str
    decision: str
    fields: dict[str, str] = field(default_factory=dict)
    chat: str = ""


@dataclass(frozen=True)
class Delivery:
    state: str  # SENT, RETRY (short rate-limit), DEFERRED (quota), UNKNOWN, BLOCKED
    message_id: str | None = None
    retry_after: float = 5


class Channel(Protocol):
    async def deliver(
        self, chat: str, card: dict[str, Any], request_id: str, message_id: str | None = None
    ) -> Delivery: ...
