"""Word documents to Markdown (python-docx).

python-docx ships in the ``packettracer`` extra, not the core
dependencies, so it is imported lazily and a missing install raises a
clear error instead of breaking import of this package.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Office control characters (BEL cell markers, soft breaks) become spaces.
OFFICE_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

HEADING_PREFIXES = (
    ("heading 1", "# "),
    ("heading 2", "## "),
    ("heading 3", "### "),
    ("heading 4", "#### "),
    ("title", "# "),
)


def clean_paragraph_text(raw_text: str) -> str:
    return OFFICE_CONTROL_PATTERN.sub(" ", raw_text).strip()


def markdown_prefix_for_style(style_name: str) -> str:
    """Heading 1-4 and Title map to #-####; anything else is plain."""
    lowered = style_name.lower()
    for style_start, prefix in HEADING_PREFIXES:
        if lowered.startswith(style_start):
            return prefix
    return ""


def convert_docx_to_markdown(docx_path: Path, *, converted_at: datetime | None = None) -> str:
    """Return Markdown text for a .docx; the source is only read.

    Body paragraphs only: table cells and images are not extracted.
    """
    try:
        import docx
    except ImportError as err:
        raise RuntimeError(
            "python-docx is required to convert Word files: "
            "pip install 'coursewright[packettracer]'"
        ) from err

    stamp = (converted_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    document = docx.Document(str(docx_path))
    lines = [
        f"# {docx_path.stem}",
        "",
        f"**Source:** {docx_path.name}",
        f"**Converted:** {stamp}",
        "",
        "---",
        "",
    ]
    for paragraph in document.paragraphs:
        text = clean_paragraph_text(paragraph.text)
        if not text:
            continue
        style_name = paragraph.style.name if paragraph.style is not None else ""
        lines.append(markdown_prefix_for_style(style_name) + text)
        lines.append("")
    return "\n".join(lines)


def write_docx_markdown(docx_path: Path) -> Path | None:
    """Convert beside the source; None when the .md already exists."""
    output_path = docx_path.with_suffix(".md")
    if output_path.exists():
        return None
    output_path.write_text(convert_docx_to_markdown(docx_path), encoding="utf-8-sig")
    return output_path
