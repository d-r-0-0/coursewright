"""Discussion assistant CLI commands.

Commands for analyzing discussions and generating instructor responses.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.core.canvas import create_canvas_client
from coursewright.core.config import AppConfig
from coursewright.core.export import MarkdownExporter
from coursewright.core.llm import LLMClient, LLMConfig, LLMProvider
from coursewright.ta.discussions import (
    PostAnalyzer,
    ResponseGenerator,
    SimilarityChecker,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()

assistant_app = typer.Typer(
    name="assistant",
    help="Discussion assistant commands",
    rich_markup_mode="rich",
)


def get_canvas_client(config: AppConfig):
    """Get authenticated Canvas client."""
    try:
        return create_canvas_client(
            base_url=config.discussion.canvas_url,
            api_token_file=config.discussion.api_token_file,
        )
    except Exception as err:
        console.print(f"[red]Failed to connect to Canvas: {err}[/red]")
        return None


def get_llm_client(config: AppConfig, verbose: bool = False) -> LLMClient | None:
    """Get configured LLM client."""
    if not config.llm.enabled:
        console.print(
            "[yellow]LLM is not enabled. Set llm.enabled=true in config.yaml[/yellow]"
        )
        return None

    try:
        provider = LLMProvider(config.llm.default_provider)
        llm_config = LLMConfig(
            provider=provider,
            model=config.llm.default_model,
            api_key=config.llm.get_api_key(),
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        return LLMClient(llm_config, verbose=verbose)
    except Exception as err:
        console.print(f"[red]Failed to initialize LLM client: {err}[/red]")
        return None


@assistant_app.command()
def analyze(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course_id: int | None = typer.Option(None, "--course", "-c", help="Canvas course ID"),
    check_plagiarism: bool = typer.Option(
        True,
        "--check-plagiarism/--no-check-plagiarism",
        help="Check for similar posts (plagiarism detection)",
    ),
    output_dir: str | None = typer.Option(
        None, "--output", "-o", help="Output directory for analysis results"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed LLM interactions"),
):
    """Analyze discussion posts and rank by academic merit."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    if not course_id:
        if config.cli.default_course_id:
            course_id = config.cli.default_course_id
        else:
            console.print("[red]No course ID specified.[/red]")
            raise typer.Exit(1) from None

    canvas_client = get_canvas_client(config)
    if not canvas_client:
        raise typer.Exit(1) from None

    llm_client = get_llm_client(config, verbose=verbose)
    if not llm_client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay("Fetching discussion from Canvas", console=console):
            discussion = canvas_client.get_discussion_topic(course_id, discussion_id)
            if discussion:
                entries = canvas_client.get_discussion_entries(course_id, discussion_id)
            else:
                entries = []

        if not discussion:
            console.print(f"[red]Discussion {discussion_id} not found[/red]")
            raise typer.Exit(1) from None

        console.print(
            Panel(
                f"[bold]Discussion:[/bold] {discussion.title}\n"
                f"[bold]Course ID:[/bold] {course_id}\n"
                f"[bold]Total Replies:[/bold] {discussion.replies_count}",
                title="Discussion Information",
                border_style="blue",
            )
        )

        if not entries:
            console.print("[yellow]No posts found in this discussion[/yellow]")
            raise typer.Exit(0)

        console.print(f"[green]Found {len(entries)} posts[/green]\n")

        ranking_weights = {
            "depth": config.assistant.ranking.depth_weight,
            "relevance": config.assistant.ranking.relevance_weight,
            "originality": config.assistant.ranking.originality_weight,
            "engagement": config.assistant.ranking.engagement_weight,
        }
        analyzer = PostAnalyzer(llm_client=llm_client, ranking_weights=ranking_weights)

        with RemoteActivityDisplay(
            "Analyzing posts with LLM", total=len(entries), console=console
        ) as activity:
            def update_progress(current, total, description="Analyzing..."):
                if total:
                    activity.set_total(int(total))
                activity.completed_steps = int(current)

            ranking = analyzer.analyze_discussion_batch(
                posts=entries,
                discussion_id=discussion_id,
                discussion_title=discussion.title,
                progress_callback=update_progress,
            )

        console.print("[green]Analysis complete![/green]\n")

        similarity_report = None
        if check_plagiarism:
            checker = SimilarityChecker(
                llm_client=llm_client,
                similarity_threshold=config.assistant.plagiarism_threshold,
            )
            with RemoteActivityDisplay("Checking post similarity", console=console):
                similarity_report = checker.check_discussion(
                    posts=entries, discussion_id=discussion_id, use_llm=True
                )

            if similarity_report.suspicious_pairs:
                console.print(
                    f"[yellow]Found {len(similarity_report.suspicious_pairs)} "
                    "suspicious pairs[/yellow]\n"
                )
            else:
                console.print("[green]No suspicious similarities detected[/green]\n")

        console.print(
            Panel("[bold cyan]Top 10 Posts by Academic Merit[/bold cyan]", border_style="cyan")
        )

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Rank", style="cyan", width=6)
        table.add_column("Author", style="yellow", width=20)
        table.add_column("Merit", style="green", width=7, justify="center")
        table.add_column("Summary", style="white")
        table.add_column("Flags", style="red", width=15)

        for post in ranking.get_top_posts(10):
            flags_str = ", ".join(f.value for f in post.flags) if post.flags else "-"
            table.add_row(
                str(post.rank_position),
                post.author_name[:18],
                f"{post.merit_score}/10",
                post.summary[:60] + "..." if len(post.summary) > 60 else post.summary,
                flags_str,
            )

        console.print(table)
        console.print()

        if ranking.flagged_posts:
            console.print(
                Panel(
                    f"[bold yellow]Posts Requiring Attention "
                    f"({len(ranking.flagged_posts)})[/bold yellow]",
                    border_style="yellow",
                )
            )

            for post in ranking.flagged_posts:
                flags_display = ", ".join(f"[red]{f.value}[/red]" for f in post.flags)
                console.print(
                    f"\n[yellow]•[/yellow] [bold]{post.author_name}[/bold] "
                    f"(Post {post.post_id})"
                )
                console.print(f"  Flags: {flags_display}")
                console.print(f"  Reason: {post.reasoning}")
            console.print()

        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            analysis_file = output_path / "post_analysis.json"
            with open(analysis_file, "w", encoding="utf-8") as handle:
                json.dump(ranking.to_dict(), handle, indent=2)
            console.print(f"[green]Analysis saved to {analysis_file}[/green]")

            if similarity_report and similarity_report.suspicious_pairs:
                similarity_file = output_path / "similarity_report.json"
                with open(similarity_file, "w", encoding="utf-8") as handle:
                    json.dump(similarity_report.dict(), handle, indent=2)
                console.print(f"[green]Similarity report saved to {similarity_file}[/green]")

        console.print("\n[cyan]Next steps:[/cyan]")
        console.print(
            "• Generate response: [yellow]coursewright ta assistant respond "
            "<discussion-id> --post <post-id>[/yellow]"
        )
        console.print(
            "• View post details: [yellow]coursewright ta discussions show "
            "<discussion-id>[/yellow]"
        )

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error during analysis: {err}[/red]")
        if verbose:
            console.print(traceback.format_exc())
        raise typer.Exit(1) from None


