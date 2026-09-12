# 兼容矩阵

PRD 12.4 / 15.3：每次兼容版本升级更新本文件。发布物对 OS/架构、Unilark、
Adapter、sidecar runtime 和数据库 schema 的组合发布兼容矩阵。

## 状态：0.0.2 本地实验版

已有本地版本化离线发行包，未公开发行；矩阵仅承诺以下实测组合。

M1 `0.0.1` 已验证真实 AGY 创建/输入/来源标签/历史/状态快照与 tag 回执对账。
真实 Lark 已验证普通收发、允许/停止按钮、待审批时重启以及实际桌面输入的同步。
0.0.2 新增真实 AGY 结构化问答/取消/新会话工作目录、三种 SIGKILL 恢复，以及干净目录安装/升级/回退/卸载。
故障和验收边界详见 [RELEASE-0.0.2.md](RELEASE-0.0.2.md)。
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
| Unilark / 状态 schema | 0.0.2 / v2；支持 v1→v2 迁移，拒绝旧程序自动回退 | 2026-09-12 |
| 本机托管 | systemd 系统服务，User=lileon；网关、AGY、桌面分别监督 | 2026-09-12 |
| 离线发行包 | Linux x86_64，Python 3.12；14 个锁定运行依赖 + Unilark wheel | 2026-09-12 |

macOS、Windows、ARM、飞书中国版、其他 AGY 版本及实际整机重启尚未验收。
Python 3.11 只有源码目标和 CI 配置，未在本机执行，因此不列入已验证离线包。
相同名称/版本但不同 bundle 哈希仍拒绝写入，不能据此猜测兼容。

## 已知的兼容性红线（来自 PRD 8.8，Spike 期间持续修订）

- **不要求用户锁 AGY 版本**。Google 服务端有最低客户端版本强制，锁版会在某天
  同时失去 AGY 与 Unilark。改为每次启动/attach 前跑契约自检，不匹配按 FR-AG-04
  拒启并给出「已在 X.Y.Z 验证，当前 A.B.C」诊断。
- 发布说明只写「已在 X.Y.Z 验证」，不写「支持 X.Y.Z 及以上」。
