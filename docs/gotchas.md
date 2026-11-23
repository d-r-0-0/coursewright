# Gotchas

## Dates must not end in `Z`

Canvas values use `YYYY-MM-DDTHH:MM:SS`, with no `Z` suffix. A trailing `Z`
declares UTC and can shift the displayed date. Let the date workflow format
values rather than constructing them by hand.

## Preview is not execution

Canvas-writing workflows default to a dry run. Read the table, then add
`--execute` only when the shown scope is correct. A supported write makes a
timestamped JSON backup first; configure `COURSEWRIGHT_WORKSPACE` so it has a
safe destination.

## Canvas lists are paginated

Use Coursewright's Canvas client and named library operations, not ad-hoc HTTP
requests. The client handles pagination, rate limits, timeouts, typed results,
and visible remote activity from one request path.

## Schedules must match modules

The date workflow expects a tab-separated schedule file. Its module names must
match the target Canvas modules. Run diagnosis first and use the preview's
skipped-module notices to correct the schedule before execution.

## Publishing has layers

A published item inside an unpublished module is still unavailable to students.
The publishing preview shows both module and item scope. Use `--first` only
when intentionally releasing the first N modules.

## Export and import files can be private

Canvas exports, gradebooks, captions, and reports may contain student or
course-specific information. Keep them in `COURSEWRIGHT_WORKSPACE`; do not add
them to the repository. Imports are Downloads-first, but the suggested file
must always be confirmed.
