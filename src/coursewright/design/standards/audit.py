"""Audit a Canvas course against a rubric and its evidence mapping.

Loads a rubric (default: OSCQR) and its companion ``<rubric_id>_evidence.toml``
mapping, walks the course's Canvas elements through ``ReadOnlyCanvas``, and
evaluates each standard to ``met``, ``not-met``, or ``needs-a-human``.

This module is silent library code: it returns structured ``AuditResult``
records and raises ``EvidenceError`` / ``RubricError`` / ``CourseNotFoundError``
on invalid inputs.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any

from coursewright.design.diagnostics.models import CourseNotFoundError
from coursewright.design.diagnostics.reading import ReadOnlyCanvas
from coursewright.design.standards.rubric import (
    DEFAULT_RUBRIC_ID,
    WORKSPACE_RUBRICS_FOLDER_NAME,
    Rubric,
    load_rubric,
    load_rubric_file,
)

STANDARDS_PACKAGE = "coursewright.design.standards"
SUPPORTED_ELEMENT_TYPES = frozenset(
    {
        "module",
        "page",
        "file",
        "learning_outcome",
        "external_url",
        "discussion",
        "assignment",
        "quiz",
        "rubric",
    }
)
SUPPORTED_CONDITIONS = frozenset({"time_limit_set", "has_points"})


class AuditStatus(StrEnum):
    """Outcome of evaluating one rubric standard against a course."""

    MET = "met"
    NOT_MET = "not-met"
    NEEDS_A_HUMAN = "needs-a-human"


class EvidenceError(ValueError):
    """An evidence-mapping file is missing, unreadable, or malformed."""


@dataclass(frozen=True)
class EvidenceRule:
    """Automated Canvas signal check for a single rubric standard."""

    standard_id: str
    element_types: tuple[str, ...]
    name_match: tuple[str, ...] = ()
    min_count: int = 1
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceMapping:
    """Parsed ``<rubric_id>_evidence.toml`` configuration."""

    rubric_id: str
    origin: str
    manual_review_standards: frozenset[str]
    rules: dict[str, EvidenceRule]


@dataclass(frozen=True)
class CourseElement:
    """Normalized Canvas course element inspected during an audit."""

    element_type: str
    name: str
    points_possible: float | None = None
    time_limit: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class AuditResult:
    """Audit outcome for one rubric standard."""

    standard_id: str
    section: str
    status: AuditStatus
    text: str = ""
    url: str = ""
    evidence: tuple[str, ...] = ()
    notes: str = ""


def _extract_evidence_table(
    document: dict[str, Any],
    origin: str,
    rubric_id: str | None,
) -> dict[str, Any]:
    """Locate the evidence table inside a decoded TOML document."""
    if rubric_id and isinstance(document.get(f"{rubric_id}_evidence"), dict):
        return document[f"{rubric_id}_evidence"]
    if isinstance(document.get("evidence"), dict):
        return document["evidence"]

    candidates = [
        value
        for key, value in document.items()
        if key.endswith("_evidence") and isinstance(value, dict)
    ]
    if len(candidates) == 1:
        return candidates[0]

    raise EvidenceError(f"{origin}: missing evidence mapping table")


def _parse_rule(
    standard_id: str,
    raw_rule: Any,
    origin: str,
) -> EvidenceRule:
    """Validate and construct one ``EvidenceRule`` from TOML data."""
    if not isinstance(raw_rule, dict):
        raise EvidenceError(f"{origin}: rule for standard '{standard_id}' must be a table")

    raw_types = raw_rule.get("element_types")
    if not isinstance(raw_types, list) or not raw_types:
        raise EvidenceError(
            f"{origin}: standard '{standard_id}' must declare a non-empty element_types list"
        )
    element_types = tuple(str(item) for item in raw_types)
    unknown_types = set(element_types) - SUPPORTED_ELEMENT_TYPES
    if unknown_types:
        raise EvidenceError(
            f"{origin}: standard '{standard_id}' references unsupported element type(s): "
            f"{', '.join(sorted(unknown_types))}"
        )

    raw_name_match = raw_rule.get("name_match", [])
    if not isinstance(raw_name_match, list):
        raise EvidenceError(f"{origin}: standard '{standard_id}' name_match must be a list")
    name_match = tuple(str(term).lower() for term in raw_name_match)

    min_count = raw_rule.get("min_count", 1)
    if isinstance(min_count, bool) or not isinstance(min_count, int) or min_count < 1:
        raise EvidenceError(
            f"{origin}: standard '{standard_id}' min_count must be a positive integer"
        )

    raw_conditions = raw_rule.get("conditions", [])
    if not isinstance(raw_conditions, list):
        raise EvidenceError(f"{origin}: standard '{standard_id}' conditions must be a list")
    conditions = tuple(str(cond) for cond in raw_conditions)
    unknown_conditions = set(conditions) - SUPPORTED_CONDITIONS
    if unknown_conditions:
        raise EvidenceError(
            f"{origin}: standard '{standard_id}' references unsupported condition(s): "
            f"{', '.join(sorted(unknown_conditions))}"
        )

    return EvidenceRule(
        standard_id=standard_id,
        element_types=element_types,
        name_match=name_match,
        min_count=min_count,
        conditions=conditions,
    )


def parse_evidence_mapping(
    document: dict[str, Any],
    origin: str,
    rubric_id: str | None = None,
) -> EvidenceMapping:
    """Validate a decoded evidence-mapping TOML document.

    Raises:
        EvidenceError: If the document is missing required tables or contains
            invalid rule definitions.
    """
    table = _extract_evidence_table(document, origin, rubric_id)
    declared_rubric = str(table.get("rubric") or rubric_id or "")
    if not declared_rubric:
        raise EvidenceError(f"{origin}: missing 'rubric' identifier")

    raw_manual = table.get("standards_manual_review", [])
    if not isinstance(raw_manual, list):
        raise EvidenceError(f"{origin}: standards_manual_review must be a list")
    manual_review = frozenset(str(item) for item in raw_manual)

    raw_standards = table.get("standards", {})
    if not isinstance(raw_standards, dict):
        raise EvidenceError(f"{origin}: standards must be a table")

    rules: dict[str, EvidenceRule] = {}
    for std_id, raw_rule in raw_standards.items():
        key = str(std_id)
        rules[key] = _parse_rule(key, raw_rule, origin)

    return EvidenceMapping(
        rubric_id=declared_rubric,
        origin=origin,
        manual_review_standards=manual_review,
        rules=rules,
    )


def load_evidence_file(
    evidence_path: Path,
    rubric_id: str | None = None,
) -> EvidenceMapping:
    """Load an evidence-mapping TOML file from disk.

    Raises:
        EvidenceError: If the file cannot be read or is malformed.
    """
    try:
        with evidence_path.open("rb") as evidence_file:
            document = tomllib.load(evidence_file)
    except OSError as err:
        raise EvidenceError(f"{evidence_path}: {err.strerror or err}") from err
    except tomllib.TOMLDecodeError as err:
        raise EvidenceError(f"{evidence_path}: not valid TOML: {err}") from err
    return parse_evidence_mapping(document, origin=str(evidence_path), rubric_id=rubric_id)


def load_evidence_mapping(
    rubric_id: str = DEFAULT_RUBRIC_ID,
    workspace_directory: Path | None = None,
    evidence_path: Path | None = None,
) -> EvidenceMapping:
    """Load the evidence mapping for *rubric_id*.

    Resolution order:
    1. Explicit *evidence_path*, if provided.
    2. Private workspace ``<rubric_id>_evidence.toml`` (in ``rubrics/`` or root).
    3. Bundled ``<rubric_id>_evidence.toml`` inside ``coursewright.design.standards``.

    Raises:
        EvidenceError: If no matching evidence mapping exists or it is malformed.
    """
    if evidence_path is not None:
        return load_evidence_file(evidence_path, rubric_id=rubric_id)

    filename = f"{rubric_id}_evidence.toml"
    if workspace_directory is not None:
        for candidate in (
            workspace_directory / WORKSPACE_RUBRICS_FOLDER_NAME / filename,
            workspace_directory / filename,
        ):
            if candidate.is_file():
                return load_evidence_file(candidate, rubric_id=rubric_id)

    bundled_resource = resources.files(STANDARDS_PACKAGE) / filename
    if bundled_resource.is_file():
        try:
            document = tomllib.loads(bundled_resource.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as err:
            raise EvidenceError(f"bundled:{filename}: not valid TOML: {err}") from err
        return parse_evidence_mapping(
            document,
            origin=f"bundled:{filename}",
            rubric_id=rubric_id,
        )

    raise EvidenceError(f"no evidence mapping found for rubric '{rubric_id}'")


def _condition_holds(element: CourseElement, condition: str) -> bool:
    """Evaluate a single named condition against a normalized element."""
    if condition == "time_limit_set":
        return element.time_limit is not None and element.time_limit > 0
    if condition == "has_points":
        return element.points_possible is not None and element.points_possible > 0
    raise EvidenceError(f"unsupported condition '{condition}'")


def _element_matches_rule(element: CourseElement, rule: EvidenceRule) -> bool:
    """Return True when *element* satisfies *rule*'s name_match and conditions."""
    if rule.name_match:
        lowered_name = element.name.lower()
        if not any(term in lowered_name for term in rule.name_match):
            return False
    return all(_condition_holds(element, condition) for condition in rule.conditions)


