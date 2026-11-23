---
name: apply-dates
description: >
  Set unlock and due dates on a Canvas course's modules, assignments,
  quizzes and discussions from a schedule file.  Use when the
  instructor has a term schedule and wants it applied, or asks to fix
  or shift due dates (Phase 4 of a course build).
---

# Apply dates (Phase 4)

## When to use it

- "Put the schedule into the course", "set the due dates".
- After a schedule file has been written or edited.

## The schedule file

A tab-separated file, one unit per line, kept in the instructor's
private workspace (`COURSEWRIGHT_WORKSPACE`), never in this
repository.  The format is documented in
`coursewright.design.dates.schedule`; read that docstring before
writing or editing one.  Use two-digit unit names (`Unit 01`).
To draft a new one, `coursewright.design.dates.generate.render_schedule_file`
renders the text and `coursewright.design.dates.generate.write_schedule_file`
saves it (it refuses to overwrite unless asked).

## Steps

1. Preview.  The dry run lists every item and the dates it would get:

   ```
   coursewright design dates apply <course-id> <schedule-file>
   ```

   Check the warnings: skipped rows and modules with no matching unit
   are reported, never silently dropped.

2. When the instructor has read the preview and agreed, write:

   ```
   coursewright design dates apply <course-id> <schedule-file> --execute
   ```

   A timestamped JSON backup is written before any change.

Library: `coursewright.design.dates.schedule.parse_schedule_file` and
`coursewright.design.dates.apply.apply_dates_to_course`.

## Rules

- **Preview, then execute.**  Never pass `--execute` before the
  instructor has seen the dry run.
- Canvas dates are `YYYY-MM-DDTHH:MM:SS` with no `Z` suffix; the
  library handles this, so do not hand-build date strings.
