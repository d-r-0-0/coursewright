"""GradeLiner Textual screen with a five-step import wizard.

This screen mirrors the Rich ``run_gradeliner_flow`` interaction inside
the Textual app while keeping the sequence intentionally explicit:

1. Detect/select a NetAcad CSV (via ``core.inbox``, Downloads first)
2. Approve assignment + student mappings
3. Select assignments by coverage
4. Preview grades and run smoke test
5. Confirm and submit grades to Canvas
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from coursewright.core.canvas.safe_write import SafeCanvasWriter, create_safe_canvas_writer
from coursewright.core.inbox import InboxCandidate, find_exported_file
from coursewright.ta.gradeliner.models import (
    AssignmentMapping,
    GradeImportRow,
    StudentMapping,
)
from coursewright.ta.gradeliner.orchestrator import (
    GradeLinerUpper,
    zero_ungraded_submissions,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.selection_table import SelectionRow, SelectionTable
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

NETACAD_IMPORT_DIR = Path("netacad-import")
NETACAD_IMPORT_DIR_LEGACY = Path("GradeLinErUpper")
NETACAD_EXPORT_PATTERNS = ("*.csv", "*.tsv")


class AssignmentMappingEditorScreen(CoursewrightScreen):
    """Full-screen editor for correcting GradeLiner assignment mappings."""

    BINDINGS = [
        Binding("enter", "apply_mapping", "Apply", show=True),
        Binding("m", "apply_mapping", "Apply", show=True),
        Binding("d", "done", "Done", show=True),
        Binding("escape", "done", "Done", show=False),
    ]

    def __init__(
        self,
        mappings: list[AssignmentMapping],
        netacad_columns: list[str],
        save_callback: Callable[[list[AssignmentMapping]], None],
    ) -> None:
        super().__init__()
        self._mappings = list(mappings)
        self._netacad_columns = list(netacad_columns)
        self._save_callback = save_callback
        self._selected_mapping_index = 0

    def compose(self) -> ComposeResult:
        """Build the mapping editor layout."""
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="assignment-mapping-editor-scroll"):
            yield Static(
                "[bold cyan]Assignment Mapping Editor[/bold cyan]",
                id="assignment-mapping-editor-title",
            )
            yield Static(
                "[dim]Highlight a Canvas mapping, highlight the correct "
                "NetAcad column, then press Enter or M.[/dim]",
                id="assignment-mapping-editor-help",
            )
            yield Static("[bold]Canvas assignments[/bold]")
            yield DataTable(id="editor-mapping-table", cursor_type="row")
            yield Static("[bold]NetAcad columns[/bold]")
            yield DataTable(id="editor-netacad-table", cursor_type="row")
            yield Static("", id="assignment-mapping-editor-status")
            yield Button("Done", id="assignment-mapping-editor-done")
        yield Footer()

    def on_mount(self) -> None:
        """Populate editor tables."""
        mapping_table = self.query_one("#editor-mapping-table", DataTable)
        mapping_table.add_columns("#", "Canvas Assignment", "NetAcad Column", "Confidence")
        mapping_table.focus()

        netacad_table = self.query_one("#editor-netacad-table", DataTable)
        netacad_table.add_columns("#", "NetAcad Column")

        self._refresh_mapping_table()
        self._refresh_netacad_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle editor buttons."""
        if event.button.id == "assignment-mapping-editor-done":
            self.action_done()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Remember which Canvas mapping row should receive corrections."""
        if event.data_table.id != "editor-mapping-table":
            return
        row_index = event.cursor_row
        if row_index is None:
            return
        if 0 <= row_index < len(self._mappings):
            self._selected_mapping_index = row_index

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Apply when a NetAcad row is selected; choose mapping rows otherwise."""
        if event.data_table.id == "editor-mapping-table":
            row_index = event.cursor_row
            if row_index is not None and 0 <= row_index < len(self._mappings):
                self._selected_mapping_index = row_index
            return
        if event.data_table.id == "editor-netacad-table":
            self.action_apply_mapping()

    def action_apply_mapping(self) -> None:
        """Map the highlighted Canvas assignment to the highlighted NetAcad column."""
        netacad_table = self.query_one("#editor-netacad-table", DataTable)
        netacad_index = netacad_table.cursor_row
        if netacad_index is None or netacad_index < 0:
            self._set_editor_status("[yellow]Select a NetAcad column first.[/yellow]")
            return
        if netacad_index >= len(self._netacad_columns):
            self._set_editor_status("[yellow]Select a NetAcad column first.[/yellow]")
            return

        selected_column = self._netacad_columns[netacad_index]
        current_mapping = self._mappings[self._selected_mapping_index]
        if current_mapping.netacad_column == selected_column:
            self._set_editor_status("[dim]Mapping already uses that column.[/dim]")
            return

        self._mappings[self._selected_mapping_index] = current_mapping.model_copy(
            update={
                "netacad_column": selected_column,
                "confidence": "manual",
            }
        )
        self._save_callback(list(self._mappings))
        self._refresh_mapping_table()
        self.query_one("#editor-mapping-table", DataTable).move_cursor(
            row=self._selected_mapping_index,
            scroll=False,
        )
        self._set_editor_status(f"[green]Saved mapping to {selected_column}.[/green]")

    def action_done(self) -> None:
        """Return to the GradeLiner wizard."""
        self.app.pop_screen()

    def _refresh_mapping_table(self) -> None:
        """Redraw Canvas mapping rows."""
        mapping_table = self.query_one("#editor-mapping-table", DataTable)
        mapping_table.clear(columns=False)
        for index, mapping in enumerate(self._mappings, start=1):
            mapping_table.add_row(
                str(index),
                mapping.canvas_assignment_name,
                mapping.netacad_column,
                _confidence_tag(mapping.confidence),
            )

    def _refresh_netacad_table(self) -> None:
        """Redraw NetAcad column choices."""
        netacad_table = self.query_one("#editor-netacad-table", DataTable)
        netacad_table.clear(columns=False)
        for index, column in enumerate(self._netacad_columns, start=1):
            netacad_table.add_row(str(index), column)

    def _set_editor_status(self, text: str) -> None:
        self.query_one("#assignment-mapping-editor-status", Static).update(text)


