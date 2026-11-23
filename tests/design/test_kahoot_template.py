"""Tests for the shared Kahoot template and CSV validation machinery.

Ported from COURSE_BUILDER ``tests/test_kahoot_template.py``, plus tests for
the CSV validation rules ported from ``cantrips/check_kahoot_csv.py``.

These guard the limits Kahoot enforces silently at import time. A bank that
violates one of them produces a file that looks fine locally and then
imports wrong, so the validation is the part worth pinning down.
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook, load_workbook

from coursewright.design.media.kahoot_template import (
    ANSWER_MAX_CHARS,
    CORRECT_SLOT_COLUMN,
    FIRST_QUESTION_ROW,
    QUESTION_MAX_CHARS,
    QuizQuestion,
    check_row,
    describe_bank,
    validate_bank,
    validate_kahoot_csv,
    write_bank,
)


def make_question(**overrides):
    """Build a valid question, with fields replaced as needed."""
    defaults = {
        "question": "What does 4 > 4 evaluate to?",
        "answers": ("True", "False", "4", "TypeError"),
        "time_limit_seconds": 20,
        "correct_slot": 2,
    }
    defaults.update(overrides)
    return QuizQuestion(**defaults)


def make_template(path):
    """Write a stand-in for the official template's question grid."""
    workbook = Workbook()
    sheet = workbook.active
    sheet["B8"] = "Question - max 120 characters"
    for offset in range(100):
        sheet.cell(row=FIRST_QUESTION_ROW + offset, column=1).value = offset + 1
    # An example question the real template ships with, which a built
    # bank must not inherit.
    sheet["B9"] = "Example question from the template"
    sheet["C9"] = "Example answer"
    workbook.save(path)
    return path


def test_validate_accepts_a_good_bank():
    validate_bank([make_question()])


def test_validate_rejects_empty_bank():
    with pytest.raises(ValueError, match="empty"):
        validate_bank([])


def test_validate_rejects_overlong_question():
    with pytest.raises(ValueError, match="limit 120"):
        validate_bank([make_question(question="x" * (QUESTION_MAX_CHARS + 1))])


def test_validate_rejects_overlong_answer():
    long_answer = "x" * (ANSWER_MAX_CHARS + 1)
    with pytest.raises(ValueError, match="limit 75"):
        validate_bank([make_question(answers=(long_answer, "b", "c", "d"))])


def test_validate_rejects_wrong_answer_count():
    with pytest.raises(ValueError, match="exactly 4 answers"):
        validate_bank([make_question(answers=("a", "b", "c"))])


def test_validate_rejects_answer_excel_would_read_as_formula():
    with pytest.raises(ValueError, match="starts with"):
        validate_bank([make_question(answers=("==", "b", "c", "="))])


def test_validate_rejects_time_limit_kahoot_does_not_offer():
    with pytest.raises(ValueError, match="not one Kahoot offers"):
        validate_bank([make_question(time_limit_seconds=45)])


@pytest.mark.parametrize("slot", [0, 5, -1])
def test_validate_rejects_out_of_range_correct_slot(slot):
    with pytest.raises(ValueError, match="not 1-4"):
        validate_bank([make_question(correct_slot=slot)])


def test_validate_rejects_bank_larger_than_template():
    with pytest.raises(ValueError, match="exceeds the template"):
        validate_bank([make_question()] * 101)


def test_write_bank_fills_rows_and_keeps_numbering(tmp_path):
    template = make_template(tmp_path / "template.xlsx")
    output = tmp_path / "built.xlsx"
    bank = [
        make_question(question="First?", correct_slot=1),
        make_question(question="Second?", correct_slot=4),
    ]

    write_bank(bank, template, output)

    sheet = load_workbook(output).active
    assert sheet["B9"].value == "First?"
    assert sheet["B10"].value == "Second?"
    assert sheet["H9"].value == 1
    assert sheet["H10"].value == 4
    # Column A's pre-numbering is the template's, and must survive.
    assert sheet["A9"].value == 1
    assert sheet["A10"].value == 2


