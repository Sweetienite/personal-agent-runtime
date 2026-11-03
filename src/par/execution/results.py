"""The Result Envelope and the rules for accepting it.

Everything under ``tests`` and ``boundary`` is measured by the Kernel. ``summary`` is the Worker's own
statement: it is kept (redacted, bounded) because a person may want to read it, and it is always marked
unverified.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from par import schema
from par.core.redact import redact_text
from par.providers.base import ProviderEvent

SUMMARY_MAX = 1200
NO_SUMMARY = "(the worker gave no summary)"


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


def acceptance_problems(*, status: str, attempt: "Mapping[str, Any] | None", contract_checks: "Iterable[Mapping[str, Any]]",
                        needs_write_approval: bool, write_approved: bool, by: str = "user") -> "list[str]":
    """Every reason this attempt may not be accepted. Empty means the caller is free to accept it.

    A coordinator never sees the diff content (the bridge has no tool for it - only diff_stat and test
    results). When a write task has no acceptance.checks at all, the Kernel has nothing of its own to measure,
    so "acceptance rests on your own review of the diff" (see contract.py's warning) can only be honored by a
    person at the CLI, who can run `par task diff`. A coordinator is refused here, not left to accept blind.
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
                        "a coordinator cannot accept it without reading the diff, which it has no tool to do - "
                        "review `par task diff` yourself and run `par task accept`")
    return problems
