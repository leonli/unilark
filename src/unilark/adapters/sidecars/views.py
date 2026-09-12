"""Small runtime view used by the gateway, independent of AGY wire types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Busy(Exception):
    """No request was sent to the runtime; retaining it in the queue is safe."""


@dataclass(frozen=True)
class StepView:
    index: int
    kind: str
    status: str
    text: str = ""
    tool: str = ""
    operation_ids: tuple[str, ...] = ()
    permission: bool = False
    fingerprint: str = ""
    resource: str = ""


@dataclass(frozen=True)
class SessionView:
    idle: bool
    status: str
    anchor: str
    steps: list[StepView] = field(default_factory=list)


class Runtime(Protocol):
    async def view(self, session_id: str) -> SessionView: ...
    async def create(self, session_id: str) -> None: ...
    async def send(
        self, session_id: str, text: str, request_id: str, *, steer: bool = False
    ) -> None: ...
    async def stop(self, session_id: str) -> bool: ...
    async def resolve_permission(
        self, session_id: str, index: int, *, allow: bool, fingerprint: str | None = None
    ) -> str: ...