@assistant_app.command()
def respond(
    ctx: typer.Context,
    discussion_id: int = typer.Argument(..., help="Discussion topic ID"),
    course_id: int | None = typer.Option(None, "--course", "-c", help="Canvas course ID"),
    post_id: int | None = typer.Option(
        None,
        "--post",
        "-p",
        help="Specific post ID to respond to (if not specified, shows top posts)",
    ),
    output_dir: str | None = typer.Option(
        None, "--output", "-o", help="Output directory for response markdown"
    ),
    auto_select: bool = typer.Option(
        False, "--auto", help="Automatically respond to top-ranked post"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed LLM interactions"),
):
    """Generate an instructor response to a discussion post."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    if not course_id:
        if config.cli.default_course_id:
            course_id = config.cli.default_course_id
        else:
            console.print("[red]No course ID specified.[/red]")
            raise typer.Exit(1) from None

    canvas_client = get_canvas_client(config)
    if not canvas_client:
        raise typer.Exit(1) from None

    llm_client = get_llm_client(config, verbose=verbose)
    if not llm_client:
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay("Fetching discussion from Canvas", console=console):
            discussion = canvas_client.get_discussion_topic(course_id, discussion_id)
            entries = (
                canvas_client.get_discussion_entries(course_id, discussion_id)
                if discussion
                else []
            )

        if not discussion:
            console.print(f"[red]Discussion {discussion_id} not found[/red]")
            raise typer.Exit(1) from None

        if not entries:
            console.print("[yellow]No posts found in this discussion[/yellow]")
            raise typer.Exit(0)

        selected_post = None
        if post_id:
            selected_post = next((p for p in entries if p.id == post_id), None)
            if not selected_post:
                console.print(f"[red]Post {post_id} not found[/red]")
                raise typer.Exit(1) from None
        elif auto_select:
            analyzer = PostAnalyzer(llm_client=llm_client)
            with RemoteActivityDisplay("Analyzing posts with LLM", console=console):
                ranking = analyzer.analyze_discussion_batch(
                    posts=entries,
                    discussion_id=discussion_id,
                    discussion_title=discussion.title,
                )

            top_post_analysis = ranking.ranked_posts[0]
            selected_post = next(
                (p for p in entries if p.id == top_post_analysis.post_id), None
            )
            console.print(
                f"[green]Selected top-ranked post by {top_post_analysis.author_name}[/green]\n"
            )
        else:
            analyzer = PostAnalyzer(llm_client=llm_client)
            with RemoteActivityDisplay("Analyzing top posts with LLM", console=console):
                ranking = analyzer.analyze_discussion_batch(
                    posts=entries,
                    discussion_id=discussion_id,
                    discussion_title=discussion.title,
                )

            console.print(Panel("[bold]Top 5 Posts to Respond To[/bold]", border_style="cyan"))
            table = Table(show_header=True)
            table.add_column("#", width=4)
            table.add_column("Post ID", width=10)
            table.add_column("Author", width=20)
            table.add_column("Merit", width=8)
            table.add_column("Summary", width=50)

            for i, post in enumerate(ranking.get_top_posts(5), 1):
                table.add_row(
                    str(i),
                    str(post.post_id),
                    post.author_name,
                    f"{post.merit_score}/10",
                    post.summary[:47] + "..." if len(post.summary) > 47 else post.summary,
                )

            console.print(table)
            console.print()

            choice = typer.prompt("Select post number (1-5) or enter post ID", type=str)
            if choice.isdigit():
                choice_num = int(choice)
                if 1 <= choice_num <= 5:
                    selected_analysis = ranking.get_top_posts(5)[choice_num - 1]
                    selected_post = next(
                        (p for p in entries if p.id == selected_analysis.post_id), None
                    )
                else:
                    selected_post = next((p for p in entries if p.id == choice_num), None)

            if not selected_post:
                console.print("[red]Invalid selection[/red]")
                raise typer.Exit(1) from None

        generator = ResponseGenerator(llm_client=llm_client)
        with RemoteActivityDisplay("Generating response with LLM", console=console):
            response = generator.generate_response(
                post=selected_post, discussion_title=discussion.title
            )

        console.print(
            Panel(
                response.final_content,
                title=f"Instructor Response to {response.student_name}",
                border_style="green",
            )
        )
        console.print(f"\n[dim]Word count: {response.word_count} words[/dim]")

        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            response_file = output_path / "professor_response.md"
            with open(response_file, "w", encoding="utf-8") as handle:
                handle.write(response.get_markdown())
            console.print(f"\n[green]Response saved to {response_file}[/green]")
        elif typer.confirm("\nSave response to file?", default=True):
            with RemoteActivityDisplay("Fetching course details", console=console):
                course_name = canvas_client.get_course(course_id).get("name", "Unknown Course")
            exporter = MarkdownExporter()
            safe_course = exporter._sanitize_for_filesystem(course_name)
            safe_discussion = exporter._sanitize_for_filesystem(discussion.title)
            export_dir = Path("exports") / safe_course / safe_discussion
            export_dir.mkdir(parents=True, exist_ok=True)
            response_file = export_dir / "professor_response.md"
            with open(response_file, "w", encoding="utf-8") as handle:
                handle.write(response.get_markdown())
            console.print(f"[green]Response saved to {response_file}[/green]")

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error generating response: {err}[/red]")
        if verbose:
            console.print(traceback.format_exc())
        raise typer.Exit(1) from None
