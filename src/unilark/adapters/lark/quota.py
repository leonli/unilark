"""Shared backoff for an explicit monthly API quota rejection, without retaining payloads."""

from __future__ import annotations

import time
from typing import Any

MONTHLY_QUOTA_CODE = 99991403
QUOTA_BACKOFF = 3600
QUOTA_REASON = "Lark 本月 API 调用额度已耗尽（99991403）；请检查后台额度，恢复后重试。"


class ApiQuota:
    def __init__(self) -> None:
        self.retry_at = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.retry_at - time.time())

    def exhausted(self) -> None:
        self.retry_at = time.time() + QUOTA_BACKOFF

    def recovered(self) -> None:
        self.retry_at = 0.0

    def snapshot(self) -> dict[str, Any]:
        if not self.retry_at:
            return {"state": "available"}
        return {
            "state": "quota_exhausted",
            "code": MONTHLY_QUOTA_CODE,
            "retry_at": self.retry_at,
            "reason": QUOTA_REASON,
        }
