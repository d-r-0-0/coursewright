"""Keep public command examples aligned with the Typer command tree.

The manifest is deliberately small and explicit: it covers every command path
and option printed in README.md and the Coursewright guides.  Invoking help is
safe because Typer exits before command callbacks can contact Canvas.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from coursewright.ui.cli.main import app

DOCUMENTED_COMMANDS = [
    ([], {"--config", "--version"}),
    (["auth", "login"], set()),
    (["auth", "status"], set()),
    (["auth", "test"], set()),
    (["auth", "logout"], set()),
    (["auth", "login-browser"], {"--url"}),
    (["auth", "migrate-to-keyring"], set()),
    (["config"], set()),
    (["config", "show"], set()),
    (["config", "validate"], set()),
    (["config", "set"], set()),
    (["config", "reset"], {"--yes"}),
    (["config", "import"], set()),
    (["config", "export"], set()),
    (["tui"], set()),
    (["menu"], set()),
    (["ta"], set()),
    (["ta", "start"], set()),
    (["ta", "menu"], set()),
    (["ta", "setup"], set()),
    (["ta", "courses", "list"], set()),
    (["ta", "courses", "info"], set()),
    (["ta", "discussions"], set()),
    (["ta", "discussions", "list"], set()),
    (["ta", "discussions", "summary"], {"--course"}),
    (["ta", "discussions", "posts"], {"--course"}),
    (["ta", "discussions", "grade"], {"--course"}),
    (["ta", "discussions", "submit-grades"], {"--course", "--execute"}),
    (["ta", "discussions", "export-course"], {"--output"}),
    (["ta", "analyze-course"], set()),
    (["ta", "attendance-warning"], {"--course-id", "--inactive-days", "--execute"}),
    (["ta", "gradeliner"], {"--netacad", "--course", "--execute"}),
    (["ta", "netacad-import"], {"--netacad", "--course", "--execute"}),
    (["ta", "zybooks-import"], {"--course", "--labs-required", "--execute"}),
    (["ta", "honor-credit"], {"--course", "--match", "--points", "--execute"}),
    (["ta", "llm", "list-models"], set()),
    (["ta", "llm", "set-default"], set()),
    (["ta", "llm", "usage"], set()),
    (["design"], set()),
    (["design", "rubrics"], set()),
    (["design", "rubric"], set()),
    (["design", "audit"], set()),
    (["design", "diagnose", "run"], set()),
    (["design", "organize", "unit-order"], {"--execute"}),
    (["design", "discussions", "create"], {"--last-unit", "--execute"}),
    (["design", "dates", "apply"], {"--execute"}),
    (["design", "publish", "content"], {"--first", "--execute"}),
    (["design", "convert", "names"], set()),
    (["design", "convert", "captions"], {"--viewer-url-prefix"}),
]


@pytest.mark.parametrize(("arguments", "documented_options"), DOCUMENTED_COMMANDS)
def test_documented_command_help_parses(
    arguments: list[str], documented_options: set[str]
) -> None:
    """Every public command example resolves and exposes its documented flags."""
    result = CliRunner().invoke(app, [*arguments, "--help"])

    command = " ".join(["coursewright", *arguments])
    assert result.exit_code == 0, f"{command} --help failed:\n{result.output}"
    for option in documented_options:
        assert option in result.output, f"{command} no longer documents {option}"
