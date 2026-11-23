# Testing

Run commands from the repository virtual environment. While changing a small
area, run the nearest relevant test file first:

```bash
.venv/bin/python -m pytest tests/test_docs_commands.py
```

Before a handoff, run the complete verification gate:

```bash
.venv/bin/python cantrips/verify.py
```

The gate stages are declared in `verify.toml`. It checks formatting and lint,
tests, layering, documentation drift, and project hygiene. Do not replace it
with a partial test run when claiming a change is complete.

Tests use synthetic data and fake Canvas clients. They must never call a real
course, require a token, or include student identifiers. Tests for a Canvas
write should prove the dry-run path, the explicit execution path, and backup
handling separately.

The documentation-command regression test invokes `--help` on each documented
command path and option. Help must exit successfully without a network call;
this keeps the published command examples coupled to the real Typer tree.

After the gate passes, follow the [human smoke test](smoke-test.md) against a
disposable Canvas course. Automated tests use synthetic services and cannot
confirm institutional SSO, real permission scopes or actual instructor files.
