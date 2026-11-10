# Getting Started

This page takes a stranger from a clean machine to a first verified result **without an AI account**,
then shows how to run a real Codex task and how to stop cleanly. It assumes no previous PAR data,
no private Harness, no model subscription and no credentials.

Verified on macOS 15.6.1 arm64 with Python 3.12. Linux is core-experimental; Windows native is not
supported (see [COMPATIBILITY.md](COMPATIBILITY.md)).

## 1. Prerequisites

- Python **3.11+** and `git` on `PATH`.
- A shell (macOS/Linux). Windows users need WSL2, which is **not tested** in this release.
- No third-party Python packages: PAR runs on the standard library.

```bash
git clone https://github.com/Sweetienite/personal-agent-runtime.git
cd personal-agent-runtime
python3 -m venv .venv
.venv/bin/pip install -e .
```

If `python3 -m venv` fails with `externally-managed-environment` and your Python came from `uv`,
create the environment with `uv venv --python 3.12 .venv && uv pip install -e .` instead. Do not
install into the system Python.

`pip install -e .` may fetch the setuptools build backend once. After installation, the demo, the
smoke scripts and `par doctor` make no network request and call no model.

Keep every command below inside this checkout; use the virtual environment's interpreter
(`.venv/bin/python`) so you are not testing a different installation.

## 2. Isolate the data directory

PAR stores its config, ledger and task artifacts in one data directory. Set `PAR_DATA_DIR` to a
fresh directory for this walkthrough so `par doctor` cannot read an existing installation:

```bash
export PAR_DATA_DIR="$(mktemp -d)"
.venv/bin/par init
.venv/bin/par doctor
.venv/bin/par version
```

Expected:

- `par init` creates the directory with mode `0700` and prints the **built-in default approval
  mode** (`coordinator_autonomous`). That default lets an MCP coordinator decide approvals inside
  the current coordinator lease; set `approval_mode = coordinator_scoped` or `human_only` in
  `profiles.toml` if you want decisions to stay with a person.
- `par doctor` ends with `PASS 5 | WARN 0 | FAIL 0` and reports every provider as disabled.
- `par version` prints `par 1.1.1 (phase RELEASE)`,
  `release status: released 2026-11-09` and
  `support: scoped; routes: codex_native, manual_handoff`.

`par doctor` runs no external program and writes nothing.

## 3. Watch the whole pipeline with no account

```bash
.venv/bin/python -B tests/tools/smoke_codex.py --self-test
```

The script uses the real Codex adapter code against a **fake `codex` program** and a throw-away
fixture project. It runs submit → approve → run → Kernel re-check → accept, prints the vendor event
types it parsed, and checks that neither the fixture nor your real files changed. It makes **no
model call and no network request**.

`tests/tools/smoke_agy.py --self-test` does the same for the experimental Antigravity adapter.

This proves the plumbing works. It does **not** prove any real vendor model is compatible; that
requires the dated real-provider evidence in [COMPATIBILITY.md](COMPATIBILITY.md).

## 4. A complete manual hand-off with no account

This path needs no provider at all: you act as the worker through any desktop tool that can open a
folder. It uses a one-line profile whose only worker is the manual relay.

```bash
mkdir -p "$PAR_DATA_DIR/config"
cat > "$PAR_DATA_DIR/config/profiles.toml" <<'TOML'
schema_version = 1
active_profile = "manual"

[profiles.manual]
coordinator = "claude_desktop"
default_worker = "w_manual"
max_parallel_workers = 1
worker_failure_policy = "ask"
additional_paid_credits = false
approval_mode = "human_only"

[profiles.manual.roles.code]
primary = "w_manual"
fallback = []

[workers.w_manual]
provider = "manual_handoff"
model = "human"
capabilities = ["read", "edit"]
TOML
.venv/bin/par doctor                       # still PASS; one worker configured, no provider enabled
.venv/bin/par fixture create demo          # a disposable writable project with one deliberate bug
```

Create a draft, compile it, and submit:

