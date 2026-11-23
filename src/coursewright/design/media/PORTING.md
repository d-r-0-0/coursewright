# Media port review notes

The Kahoot template writer and CSV validator retain the source validation
rules, template layout, formatting and summary text. CLI reporting becomes
returned validation data; an empty CSV raises instead of printing an error.

Panopto uses one client and one request choke point. OAuth client credentials,
password grants and explicit bearer tokens are strategies. All HTTP calls,
including retries, diagnostic probes and caption downloads, publish traffic
events. Events contain operation templates, status and duration; response
bodies, query values, identifiers and caption filenames are not published.

Credential resolution and atomic pair storage live in `core/auth/panopto.py`,
as required by the card. The vault namespace is Coursewright's. Both original
environment names and `COURSEWRIGHT_` names work, without mixing partial pairs.
The legacy server-only file is `panopto.key` in the working directory;
no credential file is bundled. User credentials never borrow server or Canvas
credentials. Importing media does not read the vault or contact a service.

URLs and folder IDs are caller configuration. Hosts and test identifiers are
synthetic. Session results are Pydantic v2 models with mapping access retained
for existing consumers; exact dictionary type identity is intentionally not
preserved. Token caching, OAuth fallback, continuation and page-number
pagination, retry timing and unsigned/authenticated caption downloads remain.
The API schema was consulted, not bundled.

Caption and session cantrip logic now returns data. Caption failures remain
per-record errors, with sanitized exception types instead of raw exceptions.
OAuth token-file output is explicitly requested and owner-only. No Canvas
client is created here: Canvas scanning accepts the core client from its caller.
That client port is a separate card; scanning is tested with its mocked boundary.

All five named source test files have counterparts in `tests/design`:

- `test_panopto_api.py`: all seven URL and filename regressions, sanitized.
- `test_panopto_auth.py`: login/status cases use silent library results;
  CLI identity dispatch becomes library identity dispatch.
- `test_panopto_consumers.py`: import safety, identity selection, redaction,
  OAuth payload, password grant and endpoint/folder read cases. Environment
  or explicit arguments replace prompts. Browser callback creation/cleanup is
  omitted: private course scripts must not be copied under the port contract.
- `test_panopto_credentials.py`: every atomic-resolution/storage case, plus
  Coursewright environment names and cross-namespace partial-pair rejection.
- `test_kahoot_template.py`: all template cases plus CSV validation coverage.

Additional client/workflow tests exercise HTTP behavior, traffic events,
caption discovery, deduplication and manifest output. All network and vault
operations in tests are mocked. Live service access remains unverified.

CLI commands, skill wrappers and interactive browser OAuth are left for UI
integration: this card targets the library and the port protocol restricts
unrelated package edits. The handoff cantrip is required by the 80-character
command rule and performs no board action until explicitly invoked.
