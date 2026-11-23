# Porting protocol

How a module moves from discussorator or COURSE_BUILDER into
Coursewright.  Every port card on the board follows this; the card
body only adds the module's specifics.

## Sources (read-only)

- `../discussorator`
- `../COURSE_BUILDER`

Never edit, commit to, or push from these.  Never copy anything from
`materials/`, `schedules/`, `sessions/` or a course-specific cantrip.

## Implementer

1. Work only in the card's worktree and branch.  Link the shared
   environment once: `ln -s ~/merge-src/coursewright/.venv .venv`
2. Read `AGENTS.md`, `docs/HOUSE_STYLE.md` and the card.
3. Port the code **and its tests** into the target package.  A port
   is a refactor (AGENTS.md 3.5): same behaviour, new location.
   Allowed changes: import paths, the layering fixes below, synthetic
   data, lint fixes.
4. Layering fixes that every port makes:
   - library code (`core`, `ta`, `design`) does not print, prompt or
     import Rich/Typer/Textual; move that to `ui/` or return data;
   - no imports from `ui/`; traffic events go to `core/traffic`;
   - Canvas access only through `coursewright.core.canvas`.
5. Replace every real name, email address, Canvas id, course code and
   institution-specific value in code, tests and fixtures with
   synthetic data or configuration.
6. Do **not** edit `docs/MODULE_MAP.md`, `pyproject.toml` dependency
   lists or files outside the card's target packages unless the card
   says so.  All known dependencies are already declared.
7. Run `python cantrips/verify.py` until green (max 5 iterations).
8. Commit in small steps (< 300 lines where the source allows) and
   hand off with `hermes kanban --board coursewright request-review
   <card>`, giving in `--summary`: what was ported, what was
   deliberately changed and why, gate output, and anything left out.

## Reviewer

A different vendor's model from the implementer (see Lanes below).
Approve only if all hold:

1. `python cantrips/verify.py` is green **in the worktree, run by
   you**, after `git rebase main`.
2. Behaviour is preserved: compare against the source files named on
   the card; every source test has a ported counterpart or a stated
   reason.
3. Layering and house style hold (AGENTS.md 3.6-3.9, HOUSE_STYLE.md).
4. Hygiene: no student data, real addresses, secrets, course
   materials, institution-specific code or Quality Matters text.
5. New code is exercised by tests, not just imported.

If anything fails: `hermes kanban --board coursewright reopen-review
<card> --reason "..."` with a numbered list of required changes, each
naming a file.  Do not fix it yourself.

If everything holds: merge with
`git -C ~/merge-src/coursewright merge --ff-only <branch>`, then
`hermes kanban --board coursewright complete <card> --summary "..."`
with the merged commit id.  Fast-forward only: main
must be exactly the tree the gate ran on.  If the fast-forward is
refused, rebase and run the gate again.

## Lanes

The Hermes kanban board `coursewright` is the system of record.  Cards
are worked by command-line agents, not Hermes profiles:

| Lane | Agent | Moves a card |
| --- | --- | --- |
| build | Claude Code, Fable 5.2 | ready -> review |
| review | Antigravity, Gemini | review -> done, or back to ready |
| audit | Antigravity, Gemini | ready -> done, creating fix cards |

## Stop conditions

Block the card (`hermes kanban --board coursewright block <card>
"reason"`) and say why when: the same error
occurs twice; the source does something the card did not anticipate
and porting it needs a design decision; or hygiene cannot be met
without removing behaviour.  Never push to any remote.
