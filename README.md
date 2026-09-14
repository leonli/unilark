# Unilark

**Continue your local AGY conversations from Lark.** One private chat group per
session, one live progress card per turn, and a clean final answer when the work ends.

[简体中文](README.zh-CN.md) · **English** · [Installation](docs/en/installation.md) · [User guide](docs/en/user-guide.md)

![Unilark architecture: Lark, the local gateway, and the existing AGY runtime](docs/assets/architecture.svg)

Unilark runs beside an existing, signed-in AGY desktop instance. Your desktop and
Lark share the same native conversations; the gateway stores routing, queues,
approvals, and message delivery state in SQLite. It does not replace AGY's login or model.

## What you can do

- Create a session from the bot's **新建会话 / New session** menu. Give it a title,
  choose a known project, and optionally submit its first task.
- Continue each task in its own owner-and-bot Lark group. Switch groups using Lark's
  chat list, search, or pins; no repeated session switching commands.
- Watch thinking, command previews, file work, and queue counts update one card.
  Final answers arrive as new messages, without operational headers or receipts.
- Read Markdown tables, code blocks, and Mermaid diagrams rendered locally as images.
- Approve or reject individual tool requests, answer questions, stop a turn, and
  inspect or cancel queued work.
- Resume after gateway restarts using persisted bindings, input reconciliation,
  and an outbox that retains known message IDs.

![Illustrated workflow: create a session, see one progress card, receive the answer](docs/assets/chat-flow.svg)

*Illustrations, not client screenshots. The current bot UI is primarily Chinese;
the English guide explains the visible labels.*

## Start here

Version **0.0.7**, experimental. Verified runtime: **Linux x86_64, Python 3.12,
AGY 2.13.0, and international Lark**. The adapter checks a specific AGY bundle;
arbitrary AGY versions are not supported. Python 3.11 is also a CI target.

Build an offline install bundle from a checkout:

```bash
git clone https://github.com/leonli/unilark.git
cd unilark
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,lark]'
.venv/bin/python scripts/build-release.py --output dist
cd dist
sha256sum -c SHA256SUMS
python3 unilark-0.0.7-linux-x86_64/install.py
```

Then prepare the AGY configuration, install the diagram browser, configure the Lark
app, pair your identity, and start the gateway. Follow the
[installation guide](docs/en/installation.md) in order. The source repository is
public. There is no assumed PyPI package or pre-published GitHub release to download.

Already configured? Open the bot's **新建会话** menu, enter the new group, and send:

```text
Do not use tools. Reply exactly: Connected.
```

## Documentation

| Topic | English | 简体中文 |
|---|---|---|
| Install, configure, pair, and maintain | [Installation](docs/en/installation.md) | [安装手册](docs/zh-CN/installation.md) |
| Daily conversations, queues, approvals, and recovery | [User guide](docs/en/user-guide.md) | [用户手册](docs/zh-CN/user-guide.md) |
| Components, data ownership, and delivery flow | [Architecture](docs/en/architecture.md) | [架构说明](docs/zh-CN/architecture.md) |
| Development and validation | [Development](docs/en/development.md) | [开发与验证](docs/zh-CN/development.md) |

Historical release notes and test evidence are indexed in [docs/README.md](docs/README.md).
[NOTICE.md](NOTICE.md) records the licensing status and third-party notices.

## Current limits

One paired owner and one configured AGY instance. Multiple sessions can be managed,
but **only one task is submitted for execution at a time**. Native slash-command
autocomplete, multi-user collaboration, incoming files/images, and automatic local
file attachments are not implemented. Markdown content and Mermaid images are supported.

The host and AGY must stay running. Workspace selection is not a sandbox; AGY
controls tool authorization. Uncertain input delivery is reconciled, not blindly
retried. A snapshot does not prove complete event replay. Feishu configuration is
available, but the recorded live acceptance used international Lark.

## Development

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python scripts/check-docs.py
.venv/bin/pytest -q -m 'not real_agy and not real_lark and not local_browser'
```

CI runs local tests and static/dependency/secret checks. Real AGY, real Lark, and
browser tests are separate opt-ins; a green CI run is not a claim of mobile end-to-end acceptance.
