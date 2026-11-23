"""Courses CLI commands for managing Canvas courses."""

from __future__ import annotations

import re

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.core.canvas import create_canvas_client
from coursewright.core.config import AppConfig
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()

courses_app = typer.Typer(
    name="courses",
    help="Course management commands",
    rich_markup_mode="rich",
)


def get_canvas_client(config: AppConfig) -> object | None:
    """Get authenticated Canvas client."""
    try:
        return create_canvas_client(
            base_url=config.discussion.canvas_url,
            api_token_file=config.discussion.api_token_file,
        )
    except Exception as err:
        console.print(f"[red]Failed to connect to Canvas: {err}[/red]")
        return None


@courses_app.command("list")
def list_courses(
    ctx: typer.Context,
    enrollment_type: str = typer.Option(
        "teacher", "--type", "-t", help="Enrollment type (teacher, student, ta, observer)"
    ),
    term: str | None = typer.Option(None, "--term", help="Filter by term name"),
    active_only: bool = typer.Option(True, "--active/--all", help="Show only active courses"),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Limit number of courses shown"
    ),
):
    """List your Canvas courses."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay("Fetching courses from Canvas", console=console):
            courses = client.get_courses(enrollment_type=enrollment_type)

        if not courses:
            console.print(
                f"[yellow]No courses found for enrollment type '{enrollment_type}'[/yellow]"
            )
            return

        if term:
            term_lower = term.lower()
            courses = [
                course
                for course in courses
                if course.get("term", {}).get("name", "").lower().find(term_lower) >= 0
            ]

        if active_only:
            courses = [
                course
                for course in courses
                if course.get("workflow_state") in ["available", "published"]
            ]

        if limit:
            courses = courses[:limit]

        if not courses:
            console.print("[yellow]No courses match your criteria[/yellow]")
            return

        table = Table(
            title=f"Your Canvas Courses ({len(courses)} found)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Course Name", style="white")
        table.add_column("Code", style="yellow", no_wrap=True)
        table.add_column("Term", style="blue", no_wrap=True)
        table.add_column("Students", style="green", justify="center")
        table.add_column("Status", style="magenta", no_wrap=True)

        status_map = {
            "available": "[green]Active[/green]",
            "published": "[green]Published[/green]",
            "unpublished": "[yellow]Draft[/yellow]",
            "completed": "[blue]Completed[/blue]",
            "deleted": "[red]Deleted[/red]",
        }

        for course in courses:
            course_id = str(course["id"])
            name = course.get("name", "Unnamed Course")
            code = course.get("course_code", "N/A")
            term_name = course.get("term", {}).get("name", "N/A")
            student_count = course.get("total_students", 0)
            student_str = str(student_count) if student_count > 0 else "-"
            workflow_state = course.get("workflow_state", "unknown")
            status_text = status_map.get(workflow_state, workflow_state.title())
            table.add_row(course_id, name, code, term_name, student_str, status_text)

        console.print(table)

        console.print("\n[cyan]Next steps:[/cyan]")
        console.print(
            "• Set default course: [yellow]coursewright ta courses select COURSE_ID[/yellow]"
        )
        console.print(
            "• View course details: [yellow]coursewright ta courses info COURSE_ID[/yellow]"
        )
        console.print(
            "• List discussions: [yellow]coursewright ta discussions list COURSE_ID[/yellow]"
        )

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error fetching courses: {err}[/red]")
        raise typer.Exit(1) from None


@courses_app.command()
def info(ctx: typer.Context, course_id: int = typer.Argument(..., help="Canvas course ID")):
    """Show detailed information about a specific course."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(f"Fetching course {course_id} from Canvas", console=console):
            course = client.get_course(course_id)
            if course:
                discussions = client.get_discussions(course_id)
                users = client.get_course_users(course_id, ["student"])
            else:
                discussions = []
                users = []

        if not course:
            console.print(f"[red]Course {course_id} not found or not accessible[/red]")
            raise typer.Exit(1) from None

        basic_info = (
            f"[bold]Name:[/bold] {course.get('name', 'N/A')}\n"
            f"[bold]Code:[/bold] {course.get('course_code', 'N/A')}\n"
            f"[bold]ID:[/bold] {course['id']}\n"
            f"[bold]SIS ID:[/bold] {course.get('sis_course_id', 'N/A')}"
        )
        console.print(Panel(basic_info, title="Course Information", border_style="blue"))

        term_info = course.get("term", {})
        enrollment_info = (
            f"[bold]Term:[/bold] {term_info.get('name', 'N/A')}\n"
            f"[bold]Students:[/bold] {course.get('total_students', 'N/A')}\n"
            f"[bold]Status:[/bold] {course.get('workflow_state', 'N/A').title()}\n"
            f"[bold]Public:[/bold] {'Yes' if course.get('is_public') else 'No'}"
        )
        console.print(Panel(enrollment_info, title="Enrollment & Status", border_style="green"))

        if course.get("start_at") or course.get("end_at"):
            dates_info = ""
            if course.get("start_at"):
                dates_info += f"[bold]Start Date:[/bold] {course['start_at']}\n"
            if course.get("end_at"):
                dates_info += f"[bold]End Date:[/bold] {course['end_at']}\n"
            console.print(Panel(dates_info.strip(), title="Course Dates", border_style="yellow"))

        if course.get("storage_quota_mb") or course.get("storage_quota_used_mb"):
            storage_quota = course.get("storage_quota_mb", 0)
            storage_used = course.get("storage_quota_used_mb", 0)
            storage_percent = (storage_used / storage_quota * 100) if storage_quota > 0 else 0
            storage_info = (
                f"[bold]Quota:[/bold] {storage_quota} MB\n"
                f"[bold]Used:[/bold] {storage_used} MB ({storage_percent:.1f}%)"
            )
            console.print(Panel(storage_info, title="Storage", border_style="magenta"))

        activity_info = (
            f"[bold]Discussion Topics:[/bold] {len(discussions)}\n"
            f"[bold]Enrolled Students:[/bold] {len(users)}"
        )
        console.print(Panel(activity_info, title="Course Activity", border_style="cyan"))

        if course.get("syllabus_body"):
            syllabus = course["syllabus_body"]
            if len(syllabus) > 200:
                syllabus = syllabus[:200] + "..."
            clean_syllabus = re.sub(r"<[^>]+>", "", syllabus)
            console.print(Panel(clean_syllabus, title="Course Description", border_style="blue"))

        console.print("\n[cyan]Next steps:[/cyan]")
        console.print(
            f"• Set as default: [yellow]coursewright ta courses select {course_id}[/yellow]"
        )
        console.print(
            f"• List discussions: [yellow]coursewright ta discussions list {course_id}[/yellow]"
        )

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error fetching course info: {err}[/red]")
        raise typer.Exit(1) from None


