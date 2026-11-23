# Coursewright

Coursewright is an AI teaching assistant and instructional designer for
Canvas LMS. It has two connected halves:

- `coursewright ta` helps run a live course: discussion review, grade imports,
  attendance, and exports.
- `coursewright design` helps prepare a course shell through diagnose,
  organize, discussions, dates, and publishing.

It is deliberately conservative around Canvas: reads show visible activity;
writes preview first and require `--execute`; and a private workspace holds
course material, exports, and backups.

## Install

Coursewright requires Python 3.11 or later. Use `python3` on Linux and macOS,
or `python` on Windows. On Debian or Ubuntu, if virtual-environment creation
fails because `ensurepip` is missing, install its support package first:
`sudo apt install python3-venv` (or the matching version, such as
`python3.13-venv`).

On Linux or macOS, create and activate a virtual environment, then choose a
developer or regular install:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

On Windows, use `python -m venv .venv`, then activate with
`.venv\Scripts\activate` before running the install command above. For a
regular install, replace the last line with `python -m pip install .`.

Browser-based Canvas login and Packet Tracer document analysis have optional
dependencies. Install the extras you plan to use in the same environment:

```bash
python -m pip install -e ".[browser,packettracer]"
python -m playwright install chromium
```

Browser login also needs a graphical browser session for the institutional
sign-in flow. Token-based Canvas access does not need Playwright.

Confirm the installation:

```bash
coursewright --version
coursewright --help
```

To run the developer verification gate without activating the environment on
Linux or macOS:

```bash
.venv/bin/python cantrips/verify.py
```

## First run

Set a private workspace before working with a real course. It is where
Coursewright can keep private files without putting them in this public-ready
repository. See [the workspace guide](docs/workspace.md).

```bash
export COURSEWRIGHT_WORKSPACE="$HOME/coursewright-work"
coursewright auth login
```

For the teaching-assistant half, open the live-course application. It validates
the Canvas connection before opening the course list:

```bash
coursewright ta
coursewright ta start
```

For the course-design half, inspect the five-phase command groups, then run a
read-only diagnosis of a synthetic example course identifier:

```bash
coursewright design --help
coursewright design diagnose run 1001
```

`coursewright tui` opens the full Textual application directly. The older
`coursewright menu` and `coursewright ta menu` routes still work but are
deprecated.

## What the CLI looks like

The root help is rendered by Rich. This abbreviated capture reflects the
current command tree:

```text
Usage: coursewright [OPTIONS] COMMAND [ARGS]...

An AI teaching assistant and instructional designer for Canvas.

Commands
  tui       Open the full-screen Textual application.
  ta        Run a live course: discussions, grading, imports.
  design    Build a course: the five-phase framework.
  auth      Authentication management
  config    Configuration management
```

The design command exposes the workflow rather than hiding it behind one
unsafe operation:

```text
Usage: coursewright design [OPTIONS] COMMAND [ARGS]...

Commands
  audit        Audit a course against a rubric. Reads Canvas; never writes.
  rubrics      List the course-quality rubrics available.
  diagnose     Phase 1: check a course shell (read-only).
  organize     Phase 2: put modules and items in order.
  discussions  Phase 3: create discussion topics.
  dates        Phase 4: apply unlock and due dates.
  publish      Phase 5: publish modules and items.
  convert      Convert local files (names, captions).
```

## Safe workflows

Canvas writes are previews by default. Read the preview, confirm its scope,
then repeat the exact command with `--execute`. A timestamped JSON backup is
made before a supported Canvas write.

```bash
coursewright design organize unit-order 1001
coursewright design organize unit-order 1001 --execute
coursewright ta attendance-warning --course-id 1001
coursewright ta attendance-warning --course-id 1001 --execute
```

Canvas date values use `YYYY-MM-DDTHH:MM:SS` without a `Z` suffix. The date
workflow formats these values for you; do not append the suffix yourself.

## Documentation

- [User guide](docs/user-guide.md): common TA and design workflows.
- [Authentication](docs/auth.md): Canvas credentials and configuration.
- [Private workspace](docs/workspace.md): files that must stay outside Git.
- [Gotchas](docs/gotchas.md): the common Canvas and workflow pitfalls.
- [Testing](docs/testing.md): local verification and test conventions.
- [Human smoke test](docs/smoke-test.md): end-to-end acceptance before real use.
- [Contributing](CONTRIBUTING.md), [security](SECURITY.md), and
  [changelog](CHANGELOG.md).

## License

Coursewright is licensed under AGPL-3.0-or-later. Contributions require a
Contributor License Agreement so maintainers can offer a separately
dual-licensed version. See [CONTRIBUTING.md](CONTRIBUTING.md).

### Third-party content

- The bundled OSCQR rubric
  (`src/coursewright/design/standards/rubrics/oscqr.toml`) is the SUNY Online
  Course Quality Review Rubric, © State University of New York / SUNY Online,
  licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Source: <https://oscqr.suny.edu/>. Only the standard statements are
  included, reformatted as TOML. Coursewright is not endorsed by SUNY.
- Quality Matters is referenced by standard number only
  (`src/coursewright/design/standards/rubrics/qm-he-7.toml`). No Quality
  Matters rubric text is included, and Coursewright is not endorsed by
  Quality Matters, Inc.
