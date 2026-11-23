"""GradeLiner interactive menu for importing NetAcad grades into Canvas."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from coursewright.core.inbox import find_exported_file
from coursewright.ta.gradeliner.models import AssignmentMapping, GradeImportRow, StudentMapping
from coursewright.ta.gradeliner.orchestrator import GradeLinerUpper
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()

NETACAD_IMPORT_DIR = Path("netacad-import")
NETACAD_IMPORT_DIR_LEGACY = Path("GradeLinErUpper")


def run_gradeliner_flow(
    canvas_client,
    llm_client,
    course_id: int,
    *,
    execute: bool = False,
) -> None:
    """Main entry point for the GradeLiner interactive flow."""
    console.clear()
    console.print(
        Panel.fit(
            "[bold blue]GradeLinerUpper[/bold blue]\n"
            "[dim]NetAcad -> Canvas Grade Import Engine[/dim]",
            border_style="blue",
            padding=(1, 2),
        )
    )

    console.print("\n[bold]Step 1: Provide NetAcad Export[/bold]")

    default_file = _find_newest_netacad_csv()
    if default_file:
        console.print(
            f"[green]Found newest export:[/green] [bold]{default_file.name}[/bold]\n"
            f"[dim]{default_file}[/dim]"
        )
        file_path_str = Prompt.ask(
            "[cyan]Press Enter to use this file, or paste a different path[/cyan]",
            default=str(default_file),
        ).strip()
    else:
        file_path_str = Prompt.ask("[cyan]Path to NetAcad export CSV/TSV[/cyan]").strip()

    file_path = Path(file_path_str).expanduser()

    if not file_path.exists():
        console.print(f"[red]File not found:[/red] {file_path}")
        console.input("\n[dim]Press Enter to return to main menu...[/dim]")
        return

    try:
        with RemoteActivityDisplay("Connecting to Canvas course", console=console):
            orchestrator = GradeLinerUpper(canvas_client, llm_client, course_id, file_path)
        console.print(f"[green]Connected to course:[/green] {orchestrator.course_name}")
    except Exception as err:
        console.print(f"[red]Initialization error:[/red] {rich_escape(str(err))}")
        console.input("\n[dim]Press Enter to return...[/dim]")
        return

    has_cache = orchestrator.has_cached_mappings()
    should_remap = False

    if has_cache:
        console.print("[green]Found cached mappings for this course.[/green]")
        should_remap = Confirm.ask(
            "[yellow]Would you like to force a re-mapping? (Requires LLM calls)[/yellow]",
            default=False,
        )

    if (not has_cache or should_remap) and not _run_mapping_setup(orchestrator):
        console.input("\n[dim]Press Enter to return...[/dim]")
        return

    console.print("\n[bold]Step 2: Select Assignments[/bold]")

    try:
        with RemoteActivityDisplay("Analyzing assignment coverage", console=console):
            coverage = orchestrator.summarize_assignment_coverage()
    except Exception as err:
        console.print(f"[red]Error analyzing assignments:[/red] {rich_escape(str(err))}")
        console.input("\n[dim]Press Enter to return...[/dim]")
        return

    selected_ids = _select_assignments(coverage)
    if not selected_ids:
        console.print("[yellow]No assignments selected — nothing to import.[/yellow]")
        console.input("\n[dim]Press Enter to return...[/dim]")
        return

    console.print("\n[bold]Step 3: Preview Grades[/bold]")

    try:
        with RemoteActivityDisplay("Checking Canvas for existing grades", console=console):
            import_rows = orchestrator.prepare_grade_import(selected_assignment_ids=selected_ids)
    except Exception as err:
        console.print(f"[red]Error processing grades:[/red] {rich_escape(str(err))}")
        console.input("\n[dim]Press Enter to return...[/dim]")
        return

    if not import_rows:
        console.print(
            "[green]All selected assignments are already graded in Canvas. "
            "Nothing new to import![/green]"
        )
        console.input("\n[dim]Press Enter to return...[/dim]")
        return

    all_scores = [row.scaled_score for row in import_rows]
    is_passed, message = orchestrator.validator.perform_smoke_tests(all_scores)

    if not is_passed:
        console.print(Panel(f"[bold red]SMOKE TEST WARNING[/bold red]\n\n{message}", style="red"))
        if not Confirm.ask("Proceed anyway?", default=False):
            return

    _show_grade_preview_table(import_rows)

    console.print(f"\n[bold green]Ready to import {len(import_rows)} grade entries.[/bold green]")
    if not execute:
        console.print(
            "[yellow]DRY RUN — No grades were written (pass --execute to write).[/yellow]"
        )
        console.input("\n[dim]Press Enter to return to menu...[/dim]")
        return

    if Confirm.ask("[bold cyan]Submit these grades to Canvas?[/bold cyan]", default=True):
        try:
            with RemoteActivityDisplay(
                "Submitting grades to Canvas", total=len(import_rows), console=console
            ) as activity:
                results = orchestrator.submit_grades(import_rows, execute=True)
                activity.advance(len(import_rows))
            console.print(
                Panel.fit(
                    f"[bold green]Import Successful![/bold green]\n\n"
                    f"Successfully updated: [bold]{results['successful']}[/bold] entries\n"
                    f"Failed: [bold red]{results['failed']}[/bold red] entries",
                    border_style="green",
                )
            )
        except Exception as err:
            console.print(f"[red]Submission failed:[/red] {rich_escape(str(err))}")

    console.input("\n[dim]Press Enter to return to menu...[/dim]")


def _find_newest_netacad_csv() -> Path | None:
    """Find the newest NetAcad export CSV/TSV, checking Downloads first."""
    candidate = find_exported_file("*.csv", workspace_import_directory=NETACAD_IMPORT_DIR)
    if candidate is not None:
        return candidate.path

    for search_dir in (NETACAD_IMPORT_DIR, NETACAD_IMPORT_DIR_LEGACY):
        if not search_dir.is_dir():
            continue
        csv_files = list(search_dir.glob("*.csv")) + list(search_dir.glob("*.tsv"))
        if csv_files:
            csv_files.sort(key=lambda file_path: file_path.stat().st_mtime, reverse=True)
            return csv_files[0]

    return None


def _select_assignments(coverage: list[dict]) -> set[int]:
    """Interactive assignment picker."""
    if not coverage:
        console.print("[yellow]No mapped assignments found.[/yellow]")
        return set()

    selected_indices: set[int] = {
        index for index, entry in enumerate(coverage) if entry["students_with_scores"] > 0
    }

    while True:
        table = Table(
            title="Assignment Coverage",
            show_header=True,
            header_style="bold magenta",
            box=None,
        )
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Sel", justify="center", width=4)
        table.add_column("Canvas Assignment", style="green", width=36)
        table.add_column("NetAcad Column", style="cyan", width=28)
        table.add_column("Scores", justify="right", width=10)
        table.add_column("Coverage", justify="right", width=10)

        for index, entry in enumerate(coverage):
            is_selected = index in selected_indices
            checkbox = "[bold green]✓[/bold green]" if is_selected else "[dim]·[/dim]"
            score_count = entry["students_with_scores"]
            total_count = entry["total_students"]
            pct = entry["score_percentage"]

            if pct >= 50:
                pct_display = f"[green]{pct:.0f}%[/green]"
            elif pct > 0:
                pct_display = f"[yellow]{pct:.0f}%[/yellow]"
            else:
                pct_display = f"[dim]{pct:.0f}%[/dim]"

            table.add_row(
                str(index + 1),
                checkbox,
                entry["canvas_assignment_name"],
                entry["netacad_column"],
                f"{score_count}/{total_count}",
                pct_display,
            )

        console.print(table)
        console.print(
            f"\n[dim]{len(selected_indices)} of {len(coverage)} assignments selected.[/dim]"
        )
        console.print(
            "[dim]Enter numbers to toggle (e.g. '1 3 5'), "
            "'all' to select all, 'none' to clear, or 'ok' to confirm.[/dim]"
        )

        choice = Prompt.ask("[cyan]Toggle / confirm[/cyan]", default="ok").strip().lower()

        if choice in ("ok", ""):
            break
        if choice == "all":
            selected_indices = set(range(len(coverage)))
        elif choice == "none":
            selected_indices = set()
        else:
            for token in choice.replace(",", " ").split():
                try:
                    num = int(token) - 1
                    if 0 <= num < len(coverage):
                        if num in selected_indices:
                            selected_indices.discard(num)
                        else:
                            selected_indices.add(num)
                    else:
                        console.print(f"[red]'{token}' out of range.[/red]")
                except ValueError:
                    console.print(f"[red]'{token}' is not a valid number.[/red]")

    return {coverage[index]["canvas_assignment_id"] for index in selected_indices}


def _run_mapping_setup(orchestrator: GradeLinerUpper) -> bool:
    """Orchestrate the interactive mapping approval flow."""
    if orchestrator.llm_client is None:
        console.print(
            "[red]LLM client is not available — cannot create mappings.[/red]\n"
            "[dim]Ensure your OpenAI API key is in openai.key or set OPENAI_API_KEY.[/dim]"
        )
        return False

    console.print("\n[bold]Step 1.1: Assignment Mapping[/bold]")

    try:
        with RemoteActivityDisplay(
            "Analyzing NetAcad columns and Canvas assignments", console=console
        ):
            assignment_proposals = orchestrator.get_assignment_mapping_proposals()
        _display_assignment_mapping_table(assignment_proposals)

        if not Confirm.ask(
            "\n[bold cyan]Approve these assignment mappings?[/bold cyan]", default=True
        ):
            console.print("[yellow]Setup cancelled by user.[/yellow]")
            return False

        orchestrator.save_assignment_mappings(assignment_proposals)
        console.print("[green]Assignment mappings saved.[/green]")

        console.print("\n[bold]Step 1.2: Student Identity Matching[/bold]")

        with RemoteActivityDisplay("Matching NetAcad roster to Canvas users", console=console):
            student_proposals = orchestrator.get_student_mapping_proposals()
        _display_student_mapping_table(student_proposals)

        if not Confirm.ask("\n[bold cyan]Approve these student matches?[/bold cyan]", default=True):
            console.print("[yellow]Setup cancelled by user.[/yellow]")
            return False

        orchestrator.save_student_mappings(student_proposals)
        console.print("[green]Student mappings saved.[/green]")
        return True

    except Exception as err:
        console.print(f"[red]Mapping setup failed:[/red] {rich_escape(str(err))}")
        return False


def _display_assignment_mapping_table(mappings: list[AssignmentMapping]) -> None:
    """Render a Rich table for assignment mappings."""
    table = Table(
        title="Proposed Assignment Mappings",
        show_header=True,
        header_style="bold magenta",
        box=None,
    )
    table.add_column("NetAcad Column", style="cyan", width=30)
    table.add_column("Canvas Assignment", style="green", width=40)
    table.add_column("Scaling (N->C)", justify="center")
    table.add_column("Confidence", justify="center")

    for mapping in mappings:
        confidence_style = {
            "high": "green",
            "medium": "yellow",
            "low": "red",
        }.get(mapping.confidence.lower(), "white")

        table.add_row(
            mapping.netacad_column,
            mapping.canvas_assignment_name,
            f"{mapping.netacad_points_possible} -> {mapping.canvas_points_possible}",
            f"[{confidence_style}]{mapping.confidence}[/{confidence_style}]",
        )
    console.print(table)


def _display_student_mapping_table(mappings: list[StudentMapping]) -> None:
    """Render a Rich table for student mappings."""
    table = Table(
        title="Proposed Student Mappings",
        show_header=True,
        header_style="bold magenta",
        box=None,
    )
    table.add_column("NetAcad Name", style="cyan", width=25)
    table.add_column("Canvas Name", style="green", width=25)
    table.add_column("Match Method", style="dim")
    table.add_column("Confidence", justify="center")

    for mapping in mappings:
        confidence_style = {
            "high": "green",
            "medium": "yellow",
            "low": "red",
        }.get(mapping.confidence.lower(), "white")

        table.add_row(
            mapping.netacad_name,
            mapping.canvas_name,
            mapping.match_method,
            f"[{confidence_style}]{mapping.confidence}[/{confidence_style}]",
        )
    console.print(table)


def _show_grade_preview_table(rows: list[GradeImportRow]) -> None:
    """Render a Rich table for grade preview."""
    table = Table(
        title="Grade Import Preview (Top 20)",
        show_header=True,
        header_style="bold magenta",
        box=None,
    )
    table.add_column("Student", style="cyan", width=30)
    table.add_column("Assignment", style="green", width=35)
    table.add_column("NetAcad %", justify="right")
    table.add_column("Canvas Points", justify="right", style="bold yellow")

    preview_limit = 20
    for row in rows[:preview_limit]:
        table.add_row(
            row.canvas_name,
            row.assignment_name,
            f"{row.netacad_score}%",
            f"{row.scaled_score:.1f}",
        )

    if len(rows) > preview_limit:
        table.add_row("...", "...", "...", "...")

    console.print(table)
    console.print(
        f"\n[dim]Showing {min(len(rows), preview_limit)} of {len(rows)} total entries.[/dim]"
    )
