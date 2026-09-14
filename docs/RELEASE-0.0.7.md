# Unilark 0.0.7 · API quota recovery / API 额度保护

2026-09-14，实验版。新建会话群无法完成核验时，真实 Lark 接口返回 HTTP 429、
`99991403: This month's API call quota has been exceeded`。
服务与 WebSocket 连接仍正常，因此单看进程或连接会误以为服务健康。

## 修复

- 删除 READY 会话群的空闲轮询。此前 5 秒核验缓存到期后，后台持续请求群信息和成员两个接口；
  现在仅在真实收发边界进行核验，保留短缓存和成员不匹配时暂停收发的保护。
- 将明确的月度额度错误与未知投递结果分开。新发生的额度拒绝记录为 `DEFERRED`，保存消息等待重试；
  旧版已记录的 `UNKNOWN` 不会被批量改写或重发。
- 群 API、消息发送/更新和图片上传共用一小时退避。冷却期不继续发送积压卡片，
  不消耗每张卡的短期重试次数；健康记录保存退避时间，网关重启后继续遵守。
- 群创建明确被额度拒绝时保留原请求，额度恢复后可重试；真正的模糊创建结果仍先核对，不能盲目建群。
- 群暂停原因和 `doctor` 明确显示额度错误，不再误报成群成员或权限变化。

## 验证与恢复边界

本地回归覆盖空闲时零核验请求、下次输入仍核验、跨群退避、消息与群接口共享退避、
长退避不耗尽卡片重试次数、恢复投递、保留原建群请求，以及连接正常但额度耗尽时 doctor 返回 2。

此修复不能恢复已耗尽的平台额度。需要管理员在 Lark 后台核对用量、额度和重置日期；
退避到期后下一个必要请求会尝试恢复。如果仍被额度拒绝，继续退避一小时。
无需重新发布菜单、重新配对或创建替代群。未恢复额度前，不能声称真实新建会话已验收通过。

沿用 schema 3，既有会话、消息 ID、未知结果和凭据保留。

## English

Lark rejected verification of a newly created session group with HTTP 429 and
`99991403: This month's API call quota has been exceeded`, while the gateway and
WebSocket connection remained active.

Ready groups no longer poll membership while idle. Actual message I/O still checks
membership and group settings with the existing short cache. Explicit monthly quota
rejections defer delivery instead of creating an UNKNOWN outcome. Group APIs, message
sends/updates, and image uploads share a one-hour cooldown retained across gateway
restarts. Deferred backlog does not consume short-term delivery retry attempts.
Explicitly rejected creation retains its original request for retry; ambiguous
creation and older UNKNOWN messages still require reconciliation.

The group status and doctor now distinguish exhausted quota from permission changes.
Local regression tests cover the idle request budget, membership enforcement,
shared cooldown, eventual retry, and degraded diagnostics with a healthy connection.
Real creation remains blocked until the administrator restores the platform quota
or its period resets. No new menu publication, pairing, or replacement groups are needed.
