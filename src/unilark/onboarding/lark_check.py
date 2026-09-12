"""Read-only application checks; tokens and raw platform replies never leave here."""

from __future__ import annotations

from typing import Any

import httpx

from unilark.onboarding.credentials import Credentials


async def check_application(credentials: Credentials) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=credentials.domain, timeout=15) as http:
        try:
            response = await http.post(
                "/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": credentials.app_id, "app_secret": credentials.app_secret},
            )
            payload = response.json()
            if response.status_code != 200 or payload.get("code") != 0:
                return {
                    "state": "authentication_failed",
                    "repair": "核对应用区域与凭据；修复后重跑 setup。",
                }
            token = payload["tenant_access_token"]
            response = await http.get(
                "/open-apis/bot/v3/info/", headers={"Authorization": "Bearer " + token}
            )
            payload = response.json()
            if response.status_code != 200 or payload.get("code") != 0:
                return {
                    "state": "bot_unavailable",
                    "repair": "在开发者控制台启用机器人并发布应用。",
                }
            return {
                "state": "verified",
                "bot_enabled": True,
                "permissions": "verify_by_real_messages_and_callbacks",
                "repair": "权限、发布与可用范围须结合真实收发验收。",
            }
        except (httpx.HTTPError, ValueError, KeyError):
            return {
                "state": "network_or_response_error",
                "repair": "检查网络、代理与所选 Lark 区域。",
            }
