"""LLM management CLI commands."""

from __future__ import annotations

from datetime import datetime

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.core.config import AppConfig
from coursewright.core.llm import (
    LLMProvider,
    UsageLog,
    check_budget_alert,
    estimate_feedback_cost,
    estimate_generation_seconds,
    get_model,
    get_models,
)

console = Console()
llm_app = typer.Typer(help="Manage LLM providers, models, and comparisons.")


def _color_for_provider(provider: LLMProvider) -> str:
    return "cyan" if provider == LLMProvider.OPENAI else "magenta"


def _load_config(ctx: typer.Context) -> AppConfig:
    obj = ctx.obj or {}
    config = obj.get("config")
    if not config:
        config_file = obj.get("config_file", "config.yaml")
        config = AppConfig.load_from_file(config_file)
        if ctx.obj is not None:
            ctx.obj["config"] = config
    return config


def _save_config(ctx: typer.Context, config: AppConfig) -> None:
    obj = ctx.obj or {}
    config_file = obj.get("config_file", "config.yaml")
    config.save_to_file(config_file)
    if ctx.obj is not None:
        ctx.obj["config"] = config
    console.print(f"[green]Saved configuration to {config_file}[/green]")


def _parse_provider(provider: str | None) -> LLMProvider | None:
    if not provider:
        return None
    try:
        return LLMProvider(provider.lower())
    except ValueError as err:
        raise typer.BadParameter("Provider must be 'openai' or 'anthropic'.") from err


@llm_app.command("list-models")
def list_models(
    ctx: typer.Context,
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Filter models by provider (openai or anthropic).",
    ),
):
    """Display supported LLM models with pricing info."""
    config = _load_config(ctx)
    provider_enum = _parse_provider(provider)
    models = get_models(provider_enum)

    if not models:
        console.print("[yellow]No models found for the requested provider.[/yellow]")
        raise typer.Exit(0)

    table = Table(title="Supported LLM Models", show_lines=False, header_style="bold blue")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Speed")
    table.add_column("Input $/1k", justify="right")
    table.add_column("Output $/1k", justify="right")
    table.add_column("Est. Cost/Student", justify="right")
    table.add_column("Tags")

    for metadata in models:
        cost = estimate_feedback_cost(
            metadata.name, completion_tokens=config.llm.max_tokens or 300
        )
        per_student = f"${cost['total_cost']:.4f}" if cost else "n/a"
        tags: list[str] = []
        if metadata.name == config.llm.default_model:
            tags.append("default")
        if metadata.name == config.llm.last_used_model:
            tags.append("last-used")
        if metadata.name in config.llm.comparison_models:
            tags.append("compare")

        table.add_row(
            f"[{_color_for_provider(metadata.provider)}]{metadata.provider.value}[/]",
            metadata.name,
            metadata.speed,
            f"${metadata.input_cost_per_1k:.4f}",
            f"${metadata.output_cost_per_1k:.4f}",
            per_student,
            ", ".join(tags) or "-",
        )

    console.print(table)


