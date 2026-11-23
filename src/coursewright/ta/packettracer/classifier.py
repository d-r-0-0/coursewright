#!/usr/bin/env python3
"""
Packet Tracer Assignment Classifier & Completeness Checker
==========================================================

This module provides two main classes:

``PacketTracerAssignmentDetector``
    Determines whether a Canvas assignment is a Packet Tracer lab
    by inspecting the assignment name, allowed extensions, and
    description text.

``SubmissionCompletenessChecker``
    Given a student's submission attachments, decides whether the
    required file types are present (document + Packet Tracer save
    file) and produces a structured :class:`CompletenessReport`.

Design notes
------------
* Detection uses a two-tier approach:
  1. **Extension check** — if ``allowed_extensions`` includes
     ``.pkt``, ``.pka``, or ``.pkz``, it is almost certainly a
     PT assignment.
  2. **Name/description heuristics** — regex patterns that match
     common NetAcad naming conventions like
     "11.2.1 Packet Tracer - Configuring NAT" or "PTSA".

* Completeness checking is purely extension-based: we classify
  each attachment as a *document* (.pdf, .docx, .doc) or a
  *packet-tracer file* (.pkt, .pka, .pkz) and verify that both
  categories are represented.

"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from coursewright.core.canvas.models import Assignment, Submission

# ------------------------------------------------------------------ #
#   Constants — file-extension sets
# ------------------------------------------------------------------ #

PACKET_TRACER_EXTENSIONS: frozenset[str] = frozenset({
    ".pkt",   # Packet Tracer topology (activity file)
    ".pka",   # Packet Tracer activity (graded activity)
    ".pkz",   # Packet Tracer zipped bundle
})

DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf",
    ".docx",
    ".doc",
})

# All recognised lab-submission extensions (union of both sets)
ALL_LAB_EXTENSIONS: frozenset[str] = PACKET_TRACER_EXTENSIONS | DOCUMENT_EXTENSIONS


# ------------------------------------------------------------------ #
#   Regex patterns for assignment-name detection
# ------------------------------------------------------------------ #

# NetAcad naming: "X.Y.Z Packet Tracer - <Lab Title>"
# Also matches "Packet Tracer Skills Assessment" (PTSA) variants.
_NETACAD_NAME_PATTERNS: list[re.Pattern[str]] = [
    # "Packet Tracer" anywhere in the name (most reliable)
    re.compile(r"packet\s*tracer", re.IGNORECASE),
    # Common NetAcad abbreviations
    re.compile(r"\bPT(?:SA|MO|Activity)\b", re.IGNORECASE),
    # Dotted-number prefix like "11.2.1" followed by "PT" or
    # "Packet Tracer"
    re.compile(
        r"\d+\.\d+\.\d+\s+(?:PT|Packet\s*Tracer)",
        re.IGNORECASE,
    ),
]


# ------------------------------------------------------------------ #
#   Detection confidence levels
# ------------------------------------------------------------------ #

class DetectionConfidence(str, Enum):  # noqa: UP042 - keep str() output
    """How confident we are that an assignment is a PT lab."""

    # At least one PT extension is in allowed_extensions — strong
    # signal from Canvas configuration.
    EXTENSION_MATCH = "extension_match"

    # Assignment name matches known PT naming patterns.
    NAME_MATCH = "name_match"

    # Both extension and name match — highest confidence.
    FULL_MATCH = "full_match"

    # Description text mentions Packet Tracer (weaker signal).
    DESCRIPTION_ONLY = "description_only"

    # Not a Packet Tracer assignment.
    NOT_DETECTED = "not_detected"


# ------------------------------------------------------------------ #
#   Completeness verdict
# ------------------------------------------------------------------ #

class CompletenessVerdict(Enum):
    """Outcome of checking a single student's submission."""

    # Student submitted both a document and a PT file — all good.
    COMPLETE = auto()

    # Student submitted something, but is missing one category.
    PARTIAL = auto()

    # No submission at all (workflow_state == unsubmitted).
    NOT_SUBMITTED = auto()

    # Submission exists but contains NO recognised lab files.
    NO_LAB_FILES = auto()


