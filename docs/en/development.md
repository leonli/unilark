# Development and validation

[简体中文](../zh-CN/development.md) · [Home](../../README.md) · [Architecture](architecture.md)

## Repository layout

| Path | Responsibility |
|---|---|
| `src/unilark/conversation/` | Hub, panels, private rooms, question routing, quiet turn projection |
| `src/unilark/adapters/lark/` | Authenticated SDK boundary, room API, rich-media delivery |
| `src/unilark/adapters/sidecars/agy/` | Verified native protocol, views, project lookup |
| `src/unilark/store/` | SQLite ledger, journal, panels, rooms, inbox/outbox |
| `src/unilark/projection/` | Card payloads, Markdown splitting, local Mermaid rendering |
| `src/unilark/onboarding/` | Credential storage, pairing, setup, acceptance evidence |
| `src/unilark/lifecycle/` | Process lock, health, systemd, upgrades, backups, recovery |
| `tests/` | Local regression tests |
| `tests/e2e/` | Explicit real-runtime/platform/browser checks |
| `scripts/` | Bundle build, dependency inventory, documentation checks |
| `docs/en/`, `docs/zh-CN/`, `docs/assets/` | Paired guides and illustrations |

## Local checks

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

The default suite has no permission to create native tasks or send real Lark messages.
CI checks Python 3.11/3.12, lint/types/docs, dependency vulnerabilities, and Git history
for secrets. All-history secret scanning matters because deleting a file does not
delete its previous versions.

## Opt-in validation

Only use dedicated test sessions and an explicitly selected app/runtime. Do not
open another WebSocket consumer for an app already served by the gateway.

```bash
UNILARK_LOCAL_BROWSER=1 .venv/bin/pytest -q -m local_browser
UNILARK_REAL_AGY_CONFIG=/private/path/agy.toml .venv/bin/pytest -q -m real_agy
UNILARK_REAL_LARK_CREDENTIALS=/private/path/lark.env \
UNILARK_REAL_LARK_STATE=/private/path/state.db \
.venv/bin/pytest -q tests/e2e/test_real_lark_cards.py
```

Native tests create controlled conversations and can request/resolve tool approval
and stop work. Lark tests send and recall only their own temporary cards; no user
click is simulated. Group API tests additionally require
`UNILARK_REAL_LARK_ROOM_RECORD`, a private JSON file containing the verified probe
group's `chat` and provisioning `request_id` marker. Do not point it at arbitrary groups.

Describe exactly which boundary a test exercised. A real AGY test with a local
Recorder is not a real Lark input journey. The real Settings-menu test uses a
synthetic event plus actual HTTP delivery, not a console menu click. JSON 2.0 GET
may return a compatibility placeholder, so assert only what the API exposes.
Historical evidence is in [E2E-COVERAGE.md](../E2E-COVERAGE.md).

## Changes and pull requests

Keep transport objects out of conversation logic. Persist an operation before its
external write, preserve its fixed target, and never retry a write whose result is
unknown. Revalidate native interaction fingerprints before deciding. A workspace
path is a project choice, not a security boundary.

Fix a bug at a seam that reproduces the user's behavior. Test the visible outcome,
deduplication, or control target instead of private implementation details. Do not
change the AGY bundle allowlist without new real-instance verification.

Update both language guides when behavior changes, and label illustrations as
illustrations. `scripts/check-docs.py` checks local Markdown links/images, UTF-8
replacement characters, SVG validity, and matching U01–U25 user-guide anchors.
It does not claim external URLs or mobile visual acceptance have been verified.

For a PR, describe the trigger, resulting behavior, validation, and any remaining
limit. Never commit credentials, SQLite files, user message exports, screenshots
containing account details, or raw authenticated traces.

## Build a bundle

```bash
.venv/bin/python scripts/build-release.py --output dist
```

Builds require Linux x86_64. The output includes exact runtime wheels, a manifest,
checksums, dependency SBOM, bilingual docs, and notices. Versioned outputs are
immutable: choose a new destination for subsequent builds. Test installation in
an isolated prefix, not by overwriting a working gateway.

Publishing source to GitHub does not automatically publish a binary release,
deploy the service, or grant an open-source license. See [NOTICE.md](../../NOTICE.md).
