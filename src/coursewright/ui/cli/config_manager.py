"""Configuration management CLI commands."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from coursewright.core.config import AppConfig

console = Console()

config_app = typer.Typer(
    name="config",
    help="Configuration management commands",
    rich_markup_mode="rich",
)


@config_app.command()
def show(ctx: typer.Context):
    """Show current configuration."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    config_file = obj.get("config_file", "config.yaml")

    console.print(
        Panel.fit(
            f"[bold blue]Current Configuration[/bold blue]\n\nConfiguration file: {config_file}",
            title="Configuration",
            border_style="blue",
        )
    )

    canvas_table = Table(title="Canvas Settings", show_header=True, header_style="bold magenta")
    canvas_table.add_column("Setting", style="cyan")
    canvas_table.add_column("Value", style="white")
    canvas_table.add_row("Canvas URL", config.discussion.canvas_url)
    canvas_table.add_row("API Token File", config.discussion.api_token_file)
    canvas_table.add_row("API Delay", f"{config.discussion.api_delay}s")
    canvas_table.add_row("Max Retries", str(config.discussion.max_retries))
    console.print(canvas_table)

    grading_table = Table(title="Grading Settings", show_header=True, header_style="bold magenta")
    grading_table.add_column("Setting", style="cyan")
    grading_table.add_column("Value", style="white")
    grading_table.add_row("Default Min Words", str(config.discussion.default_min_words))
    grading_table.add_row("Default Min Replies", str(config.discussion.default_min_replies))
    grading_table.add_row("Default Rubric", config.discussion.default_rubric)
    grading_table.add_row(
        "Default Grading Profile", config.discussion.default_grading_profile
    )
    grading_table.add_row(
        "Include Instructor Posts", str(config.discussion.include_instructor_posts)
    )
    grading_table.add_row("Include TA Posts", str(config.discussion.include_ta_posts))
    grading_table.add_row("Count Nested Replies", str(config.discussion.count_nested_replies))
    grading_table.add_row("Max Thread Depth", str(config.discussion.max_thread_depth))
    console.print(grading_table)

    export_table = Table(title="Export Settings", show_header=True, header_style="bold magenta")
    export_table.add_column("Setting", style="cyan")
    export_table.add_column("Value", style="white")
    export_table.add_row("Default Format", config.discussion.export.format.value)
    export_table.add_row("Output Directory", config.discussion.export.output_directory)
    export_table.add_row("Filename Prefix", config.discussion.export.filename_prefix)
    export_table.add_row("Include Raw Data", str(config.discussion.export.include_raw_data))
    export_table.add_row("Include Post Content", str(config.discussion.export.include_post_content))
    export_table.add_row("Include Timestamps", str(config.discussion.export.include_timestamps))
    export_table.add_row("Date Format", config.discussion.export.date_format)
    console.print(export_table)

    cli_table = Table(title="CLI Settings", show_header=True, header_style="bold magenta")
    cli_table.add_column("Setting", style="cyan")
    cli_table.add_column("Value", style="white")
    default_course = (
        str(config.cli.default_course_id) if config.cli.default_course_id else "Not set"
    )
    cli_table.add_row("Default Course ID", default_course)
    cli_table.add_row("Show Progress Bars", str(config.cli.show_progress_bars))
    cli_table.add_row("Colored Output", str(config.cli.colored_output))
    cli_table.add_row("Table Style", config.cli.table_style)
    cli_table.add_row("Max Table Width", str(config.cli.max_table_width))

    recent_courses = (
        ", ".join(map(str, config.cli.recent_courses[:3])) if config.cli.recent_courses else "None"
    )
    if len(config.cli.recent_courses) > 3:
        recent_courses += "..."
    cli_table.add_row("Recent Courses", recent_courses)
    console.print(cli_table)


@config_app.command()
def set(
    ctx: typer.Context,
    setting: str = typer.Argument(
        ..., help="Setting to change (use dot notation like 'discussion.default_min_words')"
    ),
    value: str = typer.Argument(..., help="New value for the setting"),
):
    """Set a configuration value and save it to the config file."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    config_file = obj.get("config_file", "config.yaml")

    try:
        parts = setting.split(".")
        current = config
        for part in parts[:-1]:
            if hasattr(current, part):
                current = getattr(current, part)
            else:
                console.print(f"[red]Invalid setting path: {setting}[/red]")
                console.print(
                    "[cyan]Use [yellow]coursewright config show[/yellow] "
                    "to see available settings[/cyan]"
                )
                raise typer.Exit(1) from None

        final_key = parts[-1]
        if not hasattr(current, final_key):
            console.print(
                f"[red]Setting '{final_key}' not found in {'.'.join(parts[:-1])}[/red]"
            )
            raise typer.Exit(1) from None

        current_value = getattr(current, final_key)
        value_type = type(current_value)

        try:
            if value_type is bool:
                converted_value = value.lower() in ("true", "1", "yes", "on")
            elif value_type is int:
                converted_value = int(value)
            elif value_type is float:
                converted_value = float(value)
            elif hasattr(current_value, "value"):
                enum_class = type(current_value)
                converted_value = enum_class(value)
            else:
                converted_value = value
        except (ValueError, TypeError):
            console.print(
                f"[red]Invalid value '{value}' for setting '{setting}' "
                f"(expected {value_type.__name__})[/red]"
            )
            raise typer.Exit(1) from None

        setattr(current, final_key, converted_value)
        config.save_to_file(config_file)

        console.print(f"[green]Setting '{setting}' updated[/green]")
        console.print(f"   Old value: {current_value}")
        console.print(f"   New value: {converted_value}")
        console.print(f"   Saved to: {config_file}")

    except typer.Exit:
        raise
    except Exception as err:
        console.print(f"[red]Error setting configuration: {err}[/red]")
        raise typer.Exit(1) from None


@config_app.command()
def reset(
    ctx: typer.Context,
    confirm_reset: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Reset configuration to defaults."""
    obj = ctx.obj or {}
    config_file = obj.get("config_file", "config.yaml")

    if not confirm_reset:
        console.print("[yellow]This will reset all configuration to defaults.[/yellow]")
        confirm_reset = Confirm.ask("Are you sure you want to continue?")

    if not confirm_reset:
        console.print("[blue]Reset cancelled[/blue]")
        return

    try:
        config_path = Path(config_file)
        if config_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = config_path.with_suffix(f".backup_{timestamp}.yaml")
            shutil.copy2(config_path, backup_file)
            console.print(f"[cyan]Backup created: {backup_file}[/cyan]")

        default_config = AppConfig()
        default_config.save_to_file(config_file)
        if ctx.obj is not None:
            ctx.obj["config"] = default_config

        console.print("[green]Configuration reset to defaults[/green]")
        console.print(f"   Configuration saved to: {config_file}")
        console.print(
            "[cyan]Run [yellow]coursewright config show[/yellow] to see current settings[/cyan]"
        )

    except Exception as err:
        console.print(f"[red]Error resetting configuration: {err}[/red]")
        raise typer.Exit(1) from None


