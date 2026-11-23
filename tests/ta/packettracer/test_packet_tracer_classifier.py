"""Tests for the Packet Tracer assignment classifier and completeness checker.

Covers:
  - PacketTracerAssignmentDetector (name, extension, description detection)
  - SubmissionCompletenessChecker (file categorisation, verdicts)
  - build_completeness_feedback_comment (comment formatting)
  - Custom template loading for missing-file messages
"""

from __future__ import annotations

from pathlib import Path

from coursewright.core.canvas.models import (
    Assignment,
    Submission,
    SubmissionAttachment,
)
from coursewright.ta.packettracer.classifier import (
    COMPLETENESS_FEEDBACK_MARKER,
    COMPLETENESS_STATUS_MARKER,
    CompletenessReport,
    CompletenessVerdict,
    DetectionConfidence,
    PacketTracerAssignmentDetector,
    SubmissionCompletenessChecker,
    build_completeness_feedback_comment,
    build_submission_feedback_comment,
    build_submission_status_comment,
)

# ======================================================================
#  Helpers — lightweight factory functions
# ======================================================================


def _make_assignment(
    name: str = "Generic Assignment",
    allowed_extensions: list[str] | None = None,
    description: str | None = None,
    assignment_id: int = 1,
) -> Assignment:
    """Build a minimal Assignment for testing."""
    return Assignment(
        id=assignment_id,
        name=name,
        allowed_extensions=allowed_extensions or [],
        description=description,
    )


def _make_attachment(
    display_name: str,
    attachment_id: int = 1,
    size: int = 1024,
) -> SubmissionAttachment:
    """Build a SubmissionAttachment with the given filename."""
    return SubmissionAttachment(
        id=attachment_id,
        display_name=display_name,
        filename=display_name,
        size=size,
    )


def _make_submission(
    user_id: int = 1,
    attachments: list[SubmissionAttachment] | None = None,
    submitted: bool = True,
    user_name: str = "Test Student",
    submission_type: str | None = None,
    body: str | None = None,
) -> Submission:
    """Build a Submission, optionally with attachments."""
    return Submission(
        id=user_id * 100,
        user_id=user_id,
        submitted_at="2025-01-15T12:00:00Z" if submitted else None,
        workflow_state="submitted" if submitted else "unsubmitted",
        submission_type=submission_type,
        body=body,
        attachments=attachments or [],
        user={"name": user_name},
    )


# ======================================================================
#  PacketTracerAssignmentDetector
# ======================================================================