# ------------------------------------------------------------------ #
#   Data classes for structured results
# ------------------------------------------------------------------ #

@dataclass
class DetectionResult:
    """Result of checking whether an assignment is a PT lab."""

    is_packet_tracer: bool
    confidence: DetectionConfidence
    matched_patterns: list[str] = field(default_factory=list)
    matched_extensions: list[str] = field(default_factory=list)


@dataclass
class CompletenessReport:
    """Per-student report on submission completeness.

    Attributes:
        student_name:      Display name from Canvas.
        user_id:           Canvas user ID.
        verdict:           Overall completeness verdict.
        has_document:      True if at least one .pdf/.docx/.doc found.
        has_packet_tracer: True if at least one .pkt/.pka/.pkz found.
        document_files:    Names of recognised document attachments.
        pt_files:          Names of recognised PT attachments.
        other_files:       Names of attachments that are neither.
        missing_types:     Human-readable list of what is missing.
    """

    student_name: str
    user_id: int
    verdict: CompletenessVerdict
    has_document: bool = False
    has_packet_tracer: bool = False
    document_files: list[str] = field(default_factory=list)
    pt_files: list[str] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)
    missing_types: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
#   PacketTracerAssignmentDetector
# ------------------------------------------------------------------ #

class PacketTracerAssignmentDetector:
    """Detect whether a Canvas assignment is a Packet Tracer lab.

    Usage::

        detector = PacketTracerAssignmentDetector()
        for assignment in assignments:
            result = detector.check(assignment)
            if result.is_packet_tracer:
                print(f"{assignment.name} — {result.confidence}")

    The detector never makes network calls.  It operates entirely
    on the in-memory :class:`Assignment` model that was already
    fetched from Canvas.
    """

    def check(self, assignment: Assignment) -> DetectionResult:
        """Analyse an assignment and return a detection result.

        The algorithm inspects three signals and combines them:

        1. ``allowed_extensions`` — strongest signal.  If the Canvas
           assignment configuration includes .pkt / .pka / .pkz this
           is almost certainly a PT lab.
        2. ``name`` — regex patterns for common NetAcad naming.
        3. ``description`` — fallback check for "Packet Tracer" text
           in the HTML description body.
        """
        extension_hits = self._check_extensions(assignment)
        name_hits = self._check_name(assignment.name)
        description_hit = self._check_description(assignment.description)

        # Combine signals into a confidence level
        has_extension = bool(extension_hits)
        has_name = bool(name_hits)

        if has_extension and has_name:
            confidence = DetectionConfidence.FULL_MATCH
        elif has_extension:
            confidence = DetectionConfidence.EXTENSION_MATCH
        elif has_name:
            confidence = DetectionConfidence.NAME_MATCH
        elif description_hit:
            confidence = DetectionConfidence.DESCRIPTION_ONLY
        else:
            confidence = DetectionConfidence.NOT_DETECTED

        is_pt = confidence != DetectionConfidence.NOT_DETECTED

        return DetectionResult(
            is_packet_tracer=is_pt,
            confidence=confidence,
            matched_patterns=[p for p in name_hits],
            matched_extensions=extension_hits,
        )

    def detect_in_course(
        self,
        assignments: list[Assignment],
    ) -> list[tuple[Assignment, DetectionResult]]:
        """Scan a list of assignments and return those detected as PT labs.

        Returns only assignments where ``is_packet_tracer`` is True,
        paired with their :class:`DetectionResult`.
        """
        results: list[tuple[Assignment, DetectionResult]] = []
        for assignment in assignments:
            result = self.check(assignment)
            if result.is_packet_tracer:
                results.append((assignment, result))
        return results

    # -- Private helpers --------------------------------------------- #

    @staticmethod
    def _check_extensions(assignment: Assignment) -> list[str]:
        """Return PT extensions found in allowed_extensions."""
        hits: list[str] = []
        for ext in assignment.allowed_extensions:
            # Canvas stores extensions without leading dots sometimes
            normalised = ext if ext.startswith(".") else f".{ext}"
            if normalised.lower() in PACKET_TRACER_EXTENSIONS:
                hits.append(normalised.lower())
        return hits

    @staticmethod
    def _check_name(name: str) -> list[str]:
        """Return pattern descriptions that matched the name."""
        hits: list[str] = []
        for pattern in _NETACAD_NAME_PATTERNS:
            if pattern.search(name):
                hits.append(pattern.pattern)
        return hits

    @staticmethod
    def _check_description(description: str | None) -> bool:
        """Return True if the description mentions Packet Tracer."""
        if not description:
            return False
        return bool(re.search(r"packet\s*tracer", description, re.IGNORECASE))


