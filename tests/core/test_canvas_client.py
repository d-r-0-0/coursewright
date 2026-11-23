"""
Tests for Canvas API Client (``canvas/client.py``)
====================================================

PURPOSE:
--------
These tests verify the ``EnhancedCanvasAPI`` class -- the central gateway
between Coursewright and the Canvas LMS REST API.  Every data-fetch or
grade-submit ultimately flows through this client, so correctness here
prevents silent data loss, mis-grading, and confusing user-facing errors.

WHAT WE'RE TESTING:
-------------------
1. Course listing — single page and paginated multi-page responses
2. Single course retrieval — success and not-found paths
3. Discussion listing — regular topics and announcements
4. Single discussion topic retrieval — success and not-found
5. Discussion entry retrieval — flat entries, nested view, and fallback
6. Course user listing — with default and custom enrollment types
7. Single user retrieval — success and not-found
8. Course search by name pattern — case-insensitive matching
9. Grade submission — single and batch, success and failure
10. Rate-limit handling — 429 retry
11. Network failure handling — ``RequestException`` path
12. Pagination — Link-header parsing with multi-page result sets
13. ``load_api_key`` / ``create_canvas_client`` helpers

WHY MOCK ``requests.Session``:
-------------------------------
We patch ``requests.Session`` so tests:
  - Run instantly (no network round-trips)
  - Need no real Canvas credentials
  - Return predictable, controllable responses
  - Work regardless of Canvas uptime

Author: Claude Code Agent
Date: February 2026
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest

from coursewright.core.canvas.client import (
    CanvasAuthError,
    CanvasRequestError,
    EnhancedCanvasAPI,
    SessionAuth,
    create_canvas_client,
    load_api_key,
)
from coursewright.core.canvas.models import CanvasDiscussion, CanvasDiscussionEntry

# =============================================================================
# HELPER — Build a fake ``requests.Response``
# =============================================================================


def _make_response(
    status_code: int = 200,
    json_data=None,
    headers: dict | None = None,
    text: str = "",
):
    """
    Build a ``Mock`` that quacks like a ``requests.Response``.

    WHY:  Every ``_make_request`` call returns a Response.  Instead of
    building the same boilerplate in every test we centralise it here.
    """
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_data if json_data is not None else {}
    response.headers = headers or {}
    response.text = text
    return response


# =============================================================================
# FIXTURE — A pre-wired ``EnhancedCanvasAPI`` with a mocked session
# =============================================================================


@pytest.fixture
def canvas_client():
    """
    Create an ``EnhancedCanvasAPI`` whose internal ``requests.Session``
    is replaced with a ``MagicMock``.

    WHY:  We need to intercept every HTTP call the client makes so we
    can feed it canned JSON and assert on the URLs it builds.
    """
    with patch("coursewright.core.canvas.client.requests.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        # Session.headers is a dict-like, provide one
        mock_session.headers = {}
        client = EnhancedCanvasAPI(
            base_url="https://canvas.example.edu/",
            access_token="fake-token-abc123",
        )
    # Expose the mock for per-test customisation
    client._mock_session = mock_session
    return client


# =============================================================================
# 1.  COURSE LISTING
# =============================================================================


class TestGetCourses:
    """Verify ``get_courses`` builds the right URL and handles pagination."""

    def test_returns_courses_from_single_page(self, canvas_client):
        """A single page of courses is returned as-is."""
        courses_json = [
            {"id": 1, "name": "Intro to CS", "course_code": "SYN-101"},
            {"id": 2, "name": "Data Structures", "course_code": "SYN-201"},
        ]
        canvas_client.get_last_student_participation_by_course = Mock(return_value={})
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=courses_json,
        )

        courses = canvas_client.get_courses()

        assert len(courses) == 2
        assert courses[0]["name"] == "Intro to CS"
        assert courses[1]["course_code"] == "SYN-201"

    def test_returns_empty_list_when_no_courses(self, canvas_client):
        """An instructor with no courses gets an empty list, not an error."""
        canvas_client.get_last_student_participation_by_course = Mock(return_value={})
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=[],
        )

        courses = canvas_client.get_courses()

        assert courses == []


# =============================================================================
# 2.  SINGLE COURSE RETRIEVAL
# =============================================================================


class TestGetCourse:
    """Verify ``get_course`` for success and not-found."""

    def test_returns_course_dict_on_success(self, canvas_client):
        course_json = {"id": 42, "name": "Ethics 101", "course_code": "DEMO-101"}
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=course_json,
        )

        result = canvas_client.get_course(42)

        assert result is not None
        assert result["id"] == 42
        assert result["name"] == "Ethics 101"

    def test_returns_none_on_404(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=404,
            text="not found",
        )

        result = canvas_client.get_course(9999)

        assert result is None


# =============================================================================
# 3.  DISCUSSION LISTING
# =============================================================================


class TestGetDiscussions:
    """Verify ``get_discussions`` parses topics and optional announcements."""

    def test_returns_parsed_discussion_objects(self, canvas_client):
        """Regular discussion topics are returned as ``CanvasDiscussion``s."""
        topics_json = [
            {
                "id": 100,
                "title": "Week 1 Discussion",
                "message": "Introduce yourself.",
                "posted_at": "2026-01-15T12:00:00Z",
                "discussion_subentry_count": 12,
                "unread_count": 0,
                "assignment_id": 500,
                "discussion_type": "threaded",
                "html_url": "https://canvas.example.edu/d/100",
                "require_initial_post": True,
                "published": True,
                "locked": False,
            },
        ]
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=topics_json,
        )

        discussions = canvas_client.get_discussions(course_id=1)

        assert len(discussions) == 1
        discussion = discussions[0]
        assert isinstance(discussion, CanvasDiscussion)
        assert discussion.id == 100
        assert discussion.title == "Week 1 Discussion"
        assert discussion.replies_count == 12
        assert discussion.is_announcement is False

    def test_includes_announcements_when_requested(self, canvas_client):
        """When ``include_announcements=True`` a second API call is made."""
        # First call returns regular topics, second returns announcements
        topics_json = [
            {
                "id": 100,
                "title": "Regular Discussion",
                "posted_at": "2026-01-15T12:00:00Z",
                "discussion_subentry_count": 0,
                "unread_count": 0,
                "published": True,
                "locked": False,
            },
        ]
        announcements_json = [
            {
                "id": 200,
                "title": "Important Announcement",
                "posted_at": "2026-01-20T08:00:00Z",
                "discussion_subentry_count": 3,
                "unread_count": 1,
                "published": True,
                "locked": False,
            },
        ]
        canvas_client._mock_session.request.side_effect = [
            _make_response(json_data=topics_json),
            _make_response(json_data=announcements_json),
        ]

        discussions = canvas_client.get_discussions(course_id=1, include_announcements=True)

        assert len(discussions) == 2
        # The first should be a regular discussion, the second an announcement
        assert discussions[0].is_announcement is False
        assert discussions[1].is_announcement is True
        assert discussions[1].title == "Important Announcement"


# =============================================================================
# 4.  SINGLE DISCUSSION TOPIC
# =============================================================================


class TestGetDiscussionTopic:
    """Verify ``get_discussion_topic``."""

    def test_returns_discussion_on_success(self, canvas_client):
        topic_json = {
            "id": 100,
            "title": "Week 2 Discussion",
            "message": "Discuss ethics.",
            "posted_at": "2026-01-22T10:00:00Z",
            "discussion_subentry_count": 7,
            "unread_count": 2,
            "assignment_id": 501,
            "is_announcement": False,
        }
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=topic_json,
        )

        result = canvas_client.get_discussion_topic(course_id=1, topic_id=100)

        assert result is not None
        assert isinstance(result, CanvasDiscussion)
        assert result.id == 100
        assert result.title == "Week 2 Discussion"

    def test_returns_none_on_404(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=404,
            text="not found",
        )

        result = canvas_client.get_discussion_topic(course_id=1, topic_id=9999)

        assert result is None


# =============================================================================
# 5.  DISCUSSION ENTRIES
# =============================================================================


class TestGetDiscussionEntries:
    """Verify ``get_discussion_entries`` — view endpoint, flat fallback, and replies."""

    def test_flat_entries_returned_when_view_disabled(self, canvas_client):
        """With ``include_replies=False`` only flat entries are fetched."""
        entries_json = [
            {
                "id": 1000,
                "user_id": 10,
                "message": "Hello world from student A",
                "created_at": "2026-01-15T14:00:00Z",
                "updated_at": "2026-01-15T14:00:00Z",
                "user_name": "Student A",
            },
            {
                "id": 1001,
                "user_id": 11,
                "message": "Greetings from student B",
                "created_at": "2026-01-15T15:00:00Z",
                "updated_at": "2026-01-15T15:00:00Z",
                "parent_id": 1000,
                "user_name": "Student B",
            },
        ]
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=entries_json,
        )

        entries = canvas_client.get_discussion_entries(
            course_id=1,
            topic_id=100,
            include_replies=False,
        )

        assert len(entries) == 2
        assert isinstance(entries[0], CanvasDiscussionEntry)
        assert entries[0].user_name == "Student A"
        assert entries[1].parent_id == 1000

    def test_view_endpoint_extracts_nested_replies(self, canvas_client):
        """The structured /view endpoint returns entries with nested replies."""
        view_json = {
            "participants": [
                {"id": 10, "display_name": "Student A"},
                {"id": 11, "display_name": "Student B"},
            ],
            "view": [
                {
                    "id": 1000,
                    "user_id": 10,
                    "message": "Top-level post by A",
                    "created_at": "2026-01-15T14:00:00Z",
                    "updated_at": "2026-01-15T14:00:00Z",
                    "replies": [
                        {
                            "id": 1001,
                            "user_id": 11,
                            "message": "Reply by B",
                            "created_at": "2026-01-15T15:00:00Z",
                            "updated_at": "2026-01-15T15:00:00Z",
                            "replies": [],
                        },
                    ],
                },
            ],
        }
        # The view endpoint now shares the request choke point.
        mock_view_response = _make_response(json_data=view_json)
        mock_view_response.raise_for_status = Mock()
        canvas_client._mock_session.request.return_value = mock_view_response

        entries = canvas_client.get_discussion_entries(
            course_id=1,
            topic_id=100,
            include_replies=True,
        )

        assert len(entries) == 2
        # Parent entry
        assert entries[0].user_name == "Student A"
        assert entries[0].parent_id is None
        # Nested reply
        assert entries[1].user_name == "Student B"
        assert entries[1].parent_id == 1000

    def test_falls_back_to_flat_entries_when_view_fails(self, canvas_client):
        """If the /view endpoint raises, the client falls back to flat /entries."""
        # Flat entries fallback
        entries_json = [
            {
                "id": 2000,
                "user_id": 20,
                "message": "Fallback entry",
                "created_at": "2026-01-15T14:00:00Z",
                "updated_at": "2026-01-15T14:00:00Z",
                "user_name": "Fallback User",
            },
        ]
        canvas_client._mock_session.request.side_effect = [
            Exception("view endpoint down"),
            _make_response(json_data=entries_json),
        ]

        entries = canvas_client.get_discussion_entries(
            course_id=1,
            topic_id=100,
            include_replies=True,
        )

        assert len(entries) == 1
        assert entries[0].user_name == "Fallback User"


# =============================================================================
# 6.  COURSE USERS
# =============================================================================


class TestGetCourseUsers:
    """Verify ``get_course_users`` returns ``CanvasUser`` objects."""

    def test_returns_user_list(self, canvas_client):
        users_json = [
            {"id": 10, "name": "Student A", "sortable_name": "A, Student"},
            {
                "id": 11,
                "name": "Student B",
                "sortable_name": "B, Student",
                "email": "student-b@example.edu",
            },
        ]
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=users_json,
        )

        users = canvas_client.get_course_users(course_id=1)

        assert len(users) == 2
        assert users[0].name == "Student A"
        assert users[1].email == "student-b@example.edu"


# =============================================================================
# 7.  SINGLE USER
# =============================================================================


class TestGetUser:
    """Verify ``get_user``."""

    def test_returns_user_on_success(self, canvas_client):
        user_json = {"id": 10, "name": "Student A", "login_id": "student-a"}
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=user_json,
        )

        user = canvas_client.get_user(10)

        assert user is not None
        assert user.name == "Student A"
        assert user.login_id == "student-a"

    def test_returns_none_on_404(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=404,
            text="not found",
        )

        user = canvas_client.get_user(9999)

        assert user is None


# =============================================================================
# 8.  COURSE SEARCH BY NAME
# =============================================================================


class TestFindCourseByName:
    """Verify ``find_course_by_name`` case-insensitive matching."""

    def test_matches_on_course_name(self, canvas_client):
        courses_json = [
            {"id": 1, "name": "Introduction to SYN", "course_code": "SYN-101"},
            {"id": 2, "name": "History of Art", "course_code": "EXAMPLE-200"},
        ]
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=courses_json,
        )

        matches = canvas_client.find_course_by_name("syn")

        assert len(matches) == 1
        assert matches[0]["id"] == 1

    def test_matches_on_course_code(self, canvas_client):
        courses_json = [
            {"id": 1, "name": "Intro to Computers", "course_code": "SYN-101"},
            {"id": 2, "name": "Philosophy", "course_code": "DEMO-301"},
        ]
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=courses_json,
        )

        matches = canvas_client.find_course_by_name("demo")

        assert len(matches) == 1
        assert matches[0]["id"] == 2


# =============================================================================
# 9.  GRADE SUBMISSION
# =============================================================================


class TestSubmitGrade:
    """Verify ``submit_grade`` and ``submit_multiple_grades``."""

    def test_single_grade_success(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=200,
        )

        result = canvas_client.submit_grade(
            course_id=1,
            assignment_id=500,
            user_id=10,
            score=95.0,
            comment="Good work!",
        )

        assert result is True

    def test_single_grade_failure_returns_false(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=403,
            text="forbidden",
        )

        result = canvas_client.submit_grade(
            course_id=1,
            assignment_id=500,
            user_id=10,
            score=95.0,
        )

        assert result is False

    def test_batch_submission_counts_successes_and_failures(self, canvas_client):
        """``submit_multiple_grades`` tallies successes vs failures."""
        # First two succeed, third fails
        canvas_client._mock_session.request.side_effect = [
            _make_response(status_code=200),
            _make_response(status_code=200),
            _make_response(status_code=500, text="server error"),
        ]

        grades = [
            {"user_id": 10, "score": 90.0},
            {"user_id": 11, "score": 85.0, "comment": "Nice job"},
            {"user_id": 12, "score": 70.0},
        ]

        result = canvas_client.submit_multiple_grades(
            course_id=1,
            assignment_id=500,
            grades=grades,
        )

        assert result["successful"] == 2
        assert result["failed"] == 1


class TestAuthErrors:
    """Verify invalid or expired token handling raises clear auth errors."""

    def test_invalid_access_token_raises_canvas_auth_error(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=401,
            text="Invalid access token",
        )

        with pytest.raises(CanvasAuthError) as auth_error_info:
            canvas_client._make_request("GET", "https://canvas.example.edu/api/v1/courses")

        assert "generate a new access token" in str(auth_error_info.value)

    def test_expired_browser_session_raises_canvas_auth_error(self, canvas_client):
        """A dead browser session (401 'user authorization required') must
        raise CanvasAuthError so the stale session is discarded and the browser
        login flow can be re-triggered instead of silently returning no data."""
        unauthenticated_body = (
            '{"status":"unauthenticated","errors":[{"message":"user authorization required"}]}'
        )
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=401,
            text=unauthenticated_body,
        )

        with pytest.raises(CanvasAuthError) as auth_error_info:
            canvas_client._make_request("GET", "https://canvas.example.edu/api/v1/courses")

        assert "login-browser" in str(auth_error_info.value)


class TestZeroOutUngradedPastDue:
    """Tests for ``EnhancedCanvasAPI.zero_out_ungraded_past_due``.

    Key invariant: only submissions where ``score is None`` get a 0 posted.
    Submissions already graded (any float, including 0.0) must not be touched.
    """

    def test_ungraded_submission_is_zeroed(self, canvas_client):
        """A submission with score=None should receive a PUT with grade=0."""
        # First call: GET submissions — one ungraded entry.
        submissions_response = _make_response(
            status_code=200,
            json_data=[{"id": 1, "user_id": 42, "assignment_id": 10, "score": None}],
            headers={"Link": ""},  # no next page
        )
        # Second call: PUT grade=0 — success.
        grade_response = _make_response(status_code=200)
        canvas_client._mock_session.request.side_effect = [
            submissions_response,
            grade_response,
        ]

        result = canvas_client.zero_out_ungraded_past_due(course_id=1, assignment_ids=[10])

        assert result == {10: [42]}

    def test_already_graded_zero_is_not_touched(self, canvas_client):
        """A submission with score=0.0 must NOT be zeroed again."""
        submissions_response = _make_response(
            status_code=200,
            json_data=[{"id": 2, "user_id": 7, "assignment_id": 10, "score": 0.0}],
            headers={"Link": ""},
        )
        canvas_client._mock_session.request.side_effect = [submissions_response]

        result = canvas_client.zero_out_ungraded_past_due(course_id=1, assignment_ids=[10])

        # user_id 7 already has score 0.0 — must not appear in zeroed list.
        assert result == {10: []}
        # Exactly one request (the GET) — no PUT should have been made.
        assert canvas_client._mock_session.request.call_count == 1

    def test_nonzero_score_is_not_touched(self, canvas_client):
        """A submission with an existing positive score must not be zeroed."""
        submissions_response = _make_response(
            status_code=200,
            json_data=[{"id": 3, "user_id": 5, "assignment_id": 10, "score": 85.0}],
            headers={"Link": ""},
        )
        canvas_client._mock_session.request.side_effect = [submissions_response]

        result = canvas_client.zero_out_ungraded_past_due(course_id=1, assignment_ids=[10])

        assert result == {10: []}
        assert canvas_client._mock_session.request.call_count == 1

    def test_multiple_assignments_processed_independently(self, canvas_client):
        """Each assignment is fetched and processed separately."""
        # Assignment 10: one ungraded submission.
        subs_10 = _make_response(
            status_code=200,
            json_data=[{"id": 1, "user_id": 1, "assignment_id": 10, "score": None}],
            headers={"Link": ""},
        )
        grade_10 = _make_response(status_code=200)
        # Assignment 11: one already-graded submission.
        subs_11 = _make_response(
            status_code=200,
            json_data=[{"id": 2, "user_id": 2, "assignment_id": 11, "score": 70.0}],
            headers={"Link": ""},
        )
        canvas_client._mock_session.request.side_effect = [subs_10, grade_10, subs_11]

        result = canvas_client.zero_out_ungraded_past_due(course_id=1, assignment_ids=[10, 11])

        assert result[10] == [1]
        assert result[11] == []

    def test_failed_grade_put_not_counted(self, canvas_client):
        """If the PUT call fails, the user is NOT included in the zeroed list."""
        subs = _make_response(
            status_code=200,
            json_data=[{"id": 1, "user_id": 99, "assignment_id": 10, "score": None}],
            headers={"Link": ""},
        )
        grade_fail = _make_response(status_code=500, text="server error")
        canvas_client._mock_session.request.side_effect = [subs, grade_fail]

        result = canvas_client.zero_out_ungraded_past_due(course_id=1, assignment_ids=[10])

        assert result == {10: []}

    def test_empty_assignment_list_returns_empty_dict(self, canvas_client):
        """Passing an empty assignment_ids list is a no-op."""
        result = canvas_client.zero_out_ungraded_past_due(course_id=1, assignment_ids=[])

        assert result == {}
        canvas_client._mock_session.request.assert_not_called()


class TestCreateCanvasClient:
    @pytest.fixture(autouse=True)
    def isolated_auth_storage(self):
        with patch("coursewright.core.canvas.client.get_browser_session_manager") as manager:
            manager.return_value.get_session.return_value = None
            yield

    @patch("coursewright.core.canvas.client.run_browser_login")
    @patch("coursewright.core.canvas.client.EnhancedCanvasAPI")
    def test_create_client_reports_auth_error_through_callback(
        self,
        mock_client_cls,
        mock_run_browser_login,
    ):
        mock_instance = mock_client_cls.return_value
        mock_instance.get_courses.side_effect = CanvasAuthError("Invalid or expired token")
        on_status = Mock()
        mock_run_browser_login.return_value = False

        result = create_canvas_client(
            base_url="https://canvas.example.edu",
            api_key="token",  # pragma: allowlist secret -- synthetic credential
            on_status=on_status,
        )

        assert result is None
        on_status.assert_any_call("Invalid or expired token")

    @patch("coursewright.core.canvas.client.load_api_key")
    @patch("coursewright.core.canvas.client.get_browser_session_manager")
    @patch("coursewright.core.canvas.client.EnhancedCanvasAPI")
    def test_create_client_reuses_stored_browser_session_before_prompting_login(
        self,
        mock_client_cls,
        mock_session_manager_factory,
        mock_load_api_key,
    ):
        """A valid stored browser session should be reused silently."""
        mock_load_api_key.return_value = None
        mock_session_manager = mock_session_manager_factory.return_value
        mock_session_manager.get_session.return_value = {
            "cookies": [{"name": "canvas_session", "value": "cookie-value"}],
            "csrf_token": "decoded-csrf-token",
        }

        mock_client_instance = mock_client_cls.return_value
        mock_client_instance.get_courses.return_value = [{"id": 1, "name": "Course"}]

        result = create_canvas_client(base_url="https://canvas.example.edu", api_key=None)

        assert result is mock_client_instance
        _, kwargs = mock_client_cls.call_args
        assert isinstance(kwargs["auth_strategy"], SessionAuth)
        mock_client_instance.get_courses.assert_called_once()

    @patch("coursewright.core.canvas.client.run_browser_login")
    @patch("coursewright.core.canvas.client.get_credential_manager")
    @patch("coursewright.core.canvas.client.get_browser_session_manager")
    @patch("coursewright.core.canvas.client.EnhancedCanvasAPI")
    def test_create_client_falls_back_to_browser_login_after_token_auth_error(
        self,
        mock_client_cls,
        mock_session_manager_factory,
        mock_credential_manager_factory,
        mock_run_browser_login,
    ):
        """An invalid token should trigger the browser-login fallback path."""
        mock_credential_manager_factory.return_value.get_token.return_value = "expired-token"

        mock_session_manager = mock_session_manager_factory.return_value
        mock_session_manager.get_session.side_effect = [
            None,
            {
                "cookies": [{"name": "canvas_session", "value": "cookie-value"}],
                "csrf_token": "decoded-csrf-token",
            },
        ]

        failing_client = Mock()
        failing_client.get_courses.side_effect = CanvasAuthError("invalid token")
        fallback_client = Mock()
        fallback_client.get_courses.return_value = [{"id": 1}]
        mock_client_cls.side_effect = [failing_client, fallback_client]

        on_status = Mock()
        confirm_login = Mock()
        mock_run_browser_login.return_value = True

        result = create_canvas_client(
            base_url="https://canvas.example.edu",
            on_status=on_status,
            wait_for_user_confirmation=confirm_login,
        )

        assert result is fallback_client
        mock_run_browser_login.assert_called_once_with(
            canvas_base_url="https://canvas.example.edu",
            wait_for_user_confirmation=confirm_login,
        )
        assert on_status.call_count == 3


# =============================================================================
# 10.  RATE-LIMIT HANDLING
# =============================================================================


class TestRateLimitHandling:
    """Verify that a 429 response triggers a retry."""

    @patch("coursewright.core.canvas.client.time.sleep")
    def test_retries_after_429(self, mock_sleep, canvas_client):
        """After a 429 the client sleeps and retries the same request."""
        first_response = _make_response(
            status_code=429,
            headers={"Retry-After": "2"},
        )
        second_response = _make_response(
            status_code=200,
            json_data={"id": 42, "name": "Delayed Course"},
        )
        canvas_client._mock_session.request.side_effect = [first_response, second_response]

        result = canvas_client.get_course(42)

        assert result is not None
        assert result["name"] == "Delayed Course"
        # Should have slept for the retry-after period (plus the rate limit delay)
        assert any(call.args == (2,) for call in mock_sleep.call_args_list)


# =============================================================================
# 11.  NETWORK FAILURE
# =============================================================================


class TestNetworkFailure:
    """Verify graceful handling of ``requests.exceptions.RequestException``."""

    def test_request_exception_raises(self, canvas_client):
        """A network error stops the read instead of returning missing data."""
        import requests

        canvas_client._mock_session.request.side_effect = requests.exceptions.ConnectionError(
            "DNS resolution failed"
        )

        with pytest.raises(CanvasRequestError):
            canvas_client.get_course(1)


# =============================================================================
# 12.  PAGINATION
# =============================================================================


class TestPagination:
    """Verify that ``_paginated_get`` follows Link headers across pages."""

    def test_follows_link_header_for_next_page(self, canvas_client):
        """Two pages of results are concatenated into a single list."""
        page1_items = [{"id": 1}, {"id": 2}]
        page2_items = [{"id": 3}]

        page1_response = _make_response(
            json_data=page1_items,
            headers={"Link": '<https://canvas.example.edu/api/v1/courses?page=2>; rel="next"'},
        )
        page2_response = _make_response(
            json_data=page2_items,
            # No next link — this is the last page
        )
        canvas_client._mock_session.request.side_effect = [page1_response, page2_response]

        url = "https://canvas.example.edu/api/v1/courses"
        all_items = canvas_client._paginated_get(url)

        assert len(all_items) == 3
        assert [item["id"] for item in all_items] == [1, 2, 3]

    def test_raises_on_error_page(self, canvas_client):
        """If any page fails, pagination returns no partial collection."""
        page1_items = [{"id": 1}]
        page1_response = _make_response(
            json_data=page1_items,
            headers={"Link": '<https://canvas.example.edu/api/v1/courses?page=2>; rel="next"'},
        )
        page2_response = _make_response(status_code=500, text="Internal Server Error")

        canvas_client._mock_session.request.side_effect = [page1_response, page2_response]

        url = "https://canvas.example.edu/api/v1/courses"
        with pytest.raises(CanvasRequestError):
            canvas_client._paginated_get(url)


class TestActiveSubmissionsForAssignments:
    """Verify the bulk submissions request used by the grading to-do."""

    def test_requests_active_students_for_all_listed_assignments(self, canvas_client):
        """One request covers every assignment and keeps excused/score fields."""
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=[
                {"id": 1, "assignment_id": 10, "user_id": 5, "score": None, "excused": True},
                {"id": 2, "assignment_id": 11, "user_id": 5, "score": 7.0, "excused": None},
            ],
        )

        submissions = canvas_client.get_active_submissions_for_assignments(42, [10, 11])

        assert canvas_client._mock_session.request.call_count == 1
        request_call = canvas_client._mock_session.request.call_args
        assert request_call.args[1].endswith("/api/v1/courses/42/students/submissions")
        request_params = request_call.kwargs["params"]
        assert request_params["student_ids[]"] == "all"
        assert request_params["assignment_ids[]"] == [10, 11]
        assert request_params["enrollment_state"] == "active"
        assert [submission.excused for submission in submissions] == [True, None]
        assert submissions[1].score == 7.0

    def test_skips_request_when_no_assignments(self, canvas_client):
        """No past-due assignments means no Canvas round trip."""
        assert canvas_client.get_active_submissions_for_assignments(42, []) == []
        canvas_client._mock_session.request.assert_not_called()


# =============================================================================
# 13.  ``load_api_key`` HELPER
# =============================================================================


class TestLoadApiKey:
    """Verify the standalone ``load_api_key`` function."""

    def test_reads_key_from_existing_file(self, tmp_path):
        key_file = tmp_path / "api.key"
        key_file.write_text("  my-secret-token  \n")

        result = load_api_key(str(key_file))

        assert result == "my-secret-token"

    def test_returns_none_for_missing_file(self, tmp_path):
        result = load_api_key(str(tmp_path / "nonexistent.key"))

        assert result is None


# =============================================================================
# 14.  ASSIGNMENT LOOKUP BY DISCUSSION
# =============================================================================


class TestGetAssignmentByDiscussion:
    """Verify ``get_assignment_by_discussion``."""

    def test_returns_assignment_when_linked(self, canvas_client):
        """When a discussion has an assignment_id, the assignment is fetched."""
        discussion_json = {"id": 100, "assignment_id": 500}
        assignment_json = {"id": 500, "name": "Week 1 Discussion", "points_possible": 100}

        canvas_client._mock_session.request.side_effect = [
            _make_response(json_data=discussion_json),
            _make_response(json_data=assignment_json),
        ]

        result = canvas_client.get_assignment_by_discussion(course_id=1, discussion_id=100)

        assert result is not None
        assert result["id"] == 500
        assert result["points_possible"] == 100

    def test_returns_none_when_no_assignment_linked(self, canvas_client):
        """A discussion with no ``assignment_id`` returns ``None``."""
        discussion_json = {"id": 100, "assignment_id": None}

        canvas_client._mock_session.request.return_value = _make_response(
            json_data=discussion_json,
        )

        result = canvas_client.get_assignment_by_discussion(course_id=1, discussion_id=100)

        assert result is None


# =============================================================================
# 15.  CLIENT INITIALISATION
# =============================================================================


class TestClientInitialisation:
    """Verify ``EnhancedCanvasAPI.__init__`` sets up state correctly."""

    def test_strips_trailing_slash_from_base_url(self):
        with patch("coursewright.core.canvas.client.requests.Session"):
            client = EnhancedCanvasAPI(
                base_url="https://canvas.example.edu///",
                access_token="tok",
            )

        # Only one trailing slash should remain stripped
        assert not client.base_url.endswith("/")

    def test_authorization_header_set(self):
        with patch("coursewright.core.canvas.client.requests.Session"):
            client = EnhancedCanvasAPI(
                base_url="https://canvas.example.edu",
                access_token="tok-123",
            )

        assert client.headers["Authorization"] == "Bearer tok-123"


# =============================================================================
# 15.  COURSE AVERAGE GRADES
# =============================================================================


class TestGetCourseAverageGrades:
    """Verify ``get_course_average_grades`` computes per-course averages."""

    def test_returns_average_for_single_course(self, canvas_client):
        """Three students with known scores should produce the correct mean."""
        enrollments_json = [
            {"user_id": 1, "type": "StudentEnrollment", "grades": {"current_score": 90.0}},
            {"user_id": 2, "type": "StudentEnrollment", "grades": {"current_score": 80.0}},
            {"user_id": 3, "type": "StudentEnrollment", "grades": {"current_score": 70.0}},
        ]
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=enrollments_json,
            headers={"Link": ""},
        )

        result = canvas_client.get_course_average_grades(course_ids=[42])

        assert 42 in result
        assert abs(result[42] - 80.0) < 0.001

    def test_omits_courses_with_no_scored_students(self, canvas_client):
        """When no enrollment has a current_score, the course is omitted."""
        enrollments_json = [
            {"user_id": 1, "grades": {"current_score": None}},
            {"user_id": 2, "grades": {}},
        ]
        canvas_client._mock_session.request.return_value = _make_response(
            json_data=enrollments_json,
            headers={"Link": ""},
        )

        result = canvas_client.get_course_average_grades(course_ids=[99])

        assert 99 not in result

    def test_returns_empty_dict_for_empty_course_list(self, canvas_client):
        """Passing an empty list is a no-op — no API calls are made."""
        result = canvas_client.get_course_average_grades(course_ids=[])

        assert result == {}
        canvas_client._mock_session.request.assert_not_called()

    def test_stops_on_course_api_error(self, canvas_client):
        """An API failure stops course aggregates before incomplete results escape."""
        canvas_client._mock_session.request.side_effect = [
            _make_response(status_code=500, text="error", headers={"Link": ""}),
            _make_response(
                json_data=[{"user_id": 1, "grades": {"current_score": 75.0}}],
                headers={"Link": ""},
            ),
        ]

        # Course aggregates must never masquerade as a complete result.
        with pytest.raises(CanvasRequestError):
            canvas_client.get_course_average_grades(course_ids=[10, 11])


# =============================================================================
# ANNOUNCEMENT POSTING
# =============================================================================


class TestPostAnnouncement:
    """Verify ``post_announcement`` builds the right request and result."""

    def test_success_returns_created_topic(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=200,
            json_data={"id": 103, "html_url": "https://canvas.example.edu/topics/103"},
        )

        created_topic = canvas_client.post_announcement(
            course_id=42,
            title="Grades are in",
            message_html="<p>Hello</p>",
        )

        assert created_topic["id"] == 103
        call_args = canvas_client._mock_session.request.call_args
        assert call_args.args[0] == "POST"
        assert call_args.args[1].endswith("/courses/42/discussion_topics")
        assert call_args.kwargs["data"]["is_announcement"] == "true"
        assert call_args.kwargs["data"]["published"] == "true"

    def test_sends_form_encoded_content_type(self, canvas_client):
        """Canvas 400s if form data is labelled as JSON — guard that."""
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=200,
            json_data={"id": 1},
        )

        canvas_client.post_announcement(1, "t", "<p>m</p>")

        sent_headers = canvas_client._mock_session.request.call_args.kwargs["headers"]
        assert sent_headers["Content-Type"] == "application/x-www-form-urlencoded"

    def test_rejection_returns_none(self, canvas_client):
        canvas_client._mock_session.request.return_value = _make_response(
            status_code=400,
        )

        assert canvas_client.post_announcement(1, "t", "<p>m</p>") is None
