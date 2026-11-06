# Changelog

User-facing summary of real changes. The detailed, line-by-line engineering log this project keeps for its own development (every behavior change, why it was made, what test proves it) is not part of this public repository — this file is the readable summary of that history.

## Unreleased

Documentation and metadata corrections only — no behavior change:

- Install commands in `README.md`, `README.en.md`, `docs/USER_GUIDE.md` and `docs/OPERATIONS.md` no longer assume the author's `~/Documents/Personal Agent Runtime`; they start from `git clone` and a checkout of your own.
- `pyproject.toml`'s description now says what the package does, instead of naming an internal phase and claiming "no real model calls".
- `AGENTS.md` points at files this repository actually publishes (root `CHANGELOG.md`; `.ai/` is the author's private task ledger) and says so explicitly.
- The Antigravity account/terms limitation is stated where the adapters are introduced, and `USER_GUIDE.md` no longer describes the removed web console.
- An ordinary Git repository can now be registered without the Harness convention: `par project add` defaults to `context_mode = "generic"`, which reads `README.md` / `AGENTS.md` / `CLAUDE.md` (when present) and the Git facts, and accepts an `ad_hoc` R2 contract once writes are switched on and the base is clean. `--context-mode harness` keeps the old parent-contract rules; registry entries written before the field keep the Harness behaviour. The mode is visible in `par project show`, `par project list --json` and `par doctor`. There is no data migration: entries without the field keep the Harness behaviour, and a Harness registration does not write the field, so older builds can still read it.

- `par task draft --draft FILE` compiles a short draft (project, goal, risk, paths, checks) into the same strict contract, deriving role, the ad-hoc flag and the idempotency key deterministically. A missing path, a missing check for a write task, an unknown project/worker/role or a Harness project without one active contract returns a question list and exit code 2 instead of a contract. It prints and never submits; it reads no repository files and never adds a command from a README.

- The MCP bridge has a 17th tool, `prepare_task`: a coordinator can turn a short draft into a strict contract and get the missing fields back as questions, without submitting or guessing a path/command. Task status and review answers now carry a derived `derived_state` and a short `summary`; the coordinator starter prompt was shortened and points at `docs/COORDINATORS.md` for the detailed contract examples instead of inlining them.

- Manual hand-offs now say exactly what they need and check what comes back: the export reports its `mode` (`workspace` for a file-changing task, `text` for a written answer), every absolute path, the base commit, the allowed paths and a copyable `next_actions` list. `PROMPT.md` starts with the folder check before any work instruction; the WorkBuddy menu path is labelled as a dated observation rather than a promise. The import now checks the marker against the ledger, the task's base and the packet's own `manifest.json`, and refuses a copy of the folder, an older export or an incomplete older packet by name instead of silently taking it. A failed import can be retried without touching the first attempt's evidence, and the noise rule for app settings folders is stated precisely: only newly added files are ignored.

- Results now carry a derived `evidence_summary` (CLI `par task result`, MCP `get_task_result`/`review_task`): a `level` (`MEASURED`, `MEASURED_WITH_EDITED_CHECKS`, `UNVERIFIED`, `FAILED`, `INCOMPLETE`, `UNKNOWN`), the Kernel's boundary and check results, whether the Worker edited a file a check reads, who reviewed the result, and `integration`/`applied_to_project` (always `NOT_INTEGRATED`/`false` until a separate integration step). The result envelope and its schema are unchanged: an old result without the newer sections comes back as `UNKNOWN`/`NOT_RUN`, never `PASS`.

- Candidate integration (`par candidate create/show/list`): accepted R2 patches are applied in dependency order to one clean baseline in a Kernel-owned repository, their checks are re-run there, and only a `VERIFIED` candidate may be used by a dependent task (`par task submit --candidate`, `par task rebase`). The real project is never written: accepted, candidate-integrated and applied stay three separate states, and the person still applies the patches. New tasks with an R2 dependency now require that dependency to be integrated; tasks submitted before this change keep the old `ACCEPTED`-is-enough meaning. The `candidates` table is additive (no ledger schema bump), so older builds can still open the ledger.

- Coordinator and Worker are now explicitly two independent roles: a role id names a primary/fallback chain and is
  not bound to a vendor, one tool may be both coordinator and worker over the same ledger (with separate private
  HOME/MCP sessions), and provider/worker `surface` values (`native_cli`, `manual`, `programmatic_session`, `acp`)
  are derived from the provider kind and included in the config snapshot. Coordinator descriptors now expose their
  surface and bridge capabilities. Old `profiles.toml`/`providers.toml` files and the `codex_manual_takeover` alias
  keep working; there is no config schema change and no rollback step. Adapter capabilities are static declarations
  (`register(..., capabilities=...)`) that routing reads without ever instantiating a factory, and the Kernel
  re-checks the real instance before an attempt: an unproven headless or write capability stops an automatic R2
  run before any process starts. `unknown` is never promoted to `yes`.

- Added `docs/COMPATIBILITY.md`: a dated, evidence-graded compatibility matrix for Codex, Gemini CLI, Claude Code,
  Antigravity CLI, ACP, OpenCode and the manual hand-off path. This round is explicitly **static research, with no
  real tests**: only offline `--version`/`--help` probes and official documentation were consulted; no model call,
  login, quota use or credential read occurred. Google's official notice (2026-06-18) ends Gemini CLI service for
  free / Google AI Pro / Ultra users and its FAQ prohibits wrapping `agy` in third-party coding agents, so `agy` is
  RED and stays experimental/disabled. There is **no GREEN candidate**, and T14 is marked `BLOCKED_BY_EVIDENCE` -
  no empty or untested adapter was added. `tests/tools/provider_probe.py` reproduces the offline probe and asserts
  it never runs a model, network call or credential read.

## 1.0.0 — 2026-11

First public release. Highlights:

- **Kernel-enforced re-verification**: `acceptance.checks` are commands the Kernel itself runs in a scratch clone after a Worker finishes — never a claim the Worker makes. A write task with no checks configured is a known edge case: the Kernel refuses to let an automated coordinator accept it blind, since there's nothing of its own to measure in that case. A human can still review the diff directly and accept it from the terminal.
- **MCP bridge with 16 tools** for any MCP-capable coordinator (Claude Desktop, Codex) to submit, inspect, approve, and accept tasks — including `get_task_diff`, which lets a coordinator read a result's redacted, size-capped diff content (not just file/line counts) before deciding how to advise a human on an edge case like the one above.
- **Two real Worker adapters**: Codex CLI (ChatGPT subscription login) and Google's Antigravity (`agy`, Google login via system keychain). Both run in a private clone per attempt; neither adapter holds or reads API keys.
- **A generalized manual hand-off path** (`par task handoff TASK --to APP`) for GUI-only desktop tools, bringing results back through the same Kernel verification pipeline as an automated Worker.
- **No GUI**: an earlier local web console (approve/reject/accept/close buttons, a read-only status view) was removed once the CLI and MCP bridge covered everything it did — maintaining a local HTTP server and its security surface for a view nobody used wasn't worth it for an open-source project. The MCP bridge and the CLI are the entire surface now.
- **Mutation-tested safety lines**: ~290 individual safety-relevant lines (permission checks, path policy, redaction, what a tool is allowed to do) are each deliberately broken one at a time in `tests/tools/mutation_check.py`, with a specific test required to catch each one.
- `par task list --waiting` surfaces exactly the tasks that need a human decision (pending approval, pending acceptance, pending recovery, or stuck on quota/login/rate-limits).

See [README.md](README.md) (Chinese, authoritative) and [README.en.md](README.en.md) for the full picture, and `docs/*.md` for the detailed technical reference.
