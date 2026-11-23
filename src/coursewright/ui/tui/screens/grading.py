"""Grading Workflow Screens for the Coursewright TUI.

Provides the post-analysis grading workflow:

* ``GradingOptionsScreen`` — 9-option menu after analysis completes.
* ``GradePreviewScreen``  — student/score/percentage DataTable with
  optional LLM feedback generation and cost estimate.
* ``SubmitConfirmModal``  — safety confirmation that requires typing
  "SUBMIT" before grades are posted to Canvas.
* ``BatchGradeScreen``    — multi-select discussions, per-discussion
  progress bar, and a summary results table.

Replaces: ``show_grading_options``, ``preview_grades``,
``submit_grades``, ``batch_grade_discussions`` from
``grading_menu.py``.
"""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Header,
    Input,
    Label,
    LoadingIndicator,
    OptionList,
    ProgressBar,
    Static,
)
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from coursewright.core.canvas.safe_write import (
    SafeCanvasWriter,
    WriteFailedError,
)
from coursewright.ta.grading.scores import (
    discussion_assignment_score,
    discussion_points_settings,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal


# =====================================================================
# GradingOptionsScreen — 9-option menu after analysis
# =====================================================================
class GradingOptionsScreen(CoursewrightScreen):
    """Post-analysis grading options: preview, submit, export, etc.

    Presents the same 9 choices as the classic Rich
    ``show_grading_options`` menu but inside a Textual OptionList.

    Key bindings:
    - 1-9      : Quick-select an option
    - Enter    : Activate highlighted option
    - p        : Switch grading profile
    - Escape   : Back to analysis results
    """

    BINDINGS = [
        Binding("p", "change_profile", "Profile", show=True),
        Binding("1", "pick(0)", "1", show=False),
        Binding("2", "pick(1)", "2", show=False),
        Binding("3", "pick(2)", "3", show=False),
        Binding("4", "pick(3)", "4", show=False),
        Binding("5", "pick(4)", "5", show=False),
        Binding("6", "pick(5)", "6", show=False),
        Binding("7", "pick(6)", "7", show=False),
        Binding("8", "pick(7)", "8", show=False),
        Binding("9", "pick(8)", "9", show=False),
    ]

    # Labels for the nine grading options, mirroring grading_menu.py
    OPTION_LABELS: list[str] = [
        "Preview grades before submission",
        "Submit grades to Canvas",
        "Preview grades with AI feedback",
        "Submit grades with AI feedback",
        "Export results to file",
        "Export to Markdown (for LLM review)",
        "Return to discussion list",
        "CyberInsight: Analyze posts (rank + flags)",
        "CyberInsight: Draft reply to best post",
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
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="grading-options-scroll"):
            yield Static(
                "[bold cyan]Grading Options[/bold cyan]\n"
                f"[bold]Discussion:[/bold] {self.discussion.title}\n"
                f"[bold]Profile:[/bold] {self.app_state.grading_profile}",
                id="grading-options-header",
            )
            yield OptionList(
                *[
                    Option(f"{idx}. {label}", id=f"opt-{idx}")
                    for idx, label in enumerate(self.OPTION_LABELS, 1)
                ],
                id="grading-options-list",
            )
            yield Static(
                "\n[bold green]Keyboard[/bold green]\n"
                "[cyan]1-9[/cyan]   Select option\n"
                "[cyan]Enter[/cyan] Activate\n"
                "[cyan]Esc[/cyan]   Back",
                id="grading-options-help",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Focus the option list on mount."""
        self.query_one("#grading-options-list", OptionList).focus()

    # ------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle click / Enter on an option."""
        self._dispatch_option(event.option_index)

    def action_pick(self, index: int) -> None:
        """Handle number key quick-select (0-based)."""
        self._dispatch_option(index)

    def _dispatch_option(self, index: int) -> None:
        """Route the chosen option to the correct screen or action."""
        if index == 0:
            # 1. Preview grades (no LLM)
            self._open_grade_preview(use_llm=False)
        elif index == 1:
            # 2. Submit grades (no LLM) — still previews first
            self._open_grade_preview(use_llm=False)
        elif index == 2:
            # 3. Preview with AI feedback
            self._pick_model_then_preview(use_llm=True)
        elif index == 3:
            # 4. Submit with AI feedback
            self._pick_model_then_preview(use_llm=True)
        elif index == 4:
            # 5. Export results (CSV / Excel format picker)
            from coursewright.ui.tui.screens.export import (
                ExportFormatScreen,
            )

            self.app.push_screen(
                ExportFormatScreen(
                    app_state=self.app_state,
                    analysis=self.analysis,
                )
            )
        elif index == 5:
            # 6. Export to Markdown (for LLM review)
            from coursewright.ui.tui.screens.export import (
                MarkdownExportScreen,
            )

            self.app.push_screen(
                MarkdownExportScreen(
                    app_state=self.app_state,
                    analysis=self.analysis,
                    discussion=self.discussion,
                )
            )
        elif index == 6:
            # 7. Return to discussion list
            self.app.pop_screen()
        elif index == 7:
            # 8. CyberInsight: Analyze
            self._open_cyberinsight_analysis()
        elif index == 8:
            # 9. CyberInsight: Draft reply
            self._open_cyberinsight_analysis()

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
            header = self.query_one("#grading-options-header", Static)
            header.update(
                "[bold cyan]Grading Options[/bold cyan]\n"
                f"[bold]Discussion:[/bold] {self.discussion.title}\n"
                f"[bold]Profile:[/bold] {self.app_state.grading_profile}"
            )

        self.app.push_screen(
            ProfilePickerModal(getattr(self.app_state, "grading_profile", "auto")),
            callback=_on_profile_selected,
        )

    def _open_grade_preview(
        self,
        use_llm: bool = False,
        model_name: str | None = None,
    ) -> None:
        """Push the grade preview screen."""
        self.app.push_screen(
            GradePreviewScreen(
                app_state=self.app_state,
                analysis=self.analysis,
                discussion=self.discussion,
                use_llm=use_llm,
                model_name=model_name,
            )
        )

    def _pick_model_then_preview(self, use_llm: bool) -> None:
        """Open model picker, then push grade preview with result."""
        from coursewright.ui.tui.screens.model_picker import (
            ModelPickerScreen,
        )

        def _on_model_selected(model_name: str | None) -> None:
            if model_name:
                self._open_grade_preview(
                    use_llm=use_llm,
                    model_name=model_name,
                )

        self.app.push_screen(
            ModelPickerScreen(),
            callback=_on_model_selected,
        )

    def _open_cyberinsight_analysis(self) -> None:
        """Push the CyberInsight analysis screen."""
        from coursewright.ui.tui.screens.cyberinsight import (
            CyberInsightAnalysisScreen,
        )

        self.app.push_screen(
            CyberInsightAnalysisScreen(
                app_state=self.app_state,
                discussion=self.discussion,
            )
        )


# =====================================================================
# GradePreviewScreen — student/score/percentage table
# =====================================================================
class GradePreviewScreen(CoursewrightScreen):
    """Grade preview table with optional LLM-enhanced feedback.

    Displays a DataTable of all students with their score,
    percentage, pass/fail status, and a comment preview.
    A summary panel below shows aggregate statistics.

    Pressing ``s`` triggers submission via ``SubmitConfirmModal``.

    Key bindings:
    - s      : Submit grades to Canvas
    - Escape : Back to grading options
    """

    BINDINGS = [
        Binding("s", "submit_grades", "Submit", show=True),
    ]

    def __init__(
        self,
        app_state: AppState,
        analysis,
        discussion,
        use_llm: bool = False,
        model_name: str | None = None,
    ) -> None:
        super().__init__()
        self.app_state = app_state
        self.analysis = analysis
        self.discussion = discussion
        self.use_llm = use_llm
        self.model_name = model_name
        # Populated by the worker — list of grade dicts ready to submit
        self._grades_to_submit: list[dict] = []
        # Assignment metadata, populated by worker
        self._assignment: dict | None = None

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        llm_tag = " (AI feedback)" if self.use_llm else ""
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="preview-scroll"):
            yield Static(
                f"[bold cyan]Grade Preview{llm_tag}[/bold cyan]\n"
                f"[bold]Discussion:[/bold] {self.discussion.title}",
                id="preview-header",
            )
            yield Static(
                "[dim]Preparing grades...[/dim]",
                id="preview-status",
            )
            yield LoadingIndicator(id="preview-loading-indicator")
            yield ProgressBar(
                total=100,
                show_eta=False,
                id="preview-submit-progress",
            )
            yield DataTable(
                id="preview-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static("", id="preview-summary")
            yield Static(
                "[dim]Enter = view full feedback • s = submit • Esc = back[/dim]",
                id="preview-help",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Kick off the grade preparation worker."""
        self._prepare_grades()

    # ------------------------------------------------------------------
    # Background worker — prepare grades (and optional LLM feedback)
    # ------------------------------------------------------------------
    @work(thread=True, exclusive=True, name="prepare-grades")
    def _prepare_grades(self) -> str:
        """Build the grade list in a background thread.

        Returns ``"ok"`` on success or ``"failed"`` on error.
        """
        from coursewright.ta.grading.gradebook import GradebookManager

        transmission_modal = TransmissionModal("Preparing grade preview...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        try:
            canvas_client = self.app_state.canvas_client
            # The preview only reads Canvas.  A dry-run writer never writes
            # and never backs up, so its backup folder is never touched;
            # the real, executing writer is built at submit time.
            preview_writer = SafeCanvasWriter(canvas_client, Path("."), execute=False)
            gradebook = GradebookManager(canvas_client, preview_writer)

            # Look up the Canvas assignment linked to this discussion
            assignment = gradebook.get_assignment_for_discussion(
                self.analysis.course_id, self.discussion.id
            )
            if not assignment:
                self.app.call_from_thread(
                    self._set_status,
                    "[red]Discussion is not linked to an assignment — cannot grade.[/red]",
                )
                return "failed"

            self._assignment = assignment
            max_points = assignment.get("points_possible", 100)

            self.app.call_from_thread(
                self._set_status,
                f"[cyan]Assignment: {assignment['name']} ({max_points} pts)[/cyan]",
            )

            # Optionally initialise LLM feedback generator
            feedback_generator = None
            discussion_context = ""
            if self.use_llm:
                feedback_generator, discussion_context = self._init_llm_feedback(max_points)

            # Build per-student grade entries
            grades: list[dict] = []
            total_students = len(self.analysis.student_participation)
            for student_index, participation in enumerate(
                self.analysis.student_participation,
                start=1,
            ):
                comment = self._build_comment(
                    gradebook,
                    participation,
                    feedback_generator,
                    discussion_context,
                )

                score = discussion_assignment_score(
                    participation, max_points, rubric_points=self.analysis.rubric_points
                )
                grades.append(
                    {
                        "student_id": participation.user_id,
                        "student_name": participation.user_name,
                        "score": score,
                        "max_points": max_points,
                        "meets_requirements": participation.meets_requirements,
                        "comment": comment,
                    }
                )

                if feedback_generator:
                    self.app.call_from_thread(
                        transmission_modal.append_log_line,
                        f"Generated AI feedback for {participation.user_name}.",
                    )
                    self.app.call_from_thread(
                        self._set_status,
                        "[cyan]Generating AI feedback for "
                        f"{participation.user_name} "
                        f"({student_index}/{total_students})...[/cyan]",
                    )

            self._grades_to_submit = grades

            # Surface any LLM errors that were silently swallowed during generation.
            if feedback_generator and feedback_generator.errors:
                first_error = feedback_generator.errors[0]
                error_count = len(feedback_generator.errors)
                self.app.call_from_thread(
                    self.app.notify,
                    f"AI feedback failed for {error_count} student(s) — "
                    f"using fallback comments.\n\nError: {first_error}",
                    title="LLM Feedback Error",
                    severity="error",
                    timeout=12,
                )

            self.app.call_from_thread(
                transmission_modal.update_status_text,
                "Grade preparation complete.",
            )
            self.app.call_from_thread(self.app.pop_screen)
            return "ok"

        except Exception as prepare_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Error preparing grades: {prepare_error}",
            )
            self.app.call_from_thread(
                self._set_status,
                f"[red]Error preparing grades: {prepare_error}[/red]",
            )
            return "failed"

    def _init_llm_feedback(self, max_points: float):
        """Set up the LLM feedback generator.

        Returns ``(feedback_generator, discussion_context)`` or
        ``(None, "")`` if initialisation fails.
        """
        from coursewright.ui.cli.llm_utils import (
            LLMInitializationError,
            create_feedback_generator,
            get_cost_summary,
        )

        try:
            config = self.app_state.config
            feedback_generator, provider, model_name = create_feedback_generator(
                config,
                model_override=self.model_name,
            )

            # Extract discussion context for the prompts
            from coursewright.core.export.markdown_exporter import (
                MarkdownExporter,
            )

            exporter = MarkdownExporter()
            discussion_context = exporter.extract_discussion_context(self.discussion, self.analysis)

            # Show cost estimate in the status line
            student_count = len(self.analysis.student_participation)
            cost_summary = get_cost_summary(
                model_name,
                max_tokens=config.llm.max_tokens,
                student_count=student_count,
            )
            if cost_summary:
                self.app.call_from_thread(
                    self._set_status,
                    f"[cyan]AI feedback: "
                    f"~${cost_summary['per_student']:.4f}/student "
                    f"(~${cost_summary['total']:.2f} for "
                    f"{student_count} students)[/cyan]",
                )

            return feedback_generator, discussion_context

        except LLMInitializationError as llm_err:
            self.app.call_from_thread(
                self._set_status,
                f"[yellow]LLM init failed: {llm_err} — using standard feedback[/yellow]",
            )
            return None, ""
        except Exception as llm_err:
            self.app.call_from_thread(
                self._set_status,
                f"[yellow]LLM error: {llm_err} — using standard feedback[/yellow]",
            )
            return None, ""

    def _build_comment(
        self,
        gradebook,
        participation,
        feedback_generator,
        discussion_context: str,
    ) -> str:
        """Create the grade comment for a single student."""
        if feedback_generator:
            config = self.app_state.config
            rubric_info = {
                "substantial_post_points": (config.discussion.substantial_post_points),
                "reply_points_each": (config.discussion.reply_points_each),
                "max_replies_counted": (config.discussion.max_replies_counted),
                "min_word_count": (config.discussion.default_min_words),
                "grading_profile": getattr(self.analysis, "grading_profile", None),
            }
            return gradebook.create_llm_enhanced_comment(
                student_participation=participation,
                discussion_context=discussion_context,
                rubric_info=rubric_info,
                llm_feedback_generator=feedback_generator,
                include_score_breakdown=config.llm.include_score_breakdown,
            )

        # Standard feedback (no LLM)
        analysis_data = {
            "word_count": participation.total_word_count,
            "reply_count": participation.reply_count,
            "late_submission": False,
        }
        scoring_data = {
            "participation_score": participation.participation_score,
            "quality_score": participation.quality_score,
            "total_score": participation.total_score,
        }
        return gradebook.create_grade_comment(analysis_data, scoring_data, participation)

    # ------------------------------------------------------------------
    # Worker lifecycle callback
    # ------------------------------------------------------------------
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Populate the table once grades are prepared."""
        if (
            event.worker.name == "prepare-grades"
            and event.state == WorkerState.SUCCESS
            and event.worker.result == "ok"
        ):
            self._set_loading_indicator_visibility(False)
            self._populate_preview_table()
        elif event.worker.name == "prepare-grades" and event.state in {
            WorkerState.SUCCESS,
            WorkerState.ERROR,
            WorkerState.CANCELLED,
        }:
            self._set_loading_indicator_visibility(False)
        elif event.worker.name == "submit-grades" and event.state == WorkerState.SUCCESS:
            # Result message is already displayed by the worker
            pass

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------
    def _populate_preview_table(self) -> None:
        """Fill the DataTable with grade rows and show summary."""
        table = self.query_one("#preview-table", DataTable)
        table.clear(columns=True)

        table.add_column("Student", width=22)
        table.add_column("Score", width=8)
        table.add_column("Percent", width=9)
        table.add_column("Status", width=8)
        table.add_column("Comment", width=38)

        for grade in self._grades_to_submit:
            name = grade["student_name"]
            if len(name) > 20:
                name = name[:17] + "..."
            max_pts = grade["max_points"]
            score = grade["score"]
            pct = (score / max_pts * 100) if max_pts > 0 else 0
            status = "[green]Pass[/green]" if grade["meets_requirements"] else "[red]Fail[/red]"
            comment = grade["comment"]
            if len(comment) > 36:
                comment = comment[:33] + "..."

            table.add_row(name, f"{score:.1f}", f"{pct:.1f}%", status, comment)

        # Build summary
        self._show_summary()
        table.focus()

    def _show_summary(self) -> None:
        """Update the summary panel below the table."""
        grades = self._grades_to_submit
        total = len(grades)
        if total == 0:
            self._set_summary("[yellow]No grades to display.[/yellow]")
            return

        meeting = sum(1 for g in grades if g["meets_requirements"])
        avg_score = sum(g["score"] for g in grades) / total
        max_pts = grades[0]["max_points"] if grades else 100

        self._set_summary(
            f"\n[bold cyan]Submission Summary[/bold cyan]\n"
            f"[bold]Students:[/bold] {total}\n"
            f"[bold]Meeting Requirements:[/bold] "
            f"{meeting}/{total} "
            f"({meeting / total * 100:.1f}%)\n"
            f"[bold]Average Score:[/bold] "
            f"{avg_score:.1f}/{max_pts} "
            f"({avg_score / max_pts * 100:.1f}%)\n"
            f"\n[dim]Press [bold]s[/bold] to submit, "
            f"[bold]Esc[/bold] to go back[/dim]"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open a feedback detail modal for the selected grade row."""
        table = self.query_one("#preview-table", DataTable)
        if event.data_table.id != "preview-table":
            return

        row_index = table.cursor_row
        if row_index is None:
            return
        if row_index < 0 or row_index >= len(self._grades_to_submit):
            return

        selected_grade = self._grades_to_submit[row_index]
        self.app.push_screen(
            FeedbackDetailModal(
                student_name=selected_grade["student_name"],
                score=selected_grade["score"],
                max_points=selected_grade["max_points"],
                full_comment=selected_grade["comment"],
            )
        )

    # ------------------------------------------------------------------
    # Submission flow
    # ------------------------------------------------------------------
    def action_submit_grades(self) -> None:
        """Open the submit confirmation modal."""
        if not self._grades_to_submit or not self._assignment:
            self.app.notify("No grades prepared yet.", severity="warning")
            return

        course_name = (
            self.app_state.current_course.name if self.app_state.current_course else "Unknown"
        )
        self.app.push_screen(
            SubmitConfirmModal(
                student_count=len(self._grades_to_submit),
                course_name=course_name,
                assignment_name=self._assignment["name"],
            ),
            callback=self._handle_submit_confirmed,
        )

    def _handle_submit_confirmed(self, confirmed: bool) -> None:
        """If the user typed SUBMIT, start the submission worker."""
        if not confirmed:
            self.app.notify("Submission cancelled.", severity="warning")
            return

        self._set_submit_progress(0, len(self._grades_to_submit))
        self._set_status("[cyan]Submitting grades to Canvas...[/cyan]")
        self._submit_grades_to_canvas()

    @work(thread=True, exclusive=True, name="submit-grades")
    def _submit_grades_to_canvas(self) -> str:
        """Submit grades via the Canvas API in a background thread.

        Submits each student individually so the progress bar and status
        label can provide real-time per-student feedback.

        The user has already typed SUBMIT, so the writer executes: each
        grade is backed up to the private workspace before it is sent
        (AGENTS.md 3.11).  A grade Canvas refuses is counted and named,
        and the rest carry on, as in the source.  A failed backup stops
        the run, because nothing may be written without one.
        """
        from coursewright.ta.grading.gradebook import GradebookManager

        transmission_modal = TransmissionModal("Submitting grades to Canvas...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        try:
            canvas_client = self.app_state.canvas_client
            writer = SafeCanvasWriter(canvas_client, self.app_state.backup_directory, execute=True)
            gradebook = GradebookManager(canvas_client, writer)
            total_students = len(self._grades_to_submit)
            successful_submissions = 0
            failed_submissions = 0
            failed_student_names: list[str] = []

            for student_position, grade_entry in enumerate(
                self._grades_to_submit,
                start=1,
            ):
                student_name = str(grade_entry.get("student_name", "Unknown student"))
                self.app.call_from_thread(
                    transmission_modal.append_log_line,
                    f"Submitting {student_position}/{total_students}: {student_name}",
                )
                self.app.call_from_thread(
                    self._set_status,
                    "[cyan]Submitting grade for "
                    f"{student_name} ({student_position}/{total_students})...[/cyan]",
                )

                try:
                    outcome = gradebook.submit_grade(
                        self.analysis.course_id,
                        self._assignment["id"],
                        grade_entry["student_id"],
                        grade_entry["score"],
                        grade_entry.get("comment"),
                    )
                    submission_was_successful = outcome.executed
                except WriteFailedError as refused:
                    # A failed *backup* (BackupFailedError) is not caught
                    # here: it propagates and stops the whole run.
                    submission_was_successful = False
                    self.app.call_from_thread(
                        transmission_modal.append_log_line,
                        f"Canvas refused the grade for {student_name}: {refused}",
                    )
                if submission_was_successful:
                    successful_submissions += 1
                else:
                    failed_submissions += 1
                    failed_student_names.append(student_name)

                self.app.call_from_thread(
                    self._set_submit_progress,
                    student_position,
                    total_students,
                )

            msg = f"[green]Submitted {successful_submissions} grades[/green]"
            if failed_submissions:
                msg += f"  [red]({failed_submissions} failed)[/red]"
                failed_students_message = ", ".join(failed_student_names)
                if failed_students_message:
                    msg += f" [red]Failed: {failed_students_message}[/red]"

            self.app.call_from_thread(self._set_status, msg)
            self.app.call_from_thread(self.app.pop_screen)
            return "ok"

        except Exception as submit_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Submission error: {submit_error}",
            )
            self.app.call_from_thread(
                self._set_status,
                f"[red]Submission error: {submit_error}[/red]",
            )
            return "failed"

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        """Update the status label (main thread)."""
        self.query_one("#preview-status", Static).update(text)

    def _set_summary(self, text: str) -> None:
        """Update the summary panel (main thread)."""
        self.query_one("#preview-summary", Static).update(text)

    def _set_submit_progress(self, completed: int, total: int) -> None:
        """Update the submit progress bar (main thread)."""
        progress_bar = self.query_one("#preview-submit-progress", ProgressBar)
        progress_bar.update(progress=completed, total=total)

    def _set_loading_indicator_visibility(self, is_visible: bool) -> None:
        """Show or hide the animated loading indicator."""
        loading_indicator = self.query_one("#preview-loading-indicator", LoadingIndicator)
        loading_indicator.styles.display = "block" if is_visible else "none"


# =====================================================================
# SubmitConfirmModal — type "SUBMIT" to confirm
# =====================================================================
class SubmitConfirmModal(ModalScreen[bool]):
    """Safety confirmation for grade submission.

    The user must type the word ``SUBMIT`` into the input field
    and press Enter.  Any other value (or Escape) cancels.
    """

    DEFAULT_CSS = """
    SubmitConfirmModal {
        align: center middle;
    }

    SubmitConfirmModal > Vertical {
        width: 1fr;
        min-width: 48;
        max-width: 72;
        height: auto;
        max-height: 22;
        padding: 1 2;
        border: thick $error;
        background: $surface;
    }

    SubmitConfirmModal > Vertical > Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }

    SubmitConfirmModal > Vertical > Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }

    SubmitConfirmModal > Vertical > Horizontal > Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        student_count: int = 0,
        course_name: str = "",
        assignment_name: str = "",
    ) -> None:
        super().__init__()
        self._student_count = student_count
        self._course_name = course_name
        self._assignment_name = assignment_name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                "[bold red]FINAL CONFIRMATION[/bold red]\n\n"
                f"Submit [bold]{self._student_count}[/bold] "
                f"grades for:\n"
                f"[cyan]{self._course_name}[/cyan] — "
                f"[cyan]{self._assignment_name}[/cyan]\n\n"
                "[yellow]This action cannot be easily undone![/yellow]\n\n"
                "Type [bold]SUBMIT[/bold] to confirm:",
            )
            yield Input(
                placeholder="Type SUBMIT here...",
                id="submit-confirm-input",
            )
            with Horizontal():
                yield Button(
                    "Confirm",
                    id="submit-confirm-btn",
                    variant="error",
                )
                yield Button(
                    "Cancel",
                    id="submit-cancel-btn",
                    variant="default",
                )

    def on_mount(self) -> None:
        """Focus the input field."""
        self.query_one("#submit-confirm-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "submit-confirm-btn":
            self._try_confirm()
        else:
            self.dismiss(False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle pressing Enter in the input field."""
        self._try_confirm()

    def _try_confirm(self) -> None:
        """Dismiss with True only if the input reads 'SUBMIT'."""
        value = self.query_one("#submit-confirm-input", Input).value.strip()
        if value == "SUBMIT":
            self.dismiss(True)
        else:
            self.app.notify(
                'You must type "SUBMIT" to confirm.',
                severity="error",
            )

    def action_cancel(self) -> None:
        """Escape cancels the submission."""
        self.dismiss(False)


class FeedbackDetailModal(ModalScreen[None]):
    """Modal showing the full AI feedback comment for one student."""

    DEFAULT_CSS = """
    FeedbackDetailModal {
        align: center middle;
    }

    FeedbackDetailModal > Vertical {
        width: 1fr;
        height: 1fr;
        min-width: 48;
        min-height: 16;
        max-width: 120;
        max-height: 40;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    #feedback-detail-header {
        margin-bottom: 1;
        width: 100%;
    }

    #feedback-detail-scroll {
        height: 1fr;
        width: 100%;
        border: solid $primary;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
    ]

    def __init__(
        self,
        student_name: str,
        score: float,
        max_points: float,
        full_comment: str,
    ) -> None:
        super().__init__()
        self._student_name = student_name
        self._score = score
        self._max_points = max_points
        self._full_comment = full_comment

    def compose(self) -> ComposeResult:
        percentage = (self._score / self._max_points * 100) if self._max_points > 0 else 0
        with Vertical():
            yield Static(
                "[bold cyan]AI Feedback Detail[/bold cyan]\n"
                f"[bold]Student:[/bold] {self._student_name}\n"
                f"[bold]Score:[/bold] {self._score:.1f}/{self._max_points:.1f} "
                f"({percentage:.1f}%)\n"
                "[dim]Includes full feedback, score breakdown, and rationale.[/dim]\n"
                "[dim]Press Esc to close.[/dim]",
                id="feedback-detail-header",
            )
            with VerticalScroll(id="feedback-detail-scroll"):
                yield Static(self._full_comment, id="feedback-detail-comment")

    def action_dismiss_modal(self) -> None:
        """Close the modal with Escape."""
        self.dismiss(None)


# =====================================================================
# BatchGradeScreen — multi-select discussions + progress
# =====================================================================
class BatchGradeScreen(CoursewrightScreen):
    """Batch-grade multiple discussions in one session.

    Displays a DataTable of discussions with a toggle column.
    After selecting discussions, a background worker grades each
    one with a progress indicator, then shows a summary table.

    Key bindings:
    - Space  : Toggle selection
    - a      : Select / deselect all
    - Enter  : Start grading selected discussions
    - Escape : Back
    """

    BINDINGS = [
        Binding("a", "toggle_all", "All", show=True),
        Binding("space", "toggle_row", "Toggle", show=True),
        Binding("enter", "start_grading", "Grade", show=True),
    ]

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        # Track which discussion indices are selected
        self._selected_indices: set[int] = set()
        self._visible_discussions: list = []
        # Results populated after grading
        self._results: list[tuple] = []

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="batch-scroll"):
            yield Static(
                "[bold blue]Batch Grade Discussions[/bold blue]\n"
                "[dim]Select discussions then press Enter to "
                "start grading.[/dim]",
                id="batch-header",
            )
            yield Static(
                "[dim]Loading...[/dim]",
                id="batch-status",
            )
            yield DataTable(
                id="batch-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static("", id="batch-summary")
            yield Static(
                "\n[bold green]Keyboard[/bold green]\n"
                "[cyan]Space[/cyan]  Toggle selection\n"
                "[cyan]a[/cyan]      Select / deselect all\n"
                "[cyan]Enter[/cyan]  Start grading\n"
                "[cyan]Esc[/cyan]    Back",
                id="batch-help",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Populate the discussion table immediately."""
        self._populate_discussion_table()

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------
    def _populate_discussion_table(self) -> None:
        """Fill the table with discussions from app state."""
        discussions = self.app_state.discussions
        table = self.query_one("#batch-table", DataTable)
        table.clear(columns=True)

        table.add_column("Sel", width=5)
        table.add_column("#", width=4)
        table.add_column("Discussion", width=35)
        table.add_column("Replies", width=8)
        table.add_column("Graded", width=8)

        self._visible_discussions = list(discussions)
        self._selected_indices = set()

        for idx, disc in enumerate(discussions):
            replies = getattr(disc, "replies_count", None)
            if replies is None:
                replies = getattr(disc, "discussion_subentry_count", 0)
            graded = "Yes" if disc.assignment_id else "No"
            title = disc.title
            if len(title) > 33:
                title = title[:30] + "..."

            table.add_row(
                "[ ]",
                str(idx + 1),
                title,
                str(replies),
                graded,
            )

        count = len(discussions)
        self._set_status(
            f"[green]{count} discussions available. Select and press Enter to grade.[/green]"
        )
        table.focus()

    # ------------------------------------------------------------------
    # Selection actions
    # ------------------------------------------------------------------
    def action_toggle_row(self) -> None:
        """Toggle selection of the currently highlighted row."""
        table = self.query_one("#batch-table", DataTable)
        row_idx = table.cursor_row
        if row_idx < 0 or row_idx >= len(self._visible_discussions):
            return

        if row_idx in self._selected_indices:
            self._selected_indices.discard(row_idx)
        else:
            self._selected_indices.add(row_idx)

        self._refresh_selection_column()

    def action_toggle_all(self) -> None:
        """Select all or deselect all discussions."""
        total = len(self._visible_discussions)
        if len(self._selected_indices) == total:
            self._selected_indices.clear()
        else:
            self._selected_indices = set(range(total))
        self._refresh_selection_column()

    def _refresh_selection_column(self) -> None:
        """Redraw the Sel column checkmarks."""
        table = self.query_one("#batch-table", DataTable)
        # Rebuild all rows to reflect current selections.
        # Textual DataTable does not support per-cell updates,
        # so we clear and re-add rows.
        table.clear()
        for idx, disc in enumerate(self._visible_discussions):
            check = "[green][X][/green]" if idx in self._selected_indices else "[ ]"
            replies = getattr(disc, "replies_count", None)
            if replies is None:
                replies = getattr(disc, "discussion_subentry_count", 0)
            graded = "Yes" if disc.assignment_id else "No"
            title = disc.title
            if len(title) > 33:
                title = title[:30] + "..."
            table.add_row(check, str(idx + 1), title, str(replies), graded)

        count = len(self._selected_indices)
        self._set_status(f"[green]{count} discussion(s) selected.[/green]")

    # ------------------------------------------------------------------
    # Grading worker
    # ------------------------------------------------------------------
    def action_start_grading(self) -> None:
        """Validate selection and kick off the grading worker."""
        if not self._selected_indices:
            self.app.notify("No discussions selected.", severity="warning")
            return

        self._set_status("[cyan]Grading in progress...[/cyan]")
        self.run_worker(
            self._run_batch_grading,
            name="batch-grade",
            thread=True,
        )

    async def _run_batch_grading(self) -> str:
        """Grade each selected discussion sequentially.

        Returns ``"ok"`` when done (even if individual items fail).
        """
        from coursewright.ta.grading.analyzer import DiscussionAnalyzer

        course = self.app_state.current_course
        canvas_client = self.app_state.canvas_client
        if not course or not canvas_client:
            return "failed"

        selected = [self._visible_discussions[i] for i in sorted(self._selected_indices)]
        total = len(selected)

        grading_profile = getattr(self.app_state, "grading_profile", "auto")
        grading_config = {
            "min_words": 300,
            "min_replies": 2,
            "rubric_file": None,
            "profile": grading_profile,
            **discussion_points_settings(self.app_state.config.discussion),
        }
        analyzer = DiscussionAnalyzer(canvas_client, grading_config)

        results: list[tuple] = []
        for position, disc in enumerate(selected, 1):
            self.app.call_from_thread(
                self._set_status,
                f"[cyan]Grading {position}/{total}: {disc.title[:30]}...[/cyan]",
            )
            try:
                analysis = analyzer.analyze_discussion(course.id, disc.id)
                if analysis:
                    results.append((disc, analysis))
            except Exception as grade_err:
                self.app.call_from_thread(
                    self._set_status,
                    f"[red]Error: {disc.title[:25]} — {grade_err}[/red]",
                )

        self._results = results

        self.app.call_from_thread(
            self._set_status,
            f"[green]Graded {len(results)}/{total} discussions successfully.[/green]",
        )
        return "ok"

    # ------------------------------------------------------------------
    # Worker lifecycle callback
    # ------------------------------------------------------------------
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Show the summary table when batch grading finishes."""
        if (
            event.worker.name == "batch-grade"
            and event.state == WorkerState.SUCCESS
            and event.worker.result == "ok"
        ):
            self._show_batch_summary()

    def _show_batch_summary(self) -> None:
        """Build and display the batch grading results table."""
        if not self._results:
            self._set_summary("[yellow]No discussions were graded successfully.[/yellow]")
            return

        lines = ["\n[bold cyan]Batch Grading Results[/bold cyan]\n"]
        # Build a simple text table (the main DataTable is still
        # showing the selection list)
        header = f"{'Discussion':<30} {'Students':>8} {'Avg':>8} {'Meeting':>10}"
        lines.append(f"[bold]{header}[/bold]")
        lines.append("-" * 60)

        for disc, analysis in self._results:
            title = disc.title[:28]
            if len(disc.title) > 28:
                title = disc.title[:25] + "..."
            students = len(analysis.student_participation)
            avg = f"{analysis.average_score:.1f}"
            meeting = f"{analysis.students_meeting_requirements}/{analysis.total_students}"
            lines.append(f"{title:<30} {students:>8} {avg:>8} {meeting:>10}")

        total_graded = len(self._results)
        total_selected = len(self._selected_indices)
        lines.append("")
        lines.append(f"[green]Graded {total_graded}/{total_selected} discussions.[/green]")

        self._set_summary("\n".join(lines))

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.query_one("#batch-status", Static).update(text)

    def _set_summary(self, text: str) -> None:
        self.query_one("#batch-summary", Static).update(text)
