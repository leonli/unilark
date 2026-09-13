"""One private Lark group per native session, with conservative provisioning recovery."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from unilark.adapters.lark.rooms import RoomApiError
from unilark.conversation.channel import Owner
from unilark.store.gateway import GatewayStore


def room_link(chat: str, domain: str = "https://open.larksuite.com") -> str:
    host = "applink.feishu.cn" if "feishu.cn" in domain else "applink.larksuite.com"
    return "https://" + host + "/client/chat/open?" + urlencode({"chatId": chat})


class Rooms:
    def __init__(self, store: GatewayStore, owner: Owner, api: Any) -> None:
        self.store, self.owner, self.api = store, owner, api
        self.verified: dict[str, float] = {}

    async def allowed(self, chat: str) -> bool:
        binding = self.store.rooms.binding(self.owner, chat, ready=False)
        if not binding:
            return False
        room = self.store.rooms.get(self.owner, binding)
        assert room is not None
        if room["status"] == "READY" and self.verified.get(chat, 0) > time.monotonic():
            return True
        if room["status"] == "BLOCKED" and room["retry_at"] > time.time():
            return False
        try:
            safe = bool(await self.api.verify(chat, room["request_id"]))
        except Exception:
            safe = False
        if safe:
            self.verified[chat] = time.monotonic() + 5
            self.store.rooms.state(binding, "READY", retry_at=0)
        else:
            self.verified.pop(chat, None)
            self.store.rooms.state(
                binding,
                "BLOCKED",
                "群成员或权限尚未通过核验，暂停群收发。",
                retry_at=time.time() + 15,
            )
        return safe

    async def tick(self, views: dict[str, Any], profile: str) -> None:
        for room in self.store.rooms.all(self.owner):
            binding = room["binding"]
            session = self.store.session(self.owner, binding)
            if session["profile"] != profile or room["retry_at"] > time.time():
                continue
            if room["status"] in ("READY", "BLOCKED"):
                if room["chat"]:
                    await self.allowed(room["chat"])
                continue
            if room["status"] == "ERROR" or session["state"] != "ACTIVE":
                continue
            try:
                if room["status"] in ("CREATING", "UNKNOWN"):
                    matches = await self.api.reconcile(room["request_id"])
                    if len(matches) != 1:
                        self.store.rooms.state(
                            binding,
                            "UNKNOWN",
                            "群创建结果待确认，未重复建群。",
                            retry_at=time.time() + 30,
                        )
                        continue
                    self.store.rooms.state(binding, "CONFIGURING", chat=matches[0])
                    refreshed = self.store.rooms.get(self.owner, binding)
                    assert refreshed is not None
                    room = refreshed
                if room["status"] == "QUEUED":
                    view = views.get(binding)
                    if view is None or not view.idle:
                        self.store.rooms.state(binding, "QUEUED", "等待会话空闲后建立群入口。")
                        continue
                    baseline = max((s.index for s in view.steps), default=-1)
                    self.store.rooms.state(binding, "CREATING", baseline=baseline)
                    chat = await self.api.create(session["title"], room["request_id"])
                    self.store.rooms.state(binding, "CONFIGURING", chat=chat)
                    refreshed = self.store.rooms.get(self.owner, binding)
                    assert refreshed is not None
                    room = refreshed
                if room["status"] == "CONFIGURING":
                    await self.api.configure(room["chat"])
                    await self.allowed(room["chat"])
            except RoomApiError as error:
                current = self.store.rooms.get(self.owner, binding)
                assert current is not None
                if current["status"] == "CREATING":
                    self.store.rooms.state(
                        binding,
                        "UNKNOWN" if error.ambiguous else "ERROR",
                        "群创建结果待确认。"
                        if error.ambiguous
                        else {
                            40301: "请在 Lark 后台添加并发布 im:chat.members:read，然后重试建群。",
                            40302: "请发布 im:message.group_msg:readonly 权限，然后重试建群。",
                        }.get(error.code, "建群未成功，请检查应用建群权限后重试。"),
                        retry_at=time.time() + 30,
                    )
                else:
                    self.store.rooms.state(
                        binding,
                        current["status"],
                        "正在等待群接口恢复。",
                        retry_at=time.time() + 30,
                    )
            except Exception:
                current = self.store.rooms.get(self.owner, binding)
                assert current is not None
                self.store.rooms.state(
                    binding,
                    "UNKNOWN" if current["status"] == "CREATING" else current["status"],
                    "群入口暂未就绪。",
                    retry_at=time.time() + 30,
                )

        # Persisted initial task is released exactly once after the group is verified.
        for room in self.store.rooms.all(self.owner):
            session = self.store.session(self.owner, room["binding"])
            if (
                room["status"] != "READY"
                or not room["first_task"]
                or session["profile"] != profile
                or session["state"] != "ACTIVE"
                or not await self.allowed(room["chat"])
            ):
                continue
            event = "room:first:" + room["binding"]
            self.store.rooms.remember(self.owner, event, room["chat"])
            self.store.accept_input(self.owner, event, room["binding"], room["first_task"], "input")
            self.store.rooms.state(room["binding"], "READY", first_task="")
