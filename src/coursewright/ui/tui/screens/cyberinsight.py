"""CyberInsight TUI screens for Coursewright.

Provides two Textual screens for discussion post analysis and drafting
instructor replies:

* ``CyberInsightAnalysisScreen`` — runs ``PostAnalyzer`` in a background
  worker, shows a ranked-posts DataTable (top 10), an optional
  plagiarism check, and a flagged-posts panel.
* ``DraftReplyScreen`` — displays the top-5 posts for selection,
  generates an instructor response via ``ResponseGenerator``, and
  offers a save-to-file confirmation.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Header, Static
from textual.worker import Worker, WorkerState

from coursewright.core.config.settings import AppConfig
from coursewright.core.llm.client import LLMClient, LLMConfig, LLMProvider
from coursewright.ta.discussions import (
    PostAnalyzer,
    ResponseGenerator,
    SimilarityChecker,
)
from coursewright.ta.discussions.response_generator import save_response_to_file
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal


def _build_llm_client(config: AppConfig) -> LLMClient:
    """Build an ``LLMClient`` from ``AppConfig`` settings."""
    provider_name = config.llm.provider or config.llm.default_provider or "openai"
    provider_enum = LLMProvider(provider_name)
    llm_config = LLMConfig(
        provider=provider_enum,
        api_key=config.llm.get_api_key(),
        model=config.llm.model or config.llm.default_model,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
    )
    return LLMClient(llm_config)


class _TransmissionModalMixin:
    """Shared TransmissionModal lifecycle helpers for CyberInsight screens."""

    _transmission_modal: TransmissionModal | None = None

    def _show_transmission_modal(self, status_text: str) -> None:
        """Show or update the shared traffic modal for remote Canvas/LLM work."""
        if self._transmission_modal is not None:
            self._transmission_modal.update_status_text(status_text)
            return

        transmission_modal = TransmissionModal(status_text)
        self._transmission_modal = transmission_modal
        self.app.push_screen(transmission_modal)

    def _dismiss_transmission_modal(self) -> None:
        """Close the shared traffic modal after successful remote work."""
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


# =====================================================================
# CyberInsightAnalysisScreen — ranked posts + plagiarism check
# =====================================================================
class CyberInsightAnalysisScreen(_TransmissionModalMixin, CoursewrightScreen):
    """Analyse discussion posts with CyberInsight.

    Runs the ``PostAnalyzer.analyze_discussion_batch`` pipeline in
    a background thread, then populates a top-10 ranked-posts
    DataTable and an optional plagiarism-check panel.

    Key bindings:
    - p      : Run plagiarism check on the analysed posts
    - d      : Open DraftReplyScreen for the current ranking
    - Escape : Back to previous screen
    """

    BINDINGS = [
        Binding(
            "p",
            "run_plagiarism_check",
            "Plagiarism",
            show=True,
        ),
        Binding(
            "d",
            "open_draft_reply",
            "Draft Reply",
            show=True,
        ),
    ]

    def __init__(
        self,
        app_state: AppState,
        discussion,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        self.discussion = discussion
        # Populated after the analysis worker finishes
        self._ranking = None
        self._entries: list = []
        # Track whether a plagiarism check has already been run
        self._plagiarism_checked = False
        self._transmission_modal: TransmissionModal | None = None

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="cyberinsight-scroll"):
            yield Static(
                "[bold cyan]CyberInsight Analysis[/bold cyan]\n"
                f"[bold]Discussion:[/bold] {self.discussion.title}",
                id="cyberinsight-header",
            )
            yield Static(
                "[dim]Initialising analysis...[/dim]",
                id="cyberinsight-status",
            )
            yield DataTable(
                id="cyberinsight-ranked-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static("", id="cyberinsight-flagged-panel")
            yield Static(
                "\n[bold green]Keyboard[/bold green]\n"
                "[cyan]p[/cyan]     Plagiarism check\n"
                "[cyan]d[/cyan]     Draft reply to top post\n"
                "[cyan]Esc[/cyan]   Back",
                id="cyberinsight-help",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Kick off the analysis worker on mount."""
        self.run_worker(
            self._run_analysis,
            name="cyberinsight-analysis",
            thread=True,
        )

    # ------------------------------------------------------------------
    # Background worker — analyse discussion posts
    # ------------------------------------------------------------------
    async def _run_analysis(self) -> str:
        """Run PostAnalyzer.analyze_discussion_batch in a thread.

        Returns ``"ok"`` on success or ``"failed"`` on error.
        """
        try:
            # --- 1. Initialise LLM client from config ----------------
            config = self.app_state.config

            if not config.assistant.enabled:
                self.app.call_from_thread(
                    self._set_status,
                    "[yellow]CyberInsight assistant is "
                    "disabled in config.yaml[/yellow]",
                )
                return "failed"

            if not config.llm.enabled:
                self.app.call_from_thread(
                    self._set_status,
                    "[yellow]LLM is disabled in config.yaml[/yellow]",
                )
                return "failed"

            llm_client = _build_llm_client(config)

            # --- 2. Fetch discussion entries -------------------------
            self.app.call_from_thread(
                self._show_transmission_modal,
                "Fetching discussion entries from Canvas...",
            )
            self.app.call_from_thread(
                self._set_status,
                "[cyan]Fetching discussion entries...[/cyan]",
            )

            canvas_client = self.app_state.canvas_client
            course = self.app_state.current_course
            entries = canvas_client.get_discussion_entries(
                course.id, self.discussion.id
            )
            if not entries:
                self.app.call_from_thread(self._dismiss_transmission_modal)
                self.app.call_from_thread(
                    self._set_status,
                    "[yellow]No posts found in this discussion.[/yellow]",
                )
                return "failed"

            self._entries = entries

            # --- 3. Build ranking weights from config ----------------
            ranking_weights = {
                "depth": config.assistant.ranking.depth_weight,
                "relevance": config.assistant.ranking.relevance_weight,
                "originality": config.assistant.ranking.originality_weight,
                "engagement": config.assistant.ranking.engagement_weight,
            }
            analyzer = PostAnalyzer(
                llm_client=llm_client,
                ranking_weights=ranking_weights,
            )

            # --- 4. Run the batch analysis ---------------------------
            def _progress_callback(
                current, total, description="Analysing..."
            ):
                progress_text = f"{description} ({current}/{total})"
                self.app.call_from_thread(
                    self._show_transmission_modal,
                    progress_text,
                )
                self.app.call_from_thread(
                    self._set_status,
                    f"[cyan]{progress_text}[/cyan]",
                )

            self.app.call_from_thread(
                self._show_transmission_modal,
                "Analysing posts with LLM...",
            )
            self.app.call_from_thread(
                self._set_status,
                "[cyan]Analysing posts...[/cyan]",
            )

            ranking = analyzer.analyze_discussion_batch(
                posts=entries,
                discussion_id=self.discussion.id,
                discussion_title=self.discussion.title,
                progress_callback=_progress_callback,
            )
            self._ranking = ranking

            self.app.call_from_thread(self._dismiss_transmission_modal)
            self.app.call_from_thread(
                self._set_status,
                f"[green]Analysis complete — "
                f"{ranking.total_posts} posts ranked."
                f"[/green]",
            )
            return "ok"

        except Exception as analysis_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"Analysis failed: {analysis_error}",
            )
            self.app.call_from_thread(
                self._set_status,
                f"[red]Analysis failed: {analysis_error}[/red]",
            )
            return "failed"

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def on_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        """Populate the table when analysis finishes."""
        if (
            event.worker.name == "cyberinsight-analysis"
            and event.state == WorkerState.SUCCESS
            and event.worker.result == "ok"
        ):
            self._populate_ranked_table()
            self._show_flagged_posts_panel()
        elif (
            event.worker.name == "plagiarism-check"
            and event.state == WorkerState.SUCCESS
        ):
            # Plagiarism results are shown by the worker itself
            pass

    # ------------------------------------------------------------------
    # Ranked posts table
    # ------------------------------------------------------------------
    def _populate_ranked_table(self) -> None:
        """Fill the DataTable with the top-10 ranked posts."""
        if not self._ranking:
            return

        table = self.query_one("#cyberinsight-ranked-table", DataTable)
        table.clear(columns=True)

        table.add_column("Rank", width=6)
        table.add_column("Author", width=20)
        table.add_column("Merit", width=7)
        table.add_column("Summary", width=40)
        table.add_column("Flags", width=15)

        for post in self._ranking.get_top_posts(10):
            flags_display = (
                ", ".join(f.value for f in post.flags)
                if post.flags
                else "-"
            )
            # Truncate long summaries for table readability
            summary_text = post.summary
            if len(summary_text) > 38:
                summary_text = summary_text[:35] + "..."

            table.add_row(
                str(post.rank_position),
                post.author_name[:18],
                f"{post.merit_score}/10",
                summary_text,
                flags_display,
            )

        table.focus()

    # ------------------------------------------------------------------
    # Flagged posts panel
    # ------------------------------------------------------------------
    def _show_flagged_posts_panel(self) -> None:
        """Build and display flagged-posts details below table."""
        if not self._ranking:
            return

        flagged = self._ranking.flagged_posts
        if not flagged:
            self._set_flagged_panel(
                "\n[green]No posts flagged for attention.[/green]"
            )
            return

        lines = [
            "",
            f"[bold yellow]Posts Requiring Attention "
            f"({len(flagged)})[/bold yellow]",
        ]
        for post in flagged:
            flags_display = ", ".join(
                f"[red]{f.value}[/red]" for f in post.flags
            )
            lines.append(
                f"\n[yellow]-[/yellow] "
                f"[bold]{post.author_name}[/bold] "
                f"(Post {post.post_id})"
            )
            lines.append(f"  Flags: {flags_display}")
            if post.reasoning:
                lines.append(f"  Reason: {post.reasoning}")

        self._set_flagged_panel("\n".join(lines))

    # ------------------------------------------------------------------
    # Plagiarism check action
    # ------------------------------------------------------------------
    def action_run_plagiarism_check(self) -> None:
        """Run the optional plagiarism / similarity check."""
        if not self._ranking or not self._entries:
            self.app.notify(
                "Run analysis first.",
                severity="warning",
            )
            return

        if self._plagiarism_checked:
            self.app.notify(
                "Plagiarism check already completed.",
                severity="information",
            )
            return

        self.app.push_screen(
            ConfirmModal(
                "Run plagiarism / similarity check?\n"
                "(This uses an LLM call for each "
                "suspicious pair.)",
                yes_label="Check",
                no_label="Skip",
            ),
            callback=self._handle_plagiarism_confirmed,
        )

    def _handle_plagiarism_confirmed(
        self, confirmed: bool
    ) -> None:
        """Start the plagiarism worker if user confirmed."""
        if not confirmed:
            return

        self._show_transmission_modal("Running plagiarism check...")
        self._set_status("[cyan]Running plagiarism check...[/cyan]")
        self.run_worker(
            self._run_plagiarism_check,
            name="plagiarism-check",
            thread=True,
        )

    async def _run_plagiarism_check(self) -> str:
        """Run SimilarityChecker in a background thread."""
        try:
            config = self.app_state.config
            llm_client = _build_llm_client(config)

            checker = SimilarityChecker(
                llm_client=llm_client,
                similarity_threshold=config.assistant.plagiarism_threshold,
            )
            report = checker.check_discussion(
                posts=self._entries,
                discussion_id=self.discussion.id,
                use_llm=True,
            )

            self._plagiarism_checked = True
            self.app.call_from_thread(self._dismiss_transmission_modal)

            if report.suspicious_pairs:
                pair_count = len(report.suspicious_pairs)
                self.app.call_from_thread(
                    self._set_status,
                    f"[yellow]Found {pair_count} "
                    f"suspicious pair(s).[/yellow]",
                )
            else:
                self.app.call_from_thread(
                    self._set_status,
                    "[green]No suspicious similarities detected.[/green]",
                )

            return "ok"

        except Exception as plag_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"Plagiarism check failed: {plag_error}",
            )
            self.app.call_from_thread(
                self._set_status,
                f"[red]Plagiarism check failed: {plag_error}[/red]",
            )
            return "failed"

    # ------------------------------------------------------------------
    # Draft reply navigation
    # ------------------------------------------------------------------
    def action_open_draft_reply(self) -> None:
        """Push the DraftReplyScreen with the current ranking."""
        if not self._ranking or not self._entries:
            self.app.notify(
                "Wait for analysis to complete.",
                severity="warning",
            )
            return

        self.app.push_screen(
            DraftReplyScreen(
                app_state=self.app_state,
                discussion=self.discussion,
                ranking=self._ranking,
                entries=self._entries,
            )
        )

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        """Update the status label (main-thread only)."""
        self.query_one("#cyberinsight-status", Static).update(text)

    def _set_flagged_panel(self, text: str) -> None:
        """Update the flagged-posts panel."""
        self.query_one("#cyberinsight-flagged-panel", Static).update(text)


