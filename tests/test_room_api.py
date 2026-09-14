from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_gateway import OWNER
from unilark.adapters.lark.rooms import LarkRooms, RoomApiError


def response(code=0, status=200, data=None):
    return SimpleNamespace(
        raw=SimpleNamespace(
            content=json.dumps({"code": code, "data": data or {}}), status_code=status
        )
    )


@pytest.mark.parametrize(
    ("result", "ambiguous"),
    [
        (response(99991672, 403), False),
        (response(1254290, 429), False),
        (response(9999, 503), True),
        (TimeoutError(), True),
    ],
)
async def test_post_ack_loss_and_explicit_denial_are_distinct(result, ambiguous):
    client = SimpleNamespace(arequest=AsyncMock())
    if isinstance(result, Exception):
        client.arequest.side_effect = result
    else:
        client.arequest.return_value = result
    with pytest.raises(RoomApiError) as error:
        await LarkRooms(client, OWNER).request("POST", "/im/v1/chats", body={})
    assert error.value.ambiguous == ambiguous
    assert client.arequest.await_count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"user_count": "2"},
        {"bot_count": "2"},
        {"owner_id": "ou_owner"},
        {"add_member_permission": "all_members"},
        {"share_card_permission": "allowed"},
        {"description": "other"},
        {"chat_type": "public"},
        {"edit_permission": "all_members"},
    ],
)
async def test_member_counts_ownership_and_locked_group_settings_required(change):
    info = dict(
        description="Unilark session req",
        chat_type="private",
        chat_mode="group",
        user_count="1",
        bot_count="1",
        add_member_permission="only_owner",
        share_card_permission="not_allowed",
        edit_permission="only_owner",
    )
    api = LarkRooms(None, OWNER)
    api.request = AsyncMock(return_value={**info, **change})
    assert not await api.verify("oc_room", "req")
    assert api.request.await_count == 1


@pytest.mark.parametrize("members", [[{"member_id": "ou_other"}], [{"member_id": OWNER.user}]])
async def test_exact_human_identity_required(members):
    info = dict(
        description="Unilark session req",
        chat_type="private",
        chat_mode="group",
        user_count="1",
        bot_count="1",
        add_member_permission="only_owner",
        share_card_permission="not_allowed",
        edit_permission="only_owner",
    )
    api = LarkRooms(None, OWNER)
    api.request = AsyncMock(side_effect=[info, {"items": members, "has_more": False}])
    assert await api.verify("oc_room", "req") == (members[0]["member_id"] == OWNER.user)


async def test_no_group_is_created_before_required_permissions_granted():
    client = SimpleNamespace(arequest=AsyncMock(return_value=response(data={"scopes": []})))
    with pytest.raises(RoomApiError) as error:
        await LarkRooms(client, OWNER).create("title", "req")
    assert error.value.code == 40301 and not error.value.ambiguous
    assert client.arequest.await_count == 1


async def test_monthly_quota_stops_other_room_api_calls_until_cooldown(monkeypatch):
    import time

    clock = time.time()
    monkeypatch.setattr(time, "time", lambda: clock)
    client = SimpleNamespace(arequest=AsyncMock(return_value=response(99991403, 429)))
    api = LarkRooms(client, OWNER)
    for path in ("/im/v1/chats/oc_one", "/im/v1/chats/oc_two", "/application/v6/scopes"):
        with pytest.raises(RoomApiError) as error:
            await api.request("GET", path)
        assert error.value.code == 99991403
    assert client.arequest.await_count == 1
    clock += 3601
    client.arequest.return_value = response(data={"recovered": True})
    assert await api.request("GET", "/im/v1/chats/oc_one") == {"recovered": True}
    assert client.arequest.await_count == 2