# ------------------------------------------------------------------ #
#   SubmissionCompletenessChecker
# ------------------------------------------------------------------ #

class SubmissionCompletenessChecker:
    """Check whether a student submitted all required file types.

    A "complete" Packet Tracer lab submission typically requires:
      * At least one **document** (.pdf, .docx, .doc) — the lab
        report / written answers.
      * At least one **Packet Tracer file** (.pkt, .pka, .pkz) —
        the saved network topology.

    The checker classifies every attachment by extension and
    produces a :class:`CompletenessReport` with clear verdicts.

    Usage::

        checker = SubmissionCompletenessChecker()
        for submission in submissions:
            report = checker.check(submission)
            if report.verdict != CompletenessVerdict.COMPLETE:
                print(f"{report.student_name}: {report.missing_types}")
    """

    def __init__(
        self,
        *,
        require_document: bool = True,
        require_packet_tracer: bool = True,
    ) -> None:
        """Configure which file categories are required.

        Args:
            require_document:       Expect at least one .pdf/.docx/.doc.
            require_packet_tracer:  Expect at least one .pkt/.pka/.pkz.
        """
        self.require_document = require_document
        self.require_packet_tracer = require_packet_tracer

    def check(self, submission: Submission) -> CompletenessReport:
        """Evaluate one student's submission for completeness.

        Args:
            submission: A Canvas :class:`Submission` object with
                        populated ``attachments`` list.

        Returns:
            A :class:`CompletenessReport` describing what was found
            and what (if anything) is missing.
        """
        student_name = submission.student_name
        user_id = submission.user_id

        # Handle unsubmitted students early
        if not submission.is_submitted:
            return CompletenessReport(
                student_name=student_name,
                user_id=user_id,
                verdict=CompletenessVerdict.NOT_SUBMITTED,
                missing_types=self._all_required_labels(),
            )

        # Classify each attachment by file extension
        document_files: list[str] = []
        pt_files: list[str] = []
        other_files: list[str] = []

        for attachment in submission.attachments:
            ext = attachment.extension
            display = attachment.display_name or attachment.filename
            if ext in DOCUMENT_EXTENSIONS:
                document_files.append(display)
            elif ext in PACKET_TRACER_EXTENSIONS:
                pt_files.append(display)
            else:
                other_files.append(display)

        # Inline text entry counts as a lab document.
        # Students sometimes paste screenshots and typed answers
        # directly into the Canvas rich-text editor instead of
        # uploading a .pdf or .docx.  If the submission has a
        # non-trivial body, treat the document as present.
        has_inline_text_entry = self._has_inline_text_entry_content(
            submission_type=submission.submission_type,
            submission_body=submission.body,
        )
        if has_inline_text_entry and not document_files:
            document_files.append("(inline text entry)")

        has_document = bool(document_files)
        has_pt = bool(pt_files)

        # Build missing-types list
        missing: list[str] = []
        if self.require_document and not has_document:
            missing.append("Lab document (.pdf or .docx)")
        if self.require_packet_tracer and not has_pt:
            missing.append("Packet Tracer file (.pkt or .pka)")

        # Determine verdict
        if not document_files and not pt_files:
            verdict = CompletenessVerdict.NO_LAB_FILES
        elif missing:
            verdict = CompletenessVerdict.PARTIAL
        else:
            verdict = CompletenessVerdict.COMPLETE

        return CompletenessReport(
            student_name=student_name,
            user_id=user_id,
            verdict=verdict,
            has_document=has_document,
            has_packet_tracer=has_pt,
            document_files=document_files,
            pt_files=pt_files,
            other_files=other_files,
            missing_types=missing,
        )

    def check_all(
        self,
        submissions: list[Submission],
    ) -> list[CompletenessReport]:
        """Check completeness for a batch of submissions.

        Args:
            submissions: All submissions for one assignment.

        Returns:
            A list of :class:`CompletenessReport` — one per student.
        """
        return [self.check(sub) for sub in submissions]

    def _has_inline_text_entry_content(
        self,
        *,
        submission_type: str | None,
        submission_body: str | None,
    ) -> bool:
        """Return True when inline submission body has meaningful content.

        Canvas inline text entry submissions can contain HTML wrappers
        that are visually empty (for example ``<p><br></p>``).  We only
        count the inline body as a "lab document" when it has either:

        1. non-empty text content, or
        2. at least one embedded image tag (common screenshot workflow).
        """
        if submission_type != "online_text_entry":
            return False
        if not submission_body:
            return False

        normalized_body = submission_body.strip()
        if not normalized_body:
            return False

        has_embedded_image = "<img" in normalized_body.lower()
        if has_embedded_image:
            return True

        body_without_html_tags = re.sub(r"<[^>]+>", " ", normalized_body)
        body_without_spaces = body_without_html_tags.replace("&nbsp;", " ")
        return bool(body_without_spaces.strip())

    def _all_required_labels(self) -> list[str]:
        """Human-readable labels for all required file categories."""
        labels: list[str] = []
        if self.require_document:
            labels.append("Lab document (.pdf or .docx)")
        if self.require_packet_tracer:
            labels.append("Packet Tracer file (.pkt or .pka)")
        return labels


