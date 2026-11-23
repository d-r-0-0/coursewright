"""Discussion List Screen for the Coursewright TUI.

Displays a scrollable DataTable of discussions for the selected course
with columns: #, Title, Type, Replies, Posted, Graded.  Selecting a
row launches analysis via a background Worker, then pushes the
AnalysisResultsScreen with the results.

Replaces: ``discussion_menu_loop`` and ``load_discussions`` from
``discussion_menu.py``.
"""

from __future__ import annotations

from datetime import datetime

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Header, Static
from textual.worker import Worker, WorkerState

from coursewright.ta.grading.scores import discussion_points_settings
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal


class DiscussionListScreen(CoursewrightScreen):
    """Scrollable discussion table for the selected course.

    Lifecycle:
    1. On mount, kicks off a background worker to fetch discussions
       from the Canvas API.
    2. Once loaded, populates a DataTable with discussion metadata.
    3. User selects a discussion (click or number key 1-9).
    4. A second worker runs the analysis with progress updates.
    5. On completion, pushes ``AnalysisResultsScreen``.

    Key bindings:
    - 1-9      : Select discussion by visible index
    - Enter    : Analyze selected discussion (row click)
    - p        : Switch grading profile
    - Escape   : Back to course list
    - q        : Quit
    """

    BINDINGS = [
        Binding("r", "refresh_discussions", "Refresh", show=True),
        Binding("e", "export_all", "Export all", show=True),
        Binding("p", "change_profile", "Profile", show=True),
        # Number keys for quick selection (first 9 visible rows)
        Binding("1", "select_discussion(0)", "1", show=False),
        Binding("2", "select_discussion(1)", "2", show=False),
        Binding("3", "select_discussion(2)", "3", show=False),
        Binding("4", "select_discussion(3)", "4", show=False),
        Binding("5", "select_discussion(4)", "5", show=False),
        Binding("6", "select_discussion(5)", "6", show=False),
        Binding("7", "select_discussion(6)", "7", show=False),
        Binding("8", "select_discussion(7)", "8", show=False),
        Binding("9", "select_discussion(8)", "9", show=False),
    ]

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        # Ordered list of discussions currently visible in the table,
        # kept in sync with row indices for number-key selection.
        self._visible_discussions: list = []
        # The transmission log shown while a Canvas worker runs, and the
        # reason the last worker failed.  A TransmissionModal can only be
        # dismissed once it is in an error state, so a failed worker must
        # mark it; otherwise the user is stuck behind "Fetching...".
        self._transmission_modal: TransmissionModal | None = None
        self._worker_failure_text = ""

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        """Build the layout: header, status + table, footer."""
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="discussion-scroll"):
            yield Static(
                "[dim]Loading discussions...[/dim]",
                id="discussion-status",
            )
            with Vertical(id="discussion-table-container"):
                yield DataTable(
                    id="discussion-table",
                    cursor_type="row",
                    zebra_stripes=True,
                )
            yield Static(
                self._build_help_text(),
                id="discussion-help",
            )
        yield HelpBar()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        """Start loading discussions in a background thread."""
        self._open_transmission_modal("Fetching discussions from Canvas...")
        self.run_worker(
            self._load_discussions_from_canvas,
            name="load-discussions",
            thread=True,
        )

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------
    async def _load_discussions_from_canvas(self) -> str:
        """Fetch discussions for the current course.

        Returns ``"ok"`` on success or ``"failed"`` on error.
        The worker result is checked in ``on_worker_state_changed``.
        """
        course = self.app_state.current_course
        if not course:
            self._worker_failure_text = "No course selected."
            return "failed"

        try:
            discussions = self.app_state.canvas_client.get_discussions(course.id)
            self.app_state.discussions = discussions
            return "ok"
        except Exception as load_error:
            self._worker_failure_text = f"Error loading discussions: {load_error}"
            return "failed"

    async def _run_analysis(self) -> str:
        """Analyze the selected discussion in a background thread.

        Stores the ``DiscussionAnalysis`` result on ``self._analysis``
        so the worker callback can push the results screen.

        Returns ``"ok"`` on success or ``"failed"`` on error.
        """
        from coursewright.ta.grading.analyzer import DiscussionAnalyzer

        course = self.app_state.current_course
        discussion = self._selected_discussion
        if not course or not discussion:
            self._worker_failure_text = "No course or discussion selected."
            return "failed"

        try:
            grading_profile = getattr(self.app_state, "grading_profile", "auto")
            grading_config = {
                "min_words": 300,
                "min_replies": 2,
                "rubric_file": None,
                "profile": grading_profile,
                **discussion_points_settings(self.app_state.config.discussion),
            }
            analyzer = DiscussionAnalyzer(self.app_state.canvas_client, grading_config)

            # Progress callback updates the status label from the
            # worker thread using call_from_thread for thread safety.
            def _on_progress(fraction: float) -> None:
                percent = int(fraction * 100)
                self.app.call_from_thread(
                    self._update_status_widget,
                    f"[cyan]Analyzing... {percent}%[/cyan]",
                )

            analysis = analyzer.analyze_discussion(
                course.id,
                discussion.id,
                progress_callback=_on_progress,
            )

            if analysis is None:
                self._worker_failure_text = "Analysis returned no results."
                return "failed"

            # Stash for the worker callback to pick up
            self._analysis_result = analysis
            return "ok"

        except Exception as analysis_error:
            self._worker_failure_text = f"Analysis error: {analysis_error}"
            return "failed"

    # ------------------------------------------------------------------
    # Worker lifecycle callback
    # ------------------------------------------------------------------
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker completion for both loading and analysis."""
        if event.worker.name in ("load-discussions", "analyze-discussion") and (
            event.state == WorkerState.ERROR
            or (event.state == WorkerState.SUCCESS and event.worker.result == "failed")
        ):
            self._report_worker_failure(self._worker_failure_text or str(event.worker.error))
            return

        if (
            event.worker.name == "load-discussions"
            and event.state == WorkerState.SUCCESS
            and event.worker.result == "ok"
        ):
            self._close_transmission_modal()
            self._populate_discussion_table()

        elif (
            event.worker.name == "analyze-discussion"
            and event.state == WorkerState.SUCCESS
            and event.worker.result == "ok"
        ):
            self._close_transmission_modal()
            self._push_analysis_results()

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------
    def _populate_discussion_table(self) -> None:
        """Fill the DataTable with discussion rows."""
        discussions = self.app_state.discussions
        table = self.query_one("#discussion-table", DataTable)
        table.clear(columns=True)

        table.add_column("#", width=4)
        table.add_column("Title", width=38)
        table.add_column("Type", width=12)
        table.add_column("Replies", width=8)
        table.add_column("Posted", width=12)
        table.add_column("Graded", width=8)

        self._visible_discussions = []
        for idx, discussion in enumerate(discussions, start=1):
            self._visible_discussions.append(discussion)
            row = self._format_discussion_row(idx, discussion)
            table.add_row(*row)

        count = len(discussions)
        course_name = self.app_state.current_course.name
        self._update_status_text(f"[green]Loaded {count} discussions for {course_name}[/green]")
        table.focus()

    def _format_discussion_row(self, idx: int, discussion) -> tuple[str, str, str, str, str, str]:
        """Format a single discussion into a table row tuple."""
        # Index
        index_str = str(idx)

        # Title (truncate long titles to keep table readable)
        title = discussion.title or "Untitled"
        if len(title) > 38:
            title = title[:35] + "..."

        # Discussion type — fall back to "threaded" when missing
        disc_type = getattr(discussion, "discussion_type", None) or "threaded"
        disc_type = disc_type.title()

        # Reply count — try replies_count alias, then subentry_count
        reply_count = getattr(discussion, "replies_count", None)
        if reply_count is None:
            reply_count = getattr(discussion, "discussion_subentry_count", 0)
        replies_str = str(reply_count)

        # Posted date
        posted_date = self._format_posted_date(discussion.posted_at)

        # Graded status
        is_graded = "Yes" if discussion.assignment_id else "No"

        return (
            index_str,
            title,
            disc_type,
            replies_str,
            posted_date,
            is_graded,
        )

    # ------------------------------------------------------------------
    # Selection and navigation
    # ------------------------------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle click / Enter on a table row."""
        self._handle_discussion_selection(event.cursor_row)

    def action_select_discussion(self, index: int) -> None:
        """Handle number-key selection (0-based)."""
        self._handle_discussion_selection(index)

    def _handle_discussion_selection(self, index: int) -> None:
        """Launch analysis for the discussion at *index*."""
        if index < 0 or index >= len(self._visible_discussions):
            return
        self._selected_discussion = self._visible_discussions[index]

        # Show progress in the status area
        title = self._selected_discussion.title
        self._update_status_text(f"[cyan]Analyzing: {title}...[/cyan]")

        # Reset any prior result
        self._analysis_result = None
        self._open_transmission_modal(f"Analyzing: {title}...")

        self.run_worker(
            self._run_analysis,
            name="analyze-discussion",
            thread=True,
        )

    def action_refresh_discussions(self) -> None:
        """Reload discussions from Canvas."""
        self._update_status_text("[dim]Reloading discussions...[/dim]")
        self._open_transmission_modal("Reloading discussions...")
        self.run_worker(
            self._load_discussions_from_canvas,
            name="load-discussions",
            thread=True,
        )

    def action_export_all(self) -> None:
        """Push the bulk export screen for all discussions."""
        if not self._visible_discussions:
            self.app.notify(
                "No discussions loaded yet.",
                severity="warning",
            )
            return

        from coursewright.ui.tui.screens.export import (
            BulkExportScreen,
        )

        self.app.push_screen(BulkExportScreen(app_state=self.app_state))

    def action_change_profile(self) -> None:
        """Open the profile picker and persist the selected value."""
        from coursewright.ui.tui.widgets.profile_picker_modal import ProfilePickerModal

        def _on_profile_selected(selected_profile: str | None) -> None:
            if not selected_profile:
                return

            self.app_state.grading_profile = selected_profile
            config = self.app_state.config
            config.discussion.default_grading_profile = selected_profile
            saved = self._persist_config_or_warn(self.app_state)
            if saved:
                self._update_status_text(
                    f"[green]Grading profile set to {selected_profile}.[/green]"
                )
            else:
                self._update_status_text(
                    f"[yellow]Grading profile set to {selected_profile} "
                    "(session only; config not saved).[/yellow]"
                )

        self.app.push_screen(
            ProfilePickerModal(getattr(self.app_state, "grading_profile", "auto")),
            callback=_on_profile_selected,
        )

    def _push_analysis_results(self) -> None:
        """Push the AnalysisResultsScreen with the completed analysis."""
        # Deferred import to avoid circular dependencies
        from coursewright.ui.tui.screens.analysis_results import (
            AnalysisResultsScreen,
        )

        self.app.push_screen(
            AnalysisResultsScreen(
                app_state=self.app_state,
                analysis=self._analysis_result,
                discussion=self._selected_discussion,
            )
        )

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _build_help_text(self) -> str:
        """Keyboard shortcut reference shown below the table."""
        return (
            "\n[bold green]Keyboard Shortcuts[/bold green]\n"
            "[cyan]1-9[/cyan]     Analyze discussion by number\n"
            "[cyan]Enter[/cyan]   Analyze selected discussion\n"
            "[cyan]p[/cyan]       Switch grading profile\n"
            "[cyan]e[/cyan]       Export all to markdown\n"
            "[cyan]r[/cyan]       Refresh discussions\n"
            "[cyan]Esc[/cyan]     Back to course list\n"
            "[cyan]q[/cyan]       Quit"
        )

    def _open_transmission_modal(self, status_text: str) -> None:
        """Show the live transmission log while a Canvas worker runs."""
        self._worker_failure_text = ""
        self._transmission_modal = TransmissionModal(status_text)
        self.app.push_screen(self._transmission_modal)

    def _close_transmission_modal(self) -> None:
        """Dismiss the transmission log after a worker succeeded."""
        if self._transmission_modal is not None and self.app.screen is self._transmission_modal:
            self.app.pop_screen()
        self._transmission_modal = None

    def _report_worker_failure(self, failure_text: str) -> None:
        """Show why a worker failed, on the screen and in the open modal."""
        self._update_status_text(f"[red]{escape(failure_text)}[/red]")
        if self._transmission_modal is not None:
            self._transmission_modal.mark_error(failure_text)

    def _update_status_text(self, text: str) -> None:
        """Update the status label (safe from main thread only)."""
        widget = self.query_one("#discussion-status", Static)
        widget.update(text)

    def _update_status_widget(self, text: str) -> None:
        """Update the status label — intended for ``call_from_thread``.

        Same as ``_update_status_text`` but named distinctly so the
        worker helper reads clearly at the call site.
        """
        self._update_status_text(text)

    @staticmethod
    def _format_posted_date(
        posted_at: datetime | None,
    ) -> str:
        """Format a datetime into MM/DD/YYYY or 'Unknown'."""
        if not posted_at:
            return "Unknown"
        try:
            if isinstance(posted_at, str):
                posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            return posted_at.strftime("%m/%d/%Y")
        except (ValueError, AttributeError):
            return "Unknown"
