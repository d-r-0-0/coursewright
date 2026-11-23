"""Pydantic v2 models for Canvas LMS resources and discussion analysis.

Ported from discussorator ``canvas/models.py``.  Configuration models
(``AppConfig``, ``DiscussionConfig``, ``ExportConfig``, ``ExportFormat``,
``LLMConfig``, ``AssistantConfig``, ``AssistantRankingWeights``,
``CLIConfig``) live in ``coursewright.core.config`` and are re-exported
here so callers importing from ``coursewright.core.canvas.models`` keep
working.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from coursewright.core.config.settings import (
    AppConfig,
    AssistantConfig,
    AssistantRankingWeights,
    CLIConfig,
    DiscussionConfig,
    ExportConfig,
    ExportFormat,
    LLMConfig,
)


class EnrollmentType(str, Enum):  # noqa: UP042
    """Canvas enrollment types."""

    STUDENT = "StudentEnrollment"
    TEACHER = "TeacherEnrollment"
    TA = "TaEnrollment"
    OBSERVER = "ObserverEnrollment"
    DESIGNER = "DesignerEnrollment"


class DiscussionType(str, Enum):  # noqa: UP042
    """Discussion topic types."""

    THREADED = "threaded"
    SIDE_COMMENT = "side_comment"
    FLAT = "flat"


class GradingCriteria(BaseModel):
    """Individual grading criteria."""

    name: str
    description: str | None = None
    min_words: int | None = None
    max_words: int | None = None
    required_replies: int | None = None
    points: float = 0.0
    weight: float = 1.0


class DiscussionRubric(BaseModel):
    """Discussion grading rubric."""

    name: str
    description: str | None = None
    total_points: float = 100.0
    criteria: list[GradingCriteria] = Field(default_factory=list)

    min_word_count: int = Field(default=150, description="Minimum words for main post")
    min_replies: int = Field(default=2, description="Minimum required replies")
    participation_points: float = Field(default=50.0, description="Points for participation")
    quality_points: float = Field(default=50.0, description="Points for post quality")
    grading_profile: Literal["legacy", "ai_discussion"] = Field(
        default="ai_discussion",
        description="Scoring profile used by the analyzer",
    )

    @field_validator("total_points")
    @classmethod
    def validate_total_points(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Total points must be greater than 0")
        return value


class CanvasCourse(BaseModel):
    """Canvas Course model."""

    id: int
    name: str
    course_code: str | None = None
    sis_course_id: str | None = None
    uuid: str | None = None
    workflow_state: str | None = "available"
    account_id: int | None = None
    root_account_id: int | None = None
    enrollment_term_id: int | None = None
    term: dict[str, Any] | None = None
    total_students: int | None = 0
    start_at: datetime | None = None
    end_at: datetime | None = None
    grading_standard_id: int | None = None
    is_public: bool = False
    is_public_to_auth_users: bool = False
    public_syllabus: bool = False
    public_syllabus_to_auth: bool = False
    public_description: str | None = None
    storage_quota_mb: int | None = None
    storage_quota_used_mb: int | None = None
    hide_final_grades: bool = False
    license: str | None = None
    allow_student_assignment_edits: bool = False
    allow_wiki_comments: bool = False
    allow_student_forum_attachments: bool = False
    open_enrollment: bool = False
    self_enrollment: bool = False
    restrict_enrollments_to_course_dates: bool = False
    course_format: str | None = None
    access_restricted_by_date: bool = False
    time_zone: str | None = None
    blueprint: bool = False
    blueprint_restrictions: dict[str, Any] | None = None
    blueprint_restrictions_by_object_type: dict[str, Any] | None = None
    template: bool = False
    enrollments: list[dict[str, Any]] = Field(default_factory=list)
    last_participation_at: datetime | None = None
    average_grade: float | None = None

    @field_serializer("start_at", "end_at", "last_participation_at")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class SubmissionAttachment(BaseModel):
    """A file attached to a Canvas submission."""

    id: int
    uuid: str | None = None
    display_name: str = ""
    filename: str = ""
    content_type: str | None = Field(default=None, alias="content-type")
    size: int = 0
    url: str = ""
    preview_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"populate_by_name": True}

    @field_serializer("created_at", "updated_at")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @property
    def extension(self) -> str:
        """Return the lowercase file extension (e.g. '.pdf')."""
        name = (self.display_name or self.filename).strip()
        return PurePosixPath(name).suffix.lower()


class SubmissionComment(BaseModel):
    """A single comment on a Canvas submission."""

    id: int
    author_id: int = 0
    comment: str = ""
    created_at: datetime | None = None

    model_config = {"populate_by_name": True}

    @field_serializer("created_at")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class Submission(BaseModel):
    """A student's submission for a Canvas assignment."""

    id: int
    user_id: int = 0
    assignment_id: int = 0
    submission_type: str | None = None
    workflow_state: str = "unsubmitted"
    submitted_at: datetime | None = None
    graded_at: datetime | None = None
    score: float | None = None
    grade: str | None = None
    excused: bool | None = None
    attempt: int | None = None
    body: str | None = None
    preview_url: str | None = None
    attachments: list[SubmissionAttachment] = Field(default_factory=list)
    submission_comments: list[SubmissionComment] = Field(default_factory=list)
    user: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}

    @field_serializer("submitted_at", "graded_at")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @property
    def student_name(self) -> str:
        """Best-effort student name from the embedded user dict."""
        if self.user and isinstance(self.user, dict):
            return self.user.get("name", f"User {self.user_id}")
        return f"User {self.user_id}"

    @property
    def is_submitted(self) -> bool:
        """True if the student has actually turned something in."""
        return self.submitted_at is not None and self.workflow_state != "unsubmitted"


