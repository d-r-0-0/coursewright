"""``coursewright ta discussions digest``: the daily discussion digest.

A thin wrapper over ``coursewright.ta.discussions.daily_digest``: parse
options, build the digest, write it, render a summary.  All logic lives
in the library (AGENTS.md 3.7).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.ta.discussions.daily_digest import (
    DEFAULT_RUN_BUDGET_SECONDS,
    DigestError,
    InstructorMentionMatcher,
    build_daily_digest,
    write_daily_digest,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay
from coursewright.ui.cli.discussions import get_canvas_client

console = Console()


def daily_digest_command(
    ctx: typer.Context,
    hours: int | None = typer.Option(
        None, "--hours", help="Lookback window (default: 72 on Monday, else 24)"
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Digest path (default: <workspace>/exports/digests/)"
    ),
    instructor_names: list[str] = typer.Option(  # noqa: B008 — Typer option declaration
        [], "--instructor-name", help="Name students use for you; repeatable"
    ),
    budget: float = typer.Option(
        DEFAULT_RUN_BUDGET_SECONDS, "--budget", help="Scan time budget in seconds"
    ),
) -> None:
    """Digest new posts across every course you teach (read-only)."""
    config: AppConfig = (ctx.obj or {}).get("config", AppConfig())
    names = tuple(config.discussion.instructor_names) + tuple(instructor_names)
    matcher = InstructorMentionMatcher(instructor_names=names)

    client = get_canvas_client(config)
    if not client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay("Surveying courses and discussions", console=console):
            digest = build_daily_digest(
                client, lookback_hours=hours, matcher=matcher, run_budget_seconds=budget
            )
        files = write_daily_digest(
            digest,
            digest_path=out,
            # The selected --config decides the workspace, never a stray config.yaml.
            workspace_directory=resolve_workspace_directory(config),
        )
    except DigestError as err:
        console.print(Panel(str(err), title="[red]Digest failed[/red]", border_style="red"))
        raise typer.Exit(1) from None

    if digest.clock_warning:
        console.print(
            Panel(digest.clock_warning, title="[red]⚠ STALE DIGEST[/red]", border_style="red")
        )
    if digest.incomplete_note:
        console.print(f"[red]⚠ INCOMPLETE: {digest.incomplete_note}[/red]")

    table = Table(title=f"New discussion activity — last {digest.lookback_hours}h")
    table.add_column("Course", style="cyan")
    table.add_column("New posts", justify="right", style="green")
    for section in digest.course_sections:
        post_count = sum(len(topic["entries"]) for topic in section["topics"])
        table.add_row(section["course_name"], str(post_count))
    console.print(table)
    console.print(f"[green]Digest:[/green] {files.written_path}")
    if files.canonical_was_preserved:
        console.print(
            f"[yellow]Kept enriched digest at {files.canonical_path}; "
            "raw output went to the .raw.md sidecar.[/yellow]"
        )
    console.print(f"[green]Posts payload:[/green] {files.posts_payload_path}")
