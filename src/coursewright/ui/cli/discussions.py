"""Discussions CLI commands for analyzing and grading Canvas discussions."""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from coursewright.core.canvas import create_canvas_client, create_safe_canvas_writer
from coursewright.core.config import AppConfig, ExportFormat
from coursewright.core.config.manager import resolve_workspace_directory
from coursewright.core.export import CSVExporter, ExcelExporter, MarkdownExporter
from coursewright.ta.grading.analyzer import DiscussionAnalyzer
from coursewright.ta.grading.gradebook import GradebookManager
from coursewright.ta.grading.scores import (
    discussion_assignment_score,
    discussion_points_settings,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay
from coursewright.ui.cli.llm_utils import (
    LLMInitializationError,
    create_feedback_generator,
    get_cost_summary,
)

console = Console()

discussions_app = typer.Typer(
    name="discussions",
    help="Discussion analysis commands",
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


def get_course_id(config: AppConfig, course_id: int | None) -> int | None:
    """Get course ID from parameter or default."""
    if course_id:
        return course_id

    if config.cli.default_course_id:
        return config.cli.default_course_id

    console.print("[red]No course ID specified and no default course set.[/red]")
    console.print(
        "[cyan]Set a default course: "
        "[yellow]coursewright ta courses select COURSE_ID[/yellow][/cyan]"
    )
    return None


def is_late_submission(participation, assignment, discussion) -> bool:
    """Detect if a student submitted their discussion posts late."""
    due_date = None

    if assignment and "due_at" in assignment and assignment["due_at"]:
        due_date_str = assignment["due_at"]
        with contextlib.suppress(ValueError, AttributeError):
            due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))

    if not due_date and discussion and hasattr(discussion, "lock_at") and discussion.lock_at:
        due_date = discussion.lock_at

    if not due_date:
        return False

    if due_date.tzinfo is None:
        due_date = due_date.replace(tzinfo=UTC)

    if participation.first_post_date:
        first_post = participation.first_post_date
        if first_post.tzinfo is None:
            first_post = first_post.replace(tzinfo=UTC)
        if first_post > due_date:
            return True

    if participation.last_post_date:
        last_post = participation.last_post_date
        if last_post.tzinfo is None:
            last_post = last_post.replace(tzinfo=UTC)
        if last_post > due_date:
            return True

    return False


def prompt_for_course(canvas_client) -> int | None:
    """Prompt user to select a course."""
    try:
        with RemoteActivityDisplay("Fetching courses from Canvas", console=console):
            courses = canvas_client.get_courses()
        if not courses:
            console.print("[yellow]No courses found[/yellow]")
            return None

        table = Table(title="Available Courses")
        table.add_column("ID", style="cyan")
        table.add_column("Course", style="blue")
        table.add_column("Term", style="green")

        for course in courses[:10]:
            course_id = course["id"] if isinstance(course, dict) else course.id
            course_name = course.get("name", "Unknown") if isinstance(course, dict) else course.name
            term_val = (
                course.get("term", {}).get("name")
                if isinstance(course, dict)
                else getattr(course, "term", None)
            )
            table.add_row(str(course_id), str(course_name)[:50], str(term_val or "N/A"))

        console.print(table)

        if len(courses) > 10:
            console.print(f"[dim]... and {len(courses) - 10} more courses[/dim]")

        course_id = typer.prompt("Enter course ID")
        return int(course_id)

    except (ValueError, KeyboardInterrupt):
        console.print("[yellow]Course selection cancelled[/yellow]")
        return None


