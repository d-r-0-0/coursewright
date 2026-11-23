"""Weekly announcements: markdown to Canvas HTML, scheduled posting, listing.

Ported from COURSE_BUILDER ``scripts/publishing/announcements.py`` and the
``list_announcements`` / ``outline_announcements`` cantrips.

Changes from the source, all required by the porting rules:

* writes go through :class:`SafeCanvasWriter`, so they preview by default
  and back up before executing (AGENTS.md 3.11);
* nothing prints; functions return data (AGENTS.md 3.8);
* the hard-coded per-course table (real course ids, office-hours address)
  is gone: course settings are an :class:`AnnouncementCourseSettings`
  loaded from the instructor's own TOML file;
* announcement-id bookkeeping takes its folder as a parameter instead of a
  ``materials/<course>/`` path.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteOutcome
from coursewright.design.publishing._strict_reads import fetch_every_announcement

# Canvas strips <style> blocks, so every element carries inline styling.
CODE_BACKGROUND_STYLE = "background-color: #f4f4f4;"
SECTION_SEPARATOR = '<hr style="margin: 20px 0;">'
HEADING_PATTERN = re.compile(r"<h2[^>]*>(.*?)</h2>", re.DOTALL | re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
ANNOUNCEMENT_IDS_FILE_NAME = "announcement_ids.json"


# ----- markdown to HTML ------------------------------------------------------


def _process_inline_markdown(text: str) -> str:
    """Turn inline markdown (links, bold, italic, code) into HTML."""
    # Markdown links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Plain URLs (not already in a tag)
    text = re.sub(r'(?<!href=")(https?://[^\s<>"]+)', r'<a href="\1">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    code_open_tag = f'<code style="{CODE_BACKGROUND_STYLE} padding: 2px 6px; border-radius: 3px;">'
    return re.sub(r"`([^`]+)`", code_open_tag + r"\1</code>", text)


def markdown_to_html(markdown_content: str) -> str:
    """Convert an announcement's markdown to clean, inline-styled Canvas HTML.

    Handles headers (``##``, ``###``), tables, fenced code blocks, ordered and
    unordered lists, bold, italic, inline code, links (markdown and plain
    URLs) and horizontal rules.  The header block (``# Week`` title, post
    date, subject, first ``---``) is skipped: it is metadata, not body.
    """
    lines = markdown_content.split("\n")
    html_parts: list[str] = []

    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if (
            line.startswith("# Week")
            or line.startswith("**Post Date:**")
            or line.startswith("**Subject:**")
        ):
            line_index += 1
            continue
        if line == "---":
            line_index += 1
            break
        line_index += 1

    while line_index < len(lines):
        line = lines[line_index]

        if line == "---":
            html_parts.append(SECTION_SEPARATOR)
            line_index += 1
            continue

        if line.startswith("```"):
            code_lines = []
            line_index += 1
            while line_index < len(lines) and not lines[line_index].startswith("```"):
                code_lines.append(lines[line_index])
                line_index += 1
            line_index += 1  # skip the closing fence
            code_content = "\n".join(code_lines)
            html_parts.append(
                f'<pre style="{CODE_BACKGROUND_STYLE} padding: 12px; border-radius: 4px; '
                f'overflow-x: auto; font-family: monospace;"><code>{code_content}</code></pre>'
            )
            continue

        next_line = lines[line_index + 1] if line_index + 1 < len(lines) else ""
        if "|" in line and "|" in next_line and "-" in next_line:
            headers = [cell.strip() for cell in line.split("|")[1:-1]]
            line_index += 2  # skip the header row and the separator row
            table_html = [
                '<table style="border-collapse: collapse; width: 100%; margin: 10px 0;">',
                "<thead><tr>",
            ]
            table_html.extend(
                '<th style="border: 1px solid #ddd; padding: 10px; '
                'background-color: #f8f8f8; text-align: left;">'
                f"{_process_inline_markdown(header)}</th>"
                for header in headers
            )
            table_html.extend(["</tr></thead>", "<tbody>"])
            while line_index < len(lines) and "|" in lines[line_index]:
                cells = [cell.strip() for cell in lines[line_index].split("|")[1:-1]]
                table_html.append("<tr>")
                table_html.extend(
                    '<td style="border: 1px solid #ddd; padding: 10px;">'
                    f"{_process_inline_markdown(cell)}</td>"
                    for cell in cells
                )
                table_html.append("</tr>")
                line_index += 1
            table_html.append("</tbody></table>")
            html_parts.append("\n".join(table_html))
            continue

        if line.startswith("## "):
            html_parts.append(
                '<h2 style="color: #2d3b45; margin-top: 25px; margin-bottom: 10px; '
                'border-bottom: 2px solid #eee; padding-bottom: 5px;">'
                f"{_process_inline_markdown(line[3:])}</h2>"
            )
            line_index += 1
            continue
        if line.startswith("### "):
            html_parts.append(
                '<h3 style="color: #2d3b45; margin-top: 20px; margin-bottom: 8px;">'
                f"{_process_inline_markdown(line[4:])}</h3>"
            )
            line_index += 1
            continue

        if line.startswith(("- ", "* ")):
            list_items = []
            while line_index < len(lines) and lines[line_index].startswith(("- ", "* ")):
                list_items.append(
                    '<li style="margin-bottom: 5px;">'
                    f"{_process_inline_markdown(lines[line_index][2:])}</li>"
                )
                line_index += 1
            joined_items = "".join(list_items)
            html_parts.append(
                f'<ul style="margin: 10px 0; padding-left: 25px;">{joined_items}</ul>'
            )
            continue

        if re.match(r"^\d+\.\s", line):
            list_items = []
            while line_index < len(lines) and re.match(r"^\d+\.\s", lines[line_index]):
                item_text = re.sub(r"^\d+\.\s", "", lines[line_index])
                list_items.append(
                    f'<li style="margin-bottom: 5px;">{_process_inline_markdown(item_text)}</li>'
                )
                line_index += 1
            joined_items = "".join(list_items)
            html_parts.append(
                f'<ol style="margin: 10px 0; padding-left: 25px;">{joined_items}</ol>'
            )
            continue

        if not line.strip():
            line_index += 1
            continue

        html_parts.append(
            f'<p style="margin: 10px 0; line-height: 1.6;">{_process_inline_markdown(line)}</p>'
        )
        line_index += 1

    return "\n".join(html_parts)


# ----- schedule and per-course settings --------------------------------------


def parse_schedule(schedule_path: Path) -> list[dict[str, Any]]:
    """Parse a tab-separated schedule file into units with their dates.

    Each non-comment line is ``Unit NN<TAB>assigned<TAB>first post<TAB>due``.
    Returns dicts with ``unit_num``, ``unit_name``, ``assigned``,
    ``first_post`` and ``due``.
    """
    units: list[dict[str, Any]] = []
    with Path(schedule_path).open(encoding="utf-8") as schedule_file:
        for raw_line in schedule_file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            unit_match = re.match(r"Unit\s*(\d+)", parts[0], re.IGNORECASE)
            if unit_match:
                units.append(
                    {
                        "unit_num": int(unit_match.group(1)),
                        "unit_name": parts[0],
                        "assigned": parts[1],
                        "first_post": parts[2],
                        "due": parts[3],
                    }
                )
    return units


@dataclass(frozen=True)
class AnnouncementCourseSettings:
    """What the announcement templates need to know about one course."""

    course_id: int
    course_name: str
    discussion_units: tuple[int, ...] = ()
    help_discussion: str = ""
    help_module: str = ""
    office_hours: str = ""
    instructor: str = ""


def load_announcement_course_settings(
    settings_path: Path, course_code: str
) -> AnnouncementCourseSettings:
    """Read one course's settings from ``[courses.<CODE>]`` in a TOML file.

    The source kept a table of real courses in code; the file now lives in
    the instructor's private workspace.

    Raises:
        KeyError: if *course_code* is not in the file.
    """
    settings_text = Path(settings_path).read_text(encoding="utf-8")
    courses = tomllib.loads(settings_text).get("courses", {})
    if course_code not in courses:
        raise KeyError(f"{course_code} is not in {Path(settings_path).name}")
    entry = courses[course_code]
    return AnnouncementCourseSettings(
        course_id=int(entry["course_id"]),
        course_name=str(entry.get("course_name", course_code)),
        discussion_units=tuple(int(unit) for unit in entry.get("discussion_units", ())),
        help_discussion=str(entry.get("help_discussion", "")),
        help_module=str(entry.get("help_module", "")),
        office_hours=str(entry.get("office_hours", "")),
        instructor=str(entry.get("instructor", "")),
    )


# ----- pushing and updating announcements ------------------------------------


def create_announcement(
    writer: SafeCanvasWriter,
    course_id: int,
    title: str,
    html_content: str,
    post_at: str,
) -> WriteOutcome:
    """Create an announcement that Canvas posts itself at *post_at*.

    *post_at* is ``YYYY-MM-DDTHH:MM:SS`` with no ``Z`` (AGENTS.md 3.10); the
    writer rejects anything else.  Without ``execute`` on the writer this
    only previews: the outcome has ``executed=False`` and no request is made.
    """
    return writer.create_discussion_topic(
        course_id,
        {
            "title": title,
            "message": html_content,
            "is_announcement": True,
            "delayed_post_at": post_at,
            "published": True,
        },
    )


def update_announcement(
    writer: SafeCanvasWriter,
    course_id: int,
    announcement_id: int,
    html_content: str,
) -> WriteOutcome:
    """Replace an announcement's body, keeping its scheduled post date."""
    return writer.update_discussion_topic(course_id, announcement_id, {"message": html_content})


def load_announcement_ids(announcements_directory: Path) -> dict[str, Any]:
    """Load the announcement ids saved after an earlier push (empty if none)."""
    ids_file = Path(announcements_directory) / ANNOUNCEMENT_IDS_FILE_NAME
    if ids_file.exists():
        with ids_file.open(encoding="utf-8") as ids_stream:
            return json.load(ids_stream)
    return {}


def save_announcement_ids(announcements_directory: Path, announcement_ids: dict[str, Any]) -> Path:
    """Save announcement ids so a later run can update them in place."""
    ids_file = Path(announcements_directory) / ANNOUNCEMENT_IDS_FILE_NAME
    ids_file.parent.mkdir(parents=True, exist_ok=True)
    with ids_file.open("w", encoding="utf-8") as ids_stream:
        json.dump(announcement_ids, ids_stream, indent=2)
    return ids_file


# ----- reading announcements -------------------------------------------------


@dataclass(frozen=True)
class AnnouncementSummary:
    """One line of an announcement listing."""

    announcement_id: int
    posted_date: str
    title: str


@dataclass(frozen=True)
class AnnouncementOutline:
    """An announcement's ``<h2>`` section headings, in body order."""

    announcement_id: int
    title: str
    section_headings: tuple[str, ...]


def list_announcements(client: CanvasClient, course_id: int) -> list[AnnouncementSummary]:
    """List every announcement of a course (id, posted date, title).

    Raises:
        CanvasReadError: if any page fails, so a failed read is never shown
            as an empty course.
    """
    return [
        AnnouncementSummary(
            announcement_id=announcement["id"],
            posted_date=(announcement.get("posted_at") or "N/A")[:10],
            title=announcement.get("title", ""),
        )
        for announcement in fetch_every_announcement(client, course_id)
    ]


def outline_announcement_body(message_html: str) -> tuple[str, ...]:
    """Return the ``<h2>`` heading of each ``<hr>``-separated section.

    A section with no ``<h2>`` is labelled ``(no h2)``.
    """
    headings = []
    for section in message_html.split(SECTION_SEPARATOR):
        heading_match = HEADING_PATTERN.search(section)
        headings.append(
            TAG_PATTERN.sub("", heading_match.group(1)).strip() if heading_match else "(no h2)"
        )
    return tuple(headings)


def outline_announcements(client: CanvasClient, course_id: int) -> list[AnnouncementOutline]:
    """Outline every announcement, ordered by scheduled post date."""
    announcements = fetch_every_announcement(client, course_id)
    announcements.sort(key=lambda announcement: announcement.get("delayed_post_at") or "")
    return [
        AnnouncementOutline(
            announcement_id=announcement["id"],
            title=announcement.get("title") or "",
            section_headings=outline_announcement_body(announcement.get("message") or ""),
        )
        for announcement in announcements
    ]
