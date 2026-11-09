# Security Policy

This is a solo-maintained personal project (pre-1.0 maturity in practice, even though the version string says `1.0.0`), used by its author on their own machine. There is no SLA, but real reports are read and taken seriously.

## Reporting a vulnerability

Please use [GitHub Security Advisories](../../security/advisories/new) for this repository ("Report a vulnerability") rather than a public issue, so there's time to fix it before details are public. The advisory thread is the private reporting channel; do not include credentials, personal paths or third-party project data in the report.

## What's in scope

The Runtime's actual safety boundary (what it is and isn't allowed to write or execute, how it isolates a Worker's attempt from your real project) is documented in detail in [`docs/SECURITY.md`](../docs/SECURITY.md) (Chinese). Reports about that boundary being bypassable — a Worker reaching outside its private clone, a path-policy or redaction gap, a write landing outside the Runtime's own data directory — are exactly what this policy is for.

Reports about the vendor CLIs it drives (Codex, Antigravity) or about your own project's code are out of scope here — take those to the relevant upstream project.
