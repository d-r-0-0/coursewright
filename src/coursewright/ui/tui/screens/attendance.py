"""Attendance screen with 14-day activity lookback + warning messages.

Provides two screens:

* ``AttendanceScreen`` — traffic-light DataTable with per-student
  activity signals.  Click a red/yellow student to preview a
  warning message; press ``s`` to bulk-send warnings.
* ``WarningPreviewScreen`` — shows the rendered attendance warning
  template for a single student with a Send button.
"""

from __future__ import annotations

from datetime import datetime

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Header, Static

from coursewright.core.canvas.safe_write import SafeWriteError
from coursewright.core.config.settings import AppConfig
from coursewright.ta.attendance.outreach import (
    load_attendance_warning_template,
    render_warning_for_student,
)
from coursewright.ta.attendance.status import (
    AttendanceReport,
    AttendanceStudentRecord,
)
from coursewright.ta.attendance.templates import OutreachContact
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

# ------------------------------------------------------------------
# Outreach contact wiring
# ------------------------------------------------------------------

def _outreach_contact_from_config(config: AppConfig | None = None) -> OutreachContact:
    """Build the outreach contact from the ``outreach`` config section.

    The contact details come straight from the instructor's config
    file (``AppConfig.outreach``); with no config the neutral
    placeholder defaults are used, so no real names or addresses
    ever leak from the package itself.
    """
    config = config if config is not None else AppConfig.load_from_file()
    contact = config.outreach
    return OutreachContact(
        instructor_signature=contact.instructor_signature,
        instructor_email=contact.instructor_email,
        office_hours_url=contact.office_hours_url,
        institution_name=contact.institution_name,
        attendance_policy_name=contact.attendance_policy_name,
        support_services_url=contact.support_services_url,
        lab_example_url=contact.lab_example_url,
    )


# =====================================================================
# WarningPreviewScreen — preview + send one attendance warning
# =====================================================================