```bash
cat > draft.json <<'JSON'
{
  "project_id": "demo",
  "title": "Fix add()",
  "description": "Make add() return the sum of its two arguments.",
  "risk": "R2",
  "allowed_paths": ["app/"],
  "acceptance": {
    "criteria": ["the fixture's unit test passes"],
    "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q"], "timeout": 60}]
  }
}
JSON
.venv/bin/par task draft --draft draft.json > draft.out
tail -1 draft.out > task.json              # the contract is the last printed line
.venv/bin/par task submit --contract task.json
.venv/bin/par task approve demo-0001       # the R2 write permission
.venv/bin/par task handoff demo-0001 --to workbuddy --json
```

The export prints `mode: workspace`, an absolute `workspace_absolute_path`, a `PROMPT.md`, and four
`next_actions`. Open that **new folder** in your desktop tool (not the real project, and not an
existing workspace that happens to have the same name), paste `PROMPT.md`, and make the change.
Then import the folder:

```bash
.venv/bin/par task import demo-0001 --from "<workspace_absolute_path from the export>"
.venv/bin/par task result demo-0001
.venv/bin/par task diff demo-0001
.venv/bin/par task accept demo-0001
```

Expected: import reports `REVIEW_REQUIRED` with the Kernel's own check result, the diff shows the
one-line fix, and `accept` records it. The original fixture is still unchanged — PAR never applies
the patch. `WorkBuddy` is used only as a label here; the packet works with any tool that can open
the folder. The `--to` label itself is not a per-brand verification.

An import from the wrong folder, a stale packet or a forged marker is refused by name; the Kernel
checks the task id, base commit and the packet it wrote.

## 5. Your first real Codex task (optional, uses quota)

Only do this after you have read [SECURITY.md](SECURITY.md). Codex must be installed and signed in
with a ChatGPT subscription; the adapter refuses API-key login and unverified binaries. This section
does **not** reuse the `demo` fixture or the manual-only profile from §4.

1. Enable the provider in `$PAR_DATA_DIR/config/providers.toml` with an **absolute** path to the
   signed binary, then probe without spending quota:

   ```bash
   cat > "$PAR_DATA_DIR/config/providers.toml" <<'TOML'
   schema_version = 1

   [providers.codex]
   kind = "codex"
   enabled = true
   executable = "/absolute/path/to/the/signed/codex"
   TOML
   .venv/bin/par health --probe
   ```

   `--probe` runs version/login/model-list checks only; it does not call a model. Copy one model
   slug from `probes[0].facts.models_in_catalog` for the next step.

2. Replace the manual-only profile from §4 with a Codex profile. Changing `active_profile` also
   changes `default_worker`, so the next task goes to Codex and not to `w_manual`:

   ```bash
   cat > "$PAR_DATA_DIR/config/profiles.toml" <<'TOML'
   schema_version = 1
   active_profile = "codex_local"

   [profiles.codex_local]
   coordinator = "codex"
   default_worker = "w_codex"
   max_parallel_workers = 1
   worker_failure_policy = "ask"
   additional_paid_credits = false
   approval_mode = "human_only"

   [profiles.codex_local.roles.code]
   primary = "w_codex"
   fallback = []

   [workers.w_codex]
   provider = "codex"
   model = "<model slug from par health --probe>"
   capabilities = ["read", "edit"]
   TOML
   .venv/bin/par doctor
   ```

   `human_only` keeps the write approval at the CLI for this walkthrough. The model slug is yours
   to choose; the Runtime never picks one.

3. Register an ordinary Git repository (no `AGENTS.md` or `.ai/STATE.md` needed):

   ```bash
   .venv/bin/par project add myproject /absolute/path/to/repo
   .venv/bin/par project show myproject
   .venv/bin/par project enable-write myproject                 # readiness check only
   .venv/bin/par project enable-write myproject --confirm myproject
   ```

   The first `enable-write` call lists what is missing and changes nothing. The second one, with the
   id typed again, switches writes on. Even then each write task needs its own approval.

