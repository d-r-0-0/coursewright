"""Teaching-assistant commands registered under ``coursewright ta``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from coursewright.core.canvas import create_canvas_client, load_api_key
from coursewright.core.canvas.models import (
    CanvasCourse,
    CanvasDiscussion,
    DiscussionAnalysis,
    StudentParticipation,
)
from coursewright.core.canvas.safe_write import default_backup_directory
from coursewright.core.config import AppConfig, ExportFormat
from coursewright.core.config.manager import resolve_workspace_directory
from coursewright.core.llm import create_client
from coursewright.ta.attendance.outreach import (
    find_students_inactive_for_threshold,
    load_attendance_warning_template,
    render_warning_for_student,
    send_canvas_conversation_message,
)
from coursewright.ta.attendance.templates import OutreachContact
from coursewright.ta.gradeliner.orchestrator import GradeLinerUpper
from coursewright.ui.cli.activity import RemoteActivityDisplay
from coursewright.ui.cli.doctor import doctor_command
from coursewright.ui.cli.gradeliner_menu import (
    _run_mapping_setup,
    _show_grade_preview_table,
    run_gradeliner_flow,
)
from coursewright.ui.cli.honor_credit import honor_credit_command
from coursewright.ui.cli.packet_tracer import packet_tracer_command
from coursewright.ui.cli.zero_ungraded import zero_ungraded_command
from coursewright.ui.cli.zybooks_import import zybooks_import_command

console = Console()

interactive_app = typer.Typer(
    name="interactive",
    help="Deprecated compatibility routes for the former TA interactive menu.",
)


def register_ta_commands(ta_app: typer.Typer) -> None:
    """Register the top-level TA commands on ``ta_app``."""
    ta_app.add_typer(interactive_app, name="interactive")
    ta_app.command("start")(start)
    ta_app.command("menu")(menu)
    ta_app.command("status")(status)
    ta_app.command("setup")(setup)
    ta_app.command(
        "netacad-import",
        help="Import NetAcad grades into Canvas (dry-run by default).",
    )(gradeliner)
    ta_app.command(
        "gradeliner",
        help="Import NetAcad grades into Canvas (dry-run by default; alias for netacad-import).",
    )(gradeliner)
    ta_app.command("zybooks-import")(zybooks_import_command)
    ta_app.command("zero-ungraded")(zero_ungraded_command)
    ta_app.command("honor-credit")(honor_credit_command)
    ta_app.command("packet-tracer")(packet_tracer_command)
    ta_app.command("doctor")(doctor_command)
    ta_app.command("attendance-warning")(attendance_warning)
    ta_app.command("demo")(demo)


def _get_startup_llm_status(config: AppConfig) -> tuple[bool, str]:
    """Check whether an LLM API key is available without blocking startup."""
    if not config.llm.enabled:
        return True, "LLM features disabled in config"

    llm_api_key = config.llm.get_api_key()
    if llm_api_key.strip():
        return True, "LLM key detected"

    return False, "LLM key missing (optional)"


def _show_startup_validation_panel(
    canvas_is_valid: bool,
    canvas_message: str,
    llm_is_ready: bool,
    llm_message: str,
) -> None:
    """Render a Rich status panel for startup API checks."""
    startup_status_table = Table(show_header=True, header_style="bold magenta")
    startup_status_table.add_column("Service", style="cyan", no_wrap=True)
    startup_status_table.add_column("Status", style="bold")
    startup_status_table.add_column("Details", style="white")

    canvas_icon = "OK" if canvas_is_valid else "FAIL"
    canvas_color = "green" if canvas_is_valid else "red"
    startup_status_table.add_row(
        "Canvas API", f"[{canvas_color}]{canvas_icon}[/{canvas_color}]", canvas_message
    )

    llm_icon = "OK" if llm_is_ready else "WARN"
    llm_color = "green" if llm_is_ready else "yellow"
    startup_status_table.add_row(
        "LLM API", f"[{llm_color}]{llm_icon}[/{llm_color}]", llm_message
    )

    panel_border_color = "green" if canvas_is_valid else "red"
    console.print(
        Panel(
            startup_status_table,
            title="Startup Validation",
            border_style=panel_border_color,
            expand=False,
        )
    )


def _run_startup_api_validation(config: AppConfig) -> bool:
    """Run startup API checks inside RemoteActivityDisplay and show a status panel."""
    with RemoteActivityDisplay("Validating Canvas connection", console=console):
        canvas_client = create_canvas_client(
            base_url=config.discussion.canvas_url,
            api_key=None,
        )

    if canvas_client is not None:
        auth_strategy = getattr(canvas_client, "_auth_strategy", None)
        auth_mode = (
            auth_strategy.describe()
            if auth_strategy is not None and hasattr(auth_strategy, "describe")
            else "Canvas client"
        )
        canvas_is_valid = True
        canvas_message = f"Connected via {auth_mode}"
    else:
        canvas_is_valid = False
        canvas_message = (
            "Could not authenticate to Canvas. Browser fallback either failed or was cancelled."
        )

    llm_is_ready, llm_message = _get_startup_llm_status(config)
    _show_startup_validation_panel(
        canvas_is_valid=canvas_is_valid,
        canvas_message=canvas_message,
        llm_is_ready=llm_is_ready,
        llm_message=llm_message,
    )
    return canvas_is_valid


def start(ctx: typer.Context) -> None:
    """Validate connections and launch the interactive Textual application."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    if not _run_startup_api_validation(config):
        raise typer.Exit(1) from None

    from coursewright.ui.tui.app import run_tui

    run_tui(config=config, config_file=obj.get("config_file", "config.yaml"))


