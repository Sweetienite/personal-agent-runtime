"""The Result Envelope and the rules for accepting it.

Everything under ``tests`` and ``boundary`` is measured by the Kernel. ``summary`` is the Worker's own
statement: it is kept (redacted, bounded) because a person may want to read it, and it is always marked
unverified.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from par import schema
from par.core.redact import redact_text
from par.providers.base import ProviderEvent

SUMMARY_MAX = 1200
NO_SUMMARY = "(the worker gave no summary)"
EVIDENCE_PATHS_MAX = 10
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_REVIEW = frozenset({"NOT_REVIEWED", "HUMAN_REVIEW", "COORDINATOR_REVIEW", "UNKNOWN"})
_INTEGRATION = frozenset({"NOT_INTEGRATED", "CANDIDATE_READY", "CANDIDATE_FAILED", "INTEGRATED", "UNKNOWN"})


def worker_summary(events: "Iterable[ProviderEvent]") -> str:
    """The Worker's last message, as an unverified statement. A CLI that reports its final answer on its terminal event
    (agy's ``result.response``) puts it in that event's ``text``; it counts like a message."""
    text = ""
    for ev in events:
        if ev.kind in ("message", "terminal"):
            value = ev.data.get("text")
            if isinstance(value, str) and value.strip():
                text = value.strip()
    if not text:
        return NO_SUMMARY
    return redact_text(text)[:SUMMARY_MAX]


def observed_model(events: "Iterable[ProviderEvent]") -> "str | None":
    """A model name the vendor reported about itself, if any (never the model we asked for)."""
    found = None
    for ev in events:
        value = ev.data.get("model")
        if isinstance(value, str) and 0 < len(value) <= 100:
            found = redact_text(value)
    return found


USAGE_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")
MAX_WARNINGS = 5


def worker_usage(events: "Iterable[ProviderEvent]") -> "dict[str, int] | None":
    """Token counts the vendor itself reported on its terminal event (never estimated, never summed by us)."""
    found = None
    for ev in events:
        usage = ev.data.get("usage") if ev.kind in ("terminal", "usage") else None
        if isinstance(usage, Mapping):
            clean = {k: usage[k] for k in USAGE_KEYS if isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool) and usage[k] >= 0}
            if clean:
                found = clean
    return found


def worker_warnings(events: "Iterable[ProviderEvent]") -> "list[str]":
    """Notes the adapter wants a person to see (declined commands, unexpected tools, retries ...), redacted and bounded. They come from
    ``warning`` events and from the ``warnings`` list a terminal event may carry."""
    out: "list[str]" = []
    for ev in events:
        notes: "list[Any]" = []
        if ev.kind == "warning":
            notes = [ev.data.get("message")]
        elif ev.kind == "terminal" and isinstance(ev.data.get("warnings"), list):     # a result event that lists what it skipped (agy: denied_actions)
            notes = list(ev.data["warnings"])
        for msg in notes:
            if isinstance(msg, str) and msg.strip():
                text = redact_text(msg.strip())[:300]
                if text not in out:
                    out.append(text)
    return out[:MAX_WARNINGS]


def reset_time(events: "Iterable[ProviderEvent]") -> "str | None":
    """When the vendor says a limit resets (an ISO timestamp an adapter parsed from an official message), if it did."""
    found = None
    for ev in events:
        value = ev.data.get("reset_at")
        if isinstance(value, str) and 10 <= len(value) <= 40:
            found = value
    return found


def build_envelope(**fields: Any) -> "dict[str, Any]":
    """Assemble the envelope; ``summary_verified`` is not a parameter because it can only be false."""
    return {**fields, "schema_version": 1, "summary_verified": False}


def envelope_problems(envelope: Mapping[str, Any]) -> "list[str]":
    return schema.check(dict(envelope), "result")


# --- evidence, derived from the envelope -------------------------------------------------------------------------
def _safe(value: Any, limit: int = 200) -> str:
    """Anything taken from a result may be old or hand-made: redact it, strip control characters, cap it."""
    text = redact_text(str(value))
    return _CONTROL.sub("", text)[:limit]


def _check_inputs(checks: "Iterable[Mapping[str, Any]]") -> "set[str]":
    """Files a check runs, when its argv names them. Module names (tests.unit.test_x) become paths too."""
    out: "set[str]" = set()
    for spec in checks:
        argv = spec.get("argv") if isinstance(spec, Mapping) else None
        if not isinstance(argv, list):
            continue
        for token in argv[1:]:
            if not isinstance(token, str):
                continue
            value = token.strip()
            while value.startswith("./"):
                value = value[2:]
            if not value or any(ch in value for ch in "*?[") or value.startswith("-"):
                continue
            if value.startswith(("tests/", "test/", "spec/")):
                out.add(value)
            elif value.endswith(".py") and "/" in value:
                out.add(value)
            elif value.startswith(("tests.", "test.")) and "/" not in value:
                out.add(value.replace(".", "/") + ".py")
    return out


def _looks_like_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (path.startswith(("tests/", "test/", "spec/")) or name.startswith("test_")
            or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts")) or name == "conftest.py")


def evidence_summary(result: "Mapping[str, Any] | None", *, contract_checks: "Iterable[Mapping[str, Any]]" = (),
                     changed_paths: "Iterable[str] | None" = None, worker_can_edit_checks: bool = False,
                     review: str = "NOT_REVIEWED", status: "str | None" = None,
                     integration: str = "NOT_INTEGRATED") -> "dict[str, Any]":
    """A layered, derived view of one result - never a new source of truth and never a claim that work is complete.

    ``tests`` and ``boundary`` are the Kernel's own measurements. ``claim`` is the Worker's statement and stays
    unverified. ``level`` says which of those a person is looking at, so ``REVIEW_REQUIRED`` or ``ACCEPTED`` cannot be
    read as "the feature is done": nothing is applied to the project, and integration is its own question.
    """
    result = result if isinstance(result, Mapping) else {}
    checks = [c for c in contract_checks if isinstance(c, Mapping)]
    names = [str(c["name"]) for c in checks if isinstance(c.get("name"), str) and c["name"]]
    tests = [t for t in result.get("tests", []) if isinstance(t, Mapping)] if isinstance(result.get("tests"), list) else []
    by_name = {str(t.get("name")): str(t.get("status")) for t in tests if t.get("name") is not None}
    failed = sorted(name for name, state in by_name.items() if state != "PASS")
    never_run = [name for name in names if name not in by_name]
    outcome = result.get("outcome")
    paths = [str(p) for p in changed_paths] if changed_paths is not None else None
    inputs = _check_inputs(checks)
    inputs_edited = sorted(inputs & set(paths)) if paths is not None else []
    test_files_changed = sorted(p for p in (paths or []) if _looks_like_test_file(p))[:EVIDENCE_PATHS_MAX]
    edited = bool(inputs_edited or test_files_changed)

    raw_boundary = result.get("boundary")
    if isinstance(raw_boundary, Mapping):
        violations = raw_boundary.get("violations") if isinstance(raw_boundary.get("violations"), int) else 0
        sensitive = raw_boundary.get("sensitive") if isinstance(raw_boundary.get("sensitive"), int) else 0
        boundary_status = "VIOLATIONS" if violations else ("EXCLUSIONS" if sensitive else "CLEAN")
        raw_paths = raw_boundary.get("paths") if isinstance(raw_boundary.get("paths"), list) else []
        boundary = {"status": boundary_status, "violations": violations, "sensitive": sensitive,
                    "paths": [_safe(p, 200) for p in raw_paths][:EVIDENCE_PATHS_MAX]}
    else:
        boundary = {"status": "UNKNOWN", "violations": 0, "sensitive": 0, "paths": []}

    if not result or outcome is None:
        checks_status, level = "NOT_RUN", "UNKNOWN"
    elif outcome in ("interrupted", "canceled", "orphaned"):
        checks_status, level = "NOT_COMPLETED", "INCOMPLETE"
    elif outcome == "failed":
        checks_status = ("FAILED" if (failed or never_run) else "PASSED" if tests else "NOT_RUN")
        level = "FAILED"
    elif outcome != "completed" or bool(result.get("partial")):
        checks_status, level = "NOT_COMPLETED", "INCOMPLETE"
    elif not names and not tests:
        checks_status, level = "NO_MECHANICAL_CHECK", "UNVERIFIED"
    elif failed or never_run:
        checks_status, level = "FAILED", "FAILED"
    elif edited:
        checks_status, level = "PASSED_CHECKS_EDITED", "MEASURED_WITH_EDITED_CHECKS"
    else:
        checks_status, level = "PASSED", "MEASURED"
    if boundary["status"] == "VIOLATIONS":
        level = "FAILED"
    elif boundary["status"] == "UNKNOWN" and level == "MEASURED":
        level = "UNKNOWN"

    untested = [f"check {name!r} never ran" for name in never_run]
    if outcome == "completed" and not result.get("partial") and not names and not tests:
        untested = ["the contract has no acceptance.checks; nothing mechanical backs this result"]

    review = review if review in _REVIEW else "UNKNOWN"
    integration = integration if integration in _INTEGRATION else "UNKNOWN"
    return {
        "level": level,
        "task_status": status,
        "claim": {"text": _safe(result.get("summary", ""), SUMMARY_MAX), "verified": False},
        "boundary": boundary,
        "checks": {"status": checks_status, "passed": sum(1 for s in by_name.values() if s == "PASS"),
                   "total": len(tests), "failed": [_safe(n, 200) for n in failed][:EVIDENCE_PATHS_MAX],
                   "never_run": [_safe(n, 200) for n in never_run][:EVIDENCE_PATHS_MAX],
                   "worker_editable": bool(worker_can_edit_checks),
                   "inputs_edited": [_safe(p, 200) for p in inputs_edited][:EVIDENCE_PATHS_MAX],
                   "test_files_changed": test_files_changed,
                   "changes_known": paths is not None},
        "review": review,
        "integration": integration,
        "applied_to_project": False,
        "untested": untested[:EVIDENCE_PATHS_MAX],
        "note": "accepting a result records it; it never applies the patch to the project",
    }


def acceptance_problems(*, status: str, attempt: "Mapping[str, Any] | None", contract_checks: "Iterable[Mapping[str, Any]]",
                        needs_write_approval: bool, write_approved: bool, by: str = "user") -> "list[str]":
    """Every reason this attempt may not be accepted. Empty means the caller is free to accept it.

    A coordinator can read the diff through the bridge's ``get_task_diff`` tool, but that is for reporting
    to a person, not for accepting on their behalf. When a write task has no acceptance.checks at all,
    the Kernel has nothing of its own to measure, so "acceptance rests on your own review of the diff"
    (see contract.py's warning) can only be honored by a person at the CLI, who can run `par task diff`.
    A coordinator is refused here, not left to accept blind.
    """
    contract_checks = list(contract_checks)
    problems: "list[str]" = []
    if status != "REVIEW_REQUIRED":
        problems.append(f"the task is {status}; only REVIEW_REQUIRED results can be accepted")
    if attempt is None:
        return problems + ["there is no attempt to accept"]
    if attempt.get("end_kind") != "COMPLETED":
        problems.append(f"the latest attempt ended as {attempt.get('end_kind') or 'unfinished'}, not COMPLETED")
    result = attempt.get("result")
    if not isinstance(result, dict):
        return problems + ["the attempt has no result envelope"]
    bad = envelope_problems(result)
    if bad:
        problems.append("the result envelope is invalid: " + "; ".join(bad[:3]))
        return problems
    if result["partial"]:
        problems.append("the result is partial")
    if result["boundary"]["violations"]:
        problems.append(f"{result['boundary']['violations']} change(s) crossed the permission boundary")
    by_name = {t["name"]: t for t in result["tests"]}
    for spec in contract_checks:
        test = by_name.get(spec["name"])
        if test is None:
            problems.append(f"check {spec['name']!r} was never run")
        elif test["status"] != "PASS":
            problems.append(f"check {spec['name']!r} is {test['status']}")
    for name, test in by_name.items():
        if test["status"] != "PASS" and not any(name == c["name"] for c in contract_checks):
            problems.append(f"check {name!r} is {test['status']}")
    if needs_write_approval and not write_approved:
        problems.append("write permission was never approved for this task")
    if needs_write_approval and not contract_checks and by == "coordinator":
        problems.append("this write task has no acceptance.checks, so the Kernel measured nothing to accept on; "
                        "a coordinator cannot accept it even after reading the diff through get_task_diff - "
                        "review `par task diff` yourself and run `par task accept`")
    return problems
