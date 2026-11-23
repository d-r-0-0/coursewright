"""MindTap gradebook backfill (library form of the old cantrip).

One-shot grade rescue.  When a publisher platform's grades never sync
back into Canvas, this reads the publisher's gradebook CSV export, pulls
one student's earned scores, matches each item to the corresponding
Canvas assignment by name, and (optionally) writes the earned grades.

Safe by default (AGENTS.md 3.11): ``apply_grade_changes`` is a dry run
unless ``execute=True`` and saves a timestamped JSON backup first.  It
only fills assignments the student is missing a grade on, unless the
plan was built with ``overwrite=True``.  Library code is silent (3.8):
it returns data and raises exceptions.

The pure helpers (parsing, basis detection, name matching, plan
building) never touch Canvas.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import (
    SafeWriteError,
    default_backup_directory,
    write_backup,
)
from coursewright.core.config.manager import resolve_workspace_directory
from coursewright.core.inbox import InboxCandidate, find_exported_file

# Filename pattern of a publisher gradebook export (Downloads first).
MINDTAP_GRADEBOOK_FILENAME_PATTERN = "MindTap*Gradebook*.csv"

# In a MindTap gradebook export the first five columns are fixed metadata and
# every column after that is one graded item (assignment).
FIRST_ASSIGNMENT_COLUMN = 5

DEFAULT_MATCH_THRESHOLD = 0.82

BACKFILL_COMMENT = "MindTap grade backfill after publisher platform migration."

_MAX_POINTS_PATTERN = re.compile(r"Max\s*Points:\s*([0-9.]+)", re.IGNORECASE)


# =============================================================================
# Data structures (pure, Canvas-free) so the parsing logic is easy to test.
# =============================================================================


@dataclass
class MindTapGradebook:
    """A parsed MindTap gradebook export."""

    assignment_names: list[str]
    max_points: list[float | None]
    # student_name -> {"total": float | None, "scores": list[float | None]}
    students: dict[str, dict] = field(default_factory=dict)


@dataclass
class StudentScores:
    """One student's earned MindTap scores lined up with assignment names."""

    name: str
    reported_total: float | None
    # Per-item earned value, aligned index-for-index with assignment_names.
    scores: list[float | None]


@dataclass
class GradeChange:
    """A single proposed (or applied) grade transfer for one assignment."""

    mindtap_name: str
    mindtap_raw: float
    mindtap_max: float | None
    canvas_assignment_id: int
    canvas_assignment_name: str
    canvas_points_possible: float | None
    match_ratio: float
    new_score: float
    current_score: float | None
    action: str  # one of: FILL, UPDATE, SKIP-HAS-GRADE, SKIP-NO-POINTS


@dataclass
class BackfillApplyResult:
    """What ``apply_grade_changes`` did, or in a dry run would do."""

    executed: bool
    planned: int
    written: int = 0
    failed_assignment_names: list[str] = field(default_factory=list)
    backup_path: Path | None = None


# =============================================================================
# Pure helpers: CSV parsing, basis detection, name matching.
# =============================================================================


def discover_latest_gradebook_csv(
    download_directory: Path | None = None,
    workspace_import_directory: Path | None = None,
) -> InboxCandidate | None:
    """Find the newest MindTap gradebook export, Downloads first.

    Returns a candidate for the interface to show and the instructor to
    confirm; it is never imported unasked (``docs/HOUSE_STYLE.md`` 2).
    """
    return find_exported_file(
        MINDTAP_GRADEBOOK_FILENAME_PATTERN,
        download_directory=download_directory,
        workspace_import_directory=workspace_import_directory,
    )


