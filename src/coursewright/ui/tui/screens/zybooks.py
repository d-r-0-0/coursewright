"""Five-step, file-only zyBooks wizard patterned after GradeLiner."""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    RichLog,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)

from coursewright.core.inbox import (
    ZYBOOKS_REPORT_FILENAME_PATTERN,
    CandidateLocation,
    InboxCandidate,
    default_download_directory,
    find_exported_file,
)
from coursewright.ta.zybooks.importer import (
    DEFAULT_LABS_REQUIRED,
    CsvImportCoordinator,
    CsvSnapshot,
    ImportProblem,
    MappingCache,
    MappingPlan,
    Preview,
    UnitMapping,
    format_sections,
    parse_sections,
    read_snapshot,
    value,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

if TYPE_CHECKING:
    from coursewright.ui.tui.state import AppState


def describe_send_confirmation(selected_rows, course_name: str) -> tuple[str, str]:
    """Build the single send-confirmation prompt and its confirm-button label.

    Every Ready preview row is exactly one of three kinds of change:

    * a zero or a decrease (``row.risky``) — the dangerous kind, called out loudly;
    * a new grade for a currently ungraded submission (``old_points is None``);
    * a raise over the grade Canvas already holds.

    The prompt is Rich markup (``ConfirmModal`` renders it with a ``Label``), so
    the course name is escaped in case it contains square brackets.
    """
    risky_row_count = sum(row.risky for row in selected_rows)
    new_grade_count = sum(not row.risky and row.old_points is None for row in selected_rows)
    raised_grade_count = len(selected_rows) - risky_row_count - new_grade_count
    row_count = len(selected_rows)
    row_word = "row" if row_count == 1 else "rows"
    prompt_lines = [
        f"[bold]Send {row_count} grade {row_word} to Canvas for {escape(course_name)}?[/bold]",
        "",
        f"{new_grade_count} new (ungraded now) · {raised_grade_count} raised · "
        f"{risky_row_count} zero/decrease",
    ]
    if risky_row_count:
        subject = "row is" if risky_row_count == 1 else "rows are"
        pronoun = "it" if risky_row_count == 1 else "them"
        prompt_lines += [
            "",
            f"[bold red]⚠ {risky_row_count} selected {subject} a zero or lower than "
            f"the current Canvas grade. Sending approves {pronoun}.[/bold red]",
        ]
    prompt_lines += [
        "",
        "Canvas grades are rechecked right before sending; if anything changed, "
        "nothing is sent. No automatic retries.",
    ]
    confirm_label = (
        f"Send, including {risky_row_count} zeros/decreases"
        if risky_row_count
        else f"Send {row_count} {row_word}"
    )
    return "\n".join(prompt_lines), confirm_label


class ZyBooksScreen(CoursewrightScreen):
    """Inspect, map, select, preview, then explicitly submit approved rows."""

    DEFAULT_CSS = """
    ZyBooksScreen #zybooks-body { padding: 0 1; height: 1fr; }
    ZyBooksScreen .wizard-step { height: auto; }
    ZyBooksScreen TabbedContent, ZyBooksScreen ContentSwitcher { height: auto; }
    ZyBooksScreen TabPane { padding: 0; }
    ZyBooksScreen Static { height: auto; }
    ZyBooksScreen DataTable { height: 9; margin: 1 0; }
    ZyBooksScreen #unit-table { height: 5; margin: 0; }
    ZyBooksScreen #mapping-notes { max-height: 6; overflow-y: auto; }
    ZyBooksScreen Select > SelectOverlay { max-height: 8; }
    ZyBooksScreen SelectCurrent { height: 3; }
    ZyBooksScreen SelectCurrent Static#label { height: 1; }
    ZyBooksScreen Checkbox { width: 1fr; height: auto; }
    ZyBooksScreen SelectionList { height: 9; margin: 1 0; }
    ZyBooksScreen #submission-log { height: 12; border: round $primary; margin: 1 0; }
    ZyBooksScreen Horizontal { height: auto; }
    ZyBooksScreen Button { margin: 0 1 1 0; }
    ZyBooksScreen #wizard-status { color: $text-muted; max-height: 3; overflow-y: auto; }
    ZyBooksScreen #work-indicator { height: 1; }
    ZyBooksScreen #calculation { border: round $primary; padding: 0 1; }
    ZyBooksScreen #action-bar { height: auto; padding: 0 1; }
    ZyBooksScreen #action-bar Button { margin: 0 1 0 0; }
    ZyBooksScreen #unit-editor { border: round $primary; padding: 0 1; height: auto; }
    ZyBooksScreen #unit-todo { color: $warning; max-height: 4; overflow-y: auto; }
    ZyBooksScreen #student-coverage { height: 2; }
    ZyBooksScreen #suggest-mappings { height: 1; border: none; margin: 0 0 1 0; }
    """
    # Step 5 has no primary action: it is the read-only results/log view that
    # appears after the single send confirmation on step 4.
    STEP_ACTIONS = {
        1: "load-mappings",
        2: "approve-mappings",
        3: "build-preview",
        4: "send-rows",
    }
    BINDINGS = [
        Binding("escape", "previous_step", "Back", priority=False),
        Binding("q", "request_quit", "Quit", priority=False),
    ]

    def __init__(
        self,
        app_state: AppState,
        import_directory: Path | None = None,
        cache: MappingCache | None = None,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        # When omitted, core.inbox searches Downloads first and then the
        # workspace import folder.
        self.import_directory = import_directory
        self.cache = cache
        self.suggested_candidate: InboxCandidate | None = None
        self.snapshot: CsvSnapshot | None = None
        self.coordinator: CsvImportCoordinator | None = None
        self.plan: MappingPlan | None = None
        self.preview: Preview | None = None
        self.step = 1
        self._busy = False
        self._submitted = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("[bold cyan]zyBooks Import[/bold cyan]", id="wizard-title")
        course = self.app_state.current_course
        yield Static(Text(f"Canvas course: {value(course, 'name')} (ID {value(course, 'id')})"))
        yield Static("", id="step-title")
        initial_directory = self.import_directory or default_download_directory()
        with VerticalScroll(id="zybooks-body"):
            with Vertical(id="step-1", classes="wizard-step"):
                yield Input(
                    str(initial_directory) + os.sep, placeholder="CSV path", id="file-path"
                )
                with Horizontal():
                    yield Button("Suggest newest CSV", id="detect-file")
                    yield Button("Inspect CSV", id="inspect-file", variant="primary")
                yield Static("", id="file-info")
                yield Static("Book name / ID (may be shared by multiple Canvas courses)")
                yield Input(placeholder="Confirmed book name / ID", id="book-id")
                yield Checkbox(
                    "This book includes students from the Canvas course shown above",
                    id="pair-confirm",
                )
            with Vertical(id="step-2", classes="wizard-step"):
                yield Static("", id="student-coverage")
                with TabbedContent(initial="unit-mappings", id="mapping-tabs"):
                    with TabPane("Units", id="unit-mappings"):
                        yield Button("Suggest mappings with AI", id="suggest-mappings")
                        with Collapsible(title="AI reasoning", collapsed=True):
                            yield Static("", id="mapping-notes")
                        yield DataTable(id="unit-table", cursor_type="row")
                        yield Static("", id="unit-todo")
                        with Vertical(id="unit-editor"):
                            yield Static(
                                "[bold]Edit one unit[/bold] "
                                "— switch here to work on another table row"
                            )
                            yield Select(
                                [("New unit", 0)], value=0, allow_blank=False, id="unit-choice"
                            )
                            yield Static("Unit label (shown in this wizard only)")
                            yield Input(
                                placeholder="Unit name (Canvas unit may differ from CSV chapter)",
                                id="unit-name",
                            )
                            yield Static("CSV sections that belong to this unit")
                            yield Input(
                                placeholder="Sections: 2.1-2.24 or 2.1,2.3,2.12", id="unit-sections"
                            )
                            yield Static("Activity target (half participation, half challenges)")
                            yield Select(
                                [("No activity target", 0)],
                                value=0,
                                allow_blank=False,
                                id="activity-target",
                            )
                            yield Static("Lab target (leave empty for activity-only units)")
                            yield Select(
                                [("No lab target", 0)], value=0, allow_blank=False, id="lab-target"
                            )
                            with Vertical(id="lab-review", classes="wizard-step"):
                                yield Static("", id="lab-summary")
                                yield SelectionList[str](id="unit-labs")
                                yield Checkbox(
                                    "I confirm each checked section is one separate lab",
                                    id="distinct-labs",
                                )
                            with Horizontal():
                                yield Button("Save unit", id="save-unit")
                                yield Button("Remove unit", id="remove-unit")
                    with TabPane("Students", id="student-mappings"):
                        yield Checkbox("Show unmatched CSV students", id="show-unmatched")
                        yield DataTable(id="student-table", cursor_type="row")
                        yield Select([], prompt="Source student", id="source-student")
                        yield Select(
                            [("Unmatched — skip", 0)],
                            value=0,
                            allow_blank=False,
                            id="canvas-student",
                        )
                        yield Button("Apply student correction", id="save-student")
            with Vertical(id="step-3", classes="wizard-step"):
                yield Static(
                    "Each Canvas assignment below is scored from the listed CSV grades. "
                    "Uncheck any you do not want to grade now."
                )
                yield SelectionList[int](id="assignment-selection")
                yield Checkbox(
                    "Also lower existing grades (raises always apply)", id="include-existing"
                )
                yield Static("", id="coverage-info")
            with Vertical(id="step-4", classes="wizard-step"):
                yield Static("", id="preview-info")
                yield DataTable(id="preview-table", cursor_type="row")
                yield Static("Highlight a row to see its calculation.", id="calculation")
                yield SelectionList[str](id="row-selection")
                with Horizontal():
                    yield Button("Select all ready rows", id="select-ready")
                    yield Button("Clear rows", id="clear-rows")
            with Vertical(id="step-5", classes="wizard-step"):
                yield Static("", id="submission-info")
                yield RichLog(id="submission-log", markup=True, wrap=True)
                yield DataTable(id="result-table", cursor_type="row")
        yield LoadingIndicator(id="work-indicator")
        yield Static("", id="wizard-status")
        # The step's primary action stays outside the scrolling body so it is
        # never pushed off a short terminal by a long mapping editor.
        with Horizontal(id="action-bar"):
            yield Button("Back", id="previous-step")
            yield Button("Confirm pairing and load mappings", id="load-mappings", variant="success")
            yield Button("Approve mappings and continue", id="approve-mappings", variant="success")
            yield Button(
                "Read Canvas grades and build preview", id="build-preview", variant="primary"
            )
            yield Button("Send selected rows to Canvas", id="send-rows", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#work-indicator").display = False
        self.query_one("#student-table", DataTable).add_columns(
            "Source student", "CSV section / ID", "Canvas match", "Method"
        )
        self.query_one("#unit-table", DataTable).add_columns(
            "Assignment / unit", "Review", "Sections", "Lab pool", "Activity target", "Lab target"
        )
        self.query_one("#preview-table", DataTable).add_columns(
            "Student", "Assignment", "Current", "Proposed", "Status"
        )
        self.query_one("#result-table", DataTable).add_columns("Student", "Assignment", "Result")
        self._show_step(1)
        self._detect_file()

    def _show_step(self, number: int) -> None:
        self.step = number
        titles = [
            "Inspect and pair CSV",
            "Approve mappings",
            "Select assignments",
            "Preview and send rows",
            "Sending and read-back results",
        ]
        self.query_one("#step-title", Static).update(
            f"[bold]Step {number}/5 — {titles[number - 1]}[/bold]"
        )
        for index in range(1, 6):
            self.query_one(f"#step-{index}").display = index == number
        for index, action_button_id in self.STEP_ACTIONS.items():
            self.query_one(f"#{action_button_id}", Button).display = index == number
        self.query_one("#zybooks-body", VerticalScroll).scroll_home(animate=False)

    def _status(self, text: str) -> None:
        self.query_one("#wizard-status", Static).update(Text(text))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.query_one("#work-indicator").display = busy
        for widget in self.query("Button, Input, Select, Checkbox, SelectionList"):
            widget.disabled = busy

    @work(exclusive=True)
    async def _perform(
        self, operation, completed, message: str, *, show_transmission: bool = False
    ) -> None:
        self._set_busy(True)
        self._status(message)
        self.query_one("#wizard-status").scroll_visible(animate=False)
        transmission_modal = TransmissionModal(message) if show_transmission else None
        error_text = None
        try:
            if transmission_modal is not None:
                # Mount first: even the first request/prompt must reach the log subscriber.
                await self.app.push_screen(transmission_modal)
            result = await asyncio.to_thread(operation)
            if self.is_mounted:
                completed(result)
        except ImportProblem as error:
            error_text = str(error)
            self._status(error_text)
        except Exception:
            error_text = (
                "The file, Canvas or AI operation failed. Nothing will be retried automatically."
            )
            self._status(error_text)
        finally:
            if transmission_modal is not None and transmission_modal.is_mounted:
                if error_text:
                    if self.step == 2:
                        self.query_one("#mapping-notes", Static).update(Text(error_text))
                        self.query_one(Collapsible).collapsed = False
                    transmission_modal.mark_error(escape(error_text))
                else:
                    transmission_modal.dismiss(None)
            if self.is_mounted:
                self._set_busy(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._busy:
            return
        handlers = {
            "detect-file": self._detect_file,
            "inspect-file": self._inspect_file,
            "load-mappings": self._load_mappings,
            "suggest-mappings": self._suggest_mappings,
            "save-student": self._save_student,
            "save-unit": self._save_unit,
            "remove-unit": self._remove_unit,
            "approve-mappings": self._approve_mappings,
            "build-preview": self._build_preview,
            "send-rows": self._confirm_and_send_rows,
            "previous-step": self.action_previous_step,
            "select-ready": lambda: self.query_one("#row-selection", SelectionList).select_all(),
            "clear-rows": lambda: self.query_one("#row-selection", SelectionList).deselect_all(),
        }
        try:
            handler = handlers.get(event.button.id)
            if handler:
                handler()
        except (ImportProblem, ValueError) as error:
            self._status(str(error))

    def _find_candidate(self, directory: Path | None = None) -> InboxCandidate | None:
        """Find the newest zyBooks report via ``core.inbox`` (Downloads first)."""
        if directory is not None:
            return find_exported_file(
                ZYBOOKS_REPORT_FILENAME_PATTERN,
                download_directory=directory,
            )
        if self.import_directory is not None:
            return find_exported_file(
                ZYBOOKS_REPORT_FILENAME_PATTERN,
                download_directory=self.import_directory,
            )
        return find_exported_file(ZYBOOKS_REPORT_FILENAME_PATTERN)

    def _detect_file(self) -> None:
        candidate = self._find_candidate()
        self.suggested_candidate = candidate
        if candidate is not None:
            self.query_one("#file-path", Input).value = str(candidate.path.resolve())
            source_label = (
                "Downloads"
                if candidate.found_in == CandidateLocation.DOWNLOADS
                else "workspace import"
            )
            self.query_one("#file-info", Static).update(
                Text(
                    f"Suggested newest report ({source_label}, not yet imported): "
                    f"{candidate.path.name}\n"
                    'Click "Inspect CSV" to read and confirm it before pairing.'
                )
            )
            self._status(
                f"Suggested newest matching file from {source_label}. "
                "Inspect and confirm it before pairing."
            )
        else:
            searched = self.import_directory or default_download_directory()
            self.query_one("#file-info", Static).update("")
            self._status(
                f"No zyBooks {ZYBOOKS_REPORT_FILENAME_PATTERN} found in {searched}. "
                "Enter an explicit file path."
            )

    def _inspect_file(self) -> None:
        path = Path(self.query_one("#file-path", Input).value.strip())
        if path.is_dir():
            # The field starts as a folder; inspecting it means "the newest report in here".
            candidate = self._find_candidate(path)
            if candidate is None:
                raise ImportProblem(
                    f"No zyBooks {ZYBOOKS_REPORT_FILENAME_PATTERN} found in {path}."
                )
            path = candidate.path
            self.query_one("#file-path", Input).value = str(path.resolve())
        self._perform(
            lambda: read_snapshot(path), self._file_loaded, "Inspecting CSV structure and scores..."
        )

    def _file_loaded(self, snapshot: CsvSnapshot) -> None:
        self.snapshot = snapshot
        self.coordinator = None
        self.plan = None
        self.preview = None
        self.query_one("#pair-confirm", Checkbox).value = False
        self.query_one("#book-id", Input).value = snapshot.book_candidate
        scope = ", ".join(snapshot.sections)
        labs = sum(column.category == "Lab" and column.possible > 0 for column in snapshot.columns)
        self.query_one("#file-info", Static).update(
            Text(
                f"File: {snapshot.path.name}\n"
                f"Book candidate: {snapshot.book_candidate or 'unknown'}\n"
                f"File modified: {snapshot.modified_at}\nScope: {scope}\n"
                f"{len(snapshot.students)} students; {labs} positive-maximum lab columns.\n"
                "No cutoff metadata in this CSV. Filename date is a hint, not proof of freshness."
            )
        )
        self._status("CSV inspected. Confirm the book and selected Canvas course.")

    def _load_mappings(self) -> None:
        if (
            self.snapshot is None
            or Path(self.query_one("#file-path", Input).value).resolve() != self.snapshot.path
        ):
            raise ImportProblem("Inspect the selected file first.")
        book = self.query_one("#book-id", Input).value.strip()
        if not book or not self.query_one("#pair-confirm", Checkbox).value:
            raise ImportProblem("Confirm the book/course association before loading mappings.")
        if not self.app_state.canvas_client or not self.app_state.current_course:
            raise ImportProblem("Select a Canvas course using the existing Canvas connection.")
        self.coordinator = CsvImportCoordinator(
            self.app_state.canvas_client,
            self.app_state.current_course.id,
            self.snapshot,
            self.cache,
            workspace_directory=self.app_state.workspace_directory,
        )

        def load_and_suggest():
            plan, cached = self.coordinator.load_mappings(book)
            notes = ""
            mapping_error = None
            if not cached:
                self.app.call_from_thread(
                    self._status,
                    "Checking saved AI proposals; requesting a new proposal only if needed...",
                )
                try:
                    plan, notes = self.coordinator.suggest_mappings(plan, self.app_state.llm_client)
                except ImportProblem as error:
                    notes = str(error)
                    # A missing model still allows manual mapping. A failed model
                    # response must leave the traffic visible for diagnosis.
                    if self.app_state.llm_client is not None:
                        mapping_error = notes
            return plan, cached, notes, mapping_error

        self._perform(
            load_and_suggest,
            self._mappings_loaded,
            "Reading Canvas roster and assignments; checking approved mapping cache...",
            show_transmission=True,
        )

    def _mappings_loaded(self, result) -> None:
        self.plan, cached, notes, mapping_error = result
        self.query_one("#mapping-notes", Static).update(Text(notes))
        roster_options = [
            (Text(value(row, "name"), no_wrap=True, overflow="ellipsis"), value(row, "id"))
            for row in sorted(
                self.coordinator.roster, key=lambda row: value(row, "name").casefold()
            )
        ]
        self.query_one("#canvas-student", Select).set_options(
            [("Unmatched — skip", 0)] + roster_options
        )
        assignment_options = [
            (
                Text(
                    f"{value(row, 'name')} ({value(row, 'points_possible')} points)",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
                value(row, "id"),
            )
            for row in sorted(
                self.coordinator.assignments, key=lambda row: value(row, "name").casefold()
            )
        ]
        self.query_one("#activity-target", Select).set_options(
            [("No activity target", 0)] + assignment_options
        )
        self.query_one("#lab-target", Select).set_options(
            [("No lab target", 0)] + assignment_options
        )
        self._refresh_mappings()
        self._show_step(2)
        self._status(
            "Approved mappings reused after schema/identity checks; corrections are available."
            if cached
            else "Review suggested targets and section coverage before approval."
        )
        if mapping_error:
            raise ImportProblem(mapping_error)

    def _suggest_mappings(self) -> None:
        if not self.plan or not self.coordinator:
            raise ImportProblem("Load the Canvas assignments first.")
        if self.app_state.llm_client is None:
            raise ImportProblem(
                "No AI model connected. Configure a model before requesting suggestions."
            )

        def confirmed(answer: bool) -> None:
            if answer:
                self._perform(
                    lambda: self.coordinator.suggest_mappings(
                        self.plan, self.app_state.llm_client, refresh=True
                    ),
                    self._suggestions_loaded,
                    "Asking the configured AI model for assignment mappings...",
                    show_transmission=True,
                )

        self.app.push_screen(
            ConfirmModal(
                "Request a new AI response and replace this draft? This makes a model call. "
                "Saved approvals stay unchanged.",
                "Suggest mappings",
            ),
            confirmed,
        )

    def _suggestions_loaded(self, result) -> None:
        self.plan, notes = result
        self.preview = None
        self.query_one("#mapping-notes", Static).update(Text(notes))
        self._refresh_mappings()
        self._status(
            "Mapping suggestions received. Review coverage and confirm eligible labs "
            "before approval."
        )

    def _eligible_labs(self, sections) -> list:
        return [
            column
            for column in self.snapshot.columns
            if column.category == "Lab" and column.possible > 0 and column.section in sections
        ]

    def _lab_blocker(self, unit: UnitMapping):
        """Short table label, full explanation, and the control that resolves it."""
        if not unit.lab_target:
            return None
        if not self._eligible_labs(unit.sections):
            return (
                "No lab columns",
                f"{unit.name} has a lab target, but its sections "
                f"({', '.join(unit.sections) or 'none listed'}) contain no lab columns. "
                "Enter the lab sections, or set Lab target to 'No lab target'.",
                "lab-target",
            )
        if len(unit.lab_sections) < unit.labs_required:
            return (
                f"Check {unit.labs_required} labs",
                f"{unit.name} scores each student's best {unit.labs_required} labs, so at "
                f"least {unit.labs_required} eligible sections must stay checked; "
                f"{len(unit.lab_sections)} are checked now.",
                "unit-labs",
            )
        if not unit.distinct_labs_confirmed:
            return (
                "Confirm labs",
                f"{unit.name}: confirm each checked section is one separate lab, then continue.",
                "distinct-labs",
            )
        return None

    def _refresh_mappings(self, selected: int | None = None) -> None:
        self._refresh_students()
        assignments = {value(row, "id"): value(row, "name") for row in self.coordinator.assignments}
        table = self.query_one("#unit-table", DataTable)
        with self.prevent(DataTable.RowHighlighted):
            table.clear()
            for unit in self.plan.units:
                blocker = self._lab_blocker(unit)
                table.add_row(
                    Text(unit.name),
                    blocker[0] if blocker else "Ready to approve",
                    format_sections(unit.sections),
                    format_sections(unit.lab_sections),
                    Text(assignments.get(unit.activity_target, "—")),
                    Text(assignments.get(unit.lab_target, "—")),
                )
        choices = sorted(
            [(unit.name, index + 1) for index, unit in enumerate(self.plan.units)],
            key=lambda option: option[0].casefold(),
        )
        outstanding = [
            blocker[1] for unit in self.plan.units if (blocker := self._lab_blocker(unit))
        ]
        self.query_one("#unit-todo", Static).update(
            Text("Needs attention before approval:\n" + "\n".join(outstanding))
            if outstanding
            else Text(
                "Every unit is ready. Approve with the button below the status line."
                if choices
                else "No units mapped yet. Suggest mappings with AI, or add one below."
            )
        )
        selected = selected if selected is not None else choices[0][1] if choices else 0
        with self.prevent(Select.Changed):
            self.query_one("#unit-choice", Select).set_options([("New unit", 0)] + choices)
            self.query_one("#unit-choice", Select).value = selected
        self._load_unit_editor(selected)

    def _refresh_students(self) -> None:
        students = {row.email: row for row in self.snapshot.students}
        roster = {value(row, "id"): value(row, "name") for row in self.coordinator.roster}
        matched = sum(row.canvas_id is not None for row in self.plan.students)
        skipped = len(self.plan.students) - matched
        absent = len(roster) - matched
        self.query_one("#student-coverage", Static).update(
            f"{matched}/{len(self.plan.students)} CSV students matched; "
            f"{skipped} skipped (a book can span sections).\n"
            f"{absent} Canvas {'student is' if absent == 1 else 'students are'} "
            "not in this CSV; no grades for them."
        )
        show_unmatched = self.query_one("#show-unmatched", Checkbox).value
        visible = sorted(
            [row for row in self.plan.students if row.canvas_id is not None or show_unmatched],
            key=lambda row: (students[row.email].name.casefold(), row.email),
        )
        table = self.query_one("#student-table", DataTable)
        table.clear()
        for mapping in visible:
            table.add_row(
                Text(students[mapping.email].name),
                Text(
                    students[mapping.email].class_section
                    or students[mapping.email].student_id
                    or "—"
                ),
                Text(roster.get(mapping.canvas_id, "Unmatched — skipped")),
                mapping.method,
            )
        selector = self.query_one("#source-student", Select)
        previous = selector.value
        selector.set_options(
            [
                (
                    Text(
                        f"{students[row.email].name} - {row.email}",
                        no_wrap=True,
                        overflow="ellipsis",
                    ),
                    row.email,
                )
                for row in visible
            ]
        )
        if previous in {row.email for row in visible}:
            selector.value = previous

    def on_select_changed(self, event: Select.Changed) -> None:
        if not self.plan:
            return
        if event.select.id == "unit-choice" and event.value is not Select.BLANK:
            self._load_unit_editor(int(event.value))
        elif event.select.id == "source-student" and event.value is not Select.BLANK:
            mapping = next((row for row in self.plan.students if row.email == event.value), None)
            self.query_one("#canvas-student", Select).value = (
                mapping.canvas_id or 0 if mapping else 0
            )
        elif event.select.id == "lab-target":
            self._update_lab_review()

    def _load_unit_editor(self, choice: int) -> None:
        with self.prevent(
            Input.Changed,
            Select.Changed,
            SelectionList.SelectedChanged,
            Checkbox.Changed,
            DataTable.RowHighlighted,
        ):
            self._populate_unit_editor(choice)

    def _populate_unit_editor(self, choice: int) -> None:
        unit = (
            self.plan.units[choice - 1]
            if 0 < choice <= len(self.plan.units)
            else UnitMapping("", (), ())
        )
        for identifier, text in (
            ("unit-name", unit.name),
            ("unit-sections", ",".join(unit.sections)),
        ):
            self.query_one(f"#{identifier}", Input).value = text
        self.query_one("#activity-target", Select).value = unit.activity_target or 0
        self.query_one("#lab-target", Select).value = unit.lab_target or 0
        self._lab_selection = set(unit.lab_sections)
        # The unit being edited keeps its own "best N labs" count; the editor
        # has no field for it, so saving must not reset it to the default.
        self._labs_required = unit.labs_required
        self._refresh_lab_options(unit.sections)
        self.query_one("#distinct-labs", Checkbox).value = (
            unit.distinct_labs_confirmed and self._lab_selection == set(unit.lab_sections)
        )
        table = self.query_one("#unit-table", DataTable)
        if 0 < choice <= table.row_count:
            table.move_cursor(row=choice - 1)
        self._update_lab_review()

    def _refresh_lab_options(self, sections) -> None:
        """Keep the eligible pool tied to whatever sections the unit currently claims."""
        selection = self.query_one("#unit-labs", SelectionList)
        eligible = self._eligible_labs(sections)
        with self.prevent(SelectionList.SelectedChanged):
            selection.clear_options()
            selection.add_options(
                [
                    (
                        f"Section {column.section} - Lab ({column.possible} source points)",
                        column.section,
                        column.section in self._lab_selection,
                    )
                    for column in eligible
                ]
            )
        selection.display = bool(eligible)
        self.query_one("#distinct-labs").display = bool(eligible)
        chosen = set(selection.selected)
        if chosen != self._lab_selection:
            self._lab_selection = chosen
            with self.prevent(Checkbox.Changed):
                self.query_one("#distinct-labs", Checkbox).value = False
        self._update_lab_review()

    def _update_lab_review(self) -> None:
        target = self.query_one("#lab-target", Select).value
        self.query_one("#lab-review").display = target not in (0, Select.BLANK)
        selection = self.query_one("#unit-labs", SelectionList)
        eligible = selection.option_count
        count = len(selection.selected)
        required = getattr(self, "_labs_required", DEFAULT_LABS_REQUIRED)
        if not eligible:
            summary = (
                "No lab columns fall inside these sections.\n"
                "Enter the lab sections above, or set Lab target to 'No lab target'."
            )
        else:
            summary = (
                f"{count} of {eligible} eligible labs checked. "
                f"Each student's best {required} count toward the Canvas grade.\n"
                "Check every section that is one separate lab; leave bundles unchecked."
            )
            if count < required:
                summary += (
                    f"\nAt least {required} must stay checked; check {required - count} more."
                )
        self.query_one("#lab-summary", Static).update(summary)

    def _save_student(self) -> None:
        email = self.query_one("#source-student", Select).value
        if email is Select.BLANK:
            raise ImportProblem("Choose a source student to correct.")
        target = self.query_one("#canvas-student", Select).value
        mappings = tuple(
            replace(row, canvas_id=target or None, method="Manual — approved on next step")
            if row.email == email
            else row
            for row in self.plan.students
        )
        self.plan = replace(self.plan, students=mappings, approved=False)
        self.coordinator.invalidate()
        self._refresh_students()
        self._status("Student correction applied; approve mappings to save it.")

    def _save_unit(self) -> None:
        if self.plan is None:
            return
        name = self.query_one("#unit-name", Input).value.strip()
        if not name:
            raise ImportProblem("Enter a unit name and its explicit sections.")
        unit = UnitMapping(
            name,
            parse_sections(self.query_one("#unit-sections", Input).value),
            parse_sections(",".join(self.query_one("#unit-labs", SelectionList).selected)),
            self.query_one("#activity-target", Select).value or None,
            self.query_one("#lab-target", Select).value or None,
            self.query_one("#distinct-labs", Checkbox).value,
            getattr(self, "_labs_required", DEFAULT_LABS_REQUIRED),
        )
        units = list(self.plan.units)
        choice = self.query_one("#unit-choice", Select).value
        if choice and choice is not Select.BLANK:
            units[int(choice) - 1] = unit
        else:
            units.append(unit)
        self.plan = replace(self.plan, units=tuple(units), approved=False)
        self.coordinator.invalidate()
        self._refresh_mappings(int(choice) if choice and choice is not Select.BLANK else len(units))
        self._status("Unit saved in this draft. Approval checks targets, membership and coverage.")

    def _remove_unit(self) -> None:
        choice = self.query_one("#unit-choice", Select).value
        if choice and choice is not Select.BLANK:
            self.plan = replace(
                self.plan,
                units=tuple(
                    unit for index, unit in enumerate(self.plan.units, 1) if index != choice
                ),
                approved=False,
            )
            self.coordinator.invalidate()
            self._refresh_mappings()

    def _approve_mappings(self) -> None:
        self._save_unit()
        pending = next(
            (
                (index, blocker)
                for index, unit in enumerate(self.plan.units, 1)
                if (blocker := self._lab_blocker(unit))
            ),
            None,
        )
        if pending:
            index, (_, explanation, control) = pending
            with self.prevent(Select.Changed):
                self.query_one("#unit-choice", Select).value = index
            self._load_unit_editor(index)
            self.query_one("#mapping-tabs", TabbedContent).active = "unit-mappings"
            self.query_one(f"#{control}").focus()
            self.query_one(f"#{control}").scroll_visible(animate=False)
            self._status(explanation)
            return
        self.plan = self.coordinator.approve_mappings(
            self.plan,
            self.app_state.current_course.id,
            self.query_one("#book-id", Input).value.strip(),
        )
        assignments = {value(row, "id"): value(row, "name") for row in self.coordinator.assignments}
        options = [
            (
                Text(
                    f"{assignments[target]}  <-  {description}",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
                target,
                True,
            )
            for unit in self.plan.units
            for target, description in (
                (
                    unit.activity_target,
                    f"CSV participation + challenges, sections {format_sections(unit.sections)}",
                ),
                (
                    unit.lab_target,
                    f"CSV labs {format_sections(unit.lab_sections)}, "
                    f"best {unit.labs_required} per student",
                ),
            )
            if target
        ]
        options.sort(key=lambda option: assignments[option[1]].casefold())
        selection = self.query_one("#assignment-selection", SelectionList)
        selection.clear_options()
        selection.add_options(options)
        matched = sum(row.canvas_id is not None for row in self.plan.students)
        self.query_one("#coverage-info", Static).update(
            Text(
                f"{matched}/{len(self.plan.students)} source students matched. "
                f"{len(self.coordinator.roster) - matched} Canvas students have no source match "
                "and will not get grade rows.\n"
                "All configured columns are present and valid. Missing records or unreadable "
                "Canvas grades block posting."
            )
        )
        self._show_step(3)
        self._status("Approved book/course mappings saved locally.")

    def _build_preview(self) -> None:
        targets = set(self.query_one("#assignment-selection", SelectionList).selected)
        include_existing = self.query_one("#include-existing", Checkbox).value
        self._perform(
            lambda: self.coordinator.prepare_preview(self.plan, targets, include_existing),
            self._preview_loaded,
            "Reading current Canvas grades and calculating the CSV snapshot...",
        )

    def _preview_loaded(self, preview: Preview) -> None:
        self.preview = preview
        table = self.query_one("#preview-table", DataTable)
        table.clear()
        ready = []
        for row in preview.rows:
            old = "Ungraded" if row.old_points is None else str(row.old_points)
            table.add_row(
                Text(row.student_name),
                Text(row.assignment_name),
                old,
                str(row.new_points),
                row.status,
                key=row.key,
            )
            if row.status == "Ready":
                ready.append(
                    (
                        f"{row.student_name} / {row.assignment_name}: {old} → {row.new_points}"
                        + (" [zero/decrease]" if row.risky else ""),
                        row.key,
                        False,
                    )
                )
        selection = self.query_one("#row-selection", SelectionList)
        selection.clear_options()
        selection.add_options(ready)
        self.query_one("#preview-info", Static).update(
            Text(
                f"{self.snapshot.path.name} · modified {self.snapshot.modified_at}\n"
                f"{len(ready)} ready rows. {preview.unmatched_source} unmatched source and "
                f"{preview.unmatched_canvas} unmatched Canvas students excluded.\n"
                "Best labs means the best percentages reported in this file. "
                "Select rows below, then send them; one confirmation lists what will change."
            )
        )
        self._show_step(4)
        self._status("Preview only: no grades have been sent.")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "unit-table" and self.plan:
            index = event.cursor_row + 1
            if index != self.query_one("#unit-choice", Select).value:
                with self.prevent(Select.Changed):
                    self.query_one("#unit-choice", Select).value = index
                self._load_unit_editor(index)
        if event.data_table.id == "preview-table" and self.preview:
            row = next((row for row in self.preview.rows if row.key == event.row_key.value), None)
            if row:
                self.query_one("#calculation", Static).update(Text("\n".join(row.breakdown)))

    def _confirm_and_send_rows(self) -> None:
        """Validate the selection, ask ONE informative question, then post.

        The modal is the only gate between the preview and Canvas writes, so it
        carries everything the instructor needs: how many rows, which course,
        what kind of change each row is, and — prominently — any zeros or
        decreases. Confirming it is the explicit risky acknowledgement; the
        coordinator still refuses risky rows unless ``accept_risky`` is passed.
        """
        selected_row_keys = set(self.query_one("#row-selection", SelectionList).selected)
        if self.preview is None or self.coordinator is None:
            raise ImportProblem("Build a current preview before sending grades.")
        if not selected_row_keys:
            raise ImportProblem("Select at least one ready row to send.")
        rows_by_key = {row.key: row for row in self.preview.rows}
        selected_rows = [rows_by_key[key] for key in selected_row_keys if key in rows_by_key]
        if len(selected_rows) != len(selected_row_keys) or any(
            row.status != "Ready" for row in selected_rows
        ):
            raise ImportProblem("Only ready preview rows may be sent.")
        if any(row.status.startswith("Blocked") for row in self.preview.rows):
            raise ImportProblem("Resolve missing Canvas grade records before posting.")
        # Nothing is approved until the instructor answers the modal.
        self.coordinator.approval = None
        course_name = str(value(self.app_state.current_course, "name"))
        prompt_text, confirm_label = describe_send_confirmation(selected_rows, course_name)
        includes_risky_rows = any(row.risky for row in selected_rows)

        def send_after_confirmation(answer: bool) -> None:
            if not answer:
                self.coordinator.approval = None
                self._status("Nothing was sent. Adjust the selected rows or send again.")
                return
            try:
                approval = self.coordinator.approve_rows(
                    self.preview, selected_row_keys, accept_risky=includes_risky_rows
                )
            except ImportProblem as error:
                self.coordinator.approval = None
                self._status(str(error))
                return
            # A fresh, single-use approval re-arms the one-shot submit guard.
            self._submitted = False
            self.query_one("#submission-info", Static).update(
                Text(
                    f"{len(approval.row_keys)} rows approved for {course_name}.\n"
                    "Rechecking the file, mappings, roster, assignments and Canvas grades "
                    "before sending.\n"
                    "No comments or automatic retries. Results are read back per row."
                )
            )
            self._show_step(5)
            try:
                self._submit()
            except ImportProblem as error:
                self._status(str(error))

        self.app.push_screen(
            ConfirmModal(prompt_text, confirm_label, "Cancel"),
            send_after_confirmation,
        )

    def _submit(self) -> None:
        if self._submitted:
            raise ImportProblem("This approval was already used; build a new preview to continue.")
        self._submitted = True
        targets = set(self.query_one("#assignment-selection", SelectionList).selected)
        include_existing = self.query_one("#include-existing", Checkbox).value
        approval = self.coordinator.approval
        log = self.query_one("#submission-log", RichLog)
        log.clear()
        log.write(
            Panel(
                f"Sending {len(approval.row_keys)} approved rows to "
                f"{self.app_state.current_course.name}.\n"
                "Each row is posted once, then read back from Canvas. Nothing is retried.",
                title="Posting grades",
                border_style="red",
            )
        )

        def progress(message: str) -> None:
            self.app.call_from_thread(self._log_progress, message)

        self._perform(
            lambda: self.coordinator.submit(
                self.plan, targets, include_existing, approval, progress, execute=True
            ),
            self._submission_finished,
            "Rechecking approval before sending any Canvas grade...",
        )

    def _log_progress(self, message: str) -> None:
        self._status(message)
        self.query_one("#submission-log", RichLog).write(Text(message))

    def _submission_finished(self, results) -> None:
        rows = {row.key: row for row in self.preview.rows}
        table = self.query_one("#result-table", DataTable)
        table.clear()
        tally = Counter()
        for result in results:
            row = rows[result.row_key]
            table.add_row(Text(row.student_name), Text(row.assignment_name), result.status)
            tally[result.status] += 1
        summary = RichTable(title="Submission results", header_style="bold")
        summary.add_column("Outcome")
        summary.add_column("Rows", justify="right")
        for status, count in sorted(tally.items()):
            summary.add_row(status, str(count))
        log = self.query_one("#submission-log", RichLog)
        log.write(summary)
        log.write(
            Text("Uncertain and failed rows were left alone; no automatic retries.", style="italic")
        )
        self._status(
            "Submission finished. Review each result; uncertain/failed rows were not retried."
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"file-path", "book-id"} and self.snapshot:
            self.query_one("#pair-confirm", Checkbox).value = False
        if event.input.id == "unit-sections" and self.plan:
            with suppress(ImportProblem):
                self._refresh_lab_options(parse_sections(event.value))
        if self.step >= 3 and self.coordinator:
            self.coordinator.invalidate()
            self.preview = None

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "show-unmatched" and self.plan:
            self._refresh_students()
        elif event.checkbox.id == "include-existing" and self.coordinator:
            self.coordinator.invalidate()
            self.preview = None

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        if event.selection_list.id == "unit-labs":
            selected = set(event.selection_list.selected)
            if selected != getattr(self, "_lab_selection", set()):
                self._lab_selection = selected
                self.query_one("#distinct-labs", Checkbox).value = False
            self._update_lab_review()
        if self.coordinator:
            if event.selection_list.id == "assignment-selection":
                self.coordinator.invalidate()
                self.preview = None
            elif event.selection_list.id == "row-selection":
                self.coordinator.approval = None

    def action_previous_step(self) -> None:
        if self._busy:
            if self.coordinator:
                self.coordinator.cancelled.set()
            self._status("Stopping after the current request; waiting for its result...")
            return
        if self.step == 1:
            self.action_go_back()
            return
        if self.coordinator:
            self.coordinator.approval = None
            if self.step <= 4:
                self.coordinator.invalidate()
                self.preview = None
        self._show_step(self.step - 1)

    def action_request_quit(self) -> None:
        if self._busy:
            self.action_previous_step()
        else:
            super().action_request_quit()

    def on_unmount(self) -> None:
        if self.coordinator:
            self.coordinator.cancelled.set()
