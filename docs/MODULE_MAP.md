# Module map

Where each piece of discussorator and COURSE_BUILDER lands in
Coursewright.  **Status** is `todo` until the code and its tests are
ported and the gate passes. This final map records the completed port and
subsequent audit fixes.

## core — shared by both halves

| Coursewright | From | Lines | Status |
| --- | --- | --- | --- |
| `core/canvas` | discussorator `canvas/client.py`, `models.py`; course-building calls from COURSE_BUILDER `lib/canvas_api.py` | 3,000 + 1,050 | **done** |
| `core/auth` | discussorator `auth/`; COURSE_BUILDER `cantrips/auth.py` | 1,450 | **done** |
| `core/llm` | discussorator `llm/` | 1,800 | **done** |
| `core/config` | discussorator `config/`; COURSE_BUILDER `config.py`, `lib/academic_calendar.py` | 300 | **done** |
| `core/export` | discussorator `export/` | 1,100 | **done** |
| `core/traffic` | discussorator `cli/tui/traffic_bus.py`, moved out of the UI | 150 | **done** |
| `core/inbox` | discussorator `zybooks/importer.py` Downloads lookup, generalised | 50 | **done** |

## ta — running a live course

| Coursewright | From (discussorator) | Lines | Status |
| --- | --- | --- | --- |
| `ta/discussions` | `assistant/`, `grading/analyzer.py`, `cantrips/daily_digest.py` | 1,450 + 900 | **done** |
| `ta/grading` | `grading/` | 1,900 | **done** |
| `ta/attendance` | `analysis/attendance.py`, `canvas/attendance_check.py` | 550 | **done** (1 fix card) |
| `ta/zybooks` | `zybooks/` | 2,050 | **done** |
| `ta/gradeliner` | `gradeliner/` | 1,350 | **done** |
| `ta/packettracer` | `packettracer/` | 800 | **done** |

*ta layer integration audit (AUDIT 2, `t_43c7c39a`): all six subpackages
ported, gate green (1587 passed), layering clean (core-only imports),
no student-data hygiene leaks, and every LLM call traced. The attendance
outreach send's dry-run/backup gate was fixed by `t_c837ea37` and merged on
`main` in `b5f5114`.*

## design — building a course

| Coursewright | From (COURSE_BUILDER) | Lines | Status |
| --- | --- | --- | --- |
| `design/diagnostics` | `scripts/diagnostic/` | 750 | **done** |
| `design/organization` | `scripts/organization/` | 500 | **done** |
| `design/discussions` | `scripts/discussions/` | 500 | **done** |
| `design/dates` | `scripts/dates/` | 600 | **done** |
| `design/publishing` | `scripts/publishing/` | 750 | **done** |
| `design/standards` | rubric engine + bundled OSCQR + QM by reference (new) | — | **done** |
| `design/standards` audit | Canvas-element mapping from `lib/qm_standards.py`, `scripts/diagnostic/qm_audit.py`, re-keyed to OSCQR | 600 | **done** |
| `design/conversion` | `scripts/conversion/`, caption cantrips | 350 + | **done** (1 fix card) |
| `design/media` | `lib/panopto_api.py`, `lib/kahoot_template.py` | 600 | **done** |

*design layer integration audit (AUDIT 4, `t_61aedb83`): all eight
subpackages ported and tested, gate green (1721 passed, 1 deselected),
layering clean (design imports core only), every Canvas write dry-run by
default with backup-first, no course-material, institution-specific or QM-text hygiene
leaks on the design surface. `design/conversion/srt_fetch.py` now uses the
sanctioned `PanoptoClient` choke point; `t_9f5c89f0` was merged on `main` in
`204c8b1`.
COURSE_BUILDER inventory: `lib/logger.py`→`core/config/logger.py`,
`lib/academic_calendar.py`→`core/config/calendar.py`,
`lib/canvas_api.py`→`core/canvas`, the legacy course-specific targets
module→dropped (course-specific), `scripts/verify_setup.py`→absorbed into
the 8-stage `cantrips/verify.py` gate; all six `scripts/` phase dirs and the generic
cantrips ported. Nothing missing.*

## ui — human interfaces

| Coursewright | From | Lines | Status |
| --- | --- | --- | --- |
| `ui/cli` | discussorator `cli/*.py` | 8,200 (with tui) | **done** — TA command groups are available under `coursewright ta`; legacy Rich menus are represented by documented compatibility routes and the Textual route. |
| `ui/tui` | discussorator `cli/tui/` | — | **done** — live-course screens, design flow, visible traffic, and documented keyboard navigation are available in the Textual application. |

COURSE_BUILDER has no interface of its own; `coursewright design`
commands and screens are new work.

## skills

| Skill | From | Status |
| --- | --- | --- |
| discussion review | new, over `ta/discussions` | **done** |
| daily digest | discussorator `cantrips/daily_digest.py`, over `ta/discussions` | **done** |
| five-phase course build | COURSE_BUILDER README and `.claude/rules/` | **done** |
| unit pipeline | COURSE_BUILDER legacy course-specific unit-pipeline skill, made course-neutral | **done** |

## Not carried over

- COURSE_BUILDER `materials/` (course content, 1.1 GB) and `schedules/`.
- Course- and term-specific cantrips (76 of COURSE_BUILDER's 130) and
  the legacy course-specific targets module.
- `sessions/` notes and `.github/AI_LESSONS.md` from both projects.
- discussorator `GradeLinErUpper/` and `PacketTracer/` top-level folders;
  their reusable source implementations were ported as `ta/gradeliner` and
  `ta/packettracer`, so the legacy top-level folder layouts were not carried
  over.

## Suggested port order

1. `core/config`, `core/auth`, `core/canvas` — everything depends on these.
2. `core/llm`, `core/export`.
3. `ta/*`, then `ui/` — this reaches parity with discussorator.
4. `design/*` phases onto the unified Canvas client.
5. `skills/` and the new design screens.

## Decisions

- **Quality rubrics.** Quality Matters text is copyrighted and its
  public one-page standards PDF says it may not be copied without
  written permission, so no QM wording ships here.
  `design/standards` is a rubric engine that loads a rubric file:
  - **Bundled default:** OSCQR (SUNY Online Course Quality Review),
    50 standards, CC BY 4.0, with attribution to SUNY Online.
  - **QM by reference:** standard numbers only, each linking to QM's
    own page: no text, section names or point values.  A subscribing
    institution can keep full QM text as `rubrics/qm-he-7.toml` in its
    private workspace, which overrides the bundled file.
  - `tests/test_rubric.py` fails if any text is added to the QM file.
- **Course work is private and lives outside this repo.** This repo
  stays local until it is polished: the `origin` push URL is disabled
  on purpose.  Once public, nothing private can be in it.  Materials, schedules, exports and course-specific
  scripts live in a separate private workspace whose path is set in
  configuration (`COURSEWRIGHT_WORKSPACE`).  The ignored `workspace/`
  folder here is only a safety net.

## Port-time hygiene resolution

- **Student identifiers:** source prompts and fixtures were reviewed as
  modules were ported.  The public tree uses synthetic data and rejects
  real names, addresses, and Canvas identifiers.
- **Templates and defaults:** only reusable template structure was ported.
  Contact details, policy text, calendars, and template names are supplied
  through configuration or an instructor's private workspace.
