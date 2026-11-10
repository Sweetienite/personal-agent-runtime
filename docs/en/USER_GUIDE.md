# User Guide

This is the daily-use reference. It assumes you have completed
[GETTING_STARTED.md](GETTING_STARTED.md). The Chinese command reference is
[docs/OPERATIONS.md](../OPERATIONS.md); the exact state machine is [docs/KERNEL.md](../KERNEL.md).

## 1. The mental model

- **Coordinator**: the person or AI client that submits work and reviews results. A person at the
  CLI is always a coordinator.
- **Worker**: what edits files or answers a question. It can be a native adapter (Codex), an
  experimental adapter (Antigravity), or a human with a GUI/web/mobile tool.
- **Kernel**: the only authority for state, permissions and evidence. It runs the acceptance
  commands itself; a worker's "tests pass" is untrusted text.
- **Private clone**: every attempt works in `tasks/<id>/attempts/<n>/workspace/`. Your real project
  is never the working directory.

Roles and interfaces are different things: one tool can be both coordinator and worker, and MCP
connects coordinators, not workers.

## 2. The normal day

```bash
par project list                       # what is registered and whether writes are on
par task draft --draft draft.json      # optional: assemble a strict contract from a short draft
par task submit --contract task.json
par task status TASK
par task approve TASK --id N             # when several items are pending; --id is optional
par task run TASK                      # foreground; Ctrl-C stops it cleanly
par task result TASK
par task diff TASK
par task accept TASK                   # records the result; applies nothing
par task close TASK                    # optional, after accepting
```

Use `par task events TASK` for the append-only history and `par task result TASK --attempt N` for
an older attempt.

### Contracts

`par task draft --draft FILE` turns a draft into a strict contract and prints it without
submitting. It derives the schema version, role, ad-hoc flag and idempotency key from the project
and active profile. A missing path or check becomes a question (exit 2), not a guess.

Risk tiers:

| Tier | Meaning | Writes? | Checks |
|---|---|---|---|
| `R0` | A quick answer, no project work | No | None |
| `R1` | Read-only analysis of the project | No | None |
| `R2` | A code change inside `allowed_paths` | Yes, in the private clone | Required |
| `R3` | Never accepted | — | — |

The contract also names `dependencies`: a dependent task runs only after its predecessor is
`ACCEPTED`/`CLOSED`. If the predecessor changed files, a `VERIFIED` candidate must exist and the
dependent task must be based on it (see §5).

## 3. Approvals, modes and the coordinator lease

Every write, worker override, base-drift confirmation and permission retry is an approval item.
MCP decision tools require a reason; the CLI accepts an optional `--note` and records an empty
reason when it is omitted. Who may decide is set by `approval_mode` in `profiles.toml`:

| Mode | A coordinator may decide | Use when |
|---|---|---|
| `coordinator_autonomous` (default) | Yes, inside the current lease; every decision is recorded | You trust the current coordinator and want fewer mechanical stops |
| `coordinator_scoped` | Only inside a person-created grant (actions, project, risk, approval kind, expiry) | You want bounded delegated authority |
| `human_only` | No; decisions happen at the CLI | High-risk projects, audits, migrations |

The **lease** comes first in every mode. `par coordinator use codex` writes a takeover packet and
records the switch. After that, a bridge started as `claude_desktop` cannot approve, reject, accept,
close, submit, run, retry, resume, create a candidate or rebase; it gets `not_the_coordinator`.
Viewing, pausing and cancelling remain allowed. Your own CLI commands are never blocked.

Create and inspect grants from the CLI only:

```bash
par coordinator status
par coordinator grant codex --action approve --project myproject --risk R2 \
  --approval-kind WRITE_PERMISSION --expires 24h
par coordinator grants
par coordinator revoke 3
```

No MCP tool can create or revoke a grant. Unknown coordinator ids are refused. The Kernel re-checks
the lease, mode and grant inside the same write transaction as the decision, so a revocation or a
switch cannot land between the check and the record.

`human_only` is not OS-level identity: a process with your shell access can still run
`par task approve`. See [SECURITY.md](SECURITY.md).

## 4. Reading a result

`par task result` prints the Result Envelope. Key fields:

- `outcome`, `terminal_state`, `failure_class`, `stage`: what the Kernel measured, not what the
  worker claimed.
- `summary`: the worker's own words, always marked `summary_verified: false` and
  `untrusted_worker_text: true`.
- `tests`: the Kernel's check runs (`PASS`/`FAIL`/`NOT_RUN`); `NO_MECHANICAL_CHECK` means no check
  ran at all.
- `boundary`: violations, sensitive paths and the paths that changed.
- `diff_stat`, `partial`, `evidence`: what changed and where the evidence lives.
- `applied_to_project` is always `false`. PAR has no code path that applies a patch.

