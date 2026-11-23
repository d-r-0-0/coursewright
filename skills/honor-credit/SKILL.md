---
name: honor-credit
description: >
  Give full credit to honor-system work: assignments graded on
  completion alone, where anything a student submitted earns the full
  points (publisher module quizzes are the usual case). Use when the
  instructor says a set of assignments is on the honor system or asks
  to give completion credit for whatever was turned in.
---

# Honor credit

Award the assignment's full points to every submission that is waiting
on the instructor, without touching anything else. Library functions:
`coursewright.ta.gradeliner.plan_honor_credit` and
`coursewright.ta.gradeliner.execute_honor_credit_plan`.

## When to use it

- "Those quizzes are on the honor system; if they submitted, give them credit."
- "Give completion credit for the module quizzes."

## What it changes, and what it never changes

- Gets full credit: a submission Canvas shows as `submitted` or
  `pending_review`, whether it has no score yet or was turned in late
  on top of an earlier, lower score (usually a past-due zero).
- Left alone: unsubmitted rows, excused rows, inactive and test
  enrollments, unpublished or zero-point assignments, and any score the
  instructor already gave that the student has not resubmitted against.

## Choose the assignments

The command refuses to run against a whole course. Name the
assignments by text their names share with `--match`, by ID with
`--assignment` (repeatable), or both; `--points` narrows a name match
to assignments worth exactly that many points.

## Preview first

```
coursewright ta honor-credit --course <course-id> --match <text> --points <points>
```

Read the table with the instructor. The "Replaces Score" column counts
late submissions that would be raised from an earlier score; pass
`--new-only` to leave those alone. `--json` gives every row.

## Execute after approval

```
coursewright ta honor-credit --course <course-id> --match <text> --points <points> --execute
```

Each score is posted with a short comment; `--comment` changes it.

## Rules

- **Preview, then execute.** Never add `--execute` until the instructor
  has confirmed which assignments are on the honor system and read the
  dry run.
- Run it before zeroing past-due work, so only students who submitted
  nothing are left for the zero pass.
- A timestamped JSON backup is written to `<workspace>/backups/canvas/`
  before every score update.
