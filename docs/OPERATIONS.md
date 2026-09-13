# 本机运行维护

2026-09-12：本机 Lark 配置、owner 配对和 AGY 登录均已完成。不要重配 owner 或另启 WebSocket 消费者。
当前网关为本地安装的 **0.0.3**（2026-09-13 已升级）；程序入口 `~/.local/bin/unilark`，发行目录 `~/.local/share/unilark/`。

手机入口：`/list` 会话面板、`/` 命令卡、`/status` 当前会话及队列、`/tasks` 全部任务。
原会话、选择、输入和已知消息编号均已核对保留。新版保持 schema 2，新增可选 UI 表；
升级前备份 `backups/before-0.0.3-1789277533050706915.db`，兼容回退目标为 0.0.2。
交互说明和真机停止边界见 [0.0.3 说明](RELEASE-0.0.3.md)。

```bash
~/.local/bin/unilark --config /home/lileon/doc/unilark/spike/runtime/unilark.toml doctor
~/.local/bin/unilark --config /home/lileon/doc/unilark/spike/runtime/unilark.toml setup --non-interactive --system
~/.local/bin/unilark service status --system
~/.local/bin/unilark service restart --system
```

| 服务 | 职责 |
|---|---|
| `unilark.service` | 网关、队列、Lark 收发，以 lileon 运行 |
| `unilark-agy.service` | 私有原生宿主、原 AGY 安装/profile/keyring |
| `unilark-desktop.service` | 本机 Xvfb :99、桌面及回环 noVNC |

三者均已启用且由 systemd 监督；AGY 依赖桌面服务。实际整机重启/睡眠恢复尚未验收。
网关重启不关闭 AGY，也不清理登录与原生历史。不要为测试这些服务重启生产 web-xia 或整台 VM。
AGY/桌面两个 unit 是本机私有环境配置，不属于通用网关卸载范围。

```bash
systemctl status unilark.service unilark-agy.service unilark-desktop.service
sudo systemctl stop unilark.service
sudo systemctl start unilark.service
# 仅在服务达到重启限额且已修复原因时：
sudo systemctl reset-failed unilark.service
```

网关异常退出等 15 秒重启，5 分钟最多 10 次。服务设 UMask=0077、NoNewPrivileges、只读系统/家目录，
允许网关写入 `~/.unilark/`。unit 只包含凭据文件路径。AGY 发现需要读取同用户 /proc 和回环 API。

| 内容 | 路径 |
|---|---|
| 实例配置 | `/home/lileon/doc/unilark/spike/runtime/unilark.toml` |
| 凭据 | `~/.unilark/lark.env`，0600 |
| 状态与历史验收 | `~/.unilark/state.db`，schema 2 |
| 新版应用日志 | `~/.unilark/gateway-app.log`，5 MB × 3 个备份 |
| 旧版历史日志 | `~/.unilark/gateway.log`，保留 |
| unit 副本、验证快照 | `~/.unilark/run/` |
| 一致性备份 | `~/.unilark/backups/` |
| 私有故障证据 | `/home/lileon/doc/unilark/spike/evidence/` |

PID 以 `systemctl show unilark.service -p MainPID` 和 doctor 的启动身份为准；`run/gateway.json` 只是快照。
doctor 返回 0 表示当前进程、心跳、Lark、AGY 观测及待确认/阻塞状态均通过；返回 2 表示待修复。
历史 acceptance 不随离线消失，也不是完整产品验收的声明。

同一应用不得同时运行 run、pair 或 doctor --connect-lark。停止网关不会停止已经在 AGY 执行的任务；
需要停止任务时使用 Lark 停止按钮或 `/stop`。UNKNOWN 保留账本，按 [安装维护指南](install.md) 本机核对。

本机未创建远程仓库、未公开发布。离线期间入站消息能否补发取决于 Lark；没有回执时先核对状态，避免重复任务。
