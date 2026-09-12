"""Sidecar Interface —— 统一操作，显式声明能力。

对应 PRD 8.4 / 8.5。这里只定义 PRD 已经需要的操作、数据归属和错误语义；不建插件
市场、不建远程 RPC 总线、不预先写三套空实现（PRD 8.4 明文禁止）。

M0 阶段的定位：这份契约是 **Spike 的输入假设**，不是结论。CDP Spike（PRD 3.4）跑完
之后，按真实可行的能力收敛一遍——AGY 是第一个真实 Adapter，只有它验证过的部分才
算数（PRD 8.4 末段：新增第二个真实 Adapter 时再收敛共同部分）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

# --------------------------------------------------------------------------
# 能力（PRD 8.5）
# --------------------------------------------------------------------------


class CapabilityLevel(StrEnum):
    """能力由 Adapter 代码、安装版本和实际连接结果共同决定。

    用户配置不能把 UNSUPPORTED 改成 SUPPORTED（PRD 8.5 首句）。
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    #: 已实现但受版本/路径限制，产品必须如实呈现限制范围（PRD 3.2 第四条）
    EXPERIMENTAL = "experimental"


class Capability(StrEnum):
    """PRD 8.5 能力表。缺失时的行为写在各自的文档串里。"""

    #: 已证明原生客户端与远程入口共享可见会话；AGY MVP 必须具备
    NATIVE_SHARED_SESSION = "native_shared_session"
    #: runtime 原生串行 / 可验证的独占接管 / 不支持
    EXECUTION_ARBITRATION = "execution_arbitration"
    #: 恢复同一原生会话；缺失时不能承诺跨重启接力
    RESUME = "resume"
    EVENT_REPLAY = "event_replay"
    STATE_QUERY = "state_query"
    REQUEST_DEDUP = "request_dedup"
    OPERATION_QUERY = "operation_query"
    #: 取消确认以及工具/子任务处理范围
    INTERRUPT = "interrupt"
    INTERACTION = "interaction"
    #: 执行前拦截。实验版（CDP 路径）没有，对应条目标「待官方 API」（PRD 8.8）
    PRE_EXECUTE_POLICY = "pre_execute_policy"
    STEER = "steer"
    USAGE = "usage"
    ARTIFACTS = "artifacts"


@dataclass(frozen=True)
class CapabilityClaim:
    """一条能力声明。

    ``detail`` 必须写清范围而不是一个布尔：PRD 8.5 要求 ``request_dedup`` 声明
    保留期限、``interrupt`` 声明子任务处理范围、``interaction`` 声明类型覆盖。
    """

    capability: Capability
    level: CapabilityLevel
    detail: str = ""


@dataclass(frozen=True)
class CapabilitySnapshot:
    """创建 / attach 时协商的能力及版本；恢复时重新核对（PRD 7.2）。"""

    engine_kind: str
    engine_version: str
    adapter_version: str
    claims: tuple[CapabilityClaim, ...] = ()

    def level(self, cap: Capability) -> CapabilityLevel:
        for c in self.claims:
            if c.capability is cap:
                return c.level
        return CapabilityLevel.UNSUPPORTED


class SessionMode(StrEnum):
    """PRD 7.2 ``session_mode``。AGY 首版必须是 NATIVE_SHARED。"""

    NATIVE_SHARED = "native_shared"
    MANAGED = "managed"


# --------------------------------------------------------------------------
# 操作结果（PRD 8.4 / 9.4）
# --------------------------------------------------------------------------


class Acceptance(StrEnum):
    """提交回执。**成功发送不等于执行完成**（PRD 8.4 ``submit`` 行）。"""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    #: 结果无法确定。调用方必须暂停该会话后续提交并对账，禁止盲目重发（PRD 9.4）
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OperationReceipt:
    request_id: str
    acceptance: Acceptance
    native_operation_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class SessionBinding:
    """Session 与 sidecar 原生会话的绑定（PRD 7.2）。"""

    sidecar_id: str
    engine_kind: str
    native_session_id: str
    session_mode: SessionMode
    workspace: str
    capability_snapshot: CapabilitySnapshot


@dataclass(frozen=True)
class NormalizedEvent:
    """规范化事件。SDK 类型不穿透此 Interface（PRD 8.4 末段）。

    ``native_opaque`` 留给 Adapter 私有的版本化恢复数据；可能含凭据的值不走这里，
    进受限秘密存储。
    """

    kind: str
    native_session_id: str
    native_turn_id: str | None
    native_cursor: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    native_opaque: bytes | None = None


class HistoryGap(Exception):
    """watch 无法重放到 ``after_cursor``。

    缺口必须可见，不能虚报已同步（PRD FG-09 / 9.5）。
    """


class AttachFailed(Exception):
    """attach 失败。**禁止隐式 create**（PRD 8.4 ``open_session`` 行）。

    上层应把 Session 置为 ``BROKEN_BINDING``，不静默新建空白会话（PRD 9.5）。
    """


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------


class SidecarAdapter(Protocol):
    """PRD 8.4 的七个操作。每个实现必须跑同一套契约场景（PRD 8.7）。"""

    async def probe(self, profile: dict[str, Any]) -> CapabilitySnapshot:
        """不启动任务；返回依赖/认证/版本检查结果，供 setup 与 doctor 共用。"""
        ...

    async def connect(self, profile: dict[str, Any]) -> Any:
        """启动已授权的自有进程或连接已有进程；返回连接句柄、能力和进程归属。"""
        ...

    async def open_session(
        self,
        *,
        create: bool,
        ref: str | None,
        workspace: str,
        request_id: str,
    ) -> SessionBinding:
        """attach 失败禁止隐式 create；创建同样遵循操作回执契约。"""
        ...

    async def submit(
        self, binding: SessionBinding, command: Any, request_id: str
    ) -> OperationReceipt:
        """command 为有类型的 input / steer / cancel / resolve_interaction。"""
        ...

    def watch(
        self, binding: SessionBinding, after_cursor: str | None
    ) -> AsyncIterator[NormalizedEvent]:
        """不支持历史重放时抛 :class:`HistoryGap`，不静默跳过。"""
        ...

    async def inspect(
        self, binding: SessionBinding, request_id: str | None = None
    ) -> dict[str, Any]:
        """查询执行状态、操作回执和恢复快照；无法判定时返回 UNKNOWN。"""
        ...

    async def disconnect(self, handle: Any) -> None:
        """只在明确配置时停止 Unilark 拥有的进程，不杀原生客户端或外部进程。"""
        ...
