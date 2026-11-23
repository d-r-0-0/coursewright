# AGENTS.md — Instructions for All AI Coding Agents

## 1. Project Overview

Coursewright is an AI teaching assistant and instructional designer
for Canvas LMS.  It merges two earlier projects: discussorator (the
TA) and COURSE_BUILDER (the designer).

| Read | For |
| --- | --- |
| `docs/HOUSE_STYLE.md` | API client model, Downloads-first, remote activity |
| `docs/PORTING.md` | How a module is ported and reviewed |
| `docs/MODULE_MAP.md` | What lands where, and its status |

**Stack:** Python 3.11+, Typer + Rich + Textual, Pydantic v2, Canvas
REST API, keyring, pytest, ruff.

## 2. Layout

```
src/coursewright/
  core/     Canvas client, auth, LLM, config, export, traffic, inbox
  ta/       running a live course
  design/   building a course (five phases)
  ui/       Typer command line + Textual application
skills/     agent skills, one folder per capability
cantrips/   short scripts that keep commands under 80 characters
tests/      mirrors src/coursewright/
```

## 3. Hard Rules (non-negotiable)

- **3.1 Command-line rule** — No CLI command > 80 characters.
  If longer, create a cantrip in `cantrips/`.
- **3.2 Verification loop** — While editing, run only the tests
  nearest the change.  Before claiming done, run the full gate:
  `python cantrips/verify.py` (stages in `verify.toml`).  Max 5
  iterations; then stop and report.
- **3.3 Coding style** — Long descriptive names.  Verbose teaching
  comments are welcome.  Small diffs (< 300 lines per commit).
- **3.4 Exception handling** — All re-raises preserve the chain:
  `from err` or `from None`.
- **3.5 Refactors preserve behavior** — Same outputs, same API
  contracts, same test results.  Porting a module is a refactor.
- **3.6 Layering** — `core` imports nothing from `ta`, `design` or
  `ui`.  `ta` and `design` import `core` only, never each other and
  never `ui`.  `tests/test_scaffold.py` enforces this.
- **3.7 Three faces per capability** — A capability is a library
  function first.  The CLI command and the skill in `skills/` are
  thin wrappers over it.  No logic lives only in `ui/` or a skill.
- **3.8 Library code is silent** — `core`, `ta` and `design` return
  data and raise exceptions.  Only `ui/` prints or prompts.
- **3.9 Rich "turned up to 11"** — In `ui/`: panels, tables,
  spinners, progress.  Never a bare `print`.
- **3.10 Canvas dates** — `YYYY-MM-DDTHH:MM:SS`, **no `Z` suffix**.
- **3.11 Safe Canvas writes** — Dry run by default, `--execute` to
  write, timestamped JSON backup first, no silent failures.
- **3.12 One Canvas path** — Only `coursewright.core.canvas`, built
  to the API model in `docs/HOUSE_STYLE.md` §1.
- **3.13 Public repo hygiene** — No student data, no course
  materials, no institution- or course-specific code, no secrets,
  no Quality Matters rubric text.  Instructor working files live in
  a private workspace outside this repo (`COURSEWRIGHT_WORKSPACE`).
- **3.14 Virtualenv** — Every command runs in `.venv`.
- **3.15 Downloads first** — Files the instructor exported are found
  through `core/inbox`: Downloads folder first, newest match, always
  confirmed (`docs/HOUSE_STYLE.md` §2).
- **3.16 Remote activity is visible** — Every remote round trip
  publishes a traffic event from the client's single request choke
  point, and `ui/` renders it (`docs/HOUSE_STYLE.md` §3).
- **3.17 Local only** — Never push.  The repo stays local until its
  owner publishes it.

## 4. Key Commands

```bash
python cantrips/verify.py          # the gate (--list, --only lint)
make verify                        # POSIX alias
```

## 5. Bailout Rules

- Same error **twice**, or a rate limit, usage cap or auth error → **STOP.**
- No creative workarounds for infrastructure issues.
- **Priority:** code quality > passing tests > push > PR.
- When stopping early, summarize: what worked, what failed, what remains.
