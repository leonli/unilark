"""Rich replies retain readable structure, original routing, and fail-safe diagram source."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from markdown_it import MarkdownIt

from test_gateway import OWNER, Channel, Runtime, new, send
from unilark.adapters.lark.rich_media import RichMedia
from unilark.adapters.sidecars.views import SessionView, StepView
from unilark.conversation.hub import Hub
from unilark.policy.redact import Redactor
from unilark.projection.mermaid import render_png
from unilark.projection.rich_text import LIMIT, blocks, diagram_source, reply_cards
from unilark.store.gateway import GatewayStore

SAMPLE = """# 架构说明

**重点**：保留 `代码`、[链接](https://example.com) 和中文。

> 说明引用

- 第一步
- 第二步

| 功能 | 状态 |
| --- | --- |
| Markdown | 支持 |

```python
print("hello")
```

```mermaid
flowchart LR
    A[手机] --> B[网关]
    B --> C[本地 Agent]
```
"""


def test_markdown_structure_and_complete_diagram_survive_projection():
    payloads = reply_cards("回复", SAMPLE, "会话 A · 项目")
    assert all(p["schema"] == "2.0" for p in payloads)
    content = [e for p in payloads for e in p["body"]["elements"]]
    rendered = "\n\n".join(e.get("content", "") for e in content)
    for feature in (
        "# 架构说明",
        "**重点**",
        "`代码`",
        "> 说明引用",
        "- 第一步",
        "| 功能 | 状态 |",
        '```python\nprint("hello")\n```',
    ):
        assert feature in rendered
    assert [diagram_source(e) for e in content if diagram_source(e)] == [
        "flowchart LR\n    A[手机] --> B[网关]\n    B --> C[本地 Agent]\n"
    ]


def test_long_code_and_table_split_at_structure_boundaries_without_losing_rows():
    code = "".join(f'print("中文{i}")\n' for i in range(600))
    table = "| 名称 | 值 |\n| --- | --- |\n" + "".join(
        f"| row{i} | 中文数据{i} |\n" for i in range(600)
    )
    elements = blocks("```python\n" + code + "```\n\n" + table)
    parser = MarkdownIt("commonmark").enable("table")
    rebuilt = ""
    rows = []
    for e in elements:
        assert len(e["content"]) <= LIMIT
        tokens = parser.parse(e["content"])
        if tokens[0].type == "fence":
            assert tokens[0].info == "python" and e["content"].endswith("```")
            rebuilt += tokens[0].content
        else:
            assert tokens[0].type == "table_open"
            assert e["content"].startswith("| 名称 | 值 |\n| --- | --- |\n")
            rows.extend(e["content"].splitlines()[2:])
    assert rebuilt == code
    assert rows == table.splitlines()[2:]


@pytest.mark.parametrize("opening,closing", [("```", "```"), ("~~~~", "~~~~~")])
def test_streaming_mermaid_waits_for_matching_closing_fence(opening, closing):
    partial = opening + "mermaid\nflowchart TD\nA-->B\n"
    assert not any(diagram_source(e) for e in blocks(partial))
    assert any(diagram_source(e) for e in blocks(partial + closing))
    assert not any(diagram_source(e) for e in blocks(partial + closing[0] * 2))


async def test_diagram_cache_and_failure_keep_source_without_mutating_saved_payload(monkeypatch):
    renderer = AsyncMock(return_value=b"png")
    monkeypatch.setattr("unilark.adapters.lark.rich_media.render_png", renderer)
    upload = AsyncMock(return_value="img_fixture")
    media = RichMedia(upload)
    original = reply_cards("回复", SAMPLE, "会话 A")[0]
    stored = json.dumps(original)
    for _ in range(2):
        prepared = await media.prepare(original)
        images = [e for e in prepared["body"]["elements"] if e["tag"] == "img"]
        assert images[0]["img_key"] == "img_fixture" and images[0]["preview"]
        assert not any(diagram_source(e) for e in prepared["body"]["elements"])
    upload.assert_awaited_once()
    renderer.assert_awaited_once()
    assert json.dumps(original) == stored
    for failure in (ValueError("private syntax details"), TimeoutError("private URL")):
        renderer.side_effect = failure
        failed = await RichMedia(upload).prepare(original)
        raw = json.dumps(failed, ensure_ascii=False)
        assert "图表暂未生成" in raw and "flowchart LR" in raw
        assert "private" not in raw
        assert not any(diagram_source(e) for e in failed["body"]["elements"])
    renderer.side_effect = None
    denied = AsyncMock(side_effect=RuntimeError("permission denied"))
    unavailable = RichMedia(denied)
    failed = await unavailable.prepare(original)
    await unavailable.prepare(original)
    denied.assert_awaited_once()  # Streaming text updates must not hammer a denied upload.
    assert "图表暂未生成" in json.dumps(failed, ensure_ascii=False)


async def test_hub_redacts_before_diagram_projection_and_reuses_card_and_quote_target(tmp_path):
    store = GatewayStore(tmp_path / "rich.db")
    store.set_owner(OWNER)
    runtime, channel = Runtime(), Channel()
    secret = "fixture-app-secret"  # noqa: S105 - synthetic redaction fixture
    hub = Hub(store, runtime, channel, OWNER, "profile", Redactor((secret,)))
    try:
        first = await new(hub, store)
        runtime.views[first["native_id"]] = SessionView(
            True, "idle", "1", [StepView(0, "assistant", "done", SAMPLE.replace("网关", secret))]
        )
        await hub.tick()
        row = dict(
            store.db.execute(
                "SELECT * FROM cards WHERE id=?", (store.card_id(OWNER, f"step:{first['id']}:0:0"),)
            ).fetchone()
        )
        assert secret not in row["payload"]
        assert "REDACTED" in row["payload"]
        assert json.loads(row["payload"])["schema"] == "2.0"
        runtime.views[first["native_id"]] = SessionView(
            True, "idle", "2", [StepView(0, "assistant", "done", "# 更新\n\n" + SAMPLE)]
        )
        await hub.tick()
        after = dict(store.db.execute("SELECT * FROM cards WHERE id=?", (row["id"],)).fetchone())
        assert after["message_id"] == row["message_id"]
        await new(hub, store)
        await send(hub, "引用继续", reply_to=after["message_id"])
        assert store.operations(OWNER)[0]["binding_id"] == first["id"]
    finally:
        store.close()


async def test_mermaid_rejects_configuration_overrides_before_browser_launch():
    for source in (
        '%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA-->B',
        "---\nconfig: {}\n---\nflowchart LR\nA-->B",
        "x" * 6001,
    ):
        with pytest.raises(ValueError):
            await render_png(source)
