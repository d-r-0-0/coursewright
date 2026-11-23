"""
Tests for Gradebook Manager
============================

PURPOSE:
--------
These tests verify the GradebookManager class which handles grade submission
and management through the Canvas LMS API. This is critical because grades
directly affect student academic records -- any bug here could produce
incorrect grades or silently lose submissions.

WHAT WE'RE TESTING:
-------------------
1. Single grade submission (success, failure, exception handling)
2. Batch grade submission (all pass, partial failures, total failures)
3. Grade validation (negative scores, over-max scores, valid scores)
4. Grade comment generation (with and without detailed post data)
5. Assignment lookup from discussion topics
6. Current grade retrieval
7. Submission listing

WHY MOCK THE CANVAS CLIENT:
----------------------------
We mock the Canvas API client so that tests:
  - Run instantly (no network calls)
  - Don't need real Canvas credentials
  - Return predictable, controllable data
  - Work even if Canvas is down
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from coursewright.core.canvas.safe_write import (
    BackupFailedError,
    SafeCanvasWriter,
    WriteFailedError,
)
from coursewright.ta.grading.gradebook import BatchGradeResult, GradebookManager

# =============================================================================
# FIXTURES — Reusable test data specific to gradebook tests
# =============================================================================


@pytest.fixture
def mock_canvas_client_for_gradebook():
    """
    Creates a mock Canvas API client pre-configured for gradebook tests.

    WHY: Every GradebookManager test needs a mock client. This fixture
    provides one with sensible defaults that individual tests can override.

    WHAT IT PROVIDES:
    - base_url: A fake Canvas instance URL
    - submit_grade: Returns True by default (success)
    - get_submission: Returns a sample submission (read for the backup)
    - get_assignment_by_discussion: Returns a sample assignment dict
    """
    mock_client = Mock()
    mock_client.base_url = "https://canvas.example.edu"

    # Default: single grade submission succeeds
    mock_client.submit_grade.return_value = True

    # Default: the safe writer can read the submission it backs up first
    mock_client.get_submission.return_value = {"id": 1, "score": None}

    # Default: assignment lookup returns a sample assignment
    mock_client.get_assignment_by_discussion.return_value = {
        "id": 9999,
        "name": "Week 1 Discussion",
        "points_possible": 100,
    }

    return mock_client


@pytest.fixture
def backup_directory(tmp_path):
    """Where the safe writer puts its pre-write JSON backups."""
    return tmp_path / "backups"


@pytest.fixture
def gradebook_manager_with_mock_client(mock_canvas_client_for_gradebook, backup_directory):
    """
    Creates a GradebookManager wired up with the mock Canvas client.

    WHY: This is the primary "system under test" fixture. Nearly every
    test in this file uses this to get a ready-to-use manager instance.
    Grade writes go through a real SafeCanvasWriter in execute mode, so
    the backup-then-write path is exercised, not mocked away.
    """
    writer = SafeCanvasWriter(mock_canvas_client_for_gradebook, backup_directory, execute=True)
    return GradebookManager(mock_canvas_client_for_gradebook, writer)


@pytest.fixture
def dry_run_gradebook_manager(mock_canvas_client_for_gradebook, backup_directory):
    """A GradebookManager whose writer is in its default dry-run mode."""
    writer = SafeCanvasWriter(mock_canvas_client_for_gradebook, backup_directory)
    return GradebookManager(mock_canvas_client_for_gradebook, writer)


@pytest.fixture
def sample_assignment_with_100_points():
    """
    A sample assignment dict as returned by the Canvas API.

    WHY: Used in validation tests where we need an assignment's
    points_possible to check that scores don't exceed the maximum.
    """
    return {
        "id": 9999,
        "name": "Week 1 Discussion",
        "points_possible": 100,
    }


@pytest.fixture
def sample_scoring_details_for_comment():
    """
    Scoring breakdown details used when generating grade comments.

    WHY: The create_grade_comment method requires a scoring_details dict
    with participation_score, quality_score, and total_score keys.
    """
    return {
        "participation_score": 40.0,
        "quality_score": 45.0,
        "total_score": 85.0,
    }


@pytest.fixture
def sample_analysis_results_for_comment():
    """
    Legacy-format analysis results used for grade comment generation.

    WHY: When no detailed participation object is provided, the comment
    generator falls back to this legacy dict format with word_count and
    reply_count fields.
    """
    return {
        "word_count": 420,
        "reply_count": 3,
        "late_submission": False,
    }


@pytest.fixture
def participation_object_with_post_details():
    """
    A SimpleNamespace mimicking a StudentParticipation with post_details.

    WHY: The create_grade_comment method checks for a participation object
    with a post_details attribute to decide between detailed and legacy
    comment formats. We use SimpleNamespace as a lightweight stand-in.
    """
    return SimpleNamespace(
        post_details=[
            {
                "word_count": 350,
                "is_largest": True,
                "is_reply": False,
                "post_number": 1,
            },
            {
                "word_count": 60,
                "is_largest": False,
                "is_reply": True,
                "post_number": 2,
            },
            {
                "word_count": 55,
                "is_largest": False,
                "is_reply": True,
                "post_number": 3,
            },
        ],
        total_word_count=465,
        reply_count=2,
    )


# =============================================================================
# TEST CLASS: Single Grade Submission
# =============================================================================


class TestSingleGradeSubmission:
    """
    Tests for submitting a single student's grade through the Canvas API.

    WHY: Grade submission is the most consequential operation in the
    gradebook. We must verify it delegates correctly to the Canvas client
    and handles both success and failure paths.
    """

    def test_submit_grade_returns_true_when_canvas_client_succeeds(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: A successful grade submission returns True.

        SCENARIO:
        - Canvas client's submit_grade returns True (API call worked)
        - GradebookManager should propagate that success

        WHY: The happy path must actually work -- students need their
        grades to be recorded.
        """
        # Arrange: client already returns True by default
        course_id = 12345
        assignment_id = 9999
        student_id = 1001
        score = 85.0
        comment = "Good discussion participation"

        # Act: submit the grade through the manager
        result = gradebook_manager_with_mock_client.submit_grade(
            course_id, assignment_id, student_id, score, comment
        )

        # Assert: Canvas accepted it and a backup was written first
        assert result.executed is True, "submit_grade should report an executed write"
        assert result.backup_path.exists(), "a backup must exist for an executed write"

        # Verify the canvas client was called with the correct arguments
        mock_canvas_client_for_gradebook.submit_grade.assert_called_once_with(
            course_id, assignment_id, student_id, score, comment
        )

    def test_submit_grade_raises_when_canvas_client_fails(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: A refused grade submission raises instead of failing silently.

        SCENARIO:
        - Canvas client's submit_grade returns False (API rejected it)
        - The safe writer raises WriteFailedError naming the backup

        WHY: If Canvas rejects a grade, the user needs to know so they
        can investigate and retry (AGENTS.md 3.11: no silent failures).
        """
        mock_canvas_client_for_gradebook.submit_grade.return_value = False

        with pytest.raises(WriteFailedError, match="backup kept"):
            gradebook_manager_with_mock_client.submit_grade(
                course_id=12345,
                assignment_id=9999,
                student_id=1001,
                score=90.0,
            )

    def test_submit_grade_aborts_when_backup_cannot_be_made(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: No grade is sent when the current submission cannot be read.

        SCENARIO:
        - Canvas returns nothing for the submission (nothing to back up)

        EXPECTED:
        - BackupFailedError, and the client's submit_grade never called
        """
        mock_canvas_client_for_gradebook.get_submission.return_value = None

        with pytest.raises(BackupFailedError):
            gradebook_manager_with_mock_client.submit_grade(12345, 9999, 1001, 75.0)

        mock_canvas_client_for_gradebook.submit_grade.assert_not_called()

    def test_submit_grade_is_a_dry_run_by_default(
        self, dry_run_gradebook_manager, mock_canvas_client_for_gradebook, backup_directory
    ):
        """
        TEST: Without execute mode nothing is read, written or backed up.
        """
        result = dry_run_gradebook_manager.submit_grade(12345, 9999, 1001, 88.0, "Nice")

        assert result.executed is False
        assert result.payload == {"score": 88.0, "comment": "Nice"}
        mock_canvas_client_for_gradebook.submit_grade.assert_not_called()
        mock_canvas_client_for_gradebook.get_submission.assert_not_called()
        assert not backup_directory.exists()

    def test_executed_grade_backup_records_payload_and_prior_state(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: The backup holds the grade sent and what Canvas held before.
        """
        mock_canvas_client_for_gradebook.get_submission.return_value = {"id": 7, "score": 50.0}

        result = gradebook_manager_with_mock_client.submit_grade(12345, 9999, 1001, 95.0)

        backup = json.loads(result.backup_path.read_text(encoding="utf-8"))
        assert backup["operation"] == "submit_grade"
        assert backup["payload"] == {"score": 95.0}
        assert backup["state_before"] == {"id": 7, "score": 50.0}


# =============================================================================
# TEST CLASS: Batch Grade Submission
# =============================================================================


class TestBatchGradeSubmission:
    """
    Tests for submitting multiple grades in a single batch operation.

    WHY: Instructors grade entire classes at once. Batch submission must
    correctly track which submissions succeeded and which failed, even
    when some fail partway through.
    """

    def test_submit_multiple_grades_reports_all_successful_when_all_pass(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: When all grades in a batch succeed, counts reflect that.

        SCENARIO:
        - 3 students with grades to submit
        - Canvas client accepts all 3

        EXPECTED:
        - successful: 3, failed: 0, and one backup per grade
        """
        grades_to_submit = [
            {"student_id": 1001, "score": 100.0, "comment": "Excellent work"},
            {"student_id": 1002, "score": 80.0, "comment": "Good effort"},
            {"student_id": 1003, "score": 60.0},
        ]

        results = gradebook_manager_with_mock_client.submit_multiple_grades(
            course_id=12345,
            assignment_id=9999,
            grades=grades_to_submit,
        )

        assert isinstance(results, BatchGradeResult)
        assert results.successful == 3, "All 3 grades should succeed"
        assert results.failed == 0, "No grades should fail"
        assert len({outcome.backup_path for outcome in results.outcomes}) == 3

    def test_submit_multiple_grades_reports_partial_failures_correctly(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: When some grades fail, both counts are reported accurately.

        SCENARIO:
        - 3 students with grades to submit
        - Canvas accepts the first and third and refuses the second

        EXPECTED:
        - successful: 2, failed: 1, with the failure named in errors

        WHY: Partial failures are common (e.g., one student was dropped
        from the course). The instructor needs accurate counts to know
        which submissions to investigate.
        """
        grades_to_submit = [
            {"student_id": 1001, "score": 100.0},
            {"student_id": 1002, "score": 80.0},
            {"student_id": 1003, "score": 60.0},
        ]
        mock_canvas_client_for_gradebook.submit_grade.side_effect = [True, False, True]

        results = gradebook_manager_with_mock_client.submit_multiple_grades(
            course_id=12345,
            assignment_id=9999,
            grades=grades_to_submit,
        )

        assert results.successful == 2, "2 grades should succeed"
        assert results.failed == 1, "1 grade should fail"
        assert len(results.errors) == 1
        assert "user 1002" in results.errors[0]

    def test_submit_multiple_grades_stops_when_a_backup_fails(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: A failed backup aborts the whole batch.

        WHY: No grade may be written without its backup, so once backups
        stop working the batch must stop rather than carry on unprotected.
        """
        grades_to_submit = [
            {"student_id": 1001, "score": 100.0},
            {"student_id": 1002, "score": 80.0},
        ]
        mock_canvas_client_for_gradebook.get_submission.return_value = None

        with pytest.raises(BackupFailedError):
            gradebook_manager_with_mock_client.submit_multiple_grades(
                course_id=12345, assignment_id=9999, grades=grades_to_submit
            )

        mock_canvas_client_for_gradebook.submit_grade.assert_not_called()

    def test_submit_multiple_grades_dry_run_previews_every_grade(
        self, dry_run_gradebook_manager, mock_canvas_client_for_gradebook
    ):
        """
        TEST: A dry-run batch counts previews and writes nothing.
        """
        grades_to_submit = [
            {"student_id": 1001, "score": 100.0},
            {"student_id": 1002, "score": 80.0},
        ]

        results = dry_run_gradebook_manager.submit_multiple_grades(
            course_id=12345, assignment_id=9999, grades=grades_to_submit
        )

        assert (results.successful, results.failed, results.previewed) == (0, 0, 2)
        mock_canvas_client_for_gradebook.submit_grade.assert_not_called()


# =============================================================================
# TEST CLASS: Grade Validation
# =============================================================================


class TestGradeValidation:
    """
    Tests for validating grade scores before submission.

    WHY: We must catch invalid grades before they reach Canvas. Negative
    scores and scores exceeding the assignment maximum are common data
    entry errors that validation should prevent.
    """

    def test_validate_rejects_negative_score_with_clear_error_message(
        self, gradebook_manager_with_mock_client, sample_assignment_with_100_points
    ):
        """
        TEST: A negative score is rejected with a helpful error message.

        SCENARIO:
        - Assignment has 100 possible points
        - Score is -5.0 (invalid negative value)

        EXPECTED:
        - is_valid: False
        - error_message: "Grade cannot be negative"
        """
        # Act
        is_valid, error_message = gradebook_manager_with_mock_client.validate_grade_submission(
            assignment=sample_assignment_with_100_points,
            score=-5.0,
        )

        # Assert
        assert is_valid is False, "Negative scores must be rejected"
        assert "negative" in error_message.lower(), "Error message should mention 'negative'"

    def test_validate_rejects_score_that_exceeds_maximum_points(
        self, gradebook_manager_with_mock_client, sample_assignment_with_100_points
    ):
        """
        TEST: A score exceeding max points is rejected with helpful context.

        SCENARIO:
        - Assignment has 100 possible points
        - Score is 150.0 (exceeds maximum)

        EXPECTED:
        - is_valid: False
        - error_message mentions the score AND the maximum

        WHY: Entering 150 for a 100-point assignment is a common typo.
        The error message should help the user understand the limit.
        """
        # Act
        is_valid, error_message = gradebook_manager_with_mock_client.validate_grade_submission(
            assignment=sample_assignment_with_100_points,
            score=150.0,
        )

        # Assert
        assert is_valid is False, "Score exceeding max must be rejected"
        assert "150" in error_message, "Error message should include the submitted score"
        assert "100" in error_message, "Error message should include the max points"

    def test_validate_accepts_valid_score_within_allowed_range(
        self, gradebook_manager_with_mock_client, sample_assignment_with_100_points
    ):
        """
        TEST: A valid score within range is accepted with no error.

        SCENARIO:
        - Assignment has 100 possible points
        - Score is 85.0 (within range)

        EXPECTED:
        - is_valid: True
        - error_message: empty string
        """
        # Act
        is_valid, error_message = gradebook_manager_with_mock_client.validate_grade_submission(
            assignment=sample_assignment_with_100_points,
            score=85.0,
        )

        # Assert
        assert is_valid is True, "Valid score should be accepted"
        assert error_message == "", "Error message should be empty for valid score"

    def test_validate_accepts_zero_score_as_valid(
        self, gradebook_manager_with_mock_client, sample_assignment_with_100_points
    ):
        """
        TEST: A score of exactly 0 is accepted (not negative).

        SCENARIO:
        - Student truly earned 0 points (no participation)
        - Score is 0.0

        EXPECTED:
        - is_valid: True
        - error_message: empty string

        WHY: Zero is a valid score for non-participation. The validator
        should not confuse it with a negative score.
        """
        # Act
        is_valid, error_message = gradebook_manager_with_mock_client.validate_grade_submission(
            assignment=sample_assignment_with_100_points,
            score=0.0,
        )

        # Assert
        assert is_valid is True, "Zero score should be valid"
        assert error_message == "", "No error for zero score"

    def test_validate_accepts_score_equal_to_maximum_points(
        self, gradebook_manager_with_mock_client, sample_assignment_with_100_points
    ):
        """
        TEST: A score exactly equal to points_possible is valid.

        SCENARIO:
        - Assignment has 100 possible points
        - Score is exactly 100.0

        EXPECTED:
        - is_valid: True

        WHY: Boundary condition -- must not accidentally reject
        a perfect score due to an off-by-one comparison.
        """
        # Act
        is_valid, error_message = gradebook_manager_with_mock_client.validate_grade_submission(
            assignment=sample_assignment_with_100_points,
            score=100.0,
        )

        # Assert
        assert is_valid is True, "Score equal to max should be accepted"
        assert error_message == "", "No error for perfect score"


# =============================================================================
# TEST CLASS: Grade Comment Generation
# =============================================================================


class TestGradeCommentGeneration:
    """
    Tests for generating formatted grade comments.

    WHY: Comments provide students with feedback about their grade.
    There are two code paths:
      1. Legacy format (no post_details) -- shows totals only
      2. Detailed format (with post_details) -- shows per-post breakdown

    Both paths must produce correct, readable output.
    """

    def test_create_comment_without_post_details_uses_legacy_format(
        self,
        gradebook_manager_with_mock_client,
        sample_analysis_results_for_comment,
        sample_scoring_details_for_comment,
    ):
        """
        TEST: When no participation object is given, use the legacy format.

        SCENARIO:
        - analysis_results has word_count=420 and reply_count=3
        - No participation object provided
        - Comment should show scoring breakdown plus totals

        EXPECTED:
        - Comment includes "Scoring breakdown:"
        - Comment includes total word count (420)
        - Comment includes reply count (3)
        """
        # Act: generate comment without participation (legacy path)
        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=sample_analysis_results_for_comment,
            scoring_details=sample_scoring_details_for_comment,
            participation=None,
        )

        # Assert: verify legacy format fields are present
        assert "Scoring breakdown:" in comment, "Comment should start with scoring breakdown header"
        assert "420" in comment, "Legacy comment should include total word count"
        assert "3" in comment, "Legacy comment should include reply count"

    def test_create_comment_with_post_details_shows_individual_posts(
        self,
        gradebook_manager_with_mock_client,
        sample_analysis_results_for_comment,
        sample_scoring_details_for_comment,
        participation_object_with_post_details,
    ):
        """
        TEST: When a participation object with post_details is provided,
        the comment includes per-post word counts.

        SCENARIO:
        - Participation has 3 posts: 350-word substantial, 60-word reply, 55-word reply
        - Comment should show each post individually

        EXPECTED:
        - Comment includes "Post details:" header
        - Comment includes word counts for each post (350, 60, 55)
        - Comment includes total word count and reply count
        """
        # Act: generate comment with detailed participation
        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=sample_analysis_results_for_comment,
            scoring_details=sample_scoring_details_for_comment,
            participation=participation_object_with_post_details,
        )

        # Assert: verify detailed format fields
        assert "Post details:" in comment, "Detailed comment should include post details header"
        assert "350" in comment, "Should show the 350-word substantial post"
        assert "60 words" in comment, "Should show the 60-word reply"
        assert "55 words" in comment, "Should show the 55-word reply"
        assert "465" in comment, "Should include total word count"

    def test_substantial_post_line_does_not_duplicate_word_count(
        self,
        gradebook_manager_with_mock_client,
        sample_analysis_results_for_comment,
        sample_scoring_details_for_comment,
        participation_object_with_post_details,
    ):
        """
        TEST: The substantial-post line must NOT show the word count
        twice (e.g. "Substantial post (350 words) ✓: 350 words" is
        wrong; "Substantial post ✓: 350 words" is correct).

        WHY: A previous bug embedded the word count inside the post-type
        label AND in the trailing ": N words" suffix, producing a
        confusing duplicate like "(350 words) ✓: 350 words".
        """
        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=sample_analysis_results_for_comment,
            scoring_details=sample_scoring_details_for_comment,
            participation=participation_object_with_post_details,
        )

        # Find the line that describes the substantial post
        substantial_lines = [line for line in comment.splitlines() if "Substantial post" in line]
        assert len(substantial_lines) == 1, "There should be exactly one substantial-post line"

        # The word count "350" should appear exactly once in that line
        substantial_line = substantial_lines[0]
        assert substantial_line.count("350") == 1, (
            f"Word count '350' appears more than once in: {substantial_line!r}"
        )

    def test_comment_uses_computed_largest_not_is_largest_flag(
        self,
        gradebook_manager_with_mock_client,
        sample_analysis_results_for_comment,
        sample_scoring_details_for_comment,
    ):
        """
        TEST: The substantial-post label is determined by the actual
        largest word count, NOT by the is_largest data flag.

        SCENARIO:
        - is_largest flag is on a 50-word post (wrong)
        - The actual largest post is 400 words
        - The 400-word post should still get the substantial label

        WHY: The is_largest flag can be stale or incorrect. The
        comment generator should compute the largest post itself.
        """
        participation_with_wrong_flag = SimpleNamespace(
            post_details=[
                # is_largest is wrongly set on this small post
                {
                    "word_count": 50,
                    "is_largest": True,
                    "is_reply": False,
                    "post_number": 1,
                },
                # This is actually the largest post
                {
                    "word_count": 400,
                    "is_largest": False,
                    "is_reply": False,
                    "post_number": 2,
                },
                {
                    "word_count": 80,
                    "is_largest": False,
                    "is_reply": True,
                    "post_number": 3,
                },
            ],
            total_word_count=530,
            reply_count=1,
        )

        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=sample_analysis_results_for_comment,
            scoring_details=sample_scoring_details_for_comment,
            participation=participation_with_wrong_flag,
        )

        # The 400-word post should get the "Substantial post ✓" label
        substantial_lines = [line for line in comment.splitlines() if "Substantial post" in line]
        assert len(substantial_lines) == 1, "There should be exactly one substantial-post line"
        assert "400 words" in substantial_lines[0], (
            "The 400-word post should be marked as the substantial post"
        )

    def test_create_comment_includes_late_submission_penalty_notice(
        self,
        gradebook_manager_with_mock_client,
        sample_scoring_details_for_comment,
    ):
        """
        TEST: When late_submission is True, the comment includes a penalty notice.

        SCENARIO:
        - analysis_results has late_submission=True
        - Comment should include late penalty notice

        WHY: Students need to see why their grade may be lower than
        expected. The late penalty notice makes grading transparent.
        """
        # Arrange: analysis results with late submission flag
        analysis_results_with_late_penalty = {
            "word_count": 300,
            "reply_count": 2,
            "late_submission": True,
        }

        # Act
        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=analysis_results_with_late_penalty,
            scoring_details=sample_scoring_details_for_comment,
            participation=None,
        )

        # Assert
        assert "Late submission penalty applied" in comment, (
            "Comment should mention the late submission penalty"
        )

    def test_create_comment_scoring_breakdown_shows_correct_point_values(
        self,
        gradebook_manager_with_mock_client,
        sample_analysis_results_for_comment,
        sample_scoring_details_for_comment,
    ):
        """
        TEST: The scoring breakdown section shows the right point values.

        SCENARIO:
        - participation_score: 40.0
        - quality_score: 45.0
        - total_score: 85.0

        EXPECTED:
        - Comment includes each score formatted to one decimal place
        """
        # Act
        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=sample_analysis_results_for_comment,
            scoring_details=sample_scoring_details_for_comment,
            participation=None,
        )

        # Assert: verify each score appears in the comment
        assert "40.0" in comment, "Comment should show participation score of 40.0"
        assert "45.0" in comment, "Comment should show quality score of 45.0"
        assert "85.0" in comment, "Comment should show total score of 85.0"

    def test_create_comment_includes_ai_discussion_block_when_band_exists(
        self,
        gradebook_manager_with_mock_client,
        sample_analysis_results_for_comment,
        sample_scoring_details_for_comment,
    ):
        participation_with_band = SimpleNamespace(
            post_details=[
                {
                    "word_count": 140,
                    "is_largest": True,
                    "is_reply": False,
                    "post_number": 1,
                    "post_role": "research",
                    "timing_status": "on_time",
                },
                {
                    "word_count": 315,
                    "is_largest": False,
                    "is_reply": True,
                    "post_number": 2,
                    "post_role": "restatement",
                    "timing_status": "late_post2",
                },
                {
                    "word_count": 170,
                    "is_largest": False,
                    "is_reply": True,
                    "post_number": 3,
                    "post_role": "reply",
                    "timing_status": "on_time",
                },
            ],
            total_word_count=625,
            reply_count=2,
            band="excellent",
            band_reasons=["Post 2 word count: 315"],
        )

        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=sample_analysis_results_for_comment,
            scoring_details=sample_scoring_details_for_comment,
            participation=participation_with_band,
        )

        assert "AI Discussion Profile:" in comment
        assert "Post 1 (Research): 140 words" in comment
        assert "Post 2 (Restatement): 315 words" in comment
        assert "Band:" not in comment
        assert "Timing Flags: late_post2" in comment

    def test_create_grade_comment_omits_ai_block_for_legacy_posts(
        self,
        gradebook_manager_with_mock_client,
        sample_analysis_results_for_comment,
        sample_scoring_details_for_comment,
    ):
        """Regular discussions never get the AI Discussion Profile block.

        In the legacy profile posts carry ``post_role="extra"`` rather
        than research/restatement/reply.  Inferring AI roles from post
        structure would tell a student they missed a "Post 2
        (Restatement)" that their assignment never asked for.
        """
        legacy_participation = SimpleNamespace(
            post_details=[
                {
                    "word_count": 1226,
                    "is_largest": True,
                    "is_reply": False,
                    "post_number": 1,
                    "post_role": "extra",
                    "parent_relationship": "top_level",
                    "timing_status": "on_time",
                },
                {
                    "word_count": 394,
                    "is_largest": False,
                    "is_reply": True,
                    "post_number": 2,
                    "post_role": "extra",
                    "parent_relationship": "self_reply",
                    "timing_status": "on_time",
                },
            ],
            total_word_count=1620,
            reply_count=1,
            band=None,
            band_reasons=[],
        )

        comment = gradebook_manager_with_mock_client.create_grade_comment(
            analysis_results=sample_analysis_results_for_comment,
            scoring_details=sample_scoring_details_for_comment,
            participation=legacy_participation,
        )

        assert "AI Discussion Profile:" not in comment
        assert "Restatement" not in comment

    def test_create_llm_enhanced_comment_uses_legacy_breakdown_for_legacy_posts(
        self,
        gradebook_manager_with_mock_client,
    ):
        """Regular discussions get a substantial-post + replies breakdown.

        The breakdown's Total is the score the analyzer actually awarded,
        so a student who earned 100 can never be shown a 56/100 tally
        built from AI-discussion roles they were never assigned.
        """
        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "Strong, well-organized post."
        )
        legacy_participation = SimpleNamespace(
            post_details=[
                {
                    "word_count": 1226,
                    "is_largest": True,
                    "is_reply": False,
                    "post_number": 1,
                    "post_role": "extra",
                    "parent_relationship": "top_level",
                    "timing_status": "on_time",
                },
                {
                    "word_count": 394,
                    "is_largest": False,
                    "is_reply": True,
                    "post_number": 2,
                    "post_role": "extra",
                    "parent_relationship": "classmate_reply",
                    "timing_status": "on_time",
                },
            ],
            initial_post_word_count=1226,
            reply_count=1,
            participation_score=80.0,
            quality_score=80.0,
            total_score=80.0,
            band=None,
        )

        comment = gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=legacy_participation,
            discussion_context="Weekly discussion",
            rubric_info={},
            llm_feedback_generator=feedback_generator,
            include_score_breakdown=True,
        )

        assert "Score Breakdown:" in comment
        assert "Substantial post: 60.0/60 points (1226 words)" in comment
        assert "Replies: 20.0/40 points (1 replies)" in comment
        assert "Total: 80.0/100 points" in comment
        assert "Restatement" not in comment

    def test_create_llm_enhanced_comment_uses_ai_role_point_breakdown(
        self,
        gradebook_manager_with_mock_client,
    ):
        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "Solid discussion work."
        )

        participation = SimpleNamespace(
            post_details=[
                {
                    "word_count": 300,
                    "is_largest": True,
                    "is_reply": False,
                    "post_number": 1,
                    "post_role": "research",
                    "timing_status": "on_time",
                },
                {
                    "word_count": 300,
                    "is_largest": False,
                    "is_reply": True,
                    "post_number": 2,
                    "post_role": "restatement",
                    "timing_status": "on_time",
                },
                {
                    "word_count": 150,
                    "is_largest": False,
                    "is_reply": True,
                    "post_number": 3,
                    "post_role": "reply",
                    "timing_status": "on_time",
                },
            ],
            total_score=100.0,
            band="excellent",
        )

        comment = gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=participation,
            discussion_context="AI discussion",
            rubric_info={},
            llm_feedback_generator=feedback_generator,
            include_score_breakdown=True,
        )

        assert "Score Breakdown:" in comment
        assert "Post 1 (Research): 30.0/30 points (300 words)" in comment
        assert "Post 2 (Restatement): 40.0/40 points (300 words)" in comment
        assert "Post 3 (Reply): 30.0/30 points (150 words)" in comment
        assert "AI Band:" not in comment
        assert "Total: 100.0/100 points" in comment

    def test_create_llm_enhanced_comment_uses_ai_breakdown_for_zero_post_student(
        self,
        gradebook_manager_with_mock_client,
    ):
        """Zero-post students on an AI discussion still see the 30/40/30 breakdown."""
        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "No participation recorded."
        )
        participation = SimpleNamespace(
            post_details=[],
            total_score=0.0,
            band=None,
        )

        comment = gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=participation,
            discussion_context="AI discussion",
            rubric_info={"grading_profile": "ai_discussion"},
            llm_feedback_generator=feedback_generator,
            include_score_breakdown=True,
        )

        assert "Score Breakdown:" in comment
        assert "Post 1 (Research): 0.0/30 points (0 words)" in comment
        assert "Post 2 (Restatement): 0.0/40 points (0 words)" in comment
        assert "Post 3 (Reply): 0.0/30 points (0 words)" in comment
        assert "Total: 0.0/100 points" in comment
        assert "Substantial post" not in comment

    def test_llm_role_review_skipped_on_perfect_score(self, gradebook_manager_with_mock_client):
        """A perfect 100 should never trigger an LLM role-verification call."""
        verify_calls: list = []

        def fake_verify(student):
            verify_calls.append(student)
            return None

        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "Great work.",
            verify_role_assignment=fake_verify,
        )
        participation = SimpleNamespace(
            user_name="Perfect Student",
            post_details=[
                {
                    "post_number": 1,
                    "word_count": 300,
                    "post_role": "research",
                    "parent_relationship": "top_level",
                    "text_snippet": "...",
                    "is_largest": True,
                    "is_reply": False,
                    "timing_status": "on_time",
                },
                {
                    "post_number": 2,
                    "word_count": 300,
                    "post_role": "restatement",
                    "parent_relationship": "self_reply",
                    "text_snippet": "...",
                    "is_largest": False,
                    "is_reply": True,
                    "timing_status": "on_time",
                },
                {
                    "post_number": 3,
                    "word_count": 150,
                    "post_role": "reply",
                    "parent_relationship": "classmate_reply",
                    "text_snippet": "...",
                    "is_largest": False,
                    "is_reply": True,
                    "timing_status": "on_time",
                },
            ],
            total_score=100.0,
            band="excellent",
            band_reasons=[],
        )

        gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=participation,
            discussion_context="AI discussion",
            rubric_info={"min_word_count": 300},
            llm_feedback_generator=feedback_generator,
        )

        assert verify_calls == [], "LLM role review must not run on a 100"

    def test_llm_role_review_skipped_on_zero_score(self, gradebook_manager_with_mock_client):
        """A 0 (no posts) should also skip the LLM role-verification call."""
        verify_calls: list = []

        def fake_verify(student):
            verify_calls.append(student)
            return None

        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "No posts found.",
            verify_role_assignment=fake_verify,
        )
        participation = SimpleNamespace(
            user_name="Missing Student",
            post_details=[],
            total_score=0.0,
            band="unsatisfactory",
            band_reasons=[],
        )

        gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=participation,
            discussion_context="AI discussion",
            rubric_info={"min_word_count": 300},
            llm_feedback_generator=feedback_generator,
        )

        assert verify_calls == [], "LLM role review must not run on a 0"

    def test_llm_role_review_regrades_when_mapping_raises_score(
        self, gradebook_manager_with_mock_client
    ):
        """When the LLM proposes a higher-scoring mapping, apply it."""
        # Current (wrong) mapping: Post 1 short -> research (12 pts),
        # Post 2 long -> restatement (40 pts), Post 3 medium -> reply (30 pts).
        # Total = 82.0
        # LLM-suggested swap: Post 2 -> research (30 pts capped),
        # Post 1 -> restatement (40 pts capped — 305 words >= 300),
        # Post 3 -> reply (30 pts) → 100.
        participation = SimpleNamespace(
            user_name="Mislabeled Student",
            post_details=[
                {
                    "post_number": 1,
                    "word_count": 305,
                    "post_role": "research",
                    "parent_relationship": "top_level",
                    "text_snippet": "intro",
                    "is_largest": False,
                    "is_reply": False,
                    "timing_status": "on_time",
                },
                {
                    "post_number": 2,
                    "word_count": 600,
                    "post_role": "restatement",
                    "parent_relationship": "self_reply",
                    "text_snippet": "deep dive",
                    "is_largest": True,
                    "is_reply": True,
                    "timing_status": "on_time",
                },
                {
                    "post_number": 3,
                    "word_count": 150,
                    "post_role": "reply",
                    "parent_relationship": "classmate_reply",
                    "text_snippet": "reply",
                    "is_largest": False,
                    "is_reply": True,
                    "timing_status": "on_time",
                },
            ],
            total_score=82.0,
            participation_score=82.0,
            quality_score=82.0,
            band="good",
            band_reasons=[],
            meets_requirements=True,
        )

        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "Nice analysis.",
            verify_role_assignment=lambda student: {
                "research": 2,
                "restatement": 1,
                "reply": 3,
                "rationale": "Post 2 is the deepest research content.",
            },
        )

        comment = gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=participation,
            discussion_context="AI discussion",
            rubric_info={"min_word_count": 300},
            llm_feedback_generator=feedback_generator,
        )

        # Score was raised and reflected in the comment.
        assert participation.total_score == 100.0
        assert participation.band == "excellent"
        assert "LLM role review adjusted" in comment
        assert "Post 2 is the deepest research content." in comment
        # Score Breakdown should now show the swapped word counts.
        assert "Post 1 (Research): 30.0/30 points (600 words)" in comment
        assert "Post 2 (Restatement): 40.0/40 points (305 words)" in comment
        # post_details roles got rewritten.
        roles_by_number = {pd["post_number"]: pd["post_role"] for pd in participation.post_details}
        assert roles_by_number == {1: "restatement", 2: "research", 3: "reply"}

    def test_llm_role_review_does_not_lower_score(self, gradebook_manager_with_mock_client):
        """If the LLM's mapping would lower the score, keep the original."""
        participation = SimpleNamespace(
            user_name="Already Optimal",
            post_details=[
                {
                    "post_number": 1,
                    "word_count": 305,
                    "post_role": "research",
                    "parent_relationship": "top_level",
                    "text_snippet": "intro",
                    "is_largest": False,
                    "is_reply": False,
                    "timing_status": "on_time",
                },
                {
                    "post_number": 2,
                    "word_count": 320,
                    "post_role": "restatement",
                    "parent_relationship": "self_reply",
                    "text_snippet": "restate",
                    "is_largest": True,
                    "is_reply": True,
                    "timing_status": "on_time",
                },
                {
                    "post_number": 3,
                    "word_count": 160,
                    "post_role": "reply",
                    "parent_relationship": "classmate_reply",
                    "text_snippet": "reply",
                    "is_largest": False,
                    "is_reply": True,
                    "timing_status": "on_time",
                },
            ],
            total_score=100.0,
            participation_score=100.0,
            quality_score=100.0,
            band="excellent",
            band_reasons=[],
            meets_requirements=True,
        )
        # Force entry into the LLM path by claiming 99 (between 0 and 100).
        participation.total_score = 99.0

        # LLM proposes a mapping that would score *lower* (drop the reply).
        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "Feedback.",
            verify_role_assignment=lambda student: {
                "research": 1,
                "restatement": 2,
                "reply": None,
                "rationale": "Could not find a classmate reply.",
            },
        )

        comment = gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=participation,
            discussion_context="AI discussion",
            rubric_info={"min_word_count": 300},
            llm_feedback_generator=feedback_generator,
        )

        # Score must NOT be lowered, no review note in the comment.
        assert participation.total_score == 99.0
        assert "LLM role review adjusted" not in comment

    def test_llm_role_review_handles_none_mapping(self, gradebook_manager_with_mock_client):
        """If the LLM call fails / returns None, fall back to current grade."""
        participation = SimpleNamespace(
            user_name="LLM Failed",
            post_details=[
                {
                    "post_number": 1,
                    "word_count": 200,
                    "post_role": "research",
                    "parent_relationship": "top_level",
                    "text_snippet": "...",
                    "is_largest": True,
                    "is_reply": False,
                    "timing_status": "on_time",
                },
                {
                    "post_number": 2,
                    "word_count": 100,
                    "post_role": "restatement",
                    "parent_relationship": "self_reply",
                    "text_snippet": "...",
                    "is_largest": False,
                    "is_reply": True,
                    "timing_status": "on_time",
                },
            ],
            total_score=45.0,
            participation_score=45.0,
            quality_score=45.0,
            band="satisfactory",
            band_reasons=[],
            meets_requirements=False,
        )

        feedback_generator = SimpleNamespace(
            generate_student_feedback=lambda **_: "Feedback.",
            verify_role_assignment=lambda student: None,
        )

        comment = gradebook_manager_with_mock_client.create_llm_enhanced_comment(
            student_participation=participation,
            discussion_context="AI discussion",
            rubric_info={"min_word_count": 300},
            llm_feedback_generator=feedback_generator,
        )

        assert participation.total_score == 45.0
        assert "LLM role review adjusted" not in comment


