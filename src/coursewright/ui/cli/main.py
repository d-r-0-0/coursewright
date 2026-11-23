"""Root ``coursewright`` command.

Sub-apps for the two halves are registered here.  Each command stays a
thin wrapper: parse arguments, call one library function, render the
result with Rich.
"""

from __future__ import annotations

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright import __version__
from coursewright.core.config import (
    WORKSPACE_ENVIRONMENT_VARIABLE,
    AppConfig,
    find_workspace_directory,
)
from coursewright.design.standards import (
    DEFAULT_RUBRIC_ID,
    Rubric,
    RubricError,
    list_rubric_ids,
    load_rubric,
)
from coursewright.ui.cli.analysis import analysis_app
from coursewright.ui.cli.assistant import assistant_app
from coursewright.ui.cli.auth import auth_app
from coursewright.ui.cli.config_manager import config_app
from coursewright.ui.cli.courses import courses_app
from coursewright.ui.cli.design import register_design_commands
from coursewright.ui.cli.digest import daily_digest_command
from coursewright.ui.cli.discussions import discussions_app
from coursewright.ui.cli.llm import llm_app
from coursewright.ui.cli.ta import register_ta_commands, start

__all__ = ["WORKSPACE_ENVIRONMENT_VARIABLE", "app", "find_workspace_directory"]

console = Console()

