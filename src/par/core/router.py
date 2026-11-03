"""Deterministic worker routing. No scoring, no model calls, no guessing.

A task is routed from a *snapshot* of the configuration taken when it was submitted (or when a person
explicitly retried it), so editing profiles.toml never silently changes a running or queued task.

The chain for a task is fixed by rule:

1. an explicit ``worker`` in the contract -> that worker only (a person asked for exactly it);
2. else the contract's ``role`` -> the role's primary, then its fallbacks in the configured order;
3. else the profile's ``default_worker``.

Each candidate is checked against facts (provider enabled, real model slug, capability for the risk tier,
an adapter actually registered, no known health problem). Every reason is kept so the UI can say *why*.
Deciding whether a switch to a later candidate may happen on its own is the Kernel's job (policy and risk),
not the router's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from par.config import RuntimeConfig, is_placeholder
from par.core.contract import canonical_json, sha256_text
from par.core.health import Health
from par.providers.registry import AdapterRegistry

AUTOMATIC_KINDS = frozenset({"codex", "antigravity"})   # manual_handoff, claude_host, gemini_legacy never run on their own


def config_snapshot(config: RuntimeConfig, profile_id: "str | None" = None) -> "dict[str, Any]":
    """The slice of configuration a task is locked to. Plain data, so it can sit in the ledger."""
    pid = profile_id or config.active_profile
    profile = config.profiles.get(pid) if pid else None
    snap: "dict[str, Any]" = {
        "profile": pid,
        "worker_failure_policy": profile.worker_failure_policy if profile else "ask",
        "default_worker": profile.default_worker if profile else None,
        "roles": {r: {"primary": e.primary, "fallback": list(e.fallback)} for r, e in (profile.roles.items() if profile else [])},
        "workers": {w.id: {"provider": w.provider, "model": w.model, "capabilities": list(w.capabilities)}
                    for w in config.workers.values()},
        "providers": {p.id: {"kind": p.kind, "enabled": p.enabled} for p in config.providers.values()},
    }
    snap["digest"] = sha256_text(canonical_json({k: v for k, v in snap.items() if k != "digest"}))
    return snap


@dataclass(frozen=True)
class Candidate:
    worker_id: str
    provider_id: "str | None"
    model: "str | None"
    usable: bool
    reasons: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class Route:
    source: str                          # worker_override | role | default_worker | none
    chain: "tuple[Candidate, ...]"
    problem: str = ""                    # why the chain is empty, if it is

    @property
    def chosen(self) -> "Candidate | None":
        return next((c for c in self.chain if c.usable), None)

    @property
    def primary(self) -> "Candidate | None":
        return self.chain[0] if self.chain else None

    @property
    def needs_switch(self) -> bool:
        """The first-choice worker cannot run but a later one can."""
        return bool(self.chain) and not self.chain[0].usable and self.chosen is not None

    def explain(self) -> str:
        if not self.chain:
            return self.problem or "no worker is configured for this task"
        return "; ".join(f"{c.worker_id}: {'ok' if c.usable else ', '.join(c.reasons)}" for c in self.chain)


def evaluate(snapshot: "Mapping[str, Any]", worker_id: str, *, risk: str, adapters: AdapterRegistry,
             health: Health, exclude: "Iterable[str]" = ()) -> Candidate:
    worker = snapshot["workers"].get(worker_id)
    if worker is None:
        return Candidate(worker_id, None, None, False, ("the worker is not defined in the configuration this task is locked to",))
    reasons: "list[str]" = []
    provider_id, model = worker["provider"], worker["model"]
    provider = snapshot["providers"].get(provider_id)
    if provider is None:
        reasons.append(f"provider {provider_id!r} is not defined")
    else:
        if not provider["enabled"]:
            reasons.append(f"provider {provider_id!r} is disabled")
        if provider["kind"] not in AUTOMATIC_KINDS:
            reasons.append(f"provider kind {provider['kind']!r} is never run automatically")
    if is_placeholder(model):
        reasons.append("the model slug is still a placeholder")
    need = "edit" if risk == "R2" else "read"
    if need not in worker["capabilities"]:
        reasons.append(f"the worker lacks the {need!r} capability")
    if not adapters.has(provider_id):
        reasons.append(f"no adapter is registered for {provider_id!r}")
    verdict = health.verdict(provider_id, model)
    if not verdict.usable:
        reasons.append(verdict.describe())
    if worker_id in set(exclude):
        reasons.append("already tried for this task")
    return Candidate(worker_id, provider_id, model, not reasons, tuple(reasons))


def route(snapshot: "Mapping[str, Any]", *, role: "str | None", worker: "str | None", risk: str,
          adapters: AdapterRegistry, health: Health, exclude: "Iterable[str]" = ()) -> Route:
    if worker:
        source, ids, problem = "worker_override", [worker], ""
    elif role:
        entry = snapshot["roles"].get(role)
        if entry is None and not snapshot.get("profile"):
            source, ids, problem = "role", [], ("no profile is configured yet: copy config/profiles.example.toml to "
                                                "<data dir>/config/profiles.toml (see docs/OPERATIONS.md)")
        elif entry is None:
            source, ids, problem = "role", [], f"role {role!r} is not configured in profile {snapshot.get('profile')!r}"
        else:
            source, ids, problem = "role", [entry["primary"], *entry["fallback"]], ""
    elif snapshot.get("default_worker"):
        source, ids, problem = "default_worker", [snapshot["default_worker"]], ""
    else:
        source, ids, problem = "none", [], "the task names no worker or role and the profile has no default worker"
    seen: "list[str]" = []
    for wid in ids:                                     # a worker listed twice is checked once
        if wid not in seen:
            seen.append(wid)
    chain = tuple(evaluate(snapshot, wid, risk=risk, adapters=adapters, health=health, exclude=exclude) for wid in seen)
    return Route(source, chain, problem)
