"""Tests for the read-only conversion validator.

Office files are faked as minimal zips holding only the parts the
validator reads (xl/workbook.xml, ppt/slides/slideN.xml), so no binary
fixtures are committed.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from coursewright.design.conversion.validation import (
    build_validation_report,
    expected_output_paths,
    sheet_slug,
    validate_materials_folder,
)


def write_fake_workbook(path: Path, sheet_names: list[str]) -> Path:
    sheets = "".join(
        f'<sheet name="{name}" sheetId="{index}"/>' for index, name in enumerate(sheet_names, 1)
    )
    with zipfile.ZipFile(path, "w") as workbook_zip:
        workbook_zip.writestr("xl/workbook.xml", f"<workbook><sheets>{sheets}</sheets></workbook>")
    return path


def write_fake_deck(path: Path, slide_count: int) -> Path:
    with zipfile.ZipFile(path, "w") as deck_zip:
        for slide_number in range(1, slide_count + 1):
            deck_zip.writestr(f"ppt/slides/slide{slide_number}.xml", "<p:sld/>")
        deck_zip.writestr("ppt/slides/_rels/slide1.xml.rels", "<r/>")
    return path


def problem_kinds(folder: Path) -> list[tuple[str, str]]:
    _, problems = validate_materials_folder(folder)
    return sorted((problem.kind, problem.path.name) for problem in problems)


def test_sheet_slug_matches_powershell_rule() -> None:
    assert sheet_slug("Answer Key") == "answer-key"
    assert sheet_slug("  Week 16 Quiz! ") == "week-16-quiz"
    assert sheet_slug("???") == "sheet"


def test_empty_inventory_does_not_report_success(tmp_path: Path) -> None:
    report = build_validation_report(tmp_path)
    assert report.exit_code == 1
    assert any("No Office sources found" in line for line in report.summary_lines())


def test_missing_materials_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        build_validation_report(tmp_path / "does-not-exist")


def test_multi_sheet_workbook_expects_one_csv_per_sheet(tmp_path: Path) -> None:
    workbook = write_fake_workbook(tmp_path / "quiz.xlsx", ["Questions", "Key"])

    assert [path.name for path in expected_output_paths(workbook)] == [
        "quiz--questions.csv",
        "quiz--key.csv",
    ]


def test_single_sheet_workbook_keeps_historical_name(tmp_path: Path) -> None:
    workbook = write_fake_workbook(tmp_path / "quiz.xlsx", ["Sheet1"])

    assert [path.name for path in expected_output_paths(workbook)] == ["quiz.csv"]


def test_sheet_slug_collision_is_reported(tmp_path: Path) -> None:
    write_fake_workbook(tmp_path / "quiz.xlsx", ["Answer Key", "Answer-Key"])
    (tmp_path / "quiz--answer-key.csv").write_text("Q,A\n", encoding="utf-8")

    assert problem_kinds(tmp_path) == [("COLLISION", "quiz--answer-key.csv")]


def test_sheet_names_decode_xml_entities(tmp_path: Path) -> None:
    workbook = write_fake_workbook(tmp_path / "quiz.xlsx", ["Q &amp; A", "Key"])

    assert [path.name for path in expected_output_paths(workbook)] == [
        "quiz--q-a.csv",
        "quiz--key.csv",
    ]


def test_valid_conversions_pass(tmp_path: Path) -> None:
    write_fake_workbook(tmp_path / "quiz.xlsx", ["Sheet1"])
    (tmp_path / "quiz.csv").write_bytes("\ufeffQ,A\r\n\u201cx\u201d,y\r\n".encode())
    write_fake_deck(tmp_path / "deck.pptx", 2)
    (tmp_path / "deck.md").write_text(
        "# deck\n**Slides:** 2\n## Slide 1\n## Slide 2\n", encoding="utf-8"
    )

    assert problem_kinds(tmp_path) == []
    report = build_validation_report(tmp_path)
    assert report.exit_code == 0
    assert any("Every source has valid counterpart(s)" in line for line in report.summary_lines())


def test_missing_sheet_output_is_reported(tmp_path: Path) -> None:
    """The old converter wrote sheet 1 only; the gap must not pass."""
    write_fake_workbook(tmp_path / "quiz.xlsx", ["Questions", "Key"])
    (tmp_path / "quiz--questions.csv").write_text("Q\n", encoding="utf-8")

    assert problem_kinds(tmp_path) == [("MISSING", "quiz--key.csv")]


def test_empty_output_is_reported(tmp_path: Path) -> None:
    write_fake_workbook(tmp_path / "quiz.xlsx", ["Sheet1"])
    (tmp_path / "quiz.csv").write_bytes(b"")

    assert problem_kinds(tmp_path) == [("EMPTY", "quiz.csv")]


def test_cp1252_csv_is_not_utf8(tmp_path: Path) -> None:
    write_fake_workbook(tmp_path / "quiz.xlsx", ["Sheet1"])
    (tmp_path / "quiz.csv").write_bytes("It\u2019s\r\n".encode("cp1252"))

    assert problem_kinds(tmp_path) == [("NOT-UTF8", "quiz.csv")]
    assert build_validation_report(tmp_path).exit_code == 1


def test_word_cell_markers_are_control_characters(tmp_path: Path) -> None:
    (tmp_path / "guide.docx").write_bytes(b"not inspected for docx")
    (tmp_path / "guide.md").write_text("Cell\x07\r\nText\n", encoding="utf-8")

    assert problem_kinds(tmp_path) == [("CONTROL", "guide.md")]


def test_slide_count_mismatch_is_reported(tmp_path: Path) -> None:
    write_fake_deck(tmp_path / "deck.pptx", 3)
    (tmp_path / "deck.md").write_text("**Slides:** 2\n", encoding="utf-8")

    assert problem_kinds(tmp_path) == [("SLIDES", "deck.pptx")]


def test_curated_markdown_without_header_skips_slide_check(tmp_path: Path) -> None:
    write_fake_deck(tmp_path / "deck.pptx", 3)
    (tmp_path / "deck.md").write_text("# Hand-edited notes\n", encoding="utf-8")

    assert problem_kinds(tmp_path) == []


def test_pptx_and_docx_sharing_a_base_name_collide(tmp_path: Path) -> None:
    write_fake_deck(tmp_path / "week1.pptx", 1)
    (tmp_path / "week1.docx").write_bytes(b"x")
    (tmp_path / "week1.md").write_text("**Slides:** 1\n", encoding="utf-8")

    assert ("COLLISION", "week1.md") in problem_kinds(tmp_path)


def test_unreadable_source_and_lock_files(tmp_path: Path) -> None:
    (tmp_path / "broken.xlsx").write_bytes(b"not a zip")
    (tmp_path / "~$open.xlsx").write_bytes(b"office lock file")

    assert problem_kinds(tmp_path) == [("UNREADABLE", "broken.xlsx")]