def _parse_float(cell: str) -> float | None:
    """Parse a numeric CSV cell, treating blanks / junk as None."""
    text = (cell or "").strip().rstrip("%")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_mindtap_gradebook(csv_path: Path) -> MindTapGradebook:
    """Parse a MindTap gradebook CSV into names, max points, and student rows.

    The export layout is:
      row 0            -> header (col 5+ are assignment names)
      a few meta rows  -> ids / "Assessment" / category / "Graded" labels
      one "Max Points" -> col 5+ hold "Max Points: N" for each item
      student rows     -> col 0 is a numeric User ID
    """
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))

    if not rows:
        raise ValueError(f"Gradebook CSV is empty: {csv_path}")

    header = rows[0]
    assignment_names = [name.strip() for name in header[FIRST_ASSIGNMENT_COLUMN:]]

    max_points: list[float | None] = [None] * len(assignment_names)
    students: dict[str, dict] = {}

    for row in rows[1:]:
        if len(row) <= FIRST_ASSIGNMENT_COLUMN:
            continue
        first_cell = (row[0] or "").strip()
        marker_cell = row[FIRST_ASSIGNMENT_COLUMN]

        # The max-points row is the meta row whose item cells say "Max Points".
        if not first_cell and _MAX_POINTS_PATTERN.search(marker_cell or ""):
            for index, cell in enumerate(row[FIRST_ASSIGNMENT_COLUMN:]):
                match = _MAX_POINTS_PATTERN.search(cell or "")
                if match and index < len(max_points):
                    max_points[index] = float(match.group(1))
            continue

        # Student rows begin with a numeric Canvas/MindTap User ID.
        if first_cell.isdigit():
            student_name = (row[1] or "").strip()
            reported_total = _parse_float(row[3]) if len(row) > 3 else None
            scores = [_parse_float(cell) for cell in row[FIRST_ASSIGNMENT_COLUMN:]]
            # Pad/truncate to align with the header width.
            scores = (scores + [None] * len(assignment_names))[: len(assignment_names)]
            students[student_name] = {"total": reported_total, "scores": scores}

    return MindTapGradebook(
        assignment_names=assignment_names,
        max_points=max_points,
        students=students,
    )


def extract_student_scores(
    gradebook: MindTapGradebook, name_substring: str
) -> StudentScores | None:
    """Find the one student whose name contains *name_substring*."""
    needle = name_substring.lower()
    matches = [name for name in gradebook.students if needle in name.lower()]
    # Also try matching on individual tokens so that "Last, First"
    # (MindTap) still matches "First Last" style spellings.
    if not matches:
        tokens = [t for t in re.split(r"[,\s]+", needle) if t]
        matches = [
            name for name in gradebook.students if all(token in name.lower() for token in tokens)
        ]
    if not matches:
        return None

    name = matches[0]
    record = gradebook.students[name]
    return StudentScores(
        name=name,
        reported_total=record["total"],
        scores=record["scores"],
    )


def detect_score_basis(
    scores: list[float | None],
    max_points: list[float | None],
    reported_total: float | None,
) -> str:
    """Decide whether MindTap cells are raw points or percentages.

    MindTap exports usually store earned raw points per item, but some exports
    store percentages.  We disambiguate by reconstructing the student's total
    both ways and seeing which reproduces the "Total Points to Date" column.
    """
    if not reported_total:
        return "points"  # Safe default; scaling later still normalizes.

    sum_as_points = 0.0
    sum_as_percent = 0.0
    for raw, maximum in zip(scores, max_points, strict=False):
        if raw is None:
            continue
        sum_as_points += raw
        if maximum:
            sum_as_percent += (raw / 100.0) * maximum

    points_error = abs(sum_as_points - reported_total)
    percent_error = abs(sum_as_percent - reported_total)
    return "points" if points_error <= percent_error else "percent"


