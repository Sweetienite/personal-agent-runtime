# Personal Agent Runtime (PAR)

*[中文说明见 README.md](README.md) — this is a shorter, English-first companion to it; the Chinese README and `docs/` remain the detailed, authoritative reference.*

[![tests](https://github.com/Sweetienite/personal-agent-runtime/actions/workflows/tests.yml/badge.svg)](https://github.com/Sweetienite/personal-agent-runtime/actions/workflows/tests.yml)

A local task runtime that lets **you** (or a coordinator LLM acting on your behalf) dispatch coding work to CLI coding agents — currently [Codex CLI](https://github.com/openai/codex) and Google's Antigravity (`agy`) — through an explicit Kernel: every attempt runs in a private clone, every result is re-measured by the Kernel itself (never taken on the agent's word), every write needs your approval, and every decision is written to an append-only ledger. Python 3.11+, **zero third-party dependencies**.

> Status: `1.0.0` (its author's own full build plan, P0 through P8, is done), actively used by its author on real (if modest) projects. Not yet battle-tested at scale — see [Honesty about where this stands](#honesty-about-where-this-stands) below.

## The one sentence that matters

**The Kernel never trusts what a Worker says about its own work.** "Tests pass" from a Worker's own summary is discarded; only the Kernel's own re-run of the acceptance checks counts. This single rule shapes almost everything else in the design.

## Why this exists / how it differs

By late 2026 a small cluster of "orchestrate CLI coding agents" tools had appeared — `codex-orch`, `cc-orchestrator`, `codeswarm`, `orc`, `agent-orchestra`, and a few others, alongside more established multi-agent frameworks like AutoGen/AG2 and CrewAI. We looked at the public descriptions of several of these before writing this file. None of this is a knock on them — most are young, and several explicitly target a different problem (fan-out across many agents/machines, not safety under a single human's review). PAR's specific angles, each backed by a real mechanism in this repo, not just a design doc:

- **Re-verification, not trust.** `acceptance.checks` are commands the *Kernel itself* runs in a scratch clone after the Worker is done — not a claim the Worker makes. A write task with no checks configured is a known, explicit edge case: the Kernel refuses to let an *automated* coordinator accept it blind, precisely because there is nothing of its own to measure. A human can still review the diff directly and accept it from the terminal. We found no comparable tool in our survey that enforces this at the orchestration-kernel level rather than leaving it as written guidance.
- **Mutation-tested safety lines.** `tests/tools/mutation_check.py` deliberately breaks ~290 individual safety-relevant lines (permission checks, path policy, redaction, what a tool is allowed to do) one at a time and asserts a specific test catches each one. This is a different, stronger bar than "we have unit tests."
- **A real audit trail with reasons, not just timestamps.** Every approve/reject/accept decision — whether made by you at the terminal or by a coordinator LLM through the MCP bridge — is written to an append-only ledger with a mandatory, non-blank explanation of *why*, not just *that* it happened.
- **A generalized manual hand-off path**, not just automated adapters. `par task handoff TASK --to APP` accepts any GUI-only desktop tool by name (WorkBuddy and TRAE are documented with real field notes from actual use; Cursor, Windsurf, Cline and Aider are wired in but not yet exercised for real). The export names its `mode` (`workspace` for a file-changing task, `text` for a written answer), every absolute path, the base commit, the allowed paths and a copyable `next_actions` list; the import checks the marker against the ledger, the task's base and the packet the Runtime wrote - a stale export, a copy of the folder or a forged marker is refused by name. Results then go through the same verification pipeline as an automated Worker — no shortcut, no unverified trust extended just because a human was in the loop.
- **Candidate integration without touching the project.** `par candidate create --task A` applies accepted R2 patches in dependency order to one clean baseline, re-runs their checks there and records the base, commit and per-patch hashes; a dependent task can then be submitted with `--candidate` (or rebased onto it) so it actually sees A's code. Accepted, candidate-integrated and applied stay three separate states, and the Runtime never writes your repository.
- **Roles, not vendor pairings.** `coordinator` names who you talk to; a `role` names a primary/fallback worker
  chain. One tool (or a human coordinator with a manual Worker) can fill both roles without a second account
  or a new product mode: the Worker always gets a fresh private HOME from the Kernel. Provider surfaces and
  adapter capabilities are explicit, and an unproven capability is refused before a write run starts.
- **An evidence-based compatibility matrix, not a support claim.** [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)
  records each candidate's version, OS, official documentation, account/terms constraints, what was actually
  probed (offline `--version`/`--help` only) and what was NOT tested. As of 2026-11-06 that research is static:
  **no GREEN candidate**, `agy` is RED (Google's FAQ prohibits wrapping it in third-party coding agents under
  consumer plans), and Gemini CLI / Claude Code / ACP / OpenCode are YELLOW. No new automatic provider was added.
- **Zero third-party dependencies**, standard library only. Easier to audit, nothing to update for CVEs in a dependency tree.

If your need is "coordinate many agents across machines fast," this is probably the wrong tool. If your need is "let an LLM actually touch my code, but never *just believe* it," this is what PAR is built for.

## Start here if you have never used PAR

The full stranger-machine walkthrough (isolated data directory, no account, fake smoke, an ordinary
Git repository, manual hand-off, and a dated log of what actually ran) is in
[docs/QUICKSTART.md](docs/QUICKSTART.md). [docs/MODES.md](docs/MODES.md) explains who the coordinator is,
who the Worker is, and why accepted / candidate-verified / applied are three different states.
**Set `PAR_DATA_DIR` to an empty directory before the first run** - otherwise `par doctor` reads
whatever configuration already exists in the default macOS data directory.

## Try it with nothing configured (2 minutes, no API key, no login)

Before setting up anything real, you can watch the whole pipeline — contract → Worker → private clone → Kernel re-verification → result — run end to end against **scripted fake CLIs**, no vendor account needed:

```bash
git clone https://github.com/Sweetienite/personal-agent-runtime.git
cd personal-agent-runtime
uv venv --python 3.12 .venv && uv pip install -e .   # zero third-party deps; requires 3.11+, 3.12 is what's actually tested day to day
# no uv? plain venv works too, UNLESS your python3.12 was itself installed by `uv python install` -
# those builds block `ensurepip` (PEP 668), so `python3.12 -m venv` fails with an externally-managed-environment
# error. Plain `python3 -m venv` (your system/distro Python) usually doesn't have that problem:
#   python3.12 -m venv .venv && .venv/bin/pip install -e .
python3 -B tests/tools/smoke_codex.py --self-test   # real Codex adapter code + a fake codex program + a throwaway fixture project
python3 -B tests/tools/smoke_agy.py --self-test     # same idea, for the Antigravity adapter
```

Both scripts print each step of a real submit → approve → run → verify → accept lifecycle. This is the fastest way to see what "the Kernel measures, not the Worker" actually looks like on screen.

## Quickstart with a real provider

```bash
cd personal-agent-runtime
uv venv --python 3.12 .venv && uv pip install -e .     # or: python3.12 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/par init                                      # creates its own data directory (mode 0700)
.venv/bin/par doctor                                    # should be all PASS, providers disabled until you configure one
python3 -B -m unittest discover -s tests -t .           # ~1558 tests, single process, ~6-9 min, no network, no model calls
python3 -B tests/tools/parallel_tests.py                # the same tests, parallelized across modules, ~1 min
```

If writing the full contract JSON is the friction, `par task draft --draft FILE` compiles a short draft (project, goal, risk, paths, checks) into the same strict contract: role, ad-hoc and the idempotency key are derived deterministically, a missing path or check becomes a question (exit 2) instead of a guess, and it only prints - it never submits and never adds a command from repository files.

From here: configure a provider (`config/providers.example.toml`), register a project with `par project add ID PATH` (an ordinary Git repository needs no `AGENTS.md` or `.ai/STATE.md` — that is the default generic mode; `--context-mode harness` is the optional advanced mode that keeps the parent-contract rules, and `--scaffold-harness` writes minimal starters only when you ask for them, never overwriting anything that exists), and `par project enable-write ID` when you're ready to let it touch real files. Every write still waits for your explicit per-task approval. Full detail (exit codes, troubleshooting, baseline workflow) is in `docs/OPERATIONS.md`.

## Design boundaries (enforced in code, not just documented)

- Workers never see or touch the real project directly — every attempt runs in a private clone; the Kernel never runs git inside a Worker's own `.git`.
- No API keys in configuration, ever; providers authenticate however their own CLI does (subscription login), and the config schema itself refuses anything that looks like a secret.
- State transitions go through one explicit table, enforced a second time by a database trigger — not just application-level checks.
- A result's own "tests pass" claim is never trusted; only the Kernel's own check runs count.
- Nothing is ever applied to a real project automatically. A result stays a diff until you run `git apply` yourself.

## Release status

As of 2026-11-06 the recommendation is **not to tag a new release yet**. A second real re-validation
did produce a successful real R1/R2 and completed the section-12 version check for the bundled
`codex-cli 0.166.0-alpha.6`, but the evidence is **`GREEN (scoped)`**: the host already provides an
OS-level external sandbox, so the runs used the vendor-documented externally-sandboxed mode. PAR's
default sandbox mode still needs a real run on a normal terminal. T14 remains closed as "no new
automatic provider", and no general GREEN candidate exists. See
[docs/RELEASE_NOTES_2026-11.md](docs/RELEASE_NOTES_2026-11.md) and the
[part 1](docs/reports/CODEX_REAL_REVALIDATION_20261106.md) /
[part 2](docs/reports/CODEX_REVALIDATION_PART2_20261106.md) re-validation reports.
Tagging needs a separate approval, and was explicitly deferred.

## What the CI badge means

Three named jobs: `smoke_install` (macOS install + doctor + version), `core_unittests`
(macOS core state/contract/approval/hand-off/integration regressions) and `linux_security_core`
(Ubuntu platform-independent security regressions). **The badge does not mean the full ~1558-test
suite passes in CI**; the remaining modules run only on a developer's Mac, and four
process-termination modules fail in the local sandbox with a failure set identical to the unmodified
baseline. See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/SECURITY.md](docs/SECURITY.md).
`docs/reports/` are historical snapshots of the version that was tested then, not current support claims.

## What's here, in English, vs. what stays in Chinese

This file and this project's commit messages are English. The deep reference docs (`docs/*.md` — security boundaries, the exact state machine, the MCP bridge's tool contracts, operational runbooks) are written in Chinese, since that's the language of the person who has used and tested this daily. They're kept in careful sync with the actual code, so a translation tool or a patient reader gets an accurate picture — contributions that translate specific docs are welcome. [CHANGELOG.md](CHANGELOG.md) has the user-facing summary of what changed and why.

## Honesty about where this stands

- Used daily by one person, on their own projects, with a single-digit-task sample size tracked honestly in `ops/reliability_report.py`'s output — not "battle-tested at scale."
- Two automated Worker adapters exist and are real (Codex CLI, Google Antigravity); other "market-standard" tools are wired in only as a manual hand-off path, explicitly labeled as not-yet-exercised where that's true (see `docs/HANDOFF.md`). Antigravity runs through Google's official `agy` CLI in its headless mode: that mode can change between vendor releases, and the account/terms risk is the user's. It ships disabled and is not the default automated path.
- No GUI. The MCP bridge (17 tools, including `prepare_task`, see `docs/MCP.md`) and the CLI are the entire surface — a deliberate choice after an earlier local web console was removed (see [CHANGELOG.md](CHANGELOG.md)), not an oversight.

## License

[MIT](LICENSE).
