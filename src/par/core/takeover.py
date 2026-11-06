"""Coordinator hand-over (plan 5.4, 6.3, T22): a read-only *takeover packet* and an explicit, recorded switch of who coordinates.

The packet is written for the next coordinator (for example Codex loaded by hand when the Claude session is gone). It holds only what the Kernel
measured - the task tree, statuses, the latest verification facts, pending decisions, pointers to the Harness files the project already has - and no
conversation, no worker text, no reasoning. Tasks that are already accepted are listed as finished: switching coordinators never re-runs them.

``takeover_confirm`` records who coordinates from now on (one event). A coordinator-tagged submission from anyone else is refused, so two
coordinators cannot schedule against the same ledger at once.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any, Optional

from par import coordinators as registry
from par.core.attempt import private_dirs, write_private
from par.core.clock import parse_iso
from par.core.states import TaskStatus as T

TAKEOVER_ID = re.compile(r"tk-[0-9]{8}T[0-9]{6}-[0-9a-f]{4}\Z")
DONE = frozenset({T.ACCEPTED.value, T.CLOSED.value})
DECISION = frozenset({T.PERMISSION_WAIT.value, T.REVIEW_REQUIRED.value, T.RECOVERY_REQUIRED.value, T.PAUSED.value, T.BLOCKED_NO_WORKER.value,
                      T.AUTH_REQUIRED.value, T.QUOTA_EXHAUSTED.value, T.RATE_LIMITED.value, T.FAILED.value, T.INTERRUPTED.value})
MAX_TASKS = 400
MAX_POINTERS = 40


def _clip(value: Any, limit: int) -> str:
    text = "".join(ch if (ch == " " or (ord(str(ch)) >= 32 and ord(str(ch)) != 127)) else "?" for ch in str(value if value is not None else ""))
    return text[:limit]


INSTRUCTIONS = [
    "You are taking over as coordinator of a Personal Agent Runtime. The Kernel and its ledger are independent of any chat: nothing was lost when the previous session ended.",
    "This packet holds facts the Kernel measured. It contains no conversation and no worker text. Do not assume anything that is not in it.",
    "Tasks under `finished` are accepted or closed: do not run them again.",
    "Decisions (`par task approve|reject|accept|retry ...`) are the human user's. Do not take them for the user.",
    "Read the Harness files listed under `pointers` yourself (they are the project's authority); do not copy their content into new files.",
    "Start with: par task list ; par task status TASK ; par health",
    "When the human has chosen you, they run: par takeover confirm TAKEOVER_ID --as COORDINATOR",
]


class TakeoverMixin:
    """Methods of :class:`par.core.kernel.Kernel`."""

    def coordinator_lease(self) -> "dict[str, Any]":
        """Who coordinates now (canonical id): the last confirmed takeover, else the active profile's configured coordinator."""
        for e in self.store.recent_events_of("TAKEOVER_CONFIRMED", 1):
            return {"coordinator": registry.normalize(e["data"].get("coordinator")) or e["data"].get("coordinator"),
                    "takeover_id": e["data"].get("takeover_id"), "since": e["ts"]}
        profile = self.config.profiles.get(self.config.active_profile) if self.config.active_profile else None
        name = profile.coordinator if profile else None
        return {"coordinator": registry.normalize(name) or name, "takeover_id": None, "since": None}

    def coordinator_seen(self, coordinator: str, *, min_interval: float = 60.0) -> bool:
        """Note that a coordinator just used the bridge (at most one event a minute each: this is a heartbeat, not a log). True when written."""
        cid = registry.normalize(coordinator)
        if cid is None:
            return False
        for e in self.store.recent_events_of("COORDINATOR_SEEN", 20):
            if e["data"].get("coordinator") == cid:
                if (self.clock.now() - parse_iso(e["ts"])).total_seconds() < min_interval:
                    return False
                break
        with self.store.tx():
            self.store.add_event("COORDINATOR_SEEN", reason=f"{cid} used the bridge", data={"coordinator": cid})
        return True

    def coordinator_status(self) -> "dict[str, Any]":
        """The coordinator picture for a person: who coordinates, who has been seen, what the configuration suggests, the last switches."""
        lease = self.coordinator_lease()
        seen: "dict[str, str]" = {}
        for e in self.store.recent_events_of("COORDINATOR_SEEN", 200):
            seen.setdefault(str(e["data"].get("coordinator")), e["ts"])
        profile = self.config.profiles.get(self.config.active_profile) if self.config.active_profile else None
        history = [{"at": e["ts"], "from": e["data"].get("previous"), "to": e["data"].get("coordinator"),
                    "takeover_id": e["data"].get("takeover_id"), "note": e["data"].get("note", "")}
                   for e in self.store.recent_events_of("TAKEOVER_CONFIRMED", 5)]
        current = registry.COORDINATORS.get(lease["coordinator"] or "")
        return {
            "current": {"id": lease["coordinator"], "name": current.name if current else None, "since": lease["since"], "takeover_id": lease["takeover_id"]},
            "configured": {"default": registry.normalize(profile.coordinator) if profile else None,
                           "fallback": registry.normalize(profile.fallback_coordinator) if profile and profile.fallback_coordinator else None},
            "coordinators": [{"id": c.id, "name": c.name, "vendor": c.vendor, "surfaces": list(c.surfaces), "connection": "mcp",
                              "surface": c.surface, "capabilities": list(c.capabilities),
                              "last_seen": seen.get(c.id), "is_current": c.id == lease["coordinator"]} for c in registry.COORDINATORS.values()],
            "history": history}

    def coordinator_switch(self, to: str, *, note: str = "") -> "dict[str, Any]":
        """The person's switch of coordinator: write the takeover packet and confirm it in one step (typing the command IS the confirmation)."""
        from par.core.kernel import Refused
        target = registry.normalize(to)
        if target is None:
            raise Refused("unknown_coordinator", f"{to!r} is not a known coordinator ({', '.join(sorted(registry.COORDINATORS))})")
        current = self.coordinator_lease()["coordinator"]
        if current == target:
            return {"switched": False, "from": current, "to": target, "takeover_id": None, "packet": None}
        packet = self.takeover_export()
        done = self.takeover_confirm(packet["takeover_id"], coordinator=target, note=note)
        return {"switched": True, "from": done["previous"], "to": target, "takeover_id": packet["takeover_id"],
                "packet": {"markdown": packet["markdown"], "json": packet["json"], "tasks": packet["tasks"]}}

    def takeover_export(self, *, project_id: "str | None" = None) -> "dict[str, Any]":
        from par.core.kernel import Refused
        if project_id is not None and project_id not in self.projects:
            raise Refused("unknown_project", f"project {project_id!r} is not registered")
        rows = self.store.list_tasks(None, project_id)[:MAX_TASKS]
        tasks: "list[dict[str, Any]]" = []
        for row in rows:
            d = self.describe(row["task_id"])
            latest = self.result(row["task_id"])
            entry: "dict[str, Any]" = {
                "task_id": d["task_id"], "project_id": d["project_id"], "title": _clip(d["title"], 160), "risk": d["risk"], "role": d["role"],
                "status": d["status"], "attempts": d["attempt_count"], "worker": d["pinned_worker"] or d["worker_requested"],
                "dependencies": [x["task_id"] for x in d["dependencies"]], "last_problem": _clip(d["last_failure"], 300) if d["last_failure"] else None,
                "pending_approvals": [{"id": a["id"], "kind": a["kind"]} for a in d["pending_approvals"]],
                "human_next": [a for a in d["next_actions"]],
            }
            if latest:
                entry["latest_attempt"] = {
                    "n": latest["attempt"], "outcome": latest["outcome"], "failure_class": latest["failure_class"], "stage": latest["stage"],
                    "checks": [{"name": _clip(t["name"], 64), "status": t["status"]} for t in latest["tests"]],
                    "diff_stat": latest["diff_stat"], "boundary_violations": latest["boundary"]["violations"], "partial": latest["partial"],
                }
            tasks.append(entry)
        pointers: "list[dict[str, Any]]" = []
        for pid in sorted({t["project_id"] for t in tasks} | ({project_id} if project_id else set())):
            project = self.projects.get(pid)
            if project is None:
                continue
            try:
                manifest = self._context(project).manifest()
            except Exception:                                    # a project that cannot be read right now must not stop the packet
                pointers.append({"project_id": pid, "error": "the project's Harness files could not be read"})
                continue
            pointers.append({"project_id": pid, "base_commit": manifest.base_git_commit, "state_revision": manifest.state_revision,
                             "parent_contract": manifest.parent_contract_ref,
                             "entries": [{"kind": e.kind, "path": e.path, "sha256": e.sha256} for e in manifest.entries][:MAX_POINTERS]})
        lease = self.coordinator_lease()
        takeover_id = "tk-" + self.clock.iso().replace("-", "").replace(":", "").split(".")[0].rstrip("Z") + "-" + secrets.token_hex(2)
        doc = {
            "schema": 1, "kind": "takeover", "takeover_id": takeover_id, "created_at": self.clock.iso(),
            "from_coordinator": lease["coordinator"], "profile": self.config.active_profile,
            "finished": [t["task_id"] for t in tasks if t["status"] in DONE],
            "needs_a_decision": [t["task_id"] for t in tasks if t["status"] in DECISION],
            "in_flight": [t["task_id"] for t in tasks if t["status"] not in DONE and t["status"] not in DECISION],
            "tasks": tasks, "pointers": pointers, "pending_approvals": [{"task_id": t["task_id"], **a} for t in tasks for a in t["pending_approvals"]],
            "instructions": INSTRUCTIONS,
        }
        text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)
        folder = self.data_dir / "takeover" / takeover_id
        private_dirs(folder, self.data_dir)
        write_private(folder / "takeover.json", text)
        write_private(folder / "takeover.md", _render(doc))
        with self.store.tx():
            self.store.add_event("TAKEOVER_EXPORTED", reason=f"takeover packet {takeover_id} written ({len(tasks)} task(s))",
                                 data={"takeover_id": takeover_id, "tasks": len(tasks), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                       "project": project_id})
        return {"takeover_id": takeover_id, "folder": str(folder), "json": str(folder / "takeover.json"), "markdown": str(folder / "takeover.md"),
                "tasks": len(tasks), "finished": len(doc["finished"]), "needs_a_decision": len(doc["needs_a_decision"]), "from": lease["coordinator"]}

    def takeover_confirm(self, takeover_id: str, *, coordinator: str, note: str = "") -> "dict[str, Any]":
        from par.core.kernel import NotAllowed, Refused
        if not TAKEOVER_ID.match(takeover_id or ""):
            raise Refused("bad_takeover_id", "that is not a takeover id (they look like tk-20261015T101500-ab12)")
        canonical = registry.normalize(coordinator)
        if canonical is None:
            raise Refused("unknown_coordinator", f"{coordinator!r} is not a known coordinator ({', '.join(sorted(registry.COORDINATORS))})")
        coordinator = canonical                                 # the older name codex_manual_takeover is recorded as codex
        events = [e for e in self.store.events() if e["type"] in ("TAKEOVER_EXPORTED", "TAKEOVER_CONFIRMED")]
        exported = [e for e in events if e["type"] == "TAKEOVER_EXPORTED"]
        if not exported or exported[-1]["data"].get("takeover_id") != takeover_id:
            raise NotAllowed("stale_takeover", "only the newest takeover packet can be confirmed (export a fresh one if the project moved on)")
        if any(e["type"] == "TAKEOVER_CONFIRMED" and e["data"].get("takeover_id") == takeover_id for e in events):
            raise NotAllowed("already_confirmed", f"{takeover_id} was already confirmed")
        previous = self.coordinator_lease()["coordinator"]
        with self.store.tx():
            self.store.add_event("TAKEOVER_CONFIRMED", reason=f"{coordinator} coordinates from now on (was {previous})",
                                 data={"takeover_id": takeover_id, "coordinator": coordinator, "previous": previous, "note": (note or "")[:200]})
        return {"takeover_id": takeover_id, "coordinator": coordinator, "previous": previous}


