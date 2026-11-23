"""Tests for Word, PDF, kebab-case, and TOC outline conversion modules.

All binary inputs (.docx, .pdf) are generated in `tmp_path` during the
test run; no binary fixture is committed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import docx
import pymupdf
import pytest

from coursewright.design.conversion.documents import (
    clean_paragraph_text,
    convert_docx_to_markdown,
    markdown_prefix_for_style,
    write_docx_markdown,
)
from coursewright.design.conversion.kebab_names import (
    rename_to_kebab_case,
    to_kebab_case,
)
from coursewright.design.conversion.syllabus_pdfs import (
    SyllabusFacts,
    analyze_syllabus_pdfs,
    analyze_syllabus_text,
    convert_pdfs_to_markdown,
    extract_pdf_text,
    extract_specific_pdfs,
    extract_text_from_pdfs,
    pdf_to_markdown,
)
from coursewright.design.conversion.toc_outline import (
    build_outline,
    clean_text,
    find_module_headers,
    parse_toc_nodes,
    write_outline,
)


def write_synthetic_pdf(path: Path, pages: list[str]) -> Path:
    document = pymupdf.open()
    for page_text in pages:
        page = document.new_page()
        if page_text:
            page.insert_text((72, 72), page_text)
    document.save(str(path))
    document.close()
    return path


def test_docx_to_markdown_styles_and_non_overwrite(tmp_path: Path) -> None:
    docx_path = tmp_path / "study-guide.docx"
    document = docx.Document()
    document.add_heading("Unit 1 Guide", level=0)
    document.add_heading("Section Overview", level=1)
    document.add_heading("Key Terms", level=2)
    document.add_paragraph("Packet routing and switching.")
    document.add_paragraph("   ")
    document.save(str(docx_path))

    stamp = datetime(2026, 9, 1, 12, 0, 0)
    markdown = convert_docx_to_markdown(docx_path, converted_at=stamp)
    assert "# study-guide" in markdown
    assert "**Source:** study-guide.docx" in markdown
    assert "**Converted:** 2026-09-01 12:00:00" in markdown
    assert "# Unit 1 Guide" in markdown
    assert "# Section Overview" in markdown
    assert "## Key Terms" in markdown
    assert "Packet routing and switching." in markdown

    written = write_docx_markdown(docx_path)
    assert written == tmp_path / "study-guide.md"
    assert written is not None and written.read_bytes().startswith(b"\xef\xbb\xbf")
    assert write_docx_markdown(docx_path) is None


def test_clean_paragraph_and_style_mapping() -> None:
    assert clean_paragraph_text("  cell\x07\x0btext ") == "cell  text"
    assert markdown_prefix_for_style("Heading 3") == "### "
    assert markdown_prefix_for_style("Heading 4") == "#### "
    assert markdown_prefix_for_style("Normal") == ""


def test_kebab_case_normalization_and_dry_run_rename(tmp_path: Path) -> None:
    assert to_kebab_case("[Assignment] Week_01 Lab!.docx", ".docx") == "assignment-week-01-lab.docx"
    assert to_kebab_case("Module 2\u200b__Overview", "") == "module-2-overview"

    nested_dir = tmp_path / "Week 01_Intro"
    nested_dir.mkdir()
    nested_file = nested_dir / "[Lab] First_Steps.txt"
    nested_file.write_text("hello\n", encoding="utf-8")

    dry_outcomes = rename_to_kebab_case(tmp_path, execute=False)
    assert [outcome.status for outcome in dry_outcomes] == ["planned", "planned"]
    assert nested_file.exists()

    live_outcomes = rename_to_kebab_case(tmp_path, execute=True)
    assert [outcome.status for outcome in live_outcomes] == ["renamed", "renamed"]
    renamed_file = tmp_path / "week-01-intro" / "lab-first-steps.txt"
    assert renamed_file.read_text(encoding="utf-8") == "hello\n"


def test_kebab_case_skips_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "Quiz 1.txt"
    target = tmp_path / "quiz-1.txt"
    source.write_text("new\n", encoding="utf-8")
    target.write_text("existing\n", encoding="utf-8")

    outcomes = rename_to_kebab_case(tmp_path, execute=True)
    assert len(outcomes) == 1
    assert outcomes[0].status == "exists"
    assert target.read_text(encoding="utf-8") == "existing\n"


def test_toc_outline_extraction(tmp_path: Path) -> None:
    sample_html = """
    <div>
      <span class="page-title">Module 1. Network Fundamentals</span>
      <span class="page-number">1</span>
      <ul>
        <li aria-level="1">
          <span class="page-title">1.1 Protocols &amp; Layers</span>
          <span class="page-number">3</span>
        </li>
        <li aria-level="2">
          <span class="page-title">1.1.1 Packet Switching</span>
          <span class="page-number">7</span>
        </li>
      </ul>
    </div>
    """
    assert clean_text("<b>Hello &amp;   World</b>") == "Hello & World"
    assert find_module_headers(sample_html) == [("Module 1. Network Fundamentals", "1")]
    assert len(parse_toc_nodes(sample_html)) == 2

    html_path = tmp_path / "toc.html"
    html_path.write_text(sample_html, encoding="utf-8")
    out_path = write_outline(html_path)
    text = out_path.read_text(encoding="utf-8")
    assert "## Top-Level Modules" in text
    assert "- Module 1. Network Fundamentals (p. 1)" in text
    assert "- 1.1 Protocols & Layers (p. 3)" in text
    assert "  - 1.1.1 Packet Switching (p. 7)" in text
    assert build_outline(sample_html) == text

    with pytest.raises(FileNotFoundError):
        write_outline(tmp_path / "missing.html")


def test_syllabus_pdf_conversion_and_analysis(tmp_path: Path) -> None:
    pdf_path = write_synthetic_pdf(
        tmp_path / "course-syllabus.pdf",
        [
            "Course Summary: 15 Weeks\nGrading Scale: A=90%\nLate Policy: 10% per day\nFinal Exam",
            "",
        ],
    )
    extracted = extract_pdf_text(pdf_path)
    assert "Course Summary:" in extracted

    all_texts = extract_text_from_pdfs(tmp_path)
    assert "course-syllabus.pdf" in all_texts
    specific = extract_specific_pdfs(tmp_path, ["course-syllabus.pdf", "missing.pdf"])
    assert isinstance(specific["course-syllabus.pdf"], str)
    assert isinstance(specific["missing.pdf"], FileNotFoundError)

    markdown = pdf_to_markdown(pdf_path)
    assert "# course-syllabus.pdf" in markdown
    assert "*[Page 2 is empty or could not be read]*" in markdown

    out_dir = tmp_path / "converted"
    conversion_results = convert_pdfs_to_markdown(tmp_path, out_dir)
    assert conversion_results["course-syllabus.pdf"] == out_dir / "course-syllabus.md"

    facts = analyze_syllabus_text("course-syllabus.pdf", extracted)
    assert facts.is_canvas_summary is True
    assert facts.has_final is True
    assert facts.has_15_week is True
    assert facts.has_7_week is False
    assert "Grading Scale" in facts.grading_snippet
    assert "Late Policy" in facts.late_policy_snippet

    analyzed = analyze_syllabus_pdfs(tmp_path)
    assert len(analyzed) == 1
    assert isinstance(analyzed[0], SyllabusFacts)
