# Changelog

All notable changes to Coursewright are recorded here. The project follows
Keep a Changelog categories and uses semantic-versioning intent while it is
pre-1.0.

## [Unreleased]

### Added

- Unified public documentation for installation, authentication, private
  workspaces, workflows, testing, security, and contribution.
- A CLI documentation regression test covering documented command paths and
  options.
- Daily discussion digest (`coursewright ta discussions digest`, library
  `ta/discussions/daily_digest.py`, `daily-digest` skill), ported from
  discussorator with configurable instructor matching and private-workspace
  output.
- Honor-system credit (`coursewright ta honor-credit`, library
  `ta/gradeliner/honor_credit.py`, `honor-credit` skill): full points for
  submitted completion-graded work, including late resubmissions, with a dry
  run first and a backup before every write.
- `coursewright ta zybooks-import --labs-required N`: how many of a unit's
  best labs count toward the lab grade. The default stays five; each unit
  mapping remembers its own value.

### Fixed

- NetAcad import no longer turns blank export cells into zeros. A blank cell
  means the student has not attempted the item, so exams that were not due
  yet were being zeroed; only recorded scores (including an explicit 0) are
  imported now.

## [0.1.0] - 2026-11-16

### Added

- Initial unified Coursewright release: Canvas client, teaching-assistant
  workflows, five-phase course design, Rich CLI, Textual application, and
  agent skills.