@discussions_app.command("list")
def list_discussions(
    ctx: typer.Context,
    course_id: int | None = typer.Argument(
        None, help="Canvas course ID (uses default if not specified)"
    ),
    include_announcements: bool = typer.Option(
        False, "--announcements", "-a", help="Include announcements in the list"
    ),
    limit: int | None = typer.Option(
        None, "--limit", "-l", help="Limit number of discussions shown"
    ),
):
    """List discussion topics in a course."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    actual_course_id = get_course_id(config, course_id)
    if not actual_course_id:
        raise typer.Exit(1) from None

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(
            f"Fetching discussions for course {actual_course_id}", console=console
        ):
            discussions = client.get_discussions(actual_course_id, include_announcements)

        if not discussions:
            console.print("[yellow]No discussions found in this course[/yellow]")
            return

        if limit:
            discussions = discussions[:limit]

        table = Table(
            title=f"Discussions in Course {actual_course_id} ({len(discussions)} found)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Title", style="white")
        table.add_column("Type", style="blue", no_wrap=True)
        table.add_column("Replies", style="green", justify="center")
        table.add_column("Posted", style="yellow", no_wrap=True)

        for discussion in discussions:
            disc_id = str(discussion.id)
            title = (
                discussion.title[:50] + "..."
                if len(discussion.title) > 50
                else discussion.title
            )
            disc_type = "Announcement" if discussion.is_announcement else "Discussion"
            replies = str(discussion.replies_count)
            posted_date = (
                discussion.posted_at.strftime("%m/%d/%Y") if discussion.posted_at else "N/A"
            )
            table.add_row(disc_id, title, disc_type, replies, posted_date)

        console.print(table)

        console.print("\n[cyan]Next steps:[/cyan]")
        console.print(
            "• View discussion details: "
            "[yellow]coursewright ta discussions show DISCUSSION_ID[/yellow]"
        )
        console.print(
            "• Grade a discussion: "
            "[yellow]coursewright ta discussions grade DISCUSSION_ID[/yellow]"
        )
        console.print(
            "• Get summary: "
            "[yellow]coursewright ta discussions summary DISCUSSION_ID[/yellow]"
        )

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error fetching discussions: {err}[/red]")
        raise typer.Exit(1) from None


@discussions_app.command()
def show(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course_id: int | None = typer.Option(
        None, "--course", "-c", help="Canvas course ID (uses default if not specified)"
    ),
):
    """Show detailed information about a discussion topic."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    actual_course_id = get_course_id(config, course_id)
    if not actual_course_id:
        raise typer.Exit(1) from None

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(
            f"Fetching discussion {discussion_id} from Canvas", console=console
        ):
            discussion = client.get_discussion_topic(actual_course_id, discussion_id)
            summary_data = (
                client.get_discussion_summary(actual_course_id, discussion_id)
                if discussion
                else None
            )

        if not discussion:
            console.print(
                f"[red]Discussion {discussion_id} not found in course {actual_course_id}[/red]"
            )
            raise typer.Exit(1) from None

        basic_info = (
            f"[bold]Title:[/bold] {discussion.title}\n"
            f"[bold]ID:[/bold] {discussion.id}\n"
            f"[bold]Course ID:[/bold] {discussion.course_id}\n"
            f"[bold]Type:[/bold] {'Announcement' if discussion.is_announcement else 'Discussion'}"
        )
        console.print(Panel(basic_info, title="Discussion Information", border_style="blue"))

        if discussion.message:
            clean_message = re.sub(r"<[^>]+>", "", discussion.message)
            if len(clean_message) > 300:
                clean_message = clean_message[:300] + "..."
            console.print(Panel(clean_message, title="Discussion Prompt", border_style="green"))

        posted_str = (
            discussion.posted_at.strftime("%Y-%m-%d %H:%M") if discussion.posted_at else "N/A"
        )
        stats_info = (
            f"[bold]Total Replies:[/bold] {discussion.replies_count}\n"
            f"[bold]Unread Count:[/bold] {discussion.unread_count}\n"
            f"[bold]Posted:[/bold] {posted_str}"
        )
        console.print(Panel(stats_info, title="Statistics", border_style="yellow"))

        if summary_data:
            total_students = summary_data["total_students"]
            rate = (
                summary_data["participating_students"] / total_students * 100
                if total_students > 0
                else 0.0
            )
            participation_info = (
                f"[bold]Total Students:[/bold] {total_students}\n"
                f"[bold]Participating:[/bold] {summary_data['participating_students']}\n"
                f"[bold]Participation Rate:[/bold] {rate:.1f}%\n"
                f"[bold]Total Posts:[/bold] {summary_data['total_posts']}\n"
                f"[bold]Average Words/Post:[/bold] {summary_data['average_words_per_post']:.1f}"
            )
            console.print(
                Panel(participation_info, title="Participation Summary", border_style="cyan")
            )

        console.print("\n[cyan]Next steps:[/cyan]")
        console.print(
            f"• Grade this discussion: "
            f"[yellow]coursewright ta discussions grade {discussion_id}[/yellow]"
        )
        console.print(
            f"• View all posts: "
            f"[yellow]coursewright ta discussions posts {discussion_id}[/yellow]"
        )

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error fetching discussion details: {err}[/red]")
        raise typer.Exit(1) from None


