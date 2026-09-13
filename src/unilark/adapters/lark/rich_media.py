"""Resolve internal diagram blocks before Lark delivery, without changing saved cards."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from unilark.projection.mermaid import render_png
from unilark.projection.rich_text import diagram_source, fenced, markdown


class RichMedia:
    def __init__(self, upload: Callable[[bytes], Awaitable[str]]) -> None:
        self.upload = upload
        # Per authenticated channel: never reuse image keys across Lark applications.
        self.cache: OrderedDict[str, str] = OrderedDict()
        self.failed: OrderedDict[str, float] = OrderedDict()
        self.lock = asyncio.Lock()

    async def image(self, source: str) -> str:
        digest = hashlib.sha256(source.encode()).hexdigest()
        async with self.lock:
            if digest in self.cache:
                self.cache.move_to_end(digest)
                return self.cache[digest]
            if self.failed.get(digest, 0) > time.monotonic():
                raise RuntimeError("Diagram retry is cooling down")
            try:
                async with asyncio.timeout(18):
                    key = await self.upload(await render_png(source))
            except Exception:
                self.failed[digest] = time.monotonic() + 30
                self.failed.move_to_end(digest)
                while len(self.failed) > 128:
                    self.failed.popitem(last=False)
                raise
            self.failed.pop(digest, None)
            self.cache[digest] = key
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return key

    async def prepare(self, card: dict[str, Any]) -> dict[str, Any]:
        prepared = copy.deepcopy(card)
        if prepared.get("schema") != "2.0":
            return prepared
        elements = []
        for element in prepared["body"]["elements"]:
            source = diagram_source(element)
            if source is None:
                elements.append(element)
                continue
            try:
                key = await self.image(source)
                elements.append(
                    {
                        "tag": "img",
                        "img_key": key,
                        "alt": {"tag": "plain_text", "content": "Mermaid 图表（点击查看大图）"},
                        "scale_type": "fit_horizontal",
                        "preview": True,
                    }
                )
                elements.append(markdown("图表可点击查看大图。"))
            except Exception:
                # No raw browser/API exception: it can contain private diagram text.
                elements.append(markdown("图表暂未生成，已保留 Mermaid 源码："))
                elements.extend(markdown(p) for p in fenced(source, "text"))
        prepared["body"]["elements"] = elements
        return prepared