class GradeLinerScreen(CoursewrightScreen):
    """Wizard-style GradeLiner flow for the Textual TUI."""

    DEFAULT_CSS = """
    #gradeliner-scroll {
        padding: 0 1;
    }

    /* Status lines are dimmer than buttons / titles so the eye lands
       on action items first.  Layout-affecting CSS (margin, padding,
       borders, min-height) is intentionally avoided here — the test
       pilot uses an 80x24 viewport and any extra rows push buttons
       past the click region.  Cosmetic-only changes only. */
    #file-status,
    #mapping-status,
    #coverage-status,
    #smoke-status,
    #submit-status {
        height: auto;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_assignment", "Toggle", show=False),
        Binding("a", "select_all_assignments", "All", show=False),
        Binding("n", "clear_assignments", "None", show=False),
        Binding("s", "submit", "Submit", show=False),
        Binding("z", "zero_ungraded", "Zero Ungraded", show=True),
    ]

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        self._selected_file: Path | None = None
        self._orchestrator: GradeLinerUpper | None = None
        self._coverage_entries: list[dict] = []
        self._selected_assignment_ids: set[int] = set()
        self._import_rows: list[GradeImportRow] = []
        self._transmission_modal: TransmissionModal | None = None
        self._assignment_mappings: list[AssignmentMapping] = []
        self._netacad_assignment_columns: list[str] = []

    def compose(self) -> ComposeResult:
        """Build the five-step GradeLiner import wizard."""
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="gradeliner-scroll"):
            yield Static(
                "[bold cyan]NetAcad Import Wizard[/bold cyan]",
                id="wizard-title",
            )
            with Vertical(id="step-file"):
                yield Static("[bold cyan]1) NetAcad CSV[/bold cyan]")
                yield Input(
                    placeholder="Path to NetAcad CSV/TSV",
                    id="csv-path-input",
                )
                yield Button("Use Detected File", id="detect-file")
                yield Button("Initialize", id="init-orchestrator")
                yield Static("", id="file-status")

            with Vertical(id="step-mapping"):
                yield Static("[bold cyan]2) Mapping Approval[/bold cyan]")
                yield Button("Load/Generate Mappings", id="run-mapping")
                yield Button(
                    "Re-generate Mappings (clear cache)",
                    id="regen-mapping",
                    variant="warning",
                )
                yield Button(
                    "Edit Assignment Mappings",
                    id="edit-assignment-mappings",
                    disabled=True,
                )
                yield DataTable(id="assignment-mapping")
                yield DataTable(id="student-mapping")
                yield Static("", id="mapping-status")

            with Vertical(id="step-select"):
                yield Static("[bold cyan]3) Assignment Selection[/bold cyan]")
                yield Button("Load Coverage", id="load-coverage")
                yield SelectionTable([], table_id="coverage-table")
                yield Static("", id="coverage-status")

            with Vertical(id="step-preview"):
                yield Static("[bold cyan]4) Grade Preview + Smoke Test[/bold cyan]")
                yield Button("Prepare Preview", id="prepare-preview")
                yield DataTable(id="preview-table")
                yield Static("", id="smoke-status")

            with Vertical(id="step-submit"):
                yield Static("[bold cyan]5) Submit[/bold cyan]")
                yield Button("Submit to Canvas", id="submit-grades")
                yield Static("", id="submit-status")

        yield HelpBar()

    def on_mount(self) -> None:
        """Initialize empty data tables and try file auto-detection."""
        self._setup_mapping_tables()
        self._setup_preview_table()
        detected_file = _find_newest_netacad_csv()
        if detected_file is not None:
            self._selected_file = detected_file
            self.query_one("#csv-path-input", Input).value = str(detected_file)
            self._set_text(
                "#file-status",
                f"[green]Detected: {detected_file}[/green] "
                "[dim](confirm with Initialize)[/dim]",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route button presses to step handlers."""
        button_id = event.button.id
        if button_id == "detect-file":
            self._detect_file()
        elif button_id == "init-orchestrator":
            self._initialize_orchestrator()
        elif button_id == "run-mapping":
            self._run_mapping_setup()
        elif button_id == "regen-mapping":
            self._confirm_and_regenerate_mappings()
        elif button_id == "edit-assignment-mappings":
            self._open_assignment_mapping_editor()
        elif button_id == "load-coverage":
            self._load_assignment_coverage()
        elif button_id == "prepare-preview":
            self._prepare_preview()
        elif button_id == "submit-grades":
            self.action_submit()

    def _show_transmission_modal(self, status_text: str) -> None:
        """Show the shared traffic modal for long Canvas/LLM work."""
        if self._transmission_modal is not None:
            self._transmission_modal.update_status_text(status_text)
            return

        transmission_modal = TransmissionModal(status_text)
        self._transmission_modal = transmission_modal
        self.app.push_screen(transmission_modal)

    def _dismiss_transmission_modal(self) -> None:
        """Close the shared traffic modal after successful work."""
        transmission_modal = self._transmission_modal
        if transmission_modal is None:
            return

        self._transmission_modal = None
        with suppress(Exception):
            if self.app.screen is transmission_modal:
                self.app.pop_screen()

    def _mark_transmission_error(self, error_text: str) -> None:
        """Leave the shared traffic modal open with an error message."""
        transmission_modal = self._transmission_modal
        self._transmission_modal = None
        if transmission_modal is None:
            return

        with suppress(Exception):
            transmission_modal.mark_error(error_text)

    def action_toggle_assignment(self) -> None:
        """Toggle selection on the highlighted coverage row."""
        table_widget = self.query_one("#step-select SelectionTable", SelectionTable)
        if table_widget.toggle_cursor_row():
            self._selected_assignment_ids = table_widget.selected_identifiers()

    def action_select_all_assignments(self) -> None:
        """Select all assignments in the coverage table."""
        table_widget = self.query_one("#step-select SelectionTable", SelectionTable)
        table_widget.select_all()
        self._selected_assignment_ids = table_widget.selected_identifiers()

    def action_clear_assignments(self) -> None:
        """Clear all selected assignments in the coverage table."""
        table_widget = self.query_one("#step-select SelectionTable", SelectionTable)
        table_widget.clear_all()
        self._selected_assignment_ids = set()

    def action_zero_ungraded(self) -> None:
        """Prompt for confirmation then zero out ungraded submissions for
        the currently selected assignments."""
        if not self._selected_assignment_ids:
            self.app.notify(
                "Select at least one assignment first (Space / a).",
                severity="warning",
            )
            return

        count = len(self._selected_assignment_ids)
        prompt = (
            f"Zero out [bold]{count}[/bold] assignment(s) for all ungraded submissions?\n"
            "[dim]Only submissions with score = None will be updated. "
            "A timestamped JSON backup will be written before any grade is sent.[/dim]"
        )

        def _handle_confirmed(confirmed: bool) -> None:
            if confirmed:
                self._zero_ungraded_worker(execute=True)

        self.app.push_screen(
            ConfirmModal(prompt, yes_label="Zero them", no_label="Cancel"),
            callback=_handle_confirmed,
        )

    @work(thread=True, exclusive=True)
    def _zero_ungraded_worker(self, *, execute: bool = False) -> None:
        """Background worker: zero out ungraded submissions via SafeCanvasWriter."""
        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course

        if canvas_client is None or current_course is None:
            self.app.call_from_thread(
                self.app.notify,
                "Canvas client not available.",
                severity="error",
            )
            return

        assignment_ids = sorted(self._selected_assignment_ids)
        self.app.call_from_thread(
            self.app.notify,
            f"Zeroing ungraded submissions for {len(assignment_ids)} assignment(s)…",
        )
        transmission_modal = TransmissionModal("Zeroing ungraded submissions in Canvas...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)

        try:
            writer: SafeCanvasWriter = create_safe_canvas_writer(
                canvas_client,
                execute=execute,
                workspace_directory=self.app_state.workspace_directory,
            )
            total_zeroed, assignments_touched = zero_ungraded_submissions(
                canvas_client,
                writer,
                current_course.id,
                assignment_ids,
                execute=execute,
                on_assignment_start=lambda index, total, assignment_id: (
                    self.app.call_from_thread(
                        transmission_modal.update_status_text,
                        f"Checking assignment {index}/{total} (id={assignment_id})...",
                    )
                ),
                on_assignment_done=lambda assignment_id, zeroed_in_assignment: (
                    self.app.call_from_thread(
                        transmission_modal.append_log_line,
                        f"Assignment {assignment_id}: {zeroed_in_assignment} submission(s) zeroed",
                    )
                ),
            )
        except Exception as zero_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Zero ungraded failed: {zero_error}",
            )
            self.app.call_from_thread(
                self.app.notify,
                f"Zero ungraded failed: {zero_error}",
                severity="error",
            )
            return

        if execute:
            message = (
                f"✅ Zeroed [bold]{total_zeroed}[/bold] ungraded submission(s) "
                f"across {assignments_touched} assignment(s)."
            )
        else:
            message = (
                f"Dry run: would zero [bold]{total_zeroed}[/bold] ungraded submission(s) "
                f"across {assignments_touched} assignment(s)."
            )
        self.app.call_from_thread(self.app.notify, message, title="Zero Ungraded")
        self.app.call_from_thread(self.app.pop_screen)

    def action_submit(self) -> None:
        """Confirm and submit prepared grades if preview rows are available."""
        if self._orchestrator is None:
            self._set_text("#submit-status", "[red]Initialize first.[/red]")
            return
        if not self._import_rows:
            self._set_text("#submit-status", "[yellow]No preview rows yet.[/yellow]")
            return

        row_count = len(self._import_rows)
        course_name = getattr(self._orchestrator, "course_name", "Canvas")
        prompt = (
            f"Submit [bold]{row_count}[/bold] grade(s) to Canvas for "
            f"[bold]{course_name}[/bold]?\n"
            "[dim]A timestamped JSON backup will be written before any grade is sent.[/dim]"
        )

        def _handle_confirmed(confirmed: bool) -> None:
            if not confirmed:
                self._set_text("#submit-status", "[yellow]Submission cancelled.[/yellow]")
                return
            submit_button = self.query_one("#submit-grades", Button)
            submit_button.disabled = True
            self._submit_grades_worker()

        self.app.push_screen(
            ConfirmModal(
                prompt,
                yes_label=f"Submit {row_count} grade(s)",
                no_label="Cancel",
            ),
            callback=_handle_confirmed,
        )

    @work(thread=True, exclusive=True)
    def _submit_grades_worker(self) -> None:
        """Run grade submission in a background thread with a spinner.

        Fires a per-student progress update to ``#submit-status`` via
        ``call_from_thread`` so the UI stays responsive and the user
        can see "Submitting 3/10: Alice..." rather than a frozen screen.
        """
        row_count = len(self._import_rows)
        transmission_modal = TransmissionModal(f"Submitting {row_count} grades to Canvas...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        self.app.call_from_thread(
            self._set_text,
            "#submit-status",
            f"[bold cyan]⏳ Submitting {row_count} grades to Canvas…[/bold cyan]",
        )

        def _on_row_progress(done: int, total: int, name: str) -> None:
            """Called by the orchestrator after each student is submitted."""
            self.app.call_from_thread(
                transmission_modal.update_status_text,
                f"Submitting {done}/{total}: {name}...",
            )
            self.app.call_from_thread(
                self._set_text,
                "#submit-status",
                f"[cyan]Submitting {done}/{total}: {name}…[/cyan]",
            )

        try:
            result = self._orchestrator.submit_grades(  # type: ignore[union-attr]
                self._import_rows,
                progress_callback=_on_row_progress,
                execute=True,
            )
        except Exception as submit_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Submission failed: {submit_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#submit-status",
                f"[red]Submission failed: {submit_error}[/red]",
            )
            self.app.call_from_thread(
                setattr,
                self.query_one("#submit-grades", Button),
                "disabled",
                False,
            )
            return

        successful = result.get("successful", 0)
        failed = result.get("failed", 0)
        self.app.call_from_thread(
            transmission_modal.append_log_line,
            f"POST /submissions -> success={successful}, failed={failed}",
        )

        self.app.call_from_thread(
            self._set_text,
            "#submit-status",
            f"[green]✅ Submitted[/green] {successful} successful, [red]{failed} failed[/red]",
        )
        self.app.call_from_thread(
            setattr,
            self.query_one("#submit-grades", Button),
            "disabled",
            False,
        )
        self.app.call_from_thread(self.app.pop_screen)

    def _detect_file(self) -> None:
        detected = _find_newest_netacad_csv()
        if detected is None:
            self._set_text("#file-status", "[yellow]No CSV/TSV found.[/yellow]")
            return
        self._selected_file = detected
        self.query_one("#csv-path-input", Input).value = str(detected)
        self._set_text(
            "#file-status",
            f"[green]Detected: {detected}[/green] [dim](confirm with Initialize)[/dim]",
        )

    def _initialize_orchestrator(self) -> None:
        csv_path_value = self.query_one("#csv-path-input", Input).value.strip()
        if not csv_path_value:
            self._set_text("#file-status", "[red]Enter a file path first.[/red]")
            return

        selected_path = Path(csv_path_value).expanduser()
        if not selected_path.exists():
            self._set_text("#file-status", f"[red]File not found: {selected_path}[/red]")
            return

        self._selected_file = selected_path
        current_course = self.app_state.current_course
        canvas_client = self.app_state.canvas_client

        if current_course is None or canvas_client is None:
            self._set_text("#file-status", "[red]Missing course/client context.[/red]")
            return

        self._set_text(
            "#file-status",
            "[bold cyan]⏳ Initializing NetAcad Import from Canvas…[/bold cyan]",
        )
        self._show_transmission_modal("Initializing NetAcad Import from Canvas…")
        self._initialize_orchestrator_worker(selected_path)

    @work(thread=True, exclusive=True)
    def _initialize_orchestrator_worker(self, selected_path: Path) -> None:
        """Create the orchestrator while Canvas course metadata loads."""
        current_course = self.app_state.current_course
        canvas_client = self.app_state.canvas_client
        try:
            orchestrator = GradeLinerUpper(
                canvas_client,
                self.app_state.llm_client,
                current_course.id,
                selected_path,
            )
        except Exception as init_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"NetAcad Import initialization failed: {init_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#file-status",
                f"[red]Initialization failed: {init_error}[/red]",
            )
            return

        self._orchestrator = orchestrator
        self.app.call_from_thread(
            self._set_text,
            "#file-status",
            f"[green]Initialized for {orchestrator.course_name}[/green]",
        )
        self.app.call_from_thread(self._dismiss_transmission_modal)

    def _run_mapping_setup(self) -> None:
        if self._orchestrator is None:
            self._set_text("#mapping-status", "[red]Initialize first.[/red]")
            return

        if self._orchestrator.has_cached_mappings():
            assignment_cache = self._orchestrator.mapper.load_assignment_cache()
            student_cache = self._orchestrator.mapper.load_student_cache()
            self._remember_netacad_assignment_options(
                assignment_cache.mappings if assignment_cache else []
            )
            if assignment_cache:
                self._render_assignment_mapping_table(assignment_cache.mappings)
            if student_cache:
                self._render_student_mapping_table(student_cache.students)
            self._set_text(
                "#mapping-status",
                "[green]Using cached mappings from previous run.[/green] "
                '[dim]Use "Re-generate" to rebuild with the LLM.[/dim]',
            )
            return

        if self._orchestrator.llm_client is None:
            self._set_text(
                "#mapping-status",
                "[red]LLM client required to generate new mappings.[/red]",
            )
            return

        self._run_mapping_worker()

    def _confirm_and_regenerate_mappings(self) -> None:
        """Ask for confirmation then clear the cache and re-run LLM mapping."""
        if self._orchestrator is None:
            self._set_text("#mapping-status", "[red]Initialize first.[/red]")
            return

        if self._orchestrator.llm_client is None:
            self._set_text(
                "#mapping-status",
                "[red]LLM client required for re-generation. Select a model first.[/red]",
            )
            return

        def _handle_confirmed(confirmed: bool) -> None:
            if confirmed:
                self._orchestrator.mapper.assignment_cache_path.unlink(missing_ok=True)  # type: ignore[union-attr]
                self._orchestrator.mapper.student_cache_path.unlink(missing_ok=True)  # type: ignore[union-attr]
                self._run_mapping_worker()

        self.app.push_screen(
            ConfirmModal(
                "Clear cached mappings and re-run LLM matching?\n"
                "[dim]This will cost LLM tokens and may take a moment.[/dim]",
                yes_label="Re-generate",
                no_label="Cancel",
            ),
            callback=_handle_confirmed,
        )

    @work(thread=True, exclusive=True)
    def _run_mapping_worker(self) -> None:
        """Generate assignment + student mappings via LLM in a background thread."""
        transmission_modal = TransmissionModal("Asking LLM for assignment mappings...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        self.app.call_from_thread(
            self._set_text,
            "#mapping-status",
            "[bold cyan]⏳ Generating mappings via LLM…[/bold cyan]",
        )
        try:
            assignment_proposals = (
                self._orchestrator.get_assignment_mapping_proposals()  # type: ignore[union-attr]
            )
            student_proposals = (
                self._orchestrator.get_student_mapping_proposals()  # type: ignore[union-attr]
            )
            self._remember_netacad_assignment_options(assignment_proposals)
            if not assignment_proposals:
                self.app.call_from_thread(
                    transmission_modal.mark_error,
                    "LLM returned zero assignment mappings.",
                )
                self.app.call_from_thread(
                    self._set_text,
                    "#mapping-status",
                    "[red]LLM returned zero assignment mappings. "
                    "Cache not saved — try again or check the model.[/red]",
                )
                return
            self._orchestrator.save_assignment_mappings(assignment_proposals)  # type: ignore[union-attr]
            self._orchestrator.save_student_mappings(student_proposals)  # type: ignore[union-attr]
            self.app.call_from_thread(
                transmission_modal.append_log_line,
                f"Mapped {len(assignment_proposals)} assignments and "
                f"{len(student_proposals)} students.",
            )
        except Exception as llm_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"LLM mapping failed: {llm_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#mapping-status",
                f"[red]Mapping failed: {llm_error}[/red]",
            )
            return

        self.app.call_from_thread(self._render_assignment_mapping_table, assignment_proposals)
        self.app.call_from_thread(self._render_student_mapping_table, student_proposals)
        self.app.call_from_thread(
            self._set_text,
            "#mapping-status",
            "[green]✅ Mappings generated and saved.[/green]",
        )
        self.app.call_from_thread(self.app.pop_screen)

    def _remember_netacad_assignment_options(
        self,
        mappings: list[AssignmentMapping],
    ) -> None:
        """Cache NetAcad score columns for the mapping correction editor."""
        discovered_columns: list[str] = []
        if self._orchestrator is not None:
            try:
                candidate_columns = self._orchestrator.get_netacad_assignment_columns()
            except Exception:
                candidate_columns = []
            if isinstance(candidate_columns, list):
                discovered_columns = [str(column) for column in candidate_columns]

        mapped_columns = [mapping.netacad_column for mapping in mappings]
        self._netacad_assignment_columns = list(
            dict.fromkeys([*discovered_columns, *mapped_columns])
        )

    def _open_assignment_mapping_editor(self) -> None:
        """Open the full-screen assignment mapping editor."""
        if not self._assignment_mappings:
            self._set_text("#mapping-status", "[yellow]Load mappings first.[/yellow]")
            return
        if not self._netacad_assignment_columns:
            self._remember_netacad_assignment_options(self._assignment_mappings)
        if not self._netacad_assignment_columns:
            self._set_text(
                "#mapping-status",
                "[yellow]No NetAcad assignment columns found.[/yellow]",
            )
            return

        self.app.push_screen(
            AssignmentMappingEditorScreen(
                mappings=self._assignment_mappings,
                netacad_columns=self._netacad_assignment_columns,
                save_callback=self._save_assignment_mappings_from_editor,
            )
        )

    def _save_assignment_mappings_from_editor(
        self,
        mappings: list[AssignmentMapping],
    ) -> None:
        """Persist mappings changed in the full-screen editor."""
        self._assignment_mappings = list(mappings)
        self._orchestrator.save_assignment_mappings(self._assignment_mappings)  # type: ignore[union-attr]
        self._render_assignment_mapping_table(self._assignment_mappings)
        self.query_one("#edit-assignment-mappings", Button).disabled = False
        if self._coverage_entries:
            self._set_text(
                "#coverage-status",
                "[yellow]Mapping saved. Reload coverage to refresh scores.[/yellow]",
            )
        self._set_text(
            "#mapping-status",
            "[green]Manual assignment mapping saved.[/green]",
        )

    def _refresh_loaded_coverage_from_current_mappings(self) -> None:
        """Refresh coverage rows when the mapping table is already loaded."""
        if not self._coverage_entries:
            return
        try:
            coverage_entries = (
                self._orchestrator.summarize_assignment_coverage_for_mappings(  # type: ignore[union-attr]
                    self._assignment_mappings
                )
            )
        except Exception as coverage_error:
            self._set_text(
                "#coverage-status",
                f"[yellow]Mapping saved; reload coverage to refresh: {coverage_error}[/yellow]",
            )
            return

        selected_assignment_ids = set(self._selected_assignment_ids)
        self._coverage_entries = coverage_entries
        self._install_coverage_selection_table(
            self._coverage_selection_rows(
                coverage_entries,
                selected_assignment_ids=selected_assignment_ids,
            )
        )

    def _load_assignment_coverage(self) -> None:
        if self._orchestrator is None:
            self._set_text("#coverage-status", "[red]Initialize first.[/red]")
            return

        self._set_text(
            "#coverage-status",
            "[bold cyan]⏳ Loading coverage from Canvas…[/bold cyan]",
        )
        self._show_transmission_modal("Loading assignment coverage from Canvas...")
        self._load_assignment_coverage_worker()

    @work(thread=True, exclusive=True)
    def _load_assignment_coverage_worker(self) -> None:
        """Fetch live Canvas assignment names and build coverage rows."""
        try:
            coverage_entries = self._orchestrator.summarize_assignment_coverage()  # type: ignore[union-attr]
        except Exception as coverage_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"Coverage load failed: {coverage_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#coverage-status",
                f"[red]Coverage load failed: {coverage_error}[/red]",
            )
            return

        self._coverage_entries = coverage_entries
        self.app.call_from_thread(
            self._install_coverage_selection_table,
            self._coverage_selection_rows(coverage_entries),
        )
        self.app.call_from_thread(self._dismiss_transmission_modal)

    def _coverage_selection_rows(
        self,
        coverage_entries: list[dict],
        selected_assignment_ids: set[int] | None = None,
    ) -> list[SelectionRow]:
        """Build selectable coverage rows from assignment summaries."""
        selection_rows: list[SelectionRow] = []
        for entry in coverage_entries:
            assignment_id = entry["canvas_assignment_id"]
            if selected_assignment_ids is None:
                default_selected = entry["students_with_scores"] > 0
            else:
                default_selected = assignment_id in selected_assignment_ids
            selection_rows.append(
                SelectionRow(
                    row_identifier=assignment_id,
                    title=entry["canvas_assignment_name"],
                    detail=entry["netacad_column"],
                    metric=(
                        f"{entry['students_with_scores']}/"
                        f"{entry['total_students']} "
                        f"({entry['score_percentage']:.0f}%)"
                    ),
                    selected=default_selected,
                )
            )
        return selection_rows

    def _install_coverage_selection_table(
        self,
        selection_rows: list[SelectionRow],
    ) -> None:
        """Replace the coverage selection table on the UI thread."""
        old_widget = self.query_one("#step-select SelectionTable", SelectionTable)
        new_widget = SelectionTable(selection_rows, table_id="coverage-table")
        old_widget.remove()
        self.query_one("#step-select", Vertical).mount(new_widget, before="#coverage-status")

        self._selected_assignment_ids = new_widget.selected_identifiers()
        self._set_text(
            "#coverage-status",
            f"[green]Loaded {len(selection_rows)} mapped assignments.[/green]",
        )

    def _prepare_preview(self) -> None:
        if self._orchestrator is None:
            self._set_text("#smoke-status", "[red]Initialize first.[/red]")
            return

        if not self._selected_assignment_ids:
            self._set_text("#smoke-status", "[yellow]No assignments selected.[/yellow]")
            return

        self._set_text(
            "#smoke-status",
            "[bold cyan]⏳ Preparing preview from Canvas…[/bold cyan]",
        )
        self._show_transmission_modal("Preparing grade preview from Canvas...")
        self._prepare_preview_worker(set(self._selected_assignment_ids))

    @work(thread=True, exclusive=True)
    def _prepare_preview_worker(self, selected_assignment_ids: set[int]) -> None:
        """Build preview rows while checking existing Canvas grades."""
        try:
            import_rows = self._orchestrator.prepare_grade_import(  # type: ignore[union-attr]
                selected_assignment_ids=selected_assignment_ids,
            )
            all_scores = [row.scaled_score for row in import_rows]
            is_passed, message = self._orchestrator.validator.perform_smoke_tests(  # type: ignore[union-attr]
                all_scores
            )
        except Exception as preview_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"Preview failed: {preview_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#smoke-status",
                f"[red]Preview failed: {preview_error}[/red]",
            )
            return

        self._import_rows = import_rows
        self.app.call_from_thread(self._render_preview_rows, import_rows)
        status_color = "green" if is_passed else "yellow"
        self.app.call_from_thread(
            self._set_text,
            "#smoke-status",
            f"[{status_color}]{message}[/{status_color}]",
        )
        self.app.call_from_thread(self._dismiss_transmission_modal)

    def _setup_mapping_tables(self) -> None:
        assignment_table = self.query_one("#assignment-mapping", DataTable)
        assignment_table.add_columns("NetAcad Column", "Canvas Assignment", "Confidence")

        student_table = self.query_one("#student-mapping", DataTable)
        student_table.add_columns("NetAcad Name", "Canvas Name", "Match", "Confidence")

    def _setup_preview_table(self) -> None:
        preview_table = self.query_one("#preview-table", DataTable)
        preview_table.add_columns("Student", "Assignment", "NetAcad %", "Canvas Points")

    def _render_assignment_mapping_table(
        self,
        mappings: list[AssignmentMapping],
    ) -> None:
        table = self.query_one("#assignment-mapping", DataTable)
        table.clear(columns=False)
        for mapping in mappings:
            confidence_tag = _confidence_tag(mapping.confidence)
            table.add_row(
                mapping.netacad_column,
                mapping.canvas_assignment_name,
                confidence_tag,
            )
        self._assignment_mappings = list(mappings)
        self.query_one("#edit-assignment-mappings", Button).disabled = not bool(mappings)

    def _render_student_mapping_table(
        self,
        mappings: list[StudentMapping],
    ) -> None:
        table = self.query_one("#student-mapping", DataTable)
        table.clear(columns=False)
        for mapping in mappings:
            confidence_tag = _confidence_tag(mapping.confidence)
            table.add_row(
                mapping.netacad_name,
                mapping.canvas_name,
                mapping.match_method,
                confidence_tag,
            )

    def _render_preview_rows(self, import_rows: list[GradeImportRow]) -> None:
        table = self.query_one("#preview-table", DataTable)
        table.clear(columns=False)
        for import_row in import_rows[:20]:
            table.add_row(
                import_row.canvas_name,
                import_row.assignment_name,
                f"{import_row.netacad_score}",
                f"{import_row.scaled_score:.1f}",
            )

    def _set_text(self, selector: str, text: str) -> None:
        self.query_one(selector, Static).update(text)


