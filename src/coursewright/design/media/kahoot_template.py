"""Shared machinery for writing and validating Kahoot question banks.

Ported from COURSE_BUILDER ``lib/kahoot_template.py`` and
``cantrips/check_kahoot_csv.py``.

Why this exists:
    Every unit's Kahoot is the same job with a different list of questions.
    Centralizing validation, row clearing, summary reporting, and CSV checks
    ensures length limits and time limits are enforced identically every time.

Kahoot's rules, which this enforces:
    - A question is at most 120 characters, an answer at most 75.
    - The time limit must be one Kahoot offers (5/10/20/30/60/90/120/240s).
    - Template bank writing requires four answers, with the correct one
      identified by slot (1-4).
    - CSV import validation requires at least two filled answers and valid
      correct-answer slot pointers.

Library code in ``design/media`` is silent (AGENTS.md 3.8): it returns
typed results and raises exceptions instead of printing.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from pydantic import BaseModel, Field

# The official template reserves rows 9-108 for questions, pre-numbered
# 1-100 in column A, and carries the conditional formatting and font that
# a rebuilt sheet would lose. So the bank is written into those rows.
FIRST_QUESTION_ROW = 9
LAST_QUESTION_ROW = 108

QUESTION_COLUMN = 2
FIRST_ANSWER_COLUMN = 3
ANSWER_COUNT = 4
TIME_LIMIT_COLUMN = 7
CORRECT_SLOT_COLUMN = 8

QUESTION_MAX_CHARS = 120
ANSWER_MAX_CHARS = 75
ALLOWED_TIME_LIMITS = frozenset({5, 10, 20, 30, 60, 90, 120, 240})

# Constants used by CSV import validation (from check_kahoot_csv.py).
MAX_QUESTION = QUESTION_MAX_CHARS
MAX_ANSWER = ANSWER_MAX_CHARS
ALLOWED_TIMES = frozenset(str(limit) for limit in ALLOWED_TIME_LIMITS)
ANSWER_COLUMNS: tuple[str, ...] = ("Answer 1", "Answer 2", "Answer 3", "Answer 4")


@dataclass(frozen=True)
class QuizQuestion:
    """One Kahoot question.

    ``correct_slot`` is 1-4, matching the answer's position, because that is
    what column H of the template expects and what Kahoot imports.
    """

    question: str
    answers: tuple[str, ...]
    time_limit_seconds: int
    correct_slot: int


class KahootCsvRowFailure(BaseModel):
    """Rule violations found in a single Kahoot CSV row."""

    row_number: int
    question_preview: str
    problems: list[str] = Field(default_factory=list)


class KahootCsvValidationResult(BaseModel):
    """Summary of validating a Kahoot CSV file for import."""

    path: Path
    question_count: int
    longest_question: int = 0
    longest_answer: int = 0
    row_failures: list[KahootCsvRowFailure] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Return True when the CSV has rows and zero rule violations."""
        return self.question_count > 0 and not self.row_failures


def validate_bank(bank: Sequence[QuizQuestion]) -> None:
    """Enforce every Kahoot limit, naming the offending question.

    Raising here rather than writing a file that Kahoot will reject means
    the failure arrives while the bank is still easy to edit.
    """
    if not bank:
        raise ValueError("The question bank is empty")

    capacity = LAST_QUESTION_ROW - FIRST_QUESTION_ROW + 1
    if len(bank) > capacity:
        raise ValueError(f"{len(bank)} questions exceeds the template's {capacity}")

    for index, item in enumerate(bank, start=1):
        if len(item.question) > QUESTION_MAX_CHARS:
            raise ValueError(
                f"Q{index}: question is {len(item.question)} chars, "
                f"limit {QUESTION_MAX_CHARS}"
            )
        if len(item.answers) != ANSWER_COUNT:
            raise ValueError(f"Q{index}: needs exactly {ANSWER_COUNT} answers")
        for answer in item.answers:
            if len(answer) > ANSWER_MAX_CHARS:
                raise ValueError(
                    f"Q{index}: answer is {len(answer)} chars, "
                    f"limit {ANSWER_MAX_CHARS}: {answer!r}"
                )
            # Excel reads a leading = as the start of a formula, so an
            # answer like "==" would be stored as a broken formula.
            if answer.startswith("="):
                raise ValueError(f"Q{index}: answer starts with '=': {answer!r}")
        if item.time_limit_seconds not in ALLOWED_TIME_LIMITS:
            raise ValueError(
                f"Q{index}: time limit {item.time_limit_seconds} is not one "
                f"Kahoot offers {sorted(ALLOWED_TIME_LIMITS)}"
            )
        if item.correct_slot not in range(1, ANSWER_COUNT + 1):
            raise ValueError(
                f"Q{index}: correct slot {item.correct_slot} is not 1-{ANSWER_COUNT}"
            )


