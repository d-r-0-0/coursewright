"""Unit tests for Canvas module section hints and propose_units integration."""

from __future__ import annotations

import csv
from decimal import Decimal
from types import SimpleNamespace

from coursewright.ta.zybooks.importer import (
    UnitMapping,
    calculate,
    extract_module_section_hints,
    propose_units,
    read_snapshot,
)


def write_chapter_split_csv(path, *, chapters=(11,), activity_end=6, lab_start=7, lab_end=12):
    """Create a synthetic CSV where early sections are activities and later sections are labs."""
    sections = []
    columns = []
    for ch in chapters:
        for idx in range(1, lab_end + 1):
            sections.append(f"{ch}.{idx}")
    for category in ("Participation", "Challenge", "Lab"):
        for sec in sections:
            idx = int(sec.split(".")[1])
            if category in {"Participation", "Challenge"} and idx <= activity_end:
                maximum = 10 if category == "Participation" else 5
                score = 80 if category == "Participation" else 60
            elif category == "Lab" and lab_start <= idx <= lab_end:
                maximum = 10
                score = 100 - (idx - lab_start) * 10
            else:
                maximum = 0
                score = "N/A"
            columns.append((f"{sec} - {category} ({maximum})", score))

    pa_total = sum(
        max_pts
        for (name, _), max_pts in (
            ((n, int(n.split("(")[1][:-1])), int(n.split("(")[1][:-1])) for n, _ in columns
        )
        if "Participation" in name
    )
    ca_total = sum(
        int(n.split("(")[1][:-1]) for n, _ in columns if "Challenge" in n
    )
    lab_total = sum(
        int(n.split("(")[1][:-1]) for n, _ in columns if "Lab" in n
    )
    headers = [
        "Last name",
        "First name",
        "Primary email",
        "Class section",
        f"Total ({pa_total + ca_total + lab_total})",
        f"Participation total ({pa_total})",
        f"Challenge total ({ca_total})",
        f"Lab total ({lab_total})",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers + [name for name, _ in columns])
        writer.writerow(
            ["Example", "Student", "example@example.invalid", "", 75, 80, 60, 80]
            + [score for _, score in columns]
        )
    return path


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeModuleClient:
    def __init__(self, modules_payload, pages_by_url, *, base_url="https://canvas.example.edu"):
        self.base_url = base_url
        self.modules_payload = modules_payload
        self.pages_by_url = pages_by_url
        self.calls = []

    def _make_request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/modules"):
            return self.modules_payload
        for slug, page in self.pages_by_url.items():
            if url.endswith(f"/pages/{slug}"):
                if isinstance(page, Exception):
                    raise page
                return page
        return FakeResponse({"message": "not found"}, status_code=404)


def test_extract_module_section_hints_parses_html_ranges_and_sorts_numerically():
    snapshot_sections = tuple(f"11.{i}" for i in range(1, 13))
    modules = FakeResponse(
        [
            {
                "id": 10,
                "name": "Unit 4",
                "items": [
                    {"type": "Page", "page_url": "unit-4-overview"},
                    {"type": "Assignment", "content_id": 201},
                    {"type": "Assignment", "content_id": 202},
                    {"type": "Assignment", "content_id": True},
                    {"type": "SubHeader", "title": "Labs"},
                ],
            },
            {
                "id": 11,
                "name": "Overview Only",
                "items": [{"type": "Page", "page_url": "should-not-fetch"}],
            },
            {
                "id": 12,
                "name": "Assignments Only",
                "items": [{"type": "Assignment", "content_id": 999}],
            },
        ]
    )
    pages = {
        "unit-4-overview": FakeResponse(
            {
                "title": "Unit 4 Overview",
                "body": (
                    "<p>Using Python 3.0 to 3.12 (v0.1).</p>"
                    "<p>Complete zyBooks <b>11.1</b>&ndash;<b>11.4</b>, "
                    "plus 11.10, and 99.1.</p>"
                ),
            }
        )
    }
    client = FakeModuleClient(modules, pages, base_url="https://canvas.example.edu/")

    hints = extract_module_section_hints(client, 42, snapshot_sections)

    assert hints == {
        201: ("11.1", "11.2", "11.3", "11.4", "11.10"),
        202: ("11.1", "11.2", "11.3", "11.4", "11.10"),
    }
    assert client.calls == [
        (
            "GET",
            "https://canvas.example.edu/api/v1/courses/42/modules",
            {"params": {"include[]": "items", "per_page": 100}},
        ),
        (
            "GET",
            "https://canvas.example.edu/api/v1/courses/42/pages/unit-4-overview",
            {},
        ),
    ]


def test_extract_module_section_hints_handles_raw_list_and_multiple_pages():
    snapshot_sections = ("2.1", "2.2", "2.3", "2.10")
    modules = [
        {
            "id": 1,
            "items": [
                {"type": "Page", "page_url": "intro"},
                {"type": "Page", "page_url": "extra"},
                {"type": "Assignment", "content_id": 50},
            ],
        },
        {
            "id": 2,
            "items": [
                {"type": "Page", "page_url": "intro"},
                {"type": "Assignment", "content_id": 51},
            ],
        },
    ]
    pages = {
        "intro": {"body": "<p>Sections 2.1 through 2.2</p>"},
        "extra": {"title": "Also 2.10", "body": None},
    }
    client = FakeModuleClient(modules, pages)

    hints = extract_module_section_hints(client, 7, snapshot_sections)

    assert hints == {
        50: ("2.1", "2.2", "2.10"),
        51: ("2.1", "2.2"),
    }
    # Shared page slug "intro" is fetched once per call.
    page_calls = [url for _, url, _ in client.calls if "/pages/" in url]
    assert len(page_calls) == 2


