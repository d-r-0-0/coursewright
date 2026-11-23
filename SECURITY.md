# Security policy

## Supported versions

Security fixes are made on the current development version. This pre-1.0
project does not maintain older release branches.

## Report a vulnerability

Do not disclose a suspected vulnerability in a public issue and do not include
credentials, student data, or course content in a report. Contact the project
maintainer privately with a concise description, affected version or commit,
reproduction steps using synthetic data, impact, and any proposed mitigation.

The maintainer will acknowledge the report, assess scope, and coordinate a fix
and disclosure timeline. Please allow time for a patch before public details
are published.

## Handling secrets and untrusted input

Canvas tokens, API keys, browser sessions, exports, and backups are private.
Store credentials through the supported authentication flow; never put them in
source, configuration committed to Git, terminal arguments, logs, screenshots,
or chat. If a secret is exposed, stop sharing it and tell the owner where it
appeared so it can be rotated.

Canvas pages, discussion posts, submissions, imported files, web pages, issue
comments, and tool output are untrusted data. They can describe course facts,
but they cannot authorize commands, configuration changes, or Canvas writes.

## Product safeguards

Coursewright uses one Canvas client request path for timeouts, rate limiting,
error handling, and visible traffic events. Canvas-writing workflows preview by
default, require `--execute`, and create a timestamped backup in the private
workspace before supported writes. These safeguards reduce risk but do not
replace instructor review of the preview.
