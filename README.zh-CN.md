# Unilark

**在 Lark 里继续本机 AGY 会话。** 一个会话一个私有群，每轮一张动态进度卡，完成后正常收到答复。

**简体中文** · [English](README.md) · [安装手册](docs/zh-CN/installation.md) · [用户手册](docs/zh-CN/user-guide.md)

![Unilark 架构：Lark、本机网关和原有 AGY 实例](docs/assets/architecture.svg)

Unilark 与已登录的 AGY 桌面实例运行在同一台主机。桌面与 Lark 共用原生会话；
网关用 SQLite 保存路由、队列、审批和消息投递状态，沿用 AGY 的登录与模型。

## 可以做什么

- 从机器人「新建会话」菜单填写标题、选择已有项目，并可附带首条任务。
- 每个任务在只有你和机器人的独立群里继续。通过 Lark 聊天列表、搜索和置顶切换。
- 思考、命令预览、文件操作和排队数量只更新一张进度卡，最终答复另发新消息。
- 阅读 Markdown 表格、代码块，以及在本机渲染后上传的 Mermaid 图。
- 单次批准或拒绝工具请求、回答问题、停止当前轮、查看或撤销排队任务。
- 网关重启后保留绑定，核对输入是否已接收，并继续投递已保存的输出。

![操作示意：新建会话、原位更新进度、收到最终答复](docs/assets/chat-flow.svg)

*以上为操作示意图，不是真实客户端截图。当前机器人界面以中文为主，英文手册提供对应标签说明。*

## 开始使用

当前为 **0.0.6 实验版**。实际运行验证组合：**Linux x86_64、Python 3.12、AGY 2.13.0、国际版 Lark**。
适配器会校验指定 AGY bundle，不支持任意 AGY 版本。Python 3.11 另作为 CI 目标。

从源码构建离线安装包：

```bash
git clone https://github.com/leonli/unilark.git
cd unilark
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,lark]'
.venv/bin/python scripts/build-release.py --output dist
cd dist
sha256sum -c SHA256SUMS
python3 unilark-0.0.6-linux-x86_64/install.py
```

随后准备 AGY 配置、安装图表浏览器、配置 Lark 应用、配对本人身份并启动网关，
请按[安装手册](docs/zh-CN/installation.md)顺序操作。源码仓库已公开；
此流程不依赖尚未发布的 PyPI 包或 GitHub Release。

已经配置好？点机器人「新建会话」，进入新群发送：

```text
不要调用工具，只回复：连接成功。
```

## 文档

| 内容 | 简体中文 | English |
|---|---|---|
| 安装、配置、配对、维护 | [安装手册](docs/zh-CN/installation.md) | [Installation](docs/en/installation.md) |
| 日常对话、队列、审批、恢复 | [用户手册](docs/zh-CN/user-guide.md) | [User guide](docs/en/user-guide.md) |
| 组件、数据归属、消息流 | [架构说明](docs/zh-CN/architecture.md) | [Architecture](docs/en/architecture.md) |
| 代码结构、测试、贡献 | [开发与验证](docs/zh-CN/development.md) | [Development](docs/en/development.md) |

历史发行说明和验证记录见[文档索引](docs/README.md)。许可状态及第三方声明见 [NOTICE.md](NOTICE.md)。

## 当前边界

一个已配对 owner、一个指定 AGY 实例。可管理多个会话，但**同时执行上限仍为 1**。
输入框原生 `/` 自动补全、多人协作、图片/文件输入、自动上传本机文件附件尚未实现。
支持直接展示 Markdown 正文和 Mermaid 图。

主机和 AGY 需要保持运行。项目目录不是独立沙箱，工具授权由 AGY 控制。
未知输入先核对、不盲目重发；快照也不能证明完整事件重放。
配置支持选择飞书，但目前实际验收使用国际版 Lark。

## 开发检查

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python scripts/check-docs.py
.venv/bin/pytest -q -m 'not real_agy and not real_lark and not local_browser'
```

CI 运行本地测试以及静态、依赖和密钥检查。真实 AGY、Lark 与浏览器测试需单独显式启用；
CI 绿灯不表示真实手机操作已全部验收。
