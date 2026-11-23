"""PDF syllabi to Markdown and quick text analysis (pypdf).

Replaces four one-off scripts that hard-coded a folder.  Callers pass
the directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


def extract_pdf_text(pdf_path: Path) -> str:
    """All page text, one newline after each page."""
    reader = PdfReader(pdf_path)
    return "".join((page.extract_text() or "") + "\n" for page in reader.pages)


def extract_text_from_pdfs(directory: Path) -> dict[str, str | Exception]:
    """Map every PDF file name in `directory` to its text or read error."""
    results: dict[str, str | Exception] = {}
    for pdf_path in sorted(directory.iterdir()):
        if pdf_path.suffix.lower() != ".pdf":
            continue
        try:
            results[pdf_path.name] = extract_pdf_text(pdf_path)
        except Exception as err:
            results[pdf_path.name] = err
    return results


def extract_specific_pdfs(directory: Path, target_files: list[str]) -> dict[str, str | Exception]:
    """Extract text for named PDFs under `directory`; missing yields FileNotFoundError."""
    results: dict[str, str | Exception] = {}
    for file_name in target_files:
        pdf_path = directory / file_name
        if not pdf_path.exists():
            results[file_name] = FileNotFoundError(f"File not found: {file_name}")
            continue
        try:
            results[file_name] = extract_pdf_text(pdf_path)
        except Exception as err:
            results[file_name] = err
    return results


def pdf_to_markdown(pdf_path: Path) -> str:
    """Markdown with a title line; empty pages are flagged, not lost."""
    reader = PdfReader(pdf_path)
    text = f"# {pdf_path.name}\n\n"
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n\n"
        else:
            text += f"*[Page {page_number} is empty or could not be read]*\n\n"
    return text


def convert_pdfs_to_markdown(
    source_dir: Path, destination_dir: Path
) -> dict[str, Exception | Path]:
    """Convert each PDF; map file name to output path or the error."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Exception | Path] = {}
    for pdf_path in sorted(source_dir.iterdir()):
        if pdf_path.suffix.lower() != ".pdf":
            continue
        try:
            markdown = pdf_to_markdown(pdf_path)
        except Exception as err:  # one bad PDF must not stop the batch
            results[pdf_path.name] = err
            continue
        output_path = destination_dir / f"{pdf_path.stem}.md"
        output_path.write_text(markdown, encoding="utf-8")
        results[pdf_path.name] = output_path
    return results


@dataclass(frozen=True)
class SyllabusFacts:
    file_name: str
    is_canvas_summary: bool
    has_final: bool
    has_15_week: bool
    has_7_week: bool
    opening: str
    grading_snippet: str
    late_policy_snippet: str


def _snippet_after(text: str, keyword: str, length: int = 500) -> str:
    index = text.find(keyword)
    return "" if index < 0 else text[index : index + length]


def analyze_syllabus_text(file_name: str, text: str) -> SyllabusFacts:
    return SyllabusFacts(
        file_name=file_name,
        is_canvas_summary="Course Summary:" in text[:1000],
        has_final="Final Exam" in text or "Final Project" in text,
        has_15_week="15 Weeks" in text or "15 weeks" in text,
        has_7_week="7 Weeks" in text or "7 weeks" in text,
        opening=text[:500],
        grading_snippet=_snippet_after(text, "Grading"),
        late_policy_snippet=_snippet_after(text, "Late"),
    )


def analyze_syllabus_pdfs(directory: Path) -> list[SyllabusFacts | tuple[str, Exception]]:
    """Facts for each PDF; an unreadable one yields (name, error)."""
    results: list[SyllabusFacts | tuple[str, Exception]] = []
    for pdf_path in sorted(directory.iterdir()):
        if pdf_path.suffix.lower() != ".pdf":
            continue
        try:
            results.append(analyze_syllabus_text(pdf_path.name, extract_pdf_text(pdf_path)))
        except Exception as err:
            results.append((pdf_path.name, err))
    return results
