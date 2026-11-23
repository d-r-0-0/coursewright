# Human smoke test

Use this checklist after the automated verification gate passes. Record the
commit, operating system, authentication method and results in the private
workspace. Real Canvas permissions, institutional SSO, live provider behavior
and the usability of real instructor exports still need this human check.

## Prepare

1. Activate the installed environment and run `coursewright --version`.
2. Set `COURSEWRIGHT_WORKSPACE` to a private directory outside the repository.
   Put the selected configuration, exports and schedule there. Pass the same
   `--config` path to each CLI invocation throughout the test.
3. Use a disposable Canvas course with synthetic learners. Prepare a graded
   discussion, assignments worth 50 and 200 points, an excused submission,
   an already graded submission, and one genuinely ungraded submission.
4. Keep the Canvas gradebook and course contents open separately so you can
   compare previews and executed changes. Save any test evidence privately.
5. For browser login, install the browser extra and Chromium first. For Packet
   Tracer document analysis, install the corresponding optional extra; see
   [installation](../README.md#install).

Complete the read-only and cancellation checks before testing execution.
Perform writes one operation at a time in the disposable course. A failed
read, missing backup, unexpected recipient or unexplained grade is a failed
test; stop that workflow and record the operation and visible error.

## Configuration and authentication

| Check | Expected result |
| --- | --- |
| Use a private non-default `--config` with a distinct Canvas URL and workspace | CLI and TUI show the intended instance; exports and backups use that workspace |
| Launch `coursewright ta start` and `coursewright tui` with that config | Course selection, profile, model selection and design use the same settings |
| Change a profile or model setting in the TUI | The selected configuration is updated; an unrelated default config stays unchanged |
| Use the credential method you intend to keep: keyring, environment, token file or browser | Courses, discussion commands, assistant and design all authenticate consistently |
| Supply malformed configuration or an invalid token | Startup gives an error; it does not silently switch to defaults or show an empty successful course list |
| Deny a test account access to one resource | The failed read is visible and no dependent write runs |
| Browser login, then logout, when using SSO | Browser session is stored privately; logout removes it and a new session must authenticate again |
| Watch CLI activity and the TUI transmission log | Requests and outcomes are visible; tokens, student bodies and model prompts are absent |

## Teaching-assistant workflows

| Surface | Check |
| --- | --- |
| Course navigation | List, inspect, refresh, select and leave a course; confirm course name and ID before every operation |
| Discussions | List topics, display posts and summaries, calculate grades, export one discussion and a whole course |
| Discussion grades | Confirm an 80/100 rubric result becomes 40/50 and 160/200; repeat with a custom rubric total; inspect feedback and any role correction before submitting |
| Grade submission | Preview leaves Canvas unchanged; cancel does nothing; execute creates a private backup with the prior score and posts only selected grades |
| GradeLiner | Confirm the suggested publisher export, roster matches and assignment mappings; inspect unmatched rows; test preview, execute and a second run |
| zyBooks | Confirm Downloads selection, assignment matching, score conversion and selected learners before a single test submission |
| Zero ungraded | Excused and already graded submissions stay untouched; only the intended ungraded submissions are candidates |
| Bulk comments | Inspect rendered text and recipients; cancel once; then post one synthetic comment and inspect it in Canvas |
| Attendance | Inspect activity and recipient selection; preview the built-in and a custom template; execute once and verify the displayed sent count against Canvas Conversations |
| Packet Tracer | Import a synthetic submission, inspect classification and any extracted document text, then verify preview and an explicitly confirmed result |
| CyberInsight | Select a synthetic submission and inspect classification, feedback and any proposed grade before confirmation |
| Assistant and LLM | Choose a configured provider/model, draft a reply, verify save location and content; inspect usage tracking after one live generation |
| Daily digest | Read recent synthetic posts, role flags, deep links and JSON output; rerun after manually enriching the Markdown and verify the enrichment survives |

For imports, put two matching exports and one unrelated newer file in the
Downloads directory. The suggestion should be the newest matching export;
declining it must leave Canvas unchanged. Also try an explicit file in the
private workspace. Check that roster caches and exports stay there.

## Course design and local tools

| Surface | Check |
| --- | --- |
| Phase 1: diagnosis | Inspect course findings, gathered content and reports; Canvas stays unchanged |
| Phase 2: organization | Preview module and item order; confirm one change and compare with Canvas |
| Phase 3: discussions | Inspect title, body, points, module placement and the selected unit range before creating a test topic |
| Phase 4: dates | Inspect unlock and due dates against the private schedule, including timezone and daylight-saving boundaries |
| Phase 5: publishing | Inspect the exact modules/items and range; test one publish operation, announcement and course-copy preview where available |
| TUI preview binding | Preview, change the schedule contents or input path, then Apply: the stale preview must be rejected and require another preview |
| Remote changes after preview | Change a relevant module, assignment or discussion through Canvas, then Apply: review is required again |
| Standards audit | Run the bundled OSCQR audit, inspect evidence links and export; Canvas stays unchanged |
| Local conversion | Preview filename/caption conversion on copied files, execute, and inspect output and backups |
| Media workflows | Inspect configured Panopto access and caption handling with a permitted test video; inspect local Kahoot templates |

For every writing surface, check the target, preview, cancellation, successful
execution, prior-state backup and visible failure path. Do not interpret a
success banner alone as proof of a write: compare Canvas and the backup.

## Record the result

Record each row as pass, fail or not tested, with a short private reproduction
for failures. Include the exact command or screen and sanitized error text.
Keep credentials and student information out of the Kanban board.

Local smoke readiness and public release are separate decisions. The existing
Kanban privacy/history audit and owner publication gate remain in force; this
checklist does not authorize publishing or rewriting history.
