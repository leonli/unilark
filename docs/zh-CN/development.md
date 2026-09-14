# 开发与验证

[English](../en/development.md) · [首页](../../README.zh-CN.md) · [架构说明](architecture.md)

## 代码结构

| 路径 | 职责 |
|---|---|
| `src/unilark/conversation/` | Hub、面板、私有群、问答路由、本轮消息投影 |
| `src/unilark/adapters/lark/` | SDK 身份边界、群接口、富媒体投递 |
| `src/unilark/adapters/sidecars/agy/` | 已验证原生协议、状态视图、项目查找 |
| `src/unilark/store/` | SQLite 账本、观测、面板、群、inbox/outbox |
| `src/unilark/projection/` | 卡片、Markdown 拆分、本机 Mermaid 渲染 |
| `src/unilark/onboarding/` | 凭据、配对、setup、验收记录 |
| `src/unilark/lifecycle/` | 进程锁、健康、systemd、升级、备份和恢复 |
| `tests/` | 本地回归测试 |
| `tests/e2e/` | 显式启用的真实实例、平台、浏览器测试 |
| `scripts/` | 离线构建、依赖清单、文档检查 |
| `docs/en/`、`docs/zh-CN/`、`docs/assets/` | 双语手册和示意图 |

## 本地检查

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,lark]'
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python scripts/check-docs.py
.venv/bin/pytest -q -m 'not real_agy and not real_lark and not local_browser'
.venv/bin/python -m pip check
```

默认测试不会创建真实原生任务或发送 Lark 消息。CI 检查 Python 3.11/3.12、格式、类型、文档、
依赖漏洞和完整 Git 历史中的密钥。删除工作树文件不会移除其历史版本，所以不能只扫描当前文件。

## 显式启用真实验证

使用专用测试会话和明确选择的应用/实例；后台已有网关时不要再为同一应用开启第二个 WebSocket 消费者。

```bash
UNILARK_LOCAL_BROWSER=1 .venv/bin/pytest -q -m local_browser
UNILARK_REAL_AGY_CONFIG=/private/path/agy.toml .venv/bin/pytest -q -m real_agy
UNILARK_REAL_LARK_CREDENTIALS=/private/path/lark.env \
UNILARK_REAL_LARK_STATE=/private/path/state.db \
.venv/bin/pytest -q tests/e2e/test_real_lark_cards.py
```

原生测试会创建受控会话，可能发起或处理审批及停止工作。
Lark 测试发送并撤回自己的临时卡片，不模拟真人点击。
群 API 测试另需 `UNILARK_REAL_LARK_ROOM_RECORD`，指向包含专用验证群 `chat`、
建群标记 `request_id` 的私有 JSON 文件，不能指向任意群。

验证报告必须说明经过的实际边界：真实 AGY 加本地 Recorder 不是 Lark 入站旅程；
设置菜单真实接口测试使用合成事件和真实 HTTP 投递，没有点击后台菜单。
JSON 2.0 GET 可能返回兼容占位内容，只能断言接口实际给出的信息。
历史证据见 [E2E-COVERAGE.md](../E2E-COVERAGE.md)。

## 修改与 PR

传输对象不要进入会话逻辑。外部写入前持久化操作，固定目标，未知结果不自动重发。
决策前重新核对原生交互指纹。目录选择只是项目选择，不是安全隔离。

在能复现用户现象的接口处修复 bug，验证可见结果、去重或控制目标，不镜像内部实现。
没有新的真实实例验证，不修改 AGY bundle 白名单。

行为改变时同步更新两种语言手册，示意图明确标注。
`scripts/check-docs.py` 检查 Markdown 本地链接/图片、UTF-8 替换字符、SVG 格式和 U01–U25 双语锚点。
它不验证外部网址可达性或手机视觉。

PR 描述写明触发条件、改后行为、验证及限制。
不要提交凭据、SQLite 文件、用户消息导出、带账号信息的截图或原始鉴权记录。

## 构建离线包

```bash
.venv/bin/python scripts/build-release.py --output dist
```

构建环境要求 Linux x86_64，产物包含精确运行依赖 wheels、manifest、校验和、SBOM、双语文档及声明。
版本化产物不可覆盖，再次构建选择新输出目录。在隔离 prefix 中验证安装，不覆盖运行中的网关。

代码上传 GitHub 不会自动发布二进制版本、部署服务或授予开源许可。许可状态见 [NOTICE.md](../../NOTICE.md)。
