"""Export Screens for the Coursewright TUI.

Provides three export-focused screens that replace legacy Rich menu
functions with proper Textual screens and background workers:

* ``BulkExportScreen``      -- progress table exporting all discussions
  in a course to individual markdown files.
* ``MarkdownExportScreen``  -- single-discussion markdown export with
  an "include analysis?" toggle before writing.
* ``ExportFormatScreen``    -- CSV / Excel picker for grade-data export.

Replaces: ``export_all_discussions``, ``export_to_markdown`` from
``discussion_menu.py`` and ``export_results`` from ``grading_menu.py``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import (
    DataTable,
    Header,
    LoadingIndicator,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.help_bar import HelpBar


# =====================================================================
# BulkExportScreen -- export every discussion to markdown
# =====================================================================
class BulkExportScreen(CoursewrightScreen):
    """Bulk-export all discussions from the current course to markdown.

    On mount a background worker iterates through every discussion in
    ``app_state.discussions``, fetches its entries from Canvas, and
    writes a markdown file via ``MarkdownExporter``.  A DataTable
    fills row-by-row so the user can track progress visually.

    Key bindings:
    - Escape : Back (worker continues in background)
    """

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        # Accumulated results: (title, status_markup, path_or_error)
        self._export_results: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        course_name = (
            self.app_state.current_course.name
            if self.app_state.current_course
            else "Unknown Course"
        )
        discussion_count = len(self.app_state.discussions)

        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="bulk-export-scroll"):
            yield Static(
                "[bold cyan]Bulk Markdown Export[/bold cyan]\n"
                f"[bold]Course:[/bold] {course_name}\n"
                f"[bold]Discussions:[/bold] {discussion_count}",
                id="bulk-export-header",
            )
            yield Static(
                "[dim]Starting export...[/dim]",
                id="bulk-export-status",
            )
            # Animated while discussions are fetched from Canvas, so the
            # remote work is visible (docs/HOUSE_STYLE.md section 3).
            yield LoadingIndicator(id="bulk-export-loading")
            yield DataTable(
                id="bulk-export-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static("", id="bulk-export-summary")
        yield HelpBar()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        """Prepare the table columns and kick off the export worker."""
        table = self.query_one("#bulk-export-table", DataTable)
        table.add_column("#", width=4)
        table.add_column("Discussion", width=35)
        table.add_column("Status", width=10)
        table.add_column("Output", width=30)

        self.run_worker(
            self._run_bulk_export,
            name="bulk-export",
            thread=True,
        )

    # ------------------------------------------------------------------
    # Background worker -- iterate, fetch, export
    # ------------------------------------------------------------------
    async def _run_bulk_export(self) -> str:
        """Export every discussion to markdown in a background thread.

        Returns ``"ok"`` when finished (even if some discussions
        failed individually).
        """
        from coursewright.core.export.markdown_exporter import (
            MarkdownExporter,
        )

        course = self.app_state.current_course
        canvas_client = self.app_state.canvas_client
        discussions = self.app_state.discussions

        if not course or not canvas_client or not discussions:
            self.app.call_from_thread(
                self._set_status,
                "[red]Missing course, client, or discussions.[/red]",
            )
            self.app.call_from_thread(self._set_loading_visible, False)
            return "failed"

        exporter = MarkdownExporter()
        total = len(discussions)
        exported_count = 0
        error_count = 0

        for position, discussion in enumerate(discussions, 1):
            title = discussion.title or "Untitled"
            short_title = (
                title[:33] + "..." if len(title) > 33 else title
            )

            self.app.call_from_thread(
                self._set_status,
                f"[cyan]Exporting {position}/{total}: "
                f"{short_title}[/cyan]",
            )

            try:
                entries = canvas_client.get_discussion_entries(
                    course.id, discussion.id
                )
                output_path = exporter.export_discussion_to_markdown(
                    course_id=course.id,
                    discussion=discussion,
                    entries=entries,
                    include_analysis=False,
                    course_name=course.name,
                )
                self._export_results.append(
                    (short_title, "[green]OK[/green]", output_path)
                )
                exported_count += 1
            except Exception as export_error:
                error_message = str(export_error)[:28]
                self._export_results.append(
                    (short_title, "[red]Error[/red]", error_message)
                )
                error_count += 1

            # Append the latest result row to the progress table
            self.app.call_from_thread(
                self._append_result_row,
                position,
                self._export_results[-1],
            )

        # Final summary
        summary_lines = (
            f"\n[bold cyan]Export Complete[/bold cyan]\n"
            f"[green]Exported:[/green] {exported_count}/{total}\n"
        )
        if error_count:
            summary_lines += (
                f"[red]Errors:[/red] {error_count}\n"
            )
        summary_lines += (
            "\n[dim]Files written to [bold]exports/[/bold][/dim]"
        )

        self.app.call_from_thread(self._set_loading_visible, False)
        self.app.call_from_thread(self._set_summary, summary_lines)
        self.app.call_from_thread(
            self._set_status,
            f"[green]Done — {exported_count}/{total} "
            f"exported.[/green]",
        )
        return "ok"

    # ------------------------------------------------------------------
    # UI helpers (main thread only)
    # ------------------------------------------------------------------
    def _append_result_row(
        self,
        index: int,
        result: tuple[str, str, str],
    ) -> None:
        """Add one result row to the progress table."""
        table = self.query_one("#bulk-export-table", DataTable)
        title, status, path = result
        # Truncate long paths for readability
        display_path = (
            "..." + path[-25:] if len(path) > 28 else path
        )
        table.add_row(str(index), title, status, display_path)

    def _set_status(self, text: str) -> None:
        self.query_one("#bulk-export-status", Static).update(text)

    def _set_summary(self, text: str) -> None:
        self.query_one("#bulk-export-summary", Static).update(text)

    def _set_loading_visible(self, is_visible: bool) -> None:
        """Show the loading indicator while Canvas is being read."""
        indicator = self.query_one("#bulk-export-loading", LoadingIndicator)
        indicator.display = is_visible


# =====================================================================
# MarkdownExportScreen -- single discussion with analysis toggle
# =====================================================================
class MarkdownExportScreen(CoursewrightScreen):
    """Export a single discussion to markdown for LLM review.

    Displays an explanation panel and an ``include analysis?`` toggle.
    Pressing ``e`` fetches discussion entries from Canvas and writes
    the markdown file via ``MarkdownExporter``.

    Key bindings:
    - e      : Start the export
    - a      : Toggle include-analysis flag
    - Escape : Back
    """

    BINDINGS = [
        Binding("e", "start_export", "Export", show=True),
        Binding("a", "toggle_analysis", "Analysis", show=True),
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
        self._include_analysis: bool = False
        self._export_complete: bool = False

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="md-export-scroll"):
            yield Static(
                "[bold cyan]Export to Markdown[/bold cyan]\n"
                f"[bold]Discussion:[/bold] "
                f"{self.discussion.title}\n\n"
                "[dim]Exports the full discussion thread to a\n"
                "markdown file suitable for LLM review or\n"
                "offline reading.  Optionally includes the\n"
                "grading analysis breakdown.[/dim]",
                id="md-export-header",
            )
            yield Static(
                self._build_options_text(),
                id="md-export-options",
            )
            yield Static(
                "[dim]Press [bold]e[/bold] to export, "
                "[bold]a[/bold] to toggle analysis, "
                "[bold]Esc[/bold] to go back.[/dim]",
                id="md-export-status",
            )
            # Hidden until the export fetches entries from Canvas.
            yield LoadingIndicator(id="md-export-loading")
        yield HelpBar()

    def on_mount(self) -> None:
        """Nothing is loading until the user presses ``e``."""
        self._set_loading_visible(False)

    # ------------------------------------------------------------------
    # Options display
    # ------------------------------------------------------------------
    def _build_options_text(self) -> str:
        """Render the current export configuration."""
        analysis_flag = (
            "[green]Yes[/green]"
            if self._include_analysis
            else "[yellow]No[/yellow]"
        )
        return (
            f"\n[bold]Export Options[/bold]\n"
            f"  Include analysis: {analysis_flag}\n"
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_toggle_analysis(self) -> None:
        """Flip the include-analysis flag and refresh the display."""
        if self._export_complete:
            return
        self._include_analysis = not self._include_analysis
        self.query_one("#md-export-options", Static).update(
            self._build_options_text()
        )

    def action_start_export(self) -> None:
        """Kick off the markdown export worker."""
        if self._export_complete:
            self.app.notify(
                "Already exported.", severity="information"
            )
            return
        self._set_status("[cyan]Exporting...[/cyan]")
        self._set_loading_visible(True)
        self.run_worker(
            self._run_markdown_export,
            name="md-export",
            thread=True,
        )

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------
    async def _run_markdown_export(self) -> str:
        """Fetch entries and write the markdown file.

        Returns ``"ok"`` on success or ``"failed"`` on error.
        """
        from coursewright.core.export.markdown_exporter import (
            MarkdownExporter,
        )

        course = self.app_state.current_course
        canvas_client = self.app_state.canvas_client

        if not course or not canvas_client:
            self.app.call_from_thread(
                self._set_status,
                "[red]Missing course or Canvas client.[/red]",
            )
            self.app.call_from_thread(self._set_loading_visible, False)
            return "failed"

        try:
            self.app.call_from_thread(
                self._set_status,
                "[cyan]Fetching discussion entries...[/cyan]",
            )
            entries = canvas_client.get_discussion_entries(
                course.id, self.discussion.id
            )

            self.app.call_from_thread(
                self._set_status,
                "[cyan]Writing markdown file...[/cyan]",
            )
            exporter = MarkdownExporter()
            output_path = exporter.export_discussion_to_markdown(
                course_id=course.id,
                discussion=self.discussion,
                entries=entries,
                include_analysis=self._include_analysis,
                analysis=(
                    self.analysis
                    if self._include_analysis
                    else None
                ),
                course_name=course.name,
            )

            self._export_complete = True
            self.app.call_from_thread(self._set_loading_visible, False)
            self.app.call_from_thread(
                self._set_status,
                f"[green]Exported successfully![/green]\n"
                f"[bold]File:[/bold] {output_path}\n\n"
                f"[dim]Press Esc to go back.[/dim]",
            )
            return "ok"

        except Exception as export_error:
            self.app.call_from_thread(self._set_loading_visible, False)
            self.app.call_from_thread(
                self._set_status,
                f"[red]Export failed: {export_error}[/red]",
            )
            return "failed"

    # ------------------------------------------------------------------
    # UI helper
    # ------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.query_one("#md-export-status", Static).update(text)

    def _set_loading_visible(self, is_visible: bool) -> None:
        """Show the loading indicator while Canvas is being read."""
        self.query_one("#md-export-loading", LoadingIndicator).display = is_visible


# =====================================================================
# ExportFormatScreen -- CSV / Excel picker
# =====================================================================
class ExportFormatScreen(CoursewrightScreen):
    """Choose CSV or Excel format for exporting analysis results.

    Presents an OptionList with two format choices.  On selection a
    background worker instantiates the correct exporter and writes
    the file.  Handles missing ``pandas``/``openpyxl`` gracefully
    for Excel exports.

    Key bindings:
    - 1      : CSV
    - 2      : Excel
    - Enter  : Activate highlighted option
    - Escape : Back
    """

    BINDINGS = [
        Binding("1", "pick(0)", "1", show=False),
        Binding("2", "pick(1)", "2", show=False),
    ]

    # (display label, file extension)
    FORMAT_OPTIONS: list[tuple[str, str]] = [
        ("CSV  -- lightweight, universal", "csv"),
        ("Excel -- formatted with multiple sheets", "xlsx"),
    ]

    def __init__(
        self,
        app_state: AppState,
        analysis,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        self.analysis = analysis
        self._export_complete: bool = False
        # Set by _dispatch_format before the worker starts
        self._chosen_format: str = "csv"

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="format-export-scroll"):
            yield Static(
                "[bold cyan]Export Results[/bold cyan]\n"
                f"[bold]Discussion:[/bold] "
                f"{self.analysis.discussion_title}\n"
                f"[bold]Students:[/bold] "
                f"{self.analysis.total_students}",
                id="format-export-header",
            )
            yield OptionList(
                *[
                    Option(
                        f"{idx}. {label}", id=f"fmt-{idx}"
                    )
                    for idx, (label, _) in enumerate(
                        self.FORMAT_OPTIONS, 1
                    )
                ],
                id="format-option-list",
            )
            yield Static(
                "[dim]Select a format to export.[/dim]",
                id="format-export-status",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Focus the option list on mount."""
        self.query_one("#format-option-list", OptionList).focus()

    # ------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------
    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Handle click / Enter on a format option."""
        self._dispatch_format(event.option_index)

    def action_pick(self, index: int) -> None:
        """Handle number key quick-select (0-based)."""
        self._dispatch_format(index)

    def _dispatch_format(self, index: int) -> None:
        """Validate the choice and start the export worker."""
        if self._export_complete:
            self.app.notify(
                "Already exported.", severity="information"
            )
            return
        if index < 0 or index >= len(self.FORMAT_OPTIONS):
            return

        _, format_extension = self.FORMAT_OPTIONS[index]
        self._chosen_format = format_extension
        self._set_status(
            f"[cyan]Exporting as "
            f"{format_extension.upper()}...[/cyan]"
        )
        self.run_worker(
            self._run_format_export,
            name="format-export",
            thread=True,
        )

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------
    async def _run_format_export(self) -> str:
        """Export the analysis to the chosen format.

        Returns ``"ok"`` on success or ``"failed"`` on error.
        """
        try:
            course_name = (
                self.app_state.current_course.name
                if self.app_state.current_course
                else None
            )

            if self._chosen_format == "csv":
                from coursewright.core.export.csv_exporter import (
                    CSVExporter,
                )

                exporter = CSVExporter()
            else:
                from coursewright.core.export.excel_exporter import (
                    ExcelExporter,
                )

                exporter = ExcelExporter()

            output_path = exporter.export_analysis(
                self.analysis,
                course_name=course_name,
            )

            self._export_complete = True
            self.app.call_from_thread(
                self._set_status,
                f"[green]Exported successfully![/green]\n"
                f"[bold]File:[/bold] {output_path}\n\n"
                f"[dim]Press Esc to go back.[/dim]",
            )
            return "ok"

        except ImportError as import_err:
            self.app.call_from_thread(
                self._set_status,
                f"[red]Missing dependency: {import_err}[/red]\n"
                "[dim]Install pandas and openpyxl for "
                "Excel export.[/dim]",
            )
            return "failed"

        except Exception as export_error:
            self.app.call_from_thread(
                self._set_status,
                f"[red]Export failed: {export_error}[/red]",
            )
            return "failed"

    # ------------------------------------------------------------------
    # UI helper
    # ------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.query_one("#format-export-status", Static).update(
            text
        )
