"""Analysis Results Screen for the Coursewright TUI.

Displays the results of a single-discussion analysis: a summary panel
at the top (discussion title, participating students, average score,
participation rate) and a scrollable DataTable of individual student
results with columns: Student, Posts, Words, Replies, Score, Status.

A callout panel highlights students who are not meeting requirements,
showing the first few with their specific deficiencies.

Replaces: ``_display_analysis_results`` and the results portion of
``analyze_single_discussion`` from ``discussion_menu.py``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Header, Static

from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.help_bar import HelpBar


class AnalysisResultsScreen(CoursewrightScreen):
    """Display analysis results: summary + student table + callout.

    This screen is pushed by ``DiscussionListScreen`` after the
    analysis worker completes.  It receives the finished
    ``DiscussionAnalysis`` and displays everything read-only.

    Key bindings:
    - g      : Open grading options
    - p      : Switch grading profile
    - Escape : Back to discussion list
    - q      : Quit
    """

    BINDINGS = [
        Binding(
            "escape",
            "go_back",
            "Back",
            priority=True,
        ),
        Binding("g", "open_grading", "Grade", show=True),
        Binding("p", "change_profile", "Profile", show=True),
    ]

    def __init__(
        self,
        app_state: AppState,
        analysis,
        discussion,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        self.analysis = analysis
        self.discussion = discussion

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        """Build the results layout: summary, table, callout."""
        yield Header(show_clock=True)
        with VerticalScroll(id="results-scroll"):
            yield Static(
                self._build_summary_panel(),
                id="results-summary",
            )
            yield DataTable(
                id="results-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static(
                self._build_failing_students_callout(),
                id="results-failing",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Populate the student results table."""
        self._populate_results_table()

    # ------------------------------------------------------------------
    # Summary panel
    # ------------------------------------------------------------------
    def _build_summary_panel(self) -> str:
        """Rich markup for the analysis summary at the top."""
        analysis = self.analysis
        lines = [
            "[bold green]Analysis Complete[/bold green]",
            "",
            f"[bold]Discussion:[/bold] {analysis.discussion_title}",
            f"[bold]Students Analyzed:[/bold] {len(analysis.student_participation)}",
            f"[bold]Participating:[/bold] "
            f"{analysis.participating_students}/"
            f"{analysis.total_students}",
            f"[bold]Average Score:[/bold] {analysis.average_score:.1f}/100",
            f"[bold]Meeting Requirements:[/bold] "
            f"{analysis.students_meeting_requirements}/"
            f"{analysis.total_students}",
            f"[bold]Grading Profile:[/bold] {self._describe_grading_profile(analysis)}",
            f"[bold]Participation Rate:[/bold] {analysis.participation_rate:.1f}%",
        ]
        return "\n".join(lines)

    @staticmethod
    def _describe_grading_profile(analysis) -> str:
        """Name the profile that actually graded this discussion.

        Under the "auto" setting the profile is chosen per discussion, so
        showing the configured setting alone would hide which rubric the
        scores on screen came from.
        """
        profile = getattr(analysis, "grading_profile", None) or "legacy"
        reason = getattr(analysis, "profile_detection_reason", None)
        if reason:
            return f"{profile} (auto-detected: {reason})"
        return profile

    # ------------------------------------------------------------------
    # Student results table
    # ------------------------------------------------------------------
    def _populate_results_table(self) -> None:
        """Fill the DataTable with per-student results."""
        table = self.query_one("#results-table", DataTable)

        table.add_column("Student", width=25)
        table.add_column("Posts", width=7)
        table.add_column("Words", width=8)
        table.add_column("Replies", width=8)
        table.add_column("Score", width=8)
        table.add_column("Status", width=8)

        # Sort students by score descending (highest first)
        sorted_students = sorted(
            self.analysis.student_participation,
            key=lambda student: student.total_score,
            reverse=True,
        )

        for student in sorted_students:
            status_icon = "[green]Pass[/green]" if student.meets_requirements else "[red]Fail[/red]"

            # Truncate long student names for readability
            display_name = student.user_name
            if len(display_name) > 25:
                display_name = display_name[:22] + "..."

            table.add_row(
                display_name,
                str(student.total_posts),
                str(student.total_word_count),
                str(student.reply_count),
                f"{student.total_score:.1f}",
                status_icon,
            )

        table.focus()

    # ------------------------------------------------------------------
    # Navigation to grading
    # ------------------------------------------------------------------
    def action_open_grading(self) -> None:
        """Push the GradingOptionsScreen for this analysis."""
        from coursewright.ui.tui.screens.grading import (
            GradingOptionsScreen,
        )

        self.app.push_screen(
            GradingOptionsScreen(
                app_state=self.app_state,
                analysis=self.analysis,
                discussion=self.discussion,
            )
        )

    def action_change_profile(self) -> None:
        """Open the profile picker and persist the selected value."""
        from coursewright.ui.tui.widgets.profile_picker_modal import ProfilePickerModal

        def _on_profile_selected(selected_profile: str | None) -> None:
            if not selected_profile:
                return

            self.app_state.grading_profile = selected_profile
            config = self.app_state.config
            config.discussion.default_grading_profile = selected_profile
            self._persist_config_or_warn(self.app_state)
            self.query_one("#results-summary", Static).update(self._build_summary_panel())

        self.app.push_screen(
            ProfilePickerModal(getattr(self.app_state, "grading_profile", "auto")),
            callback=_on_profile_selected,
        )

    # ------------------------------------------------------------------
    # Failing students callout
    # ------------------------------------------------------------------
    def _build_failing_students_callout(self) -> str:
        """Rich markup listing students not meeting requirements."""
        failing_students = [
            student
            for student in self.analysis.student_participation
            if not student.meets_requirements
        ]

        if not failing_students:
            return "\n[bold green]All students meet requirements.[/bold green]"

        lines = [
            "",
            f"[bold yellow]{len(failing_students)} student(s) "
            "not meeting requirements:[/bold yellow]",
        ]

        # Show the first 5 failing students with their issues
        display_limit = 5
        for student in failing_students[:display_limit]:
            issues = (
                ", ".join(student.missing_requirements)
                if student.missing_requirements
                else "Unknown issues"
            )
            lines.append(f"  [yellow]- {student.user_name}:[/yellow] {issues}")

        remaining = len(failing_students) - display_limit
        if remaining > 0:
            lines.append(f"  [dim]... and {remaining} more[/dim]")

        return "\n".join(lines)
