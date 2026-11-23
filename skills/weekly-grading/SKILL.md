---
name: weekly-grading
description: >
  Orchestrate weekly grading across courses in order: preflight checks,
  discussion grading, publisher grade imports, Packet Tracer checks,
  honor-system credit, and zeroing past-due unsubmitted work with
  previews before any live writes.
---

# Weekly grading

Follow one repeatable sequence across active courses, keeping every write behind a preview and respecting per-course instructor policies.

## 0. Preflight and course policies

Read `<workspace>/course-policies.yaml` first so course-specific rules (manual-only discussions, rubric profiles, top-N lab policies, external LTI guards) govern every step. Verify workspace, templates, Canvas auth, and LLM readiness:

```
coursewright ta doctor
```

## 1. Discussions (per policy profile)

Skip any course whose policy marks discussions as manual-only. Otherwise list topics, preview grades, and write only after instructor approval:

```
coursewright ta discussions list <course-id>
coursewright ta discussions submit-grades <discussion-id> --course <course-id>
```

After instructor approval:

```
coursewright ta discussions submit-grades <discussion-id> --course <course-id> --execute
```

## 2. Publisher and zyBooks imports

Run external imports **before** zeroing past-due work so imported scores land first.

NetAcad import (dry run, then execute after approval):

```
coursewright ta netacad-import --netacad <export.csv> --course <course-id>
```

```
coursewright ta netacad-import --netacad <export.csv> --course <course-id> --execute
```

zyBooks import (Downloads-first CSV discovery; dry run, then execute after approval). The best five labs count by default; add `--labs-required` when the assignment asks for a different number:

```
coursewright ta zybooks-import --course <course-id> --confirm-distinct-labs
```

```
coursewright ta zybooks-import --course <course-id> --confirm-distinct-labs --execute
```

## 3. Packet Tracer labs

Scan for Packet Tracer assignments, inspect completeness and templates, preview grades/feedback, then execute after approval:

```
coursewright ta packet-tracer --course <course-id>
coursewright ta packet-tracer --course <course-id> --assignment <assignment-id> --auto-grade --post-feedback
```

```
coursewright ta packet-tracer --course <course-id> --assignment <assignment-id> --auto-grade --post-feedback --execute
```

## 4. Honor-system (completion) credit

Where the instructor grades a set of assignments on completion alone, give full credit to whatever was submitted before zeroing anything. Preview, then execute after approval:

```
coursewright ta honor-credit --course <course-id> --match <text> --points <points>
```

```
coursewright ta honor-credit --course <course-id> --match <text> --points <points> --execute
```

## 5. Zero past-due unsubmitted work

Only after discussions, imports and honor credit are in Canvas, preview past-due zeros, inspect warnings, and execute after instructor sign-off:

```
coursewright ta zero-ungraded --course <course-id> --past-due
```

```
coursewright ta zero-ungraded --course <course-id> --past-due --execute
```

## Rules

- **Preview, then execute.** Every grade or comment write runs as a dry run first until `--execute` is confirmed with the instructor.
- Keep all student names, course IDs, and private templates inside `<workspace>`, never in the repository.