# ------------------------------------------------------------------ #
#   Comment builder
# ------------------------------------------------------------------ #

# Marker string embedded in every completeness-feedback comment.
# Used by the PT screen to detect whether we've already posted
# feedback for a given submission (duplicate-prevention logic).
COMPLETENESS_COMMENT_MARKER = "\U0001f4cb Submission Completeness"

# Markers for the split two-comment flow introduced in the PT checker.
# Each comment type has its own unique marker so the Canvas client can
# find and delete ONLY the relevant comment type without clobbering the
# other one — letting instructors delete (say) the feedback comment
# for a resubmission while leaving the status checklist in place.
COMPLETENESS_STATUS_MARKER = "\U0001f4cb Submission Status"
COMPLETENESS_FEEDBACK_MARKER = "\U0001f4ac Submission Feedback"


# Templates are instructor-authored (they carry the instructor's own
# contact details and policies), so none ship in this public package.
# When the caller passes no ``templates_dir`` we look for a
# ``templates/`` folder inside the private workspace named by
# ``COURSEWRIGHT_WORKSPACE``; if that is unset, the generic fallback
# messages are used.
_WORKSPACE_ENVIRONMENT_VARIABLE = "COURSEWRIGHT_WORKSPACE"


def default_templates_directory() -> Path | None:
    """Return the workspace ``templates/`` folder, or None if unset."""
    workspace_text = os.environ.get(
        _WORKSPACE_ENVIRONMENT_VARIABLE, ""
    ).strip()
    if not workspace_text:
        return None
    return Path(workspace_text) / "templates"


def _load_template(
    templates_dir: Path,
    template_name: str,
    *,
    student_name: str = "",
    assignment_name: str = "",
) -> str | None:
    """Load a template file and fill ``{student_name}`` /
    ``{assignment_name}`` placeholders.

    Returns ``None`` if the template file does not exist or is empty.
    """
    template_path = templates_dir / template_name
    if not template_path.exists():
        return None
    raw_text = template_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return None
    return raw_text.format(
        student_name=student_name,
        assignment_name=assignment_name,
    )


