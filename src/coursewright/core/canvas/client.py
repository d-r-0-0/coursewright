"""The one Canvas LMS REST API client for Coursewright.

Ported from discussorator ``canvas/client.py`` as the reference
implementation of docs/HOUSE_STYLE.md section 1:

- **One client class per remote service** (``CanvasClient``, with
  ``EnhancedCanvasAPI`` kept as a compatibility alias).
- **Auth is a strategy object** (``AuthStrategy`` with ``TokenAuth``
  and ``SessionAuth`` in ``auth_strategy.py``).
- **One choke point.**  Every HTTP call goes through ``_make_request``,
  which owns the 30-second timeout ceiling, rate limiting, per-request
  auth headers, error logging, and ``core/traffic`` events.
- **Library code is silent** (AGENTS.md 3.8).  No ``rich``, ``typer``,
  ``textual`` or ``coursewright.ui`` imports live here; status messages
  and the browser-login confirmation prompt are callbacks supplied by
  ``ui/``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from pydantic import SecretStr

from coursewright.core.auth import (
    get_browser_session_manager,
    get_credential_manager,
)
from coursewright.core.auth.browser_login import run_browser_login
from coursewright.core.canvas.auth_strategy import (
    AuthStrategy,
    CanvasAuthError,
    SessionAuth,
    TokenAuth,
)
from coursewright.core.canvas.models import (
    Assignment,
    CanvasDiscussion,
    CanvasDiscussionEntry,
    CanvasUser,
    Submission,
)
from coursewright.core.config.settings import DEFAULT_API_TOKEN_FILE, DEFAULT_CANVAS_URL
from coursewright.core.traffic import (
    STATUS_ERRORED,
    STATUS_IN_FLIGHT,
    publish_http_round_trip,
)

logger = logging.getLogger(__name__)

# The `source` every traffic event from this client carries.
TRAFFIC_SOURCE = "canvas"


class CanvasRequestError(RuntimeError):
    """Canvas failed to return usable data; callers must stop the operation."""


class CanvasClient:
    """Canvas LMS REST API client with discussion, grading, and course methods."""

    # Hard ceiling so a single request can never block a thread indefinitely.
    REQUEST_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        base_url: str,
        access_token: str | SecretStr = "",
        *,
        auth_strategy: AuthStrategy | None = None,
    ) -> None:
        """Initialize the Canvas API client.

        Args:
            base_url: Canvas instance URL (e.g. ``https://canvas.example.edu``).
            access_token: Canvas personal API access token.  Required when
                *auth_strategy* is not provided.
            auth_strategy: An :class:`AuthStrategy` instance to use instead
                of a raw token.  When provided, *access_token* is ignored.
        """
        self.base_url = base_url.rstrip("/")

        if auth_strategy is not None:
            self._auth_strategy: AuthStrategy = auth_strategy
        else:
            self._auth_strategy = TokenAuth(access_token)

        self.headers: dict[str, str] = {
            "Content-Type": "application/json",
        }

        self._rate_limit_delay = 0.05
        self._session = requests.Session()

        self.headers.update(self._auth_strategy.get_headers())
        self._auth_strategy.apply_to_session(self._session)
        self._session.headers.update({"Content-Type": "application/json"})

    def _make_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response | None:
        """Make a single Canvas API request with timeout, rate limiting, and traffic.

        Args:
            method: HTTP method (``GET``, ``POST``, ``PUT``, ``DELETE``, ``PATCH``).
            url: Full API endpoint URL.
            **kwargs: Additional keyword arguments forwarded to ``requests.Session.request``.

        Returns:
            A response, or ``None`` for a failed write connection. Failed reads
            raise; writes keep their existing ``None``/``False`` failure contracts.
            Every 401 raises ``CanvasAuthError``.
        """
        request_started_at = time.monotonic()
        try:
            time.sleep(self._rate_limit_delay)
            # Keep shorter caller timeouts, but never permit an unbounded wait.
            requested_timeout = kwargs.get("timeout")
            if isinstance(requested_timeout, tuple):
                kwargs["timeout"] = tuple(
                    min(value, self.REQUEST_TIMEOUT_SECONDS)
                    if value is not None
                    else self.REQUEST_TIMEOUT_SECONDS
                    for value in requested_timeout
                )
            else:
                kwargs["timeout"] = (
                    min(requested_timeout, self.REQUEST_TIMEOUT_SECONDS)
                    if requested_timeout is not None
                    else self.REQUEST_TIMEOUT_SECONDS
                )

            per_request_headers = self._auth_strategy.get_per_request_headers(method)
            if per_request_headers:
                merged = {**per_request_headers, **kwargs.get("headers", {})}
                kwargs["headers"] = merged

            # Preserve the source's single retry while reporting both round trips.
            for attempt in range(2):
                request_started_at = time.monotonic()
                publish_http_round_trip(
                    TRAFFIC_SOURCE, method, url, STATUS_IN_FLIGHT, request_started_at
                )
                response = self._session.request(method, url, **kwargs)
                publish_http_round_trip(
                    TRAFFIC_SOURCE, method, url, response.status_code, request_started_at
                )
                if response.status_code != 429 or attempt == 1:
                    break
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
            if response.status_code == 401:
                raise CanvasAuthError(
                    "Canvas rejected authentication; generate a new access token or "
                    "re-authenticate with: coursewright auth login-browser"
                )
            if response.status_code >= 400:
                # Response bodies can include student data and credentials.
                logger.error("Canvas request failed: HTTP %s", response.status_code)
                if method.upper() in {"GET", "HEAD"} and response.status_code != 404:
                    raise CanvasRequestError(
                        f"Canvas request failed (HTTP {response.status_code})."
                    )

            return response

        except requests.exceptions.RequestException as err:
            logger.error("Canvas connection failed: %s", type(err).__name__)
            publish_http_round_trip(TRAFFIC_SOURCE, method, url, STATUS_ERRORED, request_started_at)
            if method.upper() in {"GET", "HEAD"}:
                raise CanvasRequestError("Canvas connection failed; no data was loaded.") from err
            return None

    def _paginated_get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        page_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated Canvas GET endpoint."""
        all_items: list[dict[str, Any]] = []
        params = dict(params) if params else {}
        params["per_page"] = 100
        page_number = 0

        next_url: str | None = url
        visited_urls: set[str] = set()
        while next_url:
            if next_url in visited_urls:
                raise CanvasRequestError(
                    "Canvas pagination repeated a page; collection incomplete."
                )
            visited_urls.add(next_url)
            response = self._make_request("GET", next_url, params=params)
            if response is not None and response.status_code == 401:
                raise CanvasAuthError("Canvas rejected authentication. Re-authenticate and retry.")
            if response is None or response.status_code != 200:
                raise CanvasRequestError("Canvas collection could not be read completely.")
            try:
                items = response.json()
            except ValueError as err:
                raise CanvasRequestError("Canvas collection returned invalid JSON.") from err
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise CanvasRequestError("Canvas collection returned an unexpected response.")
            all_items.extend(items)
            page_number += 1

            if page_callback:
                page_callback(page_number, len(all_items))

            link_header = response.headers.get("Link", "")
            next_url = None
            for link in link_header.split(","):
                if 'rel="next"' in link:
                    next_url = link.split(";")[0].strip("<> ")
                    break

            params = None

        return all_items

    # =====================================
    # COURSE METHODS
    # =====================================

    def get_courses(self, enrollment_type: str = "teacher") -> list[dict[str, Any]]:
        """Get list of courses for the authenticated user."""
        url = f"{self.base_url}/api/v1/courses"
        params = {
            "enrollment_type": enrollment_type,
            "include[]": ["term", "total_students"],
            "state[]": ["available", "unpublished", "completed"],
        }
        return self._paginated_get(url, params)

    @staticmethod
    def _parse_canvas_datetime(value: Any) -> datetime | None:
        """Parse Canvas datetime strings into timezone-aware ``datetime`` values."""
        if not value or not isinstance(value, str):
            return None
        normalized_value = value.replace("Z", "+00:00")
        try:
            parsed_value = datetime.fromisoformat(normalized_value)
        except ValueError:
            return None
        if parsed_value.tzinfo is None:
            return parsed_value.replace(tzinfo=UTC)
        return parsed_value

    def get_last_student_participation_by_course(
        self,
        course_ids: list[int],
    ) -> dict[int, str]:
        """Return latest student activity timestamp for each requested course."""
        latest_participation_by_course: dict[int, str] = {}

        for course_id in course_ids:
            try:
                students = self.get_course_users(course_id, enrollment_type=["student"])
            except (CanvasAuthError, CanvasRequestError):
                raise
            except Exception as enrollment_error:
                logger.warning(
                    "Could not fetch student activity for course %s: %s",
                    course_id,
                    enrollment_error,
                )
                continue

            latest_participation: datetime | None = None
            for student in students:
                enrollments = getattr(student, "enrollments", [])
                if not isinstance(enrollments, list):
                    continue
                for enrollment in enrollments:
                    if not isinstance(enrollment, dict):
                        continue
                    last_activity_at = self._parse_canvas_datetime(
                        enrollment.get("last_activity_at")
                    )
                    if last_activity_at is None:
                        continue
                    if latest_participation is None or last_activity_at > latest_participation:
                        latest_participation = last_activity_at

            if latest_participation is not None:
                latest_participation_by_course[course_id] = latest_participation.isoformat()

        return latest_participation_by_course

    def get_course_average_grades(
        self,
        course_ids: list[int],
    ) -> dict[int, float]:
        """Return the average current grade (0-100) for student enrollments."""
        average_grade_by_course: dict[int, float] = {}

        for course_id in course_ids:
            url = f"{self.base_url}/api/v1/courses/{course_id}/enrollments"
            params = {
                "type[]": ["StudentEnrollment"],
                "include[]": ["current_grades"],
                "per_page": 100,
            }
            try:
                enrollments = self._paginated_get(url, params)
            except (CanvasAuthError, CanvasRequestError):
                raise
            except Exception as fetch_error:
                logger.warning(
                    "Could not fetch grade data for course %s: %s",
                    course_id,
                    fetch_error,
                )
                continue

            scores: list[float] = []
            for enrollment in enrollments:
                if not isinstance(enrollment, dict):
                    continue
                grades_block = enrollment.get("grades", {})
                if not isinstance(grades_block, dict):
                    continue
                current_score = grades_block.get("current_score")
                if current_score is not None:
                    with suppress(TypeError, ValueError):
                        scores.append(float(current_score))

            if scores:
                average_grade_by_course[course_id] = sum(scores) / len(scores)

        return average_grade_by_course

    def get_course(self, course_id: int) -> dict[str, Any] | None:
        """Get detailed information about a specific course."""
        url = f"{self.base_url}/api/v1/courses/{course_id}"
        params = {
            "include[]": [
                "term",
                "course_progress",
                "storage_quota_used_mb",
                "total_students",
                "syllabus_body",
            ]
        }

        response = self._make_request("GET", url, params=params)
        if response and response.status_code == 200:
            return response.json()
        return None

    def get_assignments(self, course_id: int) -> list[Assignment]:
        """Get all assignments for a course."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments"
        params = {
            "per_page": 100,
            "include[]": ["needs_grading_count"],
        }

        assignment_data = self._paginated_get(url, params)
        return [Assignment(**item) for item in assignment_data]

    def get_assignment(self, course_id: int, assignment_id: int) -> dict[str, Any] | None:
        """Get a specific assignment."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}"

        response = self._make_request("GET", url)
        if response and response.status_code == 200:
            return response.json()
        return None

    def get_submission(
        self, course_id: int, assignment_id: int, user_id: int
    ) -> dict[str, Any] | None:
        """Get one student's submission, or None when Canvas has none."""
        url = (
            f"{self.base_url}/api/v1/courses/{course_id}"
            f"/assignments/{assignment_id}/submissions/{user_id}"
        )
        response = self._make_request("GET", url)
        if response and response.status_code == 200:
            return response.json()
        return None

    def get_assignment_submissions(
        self,
        course_id: int,
        assignment_id: int,
    ) -> list[Submission]:
        """Fetch all submissions for an assignment."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
        params = {"per_page": 100, "include[]": "user"}
        raw_submissions = self._paginated_get(url, params)
        return [Submission(**submission) for submission in raw_submissions]

    def get_submissions_with_comments(
        self,
        course_id: int,
        assignment_id: int,
    ) -> list[Submission]:
        """Fetch submissions for an assignment with comments included."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
        params = {
            "per_page": 100,
            "include[]": ["user", "submission_comments"],
        }
        raw_submissions = self._paginated_get(url, params)
        return [Submission(**submission) for submission in raw_submissions]

    def delete_submission_comment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        comment_id: int,
    ) -> bool:
        """Delete a specific comment on a student's submission."""
        url = (
            f"{self.base_url}/api/v1/courses/{course_id}"
            f"/assignments/{assignment_id}"
            f"/submissions/{user_id}"
            f"/comments/{comment_id}"
        )
        try:
            response = self._make_request("DELETE", url)
            if response and response.status_code == 200:
                logger.info(
                    "Deleted comment %d for user %d on assignment %d",
                    comment_id,
                    user_id,
                    assignment_id,
                )
                return True
            logger.error(
                "Delete comment failed for comment %d — status %s",
                comment_id,
                response.status_code if response else "no response",
            )
            return False
        except (CanvasAuthError, CanvasRequestError):
            raise
        except Exception as err:
            logger.error(
                "Delete comment error for comment %d: %s",
                comment_id,
                err,
            )
            return False

    def get_student_submissions(
        self,
        course_id: int,
        student_id: int,
    ) -> list[dict[str, Any]]:
        """Get every assignment submission record for one student."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/students/submissions"
        params = {
            "per_page": 100,
            "student_ids[]": student_id,
        }
        return self._paginated_get(url, params)

    def get_all_student_submissions(
        self,
        course_id: int,
        page_callback: Callable[[int, int], None] | None = None,
        submitted_since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get submission records for all students in a course in one call."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/students/submissions"
        params: dict[str, Any] = {
            "per_page": 100,
            "student_ids[]": "all",
        }
        if submitted_since is not None:
            params["submitted_since"] = submitted_since.strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._paginated_get(url, params, page_callback=page_callback)

    def get_active_submissions_for_assignments(
        self,
        course_id: int,
        assignment_ids: list[int],
    ) -> list[Submission]:
        """Get active students' submissions for several assignments at once."""
        if not assignment_ids:
            return []

        url = f"{self.base_url}/api/v1/courses/{course_id}/students/submissions"
        params: dict[str, Any] = {
            "per_page": 100,
            "student_ids[]": "all",
            "assignment_ids[]": assignment_ids,
            "enrollment_state": "active",
        }
        raw_submissions = self._paginated_get(url, params)
        return [Submission(**submission) for submission in raw_submissions]

    def post_submission_comment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        comment_text: str,
    ) -> bool:
        """Post a text comment on a student's submission without changing the grade."""
        url = (
            f"{self.base_url}/api/v1/courses/{course_id}"
            f"/assignments/{assignment_id}"
            f"/submissions/{user_id}"
        )
        data = {"comment[text_comment]": comment_text}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            response = self._make_request(
                "PUT",
                url,
                data=data,
                headers=headers,
            )
            if response and response.status_code == 200:
                logger.info(
                    "Comment posted for user %d on assignment %d",
                    user_id,
                    assignment_id,
                )
                return True
            logger.error(
                "Comment post failed for user %d — status %s",
                user_id,
                response.status_code if response else "no response",
            )
            return False
        except (CanvasAuthError, CanvasRequestError):
            raise
        except Exception as err:
            logger.error(
                "Comment post error for user %d: %s",
                user_id,
                err,
            )
            return False

    def post_announcement(
        self,
        course_id: int,
        title: str,
        message_html: str,
    ) -> dict[str, Any] | None:
        """Publish a course announcement."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics"
        data = {
            "title": title,
            "message": message_html,
            "is_announcement": "true",
            "published": "true",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response = self._make_request("POST", url, data=data, headers=headers)
        if response is not None and response.status_code in (200, 201):
            created_topic = response.json()
            logger.info(
                "Announcement %s posted to course %d",
                created_topic.get("id"),
                course_id,
            )
            return created_topic
        logger.error(
            "Announcement post failed for course %d — status %s",
            course_id,
            response.status_code if response is not None else "no response",
        )
        return None

    def post_discussion_entry_reply(
        self,
        course_id: int,
        topic_id: int,
        entry_id: int,
        message_html: str,
    ) -> dict[str, Any] | None:
        """Post a reply under an existing discussion entry.

        This is a raw write: it posts immediately.  Callers that act for
        the instructor go through ``SafeCanvasWriter.post_discussion_reply``.
        """
        url = (
            f"{self.base_url}/api/v1/courses/{course_id}"
            f"/discussion_topics/{topic_id}/entries/{entry_id}/replies"
        )
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = self._make_request("POST", url, data={"message": message_html}, headers=headers)
        if response is not None and response.status_code in (200, 201):
            return response.json()
        logger.error(
            "Discussion reply failed for entry %d — status %s",
            entry_id,
            response.status_code if response is not None else "no response",
        )
        return None

    def post_conversation_message(
        self,
        course_id: int | None,
        recipient_id: int,
        subject: str,
        body: str,
    ) -> dict[str, Any] | None:
        """Create an inbox conversation message addressed to one user.

        This is a raw write: it posts immediately.  Callers that act for
        the instructor go through
        ``SafeCanvasWriter.post_conversation_message``.
        """
        url = f"{self.base_url}/api/v1/conversations"
        # The session sets Content-Type: application/json globally, so we
        # must send JSON (not form-encoded data) to avoid a 500.
        payload: dict[str, Any] = {
            "recipients": [str(recipient_id)],
            "subject": subject,
            "body": body,
            "force_new": True,
        }
        # Canvas often 500s without a course context; provide it when known.
        if course_id is not None:
            payload["context_code"] = f"course_{course_id}"
        response = self._make_request("POST", url, json=payload)
        if response is not None and response.status_code in (200, 201):
            return response.json()
        logger.error(
            "Conversation message failed for student %s — status %s",
            recipient_id,
            response.status_code if response is not None else "no response",
        )
        return None

    def download_submission_file(
        self,
        attachment_url: str,
        destination_path: Path,
    ) -> Path | None:
        """Download a submission attachment file to a local path."""
        destination_path = Path(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = self._make_request("GET", attachment_url, stream=True)
            if response is None or response.status_code != 200:
                logger.error(
                    "File download failed — status %s for %s",
                    response.status_code if response else "no response",
                    attachment_url,
                )
                return None

            with open(destination_path, "wb") as file_handle:
                for chunk in response.iter_content(chunk_size=8192):
                    file_handle.write(chunk)

            logger.info(
                "Downloaded %s (%d bytes)",
                destination_path,
                destination_path.stat().st_size,
            )
            return destination_path

        except (CanvasAuthError, CanvasRequestError):
            raise
        except Exception as download_error:
            logger.error("File download error: %s", download_error)
            return None

    # =====================================
    # DISCUSSION METHODS
    # =====================================

    def get_discussions(
        self, course_id: int, include_announcements: bool = False
    ) -> list[CanvasDiscussion]:
        """Get all discussion topics for a course."""
        discussions: list[CanvasDiscussion] = []

        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics"
        params = {"per_page": 100, "include[]": ["sections", "sections_user_count"]}

        discussion_data = self._paginated_get(url, params)

        for item in discussion_data:
            discussion = CanvasDiscussion(
                id=item["id"],
                title=item["title"],
                message=item.get("message", ""),
                posted_at=datetime.fromisoformat(item["posted_at"].replace("Z", "+00:00"))
                if item.get("posted_at")
                else None,
                replies_count=item.get("discussion_subentry_count", 0),
                unread_count=item.get("unread_count", 0),
                course_id=course_id,
                assignment_id=item.get("assignment_id"),
                discussion_type=item.get("discussion_type", "threaded"),
                html_url=item.get("html_url"),
                require_initial_post=bool(item.get("require_initial_post", False)),
                published=bool(item.get("published", True)),
                locked=bool(item.get("locked", False)),
                is_announcement=False,
            )
            discussions.append(discussion)

        if include_announcements:
            announcement_url = f"{self.base_url}/api/v1/courses/{course_id}/announcements"
            announcement_params = {
                "per_page": 100,
                "include[]": ["sections", "sections_user_count"],
            }
            announcement_data = self._paginated_get(announcement_url, announcement_params)

            for item in announcement_data:
                discussion = CanvasDiscussion(
                    id=item["id"],
                    title=item["title"],
                    message=item.get("message", ""),
                    posted_at=datetime.fromisoformat(item["posted_at"].replace("Z", "+00:00"))
                    if item.get("posted_at")
                    else None,
                    replies_count=item.get("discussion_subentry_count", 0),
                    unread_count=item.get("unread_count", 0),
                    course_id=course_id,
                    assignment_id=item.get("assignment_id"),
                    discussion_type=item.get("discussion_type", "threaded"),
                    html_url=item.get("html_url"),
                    require_initial_post=bool(item.get("require_initial_post", False)),
                    published=bool(item.get("published", True)),
                    locked=bool(item.get("locked", False)),
                    is_announcement=True,
                )
                discussions.append(discussion)

        return discussions

    def get_discussion_topic(self, course_id: int, topic_id: int) -> CanvasDiscussion | None:
        """Get a specific discussion topic."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}"

        response = self._make_request("GET", url)
        if response and response.status_code == 200:
            item = response.json()
            return CanvasDiscussion(
                id=item["id"],
                title=item["title"],
                message=item.get("message", ""),
                posted_at=datetime.fromisoformat(item["posted_at"].replace("Z", "+00:00"))
                if item.get("posted_at")
                else None,
                replies_count=item.get("discussion_subentry_count", 0),
                unread_count=item.get("unread_count", 0),
                course_id=course_id,
                assignment_id=item.get("assignment_id"),
                is_announcement=item.get("is_announcement", False),
            )
        return None

    def get_discussion_entries(
        self, course_id: int, topic_id: int, include_replies: bool = True
    ) -> list[CanvasDiscussionEntry]:
        """Get all entries (posts) for a discussion topic."""
        if include_replies:
            try:
                return self._get_discussion_view(course_id, topic_id)
            except (CanvasAuthError, CanvasRequestError):
                raise
            except Exception as err:
                logger.warning(
                    f"Could not get structured view, falling back to flat entries: {err}"
                )

        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}/entries"

        entries_data = self._paginated_get(url)
        logger.debug(f"Canvas API returned {len(entries_data)} entries from {url}")

        entries: list[CanvasDiscussionEntry] = []

        for item in entries_data:
            message = item.get("message", "")
            word_count = len(message.split()) if message else 0

            entry = CanvasDiscussionEntry(
                id=item["id"],
                user_id=item.get("user_id", 0),
                discussion_id=topic_id,
                parent_id=item.get("parent_id"),
                message=message,
                created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                if item.get("created_at")
                else datetime.now(),
                updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
                if item.get("updated_at")
                else None,
                word_count=word_count,
                user_name=item.get("user_name", "Unknown User"),
            )

            if include_replies and item.get("replies"):
                entry.replies = self._process_nested_replies(item["replies"], topic_id)

            entries.append(entry)

        return entries

    def _get_discussion_view(self, course_id: int, topic_id: int) -> list[CanvasDiscussionEntry]:
        """Get discussion entries using the structured view endpoint."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}/view"

        response = self._make_request("GET", url)
        if response is None:
            raise RuntimeError(f"Failed to fetch discussion view: {url}")
        response.raise_for_status()
        if response.status_code != 200:
            raise RuntimeError(f"Discussion view returned status {response.status_code}: {url}")
        view_data = response.json()

        entries: list[CanvasDiscussionEntry] = []
        participants = {
            participant["id"]: participant for participant in view_data.get("participants", [])
        }

        def extract_entries_recursive(
            view_items: list[dict[str, Any]],
            parent_id: int | None = None,
            depth: int = 0,
        ) -> None:
            for item in view_items:
                entry_id = item.get("id")
                user_id = item.get("user_id", 0)
                message = item.get("message", "")

                user_name = "Unknown User"
                if user_id in participants:
                    user_name = participants[user_id].get("display_name", "Unknown User")

                word_count = len(message.split()) if message else 0

                entry = CanvasDiscussionEntry(
                    id=entry_id,
                    user_id=user_id,
                    discussion_id=topic_id,
                    parent_id=parent_id,
                    message=message,
                    created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                    if item.get("created_at")
                    else datetime.now(),
                    updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
                    if item.get("updated_at")
                    else None,
                    word_count=word_count,
                    user_name=user_name,
                )
                entries.append(entry)

                replies = item.get("replies", [])
                if replies:
                    extract_entries_recursive(replies, entry_id, depth + 1)

        view_items = view_data.get("view", [])
        extract_entries_recursive(view_items)

        logger.debug(f"Extracted {len(entries)} total entries (including replies)")
        return entries

    def _process_nested_replies(
        self, replies_data: list[dict[str, Any]], topic_id: int
    ) -> list[CanvasDiscussionEntry]:
        """Process nested reply data recursively."""
        replies: list[CanvasDiscussionEntry] = []

        for item in replies_data:
            message = item.get("message", "")
            word_count = len(message.split()) if message else 0

            reply = CanvasDiscussionEntry(
                id=item["id"],
                user_id=item.get("user_id", 0),
                discussion_id=topic_id,
                parent_id=item.get("parent_id"),
                message=message,
                created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                if item.get("created_at")
                else datetime.now(),
                updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
                if item.get("updated_at")
                else None,
                word_count=word_count,
                user_name=item.get("user_name", "Unknown User"),
            )

            if item.get("replies"):
                reply.replies = self._process_nested_replies(item["replies"], topic_id)

            replies.append(reply)

        return replies

    def get_course_users(
        self, course_id: int, enrollment_type: list[str] | None = None
    ) -> list[CanvasUser]:
        """Get all users in a course."""
        if enrollment_type is None:
            enrollment_type = ["student"]

        url = f"{self.base_url}/api/v1/courses/{course_id}/users"
        params = {
            "enrollment_type[]": enrollment_type,
            "include[]": ["enrollments", "current_grades"],
            "per_page": 100,
        }

        users_data = self._paginated_get(url, params)
        users: list[CanvasUser] = []

        for item in users_data:
            user = CanvasUser(
                id=item["id"],
                name=item["name"],
                sortable_name=item.get("sortable_name"),
                short_name=item.get("short_name"),
                sis_user_id=item.get("sis_user_id"),
                login_id=item.get("login_id"),
                email=item.get("email"),
                enrollments=item.get("enrollments", []),
            )
            users.append(user)

        return users

    def get_user(self, user_id: int) -> CanvasUser | None:
        """Get user information."""
        url = f"{self.base_url}/api/v1/users/{user_id}"

        response = self._make_request("GET", url)
        if response and response.status_code == 200:
            item = response.json()
            return CanvasUser(
                id=item["id"],
                name=item["name"],
                sortable_name=item.get("sortable_name"),
                short_name=item.get("short_name"),
                sis_user_id=item.get("sis_user_id"),
                login_id=item.get("login_id"),
                email=item.get("email"),
            )
        return None

    # =====================================
    # COURSE-BUILDING HELPERS
    # (folded in from COURSE_BUILDER ``lib/canvas_api.py``)
    #
    # Every method below is a *raw* operation: it talks to Canvas
    # immediately.  The dry-run / backup / execute policy of AGENTS.md
    # 3.11 lives one layer up in ``safe_write.SafeCanvasWriter``, which
    # is the entry point every script and command should use for writes.
    # =====================================

    def _get_single_resource(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """GET one Canvas object; ``None`` when it is missing or the call failed."""
        response = self._make_request("GET", url, params=params)
        if response is not None and response.status_code == 200:
            return response.json()
        return None

    def _send_write_request(
        self,
        method: str,
        url: str,
        description: str,
        **request_arguments: Any,
    ) -> dict[str, Any] | None:
        """Send a POST/PUT and return the JSON body, or ``None`` after logging why.

        COURSE_BUILDER printed these failures behind ``if response:``, but a
        ``requests.Response`` is falsy for every 4xx/5xx, so real HTTP errors
        were silent.  Comparing with ``None`` and logging fixes that
        (AGENTS.md 3.11 "no silent failures").
        """
        response = self._make_request(method, url, **request_arguments)
        if response is None:
            logger.error("%s failed: no response from Canvas", description)
            return None
        if response.status_code in (200, 201):
            return response.json()
        logger.error("%s failed: HTTP %s", description, response.status_code)
        return None

    def _send_delete_request(self, url: str, description: str) -> bool:
        """DELETE a Canvas object; ``True`` only on a 200/204 answer."""
        response = self._make_request("DELETE", url)
        if response is not None and response.status_code in (200, 204):
            return True
        logger.error(
            "%s failed: %s",
            description,
            "no response from Canvas" if response is None else f"HTTP {response.status_code}",
        )
        return False

    # ----- course settings -------------------------------------------------

    def update_course(self, course_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update course settings, e.g. ``{"name": ..., "course_code": ...}``."""
        url = f"{self.base_url}/api/v1/courses/{course_id}"
        return self._send_write_request(
            "PUT", url, f"Updating course {course_id}", json={"course": updates}
        )

    def get_course_nickname(self, course_id: int) -> str | None:
        """Get the calling user's private nickname for a course, if one is set."""
        url = f"{self.base_url}/api/v1/users/self/course_nicknames/{course_id}"
        nickname_record = self._get_single_resource(url)
        return nickname_record.get("nickname") if nickname_record else None

    def set_course_nickname(self, course_id: int, nickname: str) -> dict[str, Any] | None:
        """Set the calling user's private nickname for a course.

        The nickname replaces the course name on the user's own dashboard and
        is invisible to students, so it is the only rename available on an
        SIS-provisioned shell.
        """
        url = f"{self.base_url}/api/v1/users/self/course_nicknames/{course_id}"
        return self._send_write_request(
            "PUT", url, f"Setting nickname on course {course_id}", json={"nickname": nickname}
        )

    # ----- modules ---------------------------------------------------------

    def get_modules(self, course_id: int) -> list[dict[str, Any]]:
        """Get every module of a course, with its items and content details."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules"
        return self._paginated_get(url, {"include[]": ["items", "content_details"]})

    def get_module(self, course_id: int, module_id: int) -> dict[str, Any] | None:
        """Get one module, with its items and content details."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}"
        return self._get_single_resource(url, {"include[]": ["items", "content_details"]})

    def create_module(self, course_id: int, module_data: dict[str, Any]) -> dict[str, Any] | None:
        """Create a module (Canvas wants the fields wrapped in ``module``)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules"
        return self._send_write_request(
            "POST", url, "Creating module", json={"module": module_data}
        )

    def update_module(
        self, course_id: int, module_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a module."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}"
        return self._send_write_request(
            "PUT", url, f"Updating module {module_id}", json={"module": updates}
        )

    def delete_module(self, course_id: int, module_id: int) -> bool:
        """Delete a module without deleting its underlying course content."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}"
        return self._send_delete_request(url, f"Deleting module {module_id}")

    def reorder_modules(self, course_id: int, module_order: list[int]) -> bool:
        """Reorder modules; *module_order* lists module ids in the desired order."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/reorder"
        reordered = self._send_write_request(
            "POST", url, "Reordering modules", data={"order[]": module_order}
        )
        return reordered is not None

    # ----- module items ----------------------------------------------------

    def get_module_items(self, course_id: int, module_id: int) -> list[dict[str, Any]]:
        """Get every item in a module."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}/items"
        return self._paginated_get(url)

    def get_module_item(
        self, course_id: int, module_id: int, item_id: int
    ) -> dict[str, Any] | None:
        """Get one module item."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}"
        return self._get_single_resource(url)

    def create_module_item(
        self, course_id: int, module_id: int, item_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create a module item (fields wrapped in ``module_item``)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}/items"
        return self._send_write_request(
            "POST", url, "Creating module item", json={"module_item": item_data}
        )

    def update_module_item(
        self, course_id: int, module_id: int, item_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a module item; *module_id* is the module that holds it now."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}"
        return self._send_write_request(
            "PUT", url, f"Updating module item {item_id}", json={"module_item": updates}
        )

    def delete_module_item(self, course_id: int, module_id: int, item_id: int) -> bool:
        """Delete a module item permanently."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}"
        return self._send_delete_request(url, f"Deleting module item {item_id}")

    def move_module_item(
        self,
        course_id: int,
        source_module_id: int,
        item_id: int,
        target_module_id: int,
        position: int | None = None,
    ) -> dict[str, Any] | None:
        """Move a module item to another module (the PUT targets the source module)."""
        updates: dict[str, Any] = {"module_id": target_module_id}
        if position is not None:
            updates["position"] = position
        return self.update_module_item(course_id, source_module_id, item_id, updates)

    # ----- assignments and assignment groups -------------------------------
    # ``get_assignments`` / ``get_assignment`` already exist above (typed
    # model results, kept as ported from discussorator).

    def get_assignment_groups(self, course_id: int) -> list[dict[str, Any]]:
        """Get every assignment group of a course, with its assignments."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignment_groups"
        return self._paginated_get(url, {"include[]": ["assignments"]})

    def get_assignment_group(self, course_id: int, group_id: int) -> dict[str, Any] | None:
        """Get one assignment group."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignment_groups/{group_id}"
        return self._get_single_resource(url)

    def create_assignment_group(
        self, course_id: int, group_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create an assignment group (top-level fields, not wrapped)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignment_groups"
        return self._send_write_request("POST", url, "Creating assignment group", json=group_data)

    def update_assignment_group(
        self, course_id: int, group_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update an assignment group (top-level fields, not wrapped)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignment_groups/{group_id}"
        return self._send_write_request(
            "PUT", url, f"Updating assignment group {group_id}", json=updates
        )

    def delete_assignment_group(self, course_id: int, group_id: int) -> bool:
        """Delete an assignment group (Canvas refuses while it holds assignments)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignment_groups/{group_id}"
        return self._send_delete_request(url, f"Deleting assignment group {group_id}")

    def create_assignment(
        self, course_id: int, assignment_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create an assignment (fields wrapped in ``assignment``)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments"
        return self._send_write_request(
            "POST", url, "Creating assignment", json={"assignment": assignment_data}
        )

    def update_assignment(
        self, course_id: int, assignment_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update an assignment; dates such as ``due_at`` carry no ``Z`` (rule 3.10)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}"
        return self._send_write_request(
            "PUT", url, f"Updating assignment {assignment_id}", json={"assignment": updates}
        )

    def delete_assignment(self, course_id: int, assignment_id: int) -> bool:
        """Delete an assignment permanently."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}"
        return self._send_delete_request(url, f"Deleting assignment {assignment_id}")

    # ----- discussion topics and announcements -----------------------------
    # Discussion fields are sent at the top level, never wrapped.

    def get_discussion_topics(self, course_id: int) -> list[dict[str, Any]]:
        """Get every discussion topic of a course as raw records."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics"
        return self._paginated_get(url)

    def get_current_user_profile(self) -> dict[str, Any] | None:
        """Get the authenticated user's own profile (``users/self``) as a raw record."""
        return self._get_single_resource(f"{self.base_url}/api/v1/users/self")

    def get_active_teacher_courses(self) -> list[dict[str, Any]]:
        """Get the courses the user actively teaches right now, as raw records.

        Asking Canvas for active teacher enrollments is semester-proof: a
        cached list of course ids goes stale every term, this never does.
        """
        url = f"{self.base_url}/api/v1/courses"
        params = {
            "enrollment_type": "teacher",
            "enrollment_state": "active",
            "state[]": ["available"],
        }
        return self._paginated_get(url, params)

    def get_discussion_view_record(self, course_id: int, topic_id: int) -> dict[str, Any] | None:
        """Get a topic's full threaded ``view`` (participants + entry tree) as raw JSON."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}/view"
        return self._get_single_resource(url)

    def get_discussion_topic_record(self, course_id: int, topic_id: int) -> dict[str, Any] | None:
        """Get one discussion topic as a raw record (``get_discussion_topic`` is typed)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}"
        return self._get_single_resource(url)

    def create_discussion_topic(
        self, course_id: int, topic_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create one discussion topic."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics"
        return self._send_write_request("POST", url, "Creating discussion topic", json=topic_data)

    def update_discussion_topic(
        self, course_id: int, topic_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update one discussion topic."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}"
        return self._send_write_request(
            "PUT", url, f"Updating discussion topic {topic_id}", json=updates
        )

    def delete_discussion_topic(self, course_id: int, topic_id: int) -> bool:
        """Delete one discussion topic or announcement."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}"
        return self._send_delete_request(url, f"Deleting discussion topic {topic_id}")

    def get_announcements(self, course_id: int) -> list[dict[str, Any]]:
        """Get the announcements of one course (``post_announcement`` publishes one)."""
        url = f"{self.base_url}/api/v1/announcements"
        return self._paginated_get(url, {"context_codes[]": [f"course_{course_id}"]})

    # ----- quizzes ---------------------------------------------------------

    def get_quizzes(self, course_id: int) -> list[dict[str, Any]]:
        """Get every quiz of a course."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/quizzes"
        return self._paginated_get(url)

    def get_quiz(self, course_id: int, quiz_id: int) -> dict[str, Any] | None:
        """Get one quiz."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/quizzes/{quiz_id}"
        return self._get_single_resource(url)

    def create_quiz(self, course_id: int, quiz_data: dict[str, Any]) -> dict[str, Any] | None:
        """Create a quiz (fields wrapped in ``quiz``)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/quizzes"
        return self._send_write_request("POST", url, "Creating quiz", json={"quiz": quiz_data})

    def update_quiz(
        self, course_id: int, quiz_id: int, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a quiz."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/quizzes/{quiz_id}"
        return self._send_write_request(
            "PUT", url, f"Updating quiz {quiz_id}", json={"quiz": updates}
        )

    def create_quiz_question(
        self, course_id: int, quiz_id: int, question_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create a question in a quiz (fields wrapped in ``question``)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/quizzes/{quiz_id}/questions"
        return self._send_write_request(
            "POST", url, "Creating quiz question", json={"question": question_data}
        )

    # ----- pages -----------------------------------------------------------

    def get_pages(self, course_id: int) -> list[dict[str, Any]]:
        """Get every page of a course (without bodies; use ``get_page`` for one)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/pages"
        return self._paginated_get(url)

    def get_page(self, course_id: int, page_url: str) -> dict[str, Any] | None:
        """Get one page, including its body, by URL slug."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/pages/{page_url}"
        return self._get_single_resource(url)

    def create_page(self, course_id: int, page_data: dict[str, Any]) -> dict[str, Any] | None:
        """Create a page; *page_data* needs ``title`` and may hold ``body``, ``published``."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/pages"
        return self._send_write_request("POST", url, "Creating page", json={"wiki_page": page_data})

    def update_page(
        self, course_id: int, page_url: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a page identified by its URL slug."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/pages/{page_url}"
        return self._send_write_request(
            "PUT", url, f"Updating page {page_url}", json={"wiki_page": updates}
        )

    def delete_page(self, course_id: int, page_url: str) -> bool:
        """Delete one page by its URL slug."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/pages/{page_url}"
        return self._send_delete_request(url, f"Deleting page {page_url}")

    # ----- files -----------------------------------------------------------

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        """Guess a MIME type from a file name."""
        import mimetypes

        content_type, _ = mimetypes.guess_type(filename)
        return content_type or "application/octet-stream"

    def upload_file(
        self, course_id: int, file_path: str | Path, folder: str | None = None
    ) -> dict[str, Any] | None:
        """Upload a local file to a course using Canvas's three-step protocol.

        1. Ask Canvas for an upload URL and parameters.
        2. POST the file to that URL.
        3. Read the created file object from the body, or follow ``Location``.

        Step 2 targets a pre-signed storage URL, not Canvas itself, so the
        ``Authorization`` header is explicitly dropped (a ``None`` header
        value removes it from the session's defaults) and redirects are not
        followed, which lets step 3 re-authenticate against Canvas.  Both
        steps still go through ``_make_request`` so the timeout and traffic
        events apply (COURSE_BUILDER called ``requests.post`` here).
        """
        local_path = Path(file_path)
        if not local_path.is_file():
            logger.error("Upload skipped: file not found: %s", local_path.name)
            return None

        reservation_request = {
            "name": local_path.name,
            "size": local_path.stat().st_size,
            "content_type": self._guess_content_type(local_path.name),
        }
        if folder:
            reservation_request["parent_folder_path"] = folder

        reservation = self._send_write_request(
            "POST",
            f"{self.base_url}/api/v1/courses/{course_id}/files",
            f"Requesting upload URL for {local_path.name}",
            json=reservation_request,
        )
        upload_url = reservation.get("upload_url") if reservation else None
        if not reservation or not upload_url:
            logger.error("No upload URL received for %s", local_path.name)
            return None

        with local_path.open("rb") as file_handle:
            upload_response = self._make_request(
                "POST",
                upload_url,
                data=reservation.get("upload_params", {}),
                files={"file": (local_path.name, file_handle)},
                headers={"Authorization": None, "Content-Type": None},
                allow_redirects=False,
            )
        if upload_response is None or upload_response.status_code not in (
            200,
            201,
            301,
            302,
            303,
        ):
            logger.error(
                "Upload of %s failed: %s",
                local_path.name,
                "no response" if upload_response is None else upload_response.status_code,
            )
            return None

        if upload_response.status_code in (301, 302, 303):
            location = upload_response.headers.get("Location")
            return self._get_single_resource(location) if location else None
        return upload_response.json()

    # ----- course copy -----------------------------------------------------

    def copy_course_content(
        self, destination_course_id: int, source_course_id: int
    ) -> dict[str, Any] | None:
        """Start a Canvas course-copy migration into *destination_course_id*.

        Returns the migration record; poll ``get_content_migration`` until its
        ``workflow_state`` is ``completed`` or ``failed``.
        """
        url = f"{self.base_url}/api/v1/courses/{destination_course_id}/content_migrations"
        migration_request = {
            "migration_type": "course_copy_importer",
            "settings": {"source_course_id": str(source_course_id)},
        }
        return self._send_write_request(
            "POST",
            url,
            f"Copying course {source_course_id} into {destination_course_id}",
            json=migration_request,
        )

    def get_content_migration(self, course_id: int, migration_id: int) -> dict[str, Any] | None:
        """Get the status record of a content migration (course copy)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/content_migrations/{migration_id}"
        return self._get_single_resource(url)

    # =====================================
    # UTILITY METHODS
    # =====================================

    def find_course_by_name(
        self, name_pattern: str, enrollment_type: str = "teacher"
    ) -> list[dict[str, Any]]:
        """Find courses by name or course-code pattern (case-insensitive)."""
        courses = self.get_courses(enrollment_type)
        pattern_lower = name_pattern.lower()

        matching_courses: list[dict[str, Any]] = []
        for course in courses:
            course_name = course.get("name", "").lower()
            course_code = course.get("course_code", "").lower()

            if pattern_lower in course_name or pattern_lower in course_code:
                matching_courses.append(course)

        return matching_courses

    def get_discussion_summary(self, course_id: int, topic_id: int) -> dict[str, Any]:
        """Get a summary of discussion participation."""
        entries = self.get_discussion_entries(course_id, topic_id)
        users = self.get_course_users(course_id, ["student"])

        user_lookup = {user.id: user.name for user in users}

        participation: dict[int, dict[str, Any]] = {}
        total_posts = 0
        total_words = 0

        for entry in entries:
            user_id = entry.user_id
            if user_id not in participation:
                participation[user_id] = {
                    "name": user_lookup.get(user_id, "Unknown User"),
                    "posts": 0,
                    "words": 0,
                    "replies": 0,
                }

            participation[user_id]["posts"] += 1
            participation[user_id]["words"] += entry.word_count
            total_posts += 1
            total_words += entry.word_count

            participation[user_id]["replies"] += len(entry.replies)
            for reply in entry.replies:
                if reply.user_id in participation:
                    participation[reply.user_id]["posts"] += 1
                    participation[reply.user_id]["words"] += reply.word_count
                else:
                    participation[reply.user_id] = {
                        "name": user_lookup.get(reply.user_id, "Unknown User"),
                        "posts": 1,
                        "words": reply.word_count,
                        "replies": 0,
                    }
                total_posts += 1
                total_words += reply.word_count

        return {
            "topic_id": topic_id,
            "total_students": len(users),
            "participating_students": len(participation),
            "total_posts": total_posts,
            "total_words": total_words,
            "average_words_per_post": (total_words / total_posts if total_posts > 0 else 0),
            "participation": participation,
        }

    def get_assignment_by_discussion(
        self, course_id: int, discussion_id: int
    ) -> dict[str, Any] | None:
        """Get the assignment associated with a discussion topic."""
        try:
            url = f"{self.base_url}/api/v1/courses/{course_id}/discussion_topics/{discussion_id}"
            response = self._make_request("GET", url)
            if response and response.status_code == 200:
                discussion_data = response.json()
                assignment_id = discussion_data.get("assignment_id")

                if assignment_id:
                    return self.get_assignment(course_id, assignment_id)
            return None
        except (CanvasAuthError, CanvasRequestError):
            raise
        except Exception as err:
            logger.error(f"Error getting assignment for discussion {discussion_id}: {err}")
            return None

    def submit_grade(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        score: float,
        comment: str | None = None,
    ) -> bool:
        """Submit a grade for a student assignment."""
        try:
            url = (
                f"{self.base_url}/api/v1/courses/{course_id}"
                f"/assignments/{assignment_id}/submissions/{user_id}"
            )

            data: dict[str, Any] = {"submission[posted_grade]": score}

            if comment:
                data["comment[text_comment]"] = comment

            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = self._make_request("PUT", url, data=data, headers=headers)
            if response and response.status_code == 200:
                logger.info(f"Grade submitted successfully for user {user_id}: {score}")
                return True

            status_label = response.status_code if response else "No response"
            logger.error(f"Failed to submit grade for user {user_id}. Status: {status_label}")
            return False

        except (CanvasAuthError, CanvasRequestError):
            raise
        except Exception as err:
            logger.error(f"Error submitting grade for user {user_id}: {err}")
            return False

    def submit_multiple_grades(
        self, course_id: int, assignment_id: int, grades: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Submit multiple grades at once."""
        results = {"successful": 0, "failed": 0}

        for grade_data in grades:
            user_id = grade_data["user_id"]
            score = grade_data["score"]
            comment = grade_data.get("comment")

            if self.submit_grade(course_id, assignment_id, user_id, score, comment):
                results["successful"] += 1
            else:
                results["failed"] += 1

        return results

    def zero_out_ungraded_past_due(
        self,
        course_id: int,
        assignment_ids: list[int],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[int, list[int]]:
        """Assign a grade of 0 to every submission where ``score is None``."""
        zeroed: dict[int, list[int]] = {}

        for assignment_id in assignment_ids:
            zeroed[assignment_id] = []
            submissions = self.get_assignment_submissions(course_id, assignment_id)

            for submission in submissions:
                if submission.score is not None:
                    continue

                success = self.submit_grade(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    user_id=submission.user_id,
                    score=0,
                )
                if success:
                    zeroed[assignment_id].append(submission.user_id)

            if on_progress:
                done = list(zeroed.keys()).index(assignment_id) + 1
                on_progress(done, len(assignment_ids))

        return zeroed


_ORIGINAL_CANVAS_CLIENT = CanvasClient
EnhancedCanvasAPI = CanvasClient


def _active_client_class() -> type[CanvasClient]:
    """Return the active client class, respecting test patches on either alias."""
    if EnhancedCanvasAPI is not _ORIGINAL_CANVAS_CLIENT:
        return EnhancedCanvasAPI
    return CanvasClient


def load_api_key(key_file: str = "api.key") -> str | None:
    """Load an API key from a plain-text file on disk."""
    try:
        with open(key_file, encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        logger.error(
            f"No API key found. Please create a '{key_file}' file with your Canvas API token."
        )
        return None


def _build_session_auth_from_stored_browser_session() -> SessionAuth | None:
    """Return a SessionAuth built from the stored browser-session blob."""
    session_manager = get_browser_session_manager()
    session_blob = session_manager.get_session()
    if not session_blob:
        return None

    cookies = session_blob.get("cookies", [])
    csrf_token = session_blob.get("csrf_token", "")

    try:
        return SessionAuth(cookies=cookies, csrf_token=csrf_token)
    except ValueError as session_error:
        logger.warning(f"Stored browser session is unusable: {session_error}")
        return None


def _try_reuse_browser_session(base_url: str) -> CanvasClient | None:
    """Try to build and validate a client from the stored browser session."""
    session_auth = _build_session_auth_from_stored_browser_session()
    if session_auth is None:
        return None

    client_cls = _active_client_class()
    try:
        client = client_cls(base_url=base_url, auth_strategy=session_auth)
        logger.info("Testing stored browser session against Canvas API...")
        courses = client.get_courses()
        logger.info(f"Stored browser session is valid. Found {len(courses)} courses.")
        return client
    except CanvasAuthError as auth_error:
        logger.warning(f"Stored browser session was rejected by Canvas: {auth_error}")
        with suppress(Exception):
            get_browser_session_manager().delete_session()
        return None
    except Exception as session_error:
        logger.warning(f"Stored browser session could not be reused: {session_error}")
        return None


def _launch_browser_login_fallback(
    base_url: str,
    *,
    wait_for_user_confirmation: Callable[[], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> CanvasClient | None:
    """Launch the browser-login fallback and return a session client."""
    launch_message = "Canvas token unavailable or invalid. Launching browser authentication..."
    logger.info(launch_message)
    if on_status is not None:
        on_status(launch_message)

    if wait_for_user_confirmation is not None:
        login_successful = run_browser_login(
            canvas_base_url=base_url,
            wait_for_user_confirmation=wait_for_user_confirmation,
        )
    else:
        login_successful = run_browser_login(
            canvas_base_url=base_url,
            require_manual_confirmation=False,
        )
    if not login_successful:
        logger.error("Browser authentication fallback did not complete successfully.")
        return None

    fallback_client = _try_reuse_browser_session(base_url)
    if fallback_client is not None:
        success_message = (
            "Browser authentication succeeded. Continuing with Canvas session cookies."
        )
        logger.info(success_message)
        if on_status is not None:
            on_status(success_message)
    return fallback_client


def create_canvas_client(
    base_url: str | None = None,
    api_key: str | SecretStr | None = None,
    *,
    api_token_file: str | Path | None = None,
    prompt_for_base_url: Callable[[str], str] | None = None,
    wait_for_user_confirmation: Callable[[], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> CanvasClient | None:
    """Create and validate a Canvas API client.

    Library code is silent (AGENTS.md 3.8): any prompting or status
    display is performed through the optional callbacks supplied by
    ``ui/``.

    Args:
        base_url: Canvas instance URL.  When omitted, *prompt_for_base_url*
            is called if provided; otherwise ``DEFAULT_CANVAS_URL`` is used.
        api_key: Canvas API token (plain string or ``SecretStr``). When omitted,
            resolved through ``CredentialManager`` then the configured token file.
        api_token_file: Token file to use after secure credential resolution.
        prompt_for_base_url: Optional callback ``(default_url) -> url``
            supplied by ``ui/`` when *base_url* is not given.
        wait_for_user_confirmation: Optional callback supplied by ``ui/``
            that blocks until the user completes browser authentication.
        on_status: Optional callback receiving human-readable status or
            error messages during authentication fallback.

    Returns:
        A validated :class:`CanvasClient` instance, or ``None`` if
        authentication failed.
    """
    if not base_url:
        if prompt_for_base_url is not None:
            entered = prompt_for_base_url(DEFAULT_CANVAS_URL).strip()
            base_url = entered if entered else DEFAULT_CANVAS_URL
        else:
            base_url = DEFAULT_CANVAS_URL

    stored_session_client = _try_reuse_browser_session(base_url)
    if stored_session_client is not None:
        return stored_session_client

    if not api_key:
        api_key = get_credential_manager().get_token() or load_api_key(
            str(api_token_file or DEFAULT_API_TOKEN_FILE)
        )

    if not api_key:
        return _launch_browser_login_fallback(
            base_url,
            wait_for_user_confirmation=wait_for_user_confirmation,
            on_status=on_status,
        )

    client_cls = _active_client_class()
    try:
        client = client_cls(base_url, api_key)

        logger.info("Testing Canvas API connection...")
        courses = client.get_courses()
        logger.info(f"Connected! Found {len(courses)} courses.")

        return client

    except CanvasAuthError as auth_error:
        logger.error(f"Failed to connect to Canvas API: {auth_error}")
        if on_status is not None:
            on_status(str(auth_error))
        return _launch_browser_login_fallback(
            base_url,
            wait_for_user_confirmation=wait_for_user_confirmation,
            on_status=on_status,
        )
    except Exception as err:
        logger.error(f"Failed to connect to Canvas API: {err}")
        return None


def connect_canvas_from_config(config: Any) -> CanvasClient | None:
    """Create and validate a :class:`CanvasClient` from an application config object.

    Reads ``config.discussion.canvas_url`` and ``config.discussion.api_token_file``
    using duck-typing so ``core`` stays decoupled from concrete config or UI types.

    Args:
        config: Configuration object (such as ``AppConfig``) or ``None``.

    Returns:
        A validated :class:`CanvasClient` instance, or ``None`` if connection
        or authentication failed.
    """
    discussion = getattr(config, "discussion", None)
    base_url = getattr(discussion, "canvas_url", None)
    api_token_file = getattr(discussion, "api_token_file", None)
    return create_canvas_client(base_url=base_url, api_token_file=api_token_file)


__all__ = [
    "Assignment",
    "AuthStrategy",
    "CanvasAuthError",
    "CanvasClient",
    "CanvasRequestError",
    "CanvasDiscussion",
    "CanvasDiscussionEntry",
    "CanvasUser",
    "EnhancedCanvasAPI",
    "SessionAuth",
    "Submission",
    "TokenAuth",
    "connect_canvas_from_config",
    "create_canvas_client",
    "load_api_key",
]
