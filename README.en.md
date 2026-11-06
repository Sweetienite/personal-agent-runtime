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
- **A generalized manual hand-off path**, not just automated adapters. `par task handoff TASK --to APP` accepts any GUI-only desktop tool by name (WorkBuddy and TRAE are documented with real field notes from actual use; Cursor, Windsurf, Cline and Aider are wired in but not yet exercised for real) and brings results back through the same verification pipeline as an automated Worker — no shortcut, no unverified trust extended just because a human was in the loop.
- **Zero third-party dependencies**, standard library only. Easier to audit, nothing to update for CVEs in a dependency tree.

If your need is "coordinate many agents across machines fast," this is probably the wrong tool. If your need is "let an LLM actually touch my code, but never *just believe* it," this is what PAR is built for.

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
python3 -B -m unittest discover -s tests -t .           # ~1370 tests, single process, ~6-9 min, no network, no model calls
python3 -B tests/tools/parallel_tests.py                # the same tests, parallelized across modules, ~1 min
```

From here: configure a provider (`config/providers.example.toml`), register a project with `par project add ID PATH` (an ordinary Git repository needs no `AGENTS.md` or `.ai/STATE.md` — that is the default generic mode; `--context-mode harness` keeps the parent-contract rules, and `--scaffold-harness` writes minimal starters only when you ask for them, never overwriting anything that exists), and `par project enable-write ID` when you're ready to let it touch real files. Every write still waits for your explicit per-task approval. Full detail (exit codes, troubleshooting, baseline workflow) is in `docs/OPERATIONS.md`.

## Design boundaries (enforced in code, not just documented)

- Workers never see or touch the real project directly — every attempt runs in a private clone; the Kernel never runs git inside a Worker's own `.git`.
- No API keys in configuration, ever; providers authenticate however their own CLI does (subscription login), and the config schema itself refuses anything that looks like a secret.
- State transitions go through one explicit table, enforced a second time by a database trigger — not just application-level checks.
- A result's own "tests pass" claim is never trusted; only the Kernel's own check runs count.
- Nothing is ever applied to a real project automatically. A result stays a diff until you run `git apply` yourself.

## What's here, in English, vs. what stays in Chinese

This file and this project's commit messages are English. The deep reference docs (`docs/*.md` — security boundaries, the exact state machine, the MCP bridge's tool contracts, operational runbooks) are written in Chinese, since that's the language of the person who has used and tested this daily. They're kept in careful sync with the actual code, so a translation tool or a patient reader gets an accurate picture — contributions that translate specific docs are welcome. [CHANGELOG.md](CHANGELOG.md) has the user-facing summary of what changed and why.

## Honesty about where this stands

- Used daily by one person, on their own projects, with a single-digit-task sample size tracked honestly in `ops/reliability_report.py`'s output — not "battle-tested at scale."
- Two automated Worker adapters exist and are real (Codex CLI, Google Antigravity); other "market-standard" tools are wired in only as a manual hand-off path, explicitly labeled as not-yet-exercised where that's true (see `docs/HANDOFF.md`). Antigravity runs through Google's official `agy` CLI in its headless mode: that mode can change between vendor releases, and the account/terms risk is the user's. It ships disabled and is not the default automated path.
- No GUI. The MCP bridge (16 tools, see `docs/MCP.md`) and the CLI are the entire surface — a deliberate choice after an earlier local web console was removed (see [CHANGELOG.md](CHANGELOG.md)), not an oversight.

## License

[MIT](LICENSE).
