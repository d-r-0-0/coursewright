"""Submit a Coursewright review or block reason without an overlong command.

Usage: python cantrips/kanban_handoff.py CARD review SUMMARY_FILE
       python cantrips/kanban_handoff.py CARD block REASON_FILE

Reading the text from a file preserves literal newlines and avoids shell
interpolation. This command changes the board only when explicitly run.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("card")
    parser.add_argument("action", choices=("review", "block"))
    parser.add_argument("message_file", type=Path)
    arguments = parser.parse_args()
    message = arguments.message_file.read_text(encoding="utf-8").strip()
    if not message:
        parser.error("The handoff message must not be empty")
    command = ["hermes", "kanban", "--board", "coursewright"]
    if arguments.action == "review":
        command.extend(["request-review", arguments.card, "--summary", message])
    else:
        command.extend(["block", arguments.card, message])
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
