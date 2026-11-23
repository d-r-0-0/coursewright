"""Textual screens for the Coursewright TUI.

Only the shell screens live here so far: start-up, the course list and
the pickers.  The workflow screens (discussions, grading, attendance,
and so on) are added by their own cards and are found by name from
``course_list.TOOL_SCREEN_LOCATIONS``.
"""

from coursewright.ui.tui.screens.course_list import CourseListScreen
from coursewright.ui.tui.screens.init_screen import InitScreen
from coursewright.ui.tui.screens.model_picker import ModelPickerScreen
from coursewright.ui.tui.screens.semester_picker import SemesterPickerScreen
from coursewright.ui.tui.screens.status_picker import StatusPickerScreen

__all__ = [
    "CourseListScreen",
    "InitScreen",
    "ModelPickerScreen",
    "SemesterPickerScreen",
    "StatusPickerScreen",
]
