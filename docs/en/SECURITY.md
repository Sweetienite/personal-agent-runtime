# Security Model

This page describes what PAR itself protects, what it explicitly cannot protect, and what remains
your decision. It is the English companion to [docs/SECURITY.md](../SECURITY.md), which contains
the exhaustive test-by-test matrix; when the two disagree, the code and the Chinese matrix are the
source of truth and this page is the summary.

## 1. One paragraph

The Runtime writes only its own data directory. A real project starts read-only; a person must run
`par project enable-write` (after readiness checks and typing the project id again). Workers run in
a private per-attempt clone and never see the real project as their working directory. Every write
permission, worker override, base-drift confirmation and permission retry is a Kernel approval.
MCP decision tools require a reason; the CLI's optional `--note` may be empty. The Kernel runs the
acceptance checks itself. No code path applies a patch to
your repository, pushes, or deploys. The only external programs the Runtime starts are git (in its
own directories, with a clean environment), the configured vendor CLI, the Kernel's check commands,
and a few offline Codex probe subcommands plus `codesign`.

## 2. Protected by the Runtime (with regression tests)

| Property | Mechanism |
|---|---|
| Writes stay inside the contract's `allowed_paths` | `normalise_allowed_path`, `path_allowed`, `classify` |
| `.git/`, `.ai/`, `AGENTS.md`, `CLAUDE.md`, credential-shaped paths are never writable | `forbidden_reason`, `is_sensitive_path` |
| Symlinks, `..`, sibling-prefix paths and protected roots cannot escape | `WriteGuard` resolves real paths and compares NFC + casefold |
| Read-only tasks cannot change anything | `classify(writes=False)`; symlinks/special files never count as work |
| Check commands are argv (no shell), preset executables, fixed PATH, no secrets | Contract allowlist + `resolve_executable` + `check_env` |
| Contracts with secrets, control characters, duplicate keys or unknown fields are refused | `parse_contract`, config validation |
| Worker text is never verified | `evidence_summary.claim.verified = false`; every bridge answer labels it untrusted |
| Unknown vendor events or exit 0 without a terminal event cannot become success | Adapter parsers + `classify_failure` |
| Approvals are per item and never reused across a worker change | Kernel approval gates + mutation tests |
| A broken config can inspect, never write, the ledger | SQLite `mode=ro`, `Store.tx` refusal, four-tool MCP allowlist, no heartbeat |
| `ACCEPTED` is not `APPLIED`; patches are never applied automatically | Only `work.patch` is stored |
| Append-only ledger, immutable ended attempts, private modes | SQLite triggers + `WriteGuard` |

## 3. Not guaranteed

- **No OS sandbox.** A private clone limits where the worker may write, not what it may read.
  Codex's sandbox restricts writes and network but not reads. Antigravity's isolation is a private
  HOME plus a generated permission profile plus the Kernel's file comparison. Check commands run
  with your user identity and may read other files, spawn processes or use the network.
- **Checks execute code from the patch.** That is what running a repository's tests means. Only run
  checks you reviewed, on repositories you trust, or put the whole Runtime in a container/VM.
- **Redaction is best-effort**, not a guarantee that every secret shape is recognised. The primary
  rule is not to read secrets.
- **A private HOME is not "zero persistence".** Vendor CLIs may still write their own diagnostics or
  refresh tokens.
- **Recovery depends on heartbeats and clocks.** Sleeping across the threshold can mark a live
  attempt `RECOVERY_REQUIRED`; it never starts a second writer.
- **A worker that calls `setsid` escapes the process group** and cannot be tracked or killed.
- **Account and terms compliance is yours.** Technical isolation does not make a vendor-prohibited
  usage permitted.
- **Copilot is text-only and has no signature/sandbox claim.** Non-interactive Copilot requires
  `--allow-all-tools`; the adapter pairs it with `--available-tools=par-no-tools` so no real tool is
  available, and grants no paths, URLs or folder trust. The npm launcher is a Node script with no
  vendor code signature, and the CLI may still load user-level Copilot configuration. The Worker
  keeps the attempt's private HOME; `prepare()` links `Library/Keychains` there to the real keychain
  (link only, never a copy) and `cleanup()` removes it, so the login made with the normal HOME is
  found. Log in once with
  `COPILOT_HOME="$HOME/.copilot" copilot --no-auto-update login`. It cannot write files or run R2;
  see [COPILOT.md](../COPILOT.md).

## 4. Approvals, approval modes and the coordinator lease