@discussions_app.command()
def summary(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course_id: int | None = typer.Option(
        None, "--course", "-c", help="Canvas course ID (uses default if not specified)"
    ),
):
    """Show participation summary for a discussion."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    actual_course_id = get_course_id(config, course_id)
    if not actual_course_id:
        raise typer.Exit(1) from None

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(
            f"Fetching participation summary for discussion {discussion_id}", console=console
        ):
            summary_data = client.get_discussion_summary(actual_course_id, discussion_id)

        total_students = summary_data["total_students"]
        participating = summary_data["participating_students"]
        participation_rate = (participating / total_students * 100) if total_students > 0 else 0

        overall_table = Table(
            title="Overall Statistics", show_header=True, header_style="bold magenta"
        )
        overall_table.add_column("Metric", style="cyan")
        overall_table.add_column("Value", style="white")
        overall_table.add_row("Total Students", str(total_students))
        overall_table.add_row("Participating Students", str(participating))
        overall_table.add_row("Participation Rate", f"{participation_rate:.1f}%")
        overall_table.add_row("Total Posts", str(summary_data["total_posts"]))
        overall_table.add_row("Total Words", str(summary_data["total_words"]))
        overall_table.add_row(
            "Average Words/Post", f"{summary_data['average_words_per_post']:.1f}"
        )
        console.print(overall_table)

        participation = summary_data["participation"]
        if participation:
            sorted_participants = sorted(
                participation.items(), key=lambda item: item[1]["posts"], reverse=True
            )
            top_participants = sorted_participants[:10]

            participants_table = Table(
                title="Top Participants", show_header=True, header_style="bold magenta"
            )
            participants_table.add_column("Student", style="cyan")
            participants_table.add_column("Posts", justify="center", style="green")
            participants_table.add_column("Words", justify="center", style="blue")
            participants_table.add_column("Replies", justify="center", style="yellow")

            for _user_id, data in top_participants:
                participants_table.add_row(
                    data["name"][:30],
                    str(data["posts"]),
                    str(data["words"]),
                    str(data["replies"]),
                )
            console.print(participants_table)

        non_participants = total_students - participating
        if non_participants > 0:
            console.print(
                f"\n[yellow]{non_participants} students have not participated "
                "in this discussion[/yellow]"
            )

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error generating summary: {err}[/red]")
        raise typer.Exit(1) from None


@discussions_app.command()
def grade(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course_id: int | None = typer.Option(
        None, "--course", "-c", help="Canvas course ID (uses default if not specified)"
    ),
    rubric_file: str | None = typer.Option(
        None, "--rubric", "-r", help="Path to grading rubric YAML file"
    ),
    export_format: str | None = typer.Option(
        None, "--export", "-e", help="Export format (csv, xlsx, json)"
    ),
    output_file: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
    min_words: int | None = typer.Option(
        None, "--min-words", help="Minimum words required for initial post"
    ),
    min_replies: int | None = typer.Option(
        None, "--min-replies", help="Minimum replies required"
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Grading profile (auto, legacy, or ai_discussion)"
    ),
):
    """Grade a discussion forum."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    actual_course_id = get_course_id(config, course_id)
    if not actual_course_id:
        raise typer.Exit(1) from None

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(
            f"Fetching course {actual_course_id} info", console=console
        ):
            course_info = client.get_course(actual_course_id)
        if course_info:
            course_name = course_info.get("name", "Unknown Course")
            course_code = course_info.get("course_code", "Unknown Code")
            console.print(f"[bold magenta]Course:[/bold magenta] {course_code} - {course_name}")
            console.print(f"[bold magenta]Course ID:[/bold magenta] {actual_course_id}")
        else:
            console.print(
                f"[yellow]Could not fetch course info for ID {actual_course_id}[/yellow]"
            )
    except Exception as err:
        console.print(f"[yellow]Error fetching course info: {err}[/yellow]")

    try:
        grading_config = {
            "min_words": min_words or config.discussion.default_min_words,
            "min_replies": min_replies or config.discussion.default_min_replies,
            "rubric_file": rubric_file or config.discussion.default_rubric,
            "profile": profile or config.discussion.default_grading_profile,
            **discussion_points_settings(config.discussion),
        }

        console.print("[cyan]Grading criteria:[/cyan]")
        console.print(f"   Minimum words: {grading_config['min_words']}")
        console.print(f"   Minimum replies: {grading_config['min_replies']}")
        console.print(f"   Profile: {grading_config['profile']}")
        if rubric_file:
            console.print(f"   Rubric file: {rubric_file}")

        analyzer = DiscussionAnalyzer(client, grading_config)

        with RemoteActivityDisplay(
            f"Grading discussion {discussion_id}", total=100, console=console
        ) as activity:
            def _on_progress(fraction: float) -> None:
                activity.completed_steps = int(fraction * 100)

            analysis = analyzer.analyze_discussion(
                actual_course_id,
                discussion_id,
                progress_callback=_on_progress,
            )

        if not analysis:
            console.print("[red]Failed to analyze discussion[/red]")
            raise typer.Exit(1) from None

        console.print(
            Panel.fit(
                f"[bold green]Grading Complete![/bold green]\n\n"
                f"[bold]Discussion:[/bold] {analysis.discussion_title}\n"
                f"[bold]Students Analyzed:[/bold] {len(analysis.student_participation)}\n"
                f"[bold]Average Score:[/bold] {analysis.average_score:.1f}\n"
                f"[bold]Meeting Requirements:[/bold] "
                f"{analysis.students_meeting_requirements}/{analysis.total_students}",
                title="Grading Results",
                border_style="green",
            )
        )

        results_table = Table(
            title="Individual Student Results", show_header=True, header_style="bold magenta"
        )
        results_table.add_column("Student", style="cyan")
        results_table.add_column("Posts", justify="center", style="white")
        results_table.add_column("Words", justify="center", style="blue")
        results_table.add_column("Replies", justify="center", style="yellow")
        results_table.add_column("Score", justify="center", style="green")
        results_table.add_column("Status", justify="center")

        for student in analysis.student_participation:
            status_text = "[green]✓[/green]" if student.meets_requirements else "[red]✗[/red]"
            results_table.add_row(
                student.user_name[:25],
                str(student.total_posts),
                str(student.total_word_count),
                str(student.reply_count),
                f"{student.total_score:.1f}",
                status_text,
            )

        console.print(results_table)

        failing_students = [s for s in analysis.student_participation if not s.meets_requirements]
        if failing_students:
            console.print(
                f"\n[yellow]{len(failing_students)} students not meeting requirements:[/yellow]"
            )
            for student in failing_students[:5]:
                issues = ", ".join(student.missing_requirements)
                console.print(f"   • {student.user_name}: {issues}")
            if len(failing_students) > 5:
                console.print(f"   ... and {len(failing_students) - 5} more")

        if export_format or output_file:
            export_fmt = (
                ExportFormat(export_format) if export_format else config.discussion.export.format
            )
            try:
                course_name = None
                with contextlib.suppress(Exception), RemoteActivityDisplay(
                    "Fetching course name for export", console=console
                ):
                    course = client.get_course(actual_course_id)
                    if course:
                        course_name = course.get("course_code") or course.get("name")

                if export_fmt == ExportFormat.CSV:
                    exporter = CSVExporter()
                    output_path = exporter.export_analysis(analysis, output_file, course_name)
                elif export_fmt == ExportFormat.EXCEL:
                    exporter = ExcelExporter()
                    output_path = exporter.export_analysis(analysis, output_file, course_name)
                else:
                    console.print(f"[red]Unsupported export format: {export_fmt}[/red]")
                    raise typer.Exit(1) from None

                console.print(f"[green]Results exported to {output_path}[/green]")
            except typer.Exit:
                raise
            except Exception as err:
                console.print(f"[red]Export failed: {err}[/red]")

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error grading discussion: {err}[/red]")
        raise typer.Exit(1) from None