def test_write_bank_clears_the_templates_example_question(tmp_path):
    template = make_template(tmp_path / "template.xlsx")
    output = tmp_path / "built.xlsx"

    write_bank([make_question(question="Only question?")], template, output)

    sheet = load_workbook(output).active
    assert sheet["B9"].value == "Only question?"
    # Row 10 held nothing of ours, so the template's leftovers must be gone.
    assert sheet["B10"].value is None
    assert sheet["C10"].value is None


def test_write_bank_stores_answers_as_text(tmp_path):
    """Numeric-looking answers must stay text or Excel reformats them."""
    template = make_template(tmp_path / "template.xlsx")
    output = tmp_path / "built.xlsx"
    bank = [make_question(answers=("11:59 PM", "0.30000000000000004", "c", "d"))]

    write_bank(bank, template, output)

    sheet = load_workbook(output).active
    assert sheet["C9"].number_format == "@"
    assert sheet["C9"].value == "11:59 PM"
    assert sheet["D9"].value == "0.30000000000000004"


def test_write_bank_leaves_the_template_untouched(tmp_path):
    template = make_template(tmp_path / "template.xlsx")
    before = template.read_bytes()

    write_bank([make_question()], template, tmp_path / "built.xlsx")

    assert template.read_bytes() == before


def test_write_bank_refuses_a_missing_template(tmp_path):
    with pytest.raises(FileNotFoundError, match="template not found"):
        write_bank([make_question()], tmp_path / "absent.xlsx", tmp_path / "o.xlsx")


def test_write_bank_validates_before_writing(tmp_path):
    """A bad bank must not leave a half-written file behind."""
    template = make_template(tmp_path / "template.xlsx")
    output = tmp_path / "built.xlsx"

    with pytest.raises(ValueError):
        write_bank([make_question(correct_slot=9)], template, output)

    assert not output.exists()


def test_describe_bank_reports_slot_spread_and_length():
    bank = [
        make_question(correct_slot=1, time_limit_seconds=30),
        make_question(correct_slot=1, time_limit_seconds=30),
        make_question(correct_slot=3, time_limit_seconds=60),
    ]

    report = describe_bank(bank)

    assert "3 questions" in report
    assert "1:2" in report
    assert "3:1" in report
    assert "minutes" in report


def test_correct_slot_column_is_column_h():
    """Kahoot reads the correct answer from column H; pin that down."""
    assert CORRECT_SLOT_COLUMN == 8


def test_check_row_and_validate_kahoot_csv(tmp_path):
    """CSV validation catches malformed rows and accepts valid ones."""
    valid_row = {
        "Question": "What is 2 + 2?",
        "Answer 1": "3",
        "Answer 2": "4",
        "Answer 3": "",
        "Answer 4": "",
        "Time limit": "20",
        "Correct answer(s)": "2",
    }
    assert check_row(valid_row) == []

    bad_row = {
        "Question": "",
        "Answer 1": "only one",
        "Answer 2": "",
        "Answer 3": "",
        "Answer 4": "x" * 76,
        "Time limit": "45",
        "Correct answer(s)": "3, 5",
    }
    problems = check_row(bad_row)
    assert "question is empty" in problems
    assert "Answer 4 is 76 chars (max 75)" in problems
    assert any("time limit '45'" in p for p in problems)
    assert "correct answer 3 points at a blank" in problems
    assert "correct answer '5' is not 1-4" in problems

    csv_file = tmp_path / "kahoot.csv"
    csv_file.write_text(
        "Question,Answer 1,Answer 2,Answer 3,Answer 4,Time limit,Correct answer(s)\n"
        "What is 2 + 2?,3,4,5,6,20,2\n",
        encoding="utf-8",
    )
    result = validate_kahoot_csv(csv_file)
    assert result.is_valid
    assert result.question_count == 1
