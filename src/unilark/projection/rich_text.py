"""Markdown blocks to bounded JSON 2.0 cards; Mermaid resolved only at transport.

Callers must redact the complete input before parsing. Never cut a secret in half.
"""

from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt

LIMIT = 3500
MAX_DIAGRAM = 6000
_PARSER = MarkdownIt("commonmark").enable("table")


def split_lines(text: str, limit: int) -> list[str]:
    """Prefer line boundaries, but bound even a single generated line."""
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            pieces.append(current)
            current = ""
        while len(line) > limit:
            pieces.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        pieces.append(current)
    return pieces or [""]


def fenced(source: str, language: str = "") -> list[str]:
    # Pick a fence that cannot be closed by source text, including fallback diagrams.
    fence = "`" * max(3, 1 + max((len(m[0]) for m in re.finditer(r"`+", source)), default=0))
    language = re.sub(r"[^a-zA-Z0-9_+.-]", "", language)[:30]
    overhead = 2 * len(fence) + len(language) + 3
    if overhead > LIMIT // 2:
        fence, overhead = "~~~", 40
    return [
        f"{fence}{language}\n{part}" + ("" if part.endswith("\n") else "\n") + fence
        for part in split_lines(source, LIMIT - overhead)
    ]


def markdown(content: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": content or "…"}


def diagram_source(element: dict[str, Any]) -> str | None:
    if element.get("tag") != "markdown":
        return None
    match = re.fullmatch(r"(`{3,})mermaid\n([\s\S]*)\n\1", element.get("content", ""))
    return match[2] if match else None


def blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    result: list[dict[str, Any]] = []
    end = 0
    for token in _PARSER.parse(text):
        if token.level != 0 or not token.map or token.map[0] < end:
            continue
        start, end = token.map
        raw = "".join(lines[start:end])
        if token.type == "fence":
            language = token.info.strip().split()[0] if token.info.strip() else ""
            closed = (
                bool(
                    re.fullmatch(
                        r" {0,3}"
                        + re.escape(token.markup[0])
                        + "{"
                        + str(len(token.markup))
                        + r",}[ \t]*\r?\n?",
                        lines[end - 1],
                    )
                )
                and end - start > 1
            )
            if language.lower() == "mermaid" and closed and len(token.content) <= MAX_DIAGRAM:
                fence = "`" * max(
                    3, 1 + max((len(m[0]) for m in re.finditer(r"`+", token.content)), default=0)
                )
                result.append(markdown(fence + "mermaid\n" + token.content + "\n" + fence))
            else:
                if language.lower() == "mermaid":
                    result.append(
                        markdown(
                            "图表生成中，完成后显示图片。"
                            if not closed
                            else "图表较大，已保留 Mermaid 源码。"
                        )
                    )
                    language = "text"
                result.extend(markdown(p) for p in fenced(token.content, language))
        elif token.type == "table_open" and len(raw) > LIMIT:
            rows = raw.splitlines(keepends=True)
            header = "".join(rows[:2])
            if len(header) < LIMIT // 2 and all(len(r) < LIMIT // 2 for r in rows[2:]):
                result.extend(
                    markdown(header + p)
                    for p in split_lines("".join(rows[2:]), LIMIT - len(header))
                )
            else:
                # An oversized individual row cannot form a valid bounded table.
                result.extend(markdown(p) for p in fenced(raw, "text"))
        else:
            result.extend(markdown(p) for p in split_lines(raw, LIMIT))
    return result or [markdown("…")]


def reply_cards(
    title: str, text: str, label: str, *, minimal: bool = False
) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    group: list[dict[str, Any]] = []
    size = 0
    for element in blocks(text):
        cost = len(element.get("content", ""))
        # Each block has its own Markdown component (at most one table per component).
        if group and (size + cost > LIMIT or len(group) >= 24):
            groups.append(group)
            group, size = [], 0
        group.append(element)
        size += cost
    if group:
        groups.append(group)
    cards: list[dict[str, Any]] = [
        {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": (title + (f" · {i + 1}/{len(groups)}" if len(groups) > 1 else ""))[
                        :80
                    ],
                },
            },
            "body": {
                "elements": [
                    *group,
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "plain_text",
                            "content": label,
                            "text_size": "notation",
                        },
                    },
                ]
            },
        }
        for i, group in enumerate(groups)
    ]
    if minimal:
        for payload, group in zip(cards, groups, strict=True):
            payload.pop("header")
            payload["body"]["elements"] = group
    return cards
