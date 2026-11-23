---
name: zero-ungraded
description: >
  Preview and safely zero out unsubmitted, past-due Canvas assignments
  without overwriting submitted work, excused grades, test-student rows,
  or unpublished items. Use when the instructor asks to zero out past-due
  missing work in a course.
---

# Zero ungraded

Cleanly zero unsubmitted work past its due date without clobbering real student submissions, inactive/test enrollments, or unimported assignments. Library functions live in `coursewright.ta.gradeliner.plan_zero_ungraded` and `coursewright.ta.gradeliner.zero_ungraded_submissions`.

## When to use it

- "Zero out past-due unsubmitted work in this course."
- "Put zeros in for missing work after the due date."

## Safety defaults built into Coursewright

- Skips unpublished assignments, excused submissions, inactive enrollments, and Canvas `Test Student` rows by default.
- Skips submitted-but-ungraded work (`submitted` / `pending_review`) unless `--include-submitted` is explicitly supplied.
- Flags assignments where **every** student is ungraded (`is_all_ungraded`) so unimported external tool scores or ungraded manual assignments are never wiped with zeros.

## Preview first

Run a dry run and inspect every flagged warning (submitted rows, all-ungraded external tools, or optional labs) with the instructor:

```
coursewright ta zero-ungraded --course <course-id> --past-due
```

Pass `--json` for structured output, `--assignment <id>` to restrict to specific assignments, or `--all` instead of `--past-due`.

## Execute after approval

Only after the instructor reviews the preview table and confirms which assignments should receive zeros, execute with timestamped JSON backups:

```
coursewright ta zero-ungraded --course <course-id> --past-due --execute
```

## Rules

- **Preview, then execute.** Never add `--execute` until the instructor has reviewed the dry-run targets and confirmed.
- Check `<workspace>/course-policies.yaml` before selecting courses or assignments.
- A timestamped JSON backup is written to `<workspace>/backups/canvas/` before every score update.
