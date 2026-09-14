"""Check repository-local documentation references without accessing the network."""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    names = (
        subprocess.check_output(  # noqa: S603
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],  # noqa: S607
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    markdown = MarkdownIt()
    errors = []
    checked = 0
    for name in sorted(set(names)):
        path = ROOT / name
        if not path.is_file() or path.suffix not in (".md", ".svg"):
            continue
        content = path.read_text()
        if "\ufffd" in content:
            errors.append(f"{name}: Unicode replacement character")
        if path.suffix == ".svg":
            try:
                ET.fromstring(content)  # noqa: S314 -- trusted repository assets, no network
            except ET.ParseError as error:
                errors.append(f"{name}: invalid SVG: {error}")
            continue
        checked += 1
        for block in markdown.parse(content):
            for token in block.children or []:
                href = token.attrGet("href") or token.attrGet("src")
                if not href:
                    continue
                url = urlsplit(href)
                if url.scheme or url.netloc or not url.path:
                    continue
                target = path.parent / unquote(url.path)
                if not target.exists():
                    errors.append(f"{name}: missing local target: {href}")
    expected = {f"u{i:02}" for i in range(1, 26)}
    for language in ("en", "zh-CN"):
        path = ROOT / "docs" / language / "user-guide.md"
        anchors = set(re.findall(r'<a id="(u\d+)"', path.read_text()))
        if anchors != expected:
            errors.append(f"{language}/user-guide.md: expected U01–U25 anchors")
    for error in errors:
        print(error)
    if errors:
        return 1
    print(
        f"Documentation checks passed: {checked} Markdown files; bilingual anchors and SVGs valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
