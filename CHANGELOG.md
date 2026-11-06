# Changelog

User-facing summary of real changes. The detailed, line-by-line engineering log this project keeps for its own development (every behavior change, why it was made, what test proves it) is not part of this public repository — this file is the readable summary of that history.

## Unreleased

Documentation and metadata corrections only — no behavior change:

- Install commands in `README.md`, `README.en.md`, `docs/USER_GUIDE.md` and `docs/OPERATIONS.md` no longer assume the author's `~/Documents/Personal Agent Runtime`; they start from `git clone` and a checkout of your own.
- `pyproject.toml`'s description now says what the package does, instead of naming an internal phase and claiming "no real model calls".
- `AGENTS.md` points at files this repository actually publishes (root `CHANGELOG.md`; `.ai/` is the author's private task ledger) and says so explicitly.
- The Antigravity account/terms limitation is stated where the adapters are introduced, and `USER_GUIDE.md` no longer describes the removed web console.
- An ordinary Git repository can now be registered without the Harness convention: `par project add` defaults to `context_mode = "generic"`, which reads `README.md` / `AGENTS.md` / `CLAUDE.md` (when present) and the Git facts, and accepts an `ad_hoc` R2 contract once writes are switched on and the base is clean. `--context-mode harness` keeps the old parent-contract rules; registry entries written before the field keep the Harness behaviour.

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
