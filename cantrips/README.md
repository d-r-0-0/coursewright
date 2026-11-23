# Cantrips

Short scripts that keep every command line under 80 characters and
record intent.  Copy `_template.py` to start one.

- `verify.py` — the verification gate; stages live in `verify.toml`.
- `sync_agent_files.py` — check or sync agent instruction files against `AGENTS.md`.
- `lint_doc_budget.py` — enforce line budgets on agent instruction files.
- `audit_deps.py` — audit dependencies for known CVEs with `pip-audit`.
- `audit_workflows.py` — audit GitHub Actions workflows with `zizmor`.
- `scan_secrets.py` — scan committable files for secrets against `.secrets.baseline`.

Cantrips in this repo are generic.  A script written for one course or
one term belongs in your private workspace outside this repo, not here.
