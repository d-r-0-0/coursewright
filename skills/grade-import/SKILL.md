---
name: grade-import
description: >
  Import grades from a publisher or lab platform into the Canvas
  gradebook: Cisco NetAcad and MindTap exports (NetAcad Import /
  GradeLinerUpper), zyBooks score reports, and Packet Tracer checks,
  feedback, and auto-grading. Use when the instructor has exported a
  gradebook or wants to process Packet Tracer submissions.
---

# Grade import

## When to use it

- "Import the NetAcad grades", "sync the publisher gradebook".
- "Pull in the zyBooks scores."
- "Check Packet Tracer submissions, post feedback, or auto-grade complete labs."

## Finding the export

Exports are found Downloads-first (`coursewright.core.inbox`): the
newest file matching the export's name pattern is suggested, and the
instructor must confirm it before anything is read.  Never import a
file the instructor has not confirmed.

## NetAcad (command line & application)

Preview first.  The dry run maps export columns to Canvas assignments
(LLM-assisted the first time, cached afterwards) and shows every grade
it would write (`ta gradeliner` remains as an alias):

```
coursewright ta netacad-import --netacad <export.csv> --course <course-id>
```

Add `--remap` to redo the column mapping.  A cell the export leaves
blank means "not attempted yet" and is never imported, so exams that
are not due are untouched; an explicit `0` is a real score and is.
Missing past-due work is zeroed separately (see the `zero-ungraded`
skill).  When the instructor has read the preview and agreed, write:

```
coursewright ta netacad-import --netacad <export.csv> --course <course-id> --execute
```

Library: `coursewright.ta.gradeliner.GradeLinerUpper`.

## MindTap (library)

`coursewright.ta.gradeliner.parse_mindtap_gradebook` reads the
export, `coursewright.ta.gradeliner.build_grade_plan` returns the
planned changes for review, and
`coursewright.ta.gradeliner.apply_grade_changes` writes them.  Show
the plan before applying it.

## zyBooks Import (command line & application)

Preview Downloads-first score reports and module overview hints first
(dry run touches neither Canvas nor the mapping cache):

```
coursewright ta zybooks-import --course <course-id> --confirm-distinct-labs
```

Each lab grade is the average of the student's best labs.  The best
five count unless the assignment asks for a different number ("any 10
of the unit's labs"); then pass `--labs-required` with that number on
both the preview and the write.

Pass `--approve-zeros` if zero/reduced rows are expected, `--file` to
override auto-discovery, or `--json` for structured output. Once the
instructor approves the preview, execute:

```
coursewright ta zybooks-import --course <course-id> --confirm-distinct-labs --execute
```

In the Textual app, press `y` on a course for **zyBooks Import**.
Library: `coursewright.ta.zybooks.importer.CsvImportCoordinator`.

## Packet Tracer (command line & application)

Scan detected Packet Tracer assignments in a course:

```
coursewright ta packet-tracer --course <course-id>
```

Preview completeness checks, templated feedback comments, and full-credit
auto-grading on complete submissions:

```
coursewright ta packet-tracer --course <course-id> --assignment <assignment-id> --auto-grade --post-feedback
```

Once approved by the instructor (requires workspace templates unless
`--allow-generic-fallback` is passed), execute with backups:

```
coursewright ta packet-tracer --course <course-id> --assignment <assignment-id> --auto-grade --post-feedback --execute
```

In the Textual app, press `p` on a course. Library:
`coursewright.ta.packettracer.PacketTracerAssignmentDetector`,
`coursewright.ta.packettracer.SubmissionCompletenessChecker`, and
`coursewright.ta.packettracer.orchestrator`.

## Rules

- **Preview, then execute.**  Every write is a dry run until
  `--execute` (or the screen's approval step).  Read the preview with
  the instructor first.
- A timestamped backup is taken before any write; mention where it is.
