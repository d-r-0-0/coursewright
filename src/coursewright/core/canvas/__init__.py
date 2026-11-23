"""The one Canvas LMS client and its Pydantic models."""

from __future__ import annotations

from coursewright.core.canvas.auth_strategy import (
    AuthStrategy,
    CanvasAuthError,
    SessionAuth,
    TokenAuth,
)
from coursewright.core.canvas.client import (
    CanvasClient,
    EnhancedCanvasAPI,
    connect_canvas_from_config,
    create_canvas_client,
    load_api_key,
)
from coursewright.core.canvas.dates import (
    format_canvas_datetime,
    validate_canvas_date_fields,
)
from coursewright.core.canvas.models import (
    AppConfig,
    Assignment,
    CanvasCourse,
    CanvasDiscussion,
    CanvasDiscussionEntry,
    CanvasUser,
    DiscussionAnalysis,
    DiscussionRubric,
    DiscussionType,
    EnrollmentType,
    GradingCriteria,
    StudentParticipation,
    Submission,
    SubmissionAttachment,
    SubmissionComment,
)
from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    SafeWriteError,
    WriteFailedError,
    WriteOutcome,
    create_safe_canvas_writer,
)

__all__ = [
    "AppConfig",
    "Assignment",
    "AuthStrategy",
    "BackupFailedError",
    "CanvasAuthError",
    "CanvasClient",
    "CanvasCourse",
    "CanvasDiscussion",
    "CanvasDiscussionEntry",
    "CanvasUser",
    "DiscussionAnalysis",
    "DiscussionRubric",
    "DiscussionType",
    "EnhancedCanvasAPI",
    "EnrollmentType",
    "GradingCriteria",
    "SafeCanvasWriter",
    "SafeWriteError",
    "SessionAuth",
    "StudentParticipation",
    "Submission",
    "SubmissionAttachment",
    "SubmissionComment",
    "TokenAuth",
    "WriteFailedError",
    "WriteOutcome",
    "connect_canvas_from_config",
    "create_canvas_client",
    "create_safe_canvas_writer",
    "format_canvas_datetime",
    "load_api_key",
    "validate_canvas_date_fields",
]
