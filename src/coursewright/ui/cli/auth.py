"""Authentication CLI commands for managing Canvas API credentials."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from coursewright.core.auth import (
    StorageMethod,
    get_browser_session_manager,
    get_credential_manager,
)
from coursewright.core.auth.browser_login import run_browser_login
from coursewright.core.canvas import EnhancedCanvasAPI
from coursewright.core.config import AppConfig
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()

auth_app = typer.Typer(
    name="auth",
    help="Authentication management commands",
    rich_markup_mode="rich",
)


@auth_app.command()
def login(
    ctx: typer.Context,
    token: str | None = typer.Option(
        None, "--token", "-t", help="Canvas API token (will prompt if not provided)"
    ),
    canvas_url: str | None = typer.Option(
        None, "--url", "-u", help="Canvas URL (will use config default if not provided)"
    ),
):
    """Set up Canvas API authentication using keyring or file storage."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    cred_manager = get_credential_manager()

    console.print(
        Panel.fit(
            "[bold blue]Canvas API Authentication Setup[/bold blue]\n\n"
            "This will configure your Canvas API credentials for Coursewright.",
            title="Secure Authentication",
            border_style="blue",
        )
    )

    keyring_available = cred_manager.is_keyring_available()

    if keyring_available:
        console.print("\n[bold cyan]Choose authentication storage method:[/bold cyan]\n")
        console.print(
            "  [bold green]1.[/bold green] System Keyring [bold](Secure - Recommended)[/bold]"
        )
        console.print("     • Encrypted storage")
        console.print("     • Protected by OS")
        console.print("     • Industry standard\n")

        console.print("  [yellow]2.[/yellow] File-based (api.key) [yellow](Less Secure)[/yellow]")
        console.print("     • Plain text on disk")
        console.print("     • Not recommended")
        console.print("     • Use only if keyring unavailable\n")

        choice = Prompt.ask("Choice", choices=["1", "2"], default="1", console=console)
        use_keyring = choice == "1"
    else:
        console.print(
            "\n[yellow]System keyring not available. Using file-based storage.[/yellow]"
        )
        use_keyring = False

    if not canvas_url:
        canvas_url = config.discussion.canvas_url
        new_url = Prompt.ask("\n[cyan]Canvas URL[/cyan]", default=canvas_url, console=console)
        if new_url:
            canvas_url = new_url

    config.discussion.canvas_url = canvas_url
    config.save_to_file(obj.get("config_file", "config.yaml"))

    if not token:
        token = Prompt.ask(
            "[cyan]Canvas API token[/cyan]",
            password=True,
            console=console,
        )

    if not token:
        console.print("[red]Token cannot be empty[/red]")
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay("Testing Canvas connection", console=console):
            client = EnhancedCanvasAPI(canvas_url, token)
            courses = client.get_courses()
        console.print(f"[green]Connected! Found {len(courses)} courses.[/green]")
    except Exception as err:
        console.print(f"[red]Connection failed: {err}[/red]")
        console.print("[yellow]Please check your URL and token.[/yellow]")
        raise typer.Exit(1) from None

    success, message = cred_manager.store_token(token, use_keyring=use_keyring)

    if success:
        console.print(f"\n[green]{message}[/green]")
        if use_keyring:
            console.print("\n[bold green]Setup Complete![/bold green]")
            console.print("Your API token is now stored securely in your system keyring.")
        else:
            console.print("\n[yellow]Setup Complete (Using Insecure Storage)[/yellow]")
            console.print("Consider migrating to keyring:")
            console.print("  [cyan]$ coursewright auth migrate-to-keyring[/cyan]")
    else:
        console.print(f"\n[red]{message}[/red]")
        raise typer.Exit(1) from None


@auth_app.command("migrate-to-keyring")
def migrate_to_keyring(ctx: typer.Context):
    """Migrate from api.key to secure keyring storage."""
    cred_manager = get_credential_manager()

    console.print(
        Panel.fit(
            "[bold blue]Migrating to Secure Keyring Storage[/bold blue]\n\n"
            "This will migrate your API token from file-based storage to\n"
            "secure, encrypted system keyring storage.",
            title="Migration",
            border_style="blue",
        )
    )

    if not cred_manager.is_keyring_available():
        console.print("\n[red]System keyring is not available on this system.[/red]")
        console.print(
            "[yellow]You can continue using file-based storage, but it's not recommended.[/yellow]"
        )
        raise typer.Exit(1) from None

    current_method = cred_manager.get_storage_method()

    if current_method == StorageMethod.KEYRING:
        console.print("\n[green]Already using keyring storage![/green]")
        return

    if current_method == StorageMethod.NONE:
        console.print("\n[yellow]No API token found to migrate.[/yellow]")
        console.print("[cyan]Run: coursewright auth login[/cyan]")
        raise typer.Exit(1) from None

    success, message = cred_manager.migrate_to_keyring()

    if success:
        console.print(f"\n[green]{message}[/green]")

        if cred_manager.file_path.exists():
            delete = Confirm.ask(
                "\nDelete api.key file? (Recommended for security)", default=True
            )
            if delete:
                try:
                    cred_manager.file_path.unlink()
                    console.print("[green]api.key file deleted[/green]")
                except Exception as err:
                    console.print(f"[red]Could not delete file: {err}[/red]")

        console.print("\n[bold green]Migration Complete![/bold green]")
        console.print("Your API token is now stored securely in your system keyring.")
    else:
        console.print(f"\n[red]{message}[/red]")
        raise typer.Exit(1) from None


