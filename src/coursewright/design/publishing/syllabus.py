"""Rendering one shared syllabus per course and publishing it to Canvas.

Ported from COURSE_BUILDER cantrip ``publish_syllabus``.  The syllabus text
lives in a markdown template; course and term facts live in a TOML file with
a ``[term]`` table and one ``[courses.<CODE>]`` table per course.  Rendering
refuses to continue if any ``{{field}}`` has no value, so a blank never
reaches students.

Publishing writes both the Syllabus tab (the course's ``syllabus_body``) and
the ``syllabus`` page.  It previews by default; with ``execute=True`` each
write is backed up first by :class:`SafeCanvasWriter` and the result is read
back.

Changes from the source: the template and facts files are parameters (they
live in the instructor's private workspace, not this repository); the field
that proves the read-back (the source used a publisher code) is a parameter;
and the report is returned instead of printed.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import markdown

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import SafeCanvasWriter

FIELD_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")
COMMENT_PATTERN = re.compile(r"<!--.*?-->\s*", re.DOTALL)
SYLLABUS_PAGE_URL = "syllabus"


class SyllabusPublishError(Exception):
    """The syllabus cannot be published; the message says why."""


@dataclass(frozen=True)
class SyllabusPublishReport:
    """What a syllabus preview found and, when executed, did."""

    course_id: int
    html_characters: int
    current_tab_characters: int
    page_found: bool
    executed: bool
    backup_paths: tuple[Path, ...] = ()
    verified: bool | None = None


def load_course(course_code: str, courses_path: Path) -> dict[str, Any]:
    """Merge the ``[term]`` table with ``[courses.<course_code>]``.

    Raises:
        KeyError: if the course is not in the file.
    """
    courses_path = Path(courses_path)
    data = tomllib.loads(courses_path.read_text(encoding="utf-8"))
    if course_code not in data.get("courses", {}):
        raise KeyError(f"{course_code} is not in {courses_path.name}")
    return {**data.get("term", {}), **data["courses"][course_code]}


def field_text(value: object) -> str:
    """Lists become Markdown bullets; everything else is stripped text."""
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value).strip()


def render_markdown(template: str, fields: dict[str, Any]) -> str:
    """Fill every ``{{field}}``; a line left empty by a blank field is dropped.

    HTML comments (editor notes) are removed first.

    Raises:
        KeyError: naming every field the template uses but *fields* lacks.
    """
    template = COMMENT_PATTERN.sub("", template)
    missing_fields = sorted(
        {name for name in FIELD_PATTERN.findall(template) if name not in fields}
    )
    if missing_fields:
        raise KeyError(f"courses.toml has no value for: {', '.join(missing_fields)}")
    filled_lines = []
    for line in template.splitlines():
        filled = FIELD_PATTERN.sub(lambda match: field_text(fields[match.group(1)]), line)
        if FIELD_PATTERN.search(line) and not filled.strip():
            continue
        filled_lines.append(filled)
    return "\n".join(filled_lines).strip() + "\n"


def render_html(markdown_text: str) -> str:
    """Render markdown (with tables) to Canvas-ready HTML."""
    return markdown.markdown(markdown_text, extensions=["tables"])


def render_syllabus_for_course(
    course_code: str, template_path: Path, courses_path: Path
) -> tuple[dict[str, Any], str]:
    """Return a course's merged facts and its rendered syllabus HTML."""
    fields = load_course(course_code, courses_path)
    template = Path(template_path).read_text(encoding="utf-8")
    return fields, render_html(render_markdown(template, fields))


def publish_syllabus(
    client: CanvasClient,
    fields: dict[str, Any],
    html: str,
    backup_directory: Path,
    *,
    execute: bool = False,
    verification_text: str | None = None,
) -> SyllabusPublishReport:
    """Preview or publish a rendered syllabus to a course's tab and page.

    *fields* must hold ``canvas_id`` and ``course_code``; the Canvas course's
    own code must contain ``course_code`` or the publish is refused, so the
    syllabus cannot land in the wrong shell.  After an executed publish the
    Syllabus tab is read back; if *verification_text* is given, ``verified``
    says whether the tab contains it. Otherwise the complete rendered HTML
    is checked, so execution always includes a readback.

    Raises:
        SyllabusPublishError: when the course cannot be read or is not the
            expected course.
        BackupFailedError / WriteFailedError: from the safe writer.
    """
    course_id = int(fields["canvas_id"])
    course = client.get_course(course_id)
    if course is None:
        raise SyllabusPublishError(f"Could not read course {course_id}")
    if fields["course_code"] not in course.get("course_code", ""):
        raise SyllabusPublishError(f"Course {course_id} is not {fields['course_code']}")
    page = client.get_page(course_id, SYLLABUS_PAGE_URL)

    report_fields: dict[str, Any] = {
        "course_id": course_id,
        "html_characters": len(html),
        "current_tab_characters": len(course.get("syllabus_body") or ""),
        "page_found": page is not None,
    }
    if not execute:
        return SyllabusPublishReport(**report_fields, executed=False)

    writer = SafeCanvasWriter(client, backup_directory, execute=True)
    backup_paths = [writer.update_course(course_id, {"syllabus_body": html}).backup_path]
    if page is not None:
        backup_paths.append(
            writer.update_page(course_id, SYLLABUS_PAGE_URL, {"body": html}).backup_path
        )

    readback = client.get_course(course_id)
    if readback is None:
        raise SyllabusPublishError("Syllabus was written but could not be read back")
    expected_text = html if verification_text is None else verification_text
    verified = expected_text in (readback.get("syllabus_body") or "")
    return SyllabusPublishReport(
        **report_fields,
        executed=True,
        backup_paths=tuple(path for path in backup_paths if path is not None),
        verified=verified,
    )