def _outcome_name(raw_outcome: dict[str, Any]) -> str:
    """Extract a readable name from a Canvas outcome link or outcome record."""
    nested = raw_outcome.get("outcome")
    if isinstance(nested, dict):
        title = nested.get("title") or nested.get("display_name")
        if title:
            return str(title)
    title = raw_outcome.get("title") or raw_outcome.get("display_name")
    if title:
        return str(title)
    return f"Outcome {raw_outcome.get('id', '')}".strip()


def collect_course_elements(
    canvas: ReadOnlyCanvas,
    course_id: int,
    element_types: Iterable[str] | None = None,
) -> dict[str, tuple[CourseElement, ...]]:
    """Collect normalized Canvas elements for *course_id* via ``ReadOnlyCanvas``.

    Only the requested *element_types* are fetched (default: all supported types).
    """
    requested = (
        frozenset(element_types) if element_types is not None else SUPPORTED_ELEMENT_TYPES
    )
    collected: dict[str, list[CourseElement]] = {kind: [] for kind in requested}

    need_modules = bool(requested & {"module", "file", "external_url"})
    raw_modules = canvas.get_modules(course_id) if need_modules else []

    if "module" in requested:
        for mod in raw_modules:
            collected["module"].append(
                CourseElement(element_type="module", name=str(mod.get("name", "")))
            )

    if requested & {"file", "external_url"}:
        for mod in raw_modules:
            for item in canvas.get_module_items(course_id, mod["id"]):
                item_type = str(item.get("type", "")).lower()
                if item_type == "file" and "file" in requested:
                    collected["file"].append(
                        CourseElement(element_type="file", name=str(item.get("title", "")))
                    )
                elif item_type == "externalurl" and "external_url" in requested:
                    collected["external_url"].append(
                        CourseElement(
                            element_type="external_url",
                            name=str(item.get("title", "")),
                            url=item.get("external_url"),
                        )
                    )

    if "page" in requested:
        for page in canvas.get_pages(course_id):
            name = str(page.get("title") or page.get("url") or "")
            collected["page"].append(
                CourseElement(element_type="page", name=name, url=page.get("url"))
            )

    if "discussion" in requested:
        for topic in canvas.get_discussion_topics(course_id):
            collected["discussion"].append(
                CourseElement(element_type="discussion", name=str(topic.get("title", "")))
            )

    if "assignment" in requested:
        for assignment in canvas.get_assignments(course_id):
            collected["assignment"].append(
                CourseElement(
                    element_type="assignment",
                    name=assignment.name,
                    points_possible=assignment.points_possible,
                    url=assignment.html_url,
                )
            )

    if "quiz" in requested:
        for quiz in canvas.get_quizzes(course_id):
            collected["quiz"].append(
                CourseElement(
                    element_type="quiz",
                    name=str(quiz.get("title", "")),
                    points_possible=quiz.get("points_possible"),
                    time_limit=quiz.get("time_limit"),
                )
            )

    if "learning_outcome" in requested:
        outcome_url = f"{canvas.base_url}/api/v1/courses/{course_id}/outcome_links"
        for raw_outcome in canvas._paginated_get(outcome_url, {"per_page": 100}):
            collected["learning_outcome"].append(
                CourseElement(element_type="learning_outcome", name=_outcome_name(raw_outcome))
            )

    if "rubric" in requested:
        rubric_url = f"{canvas.base_url}/api/v1/courses/{course_id}/rubrics"
        for raw_rubric in canvas._paginated_get(rubric_url, {"per_page": 100}):
            collected["rubric"].append(
                CourseElement(element_type="rubric", name=str(raw_rubric.get("title", "")))
            )

    return {kind: tuple(items) for kind, items in collected.items()}


