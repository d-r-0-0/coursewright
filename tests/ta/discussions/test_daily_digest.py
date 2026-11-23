"""The daily digest's flagging, clock, lookback and file-safety rules.

Ported from discussorator's daily digest cantrip tests.  Each behaviour
here guards a failure seen in real use; every name, course and post
below is synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coursewright.ta.discussions import daily_digest
from coursewright.ta.discussions.daily_digest import (
    RAW_DIGEST_MARKER,
    DigestError,
    InstructorMentionMatcher,
    build_clock_skew_warning,
    build_daily_digest,
    build_digest_markdown,
    collect_digest_flags_for_entry,
    collect_new_entries,
    collect_post_1_completeness_problems,
    default_lookback_hours,
    find_newest_activity_timestamp,
    write_daily_digest,
    write_digest_without_clobbering_enriched,
)


def build_entry(
    post_role: str = "post1_research",
    text: str = "",
    word_count: int = 500,
    is_unreplied: bool = False,
    mentions_instructor: bool = False,
) -> dict:
    """Minimal entry dict shaped like ``collect_new_entries`` output."""
    return {
        "entry_id": 1,
        "student_name": "Sample Student",
        "created_at": "2030-03-06T12:00:00Z",
        "post_role": post_role,
        "word_count": word_count,
        "is_unreplied": is_unreplied,
        "mentions_instructor": mentions_instructor,
        "text": text,
    }


class TestInstructorMentionDetection:
    """Narrow matching: a false positive outranks a real reply target."""

    matcher = InstructorMentionMatcher(instructor_names=("Quillfeather",))

    def test_configured_name_anywhere_counts(self) -> None:
        text = "I got stuck twice on the lab, any advice Professor Quillfeather?"
        assert self.matcher.mentions_instructor(text) is True

    def test_configured_name_alone_counts_without_a_title(self) -> None:
        assert self.matcher.mentions_instructor("Quillfeather said to use the shell.") is True

    def test_no_configured_name_means_names_never_match(self) -> None:
        assert InstructorMentionMatcher().mentions_instructor("Quillfeather said so.") is False

    def test_opening_vocative_counts(self) -> None:
        assert self.matcher.mentions_instructor("Professor, the VPN will not connect.") is True

    def test_greeting_before_the_vocative_still_counts(self) -> None:
        assert self.matcher.mentions_instructor("Hey prof, quick question.") is True

    def test_instructor_inside_pasted_prompt_text_is_ignored(self) -> None:
        text = (
            "You are a senior security researcher. Produce a rigorous report. "
            "Always consult your instructor before applying these controls."
        )
        assert self.matcher.mentions_instructor(text) is False

    def test_the_word_professor_deep_in_a_post_is_ignored(self) -> None:
        text = "A" * 400 + " the professor of record signs off on the change."
        assert self.matcher.mentions_instructor(text) is False

    def test_titles_are_configurable(self) -> None:
        matcher = InstructorMentionMatcher(direct_address_titles=("coach",))
        assert matcher.mentions_instructor("Coach, one question.") is True
        assert matcher.mentions_instructor("Professor, one question.") is False


class TestPostOneCompleteness:
    def test_well_formed_post_one_has_no_problems(self) -> None:
        entry = build_entry(text="My Prompt: research malware. Output: " + "word " * 400)
        assert collect_post_1_completeness_problems(entry) == []

    def test_missing_prompt_is_flagged(self) -> None:
        entry = build_entry(text="Change management is the practice of " + "word " * 400)
        problems = collect_post_1_completeness_problems(entry)
        assert any("no prompt found" in problem for problem in problems)

    def test_short_body_suggests_an_attachment(self) -> None:
        entry = build_entry(text="Prompt: cloud history. Results attached.", word_count=65)
        problems = collect_post_1_completeness_problems(entry)
        assert any("attachment" in problem for problem in problems)

    def test_prompt_generation_request_is_flagged_for_review(self) -> None:
        entry = build_entry(
            text="Craft me a strong deep research prompt about virtualization. "
            + "word " * 400
        )
        problems = collect_post_1_completeness_problems(entry)
        assert any("confirm the research output" in problem for problem in problems)

    def test_non_post_one_roles_are_never_checked(self) -> None:
        entry = build_entry(post_role="post2_restatement", text="short", word_count=10)
        assert collect_post_1_completeness_problems(entry) == []


class TestFlagsAreRoleAware:
    def test_unreplied_post_two_is_flagged(self) -> None:
        entry = build_entry(post_role="post2_restatement", word_count=400, is_unreplied=True)
        assert "unreplied" in collect_digest_flags_for_entry(entry)

    def test_unreplied_post_three_is_silent(self) -> None:
        entry = build_entry(post_role="post3_peer_reply", word_count=200, is_unreplied=True)
        assert "unreplied" not in collect_digest_flags_for_entry(entry)

    def test_unreplied_post_one_gets_the_softer_note(self) -> None:
        entry = build_entry(is_unreplied=True, text="Prompt: x " * 100)
        assert "awaiting their Post 2" in collect_digest_flags_for_entry(entry)

    def test_short_peer_reply_is_flagged(self) -> None:
        entry = build_entry(post_role="post3_peer_reply", word_count=90)
        assert "short: 90 words, min 150" in collect_digest_flags_for_entry(entry)

    def test_mentions_come_first(self) -> None:
        entry = build_entry(post_role="post2_restatement", mentions_instructor=True)
        assert collect_digest_flags_for_entry(entry)[0] == "**mentions you**"


class TestEntryTreeWalk:
    def test_roles_follow_thread_position_and_instructor_is_skipped(self) -> None:
        view = {
            "participants": [{"id": 1, "display_name": "Avery Sample"},
                             {"id": 2, "display_name": "Blake Sample"}],
            "view": [{
                "id": 10, "user_id": 1, "created_at": "2030-03-06T10:00:00Z",
                "message": "<p>Prompt: one</p>",
                "replies": [
                    {"id": 11, "user_id": 1, "created_at": "2030-03-06T11:00:00Z",
                     "message": "restated", "replies": [
                         {"id": 12, "user_id": 2, "created_at": "2030-03-06T12:00:00Z",
                          "message": "peer"},
                         {"id": 13, "user_id": 99, "created_at": "2030-03-06T12:00:00Z",
                          "message": "instructor"},
                     ]},
                    {"id": 14, "deleted": True, "created_at": "2030-03-06T12:00:00Z"},
                ],
            }],
        }
        cutoff = datetime(2030, 3, 6, tzinfo=UTC)
        entries = collect_new_entries(view, cutoff, instructor_user_id=99)
        assert [(e["entry_id"], e["post_role"]) for e in entries] == [
            (10, "post1_research"), (11, "post2_restatement"), (12, "post3_peer_reply")
        ]
        assert entries[0]["text"] == "Prompt: one"
        assert entries[2]["student_name"] == "Blake Sample"
        assert entries[2]["is_unreplied"] is True


class TestLookbackAndClock:
    def test_monday_looks_back_over_the_weekend(self) -> None:
        assert default_lookback_hours(datetime(2030, 3, 4, 6, tzinfo=UTC)) == 72  # Monday
        assert default_lookback_hours(datetime(2030, 3, 5, 6, tzinfo=UTC)) == 24

    def test_no_warning_when_the_clock_leads_canvas(self) -> None:
        now_utc = datetime(2030, 3, 6, 19, tzinfo=UTC)
        assert build_clock_skew_warning(now_utc, now_utc - timedelta(hours=3)) is None

    def test_no_warning_without_activity(self) -> None:
        assert build_clock_skew_warning(datetime(2030, 3, 6, tzinfo=UTC), None) is None

    def test_warning_when_canvas_has_posts_from_the_future(self) -> None:
        now_utc = datetime(2030, 3, 2, 19, tzinfo=UTC)
        warning = build_clock_skew_warning(now_utc, datetime(2030, 3, 6, 18, 41, tzinfo=UTC))
        assert warning is not None and "LOCAL CLOCK IS BEHIND CANVAS" in warning

    def test_newest_activity_picks_the_latest_topic(self) -> None:
        topics = [{"last_reply_at": "2030-03-04T10:00:00Z"},
                  {"last_reply_at": "2030-03-06T18:41:00Z"}, {"last_reply_at": None}]
        assert find_newest_activity_timestamp(topics) == datetime(2030, 3, 6, 18, 41, tzinfo=UTC)
        assert find_newest_activity_timestamp([]) is None


class TestEnrichedDigestIsNeverClobbered:
    def test_canonical_path_is_written_when_missing(self, tmp_path) -> None:
        digest_path = tmp_path / "daily_digest_2030-03-06.md"
        markdown = f"# Digest\n\n{RAW_DIGEST_MARKER}\n"
        assert write_digest_without_clobbering_enriched(digest_path, markdown) == (
            digest_path, False)
        assert digest_path.read_text(encoding="utf-8") == markdown
        assert (tmp_path / "daily_digest_2030-03-06.raw.md").exists()

    def test_an_unenriched_digest_is_refreshed_in_place(self, tmp_path) -> None:
        digest_path = tmp_path / "daily_digest_2030-03-06.md"
        digest_path.write_text(f"# Stale\n{RAW_DIGEST_MARKER}\n", encoding="utf-8")
        written, preserved = write_digest_without_clobbering_enriched(
            digest_path, f"# Fresh\n{RAW_DIGEST_MARKER}\n")
        assert (written, preserved) == (digest_path, False)
        assert "Fresh" in digest_path.read_text(encoding="utf-8")

    def test_enriched_digest_survives_a_rerun(self, tmp_path) -> None:
        digest_path = tmp_path / "daily_digest_2030-03-06.md"
        enriched = "# Daily Discussion Digest\n\nReal summaries and a drafted reply.\n"
        digest_path.write_text(enriched, encoding="utf-8")
        written, preserved = write_digest_without_clobbering_enriched(
            digest_path, f"# Raw\n{RAW_DIGEST_MARKER}\n")
        assert preserved is True
        assert written == tmp_path / "daily_digest_2030-03-06.raw.md"
        assert digest_path.read_text(encoding="utf-8") == enriched


class TestDigestMarkdownBanners:
    def test_clock_warning_appears_above_everything(self) -> None:
        markdown = build_digest_markdown("2030-03-02", [], 72, None, "LOCAL CLOCK IS BEHIND")
        assert markdown.index("DO NOT TRUST") < markdown.index("New posts in the last")

    def test_raw_marker_is_present_on_generated_digests(self) -> None:
        assert RAW_DIGEST_MARKER in build_digest_markdown("2030-03-06", [], 24)


class FakeDigestClient:
    """Synthetic stand-in for the shared Canvas client's read calls."""

    def __init__(self, courses: list[dict], topics: dict, views: dict) -> None:
        self.courses, self.topics, self.views = courses, topics, views

    def get_current_user_profile(self) -> dict:
        return {"id": 99}

    def get_active_teacher_courses(self) -> list[dict]:
        return self.courses

    def get_discussion_topics(self, course_id: int) -> list[dict]:
        return self.topics.get(course_id, [])

    def get_discussion_view_record(self, course_id: int, topic_id: int) -> dict | None:
        return self.views.get((course_id, topic_id))


