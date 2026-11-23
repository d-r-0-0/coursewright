"""Announcements: markdown to HTML, scheduled posting, listing and outlines.

COURSE_BUILDER had no tests for these; they are new and pin the behaviour of
the ported code.  All course data is synthetic.
"""

from __future__ import annotations

import json

import pytest
from publishing_fakes import make_response

from coursewright.core.canvas.client import CanvasAuthError
from coursewright.core.canvas.safe_write import SafeCanvasWriter, WriteFailedError
from coursewright.design.publishing import announcements as announcement_tools
from coursewright.design.publishing._strict_reads import CanvasReadError

COURSE_ID = 55
TOPICS_PATH = rf"/api/v1/courses/{COURSE_ID}/discussion_topics"

ANNOUNCEMENT_MARKDOWN = """# Week 3: Example topic

**Post Date:** Monday
**Subject:** Hello

---

## Welcome

Plain **bold** and *italic* with `code` and https://example.edu/page here.

- first
- second

1. one
2. two

| Item | Date |
|------|------|
| Lab | Friday |

```
print("hi")
```

---

### Small heading

[link text](https://example.edu/x)
"""


def make_writer(client, tmp_path, *, execute):
    return SafeCanvasWriter(client, tmp_path / "backups", execute=execute)


# ----- markdown_to_html ------------------------------------------------------


def test_header_block_is_dropped_and_body_is_converted():
    html = announcement_tools.markdown_to_html(ANNOUNCEMENT_MARKDOWN)

    assert "Week 3" not in html
    assert "Post Date" not in html
    assert ">Welcome</h2>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert ">code</code>" in html
    assert '<a href="https://example.edu/page">https://example.edu/page</a>' in html
    assert html.count("<li ") == 4
    assert "<ul " in html
    assert "<ol " in html
    assert '<th style="' in html
    assert "<td " in html
    assert 'print("hi")' in html
    assert ">Small heading</h3>" in html
    assert '<a href="https://example.edu/x">link text</a>' in html


def test_horizontal_rule_is_the_section_separator():
    html = announcement_tools.markdown_to_html(ANNOUNCEMENT_MARKDOWN)

    assert announcement_tools.SECTION_SEPARATOR in html
    assert len(html.split(announcement_tools.SECTION_SEPARATOR)) == 2


# ----- schedule and settings -------------------------------------------------


def test_schedule_lines_become_units_and_comments_are_skipped(tmp_path):
    schedule_path = tmp_path / "schedule.txt"
    schedule_path.write_text(
        "# Unit Name\tAssigned\tFirst post\tDue\n"
        "\n"
        "Unit 01\t01/12/2099\t01/16/2099\t01/23/2099\n"
        "Unit 2\t01/19/2099\t01/23/2099\t01/30/2099\n"
        "Finals\tx\ty\tz\n"
        "Unit 03\tonly-two-columns\n",
        encoding="utf-8",
    )

    units = announcement_tools.parse_schedule(schedule_path)

    assert [unit["unit_num"] for unit in units] == [1, 2]
    assert units[0] == {
        "unit_num": 1,
        "unit_name": "Unit 01",
        "assigned": "01/12/2099",
        "first_post": "01/16/2099",
        "due": "01/23/2099",
    }


def test_course_settings_load_from_a_toml_file(tmp_path):
    settings_path = tmp_path / "announcements.toml"
    settings_path.write_text(
        '[courses.XYZ101]\ncourse_id = 999\ncourse_name = "Intro to Examples"\n'
        'discussion_units = [1, 3]\noffice_hours = "By appointment"\n',
        encoding="utf-8",
    )

    settings = announcement_tools.load_announcement_course_settings(settings_path, "XYZ101")

    assert settings.course_id == 999
    assert settings.discussion_units == (1, 3)
    assert settings.help_module == ""
    with pytest.raises(KeyError, match="NOPE"):
        announcement_tools.load_announcement_course_settings(settings_path, "NOPE")


def test_announcement_ids_round_trip(tmp_path):
    folder = tmp_path / "announcements"
    assert announcement_tools.load_announcement_ids(folder) == {}

    announcement_tools.save_announcement_ids(folder, {"1": 101, "2": 102})

    assert announcement_tools.load_announcement_ids(folder) == {"1": 101, "2": 102}


# ----- pushing and updating --------------------------------------------------


def test_create_announcement_previews_without_a_request(server, client, tmp_path):
    outcome = announcement_tools.create_announcement(
        make_writer(client, tmp_path, execute=False),
        COURSE_ID,
        "Week 1",
        "<p>Hi</p>",
        "2099-01-12T08:00:00",
    )

    assert outcome.executed is False
    assert outcome.payload["delayed_post_at"] == "2099-01-12T08:00:00"
    assert server.requests_sent == []


