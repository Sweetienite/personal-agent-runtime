# Changelog

User-facing summary of real changes. The detailed, line-by-line engineering log this project keeps for its own development (every behavior change, why it was made, what test proves it) is not part of this public repository — this file is the readable summary of that history.

## Unreleased

User-facing changes since the last tag. This section is a running summary, not a release; the current recommendation is **not to tag yet** (see [docs/RELEASE_NOTES_2026-11.md](docs/RELEASE_NOTES_2026-11.md)).

- **Codex default-sandbox revalidation completed (2026-11-07).** On a normal terminal (`sandbox_check=0`, nested sandbox available) `codex_revalidate.py --yes-use-quota --model gpt-6-luna` ran the product's default `-s read-only|workspace-write` mode: offline 8/8, trust 3/3 and two real R1/R2 calls all passed. R1 correctly answered `add(2, 3) = -1` (not the spec's `5`) with no file change; R2 was `MEASURED · boundary CLEAN · checks PASSED (1/1)` with a one-line patch, 0 boundary violations and nothing applied. The fixture stayed clean, the real data directory and `~/.codex/config.toml` were unchanged. The existing Codex path is now graded **`GREEN`** (not `GREEN (scoped)`); this is not a new automatic provider, and T14's "no new provider" decision is unchanged. Full evidence: [docs/reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md](docs/reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md) and `docs/CODEX.md` §15.
- **Docs link check (2026-11-07).** `tests/tools/link_check.py` checks relative links in the current Markdown docs and exits 1 on a missing one. `docs/reports/` is excluded by default because those historical snapshots link to evidence files excluded from the public push and carry disclaimers; `--historical` lists them. Current docs: 0 missing.
- **Generic Git onboarding fix and real R2 (2026-11-07).** A project registered with the default `vcs = "auto"` could not be switched to writable: `enable-write` was refused with `vcs must be "git"`, even though the folder was a clean Git repository. `enable-write` now rewrites that entry's `vcs` to `"git"` when the project root has `.git` (other lines and the backup are kept; read-only registration is unchanged). A regression test pins it. This was then exercised with a real Codex R2 on an ordinary Git repository with **no** `AGENTS.md`/`.ai/STATE.md`/parent contract: `demo-0001` finished `REVIEW_REQUIRED` with `MEASURED · boundary CLEAN · checks PASSED (1/1)`, one file +1 −1, 0 violations, nothing applied. Evidence: [docs/reports/GENERIC_REAL_R2_20261107.md](docs/reports/GENERIC_REAL_R2_20261107.md).
- **Candidate integration now checks the accepted patch's hash (2026-11-07).** Every finished attempt records `patch_sha256` in `changes.json`; before integrating, the Kernel recomputes the hash of `work.patch` and refuses a patch that no longer matches (even if it stays inside `allowed_paths`). An attempt with no recorded hash is refused as `missing_provenance`. This closes the "someone modified the patch after acceptance" gap in the T11 provenance requirement. One new mutation check (`M915`) pins it.
- **WorkBuddy real hand-off re-run (2026-11-07).** Using WorkBuddy 5.9.3 on macOS 15.6.1, a throw-away fixture task was exported as a `workspace` hand-off, opened in WorkBuddy via **New task → Select workspace → Open local folder**, completed from `PROMPT.md`, and imported with `par task import`. The Kernel itself ran the `unit` check: `MEASURED · boundary CLEAN · checks PASSED (1/1)`, one file +1 −1, 0 boundary violations, task left in `REVIEW_REQUIRED`, nothing applied to a real project. The import verified the marker against the ledger, the packet's base and the actual exported workspace. No UI automation or vendor API was used. Full evidence: [docs/reports/WORKBUDDY_REAL_HANDOFF_20261107.md](docs/reports/WORKBUDDY_REAL_HANDOFF_20261107.md) and `docs/HANDOFF.md`.
- **Context files are no longer read through symlinks.** `README.md`, `AGENTS.md`, `CLAUDE.md`, `.ai/STATE.md` and active contract files that are symlinks, resolve outside the project root, or cannot be read are now omitted from the Context Manifest with a named warning instead of being opened to compute a hash. This closes the gap between the documented rule and the code (a symlink could previously make the Runtime read a file it was never meant to touch, and an unreadable target raised instead of being reported). Two new mutation checks (`M913`, `M914`) pin the guard.
- **Codex version re-validation completed with a scope limit (2026-11-06, part 2).** After the person granted broader authorisation, a second real run produced the project's first **successful real R2**: `codex-cli 0.166.0-alpha.6` (signed, ChatGPT subscription, `gpt-6-luna`) read the fixture correctly on R1 and on R2 produced a one-line patch that passed the Kernel's own `unit` check (`MEASURED · checks PASSED (1/1)`, 0 boundary violations, nothing applied to the project). Section 12 now passes end to end: offline 8/8, trust 3/3, `par health --probe` AVAILABLE, and 153/153 controllable failure/cancel/timeout/quota classification samples. `TESTED_VERSION` is now `0.166.0-alpha.6` and the `untested version` warning is gone. **Scope limit:** the host already runs inside a macOS App Sandbox (`sandbox_check=1`), so a nested Seatbelt sandbox cannot be stacked; the real runs used the vendor-documented `-s danger-full-access` in a throw-away worktree copy only. The main adapter was NOT changed, no switch was added, and no privilege flag is in the product defaults. At that point the result was graded **`GREEN (scoped: externally sandboxed host)`**, because PAR's default `-s read-only|workspace-write` still needed a real run on a normal terminal. **That last step was completed on 2026-11-07 (see the latest entry above), upgrading the existing Codex path to `GREEN`.** Full evidence: [docs/reports/CODEX_REVALIDATION_PART2_20261106.md](docs/reports/CODEX_REVALIDATION_PART2_20261106.md) and `docs/CODEX.md` section 14. **No new provider, no tag, no Release.**
- New `tests/tools/codex_revalidate.py` (+ 4 unit tests) runs section 12 in one command. It refuses real model calls without `--yes-use-quota --model`, and on a host that cannot create a nested sandbox it exits 2 with zero model calls rather than bypassing the CLI sandbox. `tests/tools/sandbox_probe.py` now also reports the raw inherited-sandbox `sandbox_check` value (+ 2 unit tests).

