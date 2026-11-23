"""Load the prompt files that ship inside this package.

The prompts are Markdown files in the ``prompts`` folder beside this
module, packaged as data (``pyproject.toml``).  discussorator read
them with ``Path(__file__).parent / "prompts" / name`` from two
different modules; here there is one loader, and it goes through
``importlib.resources`` so the files load from an installed package
(a wheel, a zip) as well as from a checkout.

    system_prompt = load_prompt("FEEDBACK_SYSTEM.md")
    user_prompt = load_prompt("FEEDBACK_USER.md").format(...)
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable

PROMPT_FILE_SUFFIX = ".md"


def _prompt_directory() -> Traversable:
    return files("coursewright.core.llm").joinpath("prompts")


def list_prompt_names() -> list[str]:
    """Return the file names of every bundled prompt, sorted."""
    return sorted(
        entry.name
        for entry in _prompt_directory().iterdir()
        if entry.is_file() and entry.name.endswith(PROMPT_FILE_SUFFIX)
    )


def load_prompt(prompt_file_name: str) -> str:
    """Return the text of one bundled prompt, such as ``FEEDBACK_SYSTEM.md``.

    Raises `FileNotFoundError` when no bundled prompt has that name.
    Only a bare file name is accepted: the name is never treated as a
    path, so it cannot reach outside the prompts folder.
    """
    if prompt_file_name not in list_prompt_names():
        raise FileNotFoundError(f"No bundled prompt named {prompt_file_name!r}")
    return _prompt_directory().joinpath(prompt_file_name).read_text(encoding="utf-8")