@discussions_app.command()
def posts(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course_id: int | None = typer.Option(
        None, "--course", "-c", help="Canvas course ID (uses default if not specified)"
    ),
    user_name: str | None = typer.Option(None, "--user", "-u", help="Filter posts by user name"),
    limit: int | None = typer.Option(
        10, "--limit", "-l", help="Maximum number of posts to show"
    ),
):
    """Show recent posts in a discussion."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    actual_course_id = get_course_id(config, course_id)
    if not actual_course_id:
        raise typer.Exit(1) from None

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay(
            f"Fetching posts for discussion {discussion_id}", console=console
        ):
            entries = client.get_discussion_entries(actual_course_id, discussion_id)

        if not entries:
            console.print("[yellow]No posts found in this discussion[/yellow]")
            return

        all_posts: list = []

        def flatten_posts(posts_list, depth: int = 0) -> None:
            for post in posts_list:
                post.depth = depth
                all_posts.append(post)
                if post.replies:
                    flatten_posts(post.replies, depth + 1)

        flatten_posts(entries)

        if user_name:
            user_name_lower = user_name.lower()
            all_posts = [
                post
                for post in all_posts
                if post.user_name and user_name_lower in post.user_name.lower()
            ]

        all_posts.sort(key=lambda item: item.posted_at, reverse=True)
        all_posts = all_posts[:limit]

        if not all_posts:
            filter_msg = f" matching '{user_name}'" if user_name else ""
            console.print(f"[yellow]No posts found{filter_msg}[/yellow]")
            return

        table = Table(
            title=f"Recent Posts in Discussion {discussion_id} ({len(all_posts)} shown)",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("User", style="cyan")
        table.add_column("Posted", style="yellow", no_wrap=True)
        table.add_column("Words", justify="center", style="green")
        table.add_column("Content", style="white")

        for post in all_posts:
            author = post.user_name or "Unknown User"
            posted_date = post.posted_at.strftime("%m/%d %H:%M") if post.posted_at else "N/A"
            word_count = str(post.word_count)
            clean_content = re.sub(r"<[^>]+>", "", post.message)
            indent = "  " * post.depth
            if post.depth > 0:
                clean_content = f"{indent}↳ {clean_content}"
            if len(clean_content) > 80:
                clean_content = clean_content[:80] + "..."
            table.add_row(author[:20], posted_date, word_count, clean_content)

        console.print(table)

        if len(all_posts) == limit:
            console.print(
                f"\n[cyan]Showing {limit} most recent posts. Use --limit to see more.[/cyan]"
            )

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error fetching posts: {err}[/red]")
        raise typer.Exit(1) from None


@discussions_app.command("submit-grades")
def submit_grades(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course: int | None = typer.Option(None, "--course", "-c", help="Course ID"),
    execute: bool = typer.Option(
        False, "--execute", help="Write grades to Canvas (default is dry-run)"
    ),
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation prompt"),
    rubric_file: str | None = typer.Option(None, "--rubric", "-r", help="Custom rubric file"),
    profile: str | None = typer.Option(
        None, "--profile", help="Grading profile (auto, legacy, or ai_discussion)"
    ),
    use_llm: bool = typer.Option(False, "--llm", help="Use LLM to generate personalized feedback"),
    llm_provider: str | None = typer.Option(
        None, "--llm-provider", help="Override the LLM provider (openai or anthropic)."
    ),
    llm_model: str | None = typer.Option(
        None, "--llm-model", help="Override the LLM model name."
    ),
    llm_batch: bool = typer.Option(
        True, "--llm-batch/--no-llm-batch", help="Use single batch API call (faster, cheaper)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show detailed LLM API communications"
    ),
):
    """Submit discussion grades to Canvas gradebook (dry-run by default)."""
    try:
        if ctx.obj is None:
            ctx.obj = {}

        config: AppConfig = ctx.obj.get("config")
        if not config:
            config_file = ctx.obj.get("config_file", "config.yaml")
            config = AppConfig.load_from_file(config_file)
            ctx.obj["config"] = config

        canvas_client = get_canvas_client(config)
        if not canvas_client:
            return

        feedback_generator = None
        selected_provider = None
        selected_model = None
        llm_requested = use_llm or config.llm.enabled
        if llm_requested:
            try:
                feedback_generator, selected_provider, selected_model = create_feedback_generator(
                    config,
                    provider_override=llm_provider,
                    model_override=llm_model,
                    verbose=verbose,
                )
                verbose_indicator = " (verbose mode)" if verbose else ""
                console.print(
                    f"[green]LLM-enhanced feedback enabled "
                    f"({selected_provider}/{selected_model}){verbose_indicator}[/green]"
                )
            except LLMInitializationError as err:
                console.print(f"[yellow]{err}[/yellow]")
                feedback_generator = None
            except Exception as err:
                console.print(f"[yellow]Failed to initialize LLM: {err}[/yellow]")
                feedback_generator = None
        elif llm_provider or llm_model:
            console.print(
                "[yellow]--llm-provider/--llm-model ignored without --llm flag.[/yellow]"
            )

        writer = create_safe_canvas_writer(
            canvas_client, execute=execute, workspace_directory=resolve_workspace_directory(config)
        )
        gradebook = GradebookManager(canvas_client, writer)

        if not course:
            course = prompt_for_course(canvas_client)
            if not course:
                return

        grading_config = {
            "min_words": config.discussion.default_min_words,
            "min_replies": config.discussion.default_min_replies,
            "rubric_file": rubric_file or config.discussion.default_rubric,
            "profile": profile or config.discussion.default_grading_profile,
            **discussion_points_settings(config.discussion),
        }
        analyzer = DiscussionAnalyzer(canvas_client, grading_config)

        with RemoteActivityDisplay(
            f"Analyzing discussion {discussion_id} for grading", console=console
        ):
            results = analyzer.analyze_discussion(course, discussion_id)
            discussion = canvas_client.get_discussion_topic(course, discussion_id)
            assignment = gradebook.get_assignment_for_discussion(course, discussion_id)

        if feedback_generator and selected_model:
            student_count = len(results.student_participation)
            cost_summary = get_cost_summary(
                selected_model,
                max_tokens=config.llm.max_tokens,
                student_count=student_count,
            )
            if cost_summary:
                console.print(
                    f"[cyan]Estimated LLM cost: ${cost_summary['per_student']:.4f}/student "
                    f"(~${cost_summary['total']:.2f} for {student_count} students).[/cyan]"
                )
            if selected_provider:
                config.llm.remember_selection(selected_provider, selected_model)
                config_file = ctx.obj.get("config_file", "config.yaml")
                try:
                    config.save_to_file(config_file)
                except OSError as err:
                    console.print(f"[yellow]Could not save {config_file}: {err}[/yellow]")
                ctx.obj["config"] = config

        discussion_context = ""
        if feedback_generator and discussion:
            exporter = MarkdownExporter()
            discussion_context = exporter.extract_discussion_context(discussion, results)

        if not assignment:
            console.print(
                "[red]This discussion is not linked to an assignment. Cannot submit grades.[/red]"
            )
            raise typer.Exit(1) from None

        assignment_id = assignment["id"]
        assignment_name = assignment["name"]
        max_points = assignment.get("points_possible", 100)

        console.print(f"Assignment: {assignment_name}")
        console.print(f"Max Points: {max_points}")

        llm_feedback_map: dict = {}
        if feedback_generator and llm_batch:
            rubric_info = {
                "substantial_post": config.discussion.substantial_post_points,
                "reply_points": config.discussion.reply_points_each,
                "min_replies": config.discussion.max_replies_counted,
                "min_word_count": config.discussion.default_min_words,
                "total_points": max_points,
                "grading_profile": getattr(results, "grading_profile", None),
            }
            with RemoteActivityDisplay("Generating batch LLM feedback", console=console):
                llm_feedback_map = feedback_generator.generate_batch_feedback_single_request(
                    students=results.student_participation,
                    discussion_context=discussion_context,
                    rubric_info=rubric_info,
                )
            console.print(
                f"[green]Generated feedback for {len(llm_feedback_map)} students[/green]"
            )

        grades_to_submit: list[dict] = []
        preview_table = Table(title="Grade Preview", show_lines=True)
        preview_table.add_column("Student", style="cyan")
        preview_table.add_column("Score", style="green")
        preview_table.add_column("Percentage", style="yellow")
        preview_table.add_column("Status", style="blue")
        preview_table.add_column("Comment", style="dim", overflow="fold")

        for participation in results.student_participation:
            if llm_feedback_map and participation.user_id in llm_feedback_map:
                comment = llm_feedback_map[participation.user_id]
            elif feedback_generator and not llm_batch:
                rubric_info = {
                    "substantial_post_points": config.discussion.substantial_post_points,
                    "reply_points_each": config.discussion.reply_points_each,
                    "max_replies_counted": config.discussion.max_replies_counted,
                    "min_word_count": config.discussion.default_min_words,
                    "grading_profile": getattr(results, "grading_profile", None),
                }
                with RemoteActivityDisplay(
                    f"Generating LLM feedback for {participation.user_name}", console=console
                ):
                    comment = gradebook.create_llm_enhanced_comment(
                        student_participation=participation,
                        discussion_context=discussion_context,
                        rubric_info=rubric_info,
                        llm_feedback_generator=feedback_generator,
                        include_score_breakdown=config.llm.include_score_breakdown,
                    )
            else:
                analysis_data = {
                    "word_count": participation.total_word_count,
                    "reply_count": participation.reply_count,
                    "late_submission": is_late_submission(participation, assignment, discussion),
                }
                scoring_data = {
                    "participation_score": participation.participation_score,
                    "quality_score": participation.quality_score,
                    "total_score": participation.total_score,
                }
                comment = gradebook.create_grade_comment(
                    analysis_data, scoring_data, participation
                )

            score = discussion_assignment_score(
                participation, max_points, rubric_points=results.rubric_points
            )
            percentage = (score / max_points * 100) if max_points > 0 else 0
            status_text = "✓" if participation.meets_requirements else "⚠"
            preview_table.add_row(
                participation.user_name,
                f"{score:.1f}",
                f"{percentage:.1f}%",
                status_text,
                comment,
            )
            grades_to_submit.append(
                {"student_id": participation.user_id, "score": score, "comment": comment}
            )

        console.print(preview_table)

        total_students = len(grades_to_submit)
        meeting_req = sum(1 for p in results.student_participation if p.meets_requirements)
        avg_score = (
            sum(g["score"] for g in grades_to_submit) / total_students
            if total_students > 0
            else 0
        )

        console.print("\nSummary:")
        console.print(f"   Students: {total_students}")
        console.print(f"   Meeting Requirements: {meeting_req}/{total_students}")
        console.print(f"   Average Score: {avg_score:.1f}/{max_points}")

        if not execute:
            console.print(
                "\n[yellow]DRY RUN — No grades were written (pass --execute to write)[/yellow]"
            )
            return

        if not confirm and not typer.confirm(f"\nSubmit {total_students} grades to Canvas?"):
            console.print("[yellow]Grade submission cancelled[/yellow]")
            return

        with RemoteActivityDisplay(
            "Submitting grades to Canvas", total=total_students, console=console
        ) as activity:
            submission_results = gradebook.submit_multiple_grades(
                course, assignment_id, grades_to_submit
            )
            activity.advance(total_students)

        successful = (
            submission_results["successful"]
            if isinstance(submission_results, dict)
            else submission_results.successful
        )
        failed = (
            submission_results["failed"]
            if isinstance(submission_results, dict)
            else submission_results.failed
        )

        if successful > 0:
            console.print(f"[green]Successfully submitted {successful} grades[/green]")
        if failed > 0:
            console.print(f"[red]Failed to submit {failed} grades[/red]")
        console.print("\nGrade submission complete!")

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error submitting grades: {err}[/red]")
        raise typer.Exit(1) from None


@discussions_app.command("export-course")
def export_course(
    ctx: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course ID"),
    output_dir: str | None = typer.Option(
        None, "--output", "-o", help="Output directory (default: exports/)"
    ),
    include_empty: bool = typer.Option(
        False, "--include-empty", help="Include discussions with no posts"
    ),
    include_locked: bool = typer.Option(
        False, "--include-locked", help="Include locked discussions"
    ),
):
    """Export all discussions from a course to markdown files."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    course_display = f"Course {course_id}"
    course_name = f"Course_{course_id}"
    try:
        with RemoteActivityDisplay(
            f"Fetching course {course_id} and discussions", console=console
        ):
            course_info = client.get_course(course_id)
            discussions = client.get_discussions(course_id)
        if course_info:
            course_name = course_info.get("course_code") or course_info.get(
                "name", f"Course_{course_id}"
            )
            course_display = course_info.get("name", "Unknown Course")
            console.print(f"[bold magenta]Course:[/bold magenta] {course_display}")
            console.print(f"[bold magenta]Course ID:[/bold magenta] {course_id}")
        else:
            console.print("[yellow]Could not fetch course info, using ID as name[/yellow]")
    except Exception as err:
        console.print(f"[red]Error fetching discussions: {err}[/red]")
        raise typer.Exit(1) from None

    if not discussions:
        console.print("[yellow]No discussions found in this course[/yellow]")
        raise typer.Exit(0)

    filtered_discussions = [
        disc
        for disc in discussions
        if disc.published
        and (not disc.locked or include_locked)
        and (disc.replies_count != 0 or include_empty)
    ]

    if not filtered_discussions:
        console.print("[yellow]No discussions match the filter criteria[/yellow]")
        console.print("[cyan]Try --include-empty or --include-locked to see more[/cyan]")
        raise typer.Exit(0)

    console.print(f"[green]Found {len(filtered_discussions)} discussions to export[/green]")
    if len(filtered_discussions) < len(discussions):
        skipped = len(discussions) - len(filtered_discussions)
        console.print(f"[dim]   (Skipped {skipped} empty/locked/unpublished discussions)[/dim]")

    exporter = MarkdownExporter()
    exported_count = 0
    failed_count = 0

    with RemoteActivityDisplay(
        "Exporting discussions to markdown",
        total=len(filtered_discussions),
        console=console,
    ) as activity:
        for disc in filtered_discussions:
            try:
                entries = client.get_discussion_entries(course_id, disc.id)
                exporter.export_discussion_to_markdown(
                    course_id=course_id,
                    discussion=disc,
                    entries=entries,
                    output_dir=output_dir,
                    include_analysis=False,
                    course_name=course_name,
                )
                exported_count += 1
            except Exception as err:
                failed_count += 1
                console.print(f"[red]   Failed to export '{disc.title}': {err}[/red]")
            finally:
                activity.advance()

    console.print(
        Panel.fit(
            f"[bold green]Course Export Complete![/bold green]\n\n"
            f"[bold]Course:[/bold] {course_display}\n"
            f"[bold]Discussions Exported:[/bold] {exported_count}\n"
            f"[bold]Failed:[/bold] {failed_count}\n"
            f"[bold]Output Directory:[/bold] {output_dir or 'exports/'}{course_name}/",
            title="Export Summary",
            border_style="green",
        )
    )


