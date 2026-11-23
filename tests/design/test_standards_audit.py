"""Unit tests for the rubric-driven course standards audit."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import requests

from coursewright.core.canvas.auth_strategy import TokenAuth
from coursewright.core.canvas.client import CanvasClient
from coursewright.design.diagnostics.models import CourseNotFoundError
from coursewright.design.standards import (
    AuditStatus,
    CourseElement,
    EvidenceError,
    audit_course,
    collect_course_elements,
    evaluate_rubric,
    load_evidence_file,
    load_evidence_mapping,
    load_rubric,
)

BASE_URL = "https://canvas.example.edu"
COURSE_ID = 101

# SHA-256 digests of prohibited copyrighted wording.  The word counts limit
# matching to exact, long sequences without retaining that wording here.
QM_PHRASE_HASHES = {
    4: frozenset({"517714d6def13cb603d32169d9d79802aa4df2d560a07af8a52573ed46886efe"}),
    14: frozenset({"367a97b0f9ac52592a9d393142e1e8f3a9ff0c27dce995665c42a3b601a50ebc"}),
    15: frozenset({"a8a238e5d12895b81fbcbb58f217081f9b4c23a8920a7596639a06fdf7fa7acc"}),
}


def assert_no_qm_wording(
    text: str, phrase_hashes: dict[int, frozenset[str]] = QM_PHRASE_HASHES
) -> None:
    """Raise if a prohibited phrase digest matches a word n-gram in ``text``."""
    words = re.findall(r"\w+", text.lower())
    for word_count, forbidden_hashes in phrase_hashes.items():
        for start_index in range(len(words) - word_count + 1):
            word_ngram = " ".join(words[start_index : start_index + word_count])
            word_ngram_hash = hashlib.sha256(word_ngram.encode()).hexdigest()
            if word_ngram_hash in forbidden_hashes:
                raise AssertionError("contains prohibited copyrighted wording")


@dataclass
class RecordedCanvas:
    """Routes ``requests.Session.request`` to synthetic payloads."""

    routes: dict[str, Any] = field(default_factory=dict)
    requests_seen: list[tuple[str, str]] = field(default_factory=list)

    def respond(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requests_seen.append((method, url))
        path = urlparse(url).path
        payload = self.routes.get(path)
        response = requests.Response()
        response.url = url
        if payload is None:
            response.status_code = 404
            response._content = b"{}"
        else:
            response.status_code = 200
            response._content = json.dumps(payload).encode()
        return response

    @property
    def methods_used(self) -> set[str]:
        return {method for method, _ in self.requests_seen}


def _make_canvas_client(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]
) -> tuple[CanvasClient, RecordedCanvas]:
    recorded = RecordedCanvas(routes=routes)
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda self, method, url, **kw: recorded.respond(method, url, **kw),
    )
    client = CanvasClient(BASE_URL, TokenAuth("test-token-for-unit-tests"))
    return client, recorded


def _full_oscqr_routes() -> dict[str, Any]:
    api = "/api/v1"
    course = f"{api}/courses/{COURSE_ID}"
    return {
        course: {"id": COURSE_ID, "name": "Complete Synthetic Course"},
        f"{course}/modules": [
            {"id": 1, "name": "Welcome & Getting Started"},
            {"id": 2, "name": "Unit 1: Foundations"},
        ],
        f"{course}/modules/1/items": [
            {"id": 101, "title": "Course Syllabus.pdf", "type": "File"},
            {"id": 102, "title": "OpenStax Textbook", "type": "ExternalUrl", "external_url": "https://openstax.org/example"},
        ],
        f"{course}/modules/2/items": [],
        f"{course}/pages": [
            {"title": "Course Syllabus", "url": "course-syllabus"},
        ],
        f"{course}/discussion_topics": [
            {"id": 301, "title": "Class Introductions"},
            {"id": 302, "title": "Group Peer Review"},
        ],
        f"{course}/assignments": [
            {
                "id": 401,
                "name": "Collaborative Essay",
                "points_possible": 50.0,
                "submission_types": ["online_upload"],
            }
        ],
        f"{course}/quizzes": [
            {"id": 501, "title": "Unit 1 Self-Check", "time_limit": 20, "points_possible": 10.0},
        ],
        f"{course}/outcome_links": [
            {"id": 601, "outcome": {"id": 701, "title": "Analyze primary sources"}},
        ],
        f"{course}/rubrics": [
            {"id": 801, "title": "Essay Grading Rubric"},
        ],
    }


def _empty_oscqr_routes() -> dict[str, Any]:
    api = "/api/v1"
    course = f"{api}/courses/{COURSE_ID}"
    return {
        course: {"id": COURSE_ID, "name": "Empty Synthetic Course"},
        f"{course}/modules": [],
        f"{course}/pages": [],
        f"{course}/discussion_topics": [],
        f"{course}/assignments": [],
        f"{course}/quizzes": [],
        f"{course}/outcome_links": [],
        f"{course}/rubrics": [],
    }


def test_bundled_oscqr_evidence_covers_all_fifty_standards() -> None:
    rubric = load_rubric("oscqr")
    evidence = load_evidence_mapping("oscqr")

    assert evidence.rubric_id == "oscqr"
    rubric_ids = {standard.id for standard in rubric.standards}
    automated_ids = set(evidence.rules)
    manual_ids = set(evidence.manual_review_standards)

    assert automated_ids.isdisjoint(manual_ids)
    assert automated_ids | manual_ids == rubric_ids
    assert len(automated_ids) == 16
    assert len(manual_ids) == 34


def test_audit_course_all_automated_met_and_only_gets_issued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, recorded = _make_canvas_client(monkeypatch, _full_oscqr_routes())
    results = audit_course(client, COURSE_ID)

    assert recorded.methods_used == {"GET"}
    assert len(results) == 50

    by_id = {result.standard_id: result for result in results}
    automated_ids = set(load_evidence_mapping("oscqr").rules)
    for std_id in automated_ids:
        assert by_id[std_id].status is AuditStatus.MET, f"expected {std_id} to be MET"
        assert by_id[std_id].evidence

    for std_id, result in by_id.items():
        if std_id not in automated_ids:
            assert result.status is AuditStatus.NEEDS_A_HUMAN


def test_audit_course_empty_course_marks_automated_not_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _make_canvas_client(monkeypatch, _empty_oscqr_routes())
    results = audit_course(client, {"id": COURSE_ID, "name": "Empty"})

    assert len(results) == 50
    by_id = {result.standard_id: result for result in results}
    automated_ids = set(load_evidence_mapping("oscqr").rules)
    for std_id in automated_ids:
        assert by_id[std_id].status is AuditStatus.NOT_MET
        assert by_id[std_id].evidence == ()


def test_audit_course_missing_course_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _make_canvas_client(monkeypatch, {})
    with pytest.raises(CourseNotFoundError, match=str(COURSE_ID)):
        audit_course(client, COURSE_ID)


def test_conditions_time_limit_and_has_points() -> None:
    rubric = load_rubric("oscqr")
    evidence = load_evidence_mapping("oscqr")

    # Quiz with no time limit and 0-point assignment -> 48 and 49 NOT_MET
    elements_failing = {
        "quiz": [
            CourseElement(
                element_type="quiz", name="Practice", time_limit=0, points_possible=0
            )
        ],
        "assignment": [
            CourseElement(element_type="assignment", name="Survey", points_possible=0)
        ],
    }
    failing_by_id = {r.standard_id: r for r in evaluate_rubric(rubric, evidence, elements_failing)}
    assert failing_by_id["47"].status is AuditStatus.MET
    assert failing_by_id["48"].status is AuditStatus.NOT_MET
    assert failing_by_id["49"].status is AuditStatus.NOT_MET

    # Quiz with time limit and points -> 48 and 49 MET
    elements_passing = {
        "quiz": [
            CourseElement(
                element_type="quiz", name="Midterm", time_limit=45, points_possible=25.0
            )
        ],
    }
    passing_by_id = {r.standard_id: r for r in evaluate_rubric(rubric, evidence, elements_passing)}
    assert passing_by_id["48"].status is AuditStatus.MET
    assert passing_by_id["49"].status is AuditStatus.MET


def test_custom_rubric_path_loads_sibling_evidence_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rubric_file = tmp_path / "custom.toml"
    rubric_file.write_text(
        """
