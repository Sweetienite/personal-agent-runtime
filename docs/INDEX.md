# Documentation Index

## Start here

| Page | Audience |
|---|---|
| [README](../README.md) | First look: positioning, routes, demo, limits |
| [docs/en/GETTING_STARTED.md](en/GETTING_STARTED.md) | First install, zero-account demo, manual hand-off, first Codex task, cleanup |
| [docs/en/TROUBLESHOOTING.md](en/TROUBLESHOOTING.md) | Something failed and you need the next action |

## Understand the product

| Page | Contents |
|---|---|
| [docs/en/ARCHITECTURE.md](en/ARCHITECTURE.md) | Components, one-attempt sequence, states, boundaries, extension points |
| [docs/en/INTEGRATIONS.md](en/INTEGRATIONS.md) | Native worker / MCP coordinator / manual hand-off, per-tool workflows |
| [docs/en/COMPATIBILITY.md](en/COMPATIBILITY.md) | AI-tool and host-OS matrices, evidence levels |
| [docs/en/SECURITY.md](en/SECURITY.md) | Threat model, approvals, lease, check execution, privacy |
| [docs/en/USER_GUIDE.md](en/USER_GUIDE.md) | Daily flow, results, candidates, retries, recovery |

## Operate and extend

| Page | Contents |
|---|---|
| [OPERATIONS.md](OPERATIONS.md) | Chinese command and exit-code reference |
| [KERNEL.md](KERNEL.md) | Chinese state machine, contract and ledger reference |
| [MCP.md](MCP.md) | MCP bridge, 21 tools, lease and action policy |
| [HANDOFF.md](HANDOFF.md) | Manual relay, hand-off packets, takeover |
| [COORDINATORS.md](COORDINATORS.md) | Coordinator setup, grants, switching |
| [CODEX.md](CODEX.md) · [AGY.md](AGY.md) · [COPILOT.md](COPILOT.md) · [ADAPTERS.md](ADAPTERS.md) | Adapter details, versions, evidence |
| [CONTEXT.md](CONTEXT.md) | Generic vs Harness project context |
| [ROLLBACK.md](ROLLBACK.md) | Non-destructive rollback and restore drills |
| [specs/COMMON_RULES.md](specs/COMMON_RULES.md) · [specs/MCP_BRIDGE_SPEC.md](specs/MCP_BRIDGE_SPEC.md) | Exact module contracts |

## Project and release

| Page | Contents |
|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Development, tests, mutation checks, PR rules |
| [SUPPORT.md](../SUPPORT.md) | Where to ask and what to include |
| [.github/SECURITY.md](../.github/SECURITY.md) | Private vulnerability reporting |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | Community expectations |
| [CHANGELOG.md](../CHANGELOG.md) | User-facing changes |
| [RELEASE_NOTES_2026-11.md](RELEASE_NOTES_2026-11.md) | Release review and known limits |
| [reports/](reports/) | Dated historical evidence; not current support claims |
| [product-capabilities.json](product-capabilities.json) | Machine-readable capability facts |

## Chinese reference

The detailed Chinese set starts at [README.zh-CN.md](../README.zh-CN.md) and
[docs/INDEX.zh-CN.md](INDEX.zh-CN.md). Core facts in both languages are checked against
`product-capabilities.json` by `tests/tools/docs_contract.py`.
