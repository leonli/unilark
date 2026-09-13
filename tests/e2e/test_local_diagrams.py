"""Actual local Chromium rendering, including attempted network access."""

from __future__ import annotations

import asyncio
import os
import struct

import pytest
from playwright.async_api import Error

from unilark.projection.mermaid import render_png

pytestmark = [
    pytest.mark.local_browser,
    pytest.mark.skipif(not os.environ.get("UNILARK_LOCAL_BROWSER"), reason="Browser opt-in"),
]


@pytest.mark.parametrize(
    "source",
    [
        "flowchart TD\nA[手机 Lark] --> B[会话网关]\nB --> C[本地 Agent]",
        "sequenceDiagram\nparticipant A as 手机\nparticipant B as 网关\n"
        "A->>B: 提交任务\nB-->>A: 回复",
    ],
)
async def test_real_chromium_renders_chinese_flow_and_sequence(source):
    png = await render_png(source)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1000
    width, height = struct.unpack(">II", png[16:24])
    assert 100 < width < 2500 and 100 < height < 9100


async def test_renderer_never_fetches_diagram_image_urls():
    requests = []

    async def received(reader, writer):
        requests.append(True)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(received, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        source = (
            'flowchart LR\nA@{ img: "http://127.0.0.1:'
            + str(port)
            + '/private", label: "网络图片", pos: "b", h: 60 } --> B[安全]'
        )
        # A graph referencing unavailable external images may reject or render an
        # empty image. In either case no request may reach the listening server.
        try:
            await render_png(source)
        except (Error, TimeoutError):
            assert not requests
        assert not requests
    finally:
        server.close()
        await server.wait_closed()


async def test_invalid_mermaid_fails_instead_of_rendering_error_as_success():
    with pytest.raises(Error):
        await render_png("not-a-diagram\nthis cannot parse")