`approval_mode` is a profile setting:

| Mode | Coordinator may decide | Notes |
|---|---|---|
| `coordinator_autonomous` (default) | Yes, inside the current lease | Every decision records mode, coordinator, approval kind and grant id |
| `coordinator_scoped` | Only inside a person-created grant | Grant scope: actions, approval kinds, project, risk tiers, expiry; revocable |
| `human_only` | No | All decisions at the CLI |

**The lease comes before the mode.** A decision requires a known coordinator that holds the current
lease, even in `coordinator_autonomous`. After `par coordinator use codex`, a bridge started for
`claude_desktop` gets `not_the_coordinator` for approve/reject/accept/close, submit, run, retry,
resume, create-candidate and rebase. Viewing, pausing and cancelling remain allowed. The Kernel
re-checks the lease, mode and grant inside the same write transaction, so a revocation or switch
cannot land between the check and the record.

Grants are append-only ledger records, created and revoked only from the CLI. The MCP bridge has no
tool to create or revoke one. An unknown coordinator id is refused.

### Side-effect action matrix

| Action | Coordinator via MCP | Executes repo code | Rule |
|---|---|---|---|
| `submit_task` | Yes | No | Current lease; a write task waits for approval |
| `run_task` | Yes | Yes (starts a worker) | Current lease; `QUEUED` and no pending approval |
| `retry_task` | Yes | Yes (new attempt) | Current lease; never bypasses cooldown or permission |
| `resume_task` | Yes | No (re-queues) | Current lease |
| `pause_task` / `cancel_task` | Yes | No | A replaced coordinator may still stop work |
| `recover_task` | Yes | No | `terminate=true` kills a verified process group and needs the current lease |
| `create_candidate` | Yes | **Yes** (re-runs `acceptance.checks`) | Current lease; local user permissions; actor recorded |
| `rebase_task` | Yes | No | Current lease; never-run `QUEUED`/`PAUSED` task only |
| `approve` / `reject` / `accept` / `close` | Yes | No | Lease + mode + grant; re-checked in the transaction |
| Health/config/grant creation or revocation | **No** | — | CLI only; the bridge has no such tools |

## 5. `human_only` is a protocol rule, not identity authentication

`by="user"` and the CLI are actor labels inside PAR. Any process with the same OS user's shell
access — including a model-driven terminal — can run `par task approve` directly, or bypass PAR and
edit files. `human_only` therefore cannot stop a local same-user attacker. Real isolation requires
limiting that agent's shell, using a separate OS user, a container, or a VM. Do not present
`human_only` as OS-level enforcement.

## 6. Data flow and privacy

- A native worker can read any file your user can read. Codex's sandbox does not restrict reads.
  Restrict the worker's readable scope before pointing it at sensitive projects.
- A manual hand-off sends the exported workspace to the vendor's cloud model once you open it in
  that application. The packet's README says so. Opening it is your decision.
- The data directory contains config and an execution ledger, not a copy of your project. It is not
  project memory.
- Bridge output is redacted, stripped of control characters, clipped and labelled. Every answer is
  under the output size cap.
- Do not put credentials in `providers.toml` or `profiles.toml`; the config validator refuses
  secret-shaped keys and values. Vendor logins stay with the vendor CLI.

## 7. Reporting a vulnerability

Use GitHub Security Advisories ("Report a vulnerability") on
`Sweetienite/personal-agent-runtime`. Include the PAR version, host OS, Python version, the exact
command or MCP call, expected vs actual behaviour, and a minimal reproduction. Do not include real
credentials, personal paths or third-party project data. See [.github/SECURITY.md](../../.github/SECURITY.md).

## 8. Known limitations to read before a real run

- Workers and checks run as your user; there is no `sandbox-exec`/container runner in this release.
- Codex is verified only for `codex-cli 0.166.0-alpha.6` on macOS with a ChatGPT login; upgrading
  the CLI requires re-validation.
- Antigravity is experimental, disabled by default, and the vendor's consumer terms prohibit
  wrapping it in third-party coding agents.
- Copilot is experimental and disabled by default; it answers R0/R1 text tasks only. One controlled
  real call through the private HOME with the real-keychain link succeeded (`PAR-COPILOT-OK`,
  `exitCode=0`, all tools disabled, no file changes). This is a single text answer, not full R1/R2
  acceptance evidence.
- Linux is core-experimental; Windows native is not supported.
- Real cancellation/timeout/quota incidents were not forced in the recorded evidence; scripted
  workers cover the control paths.
