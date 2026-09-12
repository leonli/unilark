"""PolicyEngine（PRD 8.2、10.7）。

按用户、聊天、工作区和**实际待执行工具请求的真实参数**返回 ALLOW / DENY /
REQUIRE_APPROVAL。摘要只用于展示，不能代替授权依据。

实验版边界（PRD 8.8）：认证归 Unilark，授权归 AGY 自身审批弹窗；
FR-SC-03/04/06 在实验版标「待官方 API」。
"""