def build_completeness_feedback_comment(
    report: CompletenessReport,
    *,
    assignment_name: str = "",
    templates_dir: Path | None = None,
) -> str:
    """Generate a Canvas comment summarising file-completeness.

    The comment starts with a checklist so students see at a glance
    what they submitted and what is missing::

        📋 Submission Completeness — 11.2.1 Packet Tracer Lab

        ✅ Lab document: Lab_Report.pdf
        ❌ Packet Tracer file: MISSING

    When custom templates exist in ``templates_dir``, the
    appropriate template is appended below the checklist:

    * ``missing_packet_tracer.md`` — when only the PT file is missing.
    * ``missing_lab_document.md``  — when only the lab document is
      missing.
    * ``missing_submission.md``    — when nothing was submitted at
      all (NOT_SUBMITTED verdict).

    Templates support ``{student_name}`` and ``{assignment_name}``
    placeholders that are filled automatically.

    If no template file is found, a generic action prompt is used as
    a fallback.

    Args:
        report:          A :class:`CompletenessReport` for one student.
        assignment_name: Optional assignment name for the header.
        templates_dir:   Directory containing ``.md`` templates.
                         Defaults to ``templates/`` inside
                         ``COURSEWRIGHT_WORKSPACE``; none if unset.

    Returns:
        A Markdown-formatted comment string ready for Canvas.
    """
    if templates_dir is None:
        templates_dir = default_templates_directory()

    header = COMPLETENESS_COMMENT_MARKER
    if assignment_name:
        header += f" — {assignment_name}"

    lines: list[str] = [header, ""]

    # Document row
    if report.has_document:
        doc_names = ", ".join(report.document_files)
        lines.append(f"✅ Lab document: {doc_names}")
    else:
        lines.append("❌ Lab document: MISSING")

    # Inline text entry note — let the student know we saw it
    if "(inline text entry)" in report.document_files:
        lines.append("   ↳ Submitted via Canvas text editor")

    # Packet Tracer row
    if report.has_packet_tracer:
        pt_names = ", ".join(report.pt_files)
        lines.append(f"✅ Packet Tracer file: {pt_names}")
    else:
        lines.append("❌ Packet Tracer file: MISSING")

    # Other files (informational)
    if report.other_files:
        other_names = ", ".join(report.other_files)
        lines.append(f"ℹ️ Other files: {other_names}")

    # ---- Custom template or generic fallback ---- #
    if report.missing_types:
        template_text = _pick_and_load_template(
            report=report,
            templates_dir=templates_dir,
            student_name=report.student_name,
            assignment_name=assignment_name,
        )
        if template_text:
            lines.append("")
            lines.append(template_text)
        else:
            # Generic fallback when no template files exist
            lines.append("")
            missing_text = " and ".join(report.missing_types)
            lines.append(f"⚠️ Please upload your {missing_text}.")

    return "\n".join(lines)


def build_submission_status_comment(
    report: CompletenessReport,
    *,
    assignment_name: str = "",
) -> str:
    """Build the *status* half of the two-comment PT feedback flow.

    Posts only the file-checklist rows (what was submitted / what is
    missing).  No instructional guidance is included here — that goes
    in the companion :func:`build_submission_feedback_comment`.

    Using a separate marker (``COMPLETENESS_STATUS_MARKER``) lets the
    PT screen find and delete ONLY the status comment independently of
    the feedback comment, so instructors can remove one without losing
    the other.

    Example output::

        📋 Submission Status — 11.2.1 Packet Tracer Lab

        ✅ Lab document: Lab_Report.pdf
        ❌ Packet Tracer file: MISSING

    Args:
        report:          A :class:`CompletenessReport` for one student.
        assignment_name: Optional assignment name for the header.

    Returns:
        A Markdown-formatted comment string ready for Canvas.
    """
    header = COMPLETENESS_STATUS_MARKER
    if assignment_name:
        header += f" — {assignment_name}"

    lines: list[str] = [header, ""]

    # Document row
    if report.has_document:
        doc_names = ", ".join(report.document_files)
        lines.append(f"✅ Lab document: {doc_names}")
    else:
        lines.append("❌ Lab document: MISSING")

    # Inline text entry note — let the student know we saw it
    if "(inline text entry)" in report.document_files:
        lines.append("   ↳ Submitted via Canvas text editor")

    # Packet Tracer row
    if report.has_packet_tracer:
        pt_names = ", ".join(report.pt_files)
        lines.append(f"✅ Packet Tracer file: {pt_names}")
    else:
        lines.append("❌ Packet Tracer file: MISSING")

    # Other files (informational)
    if report.other_files:
        other_names = ", ".join(report.other_files)
        lines.append(f"ℹ️ Other files: {other_names}")

    return "\n".join(lines)