app = typer.Typer(
    name="coursewright",
    help="An AI teaching assistant and instructional designer for Canvas.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
ta_app = typer.Typer(
    help="Run a live course: discussions, grading, imports.", invoke_without_command=True
)
design_app = typer.Typer(help="Build a course: the five-phase framework.")
interactive_app = typer.Typer(
    name="interactive",
    help="Deprecated compatibility routes for the former interactive menu.",
)
app.add_typer(ta_app, name="ta")
app.add_typer(design_app, name="design")
app.add_typer(interactive_app, name="interactive")
app.add_typer(auth_app, name="auth", help="Authentication management")
app.add_typer(config_app, name="config", help="Configuration management")

ta_app.add_typer(courses_app, name="courses", help="Course management")
discussions_app.command("digest")(daily_digest_command)
ta_app.add_typer(discussions_app, name="discussions", help="Discussion analysis")
ta_app.add_typer(llm_app, name="llm", help="LLM model management")
ta_app.add_typer(assistant_app, name="assistant", help="Discussion assistant")
ta_app.add_typer(analysis_app, name="analyze-course", help="Analyze exported course discussions")
register_ta_commands(ta_app)
register_design_commands(design_app)


def build_welcome_panel() -> Panel:
    """Return the banner shown when ``coursewright`` runs bare."""
    halves = Table.grid(padding=(0, 2))
    halves.add_column(style="bold cyan")
    halves.add_column()
    halves.add_row("coursewright ta", "Run a live course")
    halves.add_row("coursewright design", "Build a course")
    return Panel(
        halves,
        title=f"[bold]Coursewright[/bold] {__version__}",
        subtitle="--help for every command",
        border_style="cyan",
        expand=False,
    )


def show_version_and_exit(requested: bool) -> None:
    """Print the version and stop when ``--version`` was passed."""
    if requested:
        console.print(f"coursewright {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    context: typer.Context,
    config_file: str = typer.Option(
        "config.yaml", "--config", "-c", help="Configuration file path"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output"),
    version: bool = typer.Option(
        False,
        "--version",
        callback=show_version_and_exit,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Show the welcome panel when no command is given."""
    if context.obj is None:
        context.obj = {}
    context.obj["config_file"] = config_file
    context.obj["verbose"] = verbose
    try:
        context.obj["config"] = AppConfig.load_from_file(config_file)
    except (OSError, ValueError, yaml.YAMLError) as err:
        console.print(Panel(str(err), title="Invalid configuration", border_style="red"))
        raise typer.Exit(code=1) from err

    if context.invoked_subcommand is None:
        console.print(build_welcome_panel())


@ta_app.callback()
def ta_root(context: typer.Context) -> None:
    """Run a live course: discussions, grading, imports."""
    if context.invoked_subcommand is None:
        start(context)


@design_app.callback()
def design_root() -> None:
    """Build a course: the five-phase framework."""


@app.command("tui")
def launch_tui(context: typer.Context) -> None:
    """Open the full-screen Textual application."""
    # Imported here so the plain command line starts without loading Textual.
    from coursewright.ui.tui.app import run_tui

    obj = context.obj or {}
    run_tui(config=obj.get("config"), config_file=obj.get("config_file", "config.yaml"))


def show_menu_deprecation_notice(replacement_command: str) -> None:
    """Explain that a legacy menu route now opens the Textual application."""
    console.print(
        Panel(
            "This compatibility route is deprecated and will be removed in a future "
            "release. Launch the Textual application directly with "
            f"[cyan]{replacement_command}[/cyan].",
            title="[yellow]Interactive menu moved[/yellow]",
            border_style="yellow",
        )
    )


@app.command("menu")
def menu(context: typer.Context) -> None:
    """Deprecated: open the Textual app; use ``coursewright tui`` instead."""
    show_menu_deprecation_notice("coursewright tui")
    launch_tui(context)


@interactive_app.command("menu")
def interactive_menu(context: typer.Context) -> None:
    """Deprecated: open the Textual app; use ``coursewright tui`` instead."""
    menu(context)


def load_rubric_or_exit(rubric_id: str, config: AppConfig | None = None) -> Rubric:
    """Load a rubric, turning a load failure into a clean CLI error."""
    try:
        return load_rubric(rubric_id, find_workspace_directory(config))
    except RubricError as err:
        console.print(Panel(str(err), title="Rubric problem", border_style="red"))
        raise typer.Exit(code=1) from None


def build_rubric_table(rubric: Rubric, section_id: str) -> Table:
    """Return one section of a rubric as a table."""
    section_name = next(s.name for s in rubric.sections if s.id == section_id)
    table = Table(title=section_name, title_justify="left", title_style="bold cyan")
    table.add_column("#", style="bold", no_wrap=True)
    if rubric.text_included:
        table.add_column("Standard", overflow="fold")
        table.add_column("Tags", style="magenta", no_wrap=True)
    else:
        table.add_column("Read it at", overflow="fold")
    for standard in rubric.standards_in_section(section_id):
        if rubric.text_included:
            table.add_row(standard.id, standard.text, " ".join(standard.tags))
        else:
            table.add_row(standard.id, standard.url)
    return table


@design_app.command("rubrics")
def list_rubrics(context: typer.Context) -> None:
    """List the course-quality rubrics available."""
    table = Table(title="Rubrics", title_justify="left", title_style="bold cyan")
    for heading in ("Id", "Name", "Standards", "Text", "Licence", "From"):
        table.add_column(heading)
    config = (context.obj or {}).get("config")
    for rubric_id in list_rubric_ids(find_workspace_directory(config)):
        rubric = load_rubric_or_exit(rubric_id, config)
        table.add_row(
            rubric.id + (" [dim](default)[/dim]" if rubric.id == DEFAULT_RUBRIC_ID else ""),
            rubric.name,
            str(len(rubric.standards)),
            "[green]full[/green]" if rubric.text_included else "[yellow]link only[/yellow]",
            rubric.license,
            "bundled" if rubric.origin.startswith("bundled:") else "workspace",
        )
    console.print(table)


@design_app.command("rubric")
def show_rubric(
    context: typer.Context,
    rubric_id: str = typer.Argument(DEFAULT_RUBRIC_ID, help="Rubric id; see `rubrics`."),
) -> None:
    """Show one rubric's standards, section by section."""
    rubric = load_rubric_or_exit(rubric_id, (context.obj or {}).get("config"))
    credit = f"{rubric.publisher} · {rubric.license} · {rubric.source_url}"
    console.print(Panel(credit, title=f"[bold]{rubric.name}[/bold]", border_style="cyan"))
    if not rubric.text_included:
        console.print(
            "[yellow]This rubric is held by reference: its text is not "
            "redistributable. Follow the link to read each standard.[/yellow]"
        )
    for section in rubric.sections:
        console.print(build_rubric_table(rubric, section.id))
