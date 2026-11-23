# Skills

One folder per capability, each with a `SKILL.md` an agent can load.
A skill describes when to use the capability and which
`coursewright` command or library function to call; it holds no logic
of its own (AGENTS.md rule 3.7).

| Skill | Use it to |
| --- | --- |
| `discussion-review` | read, rank, reply to and grade a discussion |
| `daily-digest` | digest new posts across every course and flag who needs a reply |
| `grade-import` | bring NetAcad, MindTap, zyBooks or Packet Tracer results into Canvas |
| `honor-credit` | give full credit to submitted honor-system (completion-graded) work |
| `zero-ungraded` | preview and safely zero past-due unsubmitted assignments |
| `weekly-grading` | orchestrate preflight, discussions, imports, Packet Tracer, honor credit and zeros |
| `attendance-check` | find inactive students and send warnings |
| `course-diagnose` | health-check a course shell (Phase 1, read-only) |
| `apply-dates` | apply a schedule's unlock and due dates (Phase 4) |
| `publish-content` | publish modules and items (Phase 5) |
| `rubric-audit` | audit a course against a quality rubric (read-only) |
| `five-phase-build` | run the whole build in order |
| `unit-pipeline` | turn a recorded lecture into transcript and quiz bank |

Every write-capable skill shows the dry run before `--execute`.
`tests/test_skills.py` checks each skill's front matter, that every
command, option and library name it cites is real, that it names no
course, term or institution, and that previews come before writes.
