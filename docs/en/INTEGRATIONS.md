# Integrations and Workflows

PAR has one Kernel and three ways for an AI tool to participate. They are **not** three names for
the same automation level:

| Route | Connects | Who acts | Automation |
|---|---|---|---|
| Native worker | A built-in adapter to a CLI agent | The CLI agent | Automatic, per verified version/OS/account |
| Protocol coordinator | An MCP client that calls PAR's tools | The client decides; the Kernel enforces | Automatic for tool calls, human for installation and policy |
| Manual hand-off | Any tool that can open a folder or exchange a patch/text | A person plus the external tool | Human-mediated by definition |

One product can fill both roles (for example Codex as coordinator *and* native worker) without a
second subscription. The roles stay separate in the ledger, and the worker never inherits the
coordinator's authority.

## 1. A person at the terminal

Everything works without any AI client:

```bash
par project add myproject /path/to/repo
par task submit --contract task.json
par task approve myproject-0001
par task run myproject-0001
par task result myproject-0001
par task diff myproject-0001
par task accept myproject-0001
```

Status: the CLI path is exercised by the test suite and the dated getting-started walkthrough.
`human_only` keeps every decision here. The CLI actor label is not unforgeable identity.

## 2. Codex as coordinator and worker

Prerequisites: a signed, installed `codex` binary and a ChatGPT subscription login; the provider
config must name the absolute executable and enable it.

```bash
par health --probe                     # offline: version, signature, login, model list
par coordinator kit codex              # prints the MCP snippet; writes nothing
par coordinator prompt codex           # prints the starter prompt for the chat
par coordinator use codex              # writes a takeover packet and records the switch
par mcp serve --coordinator codex      # normally started by the MCP client, not by hand
```

Typical loop: Codex calls `prepare_task` → `submit_task`; the task waits for `WRITE_PERMISSION`;
Codex calls `get_task_status`, `review_task`, then `approve_task` if the profile and a live grant
allow it; `run_task` starts one attempt; `get_task_result`/`get_task_diff` feed the review;
`accept_task` records the result. `create_candidate` and `rebase_task` support A → B work.

Status: the MCP protocol and all 21 tools are tested with scripted clients; the real 2026-10-16
sample covers tool discovery and a read-only `list_tasks`. Submit/decision calls in a real Codex
session are not part of the dated evidence. Codex as a native worker is `VERIFIED_AUTO` only for
`codex-cli 0.166.0-alpha.6` on macOS with a ChatGPT login.

After `par coordinator use codex`, a bridge started for `claude_desktop` can no longer decide or
start work. See [USER_GUIDE.md](USER_GUIDE.md) §3.

## 3. Claude Desktop coordinator + Codex worker

Prerequisites: Claude Desktop configured with the printed `par` MCP entry, and a separate Codex
provider config for the worker.

```bash
par coordinator kit claude_desktop     # paste the printed entry into claude_desktop_config.json yourself
par coordinator use claude_desktop
# restart Claude Desktop, then call list_tasks to verify the bridge
```

Claude submits and reviews; Codex is only the execution worker. The Kernel gives the worker a
private HOME and clone, and the coordinator never gets shell access through PAR.

Status: Claude Desktop is `VERIFIED_COORDINATOR` for the recorded read-only sample (2026-10-16).
The Codex worker is `VERIFIED_AUTO` under the same limits as §2. The combination of a real Claude
decision followed by a real Codex attempt has not been recorded end to end in this repository.

## 4. Codex coordinator + WorkBuddy manual worker

Prerequisites: Codex connected as coordinator; WorkBuddy installed (or any GUI tool that can open a
folder).

```bash
par coordinator use codex
# Codex submits the task through MCP; the write permission is approved per policy
par task handoff myproject-0001 --to workbuddy --json
# a person opens the exported workspace in WorkBuddy, pastes PROMPT.md, does the work
par task import myproject-0001 --from "<exported workspace>"
```

