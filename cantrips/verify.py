#!/usr/bin/env python3
"""
CANTRIP: verify

PURPOSE:
    The single definition of "is this code correct?" for this repo.

    Every other entry point delegates here — the Makefile,
    cantrips/verify.sh, the CI workflow, and the Claude Code Stop
    hook.  One definition means they cannot drift apart, which is the
    failure this file exists to prevent.

    The stages themselves are *data*, not code: they live in
    verify.toml at the repo root.  Adapting this template to a new
    stack means editing one file instead of find-and-replacing
    {{LINT_COMMAND}} across the Makefile, the shell script, the CI
    workflow and AGENTS.md.

    Two portability problems this solves, both of which bit the
    projects this template was extracted from:

      * `make` and most linters are not on PATH on Windows, so a gate
        expressed as `make verify` cannot run on the platform this
        template is developed on.  A bare `python` token in any stage
        command is rewritten to the interpreter running this file.
      * The repo's .venv usually holds the real dependencies while the
        ambient `python` does not.  Deciding which interpreter to use
        was a judgment re-supplied every session; this cantrip makes it.

USAGE:
    Full gate (what agents and CI must run):
        python cantrips/verify.py

    A single stage, or several (run in verify.toml order):
        python cantrips/verify.py --only lint
        python cantrips/verify.py --only scaffold,doc-drift,doc-budget

    List the configured stages without running them:
        python cantrips/verify.py --list

    Stay on the current interpreter instead of the repo .venv:
        python cantrips/verify.py --no-venv

    Template repo CI only -- skip (loudly) stages still holding a
    {{TOKEN}} placeholder instead of refusing to run:
        python cantrips/verify.py --allow-placeholders

DEPENDENCIES:
    - Python 3.11+ (uses the stdlib tomllib; no third-party imports,
      so the gate can always run even when deps are broken)

FUNCTIONS (quick map):
    - find_repo_virtualenv_python: Locate .venv's interpreter, if any
    - reexec_into_virtualenv:      Re-run this cantrip under that one
    - discover_cantrip_modules:    Find every cantrip to compile-check
    - resolve_command:             Normalize a configured command
    - load_stages:                 Read verify.toml into Stage objects
    - has_unreplaced_placeholder: Spot a stage that was never filled in
    - is_unmodified_template:     Tell the template apart from a project
    - run_stage:                   Execute one stage, report pass/fail
    - main:                        Parse args, run stages, set exit code
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANTRIPS_DIR = ROOT / "cantrips"
CONFIG_PATH = ROOT / "verify.toml"
VIRTUALENV_DIR = ROOT / ".venv"

# Guards against a re-exec loop if the interpreter path comparison
# ever fails to recognize that we are already inside the virtualenv.
REEXEC_GUARD_VARIABLE = "VERIFY_REEXEC"

# Tokens rewritten to sys.executable so stages never depend on PATH.
PYTHON_TOKENS = frozenset({"python", "python3", "py"})

# Present in AGENTS.md only while this checkout is still the template.
TEMPLATE_SENTINEL = "{{PROJECT_DESCRIPTION}}"

USE_COLOR = sys.stdout.isatty()
GREEN = "\033[0;32m" if USE_COLOR else ""
RED = "\033[0;31m" if USE_COLOR else ""
YELLOW = "\033[1;33m" if USE_COLOR else ""
CYAN = "\033[0;36m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""


@dataclass(frozen=True)
class Stage:
    """One named step of the verification gate.

    Attributes:
        name: Short identifier accepted by ``--only``.
        description: Human-readable summary shown while running.
        command: Argument list, already interpreter-resolved.
        working_directory: Where to run it.
        tolerated_exit_codes: Non-zero codes that still count as a pass.
    """

    name: str
    description: str
    command: list[str]
    working_directory: Path
    tolerated_exit_codes: tuple[int, ...] = field(default=())


def find_repo_virtualenv_python() -> Path | None:
    """Return the repo ``.venv`` interpreter, if it exists."""
    candidates = (
        VIRTUALENV_DIR / "Scripts" / "python.exe",  # Windows
        VIRTUALENV_DIR / "bin" / "python",  # POSIX
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def reexec_into_virtualenv() -> None:
    """Re-run this cantrip under the repo virtualenv when appropriate.

    The ambient ``python`` is frequently not the one holding the
    project's dependencies, which makes the gate fail for reasons
    unrelated to the code under test.  Rather than asking every
    contributor and agent to remember which interpreter to use, the
    gate picks it.

    Does nothing when there is no ``.venv``, when already running
    inside it, or when the guard variable is set.  CI is unaffected
    because CI checks out a tree with no ``.venv``.
    """
    if os.environ.get(REEXEC_GUARD_VARIABLE):
        return

    virtualenv_python = find_repo_virtualenv_python()
    if virtualenv_python is None:
        return

    # Compare environments, not interpreter paths: on POSIX a venv's
    # bin/python is a symlink to the system interpreter, so resolving
    # both paths makes the system Python look like the virtualenv.
    if Path(sys.prefix).resolve() == VIRTUALENV_DIR.resolve():
        return

    print(f"{CYAN}Using repo virtualenv:{RESET} {virtualenv_python}")

    completed = subprocess.run(  # noqa: S603 - argv is repo-local, not user input
        [str(virtualenv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=ROOT,
        check=False,
        env={**os.environ, REEXEC_GUARD_VARIABLE: "1"},
    )
    sys.exit(completed.returncode)


def discover_cantrip_modules() -> list[str]:
    """Return every Python cantrip that should be compile-checked.

    Discovering from disk rather than from a hand-maintained list means
    a newly added cantrip is covered by the gate the moment it lands.
    Files prefixed with ``_`` are starters, not runnable code.

    Returns:
        Repo-relative POSIX paths, sorted for stable output.
    """
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in CANTRIPS_DIR.glob("*.py")
        if not path.name.startswith("_")
    )


def resolve_command(raw_command: str | list[str]) -> list[str]:
    """Normalize a configured command into an argument list.

    A string is shell-split for convenience; a list is taken as-is and
    is the right choice when any argument contains spaces.  A leading
    ``python`` token is rewritten to the running interpreter so stages
    never depend on what happens to be on PATH.

    Args:
        raw_command: The ``command`` value from verify.toml.

    Returns:
        An argument list ready for ``subprocess.run``.

    Raises:
        ValueError: If the command is empty.
    """
    parts = shlex.split(raw_command) if isinstance(raw_command, str) else list(raw_command)

    if not parts:
        raise ValueError("stage command is empty")

    if parts[0] in PYTHON_TOKENS:
        parts[0] = sys.executable

    return parts


def has_unreplaced_placeholder(stage: Stage) -> bool:
    """Report whether a stage still carries a ``{{TOKEN}}`` placeholder."""
    return any("{{" in part for part in stage.command)


def is_unmodified_template() -> bool:
    """Report whether this checkout is still the template, not a project.

    ``--allow-placeholders`` exists so the template repo's own CI can be
    green. It must not survive into a derived project, where it would
    turn two skipped stages into a quietly passing gate. The cheapest
    reliable tell is the project-description placeholder in AGENTS.md,
    which the README's quick start replaces before anything else.
    """
    agents_path = ROOT / "AGENTS.md"
    if not agents_path.is_file():
        return False
    return TEMPLATE_SENTINEL in agents_path.read_text(encoding="utf-8")


def load_stages() -> list[Stage]:
    """Read verify.toml and build the ordered stage list.

    Placeholders are not rejected here. ``--list`` and ``--only`` must
    keep working on a freshly instantiated template, so enforcement
    happens in ``main`` against the stages actually selected.

    Returns:
        Stages in declaration order.

    Raises:
        SystemExit: If verify.toml is missing or a stage is malformed.
    """
    if not CONFIG_PATH.is_file():
        sys.exit(
            f"{RED}verify.toml not found at {CONFIG_PATH}.{RESET}\n"
            f"The gate is defined there; see the template README."
        )

    with CONFIG_PATH.open("rb") as config_file:
        try:
            config = tomllib.load(config_file)
        except tomllib.TOMLDecodeError as err:
            sys.exit(f"{RED}verify.toml is not valid TOML: {err}{RESET}")

    stages: list[Stage] = []

    for index, entry in enumerate(config.get("stage", [])):
        name = entry.get("name")
        if not name:
            sys.exit(f"{RED}verify.toml stage #{index + 1} has no name.{RESET}")

        if entry.get("builtin") == "compile-cantrips":
            cantrips = discover_cantrip_modules()
            command = [sys.executable, "-m", "py_compile", *cantrips]
            description = entry.get(
                "description", f"Compile-check {len(cantrips)} cantrips"
            )
        else:
            raw_command = entry.get("command")
            if raw_command is None:
                sys.exit(
                    f"{RED}verify.toml stage '{name}' needs a command "
                    f"or a known builtin.{RESET}"
                )
            try:
                command = resolve_command(raw_command)
            except ValueError as err:
                sys.exit(f"{RED}verify.toml stage '{name}': {err}{RESET}")

            description = entry.get("description", name)

        stages.append(
            Stage(
                name=name,
                description=description,
                command=command,
                working_directory=ROOT / entry.get("cwd", "."),
                tolerated_exit_codes=tuple(entry.get("tolerated_exit_codes", ())),
            )
        )

    if not stages:
        sys.exit(f"{RED}verify.toml declares no stages.{RESET}")

    return stages


def run_stage(stage: Stage) -> bool:
    """Execute one stage and report whether it passed.

    Args:
        stage: The stage to run.

    Returns:
        True when the stage passed or exited with a tolerated code.
    """
    print(f"\n{YELLOW}> {stage.name}{RESET} - {stage.description}")

    try:
        completed = subprocess.run(  # noqa: S603 - argv comes from the repo's own config
            stage.command,
            cwd=stage.working_directory,
            check=False,
        )
    except FileNotFoundError:
        # A missing executable must fail loudly. A gate whose command
        # does not exist is worse than no gate: it reports success.
        print(f"{RED}  FAIL {stage.name}: command not found: {stage.command[0]}{RESET}")
        return False

    passed = completed.returncode == 0 or completed.returncode in stage.tolerated_exit_codes

    if passed:
        note = f" (exit {completed.returncode} tolerated)" if completed.returncode else ""
        print(f"{GREEN}  PASS {stage.name}{note}{RESET}")
    else:
        print(f"{RED}  FAIL {stage.name} (exit {completed.returncode}){RESET}")

    return passed


def main() -> None:
    """Run the requested stages and exit non-zero on the first failure."""
    parser = argparse.ArgumentParser(
        description="The verification gate - this repo's definition of done.",
    )
    parser.add_argument(
        "--only",
        metavar="STAGE[,STAGE...]",
        help="Run only the named stage(s), comma-separated, in gate order.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the stages and exit without running them.",
    )
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="Run on the current interpreter instead of the repo .venv.",
    )
    parser.add_argument(
        "--allow-placeholders",
        action="store_true",
        help=(
            "Skip (loudly) stages that still hold a {{TOKEN}} placeholder "
            "instead of refusing to run. For the template repo's own CI; "
            "a real project should never need it."
        ),
    )
    arguments = parser.parse_args()

    if not arguments.no_venv and not arguments.list:
        reexec_into_virtualenv()

    stages = load_stages()

    if arguments.list:
        for stage in stages:
            pending = " (placeholder)" if has_unreplaced_placeholder(stage) else ""
            print(f"{stage.name:14} {stage.description}{pending}")
        return

    if arguments.only:
        known = ", ".join(stage.name for stage in stages)
        requested = {name.strip() for name in arguments.only.split(",") if name.strip()}
        unknown = requested - {stage.name for stage in stages}
        if unknown:
            parser.error(
                f"unknown stage(s) {', '.join(sorted(unknown))!r}. "
                f"Known stages: {known}"
            )
        # Filter rather than reorder: the declared order in verify.toml
        # encodes "cheap before expensive" and should hold for subsets.
        stages = [stage for stage in stages if stage.name in requested]

    unfilled = [stage.name for stage in stages if has_unreplaced_placeholder(stage)]
    if unfilled and arguments.allow_placeholders and not is_unmodified_template():
        sys.exit(
            f"{RED}--allow-placeholders is for the unmodified template only."
            f"{RESET}\nAGENTS.md has been customised but verify.toml still "
            f"has placeholder stage(s): {', '.join(unfilled)}.\n"
            f"Fill them in and drop the flag from .github/workflows/ci.yml."
        )
    if unfilled and not arguments.allow_placeholders:
        sys.exit(
            f"{RED}Unreplaced template placeholder in stage(s): "
            f"{', '.join(unfilled)}{RESET}\n"
            f"Edit verify.toml and set this project's real commands. "
            f"A gate that runs a placeholder is a gate that checks nothing."
        )

    print(f"{CYAN}=== verify ==={RESET}")
    print(f"interpreter: {sys.executable}")

    for stage in stages:
        if has_unreplaced_placeholder(stage):
            # Only reachable with --allow-placeholders. Say so on every
            # run, so a project that forgot to fill verify.toml cannot
            # mistake a skipped stage for a passing one.
            print(f"\n{YELLOW}> {stage.name}{RESET} - {stage.description}")
            print(f"{YELLOW}  SKIP {stage.name}: placeholder not yet filled in{RESET}")
            continue
        if not run_stage(stage):
            print(f"\n{RED}Verification FAILED at stage '{stage.name}'.{RESET}")
            sys.exit(1)

    if unfilled:
        print(
            f"\n{YELLOW}All configured checks passed; "
            f"{len(unfilled)} placeholder stage(s) skipped: "
            f"{', '.join(unfilled)}.{RESET}"
        )
        return

    print(f"\n{GREEN}All checks passed.{RESET}")


if __name__ == "__main__":
    main()
