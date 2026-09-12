"""AGY 2.13.0 operations verified against the real desktop.

This is an experimental, private API. Tags correlate operations; they have NOT
been proved to deduplicate requests at the runtime. A lost reply is never retried.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from unilark.adapters.sidecars.views import Busy, SessionView, StepView

from .transport import ProtocolError, Transport

VERIFIED_BUNDLE_SHA256 = "af2d07d01fd1a81edb320d2618445d3aaa494b0156407a125edde4872c638f3e"
SANDBOX_POLICY = "CASCADE_COMMANDS_AUTO_EXECUTION_PROCEED_IN_SANDBOX"
TERMINAL = {
    "CORTEX_STEP_STATUS_DONE",
    "CORTEX_STEP_STATUS_ERROR",
    "CORTEX_STEP_STATUS_CANCELED",
    "CORTEX_STEP_STATUS_INTERRUPTED",
}


def permission_fingerprint(step: dict[str, Any]) -> str:
    metadata = step.get("metadata", {})
    value = [
        metadata.get("sourceTrajectoryStepInfo"),
        metadata.get("toolCall", {}).get("id"),
        step.get("requestedInteraction"),
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Profile:
    project_id: str
    model: str
    source_label: str = "Lark · Unilark"


def operation_tag(request_id: str) -> str:
    return "unilark:request:" + str(uuid.UUID(request_id))


def public_step(step: dict[str, Any], index: int) -> dict[str, Any]:
    """Only select product-visible fields; never copy planner thinking/signatures."""
    result: dict[str, Any] = {
        "index": index,
        "type": step.get("type", ""),
        "status": step.get("status", ""),
    }
    user = step.get("userInput", {})
    planner = step.get("plannerResponse", {})
    if user:
        result["text"] = user.get("userResponse", "")
        result["tags"] = user.get("tags", [])
        result["source_label"] = user.get("userIdentity", {}).get("username")
    elif planner:
        result["text"] = planner.get("modifiedResponse") or planner.get("response", "")
    tool = step.get("metadata", {}).get("toolCall")
    if tool:
        result["tool_name"] = tool.get("name", "")
    return result


class AgyClient:
    def __init__(self, transport: Transport, profile: Profile) -> None:
        self.transport = transport
        self.profile = profile
        self._verified_process: tuple[int, str] | None = None

    async def check(self) -> dict[str, Any]:
        endpoint = await self.transport.current()
        response = await self.transport.http.get(f"https://127.0.0.1:{endpoint.port}/main.js")
        digest = hashlib.sha256(response.content).hexdigest()
        if response.status_code != 200 or digest != VERIFIED_BUNDLE_SHA256:
            self._verified_process = None
            raise ProtocolError("Unverified AGY bundle; all writes disabled (tested: 2.13.0)")
        self._verified_process = (endpoint.process.pid, endpoint.process.started)
        return {
            "version": "2.13.0",
            "bundle_sha256": digest,
            "pid": endpoint.process.pid,
            "source_label": self.profile.source_label,
            "status": "verified",
        }

    async def _before_write(self) -> None:
        endpoint = await self.transport.current()
        if self._verified_process != (endpoint.process.pid, endpoint.process.started):
            await self.check()

    async def steps(self, session_id: str) -> list[dict[str, Any]]:
        uuid.UUID(session_id)
        response = await self.transport.call("GetCascadeTrajectorySteps", {"cascadeId": session_id})
        steps = response.get("steps", [])
        if not isinstance(steps, list) or any(not isinstance(s, dict) for s in steps):
            raise ProtocolError("Invalid trajectory steps")
        return steps

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        uuid.UUID(session_id)
        request = {
            "conversationId": session_id,
            "subscriberId": "unilark-" + uuid.uuid4().hex,
            "initialStepsPageBounds": {"startIndex": 0},
            "trajectoryVerbosity": "CLIENT_TRAJECTORY_VERBOSITY_PROD_UI",
            "initialGeneratorMetadatasPageBounds": {"startIndex": -1},
            "initialExecutorMetadatasPageBounds": {"endIndexExclusive": 0},
            "disableRehydration": True,
        }
        # A subscription's first full state is a snapshot, not complete event replay.
        async with asyncio.timeout(15):
            async with aclosing(
                self.transport.stream("StreamAgentStateUpdates", request)
            ) as stream:
                first = await anext(stream)
        update = first.get("update")
        if not isinstance(update, dict) or update.get("conversationId") != session_id:
            raise ProtocolError("No authoritative initial state for the requested session")
        return update

    @staticmethod
    def idle(snapshot: dict[str, Any]) -> bool:
        # Only call with the first full snapshot. Proto3 omits empty collections.
        background = snapshot.get("backgroundTasksUpdate", {})
        queued = snapshot.get("pendingAgentMessagesUpdate", {})
        commands = snapshot.get("backgroundCommandsUpdate", {})
        if any(not isinstance(v, dict) for v in (background, queued, commands)):
            return False
        return bool(
            snapshot.get("fullyIdle") is True
            and snapshot.get("status") == "CASCADE_RUN_STATUS_IDLE"
            and not snapshot.get("hasActiveChildren", False)
            and all(not v.get("totalLength", 0) for v in (background, queued, commands))
        )

    async def create(self, session_id: str) -> None:
        uuid.UUID(session_id)
        await self._before_write()
        await self.transport.call(
            "StartCascade",
            {
                "source": "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT",
                "cascadeId": session_id,
                "requestedModel": self.profile.model,
                "projectEnvConfig": {
                    "projectId": self.profile.project_id,
                    "defaultProjectEnvironment": {},
                },
            },
        )

    async def send(
        self, session_id: str, text: str, request_id: str, *, steer: bool = False
    ) -> None:
        uuid.UUID(session_id)
        tag = operation_tag(request_id)
        if not text.strip():
            raise ValueError("Empty input")
        await self._before_write()
        if not steer and not self.idle(await self.snapshot(session_id)):
            raise Busy("AGY is busy or state is uncertain; keep this input in the local queue")
        await self.transport.call(
            "SendUserCascadeMessage",
            {
                "cascadeId": session_id,
                "items": [{"text": text}],
                "tags": [tag],
                "userIdentity": {"username": self.profile.source_label},
                "cascadeConfig": {
                    "plannerConfig": {
                        "toolConfig": {
                            "runCommand": {
                                "autoCommandConfig": {
                                    "autoExecutionPolicy": SANDBOX_POLICY,
                                }
                            },
                            "notifyUser": {},
                        },
                        "requestedModel": {"model": self.profile.model},
                        "knowledgeConfig": {},
                        "useAiCredits": False,
                        "supportsLatexRendering": True,
                    },
                    "executorConfig": {"useCoreDirect": True},
                    "conversationHistoryConfig": {},
                },
                "customAgentSpec": {
                    "builtinAgent": {
                        "defaultAgent": {
                            "isGoogle": False,
                            "isInteractive": True,
                        }
                    }
                },
                "deliveryStrategy": (
                    "MESSAGE_DELIVERY_STRATEGY_NEXT_INVOCATION"
                    if steer
                    else "MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE"
                ),
            },
        )

    async def locate(self, session_id: str, request_id: str) -> int | None:
        tag = operation_tag(request_id)
        hits = [
            i
            for i, s in enumerate(await self.steps(session_id))
            if tag in s.get("userInput", {}).get("tags", [])
        ]
        if len(hits) > 1:
            raise ProtocolError(
                "Multiple native steps have the same operation tag; inspect manually"
            )
        return hits[0] if hits else None

    async def stop(self, session_id: str) -> bool:
        uuid.UUID(session_id)
        await self._before_write()
        response = await self.transport.call("ForceStopCascadeTree", {"conversationId": session_id})
        if session_id not in response.get("stoppedConversationIds", []):
            return False
        return self.idle(await self.snapshot(session_id))

    async def resolve_permission(
        self, session_id: str, index: int, *, allow: bool, fingerprint: str | None = None
    ) -> str:
        """Permission-only, once-only resolution; never infer type from step WAITING alone."""
        steps = await self.steps(session_id)
        if index < 0 or index >= len(steps):
            raise ValueError("Unknown step")
        step = steps[index]
        if fingerprint is not None and permission_fingerprint(step) != fingerprint:
            raise ProtocolError("Permission request changed; old card is invalid")
        tool = step.get("metadata", {}).get("toolCall", {})
        if (
            step.get("status") != "CORTEX_STEP_STATUS_WAITING"
            or tool.get("name") != "run_command"
            or not isinstance(step.get("requestedInteraction", {}).get("permission"), dict)
        ):
            raise ProtocolError("Only verified run_command permission interactions are supported")
        info = step.get("metadata", {}).get("sourceTrajectoryStepInfo", {})
        trajectory = info.get("trajectoryId")
        if not trajectory:
            raise ProtocolError("Missing native trajectory for permission interaction")
        await self._before_write()
        await self.transport.call(
            "HandleCascadeUserInteraction",
            {
                "cascadeId": session_id,
                "interaction": {
                    "trajectoryId": trajectory,
                    "stepIndex": info.get("stepIndex", index),
                    "permission": {"allow": allow, "scope": "PERMISSION_SCOPE_ONCE"},
                },
            },
        )
        updated = await self.steps(session_id)
        # Leaving WAITING might mean ERROR, not successful authorization/execution.
        return str(updated[index].get("status", "UNKNOWN")) if index < len(updated) else "UNKNOWN"

    async def close(self) -> None:
        await self.transport.close()

    async def view(self, session_id: str) -> SessionView:
        snapshot = await self.snapshot(session_id)
        raw_steps = await self.steps(session_id)
        steps = []
        anchor = "-1"
        for index, raw in enumerate(raw_steps):
            public = public_step(raw, index)
            status = str(raw.get("status", "")).removeprefix("CORTEX_STEP_STATUS_").lower()
            kind = (
                "user"
                if "userInput" in raw
                else "assistant"
                if "plannerResponse" in raw
                else "tool"
            )
            if kind == "user":
                anchor = hashlib.sha256(
                    json.dumps(
                        [
                            index,
                            raw.get("userInput"),
                            raw.get("metadata", {}).get("sourceTrajectoryStepInfo"),
                        ],
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
            permission = raw.get("requestedInteraction", {}).get("permission")
            operation_ids = []
            for tag in public.get("tags", []):
                if isinstance(tag, str) and tag.startswith("unilark:request:"):
                    try:
                        operation_ids.append(str(uuid.UUID(tag.removeprefix("unilark:request:"))))
                    except ValueError:
                        continue
            steps.append(
                StepView(
                    index,
                    kind,
                    status,
                    public.get("text", ""),
                    public.get("tool_name", ""),
                    tuple(operation_ids),
                    isinstance(permission, dict) and public.get("tool_name") == "run_command",
                    permission_fingerprint(raw),
                    str(permission.get("resource", {}).get("target", ""))
                    if isinstance(permission, dict)
                    else "",
                )
            )
        idle = self.idle(snapshot) and not any(
            s.status in ("running", "generating", "waiting") for s in steps
        )
        status = (
            "idle"
            if idle
            else "waiting"
            if any(s.status == "waiting" for s in steps)
            else "running"
        )
        return SessionView(idle, status, anchor, steps)
