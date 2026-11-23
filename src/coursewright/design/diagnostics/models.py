"""Data models returned by the Phase 1 diagnostics.

Every public function in this package returns one of these Pydantic
models (or a list of them) instead of printing.  The command line and
the skills render them; the library never does (AGENTS.md 3.8).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DiagnosticsError(Exception):
    """Base class for diagnostics failures that callers should handle."""


class CourseNotFoundError(DiagnosticsError):
    """Canvas has no course (or the instructor cannot see it) for an id."""


class WorkspaceNotConfiguredError(DiagnosticsError):
    """A report was requested but no private workspace is configured."""


class CourseSummary(BaseModel):
    """One course as listed by ``list_courses``."""

    id: int
    name: str = ""
    course_code: str = ""
    term_name: str = "N/A"
    workflow_state: str = ""


class ModuleItemRecord(BaseModel):
    """One module item, trimmed to what an audit needs."""

    id: int | None = None
    title: str = ""
    type: str = ""
    # ``None`` means Canvas did not say.  ``inspect_course`` callers treat
    # that as unpublished; ``list_module_items`` treats it as published,
    # exactly as the two source scripts did.
    published: bool | None = None
    external_url: str | None = None


class ModuleRecord(BaseModel):
    """One module with its items."""

    id: int
    name: str = ""
    published: bool = False
    unlock_at: str | None = None
    items: list[ModuleItemRecord] = Field(default_factory=list)


class CourseInspection(BaseModel):
    """A course and its module tree (``inspect_course``)."""

    course_id: int
    name: str = ""
    term_name: str | None = None
    workflow_state: str | None = None
    modules: list[ModuleRecord] = Field(default_factory=list)


class ItemMatch(BaseModel):
    """A module item whose title matched a pattern (``scan_course``)."""

    module_id: int
    item_id: int
    title: str


class CourseScan(BaseModel):
    """The terse module -> item tree plus any title matches."""

    course_id: int
    modules: list[ModuleRecord] = Field(default_factory=list)
    total_items: int = 0
    matches: list[ItemMatch] = Field(default_factory=list)


class PastDueAssignment(BaseModel):
    """An assignment whose due date is already behind us."""

    name: str
    due_at: str


class CourseDiagnostics(BaseModel):
    """Everything ``diagnose_course`` found, ready to save as JSON."""

    course_id: int
    course_name: str
    workflow_state: str = "unknown"
    timestamp: str
    total_modules: int = 0
    total_items: int = 0
    total_assignments: int = 0
    unpublished_modules: list[str] = Field(default_factory=list)
    modules_without_unlock_dates: list[str] = Field(default_factory=list)
    missing_due_dates: list[str] = Field(default_factory=list)
    past_due_dates: list[PastDueAssignment] = Field(default_factory=list)
    content_types: dict[str, int] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


class TextSurface(BaseModel):
    """One place in a course that holds text (page body, syllabus, ...)."""

    location: str
    text: str


class SurfaceMatch(BaseModel):
    """One pattern hit, with its surrounding text."""

    location: str
    snippet: str


class CourseSearchResult(BaseModel):
    """All hits of ``search_course``."""

    course_id: int
    course_name: str = ""
    pattern: str
    matches: list[SurfaceMatch] = Field(default_factory=list)

    @property
    def match_count(self) -> int:
        """How many hits there were."""
        return len(self.matches)


class PageText(BaseModel):
    """One Canvas page, as plain text and as raw HTML."""

    title: str
    url: str
    html: str = ""
    text: str = ""


LaunchSource = Literal["module item", "assignment"]


class ExternalToolLaunch(BaseModel):
    """One LTI launch found in a module item or an assignment."""

    source: LaunchSource
    module_name: str
    item_id: int | None = None
    title: str
    launch_url: str = ""
    context: str
    is_publisher_launch: bool
    is_legacy_lab_title: bool


class ContextGroup(BaseModel):
    """All launches that share one context fingerprint."""

    fingerprint: str
    launches: list[ExternalToolLaunch] = Field(default_factory=list)

    @property
    def affected_groupings(self) -> list[str]:
        """Sorted module or assignment-group names touched by this context."""
        return sorted({launch.module_name for launch in self.launches})

    @property
    def legacy_lab_count(self) -> int:
        """Launches whose title belongs to the retiring lab family."""
        return sum(1 for launch in self.launches if launch.is_legacy_lab_title)

    @property
    def module_item_count(self) -> int:
        """Launches that are navigation items."""
        return sum(1 for launch in self.launches if launch.source == "module item")

    @property
    def assignment_count(self) -> int:
        """Launches that are graded assignments."""
        return sum(1 for launch in self.launches if launch.source == "assignment")


class ContextAuditReport(BaseModel):
    """The verdict of ``audit_text_contexts``.

    ``is_clean`` replaces the source's exit code: the cantrip returned 1
    when more than one publisher context was present.
    """

    course_id: int
    total_launches: int = 0
    module_item_launch_count: int = 0
    assignment_launch_count: int = 0
    publisher_contexts: list[ContextGroup] = Field(default_factory=list)
    tool_contexts: list[ContextGroup] = Field(default_factory=list)
    titles_spanning_contexts: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def context_count(self) -> int:
        """Number of distinct publisher contexts."""
        return len(self.publisher_contexts)

    @property
    def is_clean(self) -> bool:
        """True when at most one publisher context is present."""
        return self.context_count <= 1