@discussions_app.command("process")
def process_discussion(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course_id: int | None = typer.Option(None, "--course", "-c", help="Canvas course ID"),
    execute: bool = typer.Option(
        False, "--execute", help="Write grades to Canvas (default is dry-run)"
    ),
):
    """Run the full discussion workflow: grade, review, submit, export."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    actual_course_id = get_course_id(config, course_id)
    if not actual_course_id:
        raise typer.Exit(1) from None

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    console.print(
        Panel(
            f"[bold blue]Unified Discussion Workflow[/bold blue]\n\n"
            f"Discussion [cyan]{discussion_id}[/cyan]  •  "
            f"Course [cyan]{actual_course_id}[/cyan]\n\n"
            "[dim]Each step can be skipped.[/dim]",
            border_style="blue",
        )
    )

    console.print("\n[bold cyan]Step 1/5 — Grade Discussion[/bold cyan]")
    grading_config = {
        "min_words": config.discussion.default_min_words,
        "min_replies": config.discussion.default_min_replies,
        "rubric_file": config.discussion.default_rubric,
        "profile": config.discussion.default_grading_profile,
        **discussion_points_settings(config.discussion),
    }
    analyzer = DiscussionAnalyzer(client, grading_config)

    with RemoteActivityDisplay(
        f"Analyzing discussion {discussion_id}", total=100, console=console
    ) as activity:
        def _on_progress(fraction: float) -> None:
            activity.completed_steps = int(fraction * 100)

        analysis = analyzer.analyze_discussion(
            actual_course_id,
            discussion_id,
            progress_callback=_on_progress,
        )
        discussion_obj = client.get_discussion_topic(actual_course_id, discussion_id)

    if not analysis:
        console.print("[red]Grading failed — aborting.[/red]")
        raise typer.Exit(1) from None

    console.print(
        f"[green]Graded {len(analysis.student_participation)} students  "
        f"(avg {analysis.average_score:.1f})[/green]"
    )

    feedback_generator = None
    if Confirm.ask("\n[bold cyan]Step 2/5 — Generate LLM feedback?[/bold cyan]", default=False):
        try:
            feedback_generator, provider, model_name = create_feedback_generator(config)
            console.print(f"[green]Using {provider}/{model_name}[/green]")
        except (LLMInitializationError, Exception) as err:
            console.print(f"[yellow]LLM unavailable: {err}[/yellow]")

    if Confirm.ask("\n[bold cyan]Step 3/5 — Submit grades to Canvas?[/bold cyan]", default=False):
        writer = create_safe_canvas_writer(
            client, execute=execute, workspace_directory=resolve_workspace_directory(config)
        )
        gradebook = GradebookManager(client, writer)
        with RemoteActivityDisplay("Fetching linked assignment", console=console):
            assignment = gradebook.get_assignment_for_discussion(actual_course_id, discussion_id)
        if not assignment:
            console.print("[yellow]No linked assignment — skipping.[/yellow]")
        else:
            max_points = assignment.get("points_possible", 100)
            grades_to_submit: list[dict] = []
            discussion_context = ""
            if feedback_generator and discussion_obj:
                exporter = MarkdownExporter()
                discussion_context = exporter.extract_discussion_context(discussion_obj, analysis)

            for participation in analysis.student_participation:
                if feedback_generator:
                    rubric_info = {
                        "substantial_post_points": config.discussion.substantial_post_points,
                        "reply_points_each": config.discussion.reply_points_each,
                        "max_replies_counted": config.discussion.max_replies_counted,
                        "min_word_count": config.discussion.default_min_words,
                        "grading_profile": getattr(analysis, "grading_profile", None),
                    }
                    with RemoteActivityDisplay(
                        f"Generating feedback for {participation.user_name}", console=console
                    ):
                        comment = gradebook.create_llm_enhanced_comment(
                            student_participation=participation,
                            discussion_context=discussion_context,
                            rubric_info=rubric_info,
                            llm_feedback_generator=feedback_generator,
                            include_score_breakdown=config.llm.include_score_breakdown,
                        )
                else:
                    analysis_data = {
                        "word_count": participation.total_word_count,
                        "reply_count": participation.reply_count,
                        "late_submission": is_late_submission(
                            participation, assignment, discussion_obj
                        ),
                    }
                    scoring_data = {
                        "participation_score": participation.participation_score,
                        "quality_score": participation.quality_score,
                        "total_score": participation.total_score,
                    }
                    comment = gradebook.create_grade_comment(
                        analysis_data, scoring_data, participation
                    )

                score = discussion_assignment_score(
                    participation, max_points, rubric_points=analysis.rubric_points
                )
                grades_to_submit.append(
                    {"student_id": participation.user_id, "score": score, "comment": comment}
                )

            with RemoteActivityDisplay(
                "Submitting grades to Canvas",
                total=len(grades_to_submit),
                console=console,
            ) as activity:
                result = gradebook.submit_multiple_grades(
                    actual_course_id, assignment["id"], grades_to_submit
                )
                activity.advance(len(grades_to_submit))

            successful = result["successful"] if isinstance(result, dict) else result.successful
            failed = result["failed"] if isinstance(result, dict) else result.failed
            console.print(f"[green]Submitted {successful} grades[/green]")
            if failed:
                console.print(f"[red]Failed: {failed}[/red]")

    if Confirm.ask("\n[bold cyan]Step 4/5 — Export to markdown?[/bold cyan]", default=True):
        with RemoteActivityDisplay("Fetching discussion entries for export", console=console):
            entries = client.get_discussion_entries(actual_course_id, discussion_id)
            course_name = None
            with contextlib.suppress(Exception):
                course_info = client.get_course(actual_course_id)
                if course_info:
                    course_name = course_info.get("course_code") or course_info.get("name")

        exporter = MarkdownExporter()
        out = exporter.export_discussion_to_markdown(
            course_id=actual_course_id,
            discussion=discussion_obj,
            entries=entries,
            include_analysis=True,
            analysis=analysis,
            course_name=course_name,
        )
        console.print(f"[green]Exported to {out}[/green]")

    if Confirm.ask(
        "\n[bold cyan]Step 5/5 — Run CyberInsight analysis?[/bold cyan]",
        default=False,
    ):
        console.print("[blue]Use: coursewright ta assistant analyze DISCUSSION_ID[/blue]")

    console.print(Panel("[bold green]Workflow complete.[/bold green]", border_style="green"))