def normalize_assignment_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for robust matching."""
    text = name.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_assignment(
    mindtap_name: str,
    canvas_by_norm: dict[str, object],
    threshold: float,
) -> tuple[object | None, float]:
    """Match a MindTap item to a Canvas assignment; return (assignment, ratio).

    Tries an exact normalized-name match first, then falls back to the best
    fuzzy ratio.  Returns (None, best_ratio) when nothing clears *threshold*.
    """
    norm = normalize_assignment_name(mindtap_name)
    if norm in canvas_by_norm:
        return canvas_by_norm[norm], 1.0

    best_assignment = None
    best_ratio = 0.0
    for candidate_norm, assignment in canvas_by_norm.items():
        ratio = SequenceMatcher(None, norm, candidate_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_assignment = assignment

    if best_ratio >= threshold:
        return best_assignment, best_ratio
    return None, best_ratio


def build_grade_plan(
    student: StudentScores,
    gradebook: MindTapGradebook,
    canvas_assignments: list,
    current_scores_by_assignment: dict[int, float | None],
    basis: str,
    threshold: float,
    overwrite: bool,
) -> tuple[list[GradeChange], list[tuple[str, float]]]:
    """Compute the list of grade changes plus a list of unmatched MindTap items.

    *canvas_assignments* are objects exposing ``.id``, ``.name``, and
    ``.points_possible`` (the project's ``Assignment`` model).  Only items the
    student actually attempted (non-blank score) are considered.
    """
    canvas_by_norm = {normalize_assignment_name(a.name): a for a in canvas_assignments}

    changes: list[GradeChange] = []
    unmatched: list[tuple[str, float]] = []

    for index, mindtap_name in enumerate(gradebook.assignment_names):
        raw = student.scores[index] if index < len(student.scores) else None
        if raw is None:
            continue  # Not attempted in MindTap: leave Canvas untouched.

        maximum = gradebook.max_points[index] if index < len(gradebook.max_points) else None

        assignment, ratio = match_assignment(mindtap_name, canvas_by_norm, threshold)
        if assignment is None:
            unmatched.append((mindtap_name, raw))
            continue

        # Convert the earned MindTap value to a fraction, then scale it to the
        # Canvas assignment's own points_possible so credit transfers cleanly
        # even when the two systems use different point totals.
        if basis == "percent":
            fraction = raw / 100.0
        elif maximum:
            fraction = raw / maximum
        else:
            fraction = raw / 100.0  # Unknown max: assume raw was a percentage.

        points_possible = getattr(assignment, "points_possible", None)
        current = current_scores_by_assignment.get(assignment.id)

        if not points_possible:
            action = "SKIP-NO-POINTS"
            new_score = 0.0
        else:
            new_score = round(fraction * points_possible, 2)
            if current is None:
                action = "FILL"
            elif overwrite:
                action = "UPDATE"
            else:
                action = "SKIP-HAS-GRADE"

        changes.append(
            GradeChange(
                mindtap_name=mindtap_name,
                mindtap_raw=raw,
                mindtap_max=maximum,
                canvas_assignment_id=assignment.id,
                canvas_assignment_name=assignment.name,
                canvas_points_possible=points_possible,
                match_ratio=ratio,
                new_score=new_score,
                current_score=current,
                action=action,
            )
        )

    return changes, unmatched


# =============================================================================
# Canvas I/O: the only functions here that talk to Canvas.
# =============================================================================


def find_canvas_student(canvas_client: CanvasClient, course_id: int, name_substring: str):
    """Locate the Canvas student whose name matches *name_substring*."""
    tokens = [t for t in re.split(r"[,\s]+", name_substring.lower()) if t]
    users = canvas_client.get_course_users(course_id, ["student"])
    for user in users:
        haystack = (user.name or "").lower()
        if all(token in haystack for token in tokens):
            return user
    return None


def load_current_scores(
    canvas_client: CanvasClient, course_id: int, student_id: int
) -> dict[int, float | None]:
    """Return {assignment_id: current_score} for the student (None if ungraded)."""
    submissions = canvas_client.get_student_submissions(course_id, student_id)
    current: dict[int, float | None] = {}
    for submission in submissions:
        assignment_id = submission.get("assignment_id")
        if assignment_id is not None:
            current[assignment_id] = submission.get("score")
    return current


def apply_grade_changes(
    canvas_client: CanvasClient,
    course_id: int,
    student_id: int,
    changes: list[GradeChange],
    *,
    execute: bool = False,
    backup_directory: Path | None = None,
) -> BackfillApplyResult:
    """Write eligible grade changes to Canvas (dry run unless ``execute``).

    Only ``FILL`` and ``UPDATE`` changes are eligible.  With
    ``execute=True`` the eligible changes are saved as a timestamped JSON
    backup first; if that fails nothing is written.  Individual Canvas
    refusals are reported in ``failed_assignment_names``, never hidden.

    Raises:
        BackupFailedError: the backup could not be written.
        SafeWriteError: executing with no workspace for the backup.
    """
    writable = [c for c in changes if c.action in ("FILL", "UPDATE")]
    if not execute:
        return BackfillApplyResult(executed=False, planned=len(writable))

    if backup_directory is None:
        workspace_directory = resolve_workspace_directory()
        if workspace_directory is None:
            raise SafeWriteError(
                "No private workspace is configured; set COURSEWRIGHT_WORKSPACE "
                "so grade backups have somewhere to go."
            )
        backup_directory = default_backup_directory(workspace_directory)
    backup_path = write_backup(
        backup_directory,
        "mindtap-backfill",
        f"course {course_id} user {student_id}",
        [vars(change) for change in writable],
        None,
    )

    result = BackfillApplyResult(executed=True, planned=len(writable), backup_path=backup_path)
    for change in writable:
        written_ok = canvas_client.submit_grade(
            course_id=course_id,
            assignment_id=change.canvas_assignment_id,
            user_id=student_id,
            score=change.new_score,
            comment=BACKFILL_COMMENT,
        )
        if written_ok:
            result.written += 1
        else:
            result.failed_assignment_names.append(change.canvas_assignment_name)
    return result
