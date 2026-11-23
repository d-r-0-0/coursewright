# House style

Three patterns from discussorator are the standard for all of
Coursewright.  New code and ported COURSE_BUILDER code follow them.

## 1. The API model: one client per remote service

Reference: discussorator `src/discussorator/canvas/client.py` and
`canvas/models.py`.

- **One client class per remote service** (Canvas, LLM provider,
  Panopto, zyBooks).  Nothing else opens a connection to that service.
- **Auth is a strategy object** (`AuthStrategy` with `TokenAuth`,
  `SessionAuth`).  The client never knows which kind it holds, and a
  strategy's `repr` never shows the secret.
- **One choke point.**  Every call goes through a single
  `_make_request`, which owns the timeout (30 s ceiling), rate
  limiting, error logging and traffic events.  Lists go through one
  `_paginated_get`.
- **Typed results.**  Public methods return Pydantic v2 models, not
  raw dictionaries.  Existing dict-returning methods are ported as
  they are (rule 3.5) and converted in a follow-up card.
- **Named methods, one per operation**, with long descriptive names
  (`get_assignment_submissions`, `post_submission_comment`).
- **Writes are safe by default** (AGENTS.md 3.11): dry run unless
  told to execute, backup first, failures are never silent.
- **A factory builds the client** (`create_canvas_client`), resolving
  credentials through `core/auth`.

COURSE_BUILDER's `lib/canvas_api.py` is folded into this client; its
function-style calls become methods here.

## 2. Downloads first: finding files the instructor exported

Reference: discussorator `zybooks/importer.py`
(`default_download_directory`, `detect_csv`).

When a workflow needs a file the instructor exported from a website
(zyBooks report, publisher gradebook, Canvas export):

1. Look in the user's **Downloads folder first** (on Windows, the
   registered Known Folder, so a moved Downloads still works).
2. Pick the **newest file matching that export's name pattern**.  In
   a shared folder like Downloads, never suggest a file that does not
   match the pattern.
3. Fall back to the workspace import folder, then to an explicit path.
4. **Show the suggestion and ask.**  Never import a discovered file
   without the instructor confirming it.

This lives in `core/inbox` as one shared helper; importers pass their
pattern and do not reimplement the search.

## 3. Always show remote activity

Reference: discussorator `cli/tui/traffic_bus.py` and the
`_publish_traffic_event` hooks in its Canvas and LLM clients.

Whenever the local system talks to a remote one, the person at the
keyboard can see that something is happening.

- **Every client publishes a traffic event** for every round trip:
  source, method, target, status, duration.  This is part of the
  `_make_request` choke point, so no call can skip it.
- **The bus lives in `core/traffic`**, not in `ui/` (discussorator's
  clients import it from the TUI; that upward import is not ported).
  Publishing never raises and never slows a request.
- **`ui/` subscribes and renders.**  The command line shows a Rich
  status spinner or progress bar naming the service and operation;
  the Textual app shows the live transmission log.
- **No silent waits.**  A remote call that can take more than about a
  second runs under visible activity; known-length work (pagination,
  batches) shows progress with counts.
- **Secrets and student data never appear** in traffic output: paths
  are shortened, tokens and bodies are not echoed on the command line.
