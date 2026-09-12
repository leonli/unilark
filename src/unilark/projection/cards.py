"""Small Lark cards; fixed action tokens, sanitized content, complete chunking."""

from __future__ import annotations

from typing import Any


def card(
    title: str, text: str, buttons: list[tuple[str, str, str]] | None = None
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": text or "…"}}
    ]
    if buttons:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": label},
                        "type": "default",
                        "value": {"token": token, "decision": decision},
                    }
                    for label, token, decision in buttons
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title[:80]}},
        "elements": elements,
    }


def chunks(text: str, limit: int = 3500) -> list[str]:
    # Split only AFTER redaction. One long private key must never leak in pieces.
    return [text[i : i + limit] for i in range(0, len(text), limit)] or [""]
