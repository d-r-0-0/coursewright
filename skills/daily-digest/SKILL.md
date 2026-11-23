---
name: daily-digest
description: >
  Build the daily discussion digest: every new post across the courses
  the instructor teaches, flagged by its role in the three-post process,
  with deep links and a JSON payload for drafting replies.  Use for a
  morning check-in, a scheduled run, or "what's new in my discussions?".
---

# Daily digest

One read-only command over `coursewright.ta.discussions.daily_digest`
(`coursewright.ta.discussions.build_daily_digest` scans,
`coursewright.ta.discussions.write_daily_digest` saves).  This skill
only says what to run and how to read the result; it holds no logic.

## When to use it

- "What's new in my discussions?" or "who needs a reply today?"
- A scheduled morning run that should not need any other Canvas tool.

## Steps

1. **Build the digest** (read-only; nothing is posted to Canvas):

   ```
   coursewright ta discussions digest
   ```

   The window is 72 hours on Monday and 24 otherwise; pass `--hours`
   to override.  Pass `--instructor-name` (repeatable) with the names
   students use for the instructor, or set `instructor_names` under
   `discussion` in the config, so "mentions you" can match them.

2. **Check the banners first.**  A "STALE — DO NOT TRUST" banner means
   the local clock is behind Canvas: fix the clock and re-run.  An
   "INCOMPLETE" banner lists courses the run budget did not reach.

3. **Read the files.**  They land in the private workspace under
   `exports/digests/` unless `--out` names a path:

   - `daily_digest_<date>.md` — the digest.  If someone has already
     enriched it, a re-run leaves it alone.
   - `daily_digest_<date>.raw.md` — always the newest generated copy.
   - `daily_digest_<date>.posts.json` — every new post, for summaries.

4. **Pick reply targets** in this order: "mentions you", then an
   unreplied Post 2, then short posts worth an early nudge.  Draft
   with the `discussion-review` skill; the instructor posts it.

## Rules

- Read-only: the digest never writes to Canvas.
- Never run it without a private workspace or an explicit `--out`;
  student posts must not land in this repository.
- Never paste student names or post text outside the instructor's
  own machine.