@config_app.command("export")
def export_config(
    ctx: typer.Context,
    output_file: str = typer.Argument(..., help="Output file path for exported configuration"),
):
    """Export current configuration to a YAML file."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    try:
        config.save_to_file(output_file)
        console.print(f"[green]Configuration exported to {output_file}[/green]")
    except Exception as err:
        console.print(f"[red]Error exporting configuration: {err}[/red]")
        raise typer.Exit(1) from None


@config_app.command("import")
def import_config(
    ctx: typer.Context,
    input_file: str = typer.Argument(..., help="Input file path for configuration to import"),
    merge: bool = typer.Option(
        False, "--merge", "-m", help="Merge with existing config instead of replacing"
    ),
):
    """Import configuration from a YAML file."""
    obj = ctx.obj or {}
    config_file = obj.get("config_file", "config.yaml")
    input_path = Path(input_file)

    if not input_path.exists():
        console.print(f"[red]Configuration file not found: {input_file}[/red]")
        raise typer.Exit(1) from None

    try:
        new_config = AppConfig.load_from_file(input_file)

        if merge:
            console.print("[blue]Merging configurations...[/blue]")
            console.print(
                "[yellow]Simple merge: importing configuration will replace "
                "current settings[/yellow]"
            )
            console.print(
                "[cyan]For fine-grained control, use [yellow]coursewright config set[/yellow] "
                "for individual settings[/cyan]"
            )

        new_config.save_to_file(config_file)
        if ctx.obj is not None:
            ctx.obj["config"] = new_config

        console.print(f"[green]Configuration imported from {input_file}[/green]")
        console.print(f"   Saved to: {config_file}")
        console.print(
            "[cyan]Run [yellow]coursewright config show[/yellow] to verify settings[/cyan]"
        )

    except Exception as err:
        console.print(f"[red]Error importing configuration: {err}[/red]")
        raise typer.Exit(1) from None


@config_app.command()
def validate(ctx: typer.Context):
    """Validate current configuration."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())

    console.print("[blue]Validating configuration...[/blue]")

    errors: list[str] = []
    warnings: list[str] = []

    if not config.discussion.canvas_url:
        errors.append("Canvas URL is not set")
    elif not config.discussion.canvas_url.startswith(("http://", "https://")):
        errors.append("Canvas URL must start with http:// or https://")

    token_file = Path(config.discussion.api_token_file)
    if not token_file.exists():
        warnings.append(f"API token file not found: {token_file}")
    elif not token_file.read_text(encoding="utf-8").strip():
        warnings.append("API token file is empty")

    if config.discussion.default_min_words < 0:
        errors.append("Default minimum words cannot be negative")

    if config.discussion.default_min_replies < 0:
        errors.append("Default minimum replies cannot be negative")

    export_dir = Path(config.discussion.export.output_directory)
    try:
        export_dir.mkdir(exist_ok=True)
    except Exception:
        errors.append(f"Cannot create export directory: {export_dir}")

    if config.discussion.api_delay < 0:
        errors.append("API delay cannot be negative")

    if config.discussion.max_retries < 0:
        errors.append("Max retries cannot be negative")

    if errors:
        console.print(
            Panel.fit(
                "[bold red]Configuration Errors Found[/bold red]\n\n"
                + "\n".join(f"• {error}" for error in errors),
                title="Validation Errors",
                border_style="red",
            )
        )

    if warnings:
        console.print(
            Panel.fit(
                "[bold yellow]Configuration Warnings[/bold yellow]\n\n"
                + "\n".join(f"• {warning}" for warning in warnings),
                title="Validation Warnings",
                border_style="yellow",
            )
        )

    if not errors and not warnings:
        console.print(
            Panel.fit(
                "[bold green]Configuration is valid![/bold green]\n\n"
                "No errors or warnings found in your configuration.",
                title="Validation Success",
                border_style="green",
            )
        )

    if errors:
        console.print("[cyan]Fix the errors above and run validation again[/cyan]")
        raise typer.Exit(1) from None
    if warnings:
        console.print("[cyan]Consider addressing the warnings above[/cyan]")