@courses_app.command()
def select(
    ctx: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course ID to set as default"),
):
    """Set a default course for future commands."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(f"Verifying course {course_id} in Canvas", console=console):
            course = client.get_course(course_id)

        if not course:
            console.print(f"[red]Course {course_id} not found or not accessible[/red]")
            raise typer.Exit(1) from None

        config.cli.default_course_id = course_id
        if course_id not in config.cli.recent_courses:
            config.cli.recent_courses.insert(0, course_id)
            config.cli.recent_courses = config.cli.recent_courses[:10]

        config_file = obj.get("config_file", "config.yaml")
        config.save_to_file(config_file)

        course_name = course.get("name", "Unnamed Course")
        course_code = course.get("course_code", "N/A")

        console.print(
            Panel.fit(
                f"[bold green]Default course set![/bold green]\n\n"
                f"[bold]Course:[/bold] {course_name}\n"
                f"[bold]Code:[/bold] {course_code}\n"
                f"[bold]ID:[/bold] {course_id}",
                title="Default Course Updated",
                border_style="green",
            )
        )

        console.print("\n[cyan]You can now use commands without specifying course ID:[/cyan]")
        console.print("• [yellow]coursewright ta discussions list[/yellow]")
        console.print("• [yellow]coursewright ta discussions grade DISCUSSION_ID[/yellow]")

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error setting default course: {err}[/red]")
        raise typer.Exit(1) from None


@courses_app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search term for course name or code"),
    enrollment_type: str = typer.Option(
        "teacher", "--type", "-t", help="Enrollment type (teacher, student, ta, observer)"
    ),
):
    """Search for courses by name or code."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(f"Searching courses for '{query}'", console=console):
            matching_courses = client.find_course_by_name(query, enrollment_type)

        if not matching_courses:
            console.print(f"[yellow]No courses found matching '{query}'[/yellow]")
            return

        table = Table(
            title=f"Search Results for '{query}' ({len(matching_courses)} found)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Course Name", style="white")
        table.add_column("Code", style="yellow", no_wrap=True)
        table.add_column("Term", style="blue", no_wrap=True)
        table.add_column("Students", style="green", justify="center")

        for course in matching_courses:
            course_id = str(course["id"])
            name = course.get("name", "Unnamed Course")
            code = course.get("course_code", "N/A")
            term_name = course.get("term", {}).get("name", "N/A")
            student_count = course.get("total_students", 0)
            student_str = str(student_count) if student_count > 0 else "-"
            table.add_row(course_id, name, code, term_name, student_str)

        console.print(table)

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error searching courses: {err}[/red]")
        raise typer.Exit(1) from None


@courses_app.command()
def recent(ctx: typer.Context):
    """Show recently accessed courses."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    if not config.cli.recent_courses:
        console.print("[yellow]No recent courses found[/yellow]")
        console.print(
            "[cyan]Use [yellow]coursewright ta courses select COURSE_ID[/yellow] "
            "to track course usage[/cyan]"
        )
        return

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    table = Table(title="Recent Courses", show_header=True, header_style="bold magenta")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Course Name", style="white")
    table.add_column("Code", style="yellow", no_wrap=True)
    table.add_column("Default", style="green", justify="center")

    with RemoteActivityDisplay(
        "Fetching recent courses", total=len(config.cli.recent_courses), console=console
    ) as activity:
        for course_id in config.cli.recent_courses:
            try:
                course = client.get_course(course_id)
                if course:
                    name = course.get("name", "Unnamed Course")
                    code = course.get("course_code", "N/A")
                    is_default = "✓" if course_id == config.cli.default_course_id else ""
                    table.add_row(str(course_id), name, code, is_default)
                else:
                    table.add_row(
                        str(course_id),
                        "[dim]Course not accessible[/dim]",
                        "[dim]N/A[/dim]",
                        "",
                    )
            except Exception:
                continue
            finally:
                activity.advance()

    console.print(table)
