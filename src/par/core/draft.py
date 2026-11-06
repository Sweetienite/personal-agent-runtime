"""Deterministic TaskDraft -> strict contract compiler.

A draft carries only what a person or a coordinator actually knows: the project, what to do, which paths may
change, and how the result will be checked. Everything mechanical (schema version, role default, ad-hoc flag,
idempotency key) is derived here from the registered project and the active configuration.

The compiler never guesses a safety decision: a missing path, a missing check for a write task, an unknown
project or worker, or a Harness project without exactly one active contract produces `NEEDS_DECISION` with a
short question instead of a contract. It reads no files from the project (so a README cannot inject a command)
and makes no model call; `parse_contract` remains the final authority on the result."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from par.config import ProjectEntry, RuntimeConfig
from par.core.contract import ContractError, canonical_json, parse_contract
from par.execution.validation import resolve_executable

READY, NEEDS_DECISION = "READY", "NEEDS_DECISION"
_DRAFT_KEYS = frozenset({"project_id", "title", "description", "risk", "allowed_paths", "acceptance", "role", "worker",
                         "timeouts", "dependencies", "ad_hoc", "idempotency_key"})


@dataclass(frozen=True)
class DraftResult:
    status: str
    contract: "dict[str, Any] | None" = None
    questions: "tuple[str, ...]" = ()
    warnings: "tuple[str, ...]" = ()

    def contract_json(self) -> str:
        return canonical_json(self.contract) if self.contract is not None else ""


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def compile_draft(draft: Mapping[str, Any], *, config: RuntimeConfig, projects: "Mapping[str, ProjectEntry]",
                  contract_state: "str | None" = None) -> DraftResult:
    """Compile one draft. ``contract_state`` is the project's ``none|one|multiple`` parent-contract state,
    read by the caller; it is required for a Harness project and ignored for a generic one."""
    if not isinstance(draft, Mapping):
        return DraftResult(NEEDS_DECISION, questions=("the draft must be a JSON object",))
    unknown = sorted(set(draft) - _DRAFT_KEYS)
    if unknown:
        return DraftResult(NEEDS_DECISION, questions=(f"unknown draft field(s): {', '.join(unknown)}; remove them",))

    questions: "list[str]" = []
    warnings: "list[str]" = []

    project_id = draft.get("project_id")
    project = projects.get(project_id) if isinstance(project_id, str) else None
    if project is None:
        known = ", ".join(sorted(projects)) or "none"
        questions.append(f"which registered project? (project_id; known: {known})")
    if not _nonempty(draft.get("title")):
        questions.append("a one-line title is required")
    if not _nonempty(draft.get("description")):
        questions.append("a description of the change/analysis is required")
    risk = draft.get("risk")
    if risk not in ("R0", "R1", "R2"):
        questions.append("risk is required: R0 (read-only), R1 (read-only analysis) or R2 (change files); R3 is never run")

    acceptance = draft.get("acceptance")
    criteria = acceptance.get("criteria") if isinstance(acceptance, Mapping) else None
    checks = acceptance.get("checks") if isinstance(acceptance, Mapping) else None
    if not isinstance(criteria, list) or not criteria or not all(_nonempty(c) for c in criteria):
        questions.append("acceptance.criteria needs at least one line saying what a good result is")
    if risk == "R2":
        if not isinstance(checks, list) or not checks:
            questions.append("which command proves the change? acceptance.checks is required for a write task")
    elif risk in ("R0", "R1") and checks:
        questions.append("read-only tasks cannot have acceptance.checks; remove them")
    if risk == "R2" and isinstance(checks, list):
        for check in checks:
            argv = check.get("argv") if isinstance(check, Mapping) else None
            if isinstance(argv, list) and argv and isinstance(argv[0], str) and resolve_executable(argv[0]) is None:
                questions.append(f"check {check.get('name', '?')!r}: {argv[0]!r} is not a program the Kernel can run "
                                 "(python/python3, or a program in its trusted directories)")

    paths = draft.get("allowed_paths")
    if risk == "R2":
        if not isinstance(paths, list) or not paths or not all(_nonempty(p) for p in paths):
            questions.append("which exact paths may the Worker change? allowed_paths is required for a write task "
                             "(a file or a prefix such as app/; '.' is not accepted)")
    elif risk in ("R0", "R1") and paths:
        questions.append("read-only tasks cannot declare allowed_paths; remove them")

    profile = config.profiles.get(config.active_profile) if config.active_profile else None
    role = draft.get("role")
    if profile is None:
        questions.append("no active profile is configured; configure profiles.toml first (par doctor shows what is missing)")
    elif role is None:
        if "code" in profile.roles:
            role = "code"
            warnings.append("role defaulted to 'code' from the active profile")
        elif len(profile.roles) == 1:
            role = next(iter(profile.roles))
            warnings.append(f"role defaulted to the only configured role, {role!r}")
        else:
            questions.append(f"which role? configured roles: {', '.join(sorted(profile.roles))}")
    elif not isinstance(role, str) or role not in profile.roles:
        questions.append(f"role {role!r} is not in profile {profile.id}; available: {', '.join(sorted(profile.roles))}")

    worker = draft.get("worker")
    if worker is not None and (not isinstance(worker, str) or worker not in config.workers):
        questions.append(f"worker {worker!r} is not defined in the current configuration")
    dependencies = draft.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(_nonempty(d) for d in dependencies):
        questions.append("dependencies must be a list of task ids")
    timeouts = draft.get("timeouts")
    if timeouts is not None and not isinstance(timeouts, Mapping):
        questions.append("timeouts must be an object with startup/idle/total seconds")
    key = draft.get("idempotency_key")
    if key is not None and not isinstance(key, str):
        questions.append("idempotency_key must be a string")

    if project is not None and risk in ("R0", "R1", "R2"):
        declared = draft.get("ad_hoc")
        if project.context_mode == "generic":
            if declared is False:
                questions.append("this is a generic project: it has no parent contract, so ad_hoc must be true")
            ad_hoc = True
        elif contract_state is None:
            questions.append(f"cannot tell whether {project.id!r} has an active parent contract; run `par project show {project.id}`")
            ad_hoc = None
        elif contract_state == "multiple":
            questions.append(f"{project.id!r} has several active contracts; reconcile the project first")
            ad_hoc = None
        elif contract_state == "one":
            if declared is True:
                questions.append(f"{project.id!r} has an active contract: submit under it (ad_hoc must be false)")
            ad_hoc = False
        else:
            if risk == "R2":
                questions.append(f"a Harness R2 task needs an active parent contract; {project.id!r} has none")
                ad_hoc = None
            else:
                if declared is False:
                    questions.append(f"{project.id!r} has no active contract: set ad_hoc=true for this read-only task")
                ad_hoc = True
    else:
        ad_hoc = None

    if questions:
        return DraftResult(NEEDS_DECISION, questions=tuple(questions), warnings=tuple(warnings))

    contract: "dict[str, Any]" = {
        "schema_version": 1, "project_id": project.id, "title": draft["title"].strip(),
        "description": draft["description"].strip(), "risk": risk,
        "allowed_paths": list(paths) if risk == "R2" else [],
        "acceptance": {"criteria": [c.strip() for c in criteria], "checks": checks if risk == "R2" else []},
        "role": role, "dependencies": list(dependencies), "ad_hoc": bool(ad_hoc),
    }
    if worker is not None:
        contract["worker"] = worker
    if timeouts is not None:
        contract["timeouts"] = dict(timeouts)
    if key is not None:
        contract["idempotency_key"] = key
    else:
        digest = hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()
        contract["idempotency_key"] = f"draft-{digest[:24]}"
        warnings.append("idempotency_key was derived from the contract content; supply one to submit the same text again")
    try:
        parse_contract(canonical_json(contract))
    except ContractError as exc:
        return DraftResult(NEEDS_DECISION, questions=tuple(exc.problems), warnings=tuple(warnings))
    return DraftResult(READY, contract=contract, warnings=tuple(warnings))
