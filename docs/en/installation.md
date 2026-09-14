# Installation and maintenance

[简体中文](../zh-CN/installation.md) · [Home](../../README.md) · [User guide](user-guide.md)

Applies to **0.0.7**. Follow the steps in order for a new installation. Existing
paired installations can skip to [maintenance](#maintenance). Example paths and
IDs are placeholders; never copy another installation's identity or credentials.

## 1. Prerequisites

| Requirement | Current support |
|---|---|
| Host | Linux x86_64; Ubuntu 24.04 was used for live verification |
| Python | 3.12 with `venv` for the tested offline bundle; project minimum is 3.11 |
| Agent | A running, signed-in AGY 2.13.0 instance with an existing project |
| Lark | A tenant app with bot capability; the paired user must be in its availability scope |
| Network | Outbound access to Lark; download access on the build/browser installation host |
| Services | systemd user service, or an explicitly selected system service |

Install and sign in to AGY through its own process. Unilark neither installs AGY
nor selects a replacement model. Its adapter checks the exact tested AGY bundle
and only discovers a same-user local process matching the configured executable
and `--user-data-dir`. An arbitrary newer AGY version may be rejected.

There is no independent workspace sandbox. AGY decides which tools require approval.

## 2. Build and install

Clone the public source repository, then build and install:

```bash
git clone https://github.com/leonli/unilark.git
cd unilark
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,lark]'
.venv/bin/python scripts/build-release.py --output dist
cd dist
sha256sum -c SHA256SUMS
cd ..
python3 dist/unilark-0.0.7-linux-x86_64/install.py
export PATH="$HOME/.local/bin:$PATH"
unilark --version
```

The build downloads wheels matching the installed runtime dependency versions.
Its output is an offline Python dependency bundle, not a self-contained OS image.
Do not overwrite an existing bundle directory; choose a fresh `--output` directory
for another build. You can transfer the archive and `SHA256SUMS` to a matching host,
verify, extract, and run `install.py` there. The target still needs Python and `venv`.

The installer refuses an existing installation; use `upgrade` for it. Optional
`--prefix /absolute/private/path` and `--bin-dir /absolute/bin/path` select another
layout. The prefix must be owned by you with mode `0700`.

| Default path | Purpose |
|---|---|
| `~/.local/bin/unilark` | Stable command |
| `~/.local/share/unilark/releases/0.0.7/` | Isolated program and Python environment |
| `~/.local/share/unilark/current` | Active version symlink |
| `~/.unilark/agy.toml` | Your non-secret AGY configuration |
| `~/.unilark/lark.env` | Credentials, mode `0600` |
| `~/.unilark/state.db` | Owner, bindings, queues, cards, and recovery state |

## 3. Enable Mermaid images

```bash
~/.local/share/unilark/current/.venv/bin/python -m playwright install chromium --only-shell
```

Run this as the same user as the gateway. New hosts may also need Chromium system
libraries. Offline hosts must have the matching browser cache and OS libraries
prepared in advance; Chromium is not in the offline bundle. Without it, Markdown
still works and diagrams fall back to source text. Lark 7.20+ is required for the
documented rich-card experience.

The renderer uses bundled Mermaid, blocks network requests, and rejects diagram
configuration overrides. Oversized/invalid diagrams or failed uploads retain
their source instead of blocking the rest of the answer.

## 4. Select the AGY instance

From the source checkout:

```bash
install -d -m 700 "$HOME/.unilark"
cp config.example.toml "$HOME/.unilark/agy.toml"
chmod 600 "$HOME/.unilark/agy.toml"
```

Edit the file before running setup:

```toml
[agy]
executable = "/absolute/path/to/Antigravity-x64/antigravity"
user_data = "/absolute/path/to/the/running/agy-profile"
project_id = "your-existing-native-project-id"
model = "your-verified-native-model-enum"
source_label = "Lark · Unilark"
```

`executable` and `user_data` must exist and match exactly one running AGY process
owned by the same OS user. `project_id` is AGY's native project ID, and `model` is
its native model enum, not a display name. Obtain those values from the existing
verified AGY instance/configuration. Automatic discovery of these two values is
not implemented; the example strings are not usable defaults.

```bash
unilark --config "$HOME/.unilark/agy.toml" sidecar check agy
```

Resolve an instance/bundle mismatch in AGY or the configuration. Do not bypass
the bundle check to make a different version appear supported.

## 5. Configure the Lark app

Use [Lark Developer Console](https://open.larksuite.com/app) or
[Feishu Developer Console](https://open.feishu.cn/app). Recorded live acceptance
used international Lark; Feishu has not received the same live verification.

Create a tenant app, enable the bot, and make it available to your own account.
Grant the following permissions and any dependencies required by the console:

| Permission | Used for |
|---|---|
| `im:message:send_as_bot` | Bot replies |
| `im:message:update` | Update progress and interaction cards |
| `im:message.p2p_msg:readonly` | Private messages and pairing |
| `im:message.group_msg:readonly` | Group messages without a repeated @mention |
| `im:chat:create`, `im:chat:read`, `im:chat:update` | Create and configure private session groups |
| `im:chat.members:read` | Verify that only the owner and bot are members |
| `im:resource` or the applicable `im:resource:upload` grant | Upload diagram images |

Choose **long connection / WebSocket** for events and callbacks. Subscribe to:

- `im.message.receive_v1`
- `card.action.trigger`
- `application.bot.menu_v6`

Configure three bot private-chat menu entries using the **send event** action:

| Display label (you may translate it) | Exact event ID | Text fallback |
|---|---|---|
| 新建会话 / New session | `unilark.new` | `/new-form` |
| 打开会话 / Open sessions | `unilark.sessions` | `/list` |
| 设置 / Settings | `unilark.settings` | `/settings` |

The event IDs are literal values: do not add a slash, change their spelling, or
use the label as the ID. Save and publish an app version; complete tenant approval
or installation if required. Permission/menu changes need publication too. Allow
for propagation, then test each menu. A visible menu alone does not prove its
event ID and subscription are correct.

No public inbound HTTP endpoint or CardKit streaming permission is required.
Bot private-chat menus are distinct from a group's chat menu tree.

## 6. Pair your identity

```bash
unilark --config "$HOME/.unilark/agy.toml" setup
```

The interactive prompts are currently Chinese. Enter the edition (`lark` or
`feishu`), App ID, and App Secret; the secret prompt is hidden. Never paste the
secret into a chat or place it in a command argument. Existing credentials and
pairing are preserved when you rerun setup.

The terminal starts a pairing window. If the console requires an active long
connection before saving callbacks, save it during this window. Privately send
the bot the exact `/pair CODE` displayed in the terminal. Back in the terminal,
check the tenant/user/chat and type your complete `open_id` to confirm. The code
expires after about five minutes; rerun pairing if necessary.

Only one consumer may own this app connection. Do not run `pair`, another gateway,
or `doctor --connect-lark` while the gateway service is already running.

Setup can return exit code **2** after pairing because foreground acceptance or
service handoff is still pending. Read `repairs`; do not create a new owner to
clear that status. The report is saved as `~/.unilark/setup-report.json`.

## 7. Verify a first conversation

```bash
unilark --config "$HOME/.unilark/agy.toml" run
```

In the bot's private chat, open **新建会话**, or send `/new-form`. Create a titled
session, enter its group, then send without @mentioning the bot:

```text
Do not use tools. Reply exactly: Connected.
```

Check for a progress card and final answer. Continue on the same native conversation
in AGY and verify the next answer appears in its group. Try all three menus,
`/status`, and a controlled approval/stop scenario. See the [user guide](user-guide.md).

## 8. Hand off to systemd

Stop the foreground gateway with Ctrl-C. This leaves AGY and session history intact.

```bash
unilark --config "$HOME/.unilark/agy.toml" service install
unilark service start
unilark service status
unilark --config "$HOME/.unilark/agy.toml" doctor --check-api
```

These commands use a user service. If the host has no usable user bus, explicitly
select a system service instead:

```bash
unilark --config "$HOME/.unilark/agy.toml" service install --system
unilark service start --system
unilark service status --system
```

System-service changes require administrator access but run the gateway as the
installing user. AGY and its desktop/session must be kept available separately.
User-service survival after logout depends on the host's user-manager settings.

`doctor` exit code 0 means the inspected runtime is healthy; 2 means action is
needed. Setup's stricter **ready** state also requires recorded acceptance and a
matching background service. The rich-card GET API can return compatibility
placeholders rather than Markdown text, so `acceptance verify` may not be able
to establish new rich-text acceptance even when the phone receives the answer.
Keep this distinction visible; do not mark tests passed just to satisfy setup.

## Maintenance

Global options (`--config`, `--state`, `--credentials`) go **before** the subcommand.
Use the same state/credential paths on every invocation. The following examples
use a system service; omit `--system` for user services.

```bash
unilark service restart --system
unilark --config "$HOME/.unilark/agy.toml" doctor --check-api
unilark backup --output "$HOME/.unilark/pre-upgrade.db"
unilark --config "$HOME/.unilark/agy.toml" upgrade /absolute/path/to/new-bundle --system
unilark --config "$HOME/.unilark/agy.toml" rollback --system
```

Backup destinations must be new files in private directories. Upgrades require
native tasks to be idle and no queued/submitting/unknown operations. The updater
stops the sole gateway, rechecks, backs up SQLite consistently, tests a migrated
copy, and switches the program. It never restores old data over newly accepted work.

0.0.7 uses schema **3** and accepts upgrade inputs from schemas 1–3. Version 0.0.5
can read schema 3; 0.0.4 and earlier cannot. Rollback is limited to compatible
programs. Existing 0.0.5 users need no new permissions or pairing for 0.0.7.

```bash
unilark --config "$HOME/.unilark/agy.toml" uninstall --system
```

Uninstall removes the owned program/launcher/service, retaining state, backups,
credentials, AGY, login, and workspace. It is not a data-erasure command.

## Troubleshooting and acceptance evidence

| Symptom | Check or next step |
|---|---|
| Settings menu does nothing, `/settings` works | Exact `unilark.settings` ID, send-event action, menu event subscription, published version |
| No menus yet | Use `/new-form`, `/list`, `/settings`; verify publication/propagation |
| A group is not ready | Required scopes, published tenant grant, member verification; then retry from session details |
| Group messages are ignored | Correct registered group, group-message scope, active connection, published subscription |
| Reply/card is blocked | `doctor` delivery state, send/update permissions, group membership |
| New session stalls; `99991403` or `quota_exhausted` | Monthly API quota is exhausted. Check usage/limit/reset in Lark administration. Version 0.0.7 shares a one-hour API cooldown; after quota restoration the next due request retries. Restarting cannot restore quota. Older UNKNOWN deliveries still require reconciliation. |
| AGY unavailable/incompatible | Executable/profile, same OS user, login, tested bundle; never bypass checks |
| UNKNOWN | Inspect possible native execution before doing anything that could duplicate it |
| Mermaid stays as source | Matching browser/dependencies, image upload scope, valid and reasonably sized diagram |

```bash
unilark recovery list
unilark diagnostics --output "$HOME/.unilark/diagnostics.json"
unilark audit --output "$HOME/.unilark/audit.json"
```

For explicit local recovery, stop the gateway and inspect the native history or
Lark message first. Acknowledging an unknown result accepts uncertainty and keeps
the queue paused; it does not prove that nothing executed:

```bash
unilark recovery acknowledge input REQUEST_ID --acknowledge-possible-execution
unilark retry-delivery CARD_ID
```

`control` and `card` are also recovery entities. `retry-delivery` is for a blocked
update with a known message ID, not for recreating an uncertain new message.
Keep diagnostics private; exported reports omit bodies/secrets, but `history`
explicitly prints conversation content.

After real phone/desktop checks, the evidence commands are:

```bash
unilark --config "$HOME/.unilark/agy.toml" acceptance verify text_roundtrip --binding BINDING_ID --confirm-native-view
unilark --config "$HOME/.unilark/agy.toml" acceptance verify desktop_ui_relay --binding BINDING_ID --confirm-native-view --confirm-desktop-ui
unilark --config "$HOME/.unilark/agy.toml" acceptance verify lark_permission_and_stop --binding BINDING_ID --confirm-native-view --confirm-tool-effect
```

These flags confirm actual observations, not permission to simulate a pass. See
the rich-card GET limitation above and the [test evidence](../E2E-COVERAGE.md).
