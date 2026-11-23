"""Tests for cantrips/audit_deps.py, cantrips/audit_workflows.py, and .github/workflows/."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cantrips import audit_deps, audit_workflows  # noqa: E402


def test_collect_requirements_files_finds_root_and_cantrips_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "requirements.txt").write_text("httpx>=0.25\n", encoding="utf-8")
    cantrips_dir = tmp_path / "cantrips"
    cantrips_dir.mkdir()
    (cantrips_dir / "requirements.txt").write_text("ruff>=0.6\n", encoding="utf-8")

    monkeypatch.setattr(audit_deps, "_REPO_ROOT", tmp_path)
    found = audit_deps.collect_requirements_files()
    assert [path.relative_to(tmp_path).as_posix() for path in found] == [
        "requirements.txt",
        "cantrips/requirements.txt",
    ]


def test_run_audit_invokes_pip_audit_with_fix_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("httpx>=0.25\n", encoding="utf-8")
    recorded_commands: list[list[str]] = []

    def fake_run(command: list[str], check: bool = False) -> subprocess.CompletedProcess:
        recorded_commands.append(command)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(audit_deps, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = audit_deps.run_audit(req, fix_dry_run=True)
    assert exit_code == 0
    assert recorded_commands == [
        [sys.executable, "-m", "pip_audit", "-r", str(req), "--fix", "--dry-run"]
    ]


def test_run_environment_audit_tolerates_offline_dns_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr="Temporary failure in name resolution",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert audit_deps.run_environment_audit(fix_dry_run=False) == 0


def test_run_environment_audit_fails_on_vulnerability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="Found 1 known vulnerability in 1 package",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert audit_deps.run_environment_audit(fix_dry_run=False) == 1


def test_find_zizmor_executable_searches_candidate_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary_name = "zizmor.exe" if audit_workflows.os.name == "nt" else "zizmor"
    fake_binary = tmp_path / binary_name
    fake_binary.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(
        audit_workflows,
        "candidate_script_directories",
        lambda: [tmp_path / "empty", tmp_path],
    )
    assert audit_workflows.find_zizmor_executable() == fake_binary


def test_workflows_have_no_write_permissions_or_pull_request_target() -> None:
    """Acceptance 4: no workflow has write permissions or uses pull_request_target."""
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(
        workflows_dir.glob("*.yaml")
    )
    assert workflow_files, "Expected at least one workflow in .github/workflows/"

    for workflow_path in workflow_files:
        raw_text = workflow_path.read_text(encoding="utf-8")
        assert "pull_request_target" not in raw_text, (
            f"{workflow_path.name} must not use pull_request_target"
        )
        parsed = yaml.safe_load(raw_text)
        triggers = parsed.get("on") or parsed.get(True) or {}
        assert "pull_request_target" not in triggers

        top_permissions = parsed.get("permissions", {})
        assert top_permissions != "write-all"
        if isinstance(top_permissions, dict):
            assert "write" not in top_permissions.values()

        for job_name, job in parsed.get("jobs", {}).items():
            job_permissions = job.get("permissions", {})
            assert job_permissions != "write-all", (
                f"{workflow_path.name}:{job_name} uses write-all"
            )
            if isinstance(job_permissions, dict):
                assert "write" not in job_permissions.values(), (
                    f"{workflow_path.name}:{job_name} grants write permission"
                )
