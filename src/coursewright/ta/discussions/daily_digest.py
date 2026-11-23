"""The daily discussion digest: what is new on every board, and what needs you.

Ported from discussorator ``cantrips/daily_digest.py``.  One call scans
every course the instructor actively teaches for discussion posts made
inside a lookback window, labels each post by its role in a three-post
discussion process, flags the ones that need attention, and renders a
markdown digest with Canvas deep links plus a JSON list of every new
post for a downstream drafting agent.

What changed in the port:

- Canvas is reached only through ``coursewright.core.canvas`` (AGENTS.md
  3.12); the source's private ``requests`` path, its retry loop and its
  wall-clock thread are gone because the shared client owns timeouts.
- Instructor matching is configurable (:class:`InstructorMentionMatcher`)
  instead of a hardcoded surname.
- Output defaults to the private workspace, never the repository.
- The module is silent (AGENTS.md 3.8): it returns data and raises.

Behaviour kept from the source, each guarding a past failure:

- **Weekend lookback** -- Monday looks back 72 hours, other days 24.
- **Clock warning** -- a Canvas post newer than "now" means the local
  clock is behind, and the digest says so above everything else.
- **Role-aware flags** -- "unreplied" means different things for each
  post role and is never shown for a peer reply.
- **Raw sidecar + enriched-output protection** -- generated markdown
  always lands in ``.raw.md``; the canonical file is only replaced while
  it still carries :data:`RAW_DIGEST_MARKER`, so a re-run never destroys
  a digest someone has already enriched.
- **Run budget** -- once the budget is spent, remaining courses are
  skipped whole and the digest is marked incomplete.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from coursewright.core.config.manager import resolve_workspace_directory

# Monday (weekday 0) covers the weekend for a weekday-only schedule.
WEEKDAY_LOOKBACK_HOURS = 24
MONDAY_LOOKBACK_HOURS = 72

# A partial digest on time beats a complete one late.
DEFAULT_RUN_BUDGET_SECONDS = 600

# Post text kept in the JSON payload; bounds pathological pastes only.
CANDIDATE_TEXT_CHARACTER_LIMIT = 25_000
# Excerpt shown under each poster's name in the markdown digest.
DIGEST_EXCERPT_CHARACTER_LIMIT = 300

# Written into every generated digest.  An enrichment step that rewrites
# the file does not carry it forward, so its presence means "untouched".
RAW_DIGEST_MARKER = "<!-- coursewright:raw-digest -->"

# Where digests go inside the private workspace.
DIGEST_FOLDER_PARTS = ("exports", "digests")

# --- The three-post process ----------------------------------------------
# Post 1 (research) starts a thread: the student's prompt plus raw AI
# output.  Post 2 (restatement) replies to the student's OWN Post 1 in
# their own words.  Post 3 (peer reply) answers a classmate.  Thread
# position makes the role machine-checkable without sniffing content.
POST_1_RESEARCH = "post1_research"
POST_2_RESTATEMENT = "post2_restatement"
POST_3_PEER_REPLY = "post3_peer_reply"
POST_ROLE_DIGEST_LABELS = {
    POST_1_RESEARCH: "Post 1: research",
    POST_2_RESTATEMENT: "Post 2: restatement",
    POST_3_PEER_REPLY: "Post 3: peer reply",
}
POST_1_MINIMUM_BODY_WORD_COUNT = 200
POST_2_MINIMUM_WORD_COUNT = 300
POST_3_MINIMUM_WORD_COUNT = 150

# Students label their prompt in a few predictable ways, anywhere in the post.
POST_1_PROMPT_MARKER_PATTERN = re.compile(
    r"\b(prompt|i\s+asked|my\s+ask|ai\s+used|deep\s+research)\b", re.IGNORECASE
)
# "Write me a prompt ..." pasted instead of the research it should produce.
# The gap is capped at 40 non-sentence-break characters.
POST_1_PROMPT_GENERATION_PATTERN = re.compile(
    r"\b(craft|write|give|create|make)\b[^.!?\n]{0,40}?\bprompt\b", re.IGNORECASE
)


class DigestError(RuntimeError):
    """Raised when the digest cannot be built or has nowhere safe to go."""


class DigestCanvasClient(Protocol):
    """The four read-only calls the digest needs from the Canvas client."""

    def get_current_user_profile(self) -> dict[str, Any] | None: ...
    def get_active_teacher_courses(self) -> list[dict[str, Any]]: ...
    def get_discussion_topics(self, course_id: int) -> list[dict[str, Any]]: ...
    def get_discussion_view_record(
        self, course_id: int, topic_id: int
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class InstructorMentionMatcher:
    """Decides whether a post is plausibly ADDRESSED to the instructor.

    Two ways to qualify, and deliberately no third:

    1. One of ``instructor_names`` appears anywhere as a whole word.
    2. The post OPENS by addressing a title ("Professor, I'm stuck...").
       Only the opening counts, because the same words deep in a post are
       almost always pasted prompt text ("consult your instructor..."),
       and a false positive outranks a real reply target.

    With no names configured only the opening vocative can match.
    """

    instructor_names: tuple[str, ...] = ()
    direct_address_titles: tuple[str, ...] = ("prof", "professor", "instructor")
    opening_scan_characters: int = 120

    def mentions_instructor(self, plain_text: str) -> bool:
        names = [name.strip() for name in self.instructor_names if name.strip()]
        if names:
            name_pattern = r"\b(" + "|".join(re.escape(name) for name in names) + r")\b"
            if re.search(name_pattern, plain_text, re.IGNORECASE):
                return True
        if not self.direct_address_titles:
            return False
        titles = "|".join(re.escape(title) for title in self.direct_address_titles)
        vocative = rf"^\W*(hi|hey|hello|good\s+\w+)?\W*({titles})\b"
        opening = plain_text[: self.opening_scan_characters]
        return re.search(vocative, opening, re.IGNORECASE) is not None


@dataclass
class DailyDigest:
    """Everything one scan found; rendering and writing are separate steps."""

    digest_date: str
    lookback_hours: int
    course_sections: list[dict[str, Any]] = field(default_factory=list)
    new_posts: list[dict[str, Any]] = field(default_factory=list)
    incomplete_note: str | None = None
    clock_warning: str | None = None

    def to_markdown(self) -> str:
        return build_digest_markdown(
            self.digest_date,
            self.course_sections,
            self.lookback_hours,
            self.incomplete_note,
            self.clock_warning,
        )


@dataclass(frozen=True)
class DigestFiles:
    """Where :func:`write_daily_digest` put things."""

    canonical_path: Path
    written_path: Path
    posts_payload_path: Path
    canonical_was_preserved: bool


def parse_canvas_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def default_lookback_hours(now_utc: datetime) -> int:
    """72 hours on Monday so weekend posts are covered, otherwise 24."""
    return MONDAY_LOOKBACK_HOURS if now_utc.weekday() == 0 else WEEKDAY_LOOKBACK_HOURS


def filter_recently_active_topics(topics: list[dict], cutoff: datetime) -> list[dict]:
    """Keep topics whose last reply landed inside the window.

    Filtering on ``last_reply_at`` first means a quiet discussion costs one
    list call, not a full view call.
    """
    return [
        topic
        for topic in topics
        if topic.get("last_reply_at") and parse_canvas_timestamp(topic["last_reply_at"]) >= cutoff
    ]


def find_newest_activity_timestamp(topics: list[dict]) -> datetime | None:
    """Newest ``last_reply_at`` across topics, the anchor for the clock check."""
    stamps = [parse_canvas_timestamp(t["last_reply_at"]) for t in topics if t.get("last_reply_at")]
    return max(stamps) if stamps else None


def build_clock_skew_warning(now_utc: datetime, newest_activity: datetime | None) -> str | None:
    """Warn when the local clock is behind the newest Canvas post.

    Students cannot post in the future, so a post newer than "now" means
    the clock is behind.  One-directional on purpose: a quiet stretch is a
    slow week, not a broken clock.  Returns ``None`` when all looks fine.
    """
    if newest_activity is None or now_utc >= newest_activity:
        return None
    hours_behind = (newest_activity - now_utc).total_seconds() / 3600
    return (
        f"LOCAL CLOCK IS BEHIND CANVAS by ~{hours_behind:.1f}h. "
        f"This machine thinks it is {now_utc:%Y-%m-%d %H:%M} UTC, but Canvas "
        f"already has posts from {newest_activity:%Y-%m-%d %H:%M} UTC. "
        "The lookback window and the digest date are both wrong. "
        "Fix the clock and re-run before trusting anything below."
    )


def strip_html_to_text(message_html: str | None) -> str:
    """Flatten Canvas message HTML to plain text."""
    plain_text = re.sub(r"<[^>]+>", " ", message_html or "")
    return re.sub(r"\s+", " ", plain_text).strip()


def count_words(plain_text: str) -> int:
    return len(plain_text.split())


def classify_post_role(entry_user_id: int, parent_entry: dict | None) -> str:
    """Thread starter is Post 1; reply to your own post is Post 2; else Post 3."""
    if parent_entry is None:
        return POST_1_RESEARCH
    if parent_entry.get("user_id") == entry_user_id:
        return POST_2_RESTATEMENT
    return POST_3_PEER_REPLY


def build_entry_deep_link(topic_url: str, entry_id: int) -> str:
    """Link to one post.  The Discussions Redesign honours ``?entry_id=``,
    not the legacy ``#entry-`` anchor."""
    return f"{topic_url}?entry_id={entry_id}"


