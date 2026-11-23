"""Excel workbooks to CSV, one file per worksheet (openpyxl).

Naming rule: a single-sheet workbook keeps ``<base>.csv``; a
multi-sheet workbook writes ``<base>--<sheet-slug>.csv`` per sheet so
no sheet is dropped.  Existing outputs are never overwritten, so
hand-curated files survive a re-run.  CSVs are UTF-8 with a BOM, the
encoding Excel itself writes.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from coursewright.design.conversion.validation import sheet_slug

CSV_ENCODING = "utf-8-sig"


class WorksheetNameCollisionError(ValueError):
    """Two worksheet names slug to the same CSV path."""


@dataclass(frozen=True)
class SheetOutcome:
    sheet_name: str
    output_path: Path
    written: bool  # False when the output already existed (skipped)


def sheet_csv_path(base_csv_path: Path, sheet_name: str, sheet_count: int) -> Path:
    """quiz.csv + sheet "Answer Key" (of 2) -> quiz--answer-key.csv."""
    if sheet_count <= 1:
        return base_csv_path
    return base_csv_path.with_name(f"{base_csv_path.stem}--{sheet_slug(sheet_name)}.csv")


def workbook_csv_paths(base_csv_path: Path, sheet_names: list[str]) -> list[Path]:
    """One CSV path per sheet; raises if two sheets would share a path."""
    output_paths = [sheet_csv_path(base_csv_path, name, len(sheet_names)) for name in sheet_names]
    duplicates = sorted({p.name for p in output_paths if output_paths.count(p) > 1})
    if duplicates:
        raise WorksheetNameCollisionError(
            f"Worksheet names map to the same CSV path: {', '.join(duplicates)}"
        )
    return output_paths


def _cell_text(value: object) -> str:
    return "" if value is None else str(value)


def convert_workbook_to_csv(xlsx_path: Path) -> list[SheetOutcome]:
    """Write one CSV per worksheet beside the workbook.

    The workbook is always opened, even when a legacy ``<base>.csv``
    exists, because a multi-sheet workbook needs per-sheet outputs.
    """
    base_csv_path = xlsx_path.with_suffix(".csv")
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        sheet_names = list(workbook.sheetnames)
        output_paths = workbook_csv_paths(base_csv_path, sheet_names)
        outcomes: list[SheetOutcome] = []
        for sheet_name, output_path in zip(sheet_names, output_paths, strict=True):
            if output_path.exists():
                outcomes.append(SheetOutcome(sheet_name, output_path, False))
                continue
            with output_path.open("w", encoding=CSV_ENCODING, newline="") as handle:
                writer = csv.writer(handle)
                for row in workbook[sheet_name].iter_rows(values_only=True):
                    writer.writerow([_cell_text(value) for value in row])
            outcomes.append(SheetOutcome(sheet_name, output_path, True))
        return outcomes
    finally:
        workbook.close()


def find_workbooks(materials_folder: Path) -> list[Path]:
    """Every .xlsx under the folder, skipping Office lock files."""
    return sorted(p for p in materials_folder.rglob("*.xlsx") if not p.name.startswith("~$"))
