# CLAUDE.md — Instructions for Claude Code Agent

Read `AGENTS.md` first: it holds the project overview, layout and the
hard rules, and applies to every agent.  Then read
`docs/MODULE_MAP.md` for what has been ported and what has not.

- The gate (before claiming done): `python cantrips/verify.py`
- One stage: `python cantrips/verify.py --only lint`
- Setup: `python -m venv .venv`, then `pip install -e ".[dev]"`