class TestPacketTracerAssignmentDetector:
    """Tests for the PT assignment detection heuristics."""

    def setup_method(self) -> None:
        self.detector = PacketTracerAssignmentDetector()

    # -- Extension-based detection ----------------------------------- #

    def test_detects_pka_extension(self):
        """Assignment with .pka in allowed_extensions is detected."""
        assignment = _make_assignment(
            name="Lab 1",
            allowed_extensions=["pka", "pdf"],
        )
        result = self.detector.check(assignment)
        assert result.is_packet_tracer
        assert ".pka" in result.matched_extensions

    def test_detects_pkt_extension_with_dot(self):
        """Extension with leading dot is normalised correctly."""
        assignment = _make_assignment(
            name="Lab 2",
            allowed_extensions=[".pkt", ".docx"],
        )
        result = self.detector.check(assignment)
        assert result.is_packet_tracer
        assert ".pkt" in result.matched_extensions

    def test_detects_pkz_extension(self):
        """.pkz (Packet Tracer zip bundle) triggers detection."""
        assignment = _make_assignment(
            name="Lab 3",
            allowed_extensions=["pkz"],
        )
        result = self.detector.check(assignment)
        assert result.is_packet_tracer

    def test_no_pt_extensions_not_detected(self):
        """Assignment with only .pdf/.docx is not flagged."""
        assignment = _make_assignment(
            name="Essay 1",
            allowed_extensions=["pdf", "docx"],
        )
        result = self.detector.check(assignment)
        assert not result.is_packet_tracer
        assert result.confidence == DetectionConfidence.NOT_DETECTED

    # -- Name-based detection ---------------------------------------- #

    def test_detects_packet_tracer_in_name(self):
        """'Packet Tracer' in the assignment name triggers detection."""
        assignment = _make_assignment(
            name="11.2.1 Packet Tracer - Configuring NAT",
        )
        result = self.detector.check(assignment)
        assert result.is_packet_tracer
        assert result.confidence == DetectionConfidence.NAME_MATCH

    def test_detects_ptsa_abbreviation(self):
        """PTSA (Packet Tracer Skills Assessment) is detected."""
        assignment = _make_assignment(name="Module 5 PTSA")
        result = self.detector.check(assignment)
        assert result.is_packet_tracer

    def test_detects_ptmo_abbreviation(self):
        """PTMO (Packet Tracer - Mode Observe) is detected."""
        assignment = _make_assignment(name="PTMO Lab 3")
        result = self.detector.check(assignment)
        assert result.is_packet_tracer

    def test_detects_ptactivity(self):
        """PTActivity abbreviation is detected."""
        assignment = _make_assignment(name="PTActivity - Subnetting")
        result = self.detector.check(assignment)
        assert result.is_packet_tracer

    def test_dotted_number_plus_pt(self):
        """NetAcad format '1.2.3 PT' is detected."""
        assignment = _make_assignment(name="4.5.6 PT Lab")
        result = self.detector.check(assignment)
        assert result.is_packet_tracer

    def test_generic_name_not_detected(self):
        """An ordinary assignment name is not flagged."""
        assignment = _make_assignment(name="Week 3 Reading Response")
        result = self.detector.check(assignment)
        assert not result.is_packet_tracer

    # -- Combination confidence -------------------------------------- #

    def test_full_match_when_both_signals(self):
        """Extension + name match yields FULL_MATCH confidence."""
        assignment = _make_assignment(
            name="11.2.1 Packet Tracer - NAT Config",
            allowed_extensions=["pka", "pdf"],
        )
        result = self.detector.check(assignment)
        assert result.confidence == DetectionConfidence.FULL_MATCH

    def test_extension_only_confidence(self):
        """Extension match alone yields EXTENSION_MATCH."""
        assignment = _make_assignment(
            name="Lab 1",
            allowed_extensions=["pka"],
        )
        result = self.detector.check(assignment)
        assert result.confidence == DetectionConfidence.EXTENSION_MATCH

    def test_description_only_confidence(self):
        """Packet Tracer in description only yields DESCRIPTION_ONLY."""
        assignment = _make_assignment(
            name="Network Lab",
            description="<p>Use Packet Tracer to build the topology.</p>",
        )
        result = self.detector.check(assignment)
        assert result.is_packet_tracer
        assert result.confidence == DetectionConfidence.DESCRIPTION_ONLY

    # -- Batch detection --------------------------------------------- #

    def test_detect_in_course_filters(self):
        """detect_in_course returns only PT assignments."""
        assignments = [
            _make_assignment(
                name="11.2.1 Packet Tracer Lab",
                assignment_id=1,
            ),
            _make_assignment(name="Reading Response", assignment_id=2),
            _make_assignment(
                name="Lab 3",
                allowed_extensions=["pka"],
                assignment_id=3,
            ),
        ]
        results = self.detector.detect_in_course(assignments)
        assert len(results) == 2
        detected_ids = {a.id for a, _ in results}
        assert detected_ids == {1, 3}


# ======================================================================
#  SubmissionCompletenessChecker
# ======================================================================