Typical terminal states:

| Status | Meaning | Next |
|---|---|---|
| `REVIEW_REQUIRED` | Checks passed; waiting for review | `par task diff`, then accept/reject |
| `FAILED` | Check failed, boundary violated, or rejected | Read `result`/`events`, fix the contract or retry |
| `INTERRUPTED` | The Kernel or worker stopped mid-attempt | `par task recover`, then retry |
| `RECOVERY_REQUIRED` | An orphaned attempt needs settling | `par task recover TASK` |
| `BLOCKED_NO_WORKER` | No usable adapter for the route | Configure a provider or use manual hand-off |
| `QUOTA_EXHAUSTED` / `RATE_LIMITED` / `AUTH_REQUIRED` | Vendor-side stop | Wait, fix login, or switch worker; never bypass |

A worker's claim never turns a missing or failed check into `REVIEW_REQUIRED`.

## 5. Candidates: A → B

When task A changed files and B depends on A, "A is accepted" is not enough. Build one clean
baseline:

```bash
par candidate create --task A --task B    # repeat --task in dependency order
par candidate show cand-myproject-ab12cd34
par task submit --contract b.json --candidate cand-myproject-ab12cd34
# or, for an already queued task that never ran:
par task rebase B --candidate cand-myproject-ab12cd34
```

`candidate create` applies each accepted task's own `work.patch` in dependency order, checks the
recorded patch hash, enforces `allowed_paths`, runs each task's acceptance checks on the candidate,
and records `VERIFIED` or `FAILED` with logs. It runs those check commands with your user
permissions — no OS sandbox. Nothing is applied to the real project; the candidate lives under the
data directory.

`ACCEPTED`, `CANDIDATE VERIFIED` and `APPLIED` are three different states. Only the last is a
change in your repository, and you perform it yourself.

## 6. Manual hand-off

```bash
par task handoff TASK --to workbuddy --json
# open the exported workspace, paste PROMPT.md, let the tool work
par task import TASK --from "<workspace path>"    # or --patch FILE, or --text FILE
```

The export names its `mode` (`workspace` or `text`), the absolute workspace path, base commit,
allowed paths and copyable `next_actions`. A queued task is paused so another worker cannot race it.
The import verifies the packet marker, task id, base and workspace; stale, copied or forged packets
are refused by name. The result then goes through the same file comparison, boundary and check
pipeline as a native worker.

Text-only tools can participate in R0/R1 (`--text`). They cannot produce a verified code change
without a workspace or a valid patch. `--to` is a free label; only WorkBuddy currently has a dated
real sample.

## 7. Retry, pause, resume, recovery

```bash
par task pause TASK
par task resume TASK
par task retry TASK --worker W --fresh    # both flags are optional
par task recover TASK --terminate         # only when the Kernel can verify the process
par task cancel TASK
```

- `retry` starts a **new** attempt. It keeps the stopped attempt's verified work unless `--fresh`;
  it never reuses a permission grant for a different worker and never bypasses quota/login cooldown.
- `recover` settles attempts whose Kernel disappeared. It never starts a second writer and never
  kills a process it cannot identify by PID and start time. `--terminate` additionally stops the
  verified process group and is restricted to the current coordinator over MCP.
- `cancel` always stops; a replaced coordinator may still cancel.

## 8. Failure recovery without weakening the boundary

1. Read `par task status TASK`, `par task result TASK` and `par task events TASK`.
2. Match the `failure_class`/`stage` to [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
3. Fix the cause (contract path, check command, login, quota), then retry.
4. Never "fix" a failure by adding `--dangerously-*`, disabling the check, editing the test inside
   the patch, or widening `allowed_paths` beyond what the task needs.
5. If the project moved, approve the `BASE_DRIFT` item or re-create the task from the new base.
6. If evidence was tampered with, the Kernel refuses to integrate it; keep the failed record and
   start a new attempt.

## 9. Where state lives

| Path | Contents |
|---|---|
| `$PAR_DATA_DIR/config/` | `profiles.toml`, `providers.toml`, `projects.toml` |
| `$PAR_DATA_DIR/ledger.sqlite3` | Append-only tasks, events, approvals, grants, candidates |
| `$PAR_DATA_DIR/tasks/<id>/attempts/<n>/` | Private workspace, logs, `work.patch`, result |
| `$PAR_DATA_DIR/candidates/<id>/` | Candidate repo and check logs |
| `$PAR_DATA_DIR/takeover/` | Coordinator packets |

The data directory is not project memory. Project facts stay in the project's own files. Back up
the data directory before upgrades; see [docs/ROLLBACK.md](../ROLLBACK.md) for the non-destructive
rollback procedure.
