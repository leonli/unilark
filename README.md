# Unilark 0.0.2

Lark/飞书与 AGY 桌面共享会话的本地网关。实验版，已在指定 Linux 实例完成真实收发、审批、停止、桌面接力和故障恢复验证。AGY 保留原生登录与历史，网关持久化身份、绑定、队列和卡片投递。

## 当前能力

- 私聊文本、固定会话引用、列表/切换、归档/恢复、新会话工作目录。
- 忙时排队、显式 steer、停止后保持暂停、审批与结构化问答。
- SQLite WAL 输入/控制/投递账本、快照对账、断线补投递、UNKNOWN 人工核对与审计。
- 可续做 setup、真实消息验收、分项 doctor、日志轮转、备份与脱敏诊断。
- 离线依赖包、隔离安装、用户级或显式系统级服务、升级/兼容回退/保留数据卸载。

已配置本机无需重新提供凭据或配对，操作见 [运行维护](docs/OPERATIONS.md)。
完整证据及剩余验收边界见 [0.0.2 验证报告](docs/RELEASE-0.0.2.md)。
日常操作见 [用户使用手册](docs/USER-GUIDE.md)；逐项验证范围与后续开发顺序见
[手册与 E2E 对照](docs/E2E-COVERAGE.md)。

## 首次安装

离线包要求 Linux x86_64、Python 3.12（含 venv）；不会修改全局 Python 环境。
包内包含 exact-version wheels、SHA-256 manifest、依赖清单和 CycloneDX SBOM。

```bash
sha256sum -c SHA256SUMS
tar -xzf unilark-0.0.2-linux-x86_64.tar.gz
python3 unilark-0.0.2-linux-x86_64/install.py
~/.local/bin/unilark --config /private/path/agy.toml setup
```

AGY 通过其官方流程独立安装并登录；配置参考 `config.example.toml`。
[安装指南](docs/install.md) 包含前台验收、后台交接、升级/回退和卸载步骤；
[Lark 配置](docs/LARK-SETUP.md) 说明权限、事件、配对与真实消息验收。

## 手机命令

```text
/new 标题        /attach 原生UUID     /sessions
/switch 会话ID   /status              /capabilities
/stop           /continue            /steer 补充要求
/cancel 请求ID   /archive             /resume 会话ID
/cwd 绝对目录    /help
```

普通文本保存为下一项任务；引用卡片始终发往卡片原会话。`/cwd` 只影响后续新会话。
`/stop` 先暂停本地队列，再请求停止 AGY；确认空闲后仍需 `/continue`。
单选问题可点选，多问题或自由文本引用问题卡片作答，按提示每题一行。

结果未知时不会自动重发输入或重新创建卡片。tag 只能证明原生接收，不能证明原生幂等。
本机 `recovery list` 用于核对；不要删除数据库消除 UNKNOWN。

## 验证和边界

已验证组合：Ubuntu 24.04 / x86_64 / Python 3.12 / AGY 2.13.0 / Lark SDK 1.4.0 国际版。
详见 [兼容矩阵](docs/compatibility.md) 和 [安全模型](docs/security.md)。

- 一个 owner、私聊文本、一个指定 AGY 实例；群聊、附件、其他引擎属于后续范围。
- 快照对账不承诺完整事件重放，也无法锁住桌面或所有未加载会话。
- 工具授权由 AGY 决定；工作目录选择不是独立沙箱。原始 thinking 不投影到 Lark。
- AGY bundle 不匹配时拒绝写入，不自动换模型或创建替代会话。
- 主机离线/睡眠不能即时执行或通知；系统重启、其他 OS 和首次外部用户验收尚未完成。
- 目前仅本地发行产物；许可证、公开仓库和外部 Beta 尚未决定。

## 开发验证

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,lark]'
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest -q -m 'not real_agy and not real_lark'
UNILARK_REAL_AGY_CONFIG=/private/path/agy.toml .venv/bin/pytest -q -m real_agy
.venv/bin/python scripts/build-release.py --output dist
```

真机测试创建独立会话；普通 CI 不替代真实 AGY/Lark 验收。CI 配置在 `.github/workflows/ci.yml`，本地仓库尚未接远程 CI。
