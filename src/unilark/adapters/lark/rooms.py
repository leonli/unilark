"""Official SDK authenticated HTTP boundary for private session groups."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote

from unilark.conversation.channel import Owner


class RoomApiError(RuntimeError):
    def __init__(self, code: int, *, ambiguous: bool = False) -> None:
        super().__init__(f"Lark group API code {code}")
        self.code, self.ambiguous = code, ambiguous


class LarkRooms:
    def __init__(self, client: Any, owner: Owner) -> None:
        self.client, self.owner = client, owner

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from lark_channel.core.enum import (  # type: ignore[import-untyped]
            AccessTokenType,
            HttpMethod,
        )
        from lark_channel.core.model import BaseRequest  # type: ignore[import-untyped]

        request = (
            BaseRequest.builder()
            .http_method(HttpMethod[method])
            .uri("/open-apis" + path)
            .token_types({AccessTokenType.TENANT})
            .queries(list((query or {}).items()))
            .body(body)
            .build()
        )
        try:
            async with asyncio.timeout(15):
                response = await self.client.arequest(request)
            result: dict[str, Any] = json.loads(response.raw.content)
        except Exception:
            raise RoomApiError(-1, ambiguous=method != "GET") from None
        code = result.get("code", -1)
        if code != 0:
            # Explicit denials are safe to fix and retry; server failures may have committed.
            raise RoomApiError(
                int(code),
                ambiguous=method != "GET"
                and (response.raw.status_code >= 500 or code in (-1, 99991400)),
            )
        return dict(result.get("data", {}))

    @staticmethod
    def marker(request_id: str) -> str:
        return "Unilark session " + request_id

    async def check_permissions(self) -> None:
        data = await self.request("GET", "/application/v6/scopes")
        scopes = {
            s.get("scope_name")
            for s in data.get("scopes", [])
            if s.get("grant_status") == 1 and s.get("scope_type") == "tenant"
        }
        if not scopes.intersection(
            {"im:chat.members:read", "im:chat", "im:chat:readonly", "im:chat.group_info:readonly"}
        ):
            raise RoomApiError(40301)
        if not scopes.intersection({"im:message.group_msg", "im:message.group_msg:readonly"}):
            raise RoomApiError(40302)

    async def create(self, title: str, request_id: str) -> str:
        # Fail before creating an unusable group when tenant authorization is incomplete.
        await self.check_permissions()
        data = await self.request(
            "POST",
            "/im/v1/chats",
            query={
                "user_id_type": "open_id",
                "uuid": request_id,
            },
            body={
                "name": "🤖 " + title[:60],
                "description": self.marker(request_id),
                "user_id_list": [self.owner.user],
                "chat_mode": "group",
                "chat_type": "private",
                "group_message_type": "chat",
                "membership_approval": "approval_required",
                "edit_permission": "only_owner",
            },
        )
        chat = data.get("chat_id")
        if not isinstance(chat, str) or not chat.startswith("oc_"):
            raise RoomApiError(-1, ambiguous=True)
        return chat

    async def configure(self, chat: str) -> None:
        await self.request(
            "PUT",
            "/im/v1/chats/" + quote(chat, safe=""),
            body={
                "add_member_permission": "only_owner",
                "share_card_permission": "not_allowed",
                "edit_permission": "only_owner",
                "membership_approval": "approval_required",
            },
        )

    async def verify(self, chat: str, request_id: str) -> bool:
        path = "/im/v1/chats/" + quote(chat, safe="")
        info = await self.request("GET", path, query={"user_id_type": "open_id"})
        if not (
            info.get("description") == self.marker(request_id)
            and info.get("chat_type") == "private"
            and info.get("chat_mode") == "group"
            and str(info.get("user_count")) == "1"
            and str(info.get("bot_count")) == "1"
            and not info.get("owner_id")  # Only our sole bot owns/manage-invites for this group.
            and info.get("add_member_permission") == "only_owner"
            and info.get("share_card_permission") == "not_allowed"
            and info.get("edit_permission") == "only_owner"
        ):
            return False
        members = await self.request(
            "GET",
            path + "/members",
            query={
                "member_id_type": "open_id",
                "page_size": "100",
            },
        )
        return not members.get("has_more") and [
            m.get("member_id") for m in members.get("items", [])
        ] == [self.owner.user]

    async def reconcile(self, request_id: str) -> list[str]:
        # Read-only reconciliation never blindly repeats an ambiguous creation call.
        found: list[str] = []
        token = ""
        for _ in range(20):
            data = await self.request(
                "GET",
                "/im/v1/chats",
                query={
                    "page_size": "100",
                    **({"page_token": token} if token else {}),
                },
            )
            found.extend(
                str(c["chat_id"])
                for c in data.get("items", [])
                if c.get("description") == self.marker(request_id)
            )
            if not data.get("has_more"):
                return found
            token = str(data.get("page_token", ""))
            if not token:
                break
        raise RoomApiError(-1)