4. Submit a **new** contract for `myproject`. The §4 contract still points at `demo`, and resubmitting
   it would be deduplicated instead of creating `myproject-0001`:

   ```bash
   cat > codex-task.json <<'JSON'
   {
     "schema_version": 1,
     "project_id": "myproject",
     "title": "Fix add()",
     "description": "Make add() return the sum of its two arguments.",
     "role": "code",
     "risk": "R2",
     "allowed_paths": ["app/"],
     "acceptance": {
       "criteria": ["the repository's unit test passes"],
       "checks": [
         {"name": "unit", "argv": ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"], "timeout": 60}
       ]
     },
     "dependencies": [],
     "ad_hoc": true,
     "idempotency_key": "getting-started-codex-1"
   }
   JSON
   .venv/bin/par task submit --contract codex-task.json
   .venv/bin/par task approve myproject-0001
   .venv/bin/par task run myproject-0001
   .venv/bin/par task result myproject-0001
   .venv/bin/par task diff myproject-0001
   ```

   `ad_hoc` is `true` because an ordinary Git project has no parent contract: this task's JSON is
   the execution contract. If you registered another project id or your paths differ, change
   `project_id` and `allowed_paths`. The check must be a command that exits non-zero before the fix
   and zero after it. A real call uses your subscription quota. If the task ends `REVIEW_REQUIRED`,
   the Kernel's checks passed and the patch is waiting for your review. If it ends `FAILED`, read
   `par task result` for the Kernel's own failure classification; do not retry blindly and do not
   add `--dangerously-bypass-approvals-and-sandbox` or similar flags.

5. `accept` records the result. Apply the patch yourself:

   ```bash
   .venv/bin/par task accept myproject-0001
   .venv/bin/par task diff myproject-0001 > change.patch
   git -C /absolute/path/to/repo apply --check change.patch
   git -C /absolute/path/to/repo apply change.patch
   ```

## 6. Clean up

Nothing outside the checkout and `PAR_DATA_DIR` was written.

```bash
rm -rf "$PAR_DATA_DIR"
rm -f draft.json draft.out task.json
cd .. && rm -rf personal-agent-runtime
```

If you used a real project, `par project disable-write ID` first and confirm `git status` is clean.
Removing the checkout and the data directory removes PAR's records; your repository is untouched.

## 7. What just happened

| State | Meaning |
|---|---|
| `READY` / `QUEUED` | The contract passed validation; waiting for a worker or approval |
| `PERMISSION_WAIT` | A write permission (or worker override / base drift) needs a decision |
| `RUNNING` → `REVIEW_REQUIRED` | The worker finished and the Kernel's own checks passed; a person or allowed coordinator must review |
| `ACCEPTED` | The result is recorded; **nothing was applied** to the project |
| `CANDIDATE VERIFIED` | Accepted patches were re-applied and re-checked on one clean baseline; still not applied |
| `CLOSED` | The accepted task is finished |
| `FAILED` | A check failed, a boundary was violated, or the result was rejected; evidence is kept |

## 8. Upgrading and old Harness projects

- Existing `projects.toml` entries **without** a `context_mode` keep the old Harness behaviour; a
  generic registration writes `context_mode = "generic"`. `par doctor`, `par project list` and
  `par project show` only report the mode; they never rewrite an old entry.
- There is no destructive database migration. New ledger tables and columns are additive; an older
  build can still open the file. Back up `$PAR_DATA_DIR` before upgrading anyway.
- A Harness project still needs `AGENTS.md`, `.ai/STATE.md` and exactly one active contract. An
  ordinary Git project does not; `--context-mode harness` is the opt-in advanced mode.
- To roll back a configuration or an installed version, follow
  [docs/ROLLBACK.md](../ROLLBACK.md). It never deletes the ledger or rewrites project files.

Next: [USER_GUIDE.md](USER_GUIDE.md) for the daily flow, or [INTEGRATIONS.md](INTEGRATIONS.md) for
MCP coordinators and GUI tools.
