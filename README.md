# Unilark

Lark/飞书与 AGY 桌面共享会话的本地网关。**M1 开发版；本机真实 Lark ↔ AGY 最小闭环已验收。**
AGY 保留登录与原生历史；网关保存身份、绑定、输入队列、控制意图和卡片投递状态。

## 已实现并验证

- AGY 2.13.0 Linux：精确实例定位、私有 gRPC-web/JSON、bundle SHA-256 拒写门禁。
- 原生 `userIdentity` 显示 Lark 来源，`tags` 关联输入；正文不加前缀。
- SQLite WAL 队列、固定路由、输入去重、重启后 UNKNOWN 核对；UNKNOWN 不自动重发。
- 官方 `lark-channel-sdk==1.4.0`：长连接、完整身份核验、后台回调转主线程、消息与按钮。
- `/new /attach /sessions /switch /status /stop /continue /steer /cancel /whoami /help`。
- 回答分卡、脱敏、权限/停止按钮、卡片更新与有限退避；不展示原始 thinking。
- 本机 5 分钟配对窗口，确认候选身份后才写 owner；仅知道配对码不能执行任务。
- `pair / run / doctor / attach /sessions / history` CLI 和应用配置向导。

截至 2026-09-12：53 项本地测试及 5 项真实 AGY 测试通过。真实 Lark 已完成本人配对、
两轮普通消息、允许审批、运行中停止、待审批时重启恢复；用户已确认普通消息可用。
实际通过 AGY 桌面输入框发送的输入和回答也已从 Lark API 回读验证，无重复输入或回灌。
这些是指定实例的实测记录；完整故障矩阵、干净环境安装和公开发行仍未完成。

## 配置和启动

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,lark]'
cp config.example.toml /private/path/unilark.toml
# 填好实例字段；目标 AGY 必须已登录且已有项目。
./scripts/setup-lark.sh /private/path/unilark.toml
.venv/bin/unilark --config /private/path/unilark.toml run
```

向导依次处理区域、机器人凭据、权限、长连接/发布和身份配对。
详见 [LARK-SETUP.md](docs/LARK-SETUP.md)。App Secret 在本机隐藏输入，保存到
`~/.unilark/lark.env`（0600）。凭据文件作为数据读取，绝不 `source` 或求值。

`run` 在前台运行，Ctrl-C 退出；不停止外部 AGY，不清理登录或历史。
默认账本为 `~/.unilark/state.db`，支持 `--state`、`--credentials` 指定其他路径。
本机已配置且由 `unilark.service` 托管，不必重跑向导或另启消费者；管理方式见
[OPERATIONS.md](docs/OPERATIONS.md)。该服务只管理网关，AGY 仍需独立运行并登录。
本机 Spike 配置位于仓库外 `../spike/runtime/unilark.toml`，宿主生命周期见
`../spike/M0-RESULT.md`，本轮结论见 [M1-PROGRESS.md](docs/M1-PROGRESS.md)。

```bash
.venv/bin/unilark --config /private/path/unilark.toml doctor
# 网关未运行时，独占探测 Lark 长连接；不发送消息：
.venv/bin/unilark --config /private/path/unilark.toml doctor --connect-lark
```

doctor 分开报告实时 `service` 健康与历史 `acceptance` 验收记录。
进程身份和心跳有效、Lark 已连接、AGY 会话可观测且无待确认操作或阻塞投递时返回 **0**，
否则返回 **2**。服务停机不会抹掉历史验收；健康状态也不会自动新增验收记录。
`lark.permissions` 尚不自动查询全部控制台权限，实测能力看 `acceptance`。

## 队列与恢复

普通输入保存后等待已绑定会话空闲；`/steer 内容` 显式插入当前执行。
`/stop` 立即暂停本地队列，再请求停止原生会话；确认空闲后仍需 `/continue` 才继续。
引用卡片投向原会话；切换当前会话不改变已接收输入的目标。

崩溃时已领取的输入/控制记为 UNKNOWN，只核对状态，不盲目重发。tag 能证明接收，
不能证明原生幂等或输入从未执行。重复 tag、丢失新建确认且历史为空等情形需要人工核对。
新建卡片的网络结果不明确时保留 UNKNOWN；已有 message ID 的更新可以重试。

权限卡只支持实测的 `run_command` permission，仅本次允许或拒绝。身份、聊天、
原卡片、原会话和请求指纹全部绑定；默认 5 分钟到期后记录拒绝意图，再核验并提交。
断线时不能保证恰好到时送达；恢复后核对同一指纹，不推断执行成功。

## 当前边界

- 一台 Linux 机器、一个已配对 owner、私聊文本、一个选定 AGY 实例。
- 约 2 秒轮询快照及历史，无完整事件 replay 承诺；多会话或超时会增加延迟。
- 调度覆盖已绑定会话；无法锁住桌面同时操作，也无法发现所有未加载会话。
- 工具风险判断依赖 AGY 自身审批设置；不是独立的文件/工作区安全沙箱。
- 群聊、附件、结构化问答、归档/工作目录切换、通用服务安装器和发行包尚未交付。
- 不自动替换模型或创建替代会话；bundle 变化必须重新核验。
- 未创建远程仓库，许可证与发布可见性未定。

## 开发检查

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest -q -m 'not real_agy and not real_lark'
UNILARK_REAL_AGY_CONFIG=/private/path/unilark.toml \
  .venv/bin/pytest -q -s -m real_agy
bash -n scripts/setup-lark.sh
```

真实用例创建独立会话，测试文本、临时文件审批和 `sleep 30` 停止；不连接 Lark。
默认 CI 安装 SDK 以运行真实归一化/线程契约测试；离线全绿不替代真实双端验收。
依赖与密钥扫描门禁见 `.github/workflows/ci.yml`。