class WarningPreviewScreen(CoursewrightScreen):
    """Preview and optionally send an attendance warning to one student."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("q", "go_back", "Back", show=False, priority=True),
        Binding("s", "send_warning", "Send", show=True),
    ]

    def __init__(
        self,
        app_state: AppState,
        student_record: AttendanceStudentRecord,
        subject_line: str,
        message_body: str,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        self.student_record = student_record
        self.subject_line = subject_line
        self.message_body = message_body

    def compose(self) -> ComposeResult:
        """Show the warning message in a scrollable preview."""
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="warning-preview-scroll"):
            yield Static(
                self._build_preview_header(),
                id="warning-preview-header",
            )
            yield Static(
                f"[bold]Subject:[/bold] {self.subject_line}\n",
                id="warning-preview-subject",
            )
            yield Static(
                self.message_body,
                id="warning-preview-body",
            )
        yield HelpBar()

    def _build_preview_header(self) -> str:
        """Build a coloured header identifying the student and status."""
        status = self.student_record.attendance_status
        status_markup = (
            f"[{status}]\u25cf {status.capitalize()}[/{status}]"
        )
        return (
            "[bold cyan]Attendance Warning Preview[/bold cyan]\n"
            f"[bold]Student:[/bold] "
            f"{self.student_record.student_name}  {status_markup}\n"
            "\n[dim]Press [bold]s[/bold] to send, "
            "[bold]Esc[/bold] to go back[/dim]"
        )

    def action_send_warning(self) -> None:
        """Confirm before sending the warning message."""
        self.app.push_screen(
            ConfirmModal(
                f"Send attendance warning to "
                f"[bold]{self.student_record.student_name}[/bold]?",
                yes_label="Send",
                no_label="Cancel",
            ),
            callback=self._handle_send_confirmed,
        )

    def _handle_send_confirmed(self, confirmed: bool) -> None:
        """Dispatch the send worker if the user confirmed."""
        if confirmed:
            self._do_send_single_warning()

    @work(thread=True)
    def _do_send_single_warning(self) -> None:
        """Send the warning via Canvas Conversations in a thread.

        The user confirmed, so the send executes: the message is backed
        up to the private workspace first (AGENTS.md 3.11).  Without a
        workspace nothing is sent and the user is told why.
        """
        from coursewright.ta.attendance.outreach import (
            send_canvas_conversation_message,
        )

        course_id = (
            self.app_state.current_course.id
            if self.app_state.current_course
            else None
        )
        transmission_modal = TransmissionModal("Sending attendance warning via Canvas...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        try:
            success, detail = send_canvas_conversation_message(
                canvas_client=self.app_state.canvas_client,
                student_id=self.student_record.student_id,
                subject_line=self.subject_line,
                message_body=self.message_body,
                course_id=course_id,
                execute=True,
                backup_directory=self.app_state.backup_directory,
            )
        except SafeWriteError as not_sent:
            success, detail = False, str(not_sent)
        if success:
            self.app.call_from_thread(
                self.notify,
                f"Warning sent to {self.student_record.student_name}.",
                title="Attendance Warning",
                severity="information",
            )
            # Close the transmission log, then the preview itself.
            self.app.call_from_thread(self.app.pop_screen)
            self.app.call_from_thread(self.app.pop_screen)
        else:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Not sent: {detail}",
            )
            self.app.call_from_thread(
                self.notify,
                f"Failed to send to "
                f"{self.student_record.student_name}: {detail}",
                title="Attendance Warning",
                severity="error",
            )


# =====================================================================
# AttendanceScreen — traffic-light table + warning actions
# =====================================================================


class AttendanceScreen(CoursewrightScreen):
    """Show per-student attendance signals in a traffic-light table.

    Click a red or yellow student row to preview the warning
    message.  Press ``s`` to bulk-send warnings to all flagged
    students.
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("r", "refresh_attendance", "Refresh", show=True),
        Binding("s", "send_warnings", "Send Warnings", show=True),
    ]

    def __init__(
        self,
        app_state: AppState,
        attendance_report: AttendanceReport,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        self.attendance_report = attendance_report
        # Map DataTable RowKey → student record for click lookups
        self._student_record_by_row_key: dict = {}
        # Track students who have already received a warning this session
        self._already_warned_student_ids: set[int] = set()

    def compose(self) -> ComposeResult:
        """Build attendance summary and detailed table."""
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="attendance-scroll"):
            yield Static(self._build_summary_text(), id="attendance-summary")
            yield DataTable(
                id="attendance-table",
                cursor_type="row",
                zebra_stripes=True,
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Populate attendance rows when the screen appears."""
        self._populate_attendance_table()

    def _build_summary_text(self) -> str:
        """Build a top summary panel with aggregate status counts."""
        green_count = sum(
            1
            for student_record in self.attendance_report.student_records
            if student_record.attendance_status == "green"
        )
        yellow_count = sum(
            1
            for student_record in self.attendance_report.student_records
            if student_record.attendance_status == "yellow"
        )
        red_count = sum(
            1
            for student_record in self.attendance_report.student_records
            if student_record.attendance_status == "red"
        )

        return "\n".join(
            [
                "[bold cyan]Attendance Check[/bold cyan]",
                "",
                f"[bold]Lookback:[/bold] {self.attendance_report.lookback_days} days",
                (
                    "[bold]Generated:[/bold] "
                    f"{self.attendance_report.generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                ),
                f"[green]Green:[/green] {green_count}",
                f"[yellow]Yellow:[/yellow] {yellow_count}",
                f"[red]Red:[/red] {red_count}",
            ]
        )

    def _populate_attendance_table(self) -> None:
        """Render one row per student with attendance signals."""
        attendance_table = self.query_one("#attendance-table", DataTable)
        attendance_table.add_column("Student", width=28)
        # Total grade is the first column after the student name so the
        # instructor can see standing and attendance side-by-side.
        attendance_table.add_column("Total", width=14)
        attendance_table.add_column("Last Activity", width=22)
        attendance_table.add_column("Last Submission", width=22)
        attendance_table.add_column("Last Post", width=22)
        attendance_table.add_column("Status", width=12)

        for student_record in sorted(
            self.attendance_report.student_records,
            key=lambda attendance_record: attendance_record.student_name.lower(),
        ):
            row_key = attendance_table.add_row(
                self._truncate_student_name(student_record.student_name),
                self._format_grade(student_record.current_score),
                self._format_datetime(student_record.last_activity_at),
                self._format_datetime(student_record.last_submission_at),
                self._format_datetime(student_record.last_discussion_post_at),
                self._format_status(student_record.attendance_status),
            )
            self._student_record_by_row_key[row_key] = student_record
        attendance_table.focus()

    # ------------------------------------------------------------------
    # Row selection → preview warning for one student
    # ------------------------------------------------------------------

    def on_data_table_row_selected(
        self,
        event: DataTable.RowSelected,
    ) -> None:
        """Open a warning preview when a red or yellow student is clicked."""
        student_record = self._student_record_by_row_key.get(event.row_key)
        if student_record is None:
            return
        if student_record.attendance_status == "green":
            self.notify(
                f"{student_record.student_name} is green — "
                f"no warning needed.",
                title="Attendance",
                severity="information",
            )
            return

        self._push_warning_preview(student_record)

    def _push_warning_preview(
        self,
        student_record: AttendanceStudentRecord,
    ) -> None:
        """Render the template and push the preview screen."""
        template_text = load_attendance_warning_template()
        course_name = (
            self.app_state.current_course.name
            if self.app_state.current_course
            else "Unknown Course"
        )
        subject_line, message_body = render_warning_for_student(
            student_record=student_record,
            course_name=course_name,
            template_text=template_text,
            contact=_outreach_contact_from_config(self.app_state.config),
        )

        self.app.push_screen(
            WarningPreviewScreen(
                app_state=self.app_state,
                student_record=student_record,
                subject_line=subject_line,
                message_body=message_body,
            )
        )

    # ------------------------------------------------------------------
    # Bulk send warnings to all red + yellow students
    # ------------------------------------------------------------------

    def action_send_warnings(self) -> None:
        """Send attendance warnings to all red and yellow students."""
        flagged_records = [
            record
            for record in self.attendance_report.student_records
            if record.attendance_status in ("red", "yellow")
            and record.student_id not in self._already_warned_student_ids
        ]

        if not flagged_records:
            self.notify(
                "No unsent warnings — all flagged students "
                "have already been warned this session.",
                title="Attendance Warning",
                severity="information",
            )
            return

        red_count = sum(
            1 for r in flagged_records
            if r.attendance_status == "red"
        )
        yellow_count = len(flagged_records) - red_count

        self.app.push_screen(
            ConfirmModal(
                f"Send attendance warnings to "
                f"[bold]{len(flagged_records)}[/bold] students?\n"
                f"([red]{red_count} red[/red], "
                f"[yellow]{yellow_count} yellow[/yellow])",
                yes_label="Send All",
                no_label="Cancel",
            ),
            callback=self._handle_bulk_send_confirmed,
        )

    def _handle_bulk_send_confirmed(self, confirmed: bool) -> None:
        """Dispatch the bulk send worker if confirmed."""
        if confirmed:
            self._do_bulk_send_warnings()

    @work(thread=True)
    def _do_bulk_send_warnings(self) -> None:
        """Send warnings to all flagged students in a background thread.

        Confirmed, so every send executes with a backup first.  A send
        Canvas refuses is counted as failed and the rest carry on; a
        missing workspace or failed backup stops the run.
        """
        from coursewright.ta.attendance.outreach import (
            send_canvas_conversation_message,
        )

        template_text = load_attendance_warning_template()
        course_name = (
            self.app_state.current_course.name
            if self.app_state.current_course
            else "Unknown Course"
        )
        contact = _outreach_contact_from_config(self.app_state.config)

        flagged_records = [
            record
            for record in self.attendance_report.student_records
            if record.attendance_status in ("red", "yellow")
            and record.student_id
            not in self._already_warned_student_ids
        ]

        sent_count = 0
        failed_count = 0
        transmission_modal = TransmissionModal(
            f"Sending {len(flagged_records)} attendance warning(s) via Canvas..."
        )
        self.app.call_from_thread(self.app.push_screen, transmission_modal)

        for position, student_record in enumerate(flagged_records, start=1):
            subject_line, message_body = render_warning_for_student(
                student_record=student_record,
                course_name=course_name,
                template_text=template_text,
                contact=contact,
            )
            course_id = (
                self.app_state.current_course.id
                if self.app_state.current_course
                else None
            )
            try:
                success, _detail = send_canvas_conversation_message(
                    canvas_client=self.app_state.canvas_client,
                    student_id=student_record.student_id,
                    subject_line=subject_line,
                    message_body=message_body,
                    course_id=course_id,
                    execute=True,
                    backup_directory=self.app_state.backup_directory,
                )
            except SafeWriteError as stopped:
                self.app.call_from_thread(
                    transmission_modal.mark_error,
                    f"Stopped after {sent_count} warning(s): {stopped}",
                )
                self.app.call_from_thread(
                    self.notify,
                    f"Stopped after {sent_count} warning(s): {stopped}",
                    title="Attendance Warning",
                    severity="error",
                )
                return
            if success:
                sent_count += 1
                self._already_warned_student_ids.add(
                    student_record.student_id
                )
            else:
                failed_count += 1
            self.app.call_from_thread(
                transmission_modal.append_log_line,
                f"{position}/{len(flagged_records)}: "
                f"{student_record.student_name} ({'sent' if success else 'failed'})",
            )

        self.app.call_from_thread(self.app.pop_screen)
        summary_parts = [f"Sent {sent_count} warning(s)."]
        if failed_count:
            summary_parts.append(
                f"[red]{failed_count} failed.[/red]"
            )

        self.app.call_from_thread(
            self.notify,
            " ".join(summary_parts),
            title="Attendance Warning",
            severity="information" if not failed_count else "warning",
        )

    # ------------------------------------------------------------------
    # Refresh action
    # ------------------------------------------------------------------

    def action_refresh_attendance(self) -> None:
        """Placeholder refresh action; wired by parent workflow later."""
        self.notify(
            "Refresh attendance from the parent workflow.",
            title="Attendance",
            severity="information",
        )

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_student_name(student_name: str) -> str:
        """Keep long student names readable in fixed-width table columns."""
        if len(student_name) <= 28:
            return student_name
        return student_name[:25] + "..."

    @staticmethod
    def _format_datetime(value: datetime | None) -> str:
        """Format datetimes as MM/DD/YYYY to match standard form fields."""
        if value is None:
            return "\u2014"
        return value.strftime("%m/%d/%Y")

    @staticmethod
    def _letter_grade(current_score: float) -> str:
        """Convert a 0–100 score to a letter grade using Canvas defaults.

        Canvas default grading scale:
          A  93–100 | A- 90–92 | B+ 87–89 | B  83–86 | B- 80–82
          C+ 77–79  | C  73–76 | C- 70–72 | D+ 67–69 | D  63–66
          D- 60–62  | F  0–59
        """
        if current_score >= 93:
            return "A"
        if current_score >= 90:
            return "A-"
        if current_score >= 87:
            return "B+"
        if current_score >= 83:
            return "B"
        if current_score >= 80:
            return "B-"
        if current_score >= 77:
            return "C+"
        if current_score >= 73:
            return "C"
        if current_score >= 70:
            return "C-"
        if current_score >= 67:
            return "D+"
        if current_score >= 63:
            return "D"
        if current_score >= 60:
            return "D-"
        return "F"

    @staticmethod
    def _format_grade(current_score: float | None) -> str:
        """Render a student's overall course grade as 'XX.X% (B+)' form."""
        if current_score is None:
            return "\u2014"
        letter = AttendanceScreen._letter_grade(current_score)
        return f"{current_score:.1f}% ({letter})"

    @staticmethod
    def _format_status(status_value: str) -> str:
        """Render traffic-light statuses in colored Rich markup."""
        if status_value == "green":
            return "[green]\u25cf Green[/green]"
        if status_value == "yellow":
            return "[yellow]\u25cf Yellow[/yellow]"
        return "[red]\u25cf Red[/red]"