def build_submission_feedback_comment(
    report: CompletenessReport,
    *,
    assignment_name: str = "",
    templates_dir: Path | None = None,
) -> str | None:
    """Build the *feedback* half of the two-comment PT feedback flow.

    Posts only the instructional guidance drawn from the markdown
    templates in ``templates_dir``.  The file-checklist is posted
    separately by :func:`build_submission_status_comment`.

    Returns ``None`` when no feedback is needed — i.e. when the
    submission is complete (``report.missing_types`` is empty).  The
    caller can skip posting altogether in that case.

    Using a separate marker (``COMPLETENESS_FEEDBACK_MARKER``) lets the
    PT screen find and delete ONLY the feedback comment independently of
    the status comment.

    Args:
        report:          A :class:`CompletenessReport` for one student.
        assignment_name: Optional assignment name for the header.
        templates_dir:   Directory containing ``.md`` templates.
                         Defaults to ``templates/`` inside
                         ``COURSEWRIGHT_WORKSPACE``; none if unset.

    Returns:
        A Markdown-formatted comment string ready for Canvas, or
        ``None`` if the submission is complete and no guidance is needed.
    """
    if not report.missing_types:
        # Nothing missing — no feedback comment needed at all.
        return None

    if templates_dir is None:
        templates_dir = default_templates_directory()

    header = COMPLETENESS_FEEDBACK_MARKER
    if assignment_name:
        header += f" — {assignment_name}"

    lines: list[str] = [header, ""]

    template_text = _pick_and_load_template(
        report=report,
        templates_dir=templates_dir,
        student_name=report.student_name,
        assignment_name=assignment_name,
    )
    if template_text:
        lines.append(template_text)
    else:
        # Generic fallback when no template files are present
        missing_text = " and ".join(report.missing_types)
        lines.append(f"⚠️ Please upload your {missing_text}.")

    return "\n".join(lines)


def _pick_and_load_template(
    *,
    report: CompletenessReport,
    templates_dir: Path | None,
    student_name: str,
    assignment_name: str,
) -> str | None:
    """Choose the right template based on what is missing.

    Selection logic:
    * NOT_SUBMITTED → ``missing_submission.md``
    * Has document but missing PT file → ``missing_packet_tracer.md``
    * Has PT file but missing document → ``missing_lab_document.md``
    * Missing both → concatenate both templates (PT first, then doc)
    """
    if templates_dir is None:
        return None

    if report.verdict == CompletenessVerdict.NOT_SUBMITTED:
        return _load_template(
            templates_dir,
            "missing_submission.md",
            student_name=student_name,
            assignment_name=assignment_name,
        )

    parts: list[str] = []

    if not report.has_packet_tracer:
        pt_text = _load_template(
            templates_dir,
            "missing_packet_tracer.md",
            student_name=student_name,
            assignment_name=assignment_name,
        )
        if pt_text:
            parts.append(pt_text)

    if not report.has_document:
        doc_text = _load_template(
            templates_dir,
            "missing_lab_document.md",
            student_name=student_name,
            assignment_name=assignment_name,
        )
        if doc_text:
            parts.append(doc_text)

    return "\n\n---\n\n".join(parts) if parts else None