def evaluate_rubric(
    rubric: Rubric,
    evidence: EvidenceMapping,
    elements_by_type: Mapping[str, Sequence[CourseElement]],
) -> list[AuditResult]:
    """Evaluate every standard in *rubric* against pre-collected Canvas elements."""
    results: list[AuditResult] = []
    for standard in rubric.standards:
        rule = evidence.rules.get(standard.id)
        if rule is None or standard.id in evidence.manual_review_standards:
            results.append(
                AuditResult(
                    standard_id=standard.id,
                    section=standard.section,
                    status=AuditStatus.NEEDS_A_HUMAN,
                    text=standard.text,
                    url=standard.url,
                    notes="Requires manual review.",
                )
            )
            continue

        matches: list[CourseElement] = []
        for element_type in rule.element_types:
            for element in elements_by_type.get(element_type, ()):
                if _element_matches_rule(element, rule):
                    matches.append(element)

        if len(matches) >= rule.min_count:
            matched_labels = tuple(f"{m.element_type}: {m.name}" for m in matches)
            results.append(
                AuditResult(
                    standard_id=standard.id,
                    section=standard.section,
                    status=AuditStatus.MET,
                    text=standard.text,
                    url=standard.url,
                    evidence=matched_labels,
                    notes=f"Matched {len(matches)} element(s) (minimum {rule.min_count}).",
                )
            )
        else:
            results.append(
                AuditResult(
                    standard_id=standard.id,
                    section=standard.section,
                    status=AuditStatus.NOT_MET,
                    text=standard.text,
                    url=standard.url,
                    notes=(
                        f"Found {len(matches)} matching element(s); "
                        f"expected at least {rule.min_count}."
                    ),
                )
            )
    return results