@auth_app.command()
def status(ctx: typer.Context):
    """Show authentication status."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    cred_manager = get_credential_manager()

    console.print(
        Panel.fit(
            "[bold blue]Authentication Status[/bold blue]",
            title="Status",
            border_style="blue",
        )
    )

    table = Table(show_header=False, box=None)
    table.add_column("Property", style="cyan bold")
    table.add_column("Value")

    method = cred_manager.get_storage_method()
    method_display = {
        StorageMethod.KEYRING: "[green]System Keyring (Secure)[/green]",
        StorageMethod.ENVIRONMENT: "[blue]Environment Variable[/blue]",
        StorageMethod.FILE: "[yellow]File-based (api.key)[/yellow]",
        StorageMethod.NONE: "[red]Not Configured[/red]",
    }
    table.add_row("Storage Method", method_display[method])
    table.add_row("Canvas URL", config.discussion.canvas_url)

    token = cred_manager.get_token()
    if token:
        token_display = f"[green]Present[/green] ({len(token)} characters)"
    else:
        token_display = "[red]Not Found[/red]"
    table.add_row("Token Status", token_display)

    console.print(table)

    if token:
        try:
            with RemoteActivityDisplay("Testing Canvas connection", console=console):
                client = EnhancedCanvasAPI(config.discussion.canvas_url, token)
                courses = client.get_courses()
            console.print("[green]Successfully connected to Canvas![/green]")
            console.print(f"[green]Found {len(courses)} courses.[/green]")
        except Exception as err:
            console.print(f"[red]Connection failed: {err}[/red]")

    if method == StorageMethod.FILE:
        console.print("\n[bold yellow]Recommendation:[/bold yellow]")
        console.print("Migrate to secure keyring storage:")
        console.print("  [cyan]$ coursewright auth migrate-to-keyring[/cyan]")
    elif method == StorageMethod.NONE:
        console.print("\n[bold yellow]Next Step:[/bold yellow]")
        console.print("Set up authentication:")
        console.print("  [cyan]$ coursewright auth login[/cyan]")


@auth_app.command()
def logout(ctx: typer.Context):
    """Remove stored API credentials and browser sessions."""
    cred_manager = get_credential_manager()
    session_manager = get_browser_session_manager()

    console.print(
        Panel.fit(
            "[bold red]Logout from Canvas[/bold red]\n\n"
            "This will remove your stored API token and browser session.",
            title="Logout",
            border_style="red",
        )
    )

    method = cred_manager.get_storage_method()
    if method == StorageMethod.NONE and session_manager.get_storage_method() == StorageMethod.NONE:
        console.print("\n[yellow]No stored credentials found.[/yellow]")
        return

    confirm = Confirm.ask("\nRemove stored Canvas credentials?", default=False)
    if not confirm:
        console.print("[yellow]Cancelled[/yellow]")
        return

    delete_file = False
    if cred_manager.file_path.exists():
        delete_file = Confirm.ask("Also delete api.key file if it exists?", default=True)

    success, message = cred_manager.delete_token(delete_file=delete_file)
    session_success, session_message = session_manager.delete_session()
    success = success and session_success
    message = f"{message}\n{session_message}"

    if success:
        console.print(f"\n[green]{message}[/green]")
        console.print("\n[bold green]Logged out successfully![/bold green]")
        console.print("\nTo authenticate again, run:")
        console.print("  [cyan]$ coursewright auth login[/cyan]")
    else:
        console.print(f"\n[red]{message}[/red]")


@auth_app.command()
def test(ctx: typer.Context):
    """Test Canvas API connection using stored credentials."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    cred_manager = get_credential_manager()

    token = cred_manager.get_token()
    if not token:
        console.print("[red]No API token found[/red]")
        console.print("[cyan]Run: coursewright auth login[/cyan]")
        raise typer.Exit(1) from None

    try:
        with RemoteActivityDisplay("Testing Canvas API connection", console=console):
            client = EnhancedCanvasAPI(config.discussion.canvas_url, token)
            courses = client.get_courses()
        console.print("[green]Connected successfully![/green]")
        console.print(f"[green]Found {len(courses)} courses.[/green]\n")

        if courses:
            table = Table(title="Your Courses", show_header=True, header_style="bold cyan")
            table.add_column("#", style="dim", width=4)
            table.add_column("Course Name", style="white")
            for idx, course in enumerate(courses[:5], 1):
                table.add_row(str(idx), course.get("name", "Unknown"))
            console.print(table)

            if len(courses) > 5:
                console.print(f"[dim]... and {len(courses) - 5} more[/dim]")

    except Exception as err:
        console.print(f"[red]Connection failed: {err}[/red]")
        raise typer.Exit(1) from None


@auth_app.command("login-browser")
def login_browser(
    ctx: typer.Context,
    canvas_url: str | None = typer.Option(
        None,
        "--url",
        "-u",
        help="Canvas base URL (uses config default if not provided)",
    ),
    timeout: int = typer.Option(
        300,
        "--timeout",
        "-T",
        help="Seconds to wait for login before giving up (default: 300)",
    ),
):
    """Log in via browser (supports SSO / 2FA)."""
    obj = ctx.obj or {}
    config: AppConfig = obj.get("config", AppConfig())
    effective_url: str = canvas_url or config.discussion.canvas_url

    session_manager = get_browser_session_manager()

    success = run_browser_login(
        canvas_base_url=effective_url,
        timeout_seconds=timeout,
        session_manager=session_manager,
    )

    if not success:
        raise typer.Exit(1) from None