# =====================================================================
# DraftReplyScreen — pick a top post, generate and save response
# =====================================================================
class DraftReplyScreen(_TransmissionModalMixin, CoursewrightScreen):
    """Generate an instructor response to a top-ranked post.

    Displays a DataTable of the top-5 posts.  When the user
    selects a row (Enter or number key), a background worker
    calls ``ResponseGenerator.generate_response`` and shows the
    result in a styled panel.  Pressing ``s`` saves the response
    to a markdown file.

    Key bindings:
    - 1-5    : Quick-select a post
    - Enter  : Select highlighted post
    - s      : Save response to markdown file
    - Escape : Back to analysis screen
    """

    BINDINGS = [
        Binding("1", "pick_post(0)", "1", show=False),
        Binding("2", "pick_post(1)", "2", show=False),
        Binding("3", "pick_post(2)", "3", show=False),
        Binding("4", "pick_post(3)", "4", show=False),
        Binding("5", "pick_post(4)", "5", show=False),
        Binding(
            "s",
            "save_response",
            "Save",
            show=True,
        ),
    ]

    def __init__(
        self,
        app_state: AppState,
        discussion,
        ranking,
        entries: list,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        self.discussion = discussion
        self.ranking = ranking
        self.entries = entries
        # The top-5 post analyses for the picker table
        self._top_posts = ranking.get_top_posts(5)
        # Populated after generation
        self._response_draft = None
        self._transmission_modal: TransmissionModal | None = None

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="draft-scroll"):
            yield Static(
                "[bold cyan]Draft Reply[/bold cyan]\n"
                f"[bold]Discussion:[/bold] "
                f"{self.discussion.title}\n"
                "[dim]Select a post to respond to:[/dim]",
                id="draft-header",
            )
            yield DataTable(
                id="draft-picker-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static("", id="draft-status")
            yield Static("", id="draft-response-panel")
            yield Static(
                "\n[bold green]Keyboard[/bold green]\n"
                "[cyan]1-5[/cyan]   Select post\n"
                "[cyan]Enter[/cyan] Select highlighted\n"
                "[cyan]s[/cyan]     Save response\n"
                "[cyan]Esc[/cyan]   Back",
                id="draft-help",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Populate the top-5 picker table."""
        self._populate_picker_table()

    # ------------------------------------------------------------------
    # Picker table
    # ------------------------------------------------------------------
    def _populate_picker_table(self) -> None:
        """Fill the DataTable with the top-5 posts to respond to."""
        table = self.query_one("#draft-picker-table", DataTable)
        table.clear(columns=True)

        table.add_column("#", width=4)
        table.add_column("Post ID", width=10)
        table.add_column("Author", width=20)
        table.add_column("Merit", width=8)
        table.add_column("Summary", width=45)

        for idx, post in enumerate(self._top_posts, 1):
            summary_text = post.summary
            if len(summary_text) > 43:
                summary_text = summary_text[:40] + "..."

            table.add_row(
                str(idx),
                str(post.post_id),
                post.author_name,
                f"{post.merit_score}/10",
                summary_text,
            )

        table.focus()

    # ------------------------------------------------------------------
    # Post selection
    # ------------------------------------------------------------------
    def on_data_table_row_selected(
        self, event: DataTable.RowSelected
    ) -> None:
        """Handle Enter / click on a picker row."""
        row_index = event.cursor_row
        self._select_post_by_index(row_index)

    def action_pick_post(self, index: int) -> None:
        """Handle number key quick-select (0-based)."""
        self._select_post_by_index(index)

    def _select_post_by_index(self, index: int) -> None:
        """Validate index and kick off the response worker."""
        if index < 0 or index >= len(self._top_posts):
            self.app.notify("Invalid selection.", severity="warning")
            return

        selected_analysis = self._top_posts[index]
        # Find the matching CanvasDiscussionEntry
        selected_entry = next(
            (
                entry
                for entry in self.entries
                if entry.id == selected_analysis.post_id
            ),
            None,
        )
        if not selected_entry:
            self.app.notify(
                "Could not find the selected post.",
                severity="error",
            )
            return

        status_message = f"Generating response to {selected_entry.user_name}..."
        self._show_transmission_modal(status_message)
        self._set_status(f"[cyan]{status_message}[/cyan]")
        # Store references for the worker
        self._selected_entry = selected_entry
        self._selected_analysis = selected_analysis

        self.run_worker(
            self._generate_response,
            name="generate-response",
            thread=True,
        )

    # ------------------------------------------------------------------
    # Response generation worker
    # ------------------------------------------------------------------
    async def _generate_response(self) -> str:
        """Generate an instructor response in a background thread."""
        try:
            config = self.app_state.config
            llm_client = _build_llm_client(config)

            generator = ResponseGenerator(llm_client=llm_client)
            response = generator.generate_response(
                post=self._selected_entry,
                discussion_title=self.discussion.title,
                post_analysis=self._selected_analysis,
            )
            self._response_draft = response

            self.app.call_from_thread(self._dismiss_transmission_modal)
            self.app.call_from_thread(
                self._set_status,
                f"[green]Response generated — "
                f"{response.word_count} words[/green]",
            )
            self.app.call_from_thread(
                self._show_response_panel,
                response,
            )
            return "ok"

        except Exception as gen_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"Generation failed: {gen_error}",
            )
            self.app.call_from_thread(
                self._set_status,
                f"[red]Generation failed: {gen_error}[/red]",
            )
            return "failed"

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def on_worker_state_changed(
        self, event: Worker.StateChanged
    ) -> None:
        """Handle worker completion events."""
        if (
            event.worker.name == "generate-response"
            and event.state == WorkerState.SUCCESS
            and event.worker.result == "ok"
        ):
            # Response panel is already shown by the worker via
            # call_from_thread; nothing extra needed here.
            pass

    # ------------------------------------------------------------------
    # Response display
    # ------------------------------------------------------------------
    def _show_response_panel(self, response) -> None:
        """Render the generated response in the panel."""
        student_name = response.student_name
        lines = [
            "",
            f"[bold green]Instructor Response to "
            f"{student_name}[/bold green]",
            "",
            response.final_content,
            "",
            f"[dim]Word count: {response.word_count} words[/dim]",
            "[dim]Press [bold]s[/bold] to save, "
            "[bold]Esc[/bold] to go back[/dim]",
        ]
        self.query_one("#draft-response-panel", Static).update(
            "\n".join(lines)
        )

    # ------------------------------------------------------------------
    # Save action
    # ------------------------------------------------------------------
    def action_save_response(self) -> None:
        """Save the generated response to a markdown file."""
        if not self._response_draft:
            self.app.notify(
                "No response to save — select a post "
                "and generate first.",
                severity="warning",
            )
            return

        self.app.push_screen(
            ConfirmModal(
                "Save response to markdown file?",
                yes_label="Save",
                no_label="Cancel",
            ),
            callback=self._handle_save_confirmed,
        )

    def _handle_save_confirmed(self, confirmed: bool) -> None:
        """Write the response to disk if user confirmed."""
        if not confirmed or not self._response_draft:
            return

        output_directory = Path("exports") / "cyberinsight_responses"
        output_directory.mkdir(parents=True, exist_ok=True)
        output_path = (
            output_directory
            / f"response_{self.discussion.id}"
            f"_{self._response_draft.post_id}.md"
        )

        try:
            save_response_to_file(self._response_draft, str(output_path))
            self.app.notify(
                f"Saved to {output_path}",
                severity="information",
            )
        except Exception as save_error:
            self.app.notify(
                f"Save failed: {save_error}",
                severity="error",
            )

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        """Update the status label (main-thread only)."""
        self.query_one("#draft-status", Static).update(text)
