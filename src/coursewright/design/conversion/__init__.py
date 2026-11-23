"""Office and caption files to course-ready text.

Replaces the COURSE_BUILDER conversion scripts and caption/TOC cantrips
with silent library functions (``AGENTS.md`` 3.8).  PowerPoint-to-Markdown
(``convert-pptx-to-md.ps1``) is not ported because ``python-pptx`` is not
in the declared dependencies; slide count validation of existing ``.pptx``
decks remains in :mod:`coursewright.design.conversion.validation`.
"""

from __future__ import annotations

from coursewright.design.conversion.captions import (
    CAPTIONS_SUBDIR,
    TRANSCRIPTS_SUBDIR,
    convert,
    convert_caption_folder,
    describe_source,
    drop_stutters,
    parse_srt_cues,
    to_paragraphs,
)
from coursewright.design.conversion.documents import (
    clean_paragraph_text,
    convert_docx_to_markdown,
    markdown_prefix_for_style,
    write_docx_markdown,
)
from coursewright.design.conversion.kebab_names import (
    RenameOutcome,
    rename_to_kebab_case,
    to_kebab_case,
)
from coursewright.design.conversion.spreadsheets import (
    SheetOutcome,
    WorksheetNameCollisionError,
    convert_workbook_to_csv,
    find_workbooks,
    sheet_csv_path,
    workbook_csv_paths,
)
from coursewright.design.conversion.srt_fetch import (
    FetchResult,
    PanoptoUrls,
    caption_filename,
    fetch_session_captions,
    fetch_session_title,
    fetch_srt_text,
    is_session_id,
    record_in_inventory,
    sanitize_filename_component,
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
from coursewright.design.conversion.validation import (
    ConversionProblem,
    ValidationReport,
    build_validation_report,
    check_output_text,
    check_slide_count,
    check_source,
    count_deck_slides,
    expected_output_paths,
    find_markdown_collisions,
    find_source_files,
    read_workbook_sheet_names,
    sheet_slug,
    validate_materials_folder,
)

__all__ = [
    "CAPTIONS_SUBDIR",
    "TRANSCRIPTS_SUBDIR",
    "ConversionProblem",
    "FetchResult",
    "PanoptoUrls",
    "RenameOutcome",
    "SheetOutcome",
    "SyllabusFacts",
    "ValidationReport",
    "WorksheetNameCollisionError",
    "analyze_syllabus_pdfs",
    "analyze_syllabus_text",
    "build_outline",
    "build_validation_report",
    "caption_filename",
    "check_output_text",
    "check_slide_count",
    "check_source",
    "clean_paragraph_text",
    "clean_text",
    "convert",
    "convert_caption_folder",
    "convert_docx_to_markdown",
    "convert_pdfs_to_markdown",
    "convert_workbook_to_csv",
    "count_deck_slides",
    "describe_source",
    "drop_stutters",
    "expected_output_paths",
    "extract_pdf_text",
    "extract_specific_pdfs",
    "extract_text_from_pdfs",
    "fetch_session_captions",
    "fetch_session_title",
    "fetch_srt_text",
    "find_markdown_collisions",
    "find_module_headers",
    "find_source_files",
    "find_workbooks",
    "is_session_id",
    "markdown_prefix_for_style",
    "parse_srt_cues",
    "parse_toc_nodes",
    "pdf_to_markdown",
    "read_workbook_sheet_names",
    "record_in_inventory",
    "rename_to_kebab_case",
    "sanitize_filename_component",
    "sheet_csv_path",
    "sheet_slug",
    "to_kebab_case",
    "to_paragraphs",
    "validate_materials_folder",
    "workbook_csv_paths",
    "write_docx_markdown",
    "write_outline",
]
