# User guide

Coursewright has two entry points: `ta` for a running course and `design` for
building a course shell. Every Canvas request is shown as activity in the CLI
or Textual app. Every write starts as a preview.

## Run a live course

Open the Textual application with `coursewright tui`, or let the TA startup
route validate Canvas first:

```bash
coursewright ta start
```

From the course list, choose a course with its row or keys `1` through `9`.
The available shortcuts are `d` discussions, `g` GradeLiner, `y` zyBooks, `b`
bulk commenter, `p` Packet Tracer, `a` attendance, `z` zero ungraded, `w`
course design, `m` model, `r` refresh, and `q` quit. `Escape` returns from a
screen. The footer always shows shortcuts for the current screen.

The command line is useful for focused, repeatable work:

```bash
coursewright ta courses list
coursewright ta courses info 1001
coursewright ta discussions list 1001
coursewright ta discussions summary 2001 --course 1001
coursewright ta discussions posts 2001 --course 1001
```

Use `grade` to calculate discussion results. It reads Canvas but does not post
grades. `submit-grades` previews a grade submission until `--execute` is
present:

```bash
coursewright ta discussions grade 2001 --course 1001
coursewright ta discussions submit-grades 2001 --course 1001
coursewright ta discussions submit-grades 2001 --course 1001 --execute
```

Keep exports in the private workspace because they can include student data:

```bash
coursewright ta discussions export-course 1001 \
  --output "$COURSEWRIGHT_WORKSPACE/exports"
coursewright ta analyze-course "$COURSEWRIGHT_WORKSPACE/exports"
```

Publisher grades come in through imports. A NetAcad cell left blank means
"not attempted yet" and is never imported as a zero. A zyBooks lab grade
averages each student's best five labs unless `--labs-required` says another
number:

```bash
coursewright ta netacad-import --netacad export.csv --course 1001
coursewright ta zybooks-import --course 1001 --confirm-distinct-labs
coursewright ta zybooks-import --course 1001 --confirm-distinct-labs \
  --labs-required 10
```

Some assignments are graded on completion alone (the honor system): anything
a student submitted earns full points. Name them by shared text or by ID:

```bash
coursewright ta honor-credit --course 1001 --match "Module Quiz" --points 50
coursewright ta honor-credit --course 1001 --match "Module Quiz" --points 50 \
  --execute
```

For an attendance warning, select the intended recipients in the prompted
preview. No Canvas message is sent without `--execute` and confirmation:

```bash
coursewright ta attendance-warning --course-id 1001 --inactive-days 14
```

## Design a course

The five phases work in order. Each writing phase shows its full preview,
requires `--execute`, and stores a backup in `COURSEWRIGHT_WORKSPACE`.

```bash
coursewright design diagnose run 1001
coursewright design organize unit-order 1001
coursewright design discussions create 1001 --last-unit 13
coursewright design dates apply 1001 schedule.tsv
coursewright design publish content 1001 --first 3
```

After reviewing a write preview, repeat only the applicable command with
`--execute`. Do not use `--execute` as a shortcut around review.

The Textual course-design screen opens with `w` from a selected course. Press
`1` through `5` to select a phase, `n` or `b` to move between phases, `v` to
preview, `a` to apply after confirmation, and `u` to open the rubric audit.

Run a quality audit at any time. The bundled OSCQR rubric includes attribution;
rubrics held by reference link to their source instead of redistributing text.

```bash
coursewright design rubrics
coursewright design rubric oscqr
coursewright design audit 1001
```

## Local conversion

These commands work on local private-workspace files. They also preview by
default:

```bash
coursewright design convert names materials
coursewright design convert captions materials
coursewright design convert captions materials --viewer-url-prefix 'https://video.example/?id='
```

## Get command help

Every group and command has built-in help. It is the source of truth for
available options in the installed version:

```bash
coursewright ta discussions --help
coursewright design dates apply --help
coursewright config --help
```

`coursewright ta setup` is an interactive first-time setup wizard. Prefer
`coursewright auth login` for credential setup; see [Authentication](auth.md).