def sample_client() -> FakeDigestClient:
    topic = {"id": 5, "title": "Week 1", "last_reply_at": "2030-03-06T11:00:00Z",
             "html_url": "https://canvas.example.edu/courses/1/discussion_topics/5"}
    quiet = {"id": 6, "title": "Old", "last_reply_at": "2030-01-01T00:00:00Z",
             "html_url": "https://canvas.example.edu/courses/1/discussion_topics/6"}
    view = {"participants": [{"id": 1, "display_name": "Avery Sample"}],
            "view": [{"id": 10, "user_id": 1, "created_at": "2030-03-06T10:00:00Z",
                      "message": "Hey prof, my prompt was..."}]}
    return FakeDigestClient(
        courses=[{"id": 1, "name": "Sample Course A"}, {"id": 2, "name": "Sample Course B"}],
        topics={1: [topic, quiet]},
        views={(1, 5): view},
    )


class TestBuildAndWrite:
    now_utc = datetime(2030, 3, 6, 12, tzinfo=UTC)  # a Wednesday

    def test_scan_collects_new_posts_with_deep_links(self) -> None:
        digest = build_daily_digest(sample_client(), now_utc=self.now_utc)
        assert digest.lookback_hours == 24 and digest.clock_warning is None
        assert [section["course_name"] for section in digest.course_sections] == [
            "Sample Course A"]
        (post,) = digest.new_posts
        assert post["url"].endswith("/discussion_topics/5?entry_id=10")
        assert post["mentions_instructor"] is True

    def test_spent_budget_skips_courses_and_says_so(self) -> None:
        ticks = iter([0.0, 0.0, 999.0])
        digest = build_daily_digest(sample_client(), now_utc=self.now_utc,
                                    run_budget_seconds=10, monotonic_clock=lambda: next(ticks))
        assert "Sample Course B" in digest.incomplete_note

    def test_stale_local_clock_is_reported(self) -> None:
        digest = build_daily_digest(sample_client(), now_utc=self.now_utc - timedelta(days=4),
                                    lookback_hours=200)
        assert digest.clock_warning is not None

    def test_missing_view_is_never_silent(self) -> None:
        client = sample_client()
        client.views.clear()
        with pytest.raises(DigestError):
            build_daily_digest(client, now_utc=self.now_utc)

    def test_default_output_is_the_private_workspace(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("COURSEWRIGHT_WORKSPACE", str(tmp_path))
        files = write_daily_digest(build_daily_digest(sample_client(), now_utc=self.now_utc))
        assert files.canonical_path == (
            tmp_path / "exports" / "digests" / "daily_digest_2030-03-06.md")
        assert files.posts_payload_path.exists()
        assert files.canonical_path.with_suffix(".raw.md").exists()

    def test_no_workspace_refuses_to_guess(self, monkeypatch) -> None:
        monkeypatch.delenv("COURSEWRIGHT_WORKSPACE", raising=False)
        monkeypatch.setattr(daily_digest, "resolve_workspace_directory", lambda: None)
        with pytest.raises(DigestError):
            daily_digest.default_digest_directory()
