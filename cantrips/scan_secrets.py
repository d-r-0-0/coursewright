#!/usr/bin/env python3
"""
CANTRIP: scan-secrets

PURPOSE:
    Fail the gate when a file that is, or is about to be, committed
    contains a credential that nobody has reviewed.  Wraps Yelp's
    detect-secrets and compares a fresh scan against the reviewed
    `.secrets.baseline` at the repo root.

    Why not just `detect-secrets scan --baseline .secrets.baseline`?
    That command *re-catalogs* the repo: it rewrites the baseline with
    whatever it finds and exits 0, so a new secret would be silently
    accepted.  This cantrip runs that same command against a temporary
    copy (so the baseline's plugin settings still apply), then fails
    closed on:
      * any finding not in the baseline (a new secret),
      * any finding whose baseline entry is not audited as a false
        positive (`"is_secret": false`),
      * any tool failure (missing package, non-zero exit, bad output).

    Files scanned: tracked files plus untracked files that .gitignore
    does not ignore (`git ls-files --cached --others
    --exclude-standard`) -- i.e. everything `git add -A` could commit.
    Ignored paths (.venv/, .worktrees/, .env, *.key ...) are not scanned;
    neither are the baseline itself and eval/results.jsonl (generated
    eval output).

FALSE POSITIVES:
    1. python cantrips/scan_secrets.py --update-baseline
       (re-catalogs findings into .secrets.baseline, keeping existing
       review labels; never run by the gate)
    2. python -m detect_secrets audit .secrets.baseline
       (interactive; mark each finding real or false positive)
    3. Commit the baseline.  A reviewer checks every new entry.
    Or mark one line inline with a `pragma: allowlist secret` comment.
    A real secret is never baselined: remove it and rotate it.

USAGE:
    python cantrips/scan_secrets.py                    # the gate check
    python cantrips/scan_secrets.py --update-baseline  # after review

DEPENDENCIES:
    - Python 3.11+, git
    - detect-secrets >= 1.5 (pip install -e ".[dev]")

FUNCTIONS (quick map):
    - list_candidate_files: Files git could commit, minus deleted ones
    - run_detect_secrets: Scan files into a baseline-format dict
    - scan_in_batches: Split long file lists under the argv limit
    - finding_keys: Flatten results to (path, type, hash) -> is_secret
    - find_unreviewed_findings: Findings not audited as false positives
    - update_baseline: Write a fresh catalog, carrying review labels
    - main: Orchestrates the flow and reports the result
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINE_PATH = _REPO_ROOT / ".secrets.baseline"

# Matched against repo-relative paths.  detect-secrets reports paths
# with the OS separator, hence [\\/] rather than a literal "/".
_EXCLUDED_FILE_PATTERNS = (
    r"^\.secrets\.baseline$",
    r"^\.venv[\\/]",
    r"^\.worktrees[\\/]",
    r"^eval[\\/]results\.jsonl$",
    r"(?i)\.(pptx|ppt|pdf|docx|doc|xlsx|xls|zip|png|jpe?g|gif|mp4)$",
)

# --no-verify: never call a provider's API to check a candidate.  The
# gate stays offline and deterministic, and never sends a secret out.
_SCAN_OPTIONS = ["--no-verify"] + [
    argument
    for pattern in _EXCLUDED_FILE_PATTERNS
    for argument in ("--exclude-files", pattern)
]

# detect-secrets defaults to one worker per CPU.  On Windows each
# worker is a fresh interpreter (spawn), so past a few workers startup
# costs more than it saves.
_WORKER_PROCESSES = 4

# Leaves headroom under Windows' 32,767-character command-line limit
# for the interpreter path and options.
_MAX_BATCH_ARGUMENT_CHARACTERS = 24_000

FindingKey = tuple[str, str, str]


def display_cantrip_header(cantrip_name: str) -> None:
    """Print a header announcing which cantrip is running."""
    border = "=" * 40
    print(f"\n{border}")
    print(f"  Cantrip: {cantrip_name}")
    print(f"{border}\n")


def fail(message: str) -> NoReturn:
    """Print a failure message and exit non-zero (fail closed)."""
    print(f"SCAN FAILED - {message}")
    sys.exit(1)


def list_candidate_files() -> list[str]:
    """Return repo-relative paths of every file `git add -A` could add.

    Untracked-but-not-ignored files are included so a credential is
    caught before its first commit, not after.  Paths that are not
    regular files (deleted tracked files, nested worktrees, which git
    lists as directories) are dropped.
    """
    command = [
        "git",
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        fail("git is not installed or not on PATH.")
    if result.returncode != 0:
        stderr_text = result.stderr.decode(errors="replace").strip()
        fail(f"git ls-files exited {result.returncode}: {stderr_text}")
    paths = result.stdout.decode("utf-8").split("\0")
    unique_paths = sorted({path for path in paths if path})
    return [
        path
        for path in unique_paths
        if not path.replace("\\", "/").startswith(".worktrees/")
        and (_REPO_ROOT / path).is_file()
    ]


def run_detect_secrets(files: list[str], baseline: Path | None) -> dict:
    """Scan ``files`` and return the result in baseline format.

    With ``baseline``, runs ``detect_secrets scan --baseline`` on a
    temporary copy, so the baseline's plugin settings apply while the
    committed file is never touched.  Without one, uses defaults.
    """
    command = [
        sys.executable,
        "-m",
        "detect_secrets",
        "--cores",
        str(_WORKER_PROCESSES),
        "scan",
        *_SCAN_OPTIONS,
    ]
    with tempfile.TemporaryDirectory() as scratch_directory:
        scratch_baseline = Path(scratch_directory) / "candidate.baseline"
        if baseline is not None:
            shutil.copyfile(baseline, scratch_baseline)
            command += ["--baseline", str(scratch_baseline)]
        # PYTHONUTF8=1: on Windows detect-secrets otherwise opens files as
        # cp1252 and silently SKIPS any UTF-8 file it cannot decode, so
        # the gate passed locally on files it never read while Linux CI
        # flagged them.
        result = subprocess.run(
            [*command, *files],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        if result.returncode != 0:
            fail(
                f"detect-secrets exited {result.returncode}:\n"
                f"{result.stderr.strip()}"
            )
        output = (
            scratch_baseline.read_text(encoding="utf-8")
            if baseline is not None
            else result.stdout
        )
    try:
        return json.loads(output)
    except json.JSONDecodeError as err:
        fail(f"detect-secrets produced unreadable output: {err}")


def scan_in_batches(files: list[str], baseline: Path | None) -> dict:
    """Scan ``files`` in argv-sized batches and merge the results.

    Windows caps a command line at 32,767 characters; a derived repo
    with a few thousand files would exceed it in one call.  Batches
    cover disjoint files, so their results merge by plain update.
    """
    batches: list[list[str]] = [[]]
    batch_length = 0
    for path in files:
        if batch_length + len(path) > _MAX_BATCH_ARGUMENT_CHARACTERS:
            batches.append([])
            batch_length = 0
        batches[-1].append(path)
        batch_length += len(path) + 1
    merged = run_detect_secrets(batches[0], baseline)
    for batch in batches[1:]:
        merged["results"].update(run_detect_secrets(batch, baseline)["results"])
    return merged


def finding_keys(scan: dict) -> dict[FindingKey, dict]:
    """Map each finding to its entry, keyed by (posix path, type, hash).

    Paths are normalized to POSIX so a baseline written on Linux CI
    matches a scan run on Windows.
    """
    keyed: dict[FindingKey, dict] = {}
    for filename, entries in scan.get("results", {}).items():
        posix_filename = filename.replace("\\", "/")
        for entry in entries:
            key = (posix_filename, entry["type"], entry["hashed_secret"])
            keyed[key] = {**entry, "filename": posix_filename}
    return keyed


def find_unreviewed_findings(candidate: dict, baseline: dict) -> list[str]:
    """Describe every current finding not audited as a false positive."""
    reviewed = finding_keys(baseline)
    problems = []
    for key, entry in sorted(finding_keys(candidate).items()):
        filename, secret_type, _ = key
        where = f"{filename}:{entry.get('line_number', '?')} ({secret_type})"
        if key not in reviewed:
            problems.append(f"NEW        {where}")
        elif reviewed[key].get("is_secret") is not False:
            problems.append(f"UNAUDITED  {where}")
    return problems


def update_baseline(candidate: dict, previous: dict) -> None:
    """Write ``candidate`` as the baseline, keeping existing review labels."""
    reviewed = finding_keys(previous)
    results: dict[str, list[dict]] = {}
    for key, entry in sorted(finding_keys(candidate).items()):
        entry.pop("is_secret", None)
        if key in reviewed and "is_secret" in reviewed[key]:
            entry["is_secret"] = reviewed[key]["is_secret"]
        filename = entry.pop("filename")
        results.setdefault(filename, []).append(entry)
    candidate["results"] = results
    for filter_entry in candidate.get("filters_used", []):
        if (
            filter_entry.get("path")
            == "detect_secrets.filters.common.is_baseline_file"
        ):
            filter_entry["filename"] = ".secrets.baseline"
    _BASELINE_PATH.write_text(
        json.dumps(candidate, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(finding_keys(candidate))} finding(s) to "
        f"{_BASELINE_PATH.name}.  Review new entries with:\n"
        f"  python -m detect_secrets audit .secrets.baseline"
    )


def main() -> None:
    """Entry point - scan, compare against the baseline, report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Re-catalog findings into .secrets.baseline (review after).",
    )
    arguments = parser.parse_args()
    display_cantrip_header("scan-secrets")

    if importlib.util.find_spec("detect_secrets") is None:
        fail(
            "detect-secrets is not installed for this interpreter.\n"
            f"       interpreter: {sys.executable}\n"
            '       pip install -e ".[dev]"'
        )

    has_baseline = _BASELINE_PATH.is_file()
    if not has_baseline and not arguments.update_baseline:
        fail(
            f"{_BASELINE_PATH.name} not found.  Create it with:\n"
            "  python cantrips/scan_secrets.py --update-baseline"
        )

    files = list_candidate_files()
    print(f"Scanning {len(files)} files with detect-secrets...", flush=True)
    started = time.monotonic()
    candidate = scan_in_batches(
        files,
        _BASELINE_PATH if has_baseline else None,
    )
    print(f"Scanned in {time.monotonic() - started:.1f}s.\n")

    previous = (
        json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
        if has_baseline
        else {}
    )
    if arguments.update_baseline:
        update_baseline(candidate, previous)
        return

    problems = find_unreviewed_findings(candidate, previous)
    if problems:
        print("\n".join(problems))
        print(
            "\nSCAN FAILED - unreviewed secret(s).  Remove and rotate a "
            "real one;\nfor a false positive see FALSE POSITIVES in "
            "cantrips/scan_secrets.py."
        )
        sys.exit(1)
    print("Scan clean - no unreviewed secrets.")


if __name__ == "__main__":
    main()
