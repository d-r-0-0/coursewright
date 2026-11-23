"""Tests for Excel-to-CSV conversion and worksheet routing (openpyxl)."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from coursewright.design.conversion.spreadsheets import (
    WorksheetNameCollisionError,
    convert_workbook_to_csv,
    find_workbooks,
    workbook_csv_paths,
)


def build_workbook(path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    workbook = openpyxl.Workbook()
    default_sheet = workbook.active
    assert default_sheet is not None
    for index, (sheet_name, rows) in enumerate(sheets.items()):
        sheet = default_sheet if index == 0 else workbook.create_sheet(title=sheet_name)
        sheet.title = sheet_name
        for row in rows:
            sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def test_workbook_paths_preserve_single_and_multiple_sheet_names(tmp_path: Path) -> None:
    base_csv = tmp_path / "quiz.csv"
    single_paths = workbook_csv_paths(base_csv, ["Sheet1"])
    multi_paths = workbook_csv_paths(base_csv, ["Questions", "Key"])

    assert [path.name for path in single_paths] == ["quiz.csv"]
    assert [path.name for path in multi_paths] == ["quiz--questions.csv", "quiz--key.csv"]


def test_workbook_paths_reject_slug_collisions_before_conversion(tmp_path: Path) -> None:
    base_csv = tmp_path / "quiz.csv"
    with pytest.raises(
        WorksheetNameCollisionError,
        match=r"Worksheet names map to the same CSV path: quiz--answer-key\.csv",
    ):
        workbook_csv_paths(base_csv, ["Answer Key", "Answer-Key"])


def test_legacy_csv_does_not_bypass_workbook_inspection(tmp_path: Path) -> None:
    workbook_path = build_workbook(
        tmp_path / "quiz.xlsx",
        {
            "Questions": [["Prompt", "Points"], ["What is TCP?", 5]],
            "Answer Key": [["Question", "Answer"], [1, "Transmission Control Protocol"]],
        },
    )
    legacy_csv = tmp_path / "quiz.csv"
    legacy_csv.write_text("legacy\n", encoding="utf-8")

    outcomes = convert_workbook_to_csv(workbook_path)
    summary = [(item.sheet_name, item.output_path.name, item.written) for item in outcomes]

    assert summary == [
        ("Questions", "quiz--questions.csv", True),
        ("Answer Key", "quiz--answer-key.csv", True),
    ]
    assert legacy_csv.read_text(encoding="utf-8") == "legacy\n"
    assert "What is TCP?,5" in (tmp_path / "quiz--questions.csv").read_text(encoding="utf-8-sig")


def test_existing_sheet_output_is_not_overwritten(tmp_path: Path) -> None:
    workbook_path = build_workbook(
        tmp_path / "quiz.xlsx",
        {
            "Questions": [["Q"], ["New question"]],
            "Key": [["A"], ["42"]],
        },
    )
    curated = tmp_path / "quiz--questions.csv"
    curated.write_text("hand-curated\n", encoding="utf-8")

    outcomes = convert_workbook_to_csv(workbook_path)

    assert [(outcome.output_path.name, outcome.written) for outcome in outcomes] == [
        ("quiz--questions.csv", False),
        ("quiz--key.csv", True),
    ]
    assert curated.read_text(encoding="utf-8") == "hand-curated\n"
    assert (tmp_path / "quiz--key.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_find_workbooks_skips_lock_files(tmp_path: Path) -> None:
    build_workbook(tmp_path / "unit1.xlsx", {"Sheet1": [["A", None]]})
    (tmp_path / "~$unit1.xlsx").write_bytes(b"lock")

    assert [path.name for path in find_workbooks(tmp_path)] == ["unit1.xlsx"]
