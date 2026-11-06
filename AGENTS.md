<!-- AI_HARNESS_CORE_BEGIN -->
# Agent Entry

- Project truth comes from project files, not tool memory or old chats.
- A separately stored AI Harness Core is a manual process reference, not project truth and not default context. Read it only when the user explicitly references it or a Harness problem requires it.
- For substantial work, read `.ai/STATE.md`.
- If `.ai/tasks/active/` contains a current task, treat it as the execution contract.
- Load other documents only when the current task requires them. Do not recursively read the whole repository or `.ai/`.
- Do not silently expand task scope or rewrite requirements to make implementation easier.
- Do not modify unrelated files.
- Do not create new `MEMORY.md`, `HANDOFF.md`, `SESSION_SUMMARY.md`, `CURRENT_CONTEXT.md`, or `FINAL_PLAN*.md` unless explicitly requested.
- Temporary reasoning belongs in tool/session context; persistent project facts belong in normal project documentation. A current-state fact belongs in `STATE.md` only through a Maintainer-approved update.
- Tool-native persistent memory is non-authoritative and should not be used as project truth.
- Verify with tests, reproducible commands, data checks, or direct observable evidence before claiming completion.
- Stop and report if required input is missing, authoritative sources conflict, or the task unexpectedly requires architecture/interface/schema/destructive changes.
- Keep governance lightweight. Small low-risk edits do not require a new task file.
- `.ai/STATE.md` is Maintainer-owned and is a projection of current reality, not the truth and not project memory. Read and navigate with it, but do not edit it: report `STATE_DRIFT_DETECTED` when it conflicts with stronger evidence, or `STATE_UPDATE_CANDIDATE` when completed work made it stale. Never write task history, a session log, or your own summary into it.
- There is exactly one STATE per project, `<project>/.ai/STATE.md`. Never create a nested or per-module `STATE.md`; use `README.md` or `INDEX.md` for orientation when it is genuinely needed.
- Once execution has begun, do not edit a task's `Goal`, `Scope`, `Forbidden`, `Acceptance`, or `Stop Conditions` to make the work easier. If the contract is wrong, report `TASK_CHANGE_REQUIRED` and stop or wait. You may write `Result` and propose a `STATE_UPDATE_CANDIDATE`.
- Keep at most one current contract in `.ai/tasks/active/`; after acceptance is verified, record the result and checks, then move it to `.ai/tasks/archive/`.
- If a Harness rule repeatedly causes friction or conflicts with observed tool behavior, do not silently bypass it. Capture a concrete failure, propose the smallest scoped change, validate it in a fresh session, and roll it back if recovery or safety gets worse.
<!-- AI_HARNESS_CORE_END -->

## Project-specific rules

<!-- Keep project-specific boundaries outside the managed block. -->

> These rules describe how this project is developed, including the author's private task ledger. `.ai/` and `docs/CHANGELOG.md` are not published in this repository: external contributors should start from `CONTRIBUTING.md`, and the public change summary is the root `CHANGELOG.md`.

- This is the Personal Agent Runtime itself (the tool that runs you as a Worker, not just another project it manages). Python 3.11+, standard library only - no third-party dependencies, no network calls from `src/`.
- Before claiming any change works, run the full suite from the project root: `python3 -B -m unittest discover -s tests -t .` (about 1558 tests, a few minutes). A change to a safety-relevant line (permissions, path policy, redaction, what a tool is allowed to do) also needs `python3 -B tests/tools/mutation_check.py` to pass for the entries it touches.
- No comments in code unless the WHY is non-obvious (a hidden constraint, a workaround, a subtle invariant). Never explain WHAT the code does - names should already say that.
- Keep documentation in sync with behavior in the same change: `docs/specs/*.md` for the exact contract a module implements, `docs/CHANGELOG.md` for what changed and why (the detailed internal log; the published summary is the root `CHANGELOG.md`), `docs/SECURITY.md` when a change adds, removes or narrows something the Runtime can execute or write to.
- Never touch real credentials, never call a real vendor model without it being clearly and explicitly asked for (quota and money are real).
- `allowed_paths` in your contract is the actual boundary - stay inside it even when a related file nearby looks like it needs the same fix; report it instead of widening scope yourself.
- Starting a new task contract: copy `.ai/tasks/TASK_TEMPLATE.md` (the author's private ledger; not published in this repository) and, if delegating to a Worker, `ops/contract_skeleton.json`, rather than writing one from scratch - the boilerplate that was identical across `TASK-002` through `TASK-007` is already filled in there.
- Coordinating *other* registered projects' Workers through `par` (not building this Runtime's own code - that stays direct, not through the task ledger): before submitting/running/deciding on a task, read the current rules with `par coordinator prompt claude_desktop` - the tool list, contract format, dependency splitting, and that approve/reject/accept/close are yours to decide now (recorded as `by="coordinator"`, see `TASK-009`). Do not reconstruct these from memory or an old copy in this conversation; the prompt is generated from the live tool set and changes when it does.
