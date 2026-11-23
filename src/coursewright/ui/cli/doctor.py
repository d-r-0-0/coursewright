"""``coursewright ta doctor``: read-only environment and connectivity preflight check."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from coursewright.core.canvas import connect_canvas_from_config
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()

REQUIRED_TEMPLATE_FILES: tuple[str, ...] = (
    "missing_submission.md",
    "missing_lab_document.md",
    "missing_packet_tracer.md",
)
COURSE_POLICIES_FILENAME = "course-policies.yaml"

_STATUS_STYLES: dict[str, str] = {
    "OK": "green",
    "WARN": "yellow",
    "FAIL": "red",
}


def _check_workspace(config: Any) -> tuple[dict[str, Any], Path | None, bool]:
    """Verify the private workspace directory is resolved, exists, and is writable."""
    workspace = resolve_workspace_directory(config)
    if workspace is None:
        return (
            {
                "name": "Workspace",
                "status": "FAIL",
                "detail": (
                    "Workspace not configured "
                    "(set workspace_directory or COURSEWRIGHT_WORKSPACE)."
                ),
                "path": None,
                "exists": False,
                "writable": False,
            },
            None,
            False,
        )

    exists = workspace.exists()
    is_dir = workspace.is_dir()
    if not exists:
        return (
            {
                "name": "Workspace",
                "status": "FAIL",
                "detail": f"Workspace directory does not exist: {workspace}",
                "path": str(workspace),
                "exists": False,
                "writable": False,
            },
            workspace,
            False,
        )
    if not is_dir:
        return (
            {
                "name": "Workspace",
                "status": "FAIL",
                "detail": f"Workspace path is not a directory: {workspace}",
                "path": str(workspace),
                "exists": True,
                "writable": False,
            },
            workspace,
            False,
        )

    writable = bool(os.access(workspace, os.W_OK))
    if not writable:
        return (
            {
                "name": "Workspace",
                "status": "FAIL",
                "detail": f"Workspace directory is not writable: {workspace}",
                "path": str(workspace),
                "exists": True,
                "writable": False,
            },
            workspace,
            True,
        )

    return (
        {
            "name": "Workspace",
            "status": "OK",
            "detail": f"Writable directory: {workspace}",
            "path": str(workspace),
            "exists": True,
            "writable": True,
        },
        workspace,
        True,
    )


def _check_templates(
    config: Any,
    workspace: Path | None,
    workspace_is_dir: bool,
) -> dict[str, Any]:
    """Check ``<workspace>/templates`` and required grading feedback templates."""
    templates_cfg = getattr(config, "templates", None)
    templates_subdir = getattr(templates_cfg, "templates_directory", "templates") or "templates"
    templates_dir = (workspace / templates_subdir) if workspace is not None else None

    if not workspace_is_dir or templates_dir is None:
        return {
            "name": "Templates",
            "status": "WARN",
            "detail": "Skipped (workspace directory unavailable).",
            "directory": str(templates_dir) if templates_dir is not None else None,
            "exists": False,
            "required": list(REQUIRED_TEMPLATE_FILES),
            "present": [],
            "missing": list(REQUIRED_TEMPLATE_FILES),
        }

    if not templates_dir.is_dir():
        return {
            "name": "Templates",
            "status": "WARN",
            "detail": f"Templates folder missing: {templates_dir}",
            "directory": str(templates_dir),
            "exists": False,
            "required": list(REQUIRED_TEMPLATE_FILES),
            "present": [],
            "missing": list(REQUIRED_TEMPLATE_FILES),
        }

    present = [name for name in REQUIRED_TEMPLATE_FILES if (templates_dir / name).is_file()]
    missing = [name for name in REQUIRED_TEMPLATE_FILES if name not in present]
    if missing:
        return {
            "name": "Templates",
            "status": "WARN",
            "detail": f"Missing template(s) in {templates_dir}: {', '.join(missing)}",
            "directory": str(templates_dir),
            "exists": True,
            "required": list(REQUIRED_TEMPLATE_FILES),
            "present": present,
            "missing": missing,
        }

    return {
        "name": "Templates",
        "status": "OK",
        "detail": (
            f"All {len(REQUIRED_TEMPLATE_FILES)} required templates found in {templates_dir}"
        ),
        "directory": str(templates_dir),
        "exists": True,
        "required": list(REQUIRED_TEMPLATE_FILES),
        "present": present,
        "missing": [],
    }


def _check_course_policies(workspace: Path | None, workspace_is_dir: bool) -> dict[str, Any]:
    """Check optional ``<workspace>/course-policies.yaml`` presence and YAML shape."""
    if not workspace_is_dir or workspace is None:
        return {
            "name": "Course Policies",
            "status": "WARN",
            "detail": "Skipped (workspace directory unavailable).",
            "path": None,
            "present": False,
        }

    policies_path = workspace / COURSE_POLICIES_FILENAME
    if not policies_path.is_file():
        return {
            "name": "Course Policies",
            "status": "WARN",
            "detail": f"Optional {COURSE_POLICIES_FILENAME} not found at {policies_path}",
            "path": str(policies_path),
            "present": False,
        }

    try:
        loaded = yaml.safe_load(policies_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as err:
        return {
            "name": "Course Policies",
            "status": "WARN",
            "detail": f"Could not parse {policies_path}: {type(err).__name__}",
            "path": str(policies_path),
            "present": True,
        }

    if loaded is not None and not isinstance(loaded, dict):
        return {
            "name": "Course Policies",
            "status": "WARN",
            "detail": f"Expected YAML mapping in {policies_path}",
            "path": str(policies_path),
            "present": True,
        }

    return {
        "name": "Course Policies",
        "status": "OK",
        "detail": f"Found: {policies_path}",
        "path": str(policies_path),
        "present": True,
    }


def _check_canvas(config: Any, *, as_json: bool) -> dict[str, Any]:
    """Validate Canvas API connectivity without leaking tokens."""
    discussion = getattr(config, "discussion", None)
    canvas_url = getattr(discussion, "canvas_url", None)

    canvas_client = None
    error_message: str | None = None
    try:
        if as_json:
            canvas_client = connect_canvas_from_config(config)
        else:
            with RemoteActivityDisplay("Checking Canvas connection", console=console):
                canvas_client = connect_canvas_from_config(config)
    except Exception as err:
        error_text = str(err).strip()
        error_message = f"{type(err).__name__}: {error_text}" if error_text else type(err).__name__

    if canvas_client is None:
        return {
            "name": "Canvas API",
            "status": "FAIL",
            "detail": error_message or f"Failed to connect to Canvas ({canvas_url or 'default'}).",
            "url": canvas_url,
            "connected": False,
            "auth_mode": None,
        }

    auth_strategy = getattr(canvas_client, "_auth_strategy", None)
    auth_mode = (
        auth_strategy.describe()
        if auth_strategy is not None and hasattr(auth_strategy, "describe")
        else "Canvas client"
    )
    return {
        "name": "Canvas API",
        "status": "OK",
        "detail": f"Connected via {auth_mode} ({canvas_url or 'default'})",
        "url": canvas_url,
        "connected": True,
        "auth_mode": auth_mode,
    }


def _check_llm(config: Any) -> dict[str, Any]:
    """Inspect LLM configuration and key availability without revealing secrets."""
    llm_cfg = getattr(config, "llm", None)
    enabled = bool(getattr(llm_cfg, "enabled", False))
    provider = getattr(llm_cfg, "provider", None)
    model = getattr(llm_cfg, "model", None)

    api_key_configured = False
    if enabled and llm_cfg is not None:
        get_api_key = getattr(llm_cfg, "get_api_key", None)
        if callable(get_api_key):
            try:
                api_key_configured = bool((get_api_key() or "").strip())
            except Exception:
                api_key_configured = False

    model_label = f"{provider or 'unset'}/{model or 'unset'}"
    if not enabled:
        status = "OK"
        detail = f"Disabled in config ({model_label})"
    elif api_key_configured:
        status = "OK"
        detail = f"Enabled ({model_label}); API key configured"
    else:
        status = "WARN"
        detail = f"Enabled ({model_label}); API key missing"

    return {
        "name": "LLM",
        "status": status,
        "detail": detail,
        "enabled": enabled,
        "provider": provider,
        "model": model,
        "api_key_configured": api_key_configured,
    }


def _render_doctor_table(checks: list[dict[str, Any]]) -> None:
    """Render a Rich summary table of preflight checks."""
    table = Table(title="Coursewright Doctor", show_header=True, header_style="bold magenta")
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold", no_wrap=True)
    table.add_column("Details", style="white")

    for check in checks:
        status = check["status"]
        color = _STATUS_STYLES.get(status, "white")
        table.add_row(
            escape(str(check["name"])),
            f"[{color}]{escape(status)}[/{color}]",
            escape(str(check["detail"])),
        )
    console.print(table)


def doctor_command(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON output"),
) -> None:
    """Run read-only preflight checks for workspace, templates, Canvas, and LLM."""
    config: AppConfig = (ctx.obj or {}).get("config") or AppConfig()

    workspace_check, workspace_path, workspace_is_dir = _check_workspace(config)
    templates_check = _check_templates(config, workspace_path, workspace_is_dir)
    policies_check = _check_course_policies(workspace_path, workspace_is_dir)
    canvas_check = _check_canvas(config, as_json=as_json)
    llm_check = _check_llm(config)

    checks = [
        workspace_check,
        templates_check,
        policies_check,
        canvas_check,
        llm_check,
    ]
    summary_checks = [
        {"name": check["name"], "status": check["status"], "detail": check["detail"]}
        for check in checks
    ]

    has_failures = any(check["status"] == "FAIL" for check in checks)
    has_warnings = any(check["status"] == "WARN" for check in checks)

    if as_json:
        payload = {
            "ok": not has_failures,
            "status": "fail" if has_failures else ("warn" if has_warnings else "ok"),
            "workspace": {
                "path": workspace_check["path"],
                "exists": workspace_check["exists"],
                "writable": workspace_check["writable"],
                "status": workspace_check["status"],
                "detail": workspace_check["detail"],
            },
            "templates": {
                "directory": templates_check["directory"],
                "exists": templates_check["exists"],
                "required": templates_check["required"],
                "present": templates_check["present"],
                "missing": templates_check["missing"],
                "status": templates_check["status"],
                "detail": templates_check["detail"],
            },
            "course_policies": {
                "path": policies_check["path"],
                "present": policies_check["present"],
                "status": policies_check["status"],
                "detail": policies_check["detail"],
            },
            "canvas": {
                "url": canvas_check["url"],
                "connected": canvas_check["connected"],
                "auth_mode": canvas_check["auth_mode"],
                "status": canvas_check["status"],
                "detail": canvas_check["detail"],
            },
            "llm": {
                "enabled": llm_check["enabled"],
                "provider": llm_check["provider"],
                "model": llm_check["model"],
                "api_key_configured": llm_check["api_key_configured"],
                "status": llm_check["status"],
                "detail": llm_check["detail"],
            },
            "checks": summary_checks,
        }
        typer.echo(json.dumps(payload, indent=2, default=str))
    else:
        _render_doctor_table(summary_checks)

    if has_failures:
        raise typer.Exit(1) from None
