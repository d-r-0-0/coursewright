"""Unified Pydantic v2 settings models for Coursewright.

Combines the configuration models from discussorator (``AppConfig``,
``DiscussionConfig``, ``ExportConfig``, ``ExportFormat``, ``LLMConfig``,
``AssistantConfig``, ``CLIConfig``) with the course-design settings from
COURSE_BUILDER (``config.py``) and configurable template and calendar
defaults.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from coursewright.core.config.calendar import AcademicCalendarConfig

# AGENTS.md 3.10: Canvas API date format (ISO 8601 without "Z" suffix).
CANVAS_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
DATE_FORMAT_OUTPUT = CANVAS_DATE_FORMAT
SCHEDULE_DATE_FORMAT = "%m/%d/%Y %I:%M %p"
DATE_FORMAT_INPUT = SCHEDULE_DATE_FORMAT

WORKSPACE_ENVIRONMENT_VARIABLE = "COURSEWRIGHT_WORKSPACE"
DEFAULT_CONFIG_FILENAME = "config.yaml"
DEFAULT_CANVAS_URL = "https://canvas.example.edu"
DEFAULT_API_TOKEN_FILE = "api.key"
DEFAULT_RATE_LIMIT_DELAY = 0.1
DEFAULT_PER_PAGE = 100
DEFAULT_INTRO_MODULES: tuple[str, ...] = (
    "Getting Started",
    "Course Resources",
    "Assignments",
    "Syllabus",
)
DEFAULT_UNIT_COUNT = 15
DEFAULT_DISCUSSION_UNITS: tuple[int, ...] = (1, 3, 5, 7, 9, 11, 13)
DEFAULT_DISCUSSION_POINTS = 100
DEFAULT_CONTENT_PRIORITY: dict[str, int] = {
    "Chapter": 1, "Read": 2, "Flashcard": 3, "PowerPoint": 4,
    "Simulation": 5, "Lab": 6, "Quiz": 7,
}
DEFAULT_DRY_RUN = True
DEFAULT_BACKUP_DIRECTORY = "backups"
DEFAULT_ESSENTIAL_STANDARD_THRESHOLD = 3
DEFAULT_REQUIRED_ELEMENTS: dict[str, list[str]] = {
    "syllabus": [
        "getting_started", "course_purpose", "communication_guide", "policies",
        "learning_objectives", "grading_policy", "academic_integrity",
    ],
    "resources": [
        "tech_requirements", "tech_support", "accessibility",
        "academic_support", "student_services",
    ],
    "unit": ["unit_objectives", "activities_mapped"],
}

# Module-level compatibility constants for COURSE_BUILDER callers.
CANVAS_URL = DEFAULT_CANVAS_URL
API_KEY_PATH = DEFAULT_API_TOKEN_FILE
RATE_LIMIT_DELAY = DEFAULT_RATE_LIMIT_DELAY
PER_PAGE = DEFAULT_PER_PAGE
UNIT_COUNT = DEFAULT_UNIT_COUNT
DISCUSSION_UNITS = list(DEFAULT_DISCUSSION_UNITS)
DISCUSSION_POINTS = DEFAULT_DISCUSSION_POINTS
CONTENT_PRIORITY = dict(DEFAULT_CONTENT_PRIORITY)
BACKUP_DIR = Path(DEFAULT_BACKUP_DIRECTORY)
VERBOSE = False


def format_canvas_datetime(moment: datetime) -> str:
    """Format ``moment`` for Canvas API requests without a trailing ``Z``."""
    return moment.strftime(CANVAS_DATE_FORMAT)


class ExportFormat(StrEnum):
    """Supported export file formats."""

    CSV = "csv"
    EXCEL = "xlsx"
    JSON = "json"


class ExportConfig(BaseModel):
    """Export configuration for discussion and grade reports."""

    format: ExportFormat = ExportFormat.CSV
    include_raw_data: bool = False
    include_post_content: bool = True
    include_timestamps: bool = True
    date_format: str = "%Y-%m-%d %H:%M:%S"
    output_directory: str = "exports"
    filename_prefix: str = "discussion_analysis"

    @field_validator("output_directory")
    @classmethod
    def validate_output_directory(cls, value: str) -> str:
        Path(value).mkdir(exist_ok=True, parents=True)
        return value


class DiscussionConfig(BaseModel):
    """Discussion analysis and Canvas connection configuration."""

    canvas_url: str = DEFAULT_CANVAS_URL
    api_token_file: str = DEFAULT_API_TOKEN_FILE
    default_min_words: int = 300
    default_min_replies: int = 2
    default_rubric: str = "default_rubric.yaml"
    default_grading_profile: Literal["legacy", "ai_discussion", "auto"] = "auto"
    substantial_post_points: float = 60.0
    reply_points_each: float = 20.0
    max_replies_counted: int = 2
    include_instructor_posts: bool = False
    # Names students use to address the instructor (daily digest mentions).
    instructor_names: list[str] = Field(default_factory=list)
    include_ta_posts: bool = False
    count_nested_replies: bool = True
    max_thread_depth: int = 10
    export: ExportConfig = Field(default_factory=ExportConfig)
    api_delay: float = 0.05
    max_retries: int = 3
    retry_after_default: int = 60

    model_config = {"env_prefix": "COURSEWRIGHT_"}


class LLMConfig(BaseModel):
    """LLM API configuration for feedback generation and management."""

    enabled: bool = False
    default_provider: str = Field("openai", alias="provider")
    default_model: str = Field("gpt-4o-mini", alias="model")
    comparison_models: list[str] = Field(
        default_factory=lambda: ["gpt-4o-mini", "claude-3-5-haiku-20241022"]
    )
    api_key: str | None = None
    api_key_file: str | None = None
    temperature: float = 0.7
    max_tokens: int = 300
    budget_alerts: bool = False
    monthly_budget_usd: float = 0.0
    last_used_model: str | None = None
    last_used_provider: str | None = None
    include_score_breakdown: bool = True
    max_feedback_length: int = 75

    model_config = {"populate_by_name": True}

    @property
    def provider(self) -> str:
        """Return the active provider, preferring the last session selection."""
        return self.last_used_provider or self.default_provider

    @property
    def model(self) -> str | None:
        """Return the active model, preferring the last session selection."""
        return self.last_used_model or self.default_model

    def remember_selection(self, provider: str, model_name: str) -> None:
        """Persist the most recently selected provider and model."""
        self.last_used_provider = provider
        self.last_used_model = model_name

    def get_api_key(self) -> str:
        """Resolve the LLM API key from config, file, or environment."""
        if self.api_key:
            return self.api_key
        if self.api_key_file:
            key_file = Path(self.api_key_file)
            if key_file.exists():
                return key_file.read_text(encoding="utf-8").strip()
        active_provider = self.provider or self.default_provider
        if active_provider == "openai":
            return os.getenv("OPENAI_API_KEY", "")
        if active_provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY", "")
        return ""


class AssistantRankingWeights(BaseModel):
    """Weights that control automated discussion post ranking."""

    depth_weight: float = 0.4
    relevance_weight: float = 0.3
    originality_weight: float = 0.2
    engagement_weight: float = 0.1


class AssistantConfig(BaseModel):
    """Configuration for the instructor response assistant."""

    enabled: bool = False
    auto_prompt: bool = True
    ranking: AssistantRankingWeights = Field(default_factory=AssistantRankingWeights)
    plagiarism_threshold: float = 0.85


class CLIConfig(BaseModel):
    """Command-line and TUI display preferences and recent state."""

    default_course_id: int | None = None
    show_progress_bars: bool = True
    colored_output: bool = True
    table_style: str = "simple"
    max_table_width: int = 120
    last_analysis_file: str | None = None
    recent_courses: list[int] = Field(default_factory=list)
    recent_discussions: list[int] = Field(default_factory=list)


class TemplateConfig(BaseModel):
    """Neutral template filenames; institution overrides live in the workspace."""

    templates_directory: str = "templates"
    course_policy: str = "course_policy.md"
    discussion_rubric: str = "discussion_rubric.md"
    discussion_prompt: str = "discussion_prompt.md"
    syllabus_embed: str = "syllabus_embed.html"
    course_gameplan: str = "course_gameplan.md"
    course_structure: str = "course_structure_template.md"
    schedule_format: str = "schedule_format.txt"
    script_template: str = "script_template.py"
    attendance_warning: str = "attendance_warning.md"
    missing_submission: str = "missing_submission.md"
    missing_packet_tracer: str = "missing_packet_tracer.md"
    missing_lab_document: str = "missing_lab_document.md"
    pending_review: str = "pending_review.md"
    custom: dict[str, str] = Field(default_factory=dict)

    def resolve_filename(self, key_or_filename: str) -> str:
        """Return the configured filename for a template key or literal name."""
        if key_or_filename in self.custom:
            return self.custom[key_or_filename]
        if hasattr(self, key_or_filename) and key_or_filename not in {
            "templates_directory",
            "custom",
        }:
            attr_value = getattr(self, key_or_filename)
            if isinstance(attr_value, str):
                return attr_value
        return key_or_filename

    def resolve_path(
        self,
        key_or_filename: str,
        workspace_directory: Path | None = None,
    ) -> Path:
        """Resolve a template path, preferring the private workspace copy."""
        filename = self.resolve_filename(key_or_filename)
        if workspace_directory is not None:
            workspace_candidate = workspace_directory / self.templates_directory / filename
            if workspace_candidate.exists():
                return workspace_candidate
        return Path(self.templates_directory) / filename


class CourseDesignConfig(BaseModel):
    """Course-building and automation settings ported from COURSE_BUILDER."""

    canvas_url: str = DEFAULT_CANVAS_URL
    api_key_path: str = DEFAULT_API_TOKEN_FILE
    rate_limit_delay: float = DEFAULT_RATE_LIMIT_DELAY
    per_page: int = DEFAULT_PER_PAGE
    intro_modules: list[str] = Field(default_factory=lambda: list(DEFAULT_INTRO_MODULES))
    unit_count: int = DEFAULT_UNIT_COUNT
    discussion_units: list[int] = Field(default_factory=lambda: list(DEFAULT_DISCUSSION_UNITS))
    discussion_points: int = DEFAULT_DISCUSSION_POINTS
    date_format_input: str = SCHEDULE_DATE_FORMAT
    date_format_output: str = CANVAS_DATE_FORMAT
    content_priority: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_CONTENT_PRIORITY)
    )
    dry_run_default: bool = DEFAULT_DRY_RUN
    backup_directory: str = DEFAULT_BACKUP_DIRECTORY
    verbose: bool = False
    inject_standard_notes: bool = False
    essential_standard_threshold: int = DEFAULT_ESSENTIAL_STANDARD_THRESHOLD
    required_elements: dict[str, list[str]] = Field(
        default_factory=lambda: {
            section: list(items) for section, items in DEFAULT_REQUIRED_ELEMENTS.items()
        }
    )

    @field_validator("rate_limit_delay")
    @classmethod
    def validate_rate_limit_delay(cls, value: float) -> float:
        if value < 0:
            raise ValueError("rate_limit_delay must be >= 0")
        return value

    @field_validator("per_page")
    @classmethod
    def validate_per_page(cls, value: int) -> int:
        if value < 1 or value > 100:
            raise ValueError("per_page must be between 1 and 100")
        return value

    @field_validator("canvas_url")
    @classmethod
    def validate_canvas_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("canvas_url must start with http:// or https://")
        return value

    @field_validator("date_format_output")
    @classmethod
    def validate_date_format_output(cls, value: str) -> str:
        if value.endswith("Z"):
            raise ValueError("Canvas date_format_output must not end with 'Z'")
        return value


def _convert_enums_for_serialization(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _convert_enums_for_serialization(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_convert_enums_for_serialization(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


class OutreachConfig(BaseModel):
    """Instructor and institution contact details for attendance outreach.

    Every field is a plain string so it comes straight from the config
    file.  The neutral defaults are placeholders, never real names,
    addresses or institutions.
    """

    instructor_signature: str = "Your instructor"
    instructor_email: str = "instructor@example.edu"
    office_hours_url: str = "https://example.edu/office-hours"
    institution_name: str = "Your institution"
    attendance_policy_name: str = "Attendance Policy"
    support_services_url: str = "https://example.edu/support"
    lab_example_url: str = ""


class AppConfig(BaseModel):
    """Unified application settings covering TA and course-design workflows."""

    workspace_directory: str | None = Field(default=None, alias="workspace")
    discussion: DiscussionConfig = Field(default_factory=DiscussionConfig)
    cli: CLIConfig = Field(default_factory=CLIConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    design: CourseDesignConfig = Field(default_factory=CourseDesignConfig)
    calendar: AcademicCalendarConfig = Field(default_factory=AcademicCalendarConfig)
    templates: TemplateConfig = Field(default_factory=TemplateConfig)
    outreach: OutreachConfig = Field(default_factory=OutreachConfig)

    model_config = {"populate_by_name": True}

    @classmethod
    def load_from_file(
        cls,
        config_file: str | Path = DEFAULT_CONFIG_FILENAME,
        *,
        create_if_missing: bool = True,
    ) -> AppConfig:
        """Load configuration from a YAML file on disk."""
        config_path = Path(config_file)
        if config_path.exists():
            with config_path.open(encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError(f"{config_path}: configuration root must be a mapping")
            return cls(**data)

        config = cls()
        if create_if_missing:
            config.save_to_file(config_path)
        return config

    def save_to_file(self, config_file: str | Path = DEFAULT_CONFIG_FILENAME) -> None:
        """Save configuration to a YAML file with clean enum serialization."""
        config_path = Path(config_file)
        config_path.parent.mkdir(exist_ok=True, parents=True)

        serialized = _convert_enums_for_serialization(self.model_dump(mode="python"))
        with config_path.open("w", encoding="utf-8") as handle:
            yaml.dump(serialized, handle, default_flow_style=False, indent=2, sort_keys=False)


CoursewrightConfig = AppConfig
Settings = AppConfig