def audit_course(
    canvas: ReadOnlyCanvas,
    course: int | dict[str, Any],
    rubric_path: Path | None = None,
    *,
    rubric_id: str = DEFAULT_RUBRIC_ID,
    evidence_path: Path | None = None,
    workspace_directory: Path | None = None,
) -> list[AuditResult]:
    """Audit a Canvas course against a rubric and its evidence mapping.

    Args:
        canvas: A read-only Canvas client.
        course: A Canvas course id or course record dict containing ``"id"``.
        rubric_path: Optional explicit path to a rubric TOML file.
        rubric_id: Rubric id to load when *rubric_path* is omitted (default: ``"oscqr"``).
        evidence_path: Optional explicit path to an evidence-mapping TOML file.
        workspace_directory: Optional private workspace directory for overrides.

    Returns:
        One ``AuditResult`` per standard in the loaded rubric, in rubric order.

    Raises:
        CourseNotFoundError: When *course* is an id that Canvas does not return.
        RubricError: When the rubric cannot be loaded or is malformed.
        EvidenceError: When the evidence mapping cannot be loaded or is malformed.
    """
    if isinstance(course, int):
        course_record = canvas.get_course(course)
        if not course_record:
            raise CourseNotFoundError(f"Canvas course {course} was not found.")
        course_id = course
    else:
        course_id = int(course["id"])

    if rubric_path is not None:
        rubric = load_rubric_file(rubric_path)
        resolved_evidence_path = evidence_path
        if resolved_evidence_path is None:
            sibling = rubric_path.with_name(f"{rubric.id}_evidence.toml")
            if sibling.is_file():
                resolved_evidence_path = sibling
        evidence = load_evidence_mapping(
            rubric.id,
            workspace_directory=workspace_directory,
            evidence_path=resolved_evidence_path,
        )
    else:
        rubric = load_rubric(rubric_id, workspace_directory=workspace_directory)
        evidence = load_evidence_mapping(
            rubric.id,
            workspace_directory=workspace_directory,
            evidence_path=evidence_path,
        )

    needed_types = {
        element_type
        for rule in evidence.rules.values()
        for element_type in rule.element_types
    }
    elements_by_type = collect_course_elements(canvas, course_id, element_types=needed_types)
    return evaluate_rubric(rubric, evidence, elements_by_type)
