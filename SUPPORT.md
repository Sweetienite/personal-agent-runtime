# Support

This is a solo-maintained open-source project. There is no SLA and no guaranteed response time, but
real reports are read.

## Where to ask

- **Usage question or bug**: open a GitHub issue using the templates.
- **Provider compatibility report**: use the compatibility template. Include the tool, version, OS,
  login type and exactly what you ran.
- **Security vulnerability**: do **not** open a public issue. Use GitHub Security Advisories on
  `Sweetienite/personal-agent-runtime` ("Report a vulnerability") or the contact in
  [.github/SECURITY.md](.github/SECURITY.md).

## What to include

- `par version --json` (and `par doctor --json` if relevant).
- Host OS, architecture, Python version, install method (`pip install -e .`, wheel, uv).
- The exact command or MCP tool call, expected and actual output.
- For a task: task id, `par task status`, `par task events`, and the relevant part of
  `par task result` / `par task diff`.
- Whether the data directory was isolated (`PAR_DATA_DIR`).

Redact credentials, tokens, personal paths and third-party project data. Do not paste an entire
ledger or a private repository.

## What is out of scope

- Vendor account suspensions, quota or terms decisions — contact the vendor.
- Bugs in Codex/Antigravity/Claude Desktop themselves.
- Requests to make `human_only` an OS-level identity boundary, to auto-apply patches, or to add a
  provider whose vendor terms forbid third-party wrapping.
- "Make it support every AI tool" — the compatibility matrix is intentionally evidence-based.

## Before reporting

1. Read [docs/en/TROUBLESHOOTING.md](docs/en/TROUBLESHOOTING.md).
2. Re-run with an isolated data directory to rule out existing config.
3. Check whether the same behaviour happens on the latest `main` (or the branch you installed).

If a path outside `allowed_paths` was written, a replaced coordinator made a decision, a check that
did not run was reported as passed, or your real project changed without `git apply`, treat it as a
security-relevant bug and report it privately.
