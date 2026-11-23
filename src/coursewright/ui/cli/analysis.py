"""CLI command for offline course-level analysis.

Usage::

    coursewright ta analyze-course exports/COURSE_CODE/
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.ta.attendance.course_analyzer import (
    analyze_course_directory,
    render_summary_report,
)

console = Console()

analysis_app = typer.Typer(
    name="analyze-course",
    help="Analyze exported discussions for a course",
    rich_markup_mode="rich",
)


@analysis_app.callback(invoke_without_command=True)
def analyze_course(
    course_dir: str = typer.Argument(
        ...,
        help="Path to the exported course folder",
    ),
):
    """Analyze exported discussions and produce a summary report.

    Reads every ``discussion.md`` under COURSE_DIR, computes engagement
    metrics, and writes ``COURSE_DIR/_ANALYSIS/summary_report.md``.
    """
    course_path = Path(course_dir)
    if not course_path.is_dir():
        console.print(f"[red]Directory not found: {course_dir}[/red]")
        raise typer.Exit(1) from None

    console.print(
        Panel(
            f"[bold blue]Analyzing:[/bold blue] {course_path}",
            border_style="blue",
        )
    )

    report = analyze_course_directory(course_path)

    if not report.discussions:
        console.print("[yellow]No discussion.md files found.[/yellow]")
        raise typer.Exit(1) from None

    _print_summary_table(report)

    output_dir = course_path / "_ANALYSIS"
    output_dir.mkdir(exist_ok=True)
    report_path = output_dir / "summary_report.md"
    report_path.write_text(render_summary_report(report), encoding="utf-8")

    console.print(f"\n[green]Report saved to {report_path}[/green]")


def _print_summary_table(report) -> None:
    """Print a Rich table summarising the course report."""
    table = Table(
        title="Per-Discussion Engagement",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Discussion", style="cyan", min_width=25)
    table.add_column("Posts", justify="center", width=6)
    table.add_column("Replies", justify="center", width=8)
    table.add_column("Depth", justify="center", width=6)
    table.add_column("Words", justify="right", width=8)
    table.add_column("Avg W/P", justify="right", width=8)
    table.add_column("Authors", justify="center", width=8)

    for discussion in report.discussions:
        title = (
            discussion.title[:30] + "..."
            if len(discussion.title) > 30
            else discussion.title
        )
        table.add_row(
            title,
            str(discussion.total_entries),
            str(discussion.reply_count),
            str(discussion.max_reply_depth),
            f"{discussion.total_words:,}",
            f"{discussion.average_words_per_post:.0f}",
            str(discussion.unique_authors),
        )

    console.print(table)

    console.print(
        Panel(
            f"[bold]Discussions:[/bold] {report.total_discussions}\n"
            f"[bold]Total Posts:[/bold] {report.total_posts}\n"
            f"[bold]Total Words:[/bold] {report.total_words:,}\n"
            f"[bold]Avg Words/Post:[/bold] {report.overall_average_words:.1f}\n"
            f"[bold]Median Words/Post:[/bold] {report.overall_median_words:.1f}",
            title="Overall Engagement",
            border_style="green",
        )
    )
