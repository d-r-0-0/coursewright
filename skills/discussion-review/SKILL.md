---
name: discussion-review
description: >
  Review a live Canvas discussion: read the posts, rank them, flag
  similar posts, draft instructor replies and grade participation.
  Use when the instructor asks about discussion boards, student posts,
  who needs a reply, or discussion grades.
---

# Discussion review

Every step below is a `coursewright ta` command over the library in
`coursewright.ta.discussions` and `coursewright.ta.grading`.  This
skill only says which command to run and in what order; it holds no
logic of its own.

## When to use it

- "What's going on in my discussions?" or "who needs a reply?"
- "Draft a reply to the best post in this week's discussion."
- "Grade the discussion" or "post the discussion grades".

## What you need

The Canvas course id and the discussion topic id.  If you only have
the course, list its topics first:

```
coursewright ta discussions list <course-id>
```

## Steps

1. **Read the board** (read-only):

   ```
   coursewright ta discussions summary <discussion-id> --course <course-id>
   coursewright ta discussions posts <discussion-id> --course <course-id>
   ```

2. **Rank posts and flag similar ones** (read-only; writes a local
   JSON report only):

   ```
   coursewright ta assistant analyze <discussion-id> --course <course-id>
   ```

   Surface similarity findings to the instructor as something to look
   at, never as an accusation.

3. **Draft a reply** (local markdown; nothing is posted):

   ```
   coursewright ta assistant respond <discussion-id> --course <course-id>
   ```

   Pass `--post <post-id>` to reply to a specific student.  The
   instructor edits and posts the reply; do not post on their behalf.

4. **Grade participation.**  Preview the grades first and show the
   instructor the table:

   ```
   coursewright ta discussions grade <discussion-id> --course <course-id>
   coursewright ta discussions submit-grades <discussion-id> --course <course-id>
   ```

   Only after the instructor has read the dry run and agreed, write
   the grades:

   ```
   coursewright ta discussions submit-grades <discussion-id> --course <course-id> --execute
   ```

   `coursewright ta discussions process <discussion-id>` runs grade,
   review, submit and export in one go; it too is a dry run until
   `--execute` is added.

## Rules

- **Preview, then execute.**  Every grade write is a dry run until
  `--execute`.  Read the preview with the instructor before adding it.
- Never paste student names or post text into anything outside the
  instructor's own machine.
