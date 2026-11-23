---
name: unit-pipeline
description: >
  Turn one recorded lecture into unit materials: fetch the lecture's
  captions as a readable transcript, build a quiz-game question bank
  from what was actually said, and tidy the unit's files.  Use when the
  instructor shares a new lecture recording or viewer link, asks for a
  transcript or Kahoot, or says "do the same for the next unit".
---

# Unit pipeline

Most weeks a lecture is recorded and the same chores follow.  Each
stage below is a library function or a `coursewright` command.

## The one thing you need

**The recording's viewer link.**  The session id is its `id=`
parameter.  If the instructor says "repeat that" without a link, ask
for it.  Never guess a session id or assume the next unit number.

## Stages

### 1. Transcript

Call `coursewright.design.conversion.fetch_session_captions` with the
video service's base address, the unit's materials folder (in the
private workspace) and the session id.  It saves the captions and a
reflowed transcript.

If caption files are already on disk, convert them instead.  Preview:

```
coursewright design convert captions <materials-folder>
```

then, once the instructor agrees:

```
coursewright design convert captions <materials-folder> --execute
```

Pass `--viewer-url-prefix` (the viewer address up to and including
`id=`) to link each transcript back to its recording.

If the service answers with a login page, the recording is probably
not shared with the course yet.  Tell the instructor; do not work
around it.  Transcripts are machine captions: treat them as a record
of topics and their order, not exact quotes.

### 2. Quiz bank

Build `coursewright.design.media.QuizQuestion` items from the
transcript, check them with `coursewright.design.media.validate_bank`,
and save with `coursewright.design.media.write_bank`.  For a bank
someone else exported, use
`coursewright.design.media.validate_kahoot_csv`.

Craft, not code:

- Draw wrong answers from mistakes students really make.
- Rotate the correct answer across slots.
- Prefer reading code or predicting output over vocabulary recall.
- Write every question to stand alone: never name the instructor or
  refer to "the lecture" or "the demo".
- Respect `QUESTION_MAX_CHARS` and `ANSWER_MAX_CHARS`; the validator
  reports any row that breaks them.

### 3. File names and slides

Normalise new files to kebab-case so student downloads are tidy.
Preview:

```
coursewright design convert names <folder>
```

then, once the instructor agrees:

```
coursewright design convert names <folder> --execute
```

`coursewright.design.conversion.build_validation_report` checks that
converted slide decks and workbooks match their sources.

### 4. Into the course

Placing the recording, slides and examples on Canvas pages has no
general command yet.  Say so and stop; do not hand-edit page HTML.
Dates and publishing for the unit follow the `apply-dates` and
`publish-content` skills.

## Rules

- **Preview, then execute.**  Every command here is a dry run until
  `--execute`; read it with the instructor first.
- Course materials and transcripts stay in the private workspace,
  never in this repository.
- Re-running is the fix for a stage that failed halfway.