def _find_netacad_candidate() -> InboxCandidate | None:
    """Find the newest NetAcad CSV/TSV export via ``core.inbox`` (Downloads first)."""
    candidates = [
        candidate
        for pattern in NETACAD_EXPORT_PATTERNS
        if (
            candidate := find_exported_file(
                pattern, workspace_import_directory=NETACAD_IMPORT_DIR
            )
        )
        is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (candidate.modified_at, candidate.path.name))


def _find_newest_netacad_csv() -> Path | None:
    """Find the newest NetAcad export in Downloads, netacad-import/, or legacy folder."""
    candidate = _find_netacad_candidate()
    if candidate is not None:
        return candidate.path

    for search_dir in (NETACAD_IMPORT_DIR, NETACAD_IMPORT_DIR_LEGACY):
        if not search_dir.is_dir():
            continue
        csv_files = [
            path
            for pattern in NETACAD_EXPORT_PATTERNS
            for path in search_dir.glob(pattern)
        ]
        if csv_files:
            csv_files.sort(key=lambda file_path: file_path.stat().st_mtime, reverse=True)
            return csv_files[0]

    return None


def _confidence_tag(confidence_level: str) -> str:
    """Format confidence level with Rich color markup."""
    level = confidence_level.lower().strip()
    if level == "high":
        return "[green]high[/green]"
    if level == "medium":
        return "[yellow]medium[/yellow]"
    if level == "low":
        return "[red]low[/red]"
    return confidence_level