- **T14/T13 follow-up, first attempt (2026-11-06): real Codex re-validation, no new provider.** After the person authorised the unlock and chose the officially permitted ChatGPT-subscription `codex exec` route, a real R1 and a real R2 ran against the bundled signed `codex-cli 0.166.0-alpha.6` on a non-sensitive fixture. R1 produced a clean terminal event while the Worker reported it could read no file; R2's vendor `SUCCESS` was correctly rejected by the Kernel's own `unit` check (`FAILED`, no patch, nothing applied). The cause is environmental: this host already runs inside a macOS sandbox, so a nested Seatbelt sandbox cannot be created (`sandbox-exec` reproduces `sandbox_apply: Operation not permitted`). **That attempt produced no GREEN, no new automatic provider, no empty adapter and no tag.** The full evidence is in [docs/reports/CODEX_REAL_REVALIDATION_20261106.md](docs/reports/CODEX_REAL_REVALIDATION_20261106.md); `docs/COMPATIBILITY.md`, `docs/CODEX.md` (new §13), `docs/ADAPTERS.md` and the release checklist were updated accordingly. At that point `TESTED_VERSION` stayed at the older verified build, so `par health --probe` kept reporting `untested version`; the part-2 attempt above later updated it (scoped).
- A successful run with **zero mechanical Kernel checks** no longer says `every check passed`. It now says no Kernel checks ran and asks for evidence review; a run with checks reports how many actually passed. Two regression tests in `tests/unit/test_kernel_run.py` and two mutation checks (`M673`, `M674`, both KILLED) pin both cases.
- New offline diagnostic `tests/tools/sandbox_probe.py` (+ unit tests) answers one question only: can this host enter a nested OS sandbox? No model call, no network, no credential read, no install; non-macOS hosts are reported as `not applicable`. It separates a host-sandbox limitation from a provider incompatibility.
- The opt-in real-CLI isolation check now compares **tool definitions** structurally instead of counting the word `plugin`: newer Codex builds mention plugins in static instruction prose while genuinely removing the plugin/app/MCP tool definitions. It also records that `spawn_agent` / `list_agents` still appear in the base tool list, without claiming sub-agents are verified off. The version-drift gate deliberately stayed failing until a successful real R2 existed; the part-2 attempt above later produced one (scoped).

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
  RED and stays experimental/disabled. There was **no GREEN candidate** in that round, and T14 was marked
  `BLOCKED_BY_EVIDENCE` - no empty or untested adapter was added. (The static-research description applies
  to that round only: the first real R1/R2 re-validation attempt on 2026-11-06 produced no successful R2;
  the second attempt recorded above later produced one, scoped, and the 2026-11-07 default-sandbox run
  upgraded the existing Codex path to `GREEN`. Still no other general GREEN candidate.) `tests/tools/provider_probe.py` reproduces the offline
  probe and asserts it never runs a model, network call or credential read.

