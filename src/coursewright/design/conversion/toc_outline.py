"""Extract a readable Markdown outline from textbook TOC HTML."""

from __future__ import annotations

import html
import re
from pathlib import Path

TOC_NODE_PATTERN = re.compile(
    r'<li[^>]*aria-level="(?P<level>\d+)"[^>]*?>.*?'
    r'<span[^>]*class="page-title"[^>]*>(?P<title>.*?)</span>.*?'
    r'<span[^>]*class="page-number">(?P<page>.*?)</span>',
    re.DOTALL,
)

PAGE_TITLE_PATTERN = re.compile(
    r'<span[^>]*class="page-title"[^>]*>(?P<title>.*?)</span>',
    re.DOTALL,
)

PAGE_NUMBER_PATTERN = re.compile(
    r'<span[^>]*class="page-number">(?P<page>.*?)</span>',
    re.DOTALL,
)


def clean_text(value: str) -> str:
    """Decode HTML entities and normalize whitespace."""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_toc_nodes(raw_html: str) -> list[dict[str, str | int]]:
    """Parse nested TOC items from the HTML tree widget markup."""
    nodes: list[dict[str, str | int]] = []

    for match in TOC_NODE_PATTERN.finditer(raw_html):
        title = clean_text(match.group("title"))
        page = clean_text(match.group("page"))
        level = int(match.group("level"))
        if not title:
            continue
        nodes.append({"level": level, "title": title, "page": page})

    return nodes


def find_module_headers(raw_html: str) -> list[tuple[str, str]]:
    """Find top-level module headers that sit outside the li tree items."""
    modules: list[tuple[str, str]] = []
    seen_titles: set[str] = set()

    titles = [clean_text(m.group("title")) for m in PAGE_TITLE_PATTERN.finditer(raw_html)]
    pages = [clean_text(m.group("page")) for m in PAGE_NUMBER_PATTERN.finditer(raw_html)]

    for index, title in enumerate(titles):
        if not re.match(r"^Module\s+\d+\.", title) or title in seen_titles:
            continue
        page = pages[index] if index < len(pages) else ""
        seen_titles.add(title)
        modules.append((title, page))

    return modules


def build_outline(raw_html: str) -> str:
    """Return a readable Markdown outline from textbook TOC HTML."""
    module_headers = find_module_headers(raw_html)
    nodes = parse_toc_nodes(raw_html)

    lines = ["# Extracted Textbook TOC", ""]

    if module_headers:
        lines.append("## Top-Level Modules")
        lines.append("")
        for title, page in module_headers:
            page_suffix = f" (p. {page})" if page else ""
            lines.append(f"- {title}{page_suffix}")
        lines.append("")

    lines.append("## Full Outline")
    lines.append("")

    for node in nodes:
        level = int(node["level"])
        indent = "  " * max(level - 1, 0)
        page_suffix = f" (p. {node['page']})" if node["page"] else ""
        lines.append(f"{indent}- {node['title']}{page_suffix}")

    lines.append("")
    return "\n".join(lines)


def write_outline(input_path: Path, output_path: Path | None = None) -> Path:
    """Read TOC HTML and write the outline; returns the output path.

    Raises FileNotFoundError when the input does not exist.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")
    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}_extracted.md")
    output_path.write_text(build_outline(input_path.read_text(encoding="utf-8")), encoding="utf-8")
    return output_path