class TestSubmissionCompletenessChecker:
    """Tests for file-type completeness checking."""

    def setup_method(self) -> None:
        self.checker = SubmissionCompletenessChecker()

    def test_complete_submission(self):
        """Student with both a document and a PT file is COMPLETE."""
        submission = _make_submission(
            attachments=[
                _make_attachment("Lab_Report.pdf", attachment_id=1),
                _make_attachment("network.pka", attachment_id=2),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE
        assert report.has_document
        assert report.has_packet_tracer
        assert not report.missing_types

    def test_missing_packet_tracer_file(self):
        """Student with only a document is PARTIAL."""
        submission = _make_submission(
            attachments=[
                _make_attachment("Lab_Report.pdf"),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.PARTIAL
        assert report.has_document
        assert not report.has_packet_tracer
        assert "Packet Tracer file" in report.missing_types[0]

    def test_missing_document(self):
        """Student with only a PT file is PARTIAL."""
        submission = _make_submission(
            attachments=[
                _make_attachment("topology.pkt"),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.PARTIAL
        assert not report.has_document
        assert report.has_packet_tracer
        assert "Lab document" in report.missing_types[0]

    def test_unrecognised_files_only(self):
        """Student who only uploaded a .txt gets NO_LAB_FILES."""
        submission = _make_submission(
            attachments=[
                _make_attachment("notes.txt"),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.NO_LAB_FILES
        assert not report.has_document
        assert not report.has_packet_tracer
        assert report.other_files == ["notes.txt"]

    def test_not_submitted(self):
        """Unsubmitted student gets NOT_SUBMITTED verdict."""
        submission = _make_submission(submitted=False)
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.NOT_SUBMITTED
        assert len(report.missing_types) == 2

    def test_multiple_documents_and_pt_files(self):
        """Multiple docs + multiple PT files all get categorised."""
        submission = _make_submission(
            attachments=[
                _make_attachment("Report.pdf", attachment_id=1),
                _make_attachment("Answers.docx", attachment_id=2),
                _make_attachment("lab.pka", attachment_id=3),
                _make_attachment("backup.pkt", attachment_id=4),
                _make_attachment("screenshot.png", attachment_id=5),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE
        assert len(report.document_files) == 2
        assert len(report.pt_files) == 2
        assert len(report.other_files) == 1

    def test_docx_extension_recognised(self):
        """.docx is a valid document extension."""
        submission = _make_submission(
            attachments=[
                _make_attachment("Lab.docx", attachment_id=1),
                _make_attachment("net.pka", attachment_id=2),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE
        assert report.has_document

    def test_doc_extension_recognised(self):
        """.doc (legacy Word) is a valid document extension."""
        submission = _make_submission(
            attachments=[
                _make_attachment("Lab.doc", attachment_id=1),
                _make_attachment("net.pkt", attachment_id=2),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE

    def test_pkz_extension_recognised(self):
        """.pkz is recognised as a Packet Tracer file."""
        submission = _make_submission(
            attachments=[
                _make_attachment("Lab.pdf", attachment_id=1),
                _make_attachment("bundle.pkz", attachment_id=2),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE
        assert report.has_packet_tracer

    def test_case_insensitive_extensions(self):
        """Extension detection is case-insensitive."""
        submission = _make_submission(
            attachments=[
                _make_attachment("Lab.PDF", attachment_id=1),
                _make_attachment("Net.PKA", attachment_id=2),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE

    def test_check_all_batch(self):
        """check_all returns a report for every submission."""
        submissions = [
            _make_submission(
                user_id=1,
                attachments=[
                    _make_attachment("r.pdf", attachment_id=1),
                    _make_attachment("n.pka", attachment_id=2),
                ],
                user_name="Alice",
            ),
            _make_submission(
                user_id=2,
                attachments=[],
                submitted=False,
                user_name="Bob",
            ),
        ]
        reports = self.checker.check_all(submissions)
        assert len(reports) == 2
        assert reports[0].verdict == CompletenessVerdict.COMPLETE
        assert reports[1].verdict == CompletenessVerdict.NOT_SUBMITTED

    def test_document_not_required(self):
        """When require_document=False, PT file alone is COMPLETE."""
        checker = SubmissionCompletenessChecker(require_document=False)
        submission = _make_submission(
            attachments=[_make_attachment("lab.pka")],
        )
        report = checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE

    def test_pt_not_required(self):
        """When require_packet_tracer=False, document alone is COMPLETE."""
        checker = SubmissionCompletenessChecker(
            require_packet_tracer=False,
        )
        submission = _make_submission(
            attachments=[_make_attachment("report.pdf")],
        )
        report = checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE

    def test_student_name_from_user_dict(self):
        """Report picks up student_name from the submission model."""
        submission = _make_submission(user_name="Jane Doe")
        report = self.checker.check(submission)
        assert report.student_name == "Jane Doe"

    # -- Inline text entry ------------------------------------------- #

    def test_inline_text_entry_with_pt_file_is_complete(self):
        """Inline text body + PT attachment = COMPLETE.

        Students who paste screenshots / reflections into the Canvas
        rich-text editor AND upload a .pka file should be marked
        complete — the inline body counts as the lab document.
        """
        submission = _make_submission(
            submission_type="online_text_entry",
            body="<p>Reflection: I configured NAT.</p>",
            attachments=[
                _make_attachment("network.pka"),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE
        assert report.has_document
        assert report.has_packet_tracer
        assert not report.missing_types

    def test_inline_text_entry_without_pt_is_partial(self):
        """Inline text body with no PT file = PARTIAL.

        Inline-entry scenario: student pastes screenshots and text
        into the editor but forgets to upload the .pka separately.
        """
        submission = _make_submission(
            submission_type="online_text_entry",
            body="<p>Lab write-up</p><p><img src='screenshot.png'></p>",
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.PARTIAL
        assert report.has_document
        assert not report.has_packet_tracer
        assert "(inline text entry)" in report.document_files
        assert "Packet Tracer file" in report.missing_types[0]

    def test_inline_text_entry_empty_body_is_no_lab_files(self):
        """Inline type but blank body has nothing useful."""
        submission = _make_submission(
            submission_type="online_text_entry",
            body="   ",
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.NO_LAB_FILES
        assert not report.has_document

    def test_inline_text_entry_html_shell_only_is_no_lab_files(self):
        """HTML wrappers without text/image should not count."""
        submission = _make_submission(
            submission_type="online_text_entry",
            body="<p><br></p><p>&nbsp;</p>",
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.NO_LAB_FILES
        assert not report.has_document

    def test_inline_text_entry_image_only_counts_as_document(self):
        """Image-only inline body still represents lab work."""
        submission = _make_submission(
            submission_type="online_text_entry",
            body="<p><img src='lab-screenshot.png'></p>",
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.PARTIAL
        assert report.has_document
        assert not report.has_packet_tracer
        assert "(inline text entry)" in report.document_files

    def test_inline_text_does_not_override_uploaded_document(self):
        """If student uploads a .pdf AND has inline text, the .pdf
        is used as the document — not '(inline text entry)'."""
        submission = _make_submission(
            submission_type="online_text_entry",
            body="<p>Some notes</p>",
            attachments=[
                _make_attachment("Report.pdf", attachment_id=1),
                _make_attachment("lab.pka", attachment_id=2),
            ],
        )
        report = self.checker.check(submission)
        assert report.verdict == CompletenessVerdict.COMPLETE
        assert "Report.pdf" in report.document_files
        assert "(inline text entry)" not in report.document_files

    def test_online_upload_with_body_does_not_count_inline(self):
        """Only submission_type='online_text_entry' triggers inline
        logic — regular uploads with a body field are ignored."""
        submission = _make_submission(
            submission_type="online_upload",
            body="<p>Canvas auto-generated preview</p>",
        )
        report = self.checker.check(submission)
        # online_upload with no recognised attachments → NO_LAB_FILES
        assert report.verdict == CompletenessVerdict.NO_LAB_FILES
        assert not report.has_document


# ======================================================================
#  build_completeness_feedback_comment
# ======================================================================


class TestBuildCompletenessFeedbackComment:
    """Tests for the Canvas comment generator.

    These tests use an empty ``templates_dir`` so the generic
    fallback messages are produced (no custom templates loaded).
    See ``TestCustomTemplateLoading`` below for template tests.
    """

    def setup_method(self, tmp_path_factory=None) -> None:
        """Point templates_dir at an empty directory so no
        custom templates interfere with these baseline tests."""
        # We can't use tmp_path fixture in setup_method, so use
        # a non-existent path that won't have template files.
        self._empty_dir = Path(__file__).parent / "_no_templates_"

    def test_complete_comment(self):
        """Complete submission produces two checkmarks."""
        report = CompletenessReport(
            student_name="Alice",
            user_id=1,
            verdict=CompletenessVerdict.COMPLETE,
            has_document=True,
            has_packet_tracer=True,
            document_files=["Report.pdf"],
            pt_files=["network.pka"],
        )
        comment = build_completeness_feedback_comment(
            report,
            assignment_name="11.2.1 PT Lab",
            templates_dir=self._empty_dir,
        )
        assert "✅ Lab document: Report.pdf" in comment
        assert "✅ Packet Tracer file: network.pka" in comment
        assert "⚠️" not in comment
        assert "11.2.1 PT Lab" in comment

    def test_partial_comment_missing_pt(self):
        """Missing PT file produces a cross and action prompt."""
        report = CompletenessReport(
            student_name="Bob",
            user_id=2,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Report.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=self._empty_dir,
        )
        assert "✅ Lab document: Report.pdf" in comment
        assert "❌ Packet Tracer file: MISSING" in comment
        assert "⚠️" in comment

    def test_other_files_shown(self):
        """Unrecognised files are listed with info icon."""
        report = CompletenessReport(
            student_name="Carol",
            user_id=3,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Report.pdf"],
            other_files=["screenshot.png"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=self._empty_dir,
        )
        assert "ℹ️ Other files: screenshot.png" in comment

    def test_no_assignment_name_header(self):
        """Without assignment_name, header is just the title."""
        report = CompletenessReport(
            student_name="Dave",
            user_id=4,
            verdict=CompletenessVerdict.COMPLETE,
            has_document=True,
            has_packet_tracer=True,
            document_files=["r.pdf"],
            pt_files=["n.pka"],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=self._empty_dir,
        )
        assert comment.startswith("📋 Submission Completeness\n")
        assert "—" not in comment.split("\n")[0]

    def test_inline_text_entry_comment_shows_source(self):
        """Inline text entry comment notes Canvas text editor."""
        report = CompletenessReport(
            student_name="Mike",
            user_id=5,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["(inline text entry)"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=self._empty_dir,
        )
        assert "✅ Lab document: (inline text entry)" in comment
        assert "Canvas text editor" in comment
        assert "❌ Packet Tracer file: MISSING" in comment


# ======================================================================
#  Custom template loading
# ======================================================================


class TestCustomTemplateLoading:
    """Tests that custom templates replace the generic fallback."""

    def test_missing_pt_uses_template(self, tmp_path: Path):
        """When missing_packet_tracer.md exists, its content
        replaces the generic '⚠️ Please upload' prompt."""
        template = tmp_path / "missing_packet_tracer.md"
        template.write_text(
            "Hi {student_name}, upload your .pka for {assignment_name}.\n\nBest,\nProf",
            encoding="utf-8",
        )
        report = CompletenessReport(
            student_name="Alice",
            user_id=1,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Report.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            assignment_name="Lab 5",
            templates_dir=tmp_path,
        )
        assert "Hi Alice" in comment
        assert "Lab 5" in comment
        assert "Best,\nProf" in comment
        # Generic prompt should NOT appear
        assert "⚠️" not in comment

    def test_missing_doc_uses_template(self, tmp_path: Path):
        """When missing_lab_document.md exists, it is used."""
        template = tmp_path / "missing_lab_document.md"
        template.write_text(
            "Hi {student_name}, please review the rubric.",
            encoding="utf-8",
        )
        report = CompletenessReport(
            student_name="Bob",
            user_id=2,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=False,
            has_packet_tracer=True,
            pt_files=["network.pka"],
            missing_types=["Lab document (.pdf or .docx)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=tmp_path,
        )
        assert "Hi Bob" in comment
        assert "review the rubric" in comment
        assert "⚠️" not in comment

    def test_missing_both_concatenates_templates(self, tmp_path: Path):
        """When both files are missing and both templates exist,
        both are included separated by a horizontal rule."""
        (tmp_path / "missing_packet_tracer.md").write_text(
            "Upload your .pka, {student_name}.",
            encoding="utf-8",
        )
        (tmp_path / "missing_lab_document.md").write_text(
            "Upload your lab doc, {student_name}.",
            encoding="utf-8",
        )
        report = CompletenessReport(
            student_name="Carol",
            user_id=3,
            verdict=CompletenessVerdict.NO_LAB_FILES,
            has_document=False,
            has_packet_tracer=False,
            missing_types=[
                "Lab document (.pdf or .docx)",
                "Packet Tracer file (.pkt or .pka)",
            ],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=tmp_path,
        )
        assert "Upload your .pka, Carol" in comment
        assert "Upload your lab doc, Carol" in comment
        assert "---" in comment

    def test_not_submitted_uses_template(self, tmp_path: Path):
        """NOT_SUBMITTED verdict loads missing_submission.md."""
        (tmp_path / "missing_submission.md").write_text(
            "Hi {student_name}, I noticed you didn't submit.",
            encoding="utf-8",
        )
        report = CompletenessReport(
            student_name="Dave",
            user_id=4,
            verdict=CompletenessVerdict.NOT_SUBMITTED,
            missing_types=[
                "Lab document (.pdf or .docx)",
                "Packet Tracer file (.pkt or .pka)",
            ],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=tmp_path,
        )
        assert "Hi Dave" in comment
        assert "didn't submit" in comment

    def test_fallback_when_no_template_files(self, tmp_path: Path):
        """Empty templates_dir produces the generic ⚠️ prompt."""
        report = CompletenessReport(
            student_name="Eve",
            user_id=5,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Report.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            templates_dir=tmp_path,
        )
        assert "⚠️ Please upload your Packet Tracer file" in comment

    def test_workspace_templates_dir_loads(
        self, tmp_path: Path, monkeypatch
    ):
        """With no templates_dir, ``templates/`` inside the
        COURSEWRIGHT_WORKSPACE is picked up."""
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "missing_packet_tracer.md").write_text(
            "Synthetic guidance for {assignment_name}.",
            encoding="utf-8",
        )
        monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
        report = CompletenessReport(
            student_name="Test Student",
            user_id=99,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Report.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            assignment_name="Lab 1",
        )
        # The workspace template is used (not the generic fallback)
        assert "Synthetic guidance for Lab 1." in comment
        # Generic fallback should NOT appear
        assert "⚠️" not in comment

    def test_unset_workspace_uses_generic_fallback(self, monkeypatch):
        """No templates_dir and no workspace: generic prompt, and
        nothing from the real filesystem leaks in."""
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        report = CompletenessReport(
            student_name="Test Student",
            user_id=99,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Report.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_completeness_feedback_comment(
            report,
            assignment_name="Lab 1",
        )
        assert "⚠️ Please upload your Packet Tracer file" in comment


# ======================================================================
#  build_submission_status_comment  (new two-comment split)
# ======================================================================


class TestBuildSubmissionStatusComment:
    """Tests for the status half of the split two-comment PT flow.

    The status comment must:
    * Start with ``COMPLETENESS_STATUS_MARKER`` (not the old combined
      marker and not the feedback marker).
    * Show the file-checklist rows.
    * Contain NO template / instructional guidance text.
    """

    def test_status_marker_present(self):
        """The status comment header must use the status marker."""
        report = CompletenessReport(
            student_name="Alice",
            user_id=1,
            verdict=CompletenessVerdict.COMPLETE,
            has_document=True,
            has_packet_tracer=True,
            document_files=["Report.pdf"],
            pt_files=["net.pka"],
        )
        comment = build_submission_status_comment(report)
        assert COMPLETENESS_STATUS_MARKER in comment
        # Must NOT contain the feedback marker
        assert COMPLETENESS_FEEDBACK_MARKER not in comment

    def test_assignment_name_in_header(self):
        """Assignment name is appended to the marker with an em-dash."""
        report = CompletenessReport(
            student_name="Bob",
            user_id=2,
            verdict=CompletenessVerdict.COMPLETE,
            has_document=True,
            has_packet_tracer=True,
            document_files=["Doc.pdf"],
            pt_files=["net.pka"],
        )
        comment = build_submission_status_comment(
            report, assignment_name="11.2.1 PT Lab"
        )
        assert "11.2.1 PT Lab" in comment
        assert "—" in comment

    def test_complete_submission_shows_both_checkmarks(self):
        """Both ✅ rows appear for a complete submission."""
        report = CompletenessReport(
            student_name="Carol",
            user_id=3,
            verdict=CompletenessVerdict.COMPLETE,
            has_document=True,
            has_packet_tracer=True,
            document_files=["Lab.pdf"],
            pt_files=["topology.pka"],
        )
        comment = build_submission_status_comment(report)
        assert "✅ Lab document: Lab.pdf" in comment
        assert "✅ Packet Tracer file: topology.pka" in comment

    def test_missing_pt_shows_cross(self):
        """Missing PT file shows the ❌ row."""
        report = CompletenessReport(
            student_name="Dave",
            user_id=4,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Lab.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_submission_status_comment(report)
        assert "❌ Packet Tracer file: MISSING" in comment
        assert "✅ Lab document: Lab.pdf" in comment

    def test_no_template_guidance_in_status(self):
        """Status comment must not include template fallback text
        even when files are missing (that belongs in the feedback
        comment)."""
        report = CompletenessReport(
            student_name="Eve",
            user_id=5,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=False,
            has_packet_tracer=True,
            pt_files=["net.pka"],
            missing_types=["lab document"],
        )
        comment = build_submission_status_comment(report)
        # No action prompt in status comment
        assert "⚠️" not in comment

    def test_other_files_listed(self):
        """Unrecognised files appear with the ℹ️ prefix."""
        report = CompletenessReport(
            student_name="Frank",
            user_id=6,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Lab.pdf"],
            other_files=["notes.txt"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_submission_status_comment(report)
        assert "ℹ️ Other files: notes.txt" in comment


# ======================================================================
#  build_submission_feedback_comment  (new two-comment split)
# ======================================================================


class TestBuildSubmissionFeedbackComment:
    """Tests for the feedback half of the split two-comment PT flow.

    The feedback comment must:
    * Start with ``COMPLETENESS_FEEDBACK_MARKER``.
    * Contain template / instructional guidance text.
    * Return ``None`` when the report is complete (nothing missing).
    """

    def test_returns_none_for_complete_submission(self):
        """Complete submissions need no feedback comment — return None."""
        report = CompletenessReport(
            student_name="Alice",
            user_id=1,
            verdict=CompletenessVerdict.COMPLETE,
            has_document=True,
            has_packet_tracer=True,
            document_files=["Report.pdf"],
            pt_files=["net.pka"],
        )
        result = build_submission_feedback_comment(report)
        assert result is None

    def test_feedback_marker_present_when_missing(self, tmp_path):
        """The feedback comment uses the feedback marker."""
        report = CompletenessReport(
            student_name="Bob",
            user_id=2,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Lab.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        # Empty templates_dir → generic fallback text
        comment = build_submission_feedback_comment(
            report, templates_dir=tmp_path
        )
        assert comment is not None
        assert COMPLETENESS_FEEDBACK_MARKER in comment
        assert COMPLETENESS_STATUS_MARKER not in comment

    def test_generic_fallback_when_no_template(self, tmp_path):
        """When no template file is present the generic ⚠️ prompt
        appears in the feedback comment."""
        report = CompletenessReport(
            student_name="Carol",
            user_id=3,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Lab.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_submission_feedback_comment(
            report, templates_dir=tmp_path
        )
        assert comment is not None
        assert "⚠️" in comment

    def test_custom_template_loaded(self, tmp_path):
        """When a matching template exists its content appears in
        the feedback comment."""
        (tmp_path / "missing_packet_tracer.md").write_text(
            "Please submit your PT file for {assignment_name}.",
            encoding="utf-8",
        )
        report = CompletenessReport(
            student_name="Dave",
            user_id=4,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Lab.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_submission_feedback_comment(
            report,
            assignment_name="11.2.1 Lab",
            templates_dir=tmp_path,
        )
        assert comment is not None
        assert "Please submit your PT file for 11.2.1 Lab." in comment

    def test_no_file_checklist_in_feedback(self, tmp_path):
        """The feedback comment must NOT contain checklist rows
        — those belong in the status comment."""
        report = CompletenessReport(
            student_name="Eve",
            user_id=5,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Lab.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_submission_feedback_comment(
            report, templates_dir=tmp_path
        )
        assert comment is not None
        # Checklist rows should NOT appear here
        assert "✅ Lab document" not in comment
        assert "❌ Packet Tracer file" not in comment

    def test_workspace_templates_dir_produces_non_none(
        self, tmp_path: Path, monkeypatch
    ):
        """The workspace ``templates/`` dir is used when no
        templates_dir is passed."""
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "missing_packet_tracer.md").write_text(
            "Synthetic guidance body.", encoding="utf-8"
        )
        monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
        report = CompletenessReport(
            student_name="Test Student",
            user_id=99,
            verdict=CompletenessVerdict.PARTIAL,
            has_document=True,
            has_packet_tracer=False,
            document_files=["Report.pdf"],
            missing_types=["Packet Tracer file (.pkt or .pka)"],
        )
        comment = build_submission_feedback_comment(
            report, assignment_name="Lab 1"
        )
        assert comment is not None
        assert "Synthetic guidance body." in comment