def describe_bank(bank: Sequence[QuizQuestion]) -> str:
    """Report the things worth checking before handing the file over.

    The slot spread matters because students notice when the answer is
    usually in the same position, and the game length matters because a
    bank that runs longer than the class opener is a problem to know
    about now rather than in front of the room.
    """
    spread = {
        slot: sum(1 for item in bank if item.correct_slot == slot)
        for slot in range(1, ANSWER_COUNT + 1)
    }
    longest_question = max(len(item.question) for item in bank)
    longest_answer = max(len(answer) for item in bank for answer in item.answers)
    total_seconds = sum(item.time_limit_seconds for item in bank)

    # Kahoot spends roughly as long showing each answer as asking it, so
    # wall-clock game time runs about double the summed timers.
    low_minutes = total_seconds / 60
    high_minutes = total_seconds * 2 / 60

    return (
        f"{len(bank)} questions\n"
        f"correct-answer slots: "
        + "  ".join(f"{slot}:{count}" for slot, count in spread.items())
        + "\n"
        f"longest question: {longest_question}/{QUESTION_MAX_CHARS} chars\n"
        f"longest answer:   {longest_answer}/{ANSWER_MAX_CHARS} chars\n"
        f"game length:      roughly {low_minutes:.0f}-{high_minutes:.0f} minutes"
    )


def write_bank(
    bank: Sequence[QuizQuestion], template_path: Path, output_path: Path
) -> None:
    """Fill the template's question rows and save the result as a copy.

    The template itself is never modified. Existing question rows are
    cleared first so the template's own example questions cannot survive
    into a finished bank, while column A's numbering and the sheet's
    conditional formatting stay untouched.
    """
    validate_bank(bank)

    if not template_path.exists():
        raise FileNotFoundError(f"Kahoot template not found: {template_path}")

    workbook = load_workbook(template_path)
    sheet = workbook.active

    for row in range(FIRST_QUESTION_ROW, LAST_QUESTION_ROW + 1):
        for column in range(QUESTION_COLUMN, CORRECT_SLOT_COLUMN + 1):
            sheet.cell(row=row, column=column).value = None

    for offset, item in enumerate(bank):
        row = FIRST_QUESTION_ROW + offset
        sheet.cell(row=row, column=QUESTION_COLUMN).value = item.question
        for slot, answer in enumerate(item.answers):
            cell = sheet.cell(row=row, column=FIRST_ANSWER_COLUMN + slot)
            cell.value = answer
            # Force text, or Excel turns "11:59 PM" into a time value and
            # trims a float like 0.30000000000000004 -- and Kahoot then
            # imports Excel's interpretation instead of the answer.
            cell.number_format = "@"
        sheet.cell(row=row, column=TIME_LIMIT_COLUMN).value = item.time_limit_seconds
        sheet.cell(row=row, column=CORRECT_SLOT_COLUMN).value = item.correct_slot

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def check_row(row: Mapping[str, str | None]) -> list[str]:
    """Return every Kahoot CSV rule violation found in one row."""
    problems: list[str] = []

    question = (row.get("Question") or "").strip()
    if not question:
        problems.append("question is empty")
    elif len(question) > MAX_QUESTION:
        problems.append(f"question is {len(question)} chars (max {MAX_QUESTION})")

    answers = [(name, (row.get(name) or "").strip()) for name in ANSWER_COLUMNS]
    filled = [name for name, text in answers if text]
    if len(filled) < 2:
        problems.append("fewer than two answers")

    for name, text in answers:
        if len(text) > MAX_ANSWER:
            problems.append(f"{name} is {len(text)} chars (max {MAX_ANSWER})")

    time_limit = (row.get("Time limit") or "").strip()
    if time_limit not in ALLOWED_TIMES:
        problems.append(
            f"time limit {time_limit!r} is not one of "
            f"{sorted(ALLOWED_TIMES, key=int)}"
        )

    correct = (row.get("Correct answer(s)") or "").strip()
    if not correct:
        problems.append("no correct answer marked")
    else:
        for choice in correct.split(","):
            choice = choice.strip()
            if choice not in {"1", "2", "3", "4"}:
                problems.append(f"correct answer {choice!r} is not 1-4")
            elif f"Answer {choice}" not in filled:
                problems.append(f"correct answer {choice} points at a blank")

    return problems


def validate_kahoot_csv(csv_path: Path) -> KahootCsvValidationResult:
    """Validate every row of a Kahoot CSV file without printing."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"Kahoot CSV not found: {csv_path}")

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"{csv_path.name} has no question rows")

    failures: list[KahootCsvRowFailure] = []
    for number, row in enumerate(rows, start=1):
        problems = check_row(row)
        if problems:
            failures.append(
                KahootCsvRowFailure(
                    row_number=number,
                    question_preview=(row.get("Question") or "")[:52],
                    problems=problems,
                )
            )

    longest_question = max(len(r.get("Question") or "") for r in rows)
    longest_answer = max(
        len(r.get(name) or "") for r in rows for name in ANSWER_COLUMNS
    )
    return KahootCsvValidationResult(
        path=csv_path,
        question_count=len(rows),
        longest_question=longest_question,
        longest_answer=longest_answer,
        row_failures=failures,
    )
