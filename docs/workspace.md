# Private workspace

`COURSEWRIGHT_WORKSPACE` points to an instructor-controlled directory outside
this repository. It is the boundary between reusable software and course work
that may contain student information, copyrighted material, or local policy.

Set it before any Canvas write that needs a backup:

```bash
export COURSEWRIGHT_WORKSPACE="$HOME/coursewright-work"
```

On Windows, set the same environment variable through PowerShell or the system
environment settings. Coursewright reports a clear error when a workflow needs
a workspace and none is configured.

## Keep these files there

- Course materials, including slides, captions, transcripts, and handouts.
- Term schedules and schedule source files.
- Canvas backups created before writes and exports containing course or student
  information.
- Course-specific scripts and local configuration that is not generally useful.
- An optional licensed `rubrics/qm-he-7.toml` override, when the institution is
  authorized to keep it. Do not copy its text into this repository.

The repository's ignored `workspace/` directory is only a safety net, not a
replacement for this boundary. Do not commit anything from the workspace.

## Downloads-first imports

When an import needs an instructor-exported file, Coursewright searches the
Downloads directory first, suggests the newest matching filename, and asks for
confirmation. It then falls back to the workspace import directory or a path
you explicitly provide. A suggestion is never an automatic import.

For example, keep a confirmed grade export in the workspace before a preview:

```bash
coursewright ta gradeliner --netacad grades.csv --course 1001
```

The preview does not write grades. Add `--execute` only after reviewing every
planned change.
