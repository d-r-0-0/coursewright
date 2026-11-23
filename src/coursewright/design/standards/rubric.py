"""Load course-quality rubrics from TOML files.

A rubric is data, not code.  Coursewright bundles rubrics it is
licensed to redistribute (OSCQR, CC BY 4.0) and holds others by
reference only (Quality Matters: numbers and a link, no text).  An
instructor can add or override rubrics by placing ``<id>.toml`` files
in the ``rubrics/`` folder of their private workspace.

File format::

    [rubric]
    id = "oscqr"
    name = "..."
    publisher = "..."
    license = "CC-BY-4.0"
    source_url = "https://..."
    text_included = true        # false for reference-only rubrics

    [[section]]
    id = "interaction"
    name = "Interaction"

    [[standard]]
    id = "39"
    section = "interaction"
    text = "..."                # required when text_included is true
    url = "https://..."         # optional; defaults to source_url
    points = 3                  # optional
    tags = ["rsi"]              # optional
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

BUNDLED_RUBRICS_PACKAGE = "coursewright.design.standards.rubrics"
WORKSPACE_RUBRICS_FOLDER_NAME = "rubrics"
DEFAULT_RUBRIC_ID = "oscqr"


class RubricError(ValueError):
    """A rubric file is missing, unreadable or malformed."""


@dataclass(frozen=True)
class RubricSection:
    """A named group of standards within a rubric."""

    id: str
    name: str


@dataclass(frozen=True)
class RubricStandard:
    """One reviewable standard.

    ``text`` is empty for a reference-only rubric; ``url`` always
    points somewhere a person can read the standard.
    """

    id: str
    section: str
    url: str
    text: str = ""
    points: int | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rubric:
    """A complete rubric and where it came from."""

    id: str
    name: str
    publisher: str
    license: str
    source_url: str
    text_included: bool
    origin: str
    sections: tuple[RubricSection, ...] = field(default=())
    standards: tuple[RubricStandard, ...] = field(default=())

    def standards_in_section(self, section_id: str) -> tuple[RubricStandard, ...]:
        """Return the standards belonging to one section, in file order."""
        return tuple(s for s in self.standards if s.section == section_id)

    def find_standard(self, standard_id: str) -> RubricStandard:
        """Return one standard by id.

        Raises:
            KeyError: If the rubric has no such standard.
        """
        for standard in self.standards:
            if standard.id == standard_id:
                return standard
        raise KeyError(f"rubric '{self.id}' has no standard '{standard_id}'")


def parse_rubric(document: dict, origin: str) -> Rubric:
    """Validate a decoded TOML document and build a ``Rubric``.

    Args:
        document: The result of ``tomllib.load``.
        origin: Where the document came from, for error messages.

    Raises:
        RubricError: If a required key is missing or the standards are
            inconsistent with the sections or with ``text_included``.
    """
    header = document.get("rubric")
    if not isinstance(header, dict):
        raise RubricError(f"{origin}: missing [rubric] table")

    try:
        rubric_id = str(header["id"])
        name = str(header["name"])
        source_url = str(header["source_url"])
        text_included = bool(header["text_included"])
    except KeyError as err:
        raise RubricError(f"{origin}: [rubric] is missing {err}") from None

    try:
        sections = tuple(
            RubricSection(id=str(entry["id"]), name=str(entry["name"]))
            for entry in document.get("section", [])
        )
    except KeyError as err:
        raise RubricError(f"{origin}: a [[section]] is missing {err}") from None
    known_section_ids = {section.id for section in sections}

    standards: list[RubricStandard] = []
    seen_standard_ids: set[str] = set()
    for entry in document.get("standard", []):
        try:
            standard_id = str(entry["id"])
            section_id = str(entry["section"])
        except KeyError as err:
            raise RubricError(f"{origin}: a [[standard]] is missing {err}") from None

        if standard_id in seen_standard_ids:
            raise RubricError(f"{origin}: duplicate standard id '{standard_id}'")
        seen_standard_ids.add(standard_id)

        if section_id not in known_section_ids:
            raise RubricError(
                f"{origin}: standard '{standard_id}' names unknown section '{section_id}'"
            )

        text = str(entry.get("text", "")).strip()
        if text_included and not text:
            raise RubricError(f"{origin}: standard '{standard_id}' has no text")
        if not text_included and text:
            raise RubricError(
                f"{origin}: standard '{standard_id}' has text but the rubric "
                f"declares text_included = false"
            )

        standards.append(
            RubricStandard(
                id=standard_id,
                section=section_id,
                url=str(entry.get("url", source_url)),
                text=text,
                points=entry.get("points"),
                tags=tuple(str(tag) for tag in entry.get("tags", ())),
            )
        )

    if not standards:
        raise RubricError(f"{origin}: rubric declares no standards")

    return Rubric(
        id=rubric_id,
        name=name,
        publisher=str(header.get("publisher", "")),
        license=str(header.get("license", "")),
        source_url=source_url,
        text_included=text_included,
        origin=origin,
        sections=sections,
        standards=tuple(standards),
    )


def load_rubric_file(rubric_path: Path) -> Rubric:
    """Load one rubric from a TOML file on disk.

    Raises:
        RubricError: If the file cannot be read or is not a valid rubric.
    """
    try:
        with rubric_path.open("rb") as rubric_file:
            document = tomllib.load(rubric_file)
    except OSError as err:
        raise RubricError(f"{rubric_path}: {err.strerror or err}") from err
    except tomllib.TOMLDecodeError as err:
        raise RubricError(f"{rubric_path}: not valid TOML: {err}") from err
    return parse_rubric(document, origin=str(rubric_path))


def list_bundled_rubric_ids() -> list[str]:
    """Return the ids of the rubrics shipped inside the package."""
    bundled_folder = resources.files(BUNDLED_RUBRICS_PACKAGE)
    return sorted(
        entry.name.removesuffix(".toml")
        for entry in bundled_folder.iterdir()
        if entry.name.endswith(".toml")
    )


def list_workspace_rubric_ids(workspace_directory: Path | None) -> list[str]:
    """Return the ids of rubrics found in a private workspace, if any."""
    if workspace_directory is None:
        return []
    rubrics_folder = workspace_directory / WORKSPACE_RUBRICS_FOLDER_NAME
    if not rubrics_folder.is_dir():
        return []
    return sorted(path.stem for path in rubrics_folder.glob("*.toml"))


def list_rubric_ids(workspace_directory: Path | None = None) -> list[str]:
    """Return every rubric id available, workspace and bundled combined."""
    combined = set(list_bundled_rubric_ids())
    combined.update(list_workspace_rubric_ids(workspace_directory))
    return sorted(combined)


def load_rubric(
    rubric_id: str = DEFAULT_RUBRIC_ID,
    workspace_directory: Path | None = None,
) -> Rubric:
    """Load a rubric by id, preferring the private workspace copy.

    A workspace file wins over a bundled one with the same id.  That is
    how a subscribing institution supplies full Quality Matters text
    without it ever entering this repository.

    Args:
        rubric_id: The file stem, for example ``"oscqr"``.
        workspace_directory: The instructor's private workspace, or None.

    Raises:
        RubricError: If no rubric has that id, or its file is malformed.
    """
    if rubric_id in list_workspace_rubric_ids(workspace_directory):
        assert workspace_directory is not None
        return load_rubric_file(
            workspace_directory / WORKSPACE_RUBRICS_FOLDER_NAME / f"{rubric_id}.toml"
        )

    if rubric_id in list_bundled_rubric_ids():
        bundled_file = resources.files(BUNDLED_RUBRICS_PACKAGE) / f"{rubric_id}.toml"
        try:
            document = tomllib.loads(bundled_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as err:
            raise RubricError(f"bundled:{rubric_id}: not valid TOML: {err}") from err
        return parse_rubric(document, origin=f"bundled:{rubric_id}")

    available = ", ".join(list_rubric_ids(workspace_directory))
    raise RubricError(f"no rubric named '{rubric_id}'. Available: {available}")
