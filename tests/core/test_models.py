"""
Tests for Pydantic Data Models
===============================

PURPOSE:
--------
These tests verify that our Pydantic models work correctly. Models are the
data structures that hold information about courses, discussions, students, etc.

WHAT WE'RE TESTING:
-------------------
1. Models can be created with valid data
2. Validators work correctly (e.g., word count calculation)
3. Invalid data is rejected
4. Default values are set properly
5. Serialization/deserialization works

WHY THIS MATTERS:
-----------------
If our models are broken, everything else fails. These are foundational tests
that ensure our data structures are solid.
"""

import pytest
from pydantic import ValidationError

from coursewright.core.canvas.models import (
    CanvasCourse,
    CanvasDiscussionEntry,
    DiscussionRubric,
    ExportConfig,
    GradingCriteria,
    StudentParticipation,
)

# =============================================================================
# BASIC MODEL CREATION TESTS
# =============================================================================
# These tests verify that models can be created with valid data


class TestCanvasCourse:
    """
    Tests for the CanvasCourse model.

    PATTERN: Group related tests in a class
    WHY: Keeps tests organized and makes it clear what's being tested
    """

    def test_create_valid_course(self):
        """
        TEST: Can we create a course with all required fields?

        ARRANGE: Set up the test data (course properties)
        ACT: Create the course object
        ASSERT: Verify it was created correctly
        """
        # Arrange: Prepare test data
        course_data = {"id": 123, "name": "Introduction to Testing", "course_code": "TEST-101"}

        # Act: Create the course
        course = CanvasCourse(**course_data)

        # Assert: Verify the course was created correctly
        assert course.id == 123
        assert course.name == "Introduction to Testing"
        assert course.course_code == "TEST-101"

    def test_course_with_defaults(self):
        """
        TEST: Do default values get set correctly?

        WHY: Many fields are optional with defaults. We need to verify
        that if we don't provide them, the defaults are used.
        """
        # Create course with minimal data
        course = CanvasCourse(id=123, name="Test Course")

        # Verify defaults were set
        assert course.total_students == 0  # Should default to 0
        assert course.workflow_state == "available"  # Should default to "available"
        assert not course.is_public  # Should default to False


class TestCanvasDiscussionEntry:
    """
    Tests for CanvasDiscussionEntry model.

    This model has a validator that calculates word count from HTML message.
    We need to test that this validator works correctly!
    """

    def test_word_count_calculation(self):
        """
        TEST: Does the word count validator calculate correctly?

        WHY: This is critical! The entire grading system depends on
        accurate word counts. If this is wrong, grades are wrong.

        WHAT IT TESTS:
        - HTML tags are stripped before counting
        - Words are counted correctly
        - The validator runs automatically when creating the entry
        """
        # Arrange: Create a message with known word count
        # Message has HTML tags but should count only words
        message = "<p>This is a test message with exactly ten words here</p>"

        # Act: Create entry (validator should calculate word count)
        entry = CanvasDiscussionEntry(
            id=1,
            user_id=123,
            discussion_id=456,
            message=message,
            word_count=0,  # We provide 0, but validator should override this
        )

        # Assert: Verify word count was calculated (should be 10)
        assert entry.word_count == 10, "Should count 10 words after stripping HTML"

    def test_word_count_with_empty_message(self):
        """
        TEST: What happens with an empty message?

        WHY: Edge case testing! What if a post has no message?
        Should be 0 words, not an error.
        """
        entry = CanvasDiscussionEntry(
            id=1, user_id=123, discussion_id=456, message="", word_count=0
        )

        assert entry.word_count == 0

    def test_word_count_strips_html(self):
        """
        TEST: Are HTML tags properly removed before counting?

        WHY: Canvas messages are HTML. We don't want to count tags as words!
        """
        # Message with lots of HTML but only 3 real words
        message = "<div><p><strong>Just three words</strong></p></div>"

        entry = CanvasDiscussionEntry(
            id=1, user_id=123, discussion_id=456, message=message, word_count=0
        )

        assert entry.word_count == 3


class TestStudentParticipation:
    """
    Tests for StudentParticipation model.

    This model holds all the data about how a student participated
    in a discussion. It's used extensively in grading.
    """

    def test_create_student_participation(self, excellent_student):
        """
        TEST: Can we create a StudentParticipation object?

        NOTE: We use a fixture (excellent_student) here!
        pytest automatically provides this from conftest.py
        """
        # The fixture has already created the student for us
        assert excellent_student.user_id == 1001
        assert excellent_student.user_name == "Student A"
        assert excellent_student.meets_requirements

    def test_student_with_no_posts(self, no_participation_student):
        """
        TEST: Does a student with no posts have correct data?

        WHY: We need to handle students who don't participate at all.
        """
        student = no_participation_student

        assert student.total_posts == 0
        assert student.total_word_count == 0
        assert not student.meets_requirements
        assert "No posts found" in student.missing_requirements