[rubric]
id = "custom"
name = "Custom Rubric"
source_url = "https://example.org/custom"
text_included = true

[[section]]
id = "s1"
name = "Section 1"

[[standard]]
id = "C1"
section = "s1"
text = "Course has at least two unit modules."

[[standard]]
id = "C2"
section = "s1"
text = "Instructor tone is welcoming."
""",
        encoding="utf-8",
    )
    evidence_file = tmp_path / "custom_evidence.toml"
    evidence_file.write_text(
        """
[custom_evidence]
rubric = "custom"
standards_manual_review = ["C2"]

[custom_evidence.standards."C1"]
element_types = ["module"]
name_match = ["unit"]
min_count = 2
""",
        encoding="utf-8",
    )

    routes = {
        f"/api/v1/courses/{COURSE_ID}/modules": [
            {"id": 1, "name": "Unit 1"},
            {"id": 2, "name": "Unit 2"},
        ],
    }
    client, _ = _make_canvas_client(monkeypatch, routes)
    results = audit_course(client, {"id": COURSE_ID}, rubric_path=rubric_file)

    assert [r.standard_id for r in results] == ["C1", "C2"]
    assert results[0].status is AuditStatus.MET
    assert results[1].status is AuditStatus.NEEDS_A_HUMAN


def test_collect_course_elements_normalizes_all_types(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _make_canvas_client(monkeypatch, _full_oscqr_routes())
    elements = collect_course_elements(client, COURSE_ID)

    assert len(elements["module"]) == 2
    assert len(elements["file"]) == 1
    assert elements["file"][0].name == "Course Syllabus.pdf"
    assert len(elements["external_url"]) == 1
    assert elements["external_url"][0].url == "https://openstax.org/example"
    assert len(elements["learning_outcome"]) == 1
    assert elements["learning_outcome"][0].name == "Analyze primary sources"
    assert len(elements["rubric"]) == 1
    assert elements["rubric"][0].name == "Essay Grading Rubric"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("not = [valid", "not valid TOML"),
        ('[other]\nkey = "val"\n', "missing evidence mapping table"),
        (
            '[evidence]\nrubric = "x"\n[evidence.standards."1"]\nelement_types = []\n',
            "non-empty element_types",
        ),
        (
            '[evidence]\nrubric = "x"\n[evidence.standards."1"]\nelement_types = ["bogus"]\n',
            "unsupported element type",
        ),
        (
            '[evidence]\nrubric = "x"\n[evidence.standards."1"]\n'
            'element_types = ["quiz"]\nconditions = ["nope"]\n',
            "unsupported condition",
        ),
        (
            '[evidence]\nrubric = "x"\n[evidence.standards."1"]\n'
            'element_types = ["quiz"]\nmin_count = 0\n',
            "positive integer",
        ),
    ],
)
def test_malformed_evidence_file_raises_evidence_error(
    tmp_path: Path, body: str, expected: str
) -> None:
    path = tmp_path / "bad_evidence.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(EvidenceError, match=expected):
        load_evidence_file(path)


def test_audit_module_has_no_ui_imports() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    audit_path = repo_root / "src" / "coursewright" / "design" / "standards" / "audit.py"
    tree = ast.parse(audit_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module.startswith("coursewright.ui"):
                imported.add("coursewright.ui")
    assert imported.isdisjoint({"rich", "typer", "textual", "coursewright.ui"})


def test_prohibited_wording_matcher_rejects_a_matching_synthetic_ngram() -> None:
    synthetic_phrase = "synthetic text used only to test this matcher"
    normalised_phrase = " ".join(re.findall(r"\w+", synthetic_phrase.lower()))
    synthetic_phrase_hash = hashlib.sha256(normalised_phrase.encode()).hexdigest()

    with pytest.raises(AssertionError, match="prohibited copyrighted wording"):
        assert_no_qm_wording(synthetic_phrase, {8: frozenset({synthetic_phrase_hash})})


def test_standards_directory_contains_no_quality_matters_wording() -> None:
    """The bundled standards implementation must not retain licensed text."""
    repo_root = Path(__file__).resolve().parents[2]
    standards_directory = repo_root / "src" / "coursewright" / "design" / "standards"

    for target in standards_directory.rglob("*"):
        if target.is_file() and target.suffix in {".py", ".toml"}:
            assert_no_qm_wording(target.read_text(encoding="utf-8"))