# =============================================================================
# TEST CLASS: Assignment Lookup
# =============================================================================


class TestAssignmentLookupFromDiscussion:
    """
    Tests for finding the Canvas assignment linked to a discussion topic.

    WHY: Before we can submit grades, we need to find which assignment
    corresponds to the discussion. This lookup must handle both found
    and not-found cases.
    """

    def test_get_assignment_returns_data_when_discussion_has_linked_assignment(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: When a discussion has a linked assignment, return it.

        SCENARIO:
        - Canvas client returns assignment data for the discussion
        - GradebookManager should pass it through

        EXPECTED:
        - Returns the assignment dict with id and points_possible
        """
        # Act
        assignment = gradebook_manager_with_mock_client.get_assignment_for_discussion(
            course_id=12345,
            discussion_id=67890,
        )

        # Assert
        assert assignment is not None, "Should return assignment data"
        assert assignment["id"] == 9999, "Assignment ID should match"
        assert assignment["points_possible"] == 100, "Points possible should match"

    def test_get_assignment_returns_none_when_no_linked_assignment_exists(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: When no assignment is linked, return None.

        SCENARIO:
        - Canvas client returns None (discussion is ungraded)
        - GradebookManager should return None

        WHY: Not all discussions are graded assignments. We must handle
        this gracefully instead of crashing.
        """
        # Arrange: make the canvas client return None
        mock_canvas_client_for_gradebook.get_assignment_by_discussion.return_value = None

        # Act
        assignment = gradebook_manager_with_mock_client.get_assignment_for_discussion(
            course_id=12345,
            discussion_id=67890,
        )

        # Assert
        assert assignment is None, "Should return None when no assignment is linked"


# =============================================================================
# TEST CLASS: Current Grade Retrieval
# =============================================================================


class TestCurrentGradeRetrieval:
    """
    Tests for fetching a student's current grade on an assignment.

    WHY: Before overwriting a grade, the UI often shows the current
    grade for confirmation. This must handle both graded and ungraded
    submissions.
    """

    def test_get_current_grade_returns_score_when_submission_exists(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: Returns the numeric score for an existing submission.

        SCENARIO:
        - get_submission returns a submission with score=75.0
        - get_current_grade should return 75.0
        """
        mock_canvas_client_for_gradebook.get_submission.return_value = {"score": 75.0}

        # Act
        score = gradebook_manager_with_mock_client.get_current_grade(
            course_id=12345,
            assignment_id=9999,
            student_id=1001,
        )

        # Assert
        assert score == 75.0, "Should return the current score"

    def test_get_current_grade_returns_none_when_no_submission_exists(
        self, gradebook_manager_with_mock_client, mock_canvas_client_for_gradebook
    ):
        """
        TEST: Returns None when the student has no submission yet.

        SCENARIO:
        - get_submission returns None (no submission)
        - get_current_grade should return None

        WHY: Not all students have submitted yet. Returning None lets
        the caller distinguish "no submission" from "score is 0".
        """
        # Arrange: Canvas has no such submission
        mock_canvas_client_for_gradebook.get_submission.return_value = None

        # Act
        score = gradebook_manager_with_mock_client.get_current_grade(
            course_id=12345,
            assignment_id=9999,
            student_id=1001,
        )

        # Assert
        assert score is None, "Should return None when no submission exists"
