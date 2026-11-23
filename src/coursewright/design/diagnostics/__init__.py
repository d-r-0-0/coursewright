"""Phase 1: audit a course shell and gather its content.

Read-only against Canvas.  Every function returns a data model; reports are
written to the private workspace.  See ``reading.ReadOnlyCanvas`` for the
only Canvas calls this package may make.
"""

from coursewright.design.diagnostics.diagnose import diagnose_course, diagnose_course_by_id
from coursewright.design.diagnostics.inventory import (
    find_courses,
    inspect_course,
    list_courses,
    list_module_items,
    list_modules,
    scan_course,
)
from coursewright.design.diagnostics.models import (
    ContextAuditReport,
    CourseDiagnostics,
    CourseNotFoundError,
    DiagnosticsError,
    WorkspaceNotConfiguredError,
)
from coursewright.design.diagnostics.reports import (
    save_context_audit_report,
    save_diagnostics_report,
)
from coursewright.design.diagnostics.search import read_pages, search_course
from coursewright.design.diagnostics.text_contexts import AuditPolicy, audit_text_contexts

__all__ = [
    "AuditPolicy",
    "ContextAuditReport",
    "CourseDiagnostics",
    "CourseNotFoundError",
    "DiagnosticsError",
    "WorkspaceNotConfiguredError",
    "audit_text_contexts",
    "diagnose_course",
    "diagnose_course_by_id",
    "find_courses",
    "inspect_course",
    "list_courses",
    "list_module_items",
    "list_modules",
    "read_pages",
    "save_context_audit_report",
    "save_diagnostics_report",
    "scan_course",
    "search_course",
]