def _show_menu_deprecation_notice() -> None:
    """Explain that a legacy TA menu route now opens the Textual application."""
    console.print(
        Panel(
            "This compatibility route is deprecated and will be removed in a future "
            "release. Launch the Textual application directly with "
            "[cyan]coursewright ta start[/cyan].",
            title="[yellow]Interactive menu moved[/yellow]",
            border_style="yellow",
        )
    )


def menu(ctx: typer.Context) -> None:
    """Deprecated: launch the TA app; use ``coursewright ta start`` instead."""
    _show_menu_deprecation_notice()
    start(ctx)


@interactive_app.command("menu")
def interactive_menu(ctx: typer.Context) -> None:
    """Deprecated: launch the TA app; use ``coursewright ta start`` instead."""
    menu(ctx)


def status(ctx: typer.Context) -> None:
    """Show current TA status and configuration."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    table = Table(title="Coursewright TA Status", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    table.add_row("Canvas URL", config.discussion.canvas_url)
    table.add_row("API Token File", config.discussion.api_token_file)
    table.add_row("Default Min Words", str(config.discussion.default_min_words))
    table.add_row("Default Min Replies", str(config.discussion.default_min_replies))
    table.add_row("Default Grading Profile", config.discussion.default_grading_profile)
    table.add_row("Export Format", config.discussion.export.format.value)
    table.add_row("Export Directory", config.discussion.export.output_directory)

    token_file = Path(config.discussion.api_token_file)
    if token_file.exists():
        table.add_row("API Token", "[green]Found[/green]")
    else:
        table.add_row("API Token", "[red]Not found[/red]")

    if config.cli.default_course_id:
        table.add_row("Default Course", str(config.cli.default_course_id))
    else:
        table.add_row("Default Course", "[dim]Not set[/dim]")

    console.print(table)

    if token_file.exists():
        try:
            token = token_file.read_text(encoding="utf-8").strip()
            with RemoteActivityDisplay("Testing Canvas connection", console=console):
                client = create_canvas_client(
                    base_url=config.discussion.canvas_url, api_key=token
                )
                courses = client.get_courses() if client else []
            if client:
                console.print(f"[green]Connected! Found {len(courses)} courses.[/green]")
            else:
                console.print("[red]Connection failed![/red]")
        except Exception as err:
            console.print(f"[red]Connection error: {err}[/red]")


def setup() -> None:
    """Interactive setup wizard for first-time users."""
    console.print(
        Panel.fit(
            "[bold blue]Welcome to Coursewright![/bold blue]\n\n"
            "This setup wizard will help you configure Coursewright for first use.",
            title="Setup Wizard",
            border_style="blue",
        )
    )

    config = AppConfig()

    console.print("\n[bold cyan]Step 1: Canvas URL[/bold cyan]")
    console.print("Enter your institution's Canvas URL (e.g., https://canvas.example.edu)")
    canvas_url = typer.prompt("Canvas URL", default=config.discussion.canvas_url)
    config.discussion.canvas_url = canvas_url

    console.print("\n[bold cyan]Step 2: API Token[/bold cyan]")
    has_token = typer.confirm("Do you have an API token?")
    token = ""
    if has_token:
        token = typer.prompt("Enter your API token", hide_input=True)
        token_file = Path(config.discussion.api_token_file)
        token_file.write_text(token, encoding="utf-8")
        console.print(f"[green]Token saved to {token_file}[/green]")
    else:
        console.print(
            "[yellow]You will need to set up an API token later using: "
            "coursewright auth login[/yellow]"
        )

    console.print("\n[bold cyan]Step 3: Grading Preferences[/bold cyan]")
    config.discussion.default_min_words = typer.prompt(
        "Minimum words for discussion posts", default=config.discussion.default_min_words
    )
    config.discussion.default_min_replies = typer.prompt(
        "Minimum required replies", default=config.discussion.default_min_replies
    )

    console.print("\n[bold cyan]Step 4: Export Preferences[/bold cyan]")
    export_formats = ["csv", "xlsx", "json"]
    console.print(f"Available export formats: {', '.join(export_formats)}")
    export_format = typer.prompt(
        "Default export format", default=config.discussion.export.format.value
    )
    if export_format in export_formats:
        config.discussion.export.format = ExportFormat(export_format)

    config.discussion.export.output_directory = typer.prompt(
        "Export directory", default=config.discussion.export.output_directory
    )

    config_file = "config.yaml"
    config.save_to_file(config_file)
    console.print(f"\n[green]Configuration saved to {config_file}[/green]")

    if has_token and token:
        try:
            with RemoteActivityDisplay("Testing Canvas connection", console=console):
                client = create_canvas_client(base_url=canvas_url, api_key=token)
                courses = client.get_courses() if client else []
            if client:
                console.print(f"[green]Setup complete! Found {len(courses)} courses.[/green]")
                console.print(
                    Panel.fit(
                        "[bold green]Setup Complete![/bold green]\n\n"
                        "Quick start commands:\n"
                        "• [cyan]coursewright ta courses list[/cyan] - List your courses\n"
                        "• [cyan]coursewright ta discussions list COURSE_ID[/cyan] "
                        "- List discussions\n"
                        "• [cyan]coursewright ta discussions grade DISCUSSION_ID[/cyan] "
                        "- Grade discussion\n"
                        "• [cyan]coursewright ta assistant analyze DISCUSSION_ID[/cyan] "
                        "- Analyze with Assistant\n"
                        "• [cyan]coursewright ta status[/cyan] - Check current status",
                        title="Next Steps",
                        border_style="green",
                    )
                )
            else:
                console.print("[red]Connection failed! Please check your token and URL.[/red]")
        except Exception as err:
            console.print(f"[red]Connection error: {err}[/red]")


def gradeliner(
    ctx: typer.Context,
    netacad: Path | None = typer.Option(
        None, "--netacad", "-n", help="Path to NetAcad export file"
    ),
    course_id: int | None = typer.Option(None, "--course", "-c", help="Canvas Course ID"),
    remap: bool = typer.Option(False, "--remap", help="Force re-mapping (LLM calls)"),
    execute: bool = typer.Option(
        False, "--execute", help="Write grades to Canvas (default is dry-run)"
    ),
) -> None:
    """Import NetAcad grades into Canvas (dry-run by default)."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    if not netacad and not course_id:
        console.print(
            "[yellow]Usage: coursewright ta netacad-import --netacad FILE.csv --course ID[/yellow]"
        )
        console.print("[dim]Or run 'coursewright tui' for interactive mode.[/dim]")
        return

    token = load_api_key(config.discussion.api_token_file)
    with RemoteActivityDisplay("Connecting to Canvas", console=console):
        canvas_client = create_canvas_client(config.discussion.canvas_url, token)

    def get_llm_client():
        return create_client(
            provider=config.llm.provider,
            model=config.llm.model,
            api_key=config.llm.get_api_key(),
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )

    workspace = resolve_workspace_directory(config)
    if workspace is None:
        console.print("[red]Configure a private workspace for grade mappings.[/red]")
        raise typer.Exit(1)

    if netacad and course_id:
        with RemoteActivityDisplay("Loading course mappings", console=console):
            orchestrator = GradeLinerUpper(
                canvas_client, None, course_id, netacad, export_root=workspace / "exports"
            )

        if not orchestrator.has_cached_mappings() or remap:
            llm_client = get_llm_client()
            with RemoteActivityDisplay("Initializing NetAcad Import with LLM", console=console):
                orchestrator = GradeLinerUpper(
                    canvas_client, llm_client, course_id, netacad, export_root=workspace / "exports"
                )
            _run_mapping_setup(orchestrator)

        with RemoteActivityDisplay("Preparing grade import", console=console):
            import_rows = orchestrator.prepare_grade_import()
        _show_grade_preview_table(import_rows)

        if not execute:
            console.print(
                "\n[yellow]DRY RUN — No grades were written (pass --execute to write).[/yellow]"
            )
            return

        if Confirm.ask("\n[bold cyan]Submit to Canvas?[/bold cyan]", default=True):
            with RemoteActivityDisplay(
                "Submitting grades to Canvas", total=len(import_rows), console=console
            ) as activity:
                results = orchestrator.submit_grades(
                    import_rows, execute=True, backup_directory=default_backup_directory(workspace)
                )
                activity.advance(len(import_rows))
            console.print(
                f"[green]Done:[/green] {results['successful']} Succeeded, "
                f"{results['failed']} Failed"
            )
    elif course_id:
        run_gradeliner_flow(canvas_client, get_llm_client(), course_id, execute=execute)


