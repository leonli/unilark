# 兼容矩阵

PRD 12.4 / 15.3：每次兼容版本升级更新本文件。发布物对 OS/架构、Unilark、
Adapter、sidecar runtime 和数据库 schema 的组合发布兼容矩阵。

## 状态：M0 核心门禁与本机 M1 最小闭环通过

尚无发布版本，因此矩阵只记录 Spike 实测环境，不构成任何支持承诺。

M1 `0.0.1` 已验证真实 AGY 创建/输入/来源标签/历史/状态快照与 tag 回执对账。
真实 Lark 已验证普通收发、允许/停止按钮、待审批时重启以及实际桌面输入的同步。
详见 [M1-PROGRESS.md](M1-PROGRESS.md)；完整故障与安装矩阵未完成。
当前兼容检查使用完整 `/main.js` SHA-256：
`af2d07d01fd1a81edb320d2618445d3aaa494b0156407a125edde4872c638f3e`。
不匹配拒绝写入；尚未实现更细的 descriptor 哈希或跨版本矩阵。

| 项 | Spike 实测值 | 取值日期 |
|---|---|---|
| AGY 桌面端 | Antigravity 2.13.0（linux-x64 tar.gz，`antigravity-hub` 渠道） | 2026-09-12 |
| AGY Electron / Chromium | Electron 41.10.3 / Chrome 146.0.7680.216 | 2026-09-12 |
| AGY language server | `resources/bin/language_server`，Go 1.28 构建，177MB | 2026-09-12 |
| 宿主 OS | Ubuntu 24.04（GCE VM），Xvfb :99，无物理显示器 | 2026-09-12 |
| Python | 3.12.3 | 2026-09-12 |
| Lark SDK / 区域 | lark-channel-sdk 1.4.0 / 国际版 Lark，WebSocket | 2026-09-12 |
| Unilark / 状态 schema | 0.0.1 开发版 / v1（含新增心跳与验收表） | 2026-09-12 |
| 本机网关托管 | systemd 系统服务，User=lileon；AGY 独立运行 | 2026-09-12 |

## 已知的兼容性红线（来自 PRD 8.8，Spike 期间持续修订）

- **不要求用户锁 AGY 版本**。Google 服务端有最低客户端版本强制，锁版会在某天
  同时失去 AGY 与 Unilark。改为每次启动/attach 前跑契约自检，不匹配按 FR-AG-04
  拒启并给出「已在 X.Y.Z 验证，当前 A.B.C」诊断。
- 发布说明只写「已在 X.Y.Z 验证」，不写「支持 X.Y.Z 及以上」。
