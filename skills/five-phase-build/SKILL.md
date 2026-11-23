---
name: five-phase-build
description: >
  Build or rebuild a Canvas course shell end to end with the five-phase
  framework: diagnose, organize, discussions, dates, publish.  Use when
  the instructor asks to set up a new term's shell, rebuild a course,
  or "get the course ready", and to decide which phase comes next.
---

# Five-phase course build

The phases run in order.  Each is one `coursewright design` command
group, and each has (or is) its own skill.  This skill only sets the
order and the checkpoints; it holds no logic.

| Phase | Command | Writes? |
| --- | --- | --- |
| 1 Diagnose | `coursewright design diagnose run` | no |
| 2 Organize | `coursewright design organize unit-order` | yes |
| 3 Discussions | `coursewright design discussions create` | yes |
| 4 Dates | `coursewright design dates apply` | yes |
| 5 Publish | `coursewright design publish content` | yes |

## The loop for every writing phase

1. Run the command **without** `--execute` and show the preview.
2. Wait for the instructor to agree.
3. Run it again with `--execute`.  A timestamped backup is taken first.
4. Re-run Phase 1 to confirm the change landed.

## Phases

1. **Diagnose** (see `course-diagnose`):

   ```
   coursewright design diagnose run <course-id>
   ```

2. **Organize** — put the items inside each unit module in order:

   ```
   coursewright design organize unit-order <course-id>
   coursewright design organize unit-order <course-id> --execute
   ```

3. **Discussions** — one discussion in each odd-numbered unit:

   ```
   coursewright design discussions create <course-id> --last-unit 13
   coursewright design discussions create <course-id> --last-unit 13 --execute
   ```

   Prompt text can be drafted first with
   `coursewright.design.discussions.draft_discussion_starter_post`.

4. **Dates** (see `apply-dates`):

   ```
   coursewright design dates apply <course-id> <schedule-file>
   coursewright design dates apply <course-id> <schedule-file> --execute
   ```

5. **Publish** (see `publish-content`):

   ```
   coursewright design publish content <course-id>
   coursewright design publish content <course-id> --execute
   ```

Finish with `coursewright design audit <course-id>` (see
`rubric-audit`) for a quality check.

## Rules

- **Preview, then execute**, every phase, every time.
- One course id at a time.  When several sections share content, run
  the phase once per section; do not assume they are identical.
- Schedules and reports live in the private workspace
  (`COURSEWRIGHT_WORKSPACE`), never in this repository.
