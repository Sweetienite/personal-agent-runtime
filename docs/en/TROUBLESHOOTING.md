# Troubleshooting

Diagnose first, change permissions last. None of the recoveries below require disabling a check,
widening `allowed_paths`, or using vendor bypass flags.

## 1. Install and startup

| Symptom | Likely cause | Recovery |
|---|---|---|
| `par: command not found` | The venv is not active or `pip install -e .` did not run | Use `.venv/bin/par` explicitly, or activate `.venv` |
| `externally-managed-environment` from `python3 -m venv` | The Python came from `uv`/a distro and blocks `ensurepip` | `uv venv --python 3.12 .venv && uv pip install -e .`, or use a system Python that is not externally managed |
| `ModuleNotFoundError: par` | Ran a different interpreter | Run commands through `.venv/bin/python` / `.venv/bin/par` |
| `par doctor` reports an unsafe data directory | The directory exists but is not `0700`, has the wrong owner, or is a symlink | Point `PAR_DATA_DIR` at a new empty directory; PAR never chmods an existing directory it did not create |
| `par doctor` shows a config error | `providers.toml`/`profiles.toml` has an unknown field or provider kind | Read the exact `path: message` line; fix or move the file aside. `par doctor` is read-only |
| `task list/status/events/result/diff` says inspection-only | Worker configuration is invalid, but the existing ledger can still be inspected safely | Run `par doctor`, repair `profiles.toml` or `providers.toml`, then use task actions again. In this mode MCP exposes only list/status/result/diff and refuses all other calls |
| Git-related error on submit | `git` is missing or the project is not a repository | Install `git`; an R1/R2 task needs a Git base commit and an ordinary repository |
| `404` / clone fails | Wrong URL or no network at clone time | Clone from the canonical URL once; after installation PAR itself needs no network |

Exit codes: `0` success, `1` a refused/unsafe/not-ready state, `2` usage, missing file or invalid
configuration.

## 2. The zero-account demo

| Symptom | Meaning | Recovery |
|---|---|---|
| Smoke script exits non-zero | A fixture step failed; the message names it | Re-run from the checkout root; do not point it at a real project |
| `PAR_DATA_DIR` not set | `par doctor` may read your real data directory | `export PAR_DATA_DIR="$(mktemp -d)"` before `par init` |
| `no active profile is configured` from `task draft` | A fresh data directory has no `profiles.toml`; the smoke script supplies its own | For the manual demo, create the profile in [GETTING_STARTED.md](GETTING_STARTED.md) §4 |
| `no Kernel check ...` / `NO_MECHANICAL_CHECK` | A read-only task has no checks by design | That is not a pass. Review the evidence yourself; an automated coordinator cannot accept a check-less write task |

## 3. Approvals and authorization

| Error | Meaning | Recovery |
|---|---|---|
| `approval_mode` | The profile is `human_only` | Decide at the CLI; change the profile only as a deliberate policy change |
| `no_grant` | `coordinator_scoped` and no matching grant | A person runs `par coordinator grant ...` with the right action/project/risk/kind/expiry, or decides at the CLI |
| `not_the_coordinator` | The bridge's coordinator no longer holds the lease | Start a bridge for the current coordinator (`par coordinator use NAME`, then `par mcp serve --coordinator NAME`), or use the CLI |
| `coordinator_identity` | The bridge named no known coordinator | Start it with `--coordinator claude_desktop` / `--coordinator codex` |
| `which_approval` | More than one item is pending | Pass `--id`/`approval_id` |
| `already_decided` | Another process decided it first | Re-read `par task status`; nothing was double-applied |
| A grant “disappeared” | It expired or was revoked | `par coordinator grants --all` shows the state; revoked grants cannot be re-opened |

## 4. Provider and quota