def collect_post_1_completeness_problems(entry: dict) -> list[str]:
    """Ways a Post 1 fails the "prompt AND output" rule; empty otherwise.

    Heuristics on purpose: a false positive costs one skimmed sentence, a
    false negative costs the student points a regex could have saved.
    """
    if entry["post_role"] != POST_1_RESEARCH:
        return []
    problems: list[str] = []
    if not POST_1_PROMPT_MARKER_PATTERN.search(entry["text"]):
        problems.append("no prompt found — Post 1 needs the prompt AND the output")
    if entry["word_count"] < POST_1_MINIMUM_BODY_WORD_COUNT:
        problems.append(
            f"body only {entry['word_count']} words — AI output may be in an attachment"
        )
    if POST_1_PROMPT_GENERATION_PATTERN.search(entry["text"]):
        problems.append("asked the AI to write a prompt — confirm the research output is here")
    return problems


def collect_digest_flags_for_entry(entry: dict) -> list[str]:
    """The role-aware flag list shown after a poster's name.

    - Unreplied Post 1: normal, the restatement comes later -> soft note.
    - Unreplied Post 2: the real reply target -> "unreplied".
    - Unreplied Post 3: ends the chain, so nothing.  A flag that is always
      on teaches the reader to ignore flags.

    ``entry["is_unreplied"]`` itself stays truthful in the JSON payload;
    only its display is suppressed.
    """
    role = entry["post_role"]
    flags: list[str] = []
    if entry["mentions_instructor"]:
        flags.append("**mentions you**")
    if entry["is_unreplied"]:
        if role == POST_1_RESEARCH:
            flags.append("awaiting their Post 2")
        elif role == POST_2_RESTATEMENT:
            flags.append("unreplied")
    minimum = {POST_2_RESTATEMENT: POST_2_MINIMUM_WORD_COUNT,
               POST_3_PEER_REPLY: POST_3_MINIMUM_WORD_COUNT}.get(role)
    if minimum is not None and entry["word_count"] < minimum:
        flags.append(f"short: {entry['word_count']} words, min {minimum}")
    flags.extend(collect_post_1_completeness_problems(entry))
    return flags


