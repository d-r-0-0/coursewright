"""
GradeLinerUpper Orchestrator
============================
The main engine for importing NetAcad grades into Canvas.
"""

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.safe_write import (
    SafeCanvasWriter,
    SafeWriteError,
    default_backup_directory,
)
from coursewright.core.config.manager import resolve_workspace_directory
from coursewright.core.llm.client import LLMClient
from coursewright.ta.gradeliner.mapper import MappingManager
from coursewright.ta.gradeliner.models import (
    AssignmentMapCache,
    AssignmentMapping,
    GradeImportRow,
    StudentMapCache,
    StudentMapping,
)
from coursewright.ta.gradeliner.parser import NetacadExportParser
from coursewright.ta.gradeliner.validator import GradeValidator

# Get logger for this module
logger = logging.getLogger(__name__)


class GradeLinerUpper:
    """
    Main coordinator for the GradeLiner feature.

    It ties together the parser, mapper, and validator to provide
    a seamless flow from NetAcad export to Canvas submission.
    """

    def __init__(
        self,
        canvas_client: CanvasClient,
        llm_client: LLMClient | None,
        course_id: int,
        netacad_filepath: Path,
        export_root: Path | None = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            canvas_client: Connected Canvas API client.
            llm_client: Initialized LLM client for matching.  May be
                None when running the weekly fast-path with cached
                mappings (no LLM calls needed).
            course_id: The target Canvas course ID.
            netacad_filepath: Path to the NetAcad export file.
            export_root: Where the mapping caches live (see
                ``MappingManager``); defaults to the private workspace.
        """
        self.canvas_client = canvas_client
        self.llm_client = llm_client
        self.course_id = course_id
        self.netacad_filepath = netacad_filepath

        # Load course details to get the course code (for folder naming)
        course_data = self.canvas_client.get_course(course_id)
        if not course_data:
            raise ValueError(f"Could not find course with ID {course_id}")

        self.course_name = course_data.get("name", f"Course_{course_id}")
        self.course_code = course_data.get("course_code") or self.course_name

        # Initialize sub-components
        self.parser = NetacadExportParser()
        self.mapper = MappingManager(self.course_code, self.llm_client, export_root)
        self.validator = GradeValidator()

    def has_cached_mappings(self) -> bool:
        """Check if usable assignment and student mappings are already cached.

        An empty mappings list (e.g. left behind by a failed LLM run) is
        treated as "no cache" so the wizard will re-run mapping instead
        of silently presenting zero assignments.
        """
        assignment_cache = self.mapper.load_assignment_cache()
        student_cache = self.mapper.load_student_cache()
        if assignment_cache is None or student_cache is None:
            return False
        return bool(assignment_cache.mappings) and bool(student_cache.students)

    def get_assignment_mapping_proposals(self) -> list[AssignmentMapping]:
        """
        Run the initial parse and request assignment mapping proposals from the LLM.

        Returns:
            A list of proposed mappings between NetAcad and Canvas.
        """
        self.parser.parse_export_file(self.netacad_filepath)
        netacad_columns = self.parser.get_potential_assignment_headers()
        canvas_assignments = self.canvas_client.get_assignments(self.course_id)

        return self.mapper.propose_assignment_mapping(netacad_columns, canvas_assignments)

    def get_netacad_assignment_columns(self) -> list[str]:
        """Return score columns from the loaded NetAcad export.

        The TUI uses this after the LLM has drafted mappings so an
        instructor can manually choose a better source column for a
        Canvas assignment without reopening the CSV by hand.
        """
        self.parser.parse_export_file(self.netacad_filepath)
        return self.parser.get_potential_assignment_headers()

    def save_assignment_mappings(
        self,
        mappings: list[AssignmentMapping],
        unmapped_netacad: list[str] | None = None,
        unmapped_canvas: list[str] | None = None,
    ) -> None:
        """
        Save the approved assignment mappings to the cache.
        """
        cache = AssignmentMapCache(
            course_id=self.course_id,
            canvas_course_name=self.course_name,
            mappings=mappings,
            unmapped_netacad=unmapped_netacad or [],
            unmapped_canvas=unmapped_canvas or [],
        )
        self.mapper.save_assignment_cache(cache)

    def get_student_mapping_proposals(self) -> list[StudentMapping]:
        """
        Request student identity matching proposals from the LLM.

        Returns:
            A list of proposed matches between NetAcad students and Canvas users.
        """
        self.parser.parse_export_file(self.netacad_filepath)
        netacad_records = self.parser.extract_clean_student_records()

        # Simplify record data for the LLM prompt to save tokens
        netacad_students = [
            {"name": record.get("NAME"), "email": record.get("EMAIL")} for record in netacad_records
        ]

        canvas_roster = self.canvas_client.get_course_users(self.course_id)

        return self.mapper.propose_student_mapping(netacad_students, canvas_roster)

    def save_student_mappings(
        self,
        mappings: list[StudentMapping],
        unmatched_netacad: list[str] | None = None,
        unmatched_canvas: list[str] | None = None,
    ) -> None:
        """
        Save the approved student mappings to the cache.
        """
        cache = StudentMapCache(
            course_id=self.course_id,
            students=mappings,
            unmatched_netacad=unmatched_netacad or [],
            unmatched_canvas=unmatched_canvas or [],
        )
        self.mapper.save_student_cache(cache)

    def reconcile_cached_assignment_names(self) -> list[str]:
        """
        Fetch live Canvas assignments and sync any stale display names in cache.

        Canvas assignment IDs are permanent; instructors can rename the
        display name at any time.  Calling this before reading the cache
        ensures that the selection table in the TUI always shows the
        current Canvas name even when an assignment has been renamed since
        the initial LLM mapping was generated.

        Returns:
            A list of change descriptions (one per renamed assignment), or
            an empty list if the cache is absent or no names changed.
        """
        if not self.mapper.load_assignment_cache():
            return []
        canvas_assignments = self.canvas_client.get_assignments(self.course_id)
        return self.mapper.reconcile_assignment_names(canvas_assignments)

    def summarize_assignment_coverage(self) -> list[dict]:
        """
        Analyze which mapped assignments have meaningful NetAcad scores.

        For each mapped assignment, counts how many matched students have
        non-zero scores.  This lets the TUI pre-select only assignments
        worth importing (i.e. exams that students have actually taken).

        Returns:
            A list of dicts, one per mapped assignment, with keys:
                netacad_column, canvas_assignment_name, canvas_assignment_id,
                canvas_points_possible, netacad_points_possible,
                total_students, students_with_scores, score_percentage
        """
        # Sync stale display names before reading the cache.  Canvas IDs
        # never change, but instructors can rename assignments freely.
        self.reconcile_cached_assignment_names()

        assignment_cache = self.mapper.load_assignment_cache()
        student_cache = self.mapper.load_student_cache()

        if not assignment_cache or not student_cache:
            raise ValueError("Mappings not found in cache. Please run setup first.")

        return self._summarize_assignment_coverage_from_mappings(
            assignment_cache.mappings,
            student_cache,
        )

    def summarize_assignment_coverage_for_mappings(
        self,
        mappings: list[AssignmentMapping],
    ) -> list[dict]:
        """Analyze coverage for unsaved or newly corrected mappings."""
        student_cache = self.mapper.load_student_cache()
        if not student_cache:
            raise ValueError("Student mappings not found in cache. Please run setup first.")
        return self._summarize_assignment_coverage_from_mappings(
            mappings,
            student_cache,
        )

    def _summarize_assignment_coverage_from_mappings(
        self,
        mappings: list[AssignmentMapping],
        student_cache: StudentMapCache,
    ) -> list[dict]:
        """Count NetAcad score coverage for a specific mapping list."""
        self.parser.parse_export_file(self.netacad_filepath)
        netacad_records = self.parser.extract_clean_student_records()

        # Build student lookups (same as prepare_grade_import)
        student_by_email = {s.netacad_email.lower(): s for s in student_cache.students}
        student_by_name = {s.netacad_name.lower(): s for s in student_cache.students}

        # Count matched students once (those present in both systems)
        matched_student_count = 0
        for record in netacad_records:
            net_email = str(record.get("EMAIL", "") or "").lower()
            net_name = str(record.get("NAME", "") or "").lower()
            if student_by_email.get(net_email) or student_by_name.get(net_name):
                matched_student_count += 1

        summaries = []
        for assignment_mapping in mappings:
            students_with_scores = 0
            for record in netacad_records:
                net_email = str(record.get("EMAIL", "") or "").lower()
                net_name = str(record.get("NAME", "") or "").lower()
                student_match = student_by_email.get(net_email) or student_by_name.get(net_name)
                if not student_match:
                    continue

                raw_score = self.validator.parse_numeric_score(
                    record.get(assignment_mapping.netacad_column)
                )
                if raw_score > 0:
                    students_with_scores += 1

            score_percentage = (
                (students_with_scores / matched_student_count * 100)
                if matched_student_count > 0
                else 0.0
            )

            summaries.append(
                {
                    "netacad_column": assignment_mapping.netacad_column,
                    "canvas_assignment_name": assignment_mapping.canvas_assignment_name,
                    "canvas_assignment_id": assignment_mapping.canvas_assignment_id,
                    "canvas_points_possible": assignment_mapping.canvas_points_possible,
                    "netacad_points_possible": assignment_mapping.netacad_points_possible,
                    "total_students": matched_student_count,
                    "students_with_scores": students_with_scores,
                    "score_percentage": score_percentage,
                }
            )

        return summaries

    def _fetch_already_graded_pairs(
        self,
        assignment_ids: set[int],
    ) -> set[tuple[int, int]]:
        """
        Query Canvas for submissions that already have a score, and return
        them as a set of ``(assignment_id, user_id)`` pairs.

        This prevents the importer from re-posting grades (and duplicate
        comments) on assignments that were already graded in an earlier run.

        Args:
            assignment_ids: The Canvas assignment IDs to check.

        Returns:
            A set of (assignment_id, user_id) tuples that already have scores.
        """
        already_graded: set[tuple[int, int]] = set()
        for assignment_id in assignment_ids:
            try:
                existing_submissions = self.canvas_client.get_assignment_submissions(
                    self.course_id, assignment_id
                )
                for submission in existing_submissions:
                    # Only treat a submission as "already graded" when
                    # Canvas itself considers it graded.  A score of 0.0
                    # with workflow_state == "submitted" is a placeholder
                    # left behind by a previous failed run (or by Canvas
                    # auto-zeroing missing work) — the gradebook UI still
                    # shows those as ungraded, and we want to re-post the
                    # real NetAcad score on top.
                    if submission.excused or (
                        submission.score is not None and submission.workflow_state == "graded"
                    ):
                        already_graded.add((assignment_id, submission.user_id))
            except Exception as fetch_error:
                raise SafeWriteError(
                    f"Could not check current grades for assignment {assignment_id}; "
                    "grade import was stopped."
                ) from fetch_error
        return already_graded

    def prepare_grade_import(
        self,
        selected_assignment_ids: set[int] | None = None,
    ) -> list[GradeImportRow]:
        """
        Process the NetAcad file using cached mappings to produce importable rows.

        Automatically skips students who already have a score for an
        assignment in Canvas, preventing duplicate grade posts and the
        stacking of "Imported from NetAcad" comments on repeated runs.

        A cell NetAcad left blank is skipped too.  Blank means "not
        attempted yet" (every exam that is not due is blank), so importing
        it as 0 would zero future work.  Zeroing genuinely missing work is
        the job of the zero-ungraded workflow, which checks due dates.

        Args:
            selected_assignment_ids: If provided, only include these Canvas
                assignment IDs.  When None, all mapped assignments are included.

        Returns:
            A list of GradeImportRow objects ready for review.
        """
        # 1. Ensure file is parsed
        self.parser.parse_export_file(self.netacad_filepath)
        netacad_records = self.parser.extract_clean_student_records()

        # 2. Load mappings from cache
        assignment_cache = self.mapper.load_assignment_cache()
        student_cache = self.mapper.load_student_cache()

        if not assignment_cache or not student_cache:
            raise ValueError("Mappings not found in cache. Please run setup first.")

        # 3. Determine which assignment IDs we'll actually process so we
        #    can pre-fetch existing grades from Canvas in one pass.
        active_assignment_ids: set[int] = set()
        for assignment_mapping in assignment_cache.mappings:
            if (
                selected_assignment_ids is not None
                and assignment_mapping.canvas_assignment_id not in selected_assignment_ids
            ):
                continue
            active_assignment_ids.add(assignment_mapping.canvas_assignment_id)

        # 4. Fetch existing grades to avoid duplicate submissions.
        #    A (assignment_id, user_id) pair in this set means "already graded".
        already_graded_pairs = self._fetch_already_graded_pairs(active_assignment_ids)
        if already_graded_pairs:
            logger.info(
                "Found %d already-graded (assignment, student) pairs — these will be skipped.",
                len(already_graded_pairs),
            )

        # 5. Apply mappings and process scores
        import_rows = []
        skipped_already_graded_count = 0
        skipped_blank_score_count = 0

        # Create lookups for efficient matching
        student_by_email = {s.netacad_email.lower(): s for s in student_cache.students}
        student_by_name = {s.netacad_name.lower(): s for s in student_cache.students}

        for record in netacad_records:
            net_email = str(record.get("EMAIL", "") or "").lower()
            net_name = str(record.get("NAME", "") or "").lower()

            # Try to match student by email first, then name
            student_mapping = student_by_email.get(net_email) or student_by_name.get(net_name)

            if not student_mapping:
                logger.debug(f"Skipping NetAcad record with no student mapping: {net_name}")
                continue

            for assignment_mapping in assignment_cache.mappings:
                # Skip assignments not in the user's selection (if provided)
                if (
                    selected_assignment_ids is not None
                    and assignment_mapping.canvas_assignment_id not in selected_assignment_ids
                ):
                    continue

                # Skip if this student already has a grade for this assignment
                grade_key = (
                    assignment_mapping.canvas_assignment_id,
                    student_mapping.canvas_user_id,
                )
                if grade_key in already_graded_pairs:
                    skipped_already_graded_count += 1
                    logger.debug(
                        "Skipping already-graded: %s on assignment %s",
                        student_mapping.canvas_name,
                        assignment_mapping.canvas_assignment_name,
                    )
                    continue

                raw_score_str = record.get(assignment_mapping.netacad_column)
                if not self.validator.has_recorded_score(raw_score_str):
                    skipped_blank_score_count += 1
                    continue
                raw_score = self.validator.parse_numeric_score(raw_score_str)

                # Scale the score (e.g., 100/100 NetAcad -> 50/50 Canvas)
                scaled_score = self.validator.scale_score_half_up(
                    raw_score,
                    assignment_mapping.netacad_points_possible,
                    assignment_mapping.canvas_points_possible,
                )

                import_rows.append(
                    GradeImportRow(
                        canvas_user_id=student_mapping.canvas_user_id,
                        canvas_name=student_mapping.canvas_name,
                        assignment_id=assignment_mapping.canvas_assignment_id,
                        assignment_name=assignment_mapping.canvas_assignment_name,
                        netacad_score=raw_score,
                        scaled_score=scaled_score,
                    )
                )

        if skipped_already_graded_count:
            logger.info(
                "Skipped %d grade entries that were already in Canvas.",
                skipped_already_graded_count,
            )
        if skipped_blank_score_count:
            logger.info(
                "Skipped %d blank NetAcad cells (no score recorded yet).",
                skipped_blank_score_count,
            )

        return import_rows

    def submit_grades(
        self,
        import_rows: list[GradeImportRow],
        progress_callback: Callable[[int, int, str], None] | None = None,
        *,
        execute: bool = False,
        backup_directory: Path | None = None,
    ) -> dict[str, int]:
        """Bulk submit the prepared grades to the Canvas API.

        Safe by default (AGENTS.md 3.11): without ``execute=True`` nothing
        is sent and the result only reports how many grades are
        ``planned``.  With ``execute=True`` the rows to be written are
        saved as a timestamped JSON backup first; if that backup cannot be
        made nothing is written.

        Iterates per student rather than per assignment so the optional
        ``progress_callback`` can fire for each row.

        Args:
            import_rows:       List of grades to submit.
            progress_callback: Optional callable invoked after each
                               student is submitted.  Receives
                               ``(done, total, canvas_name)``.
            execute:           Actually write to Canvas.
            backup_directory:  Where the backup goes; defaults to the
                               private workspace backup folder.

        Returns:
            Counts under ``'successful'``, ``'failed'`` and ``'planned'``
            (rows a dry run would have written).

        Raises:
            BackupFailedError: the backup could not be written.
            SafeWriteError: no workspace is configured for the backup.
        """
        total = len(import_rows)
        total_results = {"successful": 0, "failed": 0, "planned": 0}

        if not execute:
            total_results["planned"] = total
            return total_results

        if backup_directory is None:
            workspace_directory = resolve_workspace_directory()
            if workspace_directory is None:
                raise SafeWriteError(
                    "No private workspace is configured; set COURSEWRIGHT_WORKSPACE "
                    "so grade backups have somewhere to go."
                )
            backup_directory = default_backup_directory(workspace_directory)
        writer = SafeCanvasWriter(self.canvas_client, backup_directory, execute=True)

        # Group rows by assignment so we can iterate assignment by
        # assignment while still firing per-student progress.
        grades_by_assignment: dict[int, list[GradeImportRow]] = {}
        for row in import_rows:
            grades_by_assignment.setdefault(row.assignment_id, []).append(row)

        done = 0
        for assignment_id, rows in grades_by_assignment.items():
            logger.info(
                "Submitting %d grades for assignment ID %d",
                len(rows),
                assignment_id,
            )
            for row in rows:
                done += 1
                comment = f"Imported from NetAcad ({row.netacad_score}% raw)"
                writer.submit_grade(
                    self.course_id,
                    assignment_id,
                    row.canvas_user_id,
                    row.scaled_score,
                    comment,
                )
                total_results["successful"] += 1

                if progress_callback is not None:
                    progress_callback(done, total, row.canvas_name)

        return total_results


def zero_ungraded_submissions(
    canvas_client: CanvasClient,
    writer: SafeCanvasWriter,
    course_id: int,
    assignment_ids: Sequence[int],
    *,
    execute: bool = False,
    include_submitted: bool = False,
    active_user_ids: set[int] | None = None,
    on_assignment_start: Callable[[int, int, int], None] | None = None,
    on_assignment_done: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Zero out ungraded (``score is None``) submissions across assignments.

    Writes go through ``writer.submit_grade(..., score=0)`` so backups and
    dry-run gating are handled uniformly by ``SafeCanvasWriter``.  Unlike the
    legacy ``zero_out_ungraded_past_due`` helper that skipped refused writes
    and continued, ``SafeCanvasWriter`` raises ``WriteFailedError`` on the
    first refused write (no silent failures, AGENTS.md 3.11).

    Args:
        canvas_client: Canvas API client used to fetch submissions.
        writer: Safe write wrapper enforcing dry-run and backups.
        course_id: Canvas course ID.
        assignment_ids: Assignment IDs to inspect and zero.
        execute: Whether to actually write zeros to Canvas.
        include_submitted: When False (default), skip submissions with work
            already turned in (``is_submitted`` or ``workflow_state`` in
            ``("submitted", "pending_review")``).
        active_user_ids: Optional set of active student Canvas user IDs;
            submissions from other user IDs (e.g. Test Student) are skipped.
        on_assignment_start: Optional callback invoked before each assignment.
        on_assignment_done: Optional callback invoked after each assignment.

    Returns:
        ``(total_zeroed, assignments_touched)`` counts.
    """
    total_zeroed = 0
    assignments_touched = 0
    total_assignments = len(assignment_ids)
    for index, assignment_id in enumerate(assignment_ids, start=1):
        if on_assignment_start is not None:
            on_assignment_start(index, total_assignments, assignment_id)
        submissions = canvas_client.get_assignment_submissions(course_id, assignment_id)
        zeroed_in_assignment = 0
        for submission in submissions:
            if submission.score is not None or submission.excused:
                continue
            if active_user_ids is not None and submission.user_id not in active_user_ids:
                continue
            is_submitted = getattr(submission, "is_submitted", False) is True
            workflow_state = getattr(submission, "workflow_state", None)
            if not include_submitted and (
                is_submitted or workflow_state in ("submitted", "pending_review")
            ):
                continue
            outcome = writer.submit_grade(
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=submission.user_id,
                score=0,
            )
            if outcome.executed or not execute:
                zeroed_in_assignment += 1
        if zeroed_in_assignment > 0:
            assignments_touched += 1
            total_zeroed += zeroed_in_assignment
        if on_assignment_done is not None:
            on_assignment_done(assignment_id, zeroed_in_assignment)
    return total_zeroed, assignments_touched
