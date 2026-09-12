"""Capabilities of the verified AGY 2.13.0 path, never user-overridable."""

from unilark.adapters.sidecars.interface import (
    Capability,
    CapabilityClaim,
    CapabilityLevel,
    CapabilitySnapshot,
)

CAPABILITIES = CapabilitySnapshot(
    "agy",
    "2.13.0",
    "0.0.2",
    tuple(
        CapabilityClaim(cap, CapabilityLevel.EXPERIMENTAL, detail)
        for cap, detail in (
            (Capability.NATIVE_SHARED_SESSION, "同一原生会话；仅已验证的桌面 bundle"),
            (Capability.RESUME, "按实例与原生 UUID 恢复，不依赖会话枚举"),
            (Capability.STATE_QUERY, "当前快照与历史步骤；步骤位置可变，不是不可变事件游标"),
            (Capability.OPERATION_QUERY, "持久化 tag 可证明收到；缺失不能证明未执行"),
            (Capability.INTERRUPT, "停止原生会话树，核验 idle；不撤销已发生副作用"),
            (
                Capability.INTERACTION,
                "run_command 单次审批与 ask_question 回答；其他交互需桌面处理",
            ),
            (Capability.STEER, "NEXT_INVOCATION 补充当前任务；不撤销此前副作用"),
        )
    ),
)
