"""Small runtime view used by the gateway, independent of AGY wire types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from unilark.adapters.sidecars.interface import CapabilitySnapshot


class Busy(Exception):
    """No request was sent to the runtime; retaining it in the queue is safe."""


class Rejected(Exception):
    """The runtime explicitly rejected the operation; this is not a lost receipt."""


@dataclass(frozen=True)
class Question:
    text: str
    options: tuple[tuple[str, str], ...] = ()
    multi: bool = False


@dataclass(frozen=True)
class Answer:
    selected: tuple[str, ...] = ()
    text: str = ""


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
    questions: tuple[Question, ...] = ()


@dataclass(frozen=True)
class SessionView:
    idle: bool
    status: str
    anchor: str
    steps: list[StepView] = field(default_factory=list)


class Runtime(Protocol):
    capabilities: CapabilitySnapshot

    async def view(self, session_id: str) -> SessionView: ...
    async def create(self, session_id: str, *, workspace: str = "") -> None: ...
    async def workspace(self, project_id: str = "") -> str: ...
    async def send(
        self, session_id: str, text: str, request_id: str, *, steer: bool = False
    ) -> None: ...
    async def stop(self, session_id: str) -> bool: ...
    async def resolve_permission(
        self, session_id: str, index: int, *, allow: bool, fingerprint: str | None = None
    ) -> str: ...
    async def answer(
        self,
        session_id: str,
        index: int,
        answers: tuple[Answer, ...],
        *,
        fingerprint: str,
        cancel: bool = False,
    ) -> str: ...