def test_create_announcement_rejects_a_z_suffixed_date_even_in_a_preview(server, client, tmp_path):
    with pytest.raises(ValueError, match="delayed_post_at"):
        announcement_tools.create_announcement(
            make_writer(client, tmp_path, execute=False),
            COURSE_ID,
            "Week 1",
            "<p>Hi</p>",
            "2099-01-12T08:00:00Z",
        )

    assert server.requests_sent == []


def test_create_announcement_execute_backs_up_the_payload_then_posts(server, client, tmp_path):
    sent_bodies = []

    def accept_post(url, kwargs):
        sent_bodies.append(kwargs["json"])
        assert list((tmp_path / "backups").glob("*.json"))  # the backup came first
        return make_response(201, {"id": 7})

    server.route("POST", TOPICS_PATH, accept_post)

    outcome = announcement_tools.create_announcement(
        make_writer(client, tmp_path, execute=True),
        COURSE_ID,
        "Week 1",
        "<p>Hi</p>",
        "2099-01-12T08:00:00",
    )

    assert outcome.result == {"id": 7}
    assert sent_bodies == [
        {
            "title": "Week 1",
            "message": "<p>Hi</p>",
            "is_announcement": True,
            "delayed_post_at": "2099-01-12T08:00:00",
            "published": True,
        }
    ]


def test_create_announcement_refused_by_canvas_raises(server, client, tmp_path):
    server.route("POST", TOPICS_PATH, make_response(500))

    with pytest.raises(WriteFailedError):
        announcement_tools.create_announcement(
            make_writer(client, tmp_path, execute=True),
            COURSE_ID,
            "Week 1",
            "<p>Hi</p>",
            "2099-01-12T08:00:00",
        )


def test_update_announcement_keeps_the_old_body_in_the_backup(server, client, tmp_path):
    server.route("GET", TOPICS_PATH + r"/7", make_response(200, {"id": 7, "message": "<p>old</p>"}))
    server.route("PUT", TOPICS_PATH + r"/7", make_response(200, {"id": 7}))

    outcome = announcement_tools.update_announcement(
        make_writer(client, tmp_path, execute=True), COURSE_ID, 7, "<p>new</p>"
    )

    saved = json.loads(outcome.backup_path.read_text(encoding="utf-8"))
    assert saved["state_before"]["message"] == "<p>old</p>"
    assert saved["payload"] == {"message": "<p>new</p>"}


def test_update_announcement_previews_without_a_request(server, client, tmp_path):
    outcome = announcement_tools.update_announcement(
        make_writer(client, tmp_path, execute=False), COURSE_ID, 7, "<p>new</p>"
    )

    assert outcome.executed is False
    assert server.requests_sent == []


# ----- listing and outlines --------------------------------------------------


def test_announcements_are_listed_with_their_posted_dates(server, client):
    server.route(
        "GET",
        TOPICS_PATH,
        make_response(
            200,
            [
                {"id": 1, "title": "One", "posted_at": "2099-01-12T08:00:00Z"},
                {"id": 2, "title": "Delayed", "posted_at": None},
            ],
        ),
    )

    listing = announcement_tools.list_announcements(client, COURSE_ID)

    assert [(a.announcement_id, a.posted_date, a.title) for a in listing] == [
        (1, "2099-01-12", "One"),
        (2, "N/A", "Delayed"),
    ]


def test_a_failed_listing_is_an_error_not_an_empty_list(server, client):
    server.route("GET", TOPICS_PATH, make_response(500, []))

    with pytest.raises(CanvasReadError):
        announcement_tools.list_announcements(client, COURSE_ID)


def test_a_rejected_listing_raises_an_auth_error(server, client):
    server.route("GET", TOPICS_PATH, make_response(401, []))

    with pytest.raises(CanvasAuthError):
        announcement_tools.list_announcements(client, COURSE_ID)


def test_outline_lists_h2_headings_per_section_in_post_order(server, client):
    separator = announcement_tools.SECTION_SEPARATOR
    server.route(
        "GET",
        TOPICS_PATH,
        make_response(
            200,
            [
                {
                    "id": 2,
                    "title": "Later",
                    "delayed_post_at": "2099-02-01T08:00:00Z",
                    "message": "<h2>Late <b>news</b></h2>",
                },
                {
                    "id": 1,
                    "title": "Earlier",
                    "delayed_post_at": "2099-01-01T08:00:00Z",
                    "message": f'<h2 style="x">Welcome</h2>{separator}<p>no heading</p>',
                },
            ],
        ),
    )

    outlines = announcement_tools.outline_announcements(client, COURSE_ID)

    assert [outline.announcement_id for outline in outlines] == [1, 2]
    assert outlines[0].section_headings == ("Welcome", "(no h2)")
    assert outlines[1].section_headings == ("Late news",)
