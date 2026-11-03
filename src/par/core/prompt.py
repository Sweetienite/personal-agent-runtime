"""The exact text a Worker is given. Deterministic: the same contract and context always yield the same prompt.

The prompt states the task, the boundary and how the result will be judged. It does not carry hidden
history from any earlier session, and it never includes credentials (contracts containing them are refused).
"""
from __future__ import annotations

from par.core.contract import Contract


def build_prompt(contract: Contract, *, task_id: str, attempt: int, context_text: str, workspace_has_files: bool,
                 handoff_text: "str | None" = None) -> str:
    lines = [f"# Task {task_id} (attempt {attempt}): {contract.title}",
             f"Project: {contract.project_id}    Risk tier: {contract.risk}", "",
             "## What to do", contract.description.strip(), ""]
    if contract.criteria:
        lines += ["## Acceptance criteria", *[f"- {c}" for c in contract.criteria], ""]
    if contract.checks:
        lines += ["## How this will be checked",
                  "The Kernel runs these itself after you finish. Your own statement about tests is not used.",
                  *[f"- {c['name']}: {' '.join(c['argv'])}" for c in contract.checks], ""]
    lines += ["## Boundaries"]
    if contract.writes:
        lines += [f"- You may create or change files only under: {', '.join(contract.allowed_paths)}",
                  "- Everything else in this directory is read-only for you."]
    elif workspace_has_files:
        lines += ["- This task is read-only: do not create, change or delete any file."]
    else:
        lines += ["- You have no project files. Answer from the task description and the context below; do not create files."]
    lines += ["- Never touch .git, the .ai/ folder, AGENTS.md, CLAUDE.md, credentials, key files or anything outside this directory.",
              "- Do not ask for or use extra permissions. If you are blocked, stop and say what blocked you.", "",
              "## Reporting",
              "End your reply with a plain-language summary for a person (not treated as proof - the Kernel measures the actual files):",
              "- Name every file you changed and what changed in it.",
              "- If you changed nothing, say so explicitly and why. Never end without saying what happened.", ""]
    if handoff_text:                                   # a relay: facts the Kernel measured about the previous attempt (never its Worker's words)
        lines += [handoff_text.rstrip(), ""]
    lines += ["## Project context", context_text.rstrip(), ""]
    return "\n".join(lines)