def _render_flagged_student_preview_table(flagged_students: list) -> None:
    """Render a Rich preview table of inactive students."""
    table = Table(
        title=f"Inactive Students ({len(flagged_students)})",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Student", style="cyan")
    table.add_column("Last Activity", style="yellow")
    table.add_column("Signal", style="white")

    for row_number, student in enumerate(flagged_students, start=1):
        last_at = (
            student.last_activity_at.date().isoformat()
            if student.last_activity_at
            else "No recorded activity"
        )
        table.add_row(
            str(row_number),
            student.student_name,
            last_at,
            student.last_activity_description,
        )
    console.print(table)


def attendance_warning(
    ctx: typer.Context,
    course_id: int = typer.Option(..., "--course-id", help="Canvas course ID"),
    deadline_days: int = typer.Option(
        3, "--deadline-days", help="Deadline offset for the warning message"
    ),
    inactivity_days: int = typer.Option(14, "--inactive-days", help="Inactivity threshold"),
    execute: bool = typer.Option(
        False, "--execute", help="Send Canvas messages (default is dry-run)"
    ),
) -> None:
    """Send attendance warning messages to students inactive 14+ days."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    token = load_api_key(config.discussion.api_token_file)
    with RemoteActivityDisplay("Connecting to Canvas", console=console):
        canvas_client = create_canvas_client(config.discussion.canvas_url, token)
    if canvas_client is None:
        console.print("[red]Could not initialize Canvas client.[/red]")
        raise typer.Exit(1) from None

    with RemoteActivityDisplay("Loading activity signals from Canvas", console=console):
        flagged_students = find_students_inactive_for_threshold(
            canvas_client=canvas_client,
            course_id=course_id,
            inactivity_days_threshold=inactivity_days,
        )

    if not flagged_students:
        console.print("[green]No students are inactive for the selected threshold.[/green]")
        return

    _render_flagged_student_preview_table(flagged_students)
    deselected_rows_text = typer.prompt(
        "Enter row numbers to skip (comma-separated) or press Enter",
        default="",
    )
    deselected_row_numbers = {
        int(item.strip())
        for item in deselected_rows_text.split(",")
        if item.strip().isdigit()
    }

    selected_students = [
        flagged_student
        for row_number, flagged_student in enumerate(flagged_students, start=1)
        if row_number not in deselected_row_numbers
    ]
    if not selected_students:
        console.print("[yellow]No recipients selected. Aborting.[/yellow]")
        return

    workspace = resolve_workspace_directory(config)
    template_path = workspace / "templates/attendance_warning.md" if workspace else None
    template_text = load_attendance_warning_template(
        template_path=template_path if template_path and template_path.exists() else None
    )
    with RemoteActivityDisplay(f"Fetching course {course_id} details", console=console):
        course = canvas_client.get_course(course_id)
    course_name = course.get("name", f"Course {course_id}") if course else f"Course {course_id}"
    contact = OutreachContact(**config.outreach.model_dump())
    prepared_messages = [
        (
            student,
            *render_warning_for_student(
                student, course_name, template_text, contact, deadline_days=deadline_days
            ),
        )
        for student in selected_students
    ]

    console.print(f"[cyan]Prepared {len(selected_students)} warning message(s).[/cyan]")
    if not execute:
        console.print(
            "[yellow]DRY RUN — No messages were sent (pass --execute to send).[/yellow]"
        )
        return

    if workspace is None:
        console.print("[red]Configure a private workspace for message backups.[/red]")
        raise typer.Exit(1)

    if not Confirm.ask("Send messages now?", default=False):
        console.print("[yellow]Message send cancelled by user.[/yellow]")
        return

    successful_sends = 0
    failed_sends = 0
    with RemoteActivityDisplay(
        "Sending Canvas conversations", total=len(selected_students), console=console
    ) as activity:
        for flagged_student, subject_line, message_body in prepared_messages:
            send_succeeded, send_detail = send_canvas_conversation_message(
                canvas_client=canvas_client,
                student_id=flagged_student.student_id,
                subject_line=subject_line,
                message_body=message_body,
                course_id=course_id,
                execute=True,
                backup_directory=default_backup_directory(workspace),
            )
            if send_succeeded:
                successful_sends += 1
            else:
                failed_sends += 1
                console.print(
                    f"Message to {flagged_student.student_name} failed: {send_detail}",
                    markup=False,
                    style="red",
                )
            activity.advance()

    console.print(
        Panel.fit(
            f"[green]Sent:[/green] {successful_sends}\n[red]Failed:[/red] {failed_sends}",
            title="Attendance Warning Results",
            border_style="green" if failed_sends == 0 else "yellow",
        )
    )
    if failed_sends:
        raise typer.Exit(1)


def demo() -> None:
    """Run an offline demo with synthetic sample data."""
    console.print(
        Panel.fit(
            "[bold yellow]Coursewright TA Demo Mode[/bold yellow]\n\n"
            "This demo shows how Coursewright analyzes discussions with sample data.\n"
            "No Canvas connection required.",
            title="Demo Mode",
            border_style="yellow",
        )
    )

    course = CanvasCourse(id=101, name="Introduction to Computing", course_code="CS-101")
    discussion = CanvasDiscussion(
        id=501,
        title="Week 1: Introduction Discussion",
        message="Please introduce yourself and share your learning goals.",
        course_id=course.id,
        replies_count=25,
    )

    students = [
        StudentParticipation(
            user_id=2001,
            user_name="Example Student A",
            initial_post=True,
            initial_post_word_count=180,
            reply_count=3,
            total_word_count=285,
            total_posts=4,
            participation_score=95.0,
            quality_score=88.0,
            total_score=91.5,
            meets_requirements=True,
        ),
        StudentParticipation(
            user_id=2002,
            user_name="Example Student B",
            initial_post=True,
            initial_post_word_count=120,
            reply_count=1,
            total_word_count=165,
            total_posts=2,
            participation_score=70.0,
            quality_score=75.0,
            total_score=72.5,
            meets_requirements=False,
            missing_requirements=["Minimum 2 replies required"],
        ),
        StudentParticipation(
            user_id=2003,
            user_name="Example Student C",
            initial_post=True,
            initial_post_word_count=220,
            reply_count=2,
            total_word_count=340,
            total_posts=3,
            participation_score=85.0,
            quality_score=92.0,
            total_score=88.5,
            meets_requirements=True,
        ),
    ]

    analysis = DiscussionAnalysis(
        discussion_id=discussion.id,
        discussion_title=discussion.title,
        course_id=course.id,
        total_students=25,
        participating_students=23,
        total_posts=67,
        total_replies=42,
        total_words=8950,
        average_words_per_post=133.6,
        participation_rate=92.0,
        student_participation=students,
        average_score=84.2,
        students_meeting_requirements=21,
    )

    console.print(f"\n[bold cyan]Course:[/bold cyan] {course.name} ({course.course_code})")
    console.print(f"[bold cyan]Discussion:[/bold cyan] {discussion.title}")

    summary_table = Table(title="Discussion Summary", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="white")
    summary_table.add_row("Total Students", str(analysis.total_students))
    summary_table.add_row("Participating Students", str(analysis.participating_students))
    summary_table.add_row("Participation Rate", f"{analysis.participation_rate:.1f}%")
    summary_table.add_row("Total Posts", str(analysis.total_posts))
    summary_table.add_row("Average Words/Post", f"{analysis.average_words_per_post:.1f}")
    summary_table.add_row("Average Score", f"{analysis.average_score:.1f}")
    summary_table.add_row(
        "Meeting Requirements",
        f"{analysis.students_meeting_requirements}/{analysis.total_students}",
    )
    console.print(summary_table)

    student_table = Table(
        title="Student Participation Details", show_header=True, header_style="bold magenta"
    )
    student_table.add_column("Student", style="cyan")
    student_table.add_column("Posts", justify="center")
    student_table.add_column("Words", justify="center")
    student_table.add_column("Score", justify="center")
    student_table.add_column("Requirements", justify="center")

    for student in students:
        req_status = "[green]✓[/green]" if student.meets_requirements else "[red]✗[/red]"
        student_table.add_row(
            student.user_name,
            str(student.total_posts),
            str(student.total_word_count),
            f"{student.total_score:.1f}",
            req_status,
        )
    console.print(student_table)

    console.print(
        Panel.fit(
            "[bold green]Demo Complete![/bold green]\n\n"
            "This is how Coursewright analyzes real Canvas discussions.\n"
            "Run [cyan]coursewright ta setup[/cyan] to configure for your Canvas instance.",
            title="Demo Results",
            border_style="green",
        )
    )