class Assignment(BaseModel):
    """Canvas Assignment model."""

    id: int
    name: str
    description: str | None = None
    points_possible: float | None = 0.0
    due_at: datetime | None = None
    unlock_at: datetime | None = None
    lock_at: datetime | None = None
    assignment_group_id: int | None = None
    grading_type: str | None = None
    html_url: str | None = None
    published: bool = True
    course_id: int | None = None
    submission_types: list[str] = Field(default_factory=list)
    allowed_extensions: list[str] = Field(default_factory=list)
    needs_grading_count: int = 0

    @field_serializer("due_at", "unlock_at", "lock_at")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class CanvasUser(BaseModel):
    """Canvas User model."""

    id: int
    name: str
    created_at: datetime | None = None
    sortable_name: str | None = None
    short_name: str | None = None
    sis_user_id: str | None = None
    sis_import_id: int | None = None
    integration_id: str | None = None
    login_id: str | None = None
    avatar_url: str | None = None
    enrollments: list[dict[str, Any]] = Field(default_factory=list)
    email: str | None = None
    locale: str | None = None
    effective_locale: str | None = None
    last_login: datetime | None = None
    time_zone: str | None = None
    bio: str | None = None

    @field_serializer("created_at", "last_login")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class CanvasDiscussion(BaseModel):
    """Canvas Discussion Topic model."""

    id: int
    title: str
    message: str | None = None
    html_url: str | None = None
    posted_at: datetime | None = None
    last_reply_at: datetime | None = None
    require_initial_post: bool = False
    user_can_see_posts: bool = True
    discussion_subentry_count: int = 0
    read_state: str | None = None
    unread_count: int = 0
    subscribed: bool = False
    subscription_hold: str | None = None
    assignment_id: int | None = None
    delayed_post_at: datetime | None = None
    published: bool = True
    lock_at: datetime | None = None
    locked: bool = False
    pinned: bool = False
    locked_for_user: bool = False
    lock_info: dict[str, Any] | None = None
    lock_explanation: str | None = None
    user_name: str | None = None
    topic_children: list[int] = Field(default_factory=list)
    group_topic_children: list[dict[str, Any]] = Field(default_factory=list)
    root_topic_id: int | None = None
    podcast_url: str | None = None
    discussion_type: str | None = None
    group_category_id: int | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    permissions: dict[str, Any] | None = None
    allow_rating: bool = False
    only_graders_can_rate: bool = False
    sort_by_rating: bool = False
    is_announcement: bool = False

    course_id: int
    replies_count: int = Field(alias="discussion_subentry_count", default=0)

    model_config = {"populate_by_name": True}

    @field_serializer("posted_at", "last_reply_at", "delayed_post_at", "lock_at")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class CanvasDiscussionEntry(BaseModel):
    """Canvas Discussion Entry/Post model."""

    id: int
    user_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    rating_count: int | None = None
    rating_sum: int | None = None
    user_name: str | None = None
    message: str = ""
    user_display_name: str | None = None
    editor_id: int | None = None
    deleted: bool = False
    parent_id: int | None = None
    forced_read_state: bool = False
    read_state: str | None = None
    posted_at: datetime | None = None

    discussion_id: int
    word_count: int = 0
    replies: list[CanvasDiscussionEntry] = Field(default_factory=list)
    depth: int = 0

    @field_validator("word_count", mode="before")
    @classmethod
    def calculate_word_count(cls, _value: int, info: Any) -> int:
        if info.data.get("message"):
            clean_text = re.sub(r"<[^>]+>", "", info.data["message"])
            return len(clean_text.split())
        return 0

    model_config = {
        "validate_assignment": True,
        "validate_by_name": True,
    }

    @field_serializer("created_at", "updated_at", "posted_at")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class StudentParticipation(BaseModel):
    """Student participation analysis."""

    user_id: int
    user_name: str
    initial_post: bool = False
    initial_post_word_count: int = 0
    initial_post_date: datetime | None = None
    reply_count: int = 0
    total_word_count: int = 0
    total_posts: int = 0
    first_post_date: datetime | None = None
    last_post_date: datetime | None = None
    participation_score: float = 0.0
    quality_score: float = 0.0
    total_score: float = 0.0
    meets_requirements: bool = False
    band: str | None = None
    band_reasons: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    post_details: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("post_details", mode="before")
    @classmethod
    def _coerce_post_details(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @field_serializer("initial_post_date", "first_post_date", "last_post_date")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class DiscussionAnalysis(BaseModel):
    """Complete discussion analysis."""

    discussion_id: int
    discussion_title: str
    course_id: int
    analysis_date: datetime = Field(default_factory=datetime.now)

    total_students: int = 0
    participating_students: int = 0
    total_posts: int = 0
    total_replies: int = 0
    total_words: int = 0
    average_words_per_post: float = 0.0
    participation_rate: float = 0.0

    student_participation: list[StudentParticipation] = Field(default_factory=list)

    rubric_used: str | None = None
    average_score: float = 0.0
    students_meeting_requirements: int = 0

    grading_profile: Literal["legacy", "ai_discussion"] = "legacy"
    profile_detection_reason: str | None = None
    # The most a student could earn under the profile and points used.
    rubric_points: float = 100.0

    @field_serializer("analysis_date")
    @classmethod
    def serialize_datetime(cls, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


CanvasDiscussionEntry.model_rebuild()

__all__ = [
    "AppConfig",
    "Assignment",
    "AssistantConfig",
    "AssistantRankingWeights",
    "CLIConfig",
    "CanvasCourse",
    "CanvasDiscussion",
    "CanvasDiscussionEntry",
    "CanvasUser",
    "DiscussionAnalysis",
    "DiscussionConfig",
    "DiscussionRubric",
    "DiscussionType",
    "EnrollmentType",
    "ExportConfig",
    "ExportFormat",
    "GradingCriteria",
    "LLMConfig",
    "StudentParticipation",
    "Submission",
    "SubmissionAttachment",
    "SubmissionComment",
]