# =============================================================================
# VALIDATION TESTS
# =============================================================================
# These tests verify that invalid data is rejected


class TestDiscussionRubric:
    """
    Tests for DiscussionRubric model.

    This model has validators that ensure rubric data is valid.
    """

    def test_create_valid_rubric(self, sample_rubric):
        """
        TEST: Can we create a rubric with valid data?
        """
        assert sample_rubric.name == "Standard Discussion Rubric"
        assert sample_rubric.total_points == 100.0
        assert sample_rubric.min_word_count == 300

    def test_total_points_must_be_positive(self):
        """
        TEST: Does the validator reject negative points?

        WHY: Total points must be > 0. The validator should catch this!

        EXPECTED: ValidationError should be raised
        """
        # Use pytest.raises to verify an exception is raised
        with pytest.raises(ValidationError) as exc_info:
            DiscussionRubric(
                name="Bad Rubric",
                total_points=-10.0,  # Invalid! Should trigger validator
                min_word_count=150,
                min_replies=2,
            )

        # Verify the error message mentions "greater than 0"
        assert "greater than 0" in str(exc_info.value)

    def test_zero_points_rejected(self):
        """
        TEST: Zero points should also be rejected (must be > 0, not >= 0)
        """
        with pytest.raises(ValidationError):
            DiscussionRubric(
                name="Zero Points Rubric",
                total_points=0.0,  # Invalid!
                min_word_count=150,
                min_replies=2,
            )


class TestExportConfig:
    """
    Tests for ExportConfig model.

    This model has a validator that creates the output directory if it doesn't exist.
    """

    def test_output_directory_created(self, tmp_path):
        """
        TEST: Does the validator create the output directory?

        WHY: The validator should automatically create the directory
        when we set output_directory.

        NOTE: We use tmp_path (pytest built-in fixture) to get a
        temporary directory that's automatically cleaned up.
        """
        # Arrange: Create path to a non-existent directory
        test_dir = tmp_path / "test_exports"
        assert not test_dir.exists(), "Directory shouldn't exist yet"

        # Act: Create ExportConfig (should create directory)
        ExportConfig(output_directory=str(test_dir))

        # Assert: Directory should now exist
        assert test_dir.exists(), "Validator should have created the directory"


@pytest.fixture
def sample_rubric() -> DiscussionRubric:
    """
    Creates a sample grading rubric for testing.

    WHY: The grading algorithm needs a rubric. This provides a standard
    rubric that tests can use and modify as needed.
    """
    return DiscussionRubric(
        name="Standard Discussion Rubric",
        description="Standard rubric for testing",
        total_points=100.0,
        min_word_count=300,
        min_replies=2,
        participation_points=50.0,
        quality_points=50.0,
        criteria=[
            GradingCriteria(
                name="Substantial Post",
                description="Quality and depth of main post",
                min_words=300,
                points=60.0,
            ),
            GradingCriteria(
                name="Peer Responses",
                description="Meaningful responses to peers",
                required_replies=2,
                points=40.0,
            ),
        ],
    )


@pytest.fixture
def excellent_student() -> StudentParticipation:
    """
    Student who exceeds all requirements.

    WHY: Test that students who do everything right get full credit.

    CHARACTERISTICS:
    - 350 word substantial post (exceeds 300 minimum)
    - 3 replies (exceeds 2 minimum)
    - Should receive 100 points
    """
    return StudentParticipation(
        user_id=1001,
        user_name="Student A",
        initial_post=True,
        initial_post_word_count=350,
        reply_count=3,
        total_word_count=500,
        total_posts=4,
        participation_score=100.0,
        quality_score=100.0,
        total_score=100.0,
        meets_requirements=True,
        post_details=[
            {
                "word_count": 350,
                "is_reply": False,
                "is_initial": True,
                "is_largest": True,
                "meets_word_requirement": True,
            },
            {"word_count": 50, "is_reply": True, "is_initial": False, "is_largest": False},
            {"word_count": 50, "is_reply": True, "is_initial": False, "is_largest": False},
            {"word_count": 50, "is_reply": True, "is_initial": False, "is_largest": False},
        ],
    )


@pytest.fixture
def no_participation_student() -> StudentParticipation:
    """
    Student who didn't participate at all.

    WHY: Test that students who don't post get 0 points.

    CHARACTERISTICS:
    - No posts
    - Should receive 0 points
    """
    return StudentParticipation(
        user_id=1004,
        user_name="Student D",
        initial_post=False,
        initial_post_word_count=0,
        reply_count=0,
        total_word_count=0,
        total_posts=0,
        participation_score=0.0,
        quality_score=0.0,
        total_score=0.0,
        meets_requirements=False,
        missing_requirements=["No posts found"],
    )
