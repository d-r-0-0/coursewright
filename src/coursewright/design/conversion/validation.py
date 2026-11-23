"""Validate Office-to-text conversions (read-only).

Checks that every .pptx/.docx/.xlsx under a materials folder has the
counterpart(s) the converters produce, and that each one is usable
text.  It never converts or writes anything, so it is safe to run
before and after a production conversion.

Expected counterparts (same folder, same base name):
    deck.pptx  -> deck.md
    guide.docx -> guide.md
    quiz.xlsx  -> quiz.csv                (one sheet)
    quiz.xlsx  -> quiz--<sheet-slug>.csv  (one per sheet, 2+ sheets)

Problem kinds (any one fails the run):
    MISSING     expected output does not exist
    EMPTY       output is zero bytes
    NOT-UTF8    output does not decode as UTF-8
    CONTROL     output holds control characters (e.g. Word's BEL 0x07)
    SLIDES      converter header slide count != slide count in the deck
    COLLISION   a .pptx and a .docx share a base name, or two sheets
                share a CSV path
    UNREADABLE  the source is not a readable Office (zip) file
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

SOURCE_EXTENSIONS = (".pptx", ".docx", ".xlsx")
# Tab, LF and CR are legitimate in text; every other C0 control is not.
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
CONVERTER_SLIDE_HEADER_PATTERN = re.compile(r"^\*\*Slides:\*\* (\d+)", re.M)
SLIDE_PART_PATTERN = re.compile(r"ppt/slides/slide\d+\.xml")


@dataclass(frozen=True)
class ConversionProblem:
    kind: str
    path: Path
    detail: str = ""


@dataclass
class ValidationReport:
    """Everything a front end needs to describe one validation run."""

    materials_folder: Path
    source_paths: list[Path] = field(default_factory=list)
    problems: list[ConversionProblem] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """0 when clean; 1 on any problem or when nothing was found."""
        if not self.source_paths or self.problems:
            return 1
        return 0

    def source_counts(self) -> dict[str, int]:
        return {
            extension: sum(1 for p in self.source_paths if p.suffix.lower() == extension)
            for extension in SOURCE_EXTENSIONS
        }

    def summary_lines(self) -> list[str]:
        """Plain-text report lines, for `ui/` to render as it likes."""
        counts = ", ".join(f"{n} {ext[1:]}" for ext, n in self.source_counts().items())
        lines = [
            f"Validating conversions under {self.materials_folder}",
            f"Sources: {len(self.source_paths)} ({counts})",
        ]
        if not self.source_paths:
            lines.append("No Office sources found; conversion validation is incomplete")
            return lines
        for problem in self.problems:
            relative_path = problem.path.relative_to(self.materials_folder).as_posix()
            suffix = f"  ({problem.detail})" if problem.detail else ""
            lines.append(f"{problem.kind:<10} {relative_path}{suffix}")
        if self.problems:
            lines.append(f"{len(self.problems)} problem(s) found")
        else:
            lines.append("Every source has valid counterpart(s)")
        return lines


def sheet_slug(sheet_name: str) -> str:
    """Lower-case, hyphen-separated worksheet name; "sheet" if empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", sheet_name.lower()).strip("-")
    return slug or "sheet"


def read_workbook_sheet_names(xlsx_path: Path) -> list[str]:
    with zipfile.ZipFile(xlsx_path) as workbook_zip:
        workbook_xml = workbook_zip.read("xl/workbook.xml").decode("utf-8")
    root = ElementTree.fromstring(workbook_xml)
    return [
        element.attrib["name"]
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "sheet"
    ]


def count_deck_slides(pptx_path: Path) -> int:
    with zipfile.ZipFile(pptx_path) as deck_zip:
        return sum(1 for name in deck_zip.namelist() if SLIDE_PART_PATTERN.fullmatch(name))