def test_extract_module_section_hints_degrades_gracefully_on_failures():
    sections = ("1.1", "1.2")

    assert extract_module_section_hints(None, 42, sections) == {}
    assert extract_module_section_hints(SimpleNamespace(), 42, sections) == {}
    assert extract_module_section_hints(
        SimpleNamespace(base_url="", _make_request=lambda *a, **k: []), 42, sections
    ) == {}
    assert extract_module_section_hints(
        SimpleNamespace(base_url="https://canvas.example.edu", _make_request=lambda *a, **k: None),
        42,
        sections,
    ) == {}
    assert extract_module_section_hints(
        SimpleNamespace(
            base_url="https://canvas.example.edu",
            _make_request=lambda *a, **k: FakeResponse([], status_code=500),
        ),
        42,
        sections,
    ) == {}

    def raise_err(*_args, **_kwargs):
        raise RuntimeError("network down")

    assert extract_module_section_hints(
        SimpleNamespace(base_url="https://canvas.example.edu", _make_request=raise_err),
        42,
        sections,
    ) == {}

    bad_json_resp = SimpleNamespace(status_code=200, json=lambda: (_ for _ in ()).throw(ValueError))
    assert extract_module_section_hints(
        SimpleNamespace(
            base_url="https://canvas.example.edu", _make_request=lambda *a, **k: bad_json_resp
        ),
        42,
        sections,
    ) == {}

    # One broken page in a module does not prevent reading a sibling page.
    modules = FakeResponse(
        [
            {
                "id": 1,
                "items": [
                    {"type": "Page", "page_url": "broken"},
                    {"type": "Page", "page_url": "working"},
                    {"type": "Assignment", "content_id": 77},
                ],
            }
        ]
    )
    client = FakeModuleClient(
        modules,
        {
            "broken": RuntimeError("timeout"),
            "working": FakeResponse({"body": "<p>See 1.2</p>"}),
        },
    )
    assert extract_module_section_hints(client, 42, sections) == {77: ("1.2",)}


def test_propose_units_uses_hints_and_expands_single_chapter_labs(tmp_path):
    csv_path = write_chapter_split_csv(tmp_path / "Book_report_1.csv", chapters=(11,))
    snapshot = read_snapshot(csv_path)
    assignments = [
        {"id": 201, "name": "U04A01: zyBook Participation & Challenge", "description": ""},
        {"id": 202, "name": "U04A02: zyLabs", "description": "<p>Complete 5 labs.</p>"},
        {"id": 203, "name": "U04T01: Unit Quiz", "description": ""},
    ]

    # Backward compatible without hints: no explicit chapter/section in assignments -> empty.
    assert propose_units(snapshot, assignments) == ()

    hints = {
        201: ("11.1", "11.2", "11.3", "11.4", "11.5", "11.6"),
        202: ("11.1", "11.2", "11.3", "11.4", "11.5", "11.6"),
        203: ("11.1", "11.2", "11.3", "11.4", "11.5", "11.6"),
    }
    units = propose_units(snapshot, assignments, module_section_hints=hints)
    assert len(units) == 1
    unit = units[0]
    assert unit.name == "U04"
    assert unit.activity_target == 201
    assert unit.lab_target == 202
    assert unit.sections == tuple(f"11.{i}" for i in range(1, 13))
    assert unit.lab_sections == ("11.7", "11.8", "11.9", "11.10", "11.11", "11.12")
    assert unit.distinct_labs_confirmed is False

    confirmed = UnitMapping(
        unit.name,
        unit.sections,
        unit.lab_sections,
        unit.activity_target,
        unit.lab_target,
        distinct_labs_confirmed=True,
    )
    act_score = calculate(snapshot, snapshot.students[0], confirmed, "Activities", Decimal(50))
    lab_score = calculate(snapshot, snapshot.students[0], confirmed, "Lab", Decimal(50))
    assert act_score.points == Decimal("35.00")
    assert lab_score.points == Decimal("40.00")


def test_propose_units_prefers_explicit_mentions_and_guards_multichapter_hints(tmp_path):
    csv_path = write_chapter_split_csv(
        tmp_path / "Book_report_1.csv", chapters=(11, 12), activity_end=2, lab_start=3, lab_end=4
    )
    snapshot = read_snapshot(csv_path)
    assignments = [
        {
            "id": 101,
            "name": "U01A01: zyBook Participation",
            "description": "<p>Sections 11.1-11.2</p>",
        },
        {
            "id": 102,
            "name": "U02A02: zyLabs",
            "description": "",
        },
        {
            "id": 103,
            "name": "U03A02: zyLabs",
            "description": "",
        },
    ]
    hints = {
        101: ("12.1", "12.2"),  # Should be ignored because 101 has explicit description.
        102: ("11.1", "12.1"),  # Spans two chapters -> no single-chapter lab expansion.
        103: ("99.1",),  # Outside snapshot -> ignored.
    }

    units = {u.name: u for u in propose_units(snapshot, assignments, module_section_hints=hints)}
    assert set(units) == {"U01", "U02"}
    assert units["U01"].sections == ("11.1", "11.2")
    assert units["U02"].sections == ("11.1", "12.1")
    assert units["U02"].lab_sections == ()
