"""Safe Canvas writes: dry run by default, backup first, never silent.

AGENTS.md rule 3.11 and COURSE_BUILDER ADR-0102.  Canvas has no undo for most
edits and the shells hold enrolled students, so every write that scripts or
commands make goes through :class:`SafeCanvasWriter`:

1. **Dry run by default.**  Without ``execute=True`` a method validates its
   payload and returns a :class:`WriteOutcome` describing what *would* happen.
   It makes no HTTP request at all, not even a read.
2. **Backup before the write.**  With ``execute=True`` the writer reads the
   object's current state, saves it with the payload as timestamped JSON in
   the private workspace, and only then calls the client.
3. **Abort if the backup fails.**  A missing resource, an unreadable
   workspace or an unserialisable payload raises :class:`BackupFailedError`
   and Canvas is never written.
4. **No silent failure.**  If Canvas refuses the write, the client returns
   ``None`` or ``False``; the writer raises :class:`WriteFailedError`
   (naming the backup) instead of passing the failure along as a value.

The raw operations stay on :class:`CanvasClient`; this module adds policy, not
a second HTTP path.  Library code is silent (rule 3.8): it returns outcomes
and raises, and ``ui/`` decides how to show them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from coursewright.core.canvas.client import CanvasClient
from coursewright.core.canvas.dates import validate_canvas_date_fields
from coursewright.core.config.manager import resolve_workspace_directory

# Backups land in <workspace>/backups/canvas/ (private, outside this repo).
BACKUP_FOLDER_PARTS = ("backups", "canvas")


class SafeWriteError(Exception):
    """Base class for every failure the safe writer reports."""


class BackupFailedError(SafeWriteError):
    """The pre-write backup could not be made, so nothing was written."""


class WriteFailedError(SafeWriteError):
    """Canvas did not accept the write (the backup, if any, is kept)."""


@dataclass(frozen=True)
class WriteOutcome:
    """What a safe write did, or in a dry run, would do."""

    operation: str
    target: str
    payload: Any
    executed: bool
    backup_path: Path | None = None
    result: Any = None


def default_backup_directory(workspace_directory: Path) -> Path:
    """Return the backup folder inside the private workspace."""
    return Path(workspace_directory).joinpath(*BACKUP_FOLDER_PARTS)


def write_backup(
    backup_directory: Path,
    operation: str,
    target: str,
    payload: Any,
    state_before: Any,
    now: datetime | None = None,
) -> Path:
    """Save one timestamped JSON backup and return its path.

    The file is opened exclusively so an earlier backup is never overwritten.

    Raises:
        BackupFailedError: on any filesystem or serialisation problem.
    """
    moment = now or datetime.now()  # noqa: DTZ005 - local wall-clock stamp for a file name
    safe_operation = re.sub(r"[^A-Za-z0-9_-]+", "-", operation)
    backup_path = Path(backup_directory) / f"{moment:%Y%m%d-%H%M%S-%f}_{safe_operation}.json"
    record = {
        "operation": operation,
        "target": target,
        "backed_up_at": moment.isoformat(timespec="seconds"),
        "payload": payload,
        "state_before": state_before,
    }
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("x", encoding="utf-8") as backup_file:
            json.dump(record, backup_file, indent=2, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as err:
        raise BackupFailedError(
            f"Backup for {operation} on {target} failed ({err}); nothing was written to Canvas."
        ) from err
    return backup_path


class SafeCanvasWriter:
    """Every Canvas write, behind one dry-run / backup / execute policy."""

    def __init__(
        self,
        client: CanvasClient,
        backup_directory: Path,
        *,
        execute: bool = False,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._client = client
        self._backup_directory = Path(backup_directory)
        self.execute = execute
        self._clock = clock

    # ----- the one policy ---------------------------------------------------

    def _perform(
        self,
        operation: str,
        target: str,
        payload: Any,
        apply_write: Callable[[], Any],
        read_state_before: Callable[[], Any] | None = None,
    ) -> WriteOutcome:
        validate_canvas_date_fields(payload)

        if not self.execute:
            return WriteOutcome(operation, target, payload, executed=False)

        state_before = None
        if read_state_before is not None:
            state_before = read_state_before()
            if state_before is None:
                raise BackupFailedError(
                    f"Cannot back up {target}: Canvas did not return its current state; "
                    "nothing was written."
                )
        backup_path = write_backup(
            self._backup_directory,
            operation,
            target,
            payload,
            state_before,
            self._clock(),
        )

        result = apply_write()
        # The client reports refusal as None / False; surface it loudly.
        if result is None or result is False:
            raise WriteFailedError(
                f"Canvas did not accept {operation} on {target}; backup kept at {backup_path}."
            )
        return WriteOutcome(
            operation, target, payload, executed=True, backup_path=backup_path, result=result
        )

    # ----- updates ----------------------------------------------------------

    def update_course(self, course_id: int, updates: dict[str, Any]) -> WriteOutcome:
        """Update course settings."""
        return self._perform(
            "update_course",
            f"course {course_id}",
            updates,
            lambda: self._client.update_course(course_id, updates),
            lambda: self._client.get_course(course_id),
        )

    def update_module(
        self, course_id: int, module_id: int, updates: dict[str, Any]
    ) -> WriteOutcome:
        """Update a module."""
        return self._perform(
            "update_module",
            f"course {course_id} module {module_id}",
            updates,
            lambda: self._client.update_module(course_id, module_id, updates),
            lambda: self._client.get_module(course_id, module_id),
        )

    def update_module_item(
        self, course_id: int, module_id: int, item_id: int, updates: dict[str, Any]
    ) -> WriteOutcome:
        """Update a module item."""
        return self._perform(
            "update_module_item",
            f"course {course_id} module {module_id} item {item_id}",
            updates,
            lambda: self._client.update_module_item(course_id, module_id, item_id, updates),
            lambda: self._client.get_module_item(course_id, module_id, item_id),
        )

    def move_module_item(
        self,
        course_id: int,
        source_module_id: int,
        item_id: int,
        target_module_id: int,
        position: int | None = None,
    ) -> WriteOutcome:
        """Move a module item to another module."""
        payload = {"module_id": target_module_id, "position": position}
        return self._perform(
            "move_module_item",
            f"course {course_id} module {source_module_id} item {item_id}",
            payload,
            lambda: self._client.move_module_item(
                course_id, source_module_id, item_id, target_module_id, position
            ),
            lambda: self._client.get_module_item(course_id, source_module_id, item_id),
        )

    def reorder_modules(self, course_id: int, module_order: list[int]) -> WriteOutcome:
        """Reorder a course's modules; the backup holds the order they had."""
        return self._perform(
            "reorder_modules",
            f"course {course_id} modules",
            {"order": module_order},
            lambda: self._client.reorder_modules(course_id, module_order),
            lambda: self._client.get_modules(course_id),
        )

    def update_assignment(
        self, course_id: int, assignment_id: int, updates: dict[str, Any]
    ) -> WriteOutcome:
        """Update an assignment (due dates are validated: no ``Z``)."""
        return self._perform(
            "update_assignment",
            f"course {course_id} assignment {assignment_id}",
            updates,
            lambda: self._client.update_assignment(course_id, assignment_id, updates),
            lambda: self._client.get_assignment(course_id, assignment_id),
        )

    def update_assignment_group(
        self, course_id: int, group_id: int, updates: dict[str, Any]
    ) -> WriteOutcome:
        """Update an assignment group."""
        return self._perform(
            "update_assignment_group",
            f"course {course_id} assignment group {group_id}",
            updates,
            lambda: self._client.update_assignment_group(course_id, group_id, updates),
            lambda: self._client.get_assignment_group(course_id, group_id),
        )

    def update_quiz(self, course_id: int, quiz_id: int, updates: dict[str, Any]) -> WriteOutcome:
        """Update a quiz."""
        return self._perform(
            "update_quiz",
            f"course {course_id} quiz {quiz_id}",
            updates,
            lambda: self._client.update_quiz(course_id, quiz_id, updates),
            lambda: self._client.get_quiz(course_id, quiz_id),
        )

    def update_discussion_topic(
        self, course_id: int, topic_id: int, updates: dict[str, Any]
    ) -> WriteOutcome:
        """Update a discussion topic or announcement."""
        return self._perform(
            "update_discussion_topic",
            f"course {course_id} discussion topic {topic_id}",
            updates,
            lambda: self._client.update_discussion_topic(course_id, topic_id, updates),
            lambda: self._client.get_discussion_topic_record(course_id, topic_id),
        )

    def update_page(self, course_id: int, page_url: str, updates: dict[str, Any]) -> WriteOutcome:
        """Update a page; the backup keeps its previous body."""
        return self._perform(
            "update_page",
            f"course {course_id} page {page_url}",
            updates,
            lambda: self._client.update_page(course_id, page_url, updates),
            lambda: self._client.get_page(course_id, page_url),
        )

    def submit_grade(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        score: float,
        comment: str | None = None,
    ) -> WriteOutcome:
        """Grade one student's submission, with an optional comment."""
        payload: dict[str, Any] = {"score": score}
        if comment:
            payload["comment"] = comment
        return self._perform(
            "submit_grade",
            f"course {course_id} assignment {assignment_id} user {user_id}",
            payload,
            lambda: self._client.submit_grade(course_id, assignment_id, user_id, score, comment),
            lambda: self._client.get_submission(course_id, assignment_id, user_id),
        )

    def post_submission_comment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        comment_text: str,
    ) -> WriteOutcome:
        """Add a text comment to one student's submission, grade untouched.

        The submission is read back first so the backup records what the
        student's submission looked like before the comment landed.
        """
        return self._perform(
            "post_submission_comment",
            f"course {course_id} assignment {assignment_id} user {user_id}",
            {"comment": comment_text},
            lambda: self._client.post_submission_comment(
                course_id, assignment_id, user_id, comment_text
            ),
            lambda: self._client.get_submission(course_id, assignment_id, user_id),
        )

    def set_course_nickname(self, course_id: int, nickname: str) -> WriteOutcome:
        """Set the calling user's private course nickname."""
        return self._perform(
            "set_course_nickname",
            f"course {course_id}",
            {"nickname": nickname},
            lambda: self._client.set_course_nickname(course_id, nickname),
            lambda: {"nickname": self._client.get_course_nickname(course_id) or ""},
        )

    # ----- dates and publishing (thin, validated wrappers) -----------------

    def set_assignment_dates(
        self,
        course_id: int,
        assignment_id: int,
        *,
        due_at: str | None = None,
        unlock_at: str | None = None,
        lock_at: str | None = None,
    ) -> WriteOutcome:
        """Set the given assignment dates (``YYYY-MM-DDTHH:MM:SS``, no ``Z``)."""
        date_updates = {
            field_name: value
            for field_name, value in (
                ("due_at", due_at),
                ("unlock_at", unlock_at),
                ("lock_at", lock_at),
            )
            if value is not None
        }
        if not date_updates:
            raise ValueError("set_assignment_dates needs at least one date.")
        return self.update_assignment(course_id, assignment_id, date_updates)

    def set_published(
        self,
        resource_kind: str,
        course_id: int,
        resource_id: int | str,
        published: bool,
        *,
        module_id: int | None = None,
    ) -> WriteOutcome:
        """Publish or unpublish a module, module item, assignment, quiz, page or topic.

        *resource_id* is the page URL slug for ``"page"``; ``"module_item"``
        also needs *module_id*.
        """
        updates = {"published": published}
        if resource_kind == "module":
            return self.update_module(course_id, int(resource_id), updates)
        if resource_kind == "module_item":
            if module_id is None:
                raise ValueError("module_item needs module_id.")
            return self.update_module_item(course_id, module_id, int(resource_id), updates)
        if resource_kind == "assignment":
            return self.update_assignment(course_id, int(resource_id), updates)
        if resource_kind == "quiz":
            return self.update_quiz(course_id, int(resource_id), updates)
        if resource_kind == "page":
            return self.update_page(course_id, str(resource_id), updates)
        if resource_kind == "discussion_topic":
            return self.update_discussion_topic(course_id, int(resource_id), updates)
        raise ValueError(f"Cannot publish a {resource_kind!r}.")

    # ----- creates (nothing to back up but the payload) ---------------------

    def create_module(self, course_id: int, module_data: dict[str, Any]) -> WriteOutcome:
        """Create a module."""
        return self._perform(
            "create_module",
            f"course {course_id}",
            module_data,
            lambda: self._client.create_module(course_id, module_data),
        )

    def create_module_item(
        self, course_id: int, module_id: int, item_data: dict[str, Any]
    ) -> WriteOutcome:
        """Create a module item."""
        return self._perform(
            "create_module_item",
            f"course {course_id} module {module_id}",
            item_data,
            lambda: self._client.create_module_item(course_id, module_id, item_data),
        )

    def create_assignment(self, course_id: int, assignment_data: dict[str, Any]) -> WriteOutcome:
        """Create an assignment."""
        return self._perform(
            "create_assignment",
            f"course {course_id}",
            assignment_data,
            lambda: self._client.create_assignment(course_id, assignment_data),
        )

    def create_assignment_group(self, course_id: int, group_data: dict[str, Any]) -> WriteOutcome:
        """Create an assignment group."""
        return self._perform(
            "create_assignment_group",
            f"course {course_id}",
            group_data,
            lambda: self._client.create_assignment_group(course_id, group_data),
        )

    def create_quiz(self, course_id: int, quiz_data: dict[str, Any]) -> WriteOutcome:
        """Create a quiz."""
        return self._perform(
            "create_quiz",
            f"course {course_id}",
            quiz_data,
            lambda: self._client.create_quiz(course_id, quiz_data),
        )

    def create_quiz_question(
        self, course_id: int, quiz_id: int, question_data: dict[str, Any]
    ) -> WriteOutcome:
        """Create a quiz question."""
        return self._perform(
            "create_quiz_question",
            f"course {course_id} quiz {quiz_id}",
            question_data,
            lambda: self._client.create_quiz_question(course_id, quiz_id, question_data),
        )

    def create_page(self, course_id: int, page_data: dict[str, Any]) -> WriteOutcome:
        """Create a page."""
        return self._perform(
            "create_page",
            f"course {course_id}",
            page_data,
            lambda: self._client.create_page(course_id, page_data),
        )

    def create_discussion_topic(self, course_id: int, topic_data: dict[str, Any]) -> WriteOutcome:
        """Create a discussion topic."""
        return self._perform(
            "create_discussion_topic",
            f"course {course_id}",
            topic_data,
            lambda: self._client.create_discussion_topic(course_id, topic_data),
        )

    def post_announcement(self, course_id: int, title: str, message_html: str) -> WriteOutcome:
        """Publish a course announcement."""
        return self._perform(
            "post_announcement",
            f"course {course_id}",
            {"title": title, "message": message_html},
            lambda: self._client.post_announcement(course_id, title, message_html),
        )

    def post_discussion_reply(
        self, course_id: int, topic_id: int, entry_id: int, message_html: str
    ) -> WriteOutcome:
        """Post an instructor reply under a student's discussion entry."""
        return self._perform(
            "post_discussion_reply",
            f"course {course_id} discussion topic {topic_id} entry {entry_id}",
            {"message": message_html},
            lambda: self._client.post_discussion_entry_reply(
                course_id, topic_id, entry_id, message_html
            ),
        )

    def post_conversation_message(
        self,
        course_id: int | None,
        recipient_id: int,
        subject: str,
        body: str,
    ) -> WriteOutcome:
        """Send one Canvas Conversations message to a single user.

        Conversations have no read-back the write depends on, so the backup
        holds the payload only; there is nothing in Canvas to snapshot first.
        """
        return self._perform(
            "post_conversation_message",
            f"conversation to user {recipient_id}"
            + (f" (course {course_id})" if course_id is not None else ""),
            {
                "context_code": f"course_{course_id}" if course_id is not None else None,
                "recipients": [str(recipient_id)],
                "subject": subject,
                "body": body,
                "force_new": True,
            },
            lambda: self._client.post_conversation_message(
                course_id, recipient_id, subject, body
            ),
        )

    def upload_file(
        self, course_id: int, file_path: str | Path, folder: str | None = None
    ) -> WriteOutcome:
        """Upload a local file to a course."""
        return self._perform(
            "upload_file",
            f"course {course_id}",
            {"file_name": Path(file_path).name, "folder": folder},
            lambda: self._client.upload_file(course_id, file_path, folder),
        )

    def copy_course_content(
        self, destination_course_id: int, source_course_id: int
    ) -> WriteOutcome:
        """Copy one course's content into another.

        The backup records the destination's module list as it was, which is
        the nearest thing Canvas offers to an undo for a copy.
        """
        return self._perform(
            "copy_course_content",
            f"course {destination_course_id}",
            {"source_course_id": source_course_id},
            lambda: self._client.copy_course_content(destination_course_id, source_course_id),
            lambda: {"modules": self._client.get_modules(destination_course_id)},
        )

    # ----- deletes (the backup is the only copy of what was removed) --------

    def delete_module(self, course_id: int, module_id: int) -> WriteOutcome:
        """Delete a module (its content stays in the course)."""
        return self._perform(
            "delete_module",
            f"course {course_id} module {module_id}",
            None,
            lambda: self._client.delete_module(course_id, module_id),
            lambda: self._client.get_module(course_id, module_id),
        )

    def delete_module_item(self, course_id: int, module_id: int, item_id: int) -> WriteOutcome:
        """Delete a module item."""
        return self._perform(
            "delete_module_item",
            f"course {course_id} module {module_id} item {item_id}",
            None,
            lambda: self._client.delete_module_item(course_id, module_id, item_id),
            lambda: self._client.get_module_item(course_id, module_id, item_id),
        )

    def delete_assignment(self, course_id: int, assignment_id: int) -> WriteOutcome:
        """Delete an assignment."""
        return self._perform(
            "delete_assignment",
            f"course {course_id} assignment {assignment_id}",
            None,
            lambda: self._client.delete_assignment(course_id, assignment_id),
            lambda: self._client.get_assignment(course_id, assignment_id),
        )

    def delete_assignment_group(self, course_id: int, group_id: int) -> WriteOutcome:
        """Delete an assignment group."""
        return self._perform(
            "delete_assignment_group",
            f"course {course_id} assignment group {group_id}",
            None,
            lambda: self._client.delete_assignment_group(course_id, group_id),
            lambda: self._client.get_assignment_group(course_id, group_id),
        )

    def delete_discussion_topic(self, course_id: int, topic_id: int) -> WriteOutcome:
        """Delete a discussion topic or announcement."""
        return self._perform(
            "delete_discussion_topic",
            f"course {course_id} discussion topic {topic_id}",
            None,
            lambda: self._client.delete_discussion_topic(course_id, topic_id),
            lambda: self._client.get_discussion_topic_record(course_id, topic_id),
        )

    def delete_page(self, course_id: int, page_url: str) -> WriteOutcome:
        """Delete a page; the backup keeps its body."""
        return self._perform(
            "delete_page",
            f"course {course_id} page {page_url}",
            None,
            lambda: self._client.delete_page(course_id, page_url),
            lambda: self._client.get_page(course_id, page_url),
        )


_UNSELECTED_WORKSPACE = object()


def create_safe_canvas_writer(
    client: CanvasClient,
    *,
    execute: bool = False,
    workspace_directory: Path | None | object = _UNSELECTED_WORKSPACE,
) -> SafeCanvasWriter:
    """Build a writer whose backups go into the private workspace.

    Raises:
        SafeWriteError: when no workspace is configured.  A writer with
            nowhere safe to put backups is refused up front, even for a dry
            run, so the preview never promises a backup it cannot make.
    """
    resolved_workspace = (
        resolve_workspace_directory()
        if workspace_directory is _UNSELECTED_WORKSPACE
        else workspace_directory
    )
    if resolved_workspace is None:
        raise SafeWriteError(
            "No private workspace is configured; set COURSEWRIGHT_WORKSPACE so "
            "Canvas backups have somewhere to go."
        )
    return SafeCanvasWriter(client, default_backup_directory(resolved_workspace), execute=execute)