def _render(doc: "dict[str, Any]") -> str:
    lines = [f"# Takeover packet {doc['takeover_id']}", "", f"Written {doc['created_at']} by the Kernel. Coordinating until now: {doc['from_coordinator']}. Profile: {doc['profile']}.", "",
             "## How to use this", *[f"- {x}" for x in doc["instructions"]], ""]
    for title, key in (("Finished (do not run again)", "finished"), ("Waiting for a decision or stopped", "needs_a_decision"), ("In flight or queued", "in_flight")):
        ids = doc[key]
        lines += [f"## {title}: {len(ids)}"]
        for t in (t for t in doc["tasks"] if t["task_id"] in ids):
            extra = f" - {t['last_problem']}" if t.get("last_problem") else ""
            checks = ""
            if t.get("latest_attempt") and t["latest_attempt"]["checks"]:
                checks = " [checks: " + ", ".join(f"{c['name']}={c['status']}" for c in t["latest_attempt"]["checks"]) + "]"
            lines.append(f"- {t['task_id']} {t['status']} {t['risk']} {t['title']}{extra}{checks}")
        lines.append("")
    if doc["pending_approvals"]:
        lines += ["## Pending approvals (the human decides)", *[f"- {a['task_id']} #{a['id']} {a['kind']}" for a in doc["pending_approvals"]], ""]
    lines += ["## Pointers to the project's own Harness files (read them there)"]
    for p in doc["pointers"]:
        if "error" in p:
            lines.append(f"- {p['project_id']}: {p['error']}")
            continue
        lines.append(f"- {p['project_id']}: base {str(p['base_commit'])[:12]}, STATE revision {str(p['state_revision'])[:12]}")
        lines += [f"  - {e['kind']}: {e['path']} ({str(e['sha256'] or '-')[:12]})" for e in p["entries"]]
    lines.append("")
    return "\n".join(lines)
