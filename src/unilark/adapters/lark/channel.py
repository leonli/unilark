"""Official Channel SDK boundary, retaining the authenticated raw envelope."""

from __future__ import annotations

import asyncio
import importlib
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from unilark.conversation.channel import Action, Delivery, Message, Owner
from unilark.onboarding.credentials import Credentials

from .lifecycle import serialize_shutdown
from .quota import MONTHLY_QUOTA_CODE, ApiQuota
from .rich_media import RichMedia
from .rooms import LarkRooms, RoomApiError

_WS_GATE = threading.Lock()


class LarkChannel:
    def __init__(self, credentials: Credentials, owner: Owner | None = None) -> None:
        sdk = importlib.import_module("lark_channel")
        self.credentials = credentials
        self.owner = owner
        self.group_guard: Callable[[str], Awaitable[bool]] | None = None
        self.envelopes: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.on_message: Callable[[Message], Awaitable[None]] | None = None
        self.on_action: Callable[[Action], Awaitable[None]] | None = None
        self.rejections = 0
        self.errors = 0
        self.connected = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws_loop: asyncio.AbstractEventLoop | None = None
        self.previous_ws_loop: asyncio.AbstractEventLoop | None = None
        self.sdk: Any = sdk.FeishuChannel(
            app_id=credentials.app_id,
            app_secret=credentials.app_secret,
            domain=credentials.domain,
            transport="ws",
            policy=sdk.PolicyConfig(
                dm_policy="allowlist" if owner else "open",
                allow_from=[owner.user] if owner else [],
                group_policy="open" if owner else "disabled",
                require_mention=False,
            ),
            safety=sdk.SafetyConfig(
                text_batch=sdk.TextBatchConfig(delay_ms=0, max_messages=1),
                chat_queue=sdk.ChatQueueConfig(merge_while_busy=False),
            ),
            inbound=sdk.InboundConfig(
                emit_raw_events=True,
                expand_merge_forward=False,
                fetch_interactive_card=False,
                name_cache=sdk.NameCacheConfig(enabled=False),
            ),
            outbound=sdk.OutboundConfig(retry=sdk.RetryConfig(max_attempts=1)),
            security=sdk.SecurityConfig(mode="strict"),
            name_lookup=self.no_name,
        )
        serialize_shutdown(self.sdk)
        self.quota = ApiQuota()
        self.room_api = LarkRooms(self.sdk.client, owner, self.quota) if owner else None
        self.rich_media = RichMedia(self.upload_image)
        self.sdk.on("raw", self.raw)
        self.sdk.on("message", self.message)
        self.sdk.on("cardAction", self.action)
        self.sdk.on("error", self.error)
        self.sdk.on("reconnecting", self.disconnected)
        self.sdk.on("reconnected", self.reconnected)
        self.sdk.on_raw_event("application.bot.menu_v6", self.menu)

    async def raw(self, data: dict[str, Any]) -> None:
        event = data.get("event", {})
        message = event.get("message", {})
        mid = message.get("message_id")
        if isinstance(mid, str) and mid:
            # Normalized InboundMessage.raw contains only the message, not its tenant header.
            self.envelopes[mid] = {
                "header": data.get("header", {}),
                "sender": event.get("sender", {}),
                "chat": message.get("chat_id"),
            }
            self.envelopes.move_to_end(mid)
            while len(self.envelopes) > 2048:
                self.envelopes.popitem(last=False)

    async def no_name(self, identity: str) -> None:
        return None

    async def dispatch(self, work: Awaitable[None]) -> None:
        # The SDK invokes handlers on its own background event-loop thread.
        # SQLite and the Hub belong exclusively to the runner's loop.
        if self.loop is None or asyncio.get_running_loop() is self.loop:
            await work
        else:

            async def invoke() -> None:
                await work

            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(invoke(), self.loop))

    def envelope_owner(
        self, header: dict[str, Any], user: str, chat: str, *, group: bool = False
    ) -> Owner | None:
        tenant = header.get("tenant_key")
        if header.get("app_id") != self.credentials.app_id or not tenant or not user or not chat:
            return None
        candidate = Owner(self.credentials.account, str(tenant), user, chat)
        if (
            group
            and self.owner
            and self.group_guard
            and (candidate.account, candidate.tenant, candidate.user)
            == (self.owner.account, self.owner.tenant, self.owner.user)
        ):
            return self.owner
        return candidate if self.owner is None or candidate == self.owner else None

    async def message(self, msg: Any) -> None:
        envelope = self.envelopes.pop(msg.message_id, {})
        candidate = self.envelope_owner(
            envelope.get("header", {}), msg.sender_id, msg.chat_id, group=msg.chat_type == "group"
        )
        raw_sender = envelope.get("sender", {})
        raw_id = raw_sender.get("sender_id", {}).get("open_id")
        valid = (
            candidate is not None
            and (
                msg.chat_type == "p2p"
                or (
                    msg.chat_type == "group"
                    and self.group_guard is not None
                    and self.owner is not None
                )
            )
            and msg.sender_type == "user"
            and raw_sender.get("sender_type") == "user"
            and raw_sender.get("tenant_key") == envelope.get("header", {}).get("tenant_key")
            and raw_id == msg.sender_id
            and envelope.get("chat") == msg.chat_id
            and not msg.batched_sources
            and msg.create_time > 0
        )
        if not valid or candidate is None:
            self.rejections += 1
            return
        if self.on_message:
            await self.dispatch(
                self.on_message(
                    Message(
                        candidate,
                        msg.message_id,
                        msg.content_text,
                        msg.create_time / 1000,
                        msg.reply_to_message_id,
                        msg.raw_content_type == "text",
                        msg.chat_id,
                    )
                )
            )

    async def action(self, event: Any) -> None:
        # Pairing mode cannot dispatch controls even if somebody obtains a card token.
        if self.owner is None:
            self.rejections += 1
            return
        raw = event.raw
        candidate = self.envelope_owner(
            raw.get("header", {}),
            event.operator.open_id,
            event.chat_id,
            group=event.chat_id != self.owner.chat,
        )
        value = event.action.value
        eid = raw.get("header", {}).get("event_id")
        operator_tenant = raw.get("event", {}).get("operator", {}).get("tenant_key")
        fields = getattr(event.action, "form_value", None) or {}
        if (
            candidate is None
            or operator_tenant != candidate.tenant
            or not isinstance(value, dict)
            or not isinstance(eid, str)
            or not eid
            or not isinstance(fields, dict)
            or len(fields) > 5
            or any(
                not isinstance(k, str) or not isinstance(v, str) or len(v) > 2000
                for k, v in fields.items()
            )
        ):
            self.rejections += 1
            return
        if self.on_action:
            await self.dispatch(
                self.on_action(
                    Action(
                        candidate,
                        "action:" + eid,
                        event.message_id,
                        str(value.get("token", "")),
                        str(value.get("decision", "")),
                        fields,
                        event.chat_id,
                    )
                )
            )

    async def menu(self, data: dict[str, Any]) -> None:
        if self.owner is None or self.on_message is None:
            return
        header, event = data.get("header", {}), data.get("event", {})
        user = event.get("operator", {}).get("operator_id", {}).get("open_id", "")
        candidate = self.envelope_owner(header, user, self.owner.chat)
        command = {
            "unilark.new": "/new-form",
            "unilark.sessions": "/list",
            "unilark.settings": "/settings",
        }.get(event.get("event_key"))
        eid = header.get("event_id")
        try:
            created = float(event.get("timestamp", 0))
        except (TypeError, ValueError):
            return
        if candidate != self.owner or not command or not isinstance(eid, str) or not eid:
            self.rejections += 1
            return
        await self.dispatch(self.on_message(Message(candidate, "menu:" + eid, command, created)))

    def error(self, error: Any) -> None:
        self.errors += 1  # Never log SDK exception text or raw payloads here.

    def disconnected(self) -> None:
        self.connected = False

    def reconnected(self) -> None:
        self.connected = True

    async def connect(self) -> None:
        if self.connected:
            return
        if not _WS_GATE.acquire(blocking=False):
            raise RuntimeError("Only one Lark WebSocket consumer may run in this process")
        self.loop = asyncio.get_running_loop()
        # SDK 1.4.0 captures an event loop at import and runs it synchronously
        # on its transport thread. Importing inside asyncio.run otherwise captures
        # our already-running Hub loop, and SDK shutdown can stop that loop too.
        ws: Any = importlib.import_module("lark_channel.ws.client")
        self.previous_ws_loop = ws.loop
        self.ws_loop = asyncio.new_event_loop()
        ws.loop = self.ws_loop
        try:
            await self.sdk.connect_until_ready(timeout=30)
            self.connected = bool(self.sdk.is_ready)
            if not self.connected:
                raise RuntimeError("Lark transport did not reach ready state")
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        self.connected = False
        try:
            await self.sdk.disconnect()
        finally:
            if self.ws_loop is not None:
                await asyncio.to_thread(self.close_ws_loop)

    def close_ws_loop(self) -> None:
        loop = self.ws_loop
        if loop is None:
            return
        deadline = time.monotonic() + 3
        while loop.is_running() and time.monotonic() < deadline:
            time.sleep(0.01)
        if loop.is_running():
            raise RuntimeError("SDK transport did not stop; refusing to reuse its loop")
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:

            async def drain() -> None:
                await asyncio.gather(*pending, return_exceptions=True)

            loop.run_until_complete(drain())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        ws: Any = importlib.import_module("lark_channel.ws.client")
        ws.loop = self.previous_ws_loop
        self.ws_loop = None
        _WS_GATE.release()

    async def upload_image(self, data: bytes) -> str:
        if self.quota.remaining:
            raise RoomApiError(MONTHLY_QUOTA_CODE, retry_after=self.quota.remaining)
        result = await self.sdk.driver.upload_image(data=data, file_name="diagram.png")
        if result.get("code") == MONTHLY_QUOTA_CODE:
            self.quota.exhausted()
            raise RoomApiError(MONTHLY_QUOTA_CODE)
        key = result.get("data", {}).get("image_key")
        if result.get("code") != 0 or not isinstance(key, str) or not key:
            raise RuntimeError("Lark image upload failed")
        return key

    @property
    def delivery_backoff(self) -> float:
        return self.quota.remaining

    async def deliver(
        self, chat: str, card: dict[str, Any], request_id: str, message_id: str | None = None
    ) -> Delivery:
        if self.owner is None or (chat != self.owner.chat and self.group_guard is None):
            return Delivery("BLOCKED")
        if self.quota.remaining:
            return Delivery("DEFERRED", retry_after=self.quota.remaining)
        if chat != self.owner.chat and self.group_guard and not await self.group_guard(chat):
            return Delivery("RETRY", retry_after=15)
        card = await self.rich_media.prepare(card)
        if self.quota.remaining:
            return Delivery("DEFERRED", retry_after=self.quota.remaining)
        # Image rendering/upload can outlast the membership cache.
        if chat != self.owner.chat and (
            self.group_guard is None or not await self.group_guard(chat)
        ):
            return Delivery("RETRY", retry_after=15)
        try:
            if message_id:
                result = await self.sdk.update_card(message_id, card)
            else:
                result = await self.sdk.send(
                    chat, {"card": card}, {"uuid": request_id, "receive_id_type": "chat_id"}
                )
        except Exception as failure:
            if getattr(failure, "raw_code", None) == MONTHLY_QUOTA_CODE:
                self.quota.exhausted()
                return Delivery("DEFERRED", retry_after=self.quota.remaining)
            return Delivery("RETRY" if message_id else "UNKNOWN")
        if result.success and (result.message_id or message_id):
            self.quota.recovered()
            return Delivery("SENT", result.message_id or message_id)
        error = result.error
        if getattr(error, "raw_code", None) == MONTHLY_QUOTA_CODE:
            self.quota.exhausted()
            return Delivery("DEFERRED", retry_after=self.quota.remaining)
        code = getattr(getattr(error, "code", None), "value", "")
        if code == "rate_limited" or (message_id and getattr(error, "retryable", False)):
            return Delivery(
                "RETRY",
                retry_after=max(
                    1, min(float(getattr(error, "retry_after_seconds", None) or 5), 300)
                ),
            )
        if code in ("permission_denied", "format_error", "target_revoked"):
            return Delivery("BLOCKED")
        return Delivery("UNKNOWN")
