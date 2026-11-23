---
name: attendance-check
description: >
  Find students who have gone quiet and, if the instructor agrees,
  send them an attendance warning; or summarise engagement across an
  exported course.  Use when the instructor asks who is inactive, who
  has stopped participating, or wants attendance warnings sent.
---

# Attendance check

## When to use it

- "Who hasn't done anything in two weeks?"
- "Send the attendance warnings."
- "How is participation across the whole course?"

## Inactive students and warnings

Preview first.  The dry run lists each inactive student, their last
activity and the message they would get; nothing is sent:

```
coursewright ta attendance-warning --course-id <course-id>
```

Tune with `--inactive-days` (default 14) and `--deadline-days`
(default 3).  Only after the instructor has read the preview and
agreed, send the messages:

```
coursewright ta attendance-warning --course-id <course-id> --execute
```

Library: `coursewright.ta.attendance.outreach.find_students_inactive_for_threshold`
and `coursewright.ta.attendance.outreach.render_warning_for_student`.

## Course-wide engagement (read-only)

Export the course's discussions to a local folder, then analyse it:

```
coursewright ta discussions export-course <course-id> --output <folder>
coursewright ta analyze-course <folder>
```

The report is written to `<folder>/_ANALYSIS/summary_report.md`.
Library: `coursewright.ta.attendance.course_analyzer.analyze_course_directory`.

## Rules

- **Preview, then execute.**  Messages go to real students; read the
  dry run with the instructor before adding `--execute`.
- Exports hold student data: keep them in the private workspace,
  never in this repository.
