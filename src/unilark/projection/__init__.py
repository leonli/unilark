"""Projection & Outbox（PRD 8.2、9.5）。

按 cursor 把已确认事实投影到 Lark。投影失败不回滚已发生的任务，进可重试
outbox 并标同步降级；不把事件日志当 prompt 回灌给原生端。
"""
