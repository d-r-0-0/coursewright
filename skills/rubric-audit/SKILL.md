---
name: rubric-audit
description: >
  Audit a Canvas course against a course-quality rubric (OSCQR is
  bundled; others can be added in the workspace) and report which
  standards have evidence.  Read-only.  Use when the instructor asks
  about course quality, accessibility review or rubric readiness.
---

# Rubric audit

## When to use it

- "How does my course score against the rubric?"
- "What do I still need for a quality review?"

## Commands

List the rubrics and read one:

```
coursewright design rubrics
coursewright design rubric <rubric-id>
```

Audit a course (read-only; the default rubric is used when
`--rubric` is omitted):

```
coursewright design audit <course-id>
coursewright design audit <course-id> --rubric <rubric-id>
```

The audit only reads Canvas and never writes, so there is no
`--execute`.  It reports each standard as `met`, `not-met` or
`needs-a-human`.

Library: `coursewright.design.standards.audit_course` and
`coursewright.design.standards.load_rubric`.

## Rules

- A rubric held by reference has no text in this repository: link to
  the standard; never paste its wording.
- `needs-a-human` is a prompt for the instructor's judgment, not a
  failure.  Present it that way.
