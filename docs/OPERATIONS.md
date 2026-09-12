# 本机运行维护

2026-09-12：本机配置与 owner 配对已完成，不必再跑配置向导。
`unilark.service` 是系统级 unit，以 `lileon` 用户运行网关；已启动并启用开机启动。
AGY 由原有独立宿主管理，网关退出或重启不关闭桌面、不清理登录与会话。
AGY 尚未加入开机托管；机器重启后需先恢复该实例，再确认网关健康。

```bash
systemctl status unilark.service
cd /home/lileon/doc/unilark/repo
.venv/bin/unilark --config ../spike/runtime/unilark.toml doctor
sudo systemctl restart unilark.service
# 停止并保留全部状态：
sudo systemctl stop unilark.service
```

unit 位于 `/etc/systemd/system/unilark.service`。异常退出后等 15 秒再启动，
5 分钟内最多启动 10 次；若 AGY 不可用而达到限制，恢复 AGY 后执行：

```bash
sudo systemctl reset-failed unilark.service
sudo systemctl start unilark.service
```

服务使用 `UMask=0077`、`NoNewPrivileges=true`、只读系统/家目录，
仅允许网关写入 `~/.unilark/`。unit 只包含凭据文件路径，不包含 Secret。
本机实例发现需要读取同用户的 `/proc` 与访问 AGY 回环 HTTPS，因此不隔离网络或隐藏进程。

| 内容 | 位置 |
|---|---|
| 实例配置 | `/home/lileon/doc/unilark/spike/runtime/unilark.toml` |
| 凭据 | `~/.unilark/lark.env`（0600） |
| 状态与历史验收 | `~/.unilark/state.db` |
| 网关日志 | `~/.unilark/gateway.log` |
| 服务单元副本和验证快照 | `~/.unilark/run/` |

PID 会变化，以 `systemctl show unilark.service -p MainPID` 和 doctor 的进程启动身份为准；
`run/gateway.json` 只是最近核对的快照。不要仅凭旧 PID 停进程。
日志可能包含运行信息，不应公开上传；Secret 已通过日志脱敏处理。

doctor 返回 0 表示当前进程/心跳、连接、AGY 会话观测及待确认/阻塞状态均通过检查，
返回 2 表示未就绪。`acceptance` 保存已完成的真实验证，不会因服务停机而自动消失，
也不代表当前版本已经通过全部产品验收。

同一应用不要同时启动 `run`、`pair` 或 `doctor --connect-lark` 的第二个消费者。
更改源码后须重启服务才载入新代码；运行过程中不要删除账本强制重发 UNKNOWN 操作。
停止服务仅暂停网关工作，已经在 AGY 执行的任务仍可能继续；需要停止任务时先在 Lark 使用停止按钮或 `/stop`。

此 unit 是本机配置；通用安装器、日志轮转、升级回退与跨平台服务管理尚未交付。