def collect_new_entries(
    discussion_view: dict,
    cutoff: datetime,
    instructor_user_id: int,
    matcher: InstructorMentionMatcher | None = None,
) -> list[dict]:
    """Walk a topic's entry tree and keep student posts created in the window."""
    matcher = matcher or InstructorMentionMatcher()
    names_by_user_id = {
        participant["id"]: participant.get("display_name", "Unknown")
        for participant in discussion_view.get("participants", [])
    }
    new_entries: list[dict] = []

    def walk(entry: dict, parent_entry: dict | None) -> None:
        child_replies = entry.get("replies") or []
        # Deleted entries keep their slot in the tree but have no author.
        is_student_post = (
            not entry.get("deleted")
            and entry.get("user_id")
            and entry["user_id"] != instructor_user_id
        )
        if is_student_post and parse_canvas_timestamp(entry["created_at"]) >= cutoff:
            plain_text = strip_html_to_text(entry.get("message"))
            new_entries.append(
                {
                    "entry_id": entry["id"],
                    "student_name": names_by_user_id.get(entry["user_id"], "Unknown"),
                    "created_at": entry["created_at"],
                    "post_role": classify_post_role(entry["user_id"], parent_entry),
                    "word_count": count_words(plain_text),
                    "is_unreplied": not child_replies,
                    "mentions_instructor": matcher.mentions_instructor(plain_text),
                    "text": plain_text[:CANDIDATE_TEXT_CHARACTER_LIMIT],
                }
            )
        for reply in child_replies:
            walk(reply, entry)

    for top_level_entry in discussion_view.get("view", []):
        walk(top_level_entry, None)
    return new_entries


