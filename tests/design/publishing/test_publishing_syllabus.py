"""The shared syllabus renders per course, and never ships a blank field.

Ported from COURSE_BUILDER ``tests/test_publish_syllabus.py``.  The source
rendered a real course's template from ``materials/``; these tests build a
synthetic template and facts file in ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest
from publishing_fakes import make_response

from coursewright.core.canvas.safe_write import WriteFailedError
from coursewright.design.publishing import syllabus as publish

COURSE_ID = 4321

TEMPLATE_TEXT = """<!-- editor note: not for students -->
# {{course_title}}

{{outcomes}}

Textbook code: {{textbook_code}}

| Item | Weight |
|------|--------|
| Labs | 40% |
"""

COURSES_TEXT = f"""
[term]
term_name = "Fall 2099"

[courses.XYZ101]
canvas_id = {COURSE_ID}
course_code = "XYZ101"
course_title = "Intro to Examples"
textbook_code = "EXAMPLECODE2099"
outcomes = ["Explain examples", "Build examples"]
"""


@pytest.fixture
def syllabus_files(tmp_path):
    template_path = tmp_path / "syllabus.md"
    template_path.write_text(TEMPLATE_TEXT, encoding="utf-8")
    courses_path = tmp_path / "courses.toml"
    courses_path.write_text(COURSES_TEXT, encoding="utf-8")
    return template_path, courses_path


# ----- rendering (the source tests) ------------------------------------------


def test_fields_lists_and_dropped_lines():
    template = "# {{title}}\n\n{{outcomes}}\n\n{{isbn}}\nEnd\n"
    out = publish.render_markdown(
        template, {"title": "CS I", "outcomes": ["One", "Two"], "isbn": ""}
    )
    assert out == "# CS I\n\n- One\n- Two\n\nEnd\n"


def test_comments_are_stripped_before_publishing():
    out = publish.render_markdown("<!-- editor note -->\nBody\n", {})
    assert out == "Body\n"


def test_a_field_missing_from_courses_toml_stops_the_render():
    with pytest.raises(KeyError, match="zybook_code"):
        publish.render_markdown("Code {{zybook_code}}", {})


def test_a_course_renders_with_its_code_and_no_leftover_fields(syllabus_files):
    template_path, courses_path = syllabus_files

    fields, html = publish.render_syllabus_for_course("XYZ101", template_path, courses_path)

    assert fields["term_name"] == "Fall 2099"  # [term] merged into the course
    assert "EXAMPLECODE2099" in html
    assert "<li>Explain examples</li>" in html
    assert "{{" not in html
    assert "editor note" not in html
    assert "<table>" in html


def test_unknown_course_is_refused(syllabus_files):
    _template_path, courses_path = syllabus_files
    with pytest.raises(KeyError, match="XYZ999"):
        publish.load_course("XYZ999", courses_path)


# ----- publishing ------------------------------------------------------------

COURSE_PATH = rf"/api/v1/courses/{COURSE_ID}"
PAGE_PATH = rf"/api/v1/courses/{COURSE_ID}/pages/syllabus"


def serve_course(server, *, page=True, course_code="XYZ101-SECTION-A", writable=True):
    course = {"id": COURSE_ID, "course_code": course_code, "syllabus_body": "<p>old</p>"}
    server.route("GET", COURSE_PATH, make_response(200, course))
    if page:
        server.route(
            "GET", PAGE_PATH, make_response(200, {"url": "syllabus", "body": "<p>old</p>"})
        )
    else:
        server.route("GET", PAGE_PATH, make_response(404))
    status = 200 if writable else 500
    server.route("PUT", COURSE_PATH, make_response(status, course))
    server.route("PUT", PAGE_PATH, make_response(status, {"url": "syllabus"}))


def test_publish_defaults_to_a_preview_that_writes_nothing(server, client, tmp_path):
    serve_course(server)
    fields = {"canvas_id": COURSE_ID, "course_code": "XYZ101"}

    report = publish.publish_syllabus(client, fields, "<p>new</p>", tmp_path / "backups")

    assert report.executed is False
    assert report.page_found is True
    assert report.current_tab_characters == len("<p>old</p>")
    assert "PUT" not in server.methods_sent
    assert not (tmp_path / "backups").exists()


def test_execute_backs_up_the_tab_and_page_before_each_write(server, client, tmp_path):
    serve_course(server)
    backup_directory = tmp_path / "backups"
    backups_at_each_put = []

    def note_backups_when_putting(method, url):
        if method == "PUT":
            backups_at_each_put.append(len(list(backup_directory.glob("*.json"))))

    server.before_request = note_backups_when_putting
    fields = {"canvas_id": COURSE_ID, "course_code": "XYZ101"}

    report = publish.publish_syllabus(client, fields, "<p>new</p>", backup_directory, execute=True)

    assert report.executed is True
    assert report.verified is False  # the fake still serves the old tab body
    assert backups_at_each_put == [1, 2]  # one more backup precedes each of the two writes
    assert len(report.backup_paths) == 2
    tab_backup = json.loads(report.backup_paths[0].read_text(encoding="utf-8"))
    assert tab_backup["state_before"]["syllabus_body"] == "<p>old</p>"


def test_without_a_syllabus_page_only_the_tab_is_written(server, client, tmp_path):
    serve_course(server, page=False)
    fields = {"canvas_id": COURSE_ID, "course_code": "XYZ101"}

    report = publish.publish_syllabus(
        client, fields, "<p>new</p>", tmp_path / "backups", execute=True
    )

    assert report.page_found is False
    assert server.methods_sent.count("PUT") == 1


def test_a_course_with_the_wrong_code_is_refused_before_any_write(server, client, tmp_path):
    serve_course(server, course_code="OTHER200")
    fields = {"canvas_id": COURSE_ID, "course_code": "XYZ101"}

    with pytest.raises(publish.SyllabusPublishError, match="is not XYZ101"):
        publish.publish_syllabus(client, fields, "<p>new</p>", tmp_path / "backups", execute=True)

    assert "PUT" not in server.methods_sent


def test_canvas_refusing_the_tab_update_raises(server, client, tmp_path):
    serve_course(server, writable=False)
    fields = {"canvas_id": COURSE_ID, "course_code": "XYZ101"}

    with pytest.raises(WriteFailedError):
        publish.publish_syllabus(client, fields, "<p>new</p>", tmp_path / "backups", execute=True)


def test_readback_checks_the_published_text(server, client, tmp_path):
    serve_course(server)
    fields = {"canvas_id": COURSE_ID, "course_code": "XYZ101"}

    report = publish.publish_syllabus(
        client,
        fields,
        "<p>new</p>",
        tmp_path / "backups",
        execute=True,
        verification_text="never appears in the stubbed readback",
    )

    assert report.verified is False


def test_readback_verifies_the_full_html_by_default(server, client, tmp_path):
    serve_course(server, page=False)
    fields = {"canvas_id": COURSE_ID, "course_code": "XYZ101"}
    course = {"id": COURSE_ID, "course_code": "XYZ101", "syllabus_body": "<p>old</p>"}
    server.routes[0] = (
        "GET", server.routes[0][1],
        [make_response(200, course), make_response(200, course),
         make_response(200, {**course, "syllabus_body": "<p>new</p>"})],
    )

    report = publish.publish_syllabus(client, fields, "<p>new</p>", tmp_path, execute=True)

    assert report.verified is True
