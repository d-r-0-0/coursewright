# Phase 5 publishing port

This package is the library face of publishing. Callers supply the shared
`CanvasClient` and a backup directory in the private instructor workspace.
Use `core.canvas.safe_write.default_backup_directory` with the configured
workspace to locate backups. Announcement writes accept a `SafeCanvasWriter`;
the other workflows take keyword-only `execute=False`. No workflow prints,
prompts, resolves credentials, or creates its own HTTP connection.

| Source in COURSE_BUILDER | Library entry points |
| --- | --- |
| `scripts/publishing/announcements.py` | `announcements`: HTML conversion, schedule parsing, settings, announcement creation/update, saved IDs |
| `scripts/publishing/publish_content.py` | `content`: publishing plan, all/selected modules, progressive release, checklist |
| `cantrips/copy_course.py` | `copy_course.copy_course_content` |
| `cantrips/check_migration.py` | `copy_course.get_migration_status`, `list_content_migrations` |
| `cantrips/publish_syllabus.py` | `syllabus`: rendering, publishing, verification |
| `cantrips/list_announcements.py` | `announcements.list_announcements` |
| `cantrips/outline_announcements.py` | `announcements.outline_announcements` |
| Three deletion cantrips | `deletions.delete_announcements`, `delete_module`, `delete_assignment` |

## Deliberate changes

- Printed summaries and exit statuses become reports and exceptions. Every
  deletion report names the proposed targets and the successfully deleted
  targets separately. `PartialDeletionError.report` preserves progress when
  a later deletion or verification fails.
- All writes use `SafeCanvasWriter`: previews are the default, executed
  writes have timestamped JSON backups, and a rejected write raises. A
  publishing report retains individual write failures as the source did;
  backup failures abort. Whole-set announcement and module/item snapshots
  precede deletion, in addition to the writer's per-object backups.
- The source's strict announcement pagination is shared by safety-sensitive
  collection reads. The shared client's ordinary list methods can return a
  partial list on failure. Until core offers a strict mode, these reads use
  its `_make_request` choke point, preserving auth, timeout and traffic
  events. Failed reads cannot authorize deletion or copying into a shell
  that only appears empty; failed readback cannot certify deletion.
- Course facts, template paths, ID storage directories and allowed default
  module names are supplied by the caller instead of embedded in code.
  Announcement settings load from `[courses.CODE]` in a private TOML file.
  Syllabus facts retain the source's `[term]` / `[courses.CODE]` merge.
- Announcement writes return `WriteOutcome` instead of success dictionaries;
  dates are validated by the shared writer. Syllabus readback accepts a
  verification string; by default it checks the rendered HTML. Course copy
  returns a report including the migration ID and collected progress, with
  injectable sleep for offline testing.
- Publishing all modules uses `module_names=None`; selected and progressive
  publishing use a collection of names. Preview counts describe the actual
  proposed changes, whereas the source displayed zero executed changes.

## Source test coverage

All four requested source files have counterparts under
`tests/design/publishing/`:

- `test_copy_course.py` → `test_publishing_copy_course.py`: all three source
  safety cases plus migration failure, polling and configuration cases.
- `test_publish_syllabus.py` → `test_publishing_syllabus.py`: all five source
  rendering cases, with a synthetic template and TOML fixture, plus writes.
- `test_delete_announcements.py` → `test_publishing_delete_announcements.py`:
  dry run, backup order, pagination, backup failure, first-delete failure,
  failed reads (including no response), empty course and historical live
  flag rejection. The flag test becomes rejection of `live=True` and of a
  positional execution argument; this package has no argument parser.
- `test_delete_module.py` → `test_publishing_delete_module.py`: both graded
  item tests, plus deletion safety and assignment deletion coverage.

Additional tests cover announcements, content publishing and incomplete
reads. HTTP is simulated behind the real Canvas client and safe writer;
course-copy unit tests also retain the source's small fake client. No live
Canvas operations were performed.

## Outside this port

Per `docs/PORTING.md`, changes stay in this package and its tests. CLI
commands, traffic rendering and skill wrappers belong to the later UI and
skills work listed in `docs/MODULE_MAP.md`. Interactive course selection
remains UI work; the core client already supports course search.

The source announcement command was a framework stub redirecting to private,
course-specific scripts. Those scripts, real templates, schedules, course
facts and instructional materials are deliberately excluded. The generic
workflow described in the source announcement documentation is retained:
render local Markdown, preview, create with a scheduled timestamp, save IDs,
then update bodies without changing the schedule. After a course copy,
callers must review announcement schedules and date wording for the new term;
this port does not infer replacement dates or rewrite course materials.
