"""Search every text surface of a course; read one page as plain text.

Library versions of the ``grep_course`` and ``show_page`` cantrips.
Copy-forward shells hide stale prose (office hours, room numbers, meeting
times) in places nobody opens: an assignment description, a reading, the
syllabus, a scheduled announcement.  Everything here is read-only.
"""

from __future__ import annotations

import re

from coursewright.design.diagnostics.models import (
    CourseSearchResult,
    PageText,
    SurfaceMatch,
    TextSurface,
)
from coursewright.design.diagnostics.reading import ReadOnlyCanvas

TAG_PATTERN = re.compile(r"<[^>]+>")
BLOCK_END_PATTERN = re.compile(r"<br\s*/?>|</(p|li|h\d|div)>")
DEFAULT_CONTEXT_CHARACTERS = 90


def html_to_plain_text(html: str) -> str:
    """Turn a page body into text, keeping line breaks at block ends."""
    with_breaks = BLOCK_END_PATTERN.sub("\n", html)
    return TAG_PATTERN.sub("", with_breaks).strip()


def collect_surfaces(canvas: ReadOnlyCanvas, course_id: int) -> list[TextSurface]:
    """Gather every (location, text) pair the course exposes."""
    surfaces: list[TextSurface] = []

    course = canvas.get_course(course_id)
    if course:
        surfaces.append(TextSurface(location="Syllabus", text=course.get("syllabus_body") or ""))

    for module in canvas.get_modules(course_id):
        for item in canvas.get_module_items(course_id, module["id"]):
            surfaces.append(
                TextSurface(
                    location=f"Module item [{module['name']}] {item.get('title', '')}",
                    text=item.get("external_url") or "",
                )
            )

    for page in canvas.get_pages(course_id):
        detail = canvas.get_page(course_id, page["url"])
        if detail:
            surfaces.append(
                TextSurface(
                    location=f"Page: {page.get('title', '')}", text=detail.get("body") or ""
                )
            )

    for assignment in canvas.get_assignments(course_id):
        surfaces.append(
            TextSurface(
                location=f"Assignment: {assignment.name}", text=assignment.description or ""
            )
        )

    for quiz in canvas.get_quizzes(course_id):
        surfaces.append(
            TextSurface(
                location=f"Quiz: {quiz.get('title', '')}", text=quiz.get("description") or ""
            )
        )

    for topic in canvas.get_discussion_topics(course_id):
        surfaces.append(
            TextSurface(
                location=f"Discussion: {topic.get('title', '')}", text=topic.get("message") or ""
            )
        )

    for announcement in canvas.get_announcements(course_id):
        surfaces.append(
            TextSurface(
                location=f"Announcement: {announcement.get('title', '')}",
                text=announcement.get("message") or "",
            )
        )

    return surfaces


def search_surfaces(
    surfaces: list[TextSurface],
    pattern: str,
    context_characters: int = DEFAULT_CONTEXT_CHARACTERS,
    search_markup: bool = False,
) -> list[SurfaceMatch]:
    """Find *pattern* (case-insensitive regex) in each surface's text.

    Args:
        search_markup: Search the HTML too.  Needed to find embeds and
            hrefs, which live inside tags and are invisible otherwise.
    """
    compiled = re.compile(pattern, re.IGNORECASE)
    matches: list[SurfaceMatch] = []
    for surface in surfaces:
        if not surface.text:
            continue
        searchable = surface.text if search_markup else TAG_PATTERN.sub(" ", surface.text)
        searchable = re.sub(r"\s+", " ", searchable).strip()
        for hit in compiled.finditer(searchable):
            start = max(0, hit.start() - context_characters)
            end = min(len(searchable), hit.end() + context_characters)
            matches.append(
                SurfaceMatch(location=surface.location, snippet=f"...{searchable[start:end]}...")
            )
    return matches


def search_course(
    canvas: ReadOnlyCanvas,
    course_id: int,
    pattern: str,
    context_characters: int = DEFAULT_CONTEXT_CHARACTERS,
    search_markup: bool = False,
) -> CourseSearchResult:
    """Search all text surfaces of a course for a regex."""
    course = canvas.get_course(course_id)
    return CourseSearchResult(
        course_id=course_id,
        course_name=(course or {}).get("name") or "",
        pattern=pattern,
        matches=search_surfaces(
            collect_surfaces(canvas, course_id), pattern, context_characters, search_markup
        ),
    )


def read_pages(canvas: ReadOnlyCanvas, course_id: int, title_fragment: str) -> list[PageText]:
    """Return every page whose title contains *title_fragment* (any case)."""
    fragment = title_fragment.lower()
    pages: list[PageText] = []
    for page in canvas.get_pages(course_id):
        if fragment not in page.get("title", "").lower():
            continue
        detail = canvas.get_page(course_id, page["url"])
        body = (detail or {}).get("body") or ""
        pages.append(
            PageText(
                title=page.get("title", ""),
                url=page["url"],
                html=body,
                text=html_to_plain_text(body),
            )
        )
    return pages