@llm_app.command("set-default")
def set_default(
    ctx: typer.Context,
    model_name: str = typer.Argument(..., help="Model identifier, e.g., gpt-4o-mini"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Provider override."),
):
    """Update the default provider and model stored in config."""
    config = _load_config(ctx)
    metadata = get_model(model_name)
    provider_name = (
        provider.lower()
        if provider
        else (metadata.provider.value if metadata else config.llm.default_provider)
    )

    if provider_name not in ("openai", "anthropic"):
        raise typer.BadParameter("Provider must be openai or anthropic.")

    if metadata and metadata.provider.value != provider_name:
        console.print(
            f"[yellow]Model {model_name} belongs to {metadata.provider.value}. "
            f"Using that provider instead of {provider_name}.[/yellow]"
        )
        provider_name = metadata.provider.value

    config.llm.default_provider = provider_name
    config.llm.default_model = model_name
    config.llm.remember_selection(provider_name, model_name)
    _save_config(ctx, config)
    console.print(
        f"[green]Default LLM set to {model_name} ({provider_name}). "
        "Future commands will start here.[/green]"
    )


@llm_app.command("compare")
def compare_models(
    ctx: typer.Context,
    discussion_id: int | None = typer.Argument(
        None,
        help="Optional discussion ID to note in the comparison output.",
    ),
    models: str | None = typer.Option(
        None,
        "--models",
        help="Comma-separated list of model names. Defaults to config.llm.comparison_models.",
    ),
    students: int = typer.Option(
        25,
        "--students",
        "-s",
        help="Number of students to estimate for.",
    ),
    prompt_tokens: int = typer.Option(
        450,
        "--prompt-tokens",
        help="Estimated prompt tokens per student.",
    ),
    completion_tokens: int | None = typer.Option(
        None,
        "--completion-tokens",
        help="Estimated response tokens per student (defaults to config.llm.max_tokens).",
    ),
):
    """Compare multiple models by estimated per-student cost and runtime.

    This is a planning aid and does not call any LLMs.
    """
    config = _load_config(ctx)
    comparison_models = (
        [m.strip() for m in models.split(",") if m.strip()]
        if models
        else config.llm.comparison_models
    )
    if not comparison_models:
        comparison_models = [config.llm.default_model]

    est_completion = completion_tokens or config.llm.max_tokens or 300
    table = Table(title="LLM Cost Comparison", header_style="bold blue")
    table.add_column("Model")
    table.add_column("Provider")
    table.add_column("Cost/Student", justify="right")
    table.add_column("Estimated Total", justify="right")
    table.add_column("Est. Runtime", justify="right")
    table.add_column("Notes")

    for name in comparison_models:
        metadata = get_model(name)
        provider_value = metadata.provider.value if metadata else "custom"
        cost = estimate_feedback_cost(
            name, prompt_tokens=prompt_tokens, completion_tokens=est_completion
        )
        runtime_seconds = estimate_generation_seconds(name, students)

        cost_per_student = cost["total_cost"] if cost else 0.0
        total_cost = cost_per_student * students
        runtime_text = (
            f"{int(runtime_seconds // 60)}m {int(runtime_seconds % 60)}s"
            if runtime_seconds
            else "n/a"
        )

        notes: list[str] = []
        if metadata:
            notes.append(metadata.speed)
        if name == config.llm.default_model:
            notes.append("default")
        if name == config.llm.last_used_model:
            notes.append("last-used")

        table.add_row(
            name,
            provider_value,
            f"${cost_per_student:.4f}" if cost else "n/a",
            f"${total_cost:.2f}" if cost else "n/a",
            runtime_text,
            ", ".join(notes) or "-",
        )

    if discussion_id:
        console.print(
            f"\nComparing models for discussion [cyan]{discussion_id}[/cyan] "
            f"with ~{students} students.\n"
        )

    console.print(table)


@llm_app.command("usage")
def show_usage(
    ctx: typer.Context,
    month: str | None = typer.Option(
        None,
        "--month",
        "-m",
        help="Month as YYYY-MM (defaults to current month).",
    ),
):
    """Show LLM token usage and estimated cost for a month.

    Reads the persistent usage log at ~/.coursewright/usage.json.
    """
    config = _load_config(ctx)

    if month:
        try:
            target = datetime.strptime(month, "%Y-%m")
        except ValueError as err:
            raise typer.BadParameter("Use YYYY-MM format (e.g. 2026-02).") from err
        year, mon = target.year, target.month
    else:
        now = datetime.now()
        year, mon = now.year, now.month

    log = UsageLog.load()
    records = log.monthly_records(year, mon)
    month_label = f"{year:04d}-{mon:02d}"

    if not records:
        console.print(f"[dim]No usage recorded for {month_label}.[/dim]")
        return

    model_totals: dict[str, dict] = {}
    for record in records:
        entry = model_totals.setdefault(record.model, {"tokens": 0, "cost": 0.0, "calls": 0})
        entry["tokens"] += record.tokens_used
        entry["cost"] += record.estimated_cost_usd
        entry["calls"] += 1

    table = Table(
        title=f"LLM Usage — {month_label}",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("Model", style="cyan")
    table.add_column("Calls", justify="center", width=6)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Est. Cost", justify="right", width=10)

    total_cost = 0.0
    total_tokens = 0
    for model_name, totals in sorted(model_totals.items()):
        table.add_row(
            model_name,
            str(totals["calls"]),
            f"{totals['tokens']:,}",
            f"${totals['cost']:.4f}",
        )
        total_cost += totals["cost"]
        total_tokens += totals["tokens"]

    console.print(table)

    console.print(
        Panel(
            f"[bold]Total Tokens:[/bold] {total_tokens:,}\n"
            f"[bold]Total Cost:[/bold] ${total_cost:.4f}",
            title="Monthly Summary",
            border_style="green",
        )
    )

    budget = config.llm.monthly_budget_usd
    if budget and budget > 0:
        alert = check_budget_alert(budget)
        if alert:
            console.print(f"\n[yellow]{alert}[/yellow]")
