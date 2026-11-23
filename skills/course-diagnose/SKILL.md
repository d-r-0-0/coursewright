---
name: course-diagnose
description: >
  Health-check a Canvas course shell: modules, items, missing due
  dates and other structural problems.  Read-only.  Use when the
  instructor asks what is wrong with a course, whether a shell is ready,
  or as Phase 1 of a course build.
---

# Course diagnose (Phase 1)

## When to use it

- "Check my course shell", "what's broken in this course?"
- Before any other design phase, to see the starting point.

## Command

```
coursewright design diagnose run <course-id>
```

This only reads Canvas; it never writes, so there is nothing to
preview and no `--execute`.  The full report is saved to the private
workspace (`COURSEWRIGHT_WORKSPACE`); the command prints where.  If no
workspace is configured it says so: ask the instructor to set one
rather than writing reports into this repository.

Library: `coursewright.design.diagnostics.diagnose_course_by_id`.

## What to do with the result

Summarise the findings for the instructor, grouped by module, and
suggest which phase fixes each one: ordering problems go to
`coursewright design organize unit-order`, missing dates to
`coursewright design dates apply`, unpublished content to
`coursewright design publish content`.  Each of those is its own
skill (`five-phase-build`, `apply-dates`, `publish-content`).
