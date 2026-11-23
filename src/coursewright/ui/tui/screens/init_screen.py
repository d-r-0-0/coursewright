"""Initialization & Welcome screen for the Coursewright TUI.

Connects to the Canvas API, optionally initialises the LLM client,
loads courses, and displays connection status.  Every step talks to a
remote service, so the screen shows a loading indicator while the
worker runs (docs/HOUSE_STYLE.md section 3: no silent waits).  Once
everything is ready the screen shows a welcome summary panel and moves
on to the course list.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Center, VerticalScroll
from textual.widgets import Footer, Header, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

from coursewright.core.config.semester import semester_matches_term
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState

# -- Status-line helpers ---------------------------------------------------
# Each step has a "pending" and "done" version so the user sees progress.
_STEP_CANVAS = "canvas"
_STEP_LLM = "llm"
_STEP_COURSES = "courses"

_PENDING_ICON = "[dim]⏳[/dim]"
_OK_ICON = "[green]✅[/green]"
_WARN_ICON = "[yellow]⚠️[/yellow]"
_FAIL_ICON = "[red]❌[/red]"


class InitScreen(CoursewrightScreen):
    """First screen the user sees when ``coursewright tui`` starts.

    Lifecycle:
    1. ``on_mount`` kicks off a Textual **worker** that runs the
       blocking Canvas / LLM / course-loading steps off the main
       thread so the loading indicator stays animated.
    2. As each step completes the status labels update in real-time.
    3. On success the welcome summary panel appears.
    4. On failure an error panel is shown and the user can press
       ``q`` to quit.
    """

    # The AppState instance is injected by CoursewrightApp before
    # this screen is pushed.  Keeping it as a plain attribute (not a
    # reactive) is fine — we only write to it from the worker and
    # read from compose/update helpers.
    app_state: AppState

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        """Build the widget tree: header, status area, footer."""
        yield Header(show_clock=True)
        with VerticalScroll(id="init-scroll"):
            with Center():
                yield Static(
                    "[bold blue]Coursewright[/bold blue]\n"
                    "[dim]AI teaching assistant for Canvas[/dim]",
                    id="init-title",
                )
            yield LoadingIndicator(id="init-loading")
            yield Static(
                f"  {_PENDING_ICON}  Connecting to Canvas…",
                id="status-canvas",
            )
            yield Static(
                f"  {_PENDING_ICON}  Initialising LLM client…",
                id="status-llm",
            )
            yield Static(
                f"  {_PENDING_ICON}  Loading courses…",
                id="status-courses",
            )
            # This widget is hidden until init finishes; it shows the
            # welcome summary or an error message.
            yield Static("", id="init-result")
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        """Start the background initialisation worker."""
        self.run_worker(
            self._run_initialisation,
            name="init-worker",
            thread=True,
        )

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------
    async def _run_initialisation(self) -> str:
        """Run Canvas + LLM + course-loading steps.

        Returns a short status string used by ``on_worker_state_changed``
        to decide whether to show the success or error panel.  Heavy
        imports are deferred so the module loads quickly even when deps
        are missing.
        """
        # -- Step 1: Canvas client ----------------------------------------
        canvas_ok = await self._initialise_canvas_client()
        if not canvas_ok:
            return "canvas_failed"

        # -- Step 2: LLM client (non-fatal) --------------------------------
        await self._initialise_llm_client()

        # -- Step 3: Load courses ------------------------------------------
        courses_ok = await self._load_courses_from_canvas()
        if not courses_ok:
            return "courses_failed"

        return "ok"

    # -- Step helpers (each updates its status widget) ---------------------

    async def _initialise_canvas_client(self) -> bool:
        """Create the Canvas API client and test the connection."""
        try:
            from coursewright.core.canvas.client import create_canvas_client
            config = self.app_state.config

            client = create_canvas_client(
                base_url=config.discussion.canvas_url,
                api_token_file=config.discussion.api_token_file,
                on_status=self._show_authentication_status,
            )

            if not client:
                self._update_status(
                    _STEP_CANVAS,
                    _FAIL_ICON,
                    "Failed to connect to Canvas",
                )
                self._show_error(
                    "Could not initialise the Canvas client.\n"
                    "Check your API key and Canvas URL in "
                    "[bold]config.yaml[/bold]."
                )
                return False

            self.app_state.canvas_client = client
            self.app_state.grading_profile = config.discussion.default_grading_profile
            self._update_status(
                _STEP_CANVAS,
                _OK_ICON,
                "Connected to Canvas",
            )
            return True

        except Exception as canvas_error:
            self._update_status(
                _STEP_CANVAS,
                _FAIL_ICON,
                f"Canvas error: {escape(str(canvas_error))}",
            )
            self._show_error(f"Error connecting to Canvas:\n[red]{escape(str(canvas_error))}[/red]")
            return False

    async def _initialise_llm_client(self) -> None:
        """Create the LLM client.  Failures are non-fatal."""
        try:
            from coursewright.core.llm.client import LLMProvider, create_client

            config = self.app_state.config

            self.app_state.llm_client = create_client(
                provider=LLMProvider(config.llm.provider),
                model=config.llm.model,
                api_key=config.llm.get_api_key(),
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
            )
            self._update_status(
                _STEP_LLM,
                _OK_ICON,
                "LLM client ready",
            )

        except Exception as llm_error:
            # LLM is optional — warn but continue.
            self._update_status(
                _STEP_LLM,
                _WARN_ICON,
                f"LLM unavailable: {escape(str(llm_error))}",
            )

    async def _load_courses_from_canvas(self) -> bool:
        """Fetch courses from Canvas and populate ``app_state``."""
        try:
            from coursewright.core.canvas.models import CanvasCourse

            courses_data = self.app_state.canvas_client.get_courses()

            self.app_state.courses = [
                CanvasCourse(
                    id=course.get("id"),
                    name=course.get("name", "Unknown"),
                    course_code=course.get("course_code", ""),
                    workflow_state=course.get("workflow_state", "available"),
                    term=course.get("term", {}),
                    total_students=course.get("total_students", 0),
                    last_participation_at=course.get("last_participation_at"),
                )
                for course in courses_data
            ]

            # Apply default semester filter (mirrors the course list)
            self._apply_default_course_filter()

            course_count = len(self.app_state.courses)
            filtered_count = len(self.app_state.filtered_courses)
            self._update_status(
                _STEP_COURSES,
                _OK_ICON,
                f"Loaded {course_count} courses ({filtered_count} this semester)",
            )
            return True

        except Exception as courses_error:
            self._update_status(
                _STEP_COURSES,
                _FAIL_ICON,
                f"Course loading failed: {escape(str(courses_error))}",
            )
            self._show_error(
                f"Could not load courses from Canvas:\n[red]{escape(str(courses_error))}[/red]"
            )
            return False

    # ------------------------------------------------------------------
    # UI update helpers (called from the worker thread via call_from_thread)
    # ------------------------------------------------------------------
    def _show_authentication_status(self, message: str) -> None:
        """Relay a sign-in progress message from the Canvas client."""
        self._update_status(_STEP_CANVAS, _PENDING_ICON, escape(message))

    def _update_status(self, step: str, icon: str, message: str) -> None:
        """Update one of the three status lines."""
        widget = self.query_one(f"#status-{step}", Static)
        self.app.call_from_thread(widget.update, f"  {icon}  {message}")

    def _show_error(self, message: str) -> None:
        """Display a red error panel in the result area."""
        result_widget = self.query_one("#init-result", Static)
        error_text = (
            "\n[bold red]Initialisation Failed[/bold red]\n\n"
            f"{message}\n\n"
            "[dim]Press [bold]q[/bold] to quit.[/dim]"
        )
        self.app.call_from_thread(result_widget.update, error_text)

    def _hide_loading_indicator(self) -> None:
        """Remove the loading indicator once the worker has finished."""
        self.query_one("#init-loading", LoadingIndicator).display = False

    def _show_welcome_summary(self) -> None:
        """Display the success welcome panel after all steps pass."""
        course_count = len(self.app_state.courses)
        filtered_count = len(self.app_state.filtered_courses)
        semester = self.app_state.current_semester

        welcome_text = (
            "\n[bold green]Setup Complete[/bold green]\n\n"
            "[green]✅ Connected to Canvas[/green]\n"
            f"[green]✅ Loaded {course_count} courses[/green]\n\n"
            f"[bold yellow]Current Semester: {semester}[/bold yellow]\n"
            f"[dim]Showing {filtered_count} courses "
            f"for this semester.[/dim]\n\n"
            "[dim]Loading course list...[/dim]"
        )
        result_widget = self.query_one("#init-result", Static)
        result_widget.update(welcome_text)

    def _transition_to_course_list(self) -> None:
        """Transition to the course list screen after initialization.

        Called from the worker callback on successful init.  Pushes the
        CourseListScreen which will display the loaded courses.
        """
        # Import here to avoid circular dependency
        from coursewright.ui.tui.screens.course_list import CourseListScreen

        # on_worker_state_changed runs on the main thread, so push directly
        self.app.push_screen(CourseListScreen(self.app_state))

    # ------------------------------------------------------------------
    # Worker lifecycle callback
    # ------------------------------------------------------------------
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """React when the init worker finishes (success or error)."""
        if event.worker.name != "init-worker":
            return
        if event.state in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            self._hide_loading_indicator()
        if event.state == WorkerState.SUCCESS and event.worker.result == "ok":
            self._show_welcome_summary()
            # Transition to course list screen after successful init
            self._transition_to_course_list()

    # ------------------------------------------------------------------
    # Course filter (lightweight copy of the course list's filter)
    # ------------------------------------------------------------------
    def _apply_default_course_filter(self) -> None:
        """Apply the default semester + status filter."""
        state = self.app_state
        filtered = state.courses

        # Filter by workflow state
        if state.filter_status == "active":
            filtered = [
                c for c in filtered if getattr(c, "workflow_state", "available") == "available"
            ]

        # Filter by current semester
        if state.show_current_semester_only and state.current_semester:
            filtered = [
                c
                for c in filtered
                if semester_matches_term(
                    state.current_semester,
                    self._extract_term_name(getattr(c, "term", None)),
                )
            ]

        state.filtered_courses = filtered

    @staticmethod
    def _extract_term_name(term) -> str:
        """Extract term name from a dict or string.

        Canvas returns ``term`` as a dict like ``{"name": "Fall 2025"}``
        but ``semester_matches_term`` expects a plain string.
        """
        if not term:
            return "Unknown"
        if isinstance(term, dict):
            return term.get("name", "Unknown")
        return str(term)
