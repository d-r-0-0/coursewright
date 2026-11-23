---
name: publish-content
description: >
  Publish a Canvas course's modules and the items inside them, all at
  once or only the first few modules.  Use when the instructor asks to
  publish, open or release course content (Phase 5 of a course build).
---

# Publish content (Phase 5)

## When to use it

- "Publish the course", "open the first three units".
- The last step of a course build, once dates are in.

## Steps

1. Preview.  The dry run lists each module and item that would be
   published:

   ```
   coursewright design publish content <course-id>
   coursewright design publish content <course-id> --first 3
   ```

2. When the instructor has read the preview and agreed, write:

   ```
   coursewright design publish content <course-id> --execute
   coursewright design publish content <course-id> --first 3 --execute
   ```

   A backup is written first, and every per-item failure is listed.

Library: `coursewright.design.publishing.content.plan_content_publishing`
and `coursewright.design.publishing.content.publish_course_content`.

## Rules

- **Preview, then execute.**  Publishing is visible to students the
  moment it happens.  Confirm the scope (`--first`) before `--execute`.
- Run `coursewright design dates apply` first, so content does not
  open with no due dates.