- Security fix (independent PR): `is_sensitive_path` now splits Windows drive letters, UNC shares and
  backslashes as path separators. On POSIX a backslash is a legal file-name character, so a path such as
  `C:\Users\me\.ssh\id_rsa` was previously seen as one harmless-looking name and could slip past the
  deny-by-name guard. The change is deliberately conservative (a false positive only hides a path) and
  is covered by `test_policy` cases plus mutation `M672`. Windows itself remains an untested platform.

- CI reliability: `tests/helpers.py::tree_digest` now skips a path that vanishes between
  listing and stat'ing it (git's background maintenance creates/removes
  `.git/objects/maintenance.lock` mid-run, which made a macOS CI job fail intermittently).
  A direct regression test simulates the race; real content changes are still detected.

- Security and portability audit (T15): `docs/SECURITY.md` now has a traceable
  **protected / not-guaranteed / user-responsibility** matrix, backed by 21 runnable negative
  cases in `tests/unit/test_p3_security_audit.py` (path escape, unauthorised writes, hostile
  project rules / Worker messages, check-command side effects, unknown vendor events). A
  platform grading table records macOS `E2E`, Linux `CORE_EXPERIMENTAL` and Windows
  `STATIC_GAP` with their exact gaps. A new `linux_security_core` CI job installs, runs
  `par doctor`/`version` and repeats the platform-independent security regressions on
  Ubuntu, so a platform difference cannot silently skip a security behaviour. No new
  OS-sandbox or full Windows support is claimed.

- Public documentation and release review (T16): added `docs/QUICKSTART.md` (a dated,
  stranger-machine reproduction using an isolated `PAR_DATA_DIR`: install, `par doctor` 5 PASS / 0 FAIL,
  fake CLI R1+R2 smoke, an ordinary Git repository registered in generic mode, and a manual hand-off
  packet), `docs/MODES.md` (three independent roles, three collaboration paths, and the
  ACCEPTED / CANDIDATE_READY / APPLIED distinction), `docs/reports/README.md` (historical snapshots are
  not current support claims; the old P1-P8 phases are distinct from this round's P0-P3), and
  `docs/RELEASE_NOTES_2026-11.md` with an explicit **recommendation not to tag a new release yet**
  (T14 was closed by a scope decision - no new automatic provider - after a first real re-validation that
  produced no successful R2; the later default-sandbox run above upgraded Codex to `GREEN`; the
  bundled Codex binary is an alpha; platform evidence is incomplete).
  README / README.en / CONTRIBUTING / AGENTS were aligned: ~1558 tests at that point, three named CI jobs, a badge that
  does not mean the full suite, no stale private paths, and honest historical-report framing.

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
