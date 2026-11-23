"""Load and fill the discussion templates that ship inside this package.

The files in ``templates/`` hold structure and ``{{placeholder}}`` markers
only: no instructor voice, no college policy.  An instructor's own wording
is passed in as values (or kept in the private workspace), never committed.

Loading goes through ``importlib.resources`` so the files are found in an
installed package as well as in a checkout, the same way
``coursewright.core.llm.prompt_library`` loads its prompts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from importlib.resources import files
from importlib.resources.abc import Traversable

TEMPLATE_FILE_SUFFIX = ".md"

# ``{{name}}``: lower-case words and underscores, nothing else.
PLACEHOLDER_PATTERN = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")


def _template_directory() -> Traversable:
    return files("coursewright.design.discussions").joinpath("templates")


def list_template_names() -> list[str]:
    """Return the file names of every bundled template, sorted."""
    return sorted(
        entry.name
        for entry in _template_directory().iterdir()
        if entry.is_file() and entry.name.endswith(TEMPLATE_FILE_SUFFIX)
    )


def load_template(template_file_name: str) -> str:
    """Return the raw text of one bundled template.

    Raises `FileNotFoundError` for an unknown name.  Only a bare file name
    is accepted, so a name can never reach outside the templates folder.
    """
    if template_file_name not in list_template_names():
        raise FileNotFoundError(f"No bundled discussion template named {template_file_name!r}")
    return _template_directory().joinpath(template_file_name).read_text(encoding="utf-8")


def find_placeholders(template_text: str) -> list[str]:
    """Return the placeholder names in *template_text*, first appearance first."""
    names: list[str] = []
    for name in PLACEHOLDER_PATTERN.findall(template_text):
        if name not in names:
            names.append(name)
    return names


def fill_template(template_text: str, values: Mapping[str, str]) -> str:
    """Replace every ``{{name}}`` with its value.

    Raises:
        KeyError: a placeholder has no value.  A half-filled template must
            never reach Canvas, so a missing value is loud, not blank.
    """
    missing_names = [name for name in find_placeholders(template_text) if name not in values]
    if missing_names:
        raise KeyError(f"No value for template placeholder(s): {', '.join(missing_names)}")
    return PLACEHOLDER_PATTERN.sub(lambda match: str(values[match.group(1)]), template_text)