def build_digest_markdown(
    digest_date: str,
    course_sections: list[dict],
    lookback_hours: int,
    incomplete_note: str | None = None,
    clock_warning: str | None = None,
) -> str:
    """Render the digest: one linked poster per post plus a short excerpt.

    A clock warning sits above an incomplete note, because a wrong date
    invalidates the whole document rather than just the missing courses.
    """
    lines = [f"# Daily Discussion Digest — {digest_date}", ""]
    if clock_warning:
        lines += [f"**⚠ STALE — DO NOT TRUST THIS DIGEST: {clock_warning}**", ""]
    if incomplete_note:
        lines += [f"**⚠ INCOMPLETE: {incomplete_note}**", ""]
    lines.append(f"_New posts in the last {lookback_hours} hours._")
    for section in course_sections:
        lines.append(f"\n## {section['course_name']}")
        for topic_block in section["topics"]:
            lines.append(f"\n### [{topic_block['title']}]({topic_block['url']})")
            for entry in topic_block["entries"]:
                entry_url = build_entry_deep_link(topic_block["url"], entry["entry_id"])
                flags = collect_digest_flags_for_entry(entry)
                flag_note = f" ({', '.join(flags)})" if flags else ""
                role_label = POST_ROLE_DIGEST_LABELS[entry["post_role"]]
                lines.append(
                    f"\n**[{entry['student_name']}]({entry_url})** · {role_label}{flag_note}"
                )
                excerpt = entry["text"][:DIGEST_EXCERPT_CHARACTER_LIMIT]
                if len(entry["text"]) > DIGEST_EXCERPT_CHARACTER_LIMIT:
                    excerpt += "…"
                lines.append(f"\n> {excerpt}")
    if not course_sections:
        lines.append("\nNo new discussion posts in the window. Quiet day.")
    lines.append(f"\n{RAW_DIGEST_MARKER}")
    return "\n".join(lines) + "\n"


def write_digest_without_clobbering_enriched(
    digest_path: Path, digest_markdown: str
) -> tuple[Path, bool]:
    """Write the digest without ever destroying an enriched version of it.

    The generated markdown ALWAYS lands in a ``.raw.md`` sidecar.  The
    canonical path is written only when it is missing or still carries
    :data:`RAW_DIGEST_MARKER`.  Returns ``(path_written, canonical_kept)``.
    """
    raw_sidecar_path = digest_path.with_suffix(".raw.md")
    raw_sidecar_path.write_text(digest_markdown, encoding="utf-8")
    holds_enriched_work = (
        digest_path.exists() and RAW_DIGEST_MARKER not in digest_path.read_text(encoding="utf-8")
    )
    if holds_enriched_work:
        return raw_sidecar_path, True
    digest_path.write_text(digest_markdown, encoding="utf-8")
    return digest_path, False


