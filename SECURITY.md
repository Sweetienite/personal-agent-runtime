# Security Policy

The full policy and reporting channel live in
[.github/SECURITY.md](.github/SECURITY.md). In short:

- Report vulnerabilities privately through GitHub Security Advisories ("Report a vulnerability")
  on `Sweetienite/personal-agent-runtime`, not in a public issue.
- In scope: a worker escaping its private clone, a path-policy or redaction bypass, a write outside
  the data directory, a replaced coordinator making a decision, a check that did not run reported as
  passed, or a real project changing without `git apply`.
- Out of scope: vendor CLI bugs, account/terms decisions, and the security of your own project code.
- Include the PAR version, OS, Python, exact command/MCP call, expected vs actual, and a minimal
  reproduction. Never include real credentials or private project data.

`human_only` is a PAR-protocol restriction, not OS-level identity authentication. See
[docs/en/SECURITY.md](docs/en/SECURITY.md) and [docs/SECURITY.md](docs/SECURITY.md).
