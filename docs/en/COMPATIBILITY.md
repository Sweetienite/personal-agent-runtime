# Compatibility and Evidence

This page is the human-readable form of [docs/product-capabilities.json](../product-capabilities.json).
The JSON is deliberately conservative; the tables below explain what each label means and which
dated evidence supports it.

**Product status:** `1.0.0`, untagged release candidate. Release status reported by
`par version`: `untagged; P3 gate partial (Codex automatic + manual hand-off)`.

## 1. Evidence levels

| Level | Meaning |
|---|---|
| `VERIFIED_AUTO` | A native adapter completed a real, dated run for the named version, OS and account path, including the required R1/R2 and cancellation/failure checks |
| `VERIFIED_COORDINATOR` | The named client called PAR's MCP tools in a real environment; read-only and write paths are distinguished |
| `VERIFIED_MANUAL` | The named client completed a dated export → external edit → import → Kernel verification |
| `GENERIC_MANUAL` | The protocol supports this class of client **if** it can open a workspace or exchange a patch; not a brand verification |
| `GENERIC_MCP` | The MCP interface is implemented; no per-brand real test |
| `GENERIC_TEXT` | Text-only participation, R0/R1 only |
| `EXPERIMENTAL` | Code exists, but a release cannot rely on it (terms risk, missing evidence, or both) |
| `RESEARCH_ONLY` | Design/study only; no implementation in this build |
| `NOT_TESTED` | No evidence; do not read as support |
| `NOT_SUPPORTED` | A known gap; the platform/route is not claimed |

A technical interface existing is never enough to earn `VERIFIED_*`.

## 2. AI tools (not the host OS)

| Tool / class | Native worker | MCP coordinator | Manual hand-off | Level | Version / OS / date | What was actually exercised |
|---|---|---|---|---|---|---|
| Codex CLI | Yes | Yes | — | `VERIFIED_AUTO` (worker); `VERIFIED_COORDINATOR` (read-only) | `codex-cli 0.166.0-alpha.6`, macOS 15.6.1 arm64, ChatGPT login, 2026-11-07 (worker); 2026-10-16 (coordinator) | Real R1/R2 with the default `read-only`/`workspace-write` sandbox; coordinator sample called `list_tasks` |
| Claude Desktop | No | Yes | — | `VERIFIED_COORDINATOR` | macOS 15.6.1 arm64, 2026-10-16 | Tool discovery and a real read-only `list_tasks`; submit/decision calls were scripted, not real |
| Antigravity (`agy`) | Code exists | No | — | `EXPERIMENTAL` | `agy 1.3.5`, macOS arm64, historical smoke 2026-10-15 | Adapter smoke only; disabled by default; vendor FAQ prohibits wrapping it in third-party coding agents under consumer plans |
| WorkBuddy | No | No | Yes | `VERIFIED_MANUAL` | WorkBuddy 5.9.3, macOS 15.6.1, 2026-11-07 | Real R2 export → GUI edit → import; Kernel check PASS, boundary clean, `REVIEW_REQUIRED`, nothing applied |
| TRAE | No | No | Conditional | `GENERIC_MANUAL` | — | Documented path; no dated export/import record |
| Cursor | No | No | Conditional | `GENERIC_MANUAL` | — | Documented path; no dated export/import record |
| Windsurf | No | No | Conditional | `GENERIC_MANUAL` | — | Documented path; no dated export/import record |
| Cline | No | No | Conditional | `GENERIC_MANUAL` | — | Documented path; no dated export/import record |
| Aider | No | No | Patch | `GENERIC_MANUAL` | — | Documented patch path; not verified |
| Other MCP clients | No | Protocol-compatible | — | `GENERIC_MCP` | — | The 21-tool interface is implemented and scripted-tested; no per-brand evidence |
| Web / mobile chat | No | No | Text | `GENERIC_TEXT` | — | R0/R1 text import only; cannot produce a verified R2 without a workspace or patch |
| ACP | No | No | — | `RESEARCH_ONLY` | — | No implementation |

Notes:

- **MCP connects coordinators, not workers.** A client that can call `submit_task` does not become
  an automatic code worker. Native execution requires an adapter manifest in this build.
- **One tool can hold both roles.** Codex can coordinate one task and execute another; the Kernel
  separates sessions, HOME and audit records.
- **Account and terms risk is yours.** Antigravity is the clearest example: technically present,
  contractually unsafe to present as a recommended automatic path.

## 3. Host operating systems

These are **host** facts, separate from AI-tool compatibility. A tool being compatible does not
imply any OS is supported.

| Host | Install | CLI core | Fake smoke | Real provider | Cancel/timeout | Sandbox |
|---|---|---|---|---|---|---|
| macOS 15.6.1 arm64 | Verified (`pip install -e .`, clean venv) | Verified | Verified (`smoke_codex.py --self-test`, `smoke_agy.py --self-test`) | Codex `VERIFIED_AUTO`; Antigravity `EXPERIMENTAL` | Verified with scripted workers | No OS sandbox; private clone only |
| Linux (`ubuntu-latest`, GitHub Actions) | `pip install -e .` in CI | `linux_security_core` (platform-independent security regressions) | Not run | Not supported (adapters are macOS-specific) | `linux_process_control` job | No OS sandbox |
| Windows WSL2 | `NOT_TESTED` | `NOT_TESTED` | Not run | Not run | `NOT_TESTED` | `NOT_TESTED` |
| Windows native | `NOT_SUPPORTED` | `STATIC_GAP` | Not run | `NOT_SUPPORTED` | `NOT_SUPPORTED` | `NOT_SUPPORTED` |

The Linux jobs are CI configuration until a run record exists; the release readiness ledger names
the exact run. Windows process-group, signal, file-mode, signing and path semantics are untested.

## 4. What PAR does **not** claim

- Every coding assistant is automatically supported.
- Native execution on all operating systems.
- MCP is a generic automatic-worker protocol.
- A private clone is an OS sandbox that stops reads or network access.
- `human_only` is unforgeable human identity.
- A worker's own "tests pass" statement is evidence.
- A patch is applied to your repository automatically.
- The `--to` label count equals the number of verified GUI clients.
- Historical reports under [docs/reports](../reports/) describe the current build. They are dated
  snapshots of the version tested then.

## 5. Re-checking a claim

```bash
par version --json
par doctor --json
par health --probe                 # offline provider facts; no model call
python3 -B tests/tools/provider_probe.py --versions
python3 -B tests/tools/codex_revalidate.py --check-only
```

The first two do not call a model. `codex_revalidate.py --check-only` runs offline checks only;
a real re-validation needs `--yes-use-quota` and an explicit decision because it spends subscription
quota. The `docs/reports/` pages record the exact commands and side-effect checks used for the
dated evidence above.