def default_digest_directory(workspace_directory: Path | None = None) -> Path:
    """``<private workspace>/exports/digests``; refuses to guess without one."""
    workspace = workspace_directory or resolve_workspace_directory()
    if workspace is None:
        raise DigestError(
            "No private workspace is configured; set COURSEWRIGHT_WORKSPACE or "
            "pass an explicit output path so the digest stays out of this repository."
        )
    return Path(workspace).joinpath(*DIGEST_FOLDER_PARTS)


def build_daily_digest(
    canvas_client: DigestCanvasClient,
    *,
    now_utc: datetime | None = None,
    lookback_hours: int | None = None,
    matcher: InstructorMentionMatcher | None = None,
    run_budget_seconds: float = DEFAULT_RUN_BUDGET_SECONDS,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> DailyDigest:
    """Scan every actively taught course and return the digest data (read-only)."""
    now_utc = now_utc or datetime.now(UTC)
    lookback_hours = lookback_hours or default_lookback_hours(now_utc)
    cutoff = now_utc - timedelta(hours=lookback_hours)
    run_deadline = monotonic_clock() + run_budget_seconds

    own_profile = canvas_client.get_current_user_profile()
    if not own_profile or "id" not in own_profile:
        raise DigestError("Canvas did not return your own profile (users/self).")
    instructor_user_id = own_profile["id"]

    digest = DailyDigest(digest_date=f"{now_utc:%Y-%m-%d}", lookback_hours=lookback_hours)
    skipped_courses: list[str] = []
    newest_activity: datetime | None = None
    for course in canvas_client.get_active_teacher_courses():
        course_name = course.get("name") or f"Course {course['id']}"
        # Between courses is the natural seam: a course is scanned whole or not at all.
        if monotonic_clock() >= run_deadline:
            skipped_courses.append(course_name)
            continue
        active_topics = filter_recently_active_topics(
            canvas_client.get_discussion_topics(course["id"]), cutoff
        )
        newest_here = find_newest_activity_timestamp(active_topics)
        if newest_here is not None:
            newest_activity = max(filter(None, (newest_activity, newest_here)))
        topic_blocks = []
        for topic in active_topics:
            view = canvas_client.get_discussion_view_record(course["id"], topic["id"])
            if view is None:
                raise DigestError(
                    f"Canvas did not return the discussion view for topic {topic['id']} "
                    f"in course {course['id']}."
                )
            entries = collect_new_entries(view, cutoff, instructor_user_id, matcher)
            if not entries:
                continue
            topic_blocks.append({"title": topic["title"], "url": topic["html_url"],
                                 "entries": entries})
            # Every new post goes in the payload so the drafting agent can
            # summarize each one and choose its own reply target.
            digest.new_posts += [
                {
                    "course": course_name,
                    "course_id": course["id"],
                    "topic": topic["title"],
                    "topic_id": topic["id"],
                    "url": build_entry_deep_link(topic["html_url"], entry["entry_id"]),
                    **entry,
                }
                for entry in entries
            ]
        if topic_blocks:
            digest.course_sections.append({"course_name": course_name, "topics": topic_blocks})

    if skipped_courses:
        digest.incomplete_note = (
            f"run budget ({run_budget_seconds:g}s) exhausted; "
            f"courses NOT scanned: {', '.join(skipped_courses)}"
        )
    digest.clock_warning = build_clock_skew_warning(now_utc, newest_activity)
    return digest


def write_daily_digest(
    digest: DailyDigest,
    digest_path: Path | None = None,
    workspace_directory: Path | None = None,
) -> DigestFiles:
    """Write markdown (sidecar-protected) and the always-refreshed posts JSON."""
    if digest_path is None:
        digest_path = (
            default_digest_directory(workspace_directory)
            / f"daily_digest_{digest.digest_date}.md"
        )
    digest_path = Path(digest_path)
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    written_path, was_preserved = write_digest_without_clobbering_enriched(
        digest_path, digest.to_markdown()
    )
    posts_payload_path = digest_path.with_suffix(".posts.json")
    posts_payload_path.write_text(json.dumps(digest.new_posts, indent=2), encoding="utf-8")
    return DigestFiles(digest_path, written_path, posts_payload_path, was_preserved)