| Symptom | Meaning | Recovery |
|---|---|---|
| `BLOCKED_NO_WORKER` | No adapter is registered for the role | Enable a provider with an absolute executable, or use manual hand-off |
| `AUTH_REQUIRED` | Vendor login is missing/expired | Sign in with the vendor CLI; `par health --probe` is offline and confirms the state |
| `QUOTA_EXHAUSTED` | Subscription quota is exhausted | Wait for the window or switch to a configured fallback; PAR never buys credits |
| `RATE_LIMITED` | Vendor rate limit | Wait for the cooldown; do not retry in a loop |
| Codex binary refused | Unsigned wrapper, wrong team, API-key login or not executable | Use the signed binary inside ChatGPT.app at an absolute path; sign in with the ChatGPT subscription |
| `sandbox_apply: Operation not permitted` | The host already runs inside a macOS sandbox and cannot nest one | Run PAR in a normal terminal, not inside another sandboxed host; `tests/tools/sandbox_probe.py --json` shows the state |
| Model slug rejected | The configured slug is not in the vendor's current catalog | `par health --probe` lists local facts; update the worker's `model` deliberately |

Never “fix” these with `--dangerously-bypass-approvals-and-sandbox`, `--yolo`, a metered API key
unless you deliberately chose that account path, or by disabling the check.

## 5. Task state

| Status | Meaning | Recovery |
|---|---|---|
| `PERMISSION_WAIT` | An approval item is pending | `par task status` then `par task approve TASK --id N` (or `reject`) |
| `REVIEW_REQUIRED` | Checks passed, result awaits review | `par task diff`, `par task result`, then accept/reject |
| `FAILED` | Check failed, boundary violated or result rejected | Read `failure_class`/`stage`; fix the contract/check and retry |
| `INTERRUPTED` | The attempt stopped mid-run | `par task recover TASK`, then retry |
| `RECOVERY_REQUIRED` | An orphaned attempt is unsettled | `par task recover TASK`; `--terminate` only if the Kernel can verify the process |
| `CANCEL_REQUESTED` stuck | A process group has not stopped yet | Wait for the grace period; `recover --terminate` only targets a verified group |
| `dirty_project` | The repository has uncommitted changes | Commit or stash; a candidate/R2 task needs a clean base |
| `BASE_DRIFT` | The project moved since submission | Review the new base, then approve the item or create a new task |
| `stale_candidate` | The candidate was built on another commit | Rebuild the candidate from the current state |
| `missing_provenance` / tampered patch | The recorded patch hash does not match | Do not integrate; keep the evidence and start a new attempt |

## 6. Manual hand-off and imports

| Symptom | Meaning | Recovery |
|---|---|---|
| Refused: marker/task id | Wrong folder or copied packet | Re-run `par task handoff`, open the new folder, import that folder |
| Refused: base commit | The workspace belongs to another project state | Export a fresh packet from the task's current base |
| Refused: workspace path | The path is not the exported workspace | Use the exact `workspace_absolute_path` from the export |
| Changes outside `allowed_paths` | Boundary violation | Review the paths; start a new task with a contract that actually needs those files |
| Text import on an R2 task | A text answer is not a code change | Use a workspace or patch, or implement the change yourself |
| Stale export | A newer export exists | Only the newest packet can be imported |

## 7. MCP bridge

| Symptom | Meaning | Recovery |
|---|---|---|
| Client shows no `par` tools | Snippet not installed, wrong Python path, or the client was not restarted | `par coordinator kit NAME` prints a fresh snippet; keep `PYTHONPATH`; restart the client |
| Tools appear but every call errors | The bridge was started for a different data directory | Use the same `--data-dir` as the CLI |
| `not_the_coordinator` | Someone switched coordinators | See §3 |
| Output truncated | The bridge caps every answer | Request a narrower field (`get_task_diff` for the patch, `list_tasks` with a limit) |
| Bridge log shows `internal error` | A bug or an unexpected input shape | Re-run the same call with `--data-dir` isolated and capture the tool name; report it with the version |

## 8. When to stop and report

Report a bug instead of working around it when:

- a path outside `allowed_paths` appears in a patch, or a protected file was modified;
- a worker's claim is shown as verified;
- a decision by a replaced coordinator succeeds;
- a check that did not run is reported as passed;
- the real project changes without your `git apply`;
- the Kernel kills a process it cannot identify.

Include `par version --json`, `par doctor --json` (with an isolated data directory if the real one
contains private paths), the task id, the command, the expected and actual output, and the relevant
event types. Redact credentials and personal paths. See [SUPPORT.md](../../SUPPORT.md).
