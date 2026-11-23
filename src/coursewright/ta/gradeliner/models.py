"""
GradeLiner Models
=================
Pydantic models for assignment and student mappings.

"""

from datetime import datetime

from pydantic import BaseModel, Field


class AssignmentMapping(BaseModel):
    """
    Represents a successful match between a NetAcad export column
    and a specific Canvas assignment.
    """

    netacad_column: str
    canvas_assignment_name: str
    canvas_assignment_id: int
    canvas_points_possible: float
    netacad_points_possible: float = 100.0  # Cisco defaults to 100%
    confidence: str = "low"


class AssignmentMapCache(BaseModel):
    """
    Schema for the gradeliner_assignment_map.json file stored in the
    course export directory.
    """

    course_id: int
    canvas_course_name: str
    created_at: datetime = Field(default_factory=datetime.now)
    mappings: list[AssignmentMapping] = []
    unmapped_netacad: list[str] = []
    unmapped_canvas: list[str] = []


class StudentMapping(BaseModel):
    """
    Represents a match between a NetAcad student (by name/email)
    and a Canvas user ID.
    """

    netacad_name: str
    netacad_email: str
    canvas_user_id: int
    canvas_name: str
    canvas_login_id: str
    match_method: str = "none"
    confidence: str = "low"


class StudentMapCache(BaseModel):
    """
    Schema for the gradeliner_student_map.json file.
    """

    course_id: int
    created_at: datetime = Field(default_factory=datetime.now)
    students: list[StudentMapping] = []
    unmatched_netacad: list[str] = []
    unmatched_canvas: list[str] = []


class GradeImportRow(BaseModel):
    """
    Represents a single grade for a single student ready for preview
    or API submission.
    """

    canvas_user_id: int
    canvas_name: str
    assignment_id: int
    assignment_name: str
    netacad_score: float
    scaled_score: float
    current_canvas_score: float | None = None