The import returns the result to the Kernel; Codex reviews the diff and evidence like any other
worker's output. The export names `mode: workspace`, the absolute folder, the base commit, allowed
paths and `next_actions`.

Status: WorkBuddy 5.9.3 on macOS 15.6.1 completed a real R2 hand-off on 2026-11-07 (Kernel check
PASS, boundary clean, `REVIEW_REQUIRED`, nothing applied). This is `VERIFIED_MANUAL` for that
version. A Codex-coordinator-plus-WorkBuddy session in one sitting was not recorded.

## 5. Any GUI tool that can open an isolated folder

Same packet, different label:

```bash
par task handoff myproject-0001 --to cursor --json
```

Prerequisites: the tool can (a) open the exported folder as a workspace, (b) edit files there, and
(c) let you return the folder or a patch. TRAE, Cursor, Windsurf, Cline and Aider are documented
paths, but this repository has **no dated export/import success record** for them. The `--to` label
is a free name, not a compatibility claim.

Import rules are the same: the marker, task id, base commit and workspace must match; new files in
the tool's own config folders (`.trae/`, `.workbuddy/`, etc.) are ignored as noise, while changes
to tracked files are judged normally.

## 6. A patch-producing tool

If the tool can produce a valid unified diff against the task's base commit:

```bash
par task import myproject-0001 --patch fix.patch
```

The Kernel enforces the contract's `allowed_paths`, protected names, symlinks and the recorded base;
a patch outside the scope or against another base is refused. The checks still run in the Kernel's
copy. Aider is a plausible producer; it is `GENERIC_MANUAL` and not verified here.

## 7. Text-only web or mobile chat

Prerequisites: no project access, only copy/paste.

- R0/R1: `par task handoff TASK --to webchat`, paste `PROMPT.md`, save the answer to a text file,
  then `par task import TASK --text answer.txt`. This is `GENERIC_TEXT`.
- R2: a text answer cannot be a verified code change. Without a workspace or a valid patch, do not
  claim the task was implemented. Use a tool with folder or patch capability, or apply the change
  yourself and create a new task/commit.

PAR is a local CLI plus an MCP server. It is not a mobile app and does not run on a web host; a
phone/web assistant participates only through the human hand-off.

## 8. MCP capability, accurately

`par mcp serve` exposes 21 tools over stdio. The bridge:
- validates every argument before calling the Kernel;
- redacts and clips worker text, and labels it untrusted;
- cannot create/revoke grants, probe providers, clear health, change configuration or read arbitrary
  files;
- enforces the coordinator lease for decisions and work-starting actions.

`MCP coordinator ≠ MCP worker`. A client that can call these tools coordinates; it does not become
an automatic code worker unless PAR has a native adapter for it. ACP is `RESEARCH_ONLY`; no ACP
implementation exists in this build.

To inspect the exact tool list and schemas:

```bash
par coordinator kit claude_desktop --json
```

## 9. Failure paths common to every route

| Symptom | Meaning | Recovery |
|---|---|---|
| `not_the_coordinator` | The bridge's coordinator was replaced | Start a bridge for the current coordinator, or use the CLI |
| Refused import: marker/base/workspace | Wrong folder, stale packet or copied packet | Re-export and open the new folder |
| `no_grant` | `coordinator_scoped` without a matching grant | A person creates a grant, or decides at the CLI |
| `approval_mode` refusal | `human_only` | Decide at the CLI |
| `BLOCKED_NO_WORKER` | No adapter for the role | Configure a provider or use manual hand-off |
| `QUOTA_EXHAUSTED` / `AUTH_REQUIRED` | Vendor-side stop | Wait/fix login; never bypass |
| `FAILED` with boundary violations | The worker changed something outside scope | Read the paths in `result`; do not accept |

Evidence levels for every tool are in [COMPATIBILITY.md](COMPATIBILITY.md).
