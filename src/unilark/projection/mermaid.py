"""Private, bounded local diagram rendering. Browser requests never leave this host."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from unilark.projection.rich_text import MAX_DIAGRAM

ASSETS = Path(__file__).with_name("assets")


async def render_png(source: str) -> bytes:
    if not source.strip() or len(source) > MAX_DIAGRAM:
        raise ValueError("Diagram source size is invalid")
    # Mermaid permits document config overrides; use only our fixed strict config.
    if re.search(r"%%\s*\{|^\s*---\s*$", source, re.MULTILINE):
        raise ValueError("Diagram configuration overrides are disabled")
    from playwright.async_api import async_playwright

    async with asyncio.timeout(12), async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 1600, "height": 1000},
                device_scale_factor=1.5,
                service_workers="block",
            )
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            page.set_default_timeout(8000)
            await page.set_content(
                '<!doctype html><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="'
                "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; img-src data:; font-src 'none'; "
                "connect-src 'none'; frame-src 'none'\">"
                "<style>body{margin:0;padding:24px;background:white;"
                'font-family:"Noto Sans CJK SC",sans-serif}svg{display:block}'
                "#diagram{width:fit-content;padding:8px}</style>"
                '<div id="diagram"></div>'
            )
            await page.add_script_tag(path=str(ASSETS / "mermaid.min.js"))
            await page.evaluate(
                """async (source) => {
                mermaid.initialize({startOnLoad:false, securityLevel:'strict',
                    theme:'base', maxTextSize:6000, maxEdges:300,
                    themeVariables:{primaryColor:'#e8f0fe',primaryBorderColor:'#4285f4',
                        primaryTextColor:'#202124',lineColor:'#5f6368'},
                    fontFamily:'Noto Sans CJK SC, sans-serif',
                    flowchart:{htmlLabels:false, useMaxWidth:false},
                    sequence:{useMaxWidth:false}, suppressErrorRendering:true});
                const result = await mermaid.render('unilark-diagram', source);
                document.querySelector('#diagram').innerHTML = result.svg;
                const svg = document.querySelector('#diagram svg');
                const box = svg.viewBox.baseVal;
                if (!box.width || !box.height) throw new Error('No diagram bounds');
                const width = Math.min(1552, Math.max(320, box.width));
                const height = box.height * width / box.width;
                if (height > 6000) throw new Error('Diagram too tall');
                svg.style.maxWidth = 'none';
                svg.setAttribute('width', width);
                svg.setAttribute('height', height);
                await document.fonts.ready;
            }""",
                source,
            )
            result = await page.locator("#diagram").screenshot(type="png")
            if len(result) >= 9_000_000:
                raise ValueError("Diagram image is too large")
            return result
        finally:
            await browser.close()
