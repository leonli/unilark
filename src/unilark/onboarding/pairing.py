"""A short local pairing window; a remote code never grants execution alone."""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time

from unilark.conversation.channel import Message, Owner


class Pairing:
    def __init__(self, account: str, lifetime: float = 300) -> None:
        self.account = account
        self.code = secrets.token_hex(4).upper()
        self.opened = time.time()
        self.expires = self.opened + lifetime
        self.candidates: asyncio.Queue[Owner] = asyncio.Queue(maxsize=20)
        self.seen: set[Owner] = set()
        self.confirmed: Owner | None = None

    async def receive(self, message: Message) -> None:
        if (
            self.confirmed
            or time.time() >= self.expires
            or not message.supported
            or message.owner.account != self.account
            or message.created_at < self.opened - 5
            or message.created_at > time.time() + 60
            or not hmac.compare_digest(
                message.text.strip().encode(), ("/pair " + self.code).encode()
            )
            or message.owner in self.seen
            or len(self.seen) >= 20
        ):
            return
        self.seen.add(message.owner)
        self.candidates.put_nowait(message.owner)

    def confirm(self, candidate: Owner, typed_user: str) -> Owner:
        if (
            self.confirmed
            or time.time() >= self.expires
            or candidate not in self.seen
            or typed_user != candidate.user
        ):
            raise ValueError("Pairing expired or local identity confirmation did not match")
        self.confirmed = candidate
        return candidate