def expected_output_paths(source_path: Path) -> list[Path]:
    """Return the counterpart file(s) the converters write for a source."""
    if source_path.suffix.lower() != ".xlsx":
        return [source_path.with_suffix(".md")]
    sheet_names = read_workbook_sheet_names(source_path)
    if len(sheet_names) <= 1:
        return [source_path.with_suffix(".csv")]
    return [
        source_path.with_name(f"{source_path.stem}--{sheet_slug(name)}.csv")
        for name in sheet_names
    ]


def find_source_files(materials_folder: Path) -> list[Path]:
    """Office sources, skipping lock files (~$x) and node_modules."""
    return sorted(
        path
        for path in materials_folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SOURCE_EXTENSIONS
        and not path.name.startswith("~$")
        and "node_modules" not in path.parts
    )


def check_output_text(output_path: Path) -> tuple[list[ConversionProblem], str]:
    raw_bytes = output_path.read_bytes()
    if not raw_bytes:
        return [ConversionProblem("EMPTY", output_path)], ""
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as decode_error:
        detail = f"byte {decode_error.start}"
        return [ConversionProblem("NOT-UTF8", output_path, detail)], ""
    control_count = len(CONTROL_CHARACTER_PATTERN.findall(text))
    if control_count:
        detail = f"{control_count} control chars"
        return [ConversionProblem("CONTROL", output_path, detail)], text
    return [], text


def check_slide_count(deck_path: Path, markdown_text: str) -> list[ConversionProblem]:
    """Only converter-written Markdown carries a **Slides:** header."""
    header_match = CONVERTER_SLIDE_HEADER_PATTERN.search(markdown_text)
    if not header_match:
        return []
    declared_slides = int(header_match.group(1))
    actual_slides = count_deck_slides(deck_path)
    if declared_slides == actual_slides:
        return []
    detail = f"header says {declared_slides}, deck has {actual_slides}"
    return [ConversionProblem("SLIDES", deck_path, detail)]


def check_source(source_path: Path) -> list[ConversionProblem]:
    try:
        output_paths = expected_output_paths(source_path)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as read_error:
        return [ConversionProblem("UNREADABLE", source_path, str(read_error))]
    duplicate_paths = [path for path, count in Counter(output_paths).items() if count > 1]
    if duplicate_paths:
        return [
            ConversionProblem("COLLISION", path, "worksheet names share a CSV path")
            for path in duplicate_paths
        ]
    problems: list[ConversionProblem] = []
    for output_path in output_paths:
        if not output_path.exists():
            problems.append(ConversionProblem("MISSING", output_path))
            continue
        text_problems, text = check_output_text(output_path)
        problems.extend(text_problems)
        if text and source_path.suffix.lower() == ".pptx":
            try:
                problems.extend(check_slide_count(source_path, text))
            except zipfile.BadZipFile as read_error:
                problems.append(ConversionProblem("UNREADABLE", source_path, str(read_error)))
    return problems


def find_markdown_collisions(source_paths: list[Path]) -> list[ConversionProblem]:
    markdown_targets: dict[Path, list[Path]] = {}
    for source_path in source_paths:
        if source_path.suffix.lower() in (".pptx", ".docx"):
            target = source_path.with_suffix(".md")
            markdown_targets.setdefault(target, []).append(source_path)
    return [
        ConversionProblem("COLLISION", target, " + ".join(p.name for p in sources))
        for target, sources in markdown_targets.items()
        if len(sources) > 1
    ]


def validate_materials_folder(
    materials_folder: Path,
) -> tuple[list[Path], list[ConversionProblem]]:
    source_paths = find_source_files(materials_folder)
    problems = find_markdown_collisions(source_paths)
    for source_path in source_paths:
        problems.extend(check_source(source_path))
    return source_paths, problems


def build_validation_report(materials_folder: Path) -> ValidationReport:
    """Validate a folder and return the whole report as data.

    Raises NotADirectoryError when the folder does not exist.
    """
    if not materials_folder.is_dir():
        raise NotADirectoryError(f"Not a folder: {materials_folder}")
    source_paths, problems = validate_materials_folder(materials_folder)
    return ValidationReport(materials_folder, source_paths, problems)
