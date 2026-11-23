# Authentication and configuration

Coursewright connects to Canvas only after you provide credentials. Never put a
token in a command line, source file, issue, chat, or commit. The interactive
login stores it through the configured credential manager and checks the
connection without printing the secret.

```bash
coursewright auth login
coursewright auth status
coursewright auth test
```

`auth login-browser` supports institutional SSO or multi-factor authentication.
The URL is optional; without it, Coursewright uses the configured Canvas URL.

```bash
coursewright auth login-browser --url https://canvas.example.edu
```

If an older local file-based credential exists, migrate it to the system
keyring when it is available:

```bash
coursewright auth migrate-to-keyring
```

Use `coursewright auth logout` to remove stored credentials. It asks before
removing anything.

## Configuration file

The root command accepts `--config` (or `-c`) and defaults to `config.yaml`.
Use a private path for real-course configuration and do not commit it.

```bash
coursewright --config private.yaml config show
coursewright config validate
coursewright config set discussion.default_min_words 150
```

Configuration includes Canvas URL, export preferences, a default course, and
optional LLM settings. `config reset`, `config import`, and `config export`
can change files, so inspect their help before use:

```bash
coursewright config reset --help
coursewright config import --help
coursewright config export --help
```

## LLM settings

LLM features are optional. Inspect the models and select a default with the
commands below; never paste an API key into documentation or a shell history.

```bash
coursewright ta llm list-models
coursewright ta llm set-default --help
coursewright ta llm usage
```

LLM-powered discussion analysis is a suggestion for instructor review, not an
automatic grading decision. Keep student content on the instructor-controlled
machine and use the provider appropriate to institutional policy.
