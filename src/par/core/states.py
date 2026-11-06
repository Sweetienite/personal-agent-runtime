"""Task states and the explicit transition table.

The table below is the single source of truth. The Kernel checks it in Python, and the very same pairs are
written into the ledger's ``transitions`` table, which a database trigger consults - so a buggy writer or
another process cannot make an illegal jump either.

Rules the table encodes (construction plan, section 4.2):

* ``RUNNING -> ACCEPTED`` does not exist: nothing is accepted without passing VERIFYING and a review.
* ``ACCEPTED -> RUNNING`` does not exist: an accepted task is never re-run in place.
* ``CANCELED -> RUNNING`` does not exist: a canceled task can only go back to QUEUED, which starts a NEW attempt.
* Nothing ever goes back to DRAFT.
"""
from __future__ import annotations

import hashlib
from enum import Enum


class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    QUEUED = "QUEUED"
    PERMISSION_WAIT = "PERMISSION_WAIT"
    BLOCKED_NO_WORKER = "BLOCKED_NO_WORKER"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ACCEPTED = "ACCEPTED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    PAUSED = "PAUSED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"


S = TaskStatus

TRANSITIONS: "dict[TaskStatus, frozenset[TaskStatus]]" = {
    S.DRAFT: frozenset({S.READY, S.CANCELED}),
    S.READY: frozenset({S.QUEUED, S.PERMISSION_WAIT, S.PAUSED, S.CANCELED}),
    S.QUEUED: frozenset({S.STARTING, S.PERMISSION_WAIT, S.BLOCKED_NO_WORKER, S.PAUSED, S.CANCELED}),
    S.PERMISSION_WAIT: frozenset({S.QUEUED, S.PAUSED, S.CANCELED}),
    S.BLOCKED_NO_WORKER: frozenset({S.QUEUED, S.PAUSED, S.CANCELED}),
    S.STARTING: frozenset({S.RUNNING, S.FAILED, S.AUTH_REQUIRED, S.RATE_LIMITED, S.QUOTA_EXHAUSTED,
                           S.BLOCKED_NO_WORKER, S.INTERRUPTED, S.CANCEL_REQUESTED, S.RECOVERY_REQUIRED}),
    S.RUNNING: frozenset({S.VERIFYING, S.FAILED, S.AUTH_REQUIRED, S.RATE_LIMITED, S.QUOTA_EXHAUSTED,
                          S.PERMISSION_WAIT, S.BLOCKED_NO_WORKER, S.INTERRUPTED, S.CANCEL_REQUESTED,
                          S.RECOVERY_REQUIRED}),
    S.VERIFYING: frozenset({S.REVIEW_REQUIRED, S.FAILED, S.INTERRUPTED, S.CANCEL_REQUESTED, S.RECOVERY_REQUIRED}),
    S.REVIEW_REQUIRED: frozenset({S.ACCEPTED, S.FAILED, S.CANCELED}),
    S.ACCEPTED: frozenset({S.CLOSED}),
    S.CLOSED: frozenset(),
    S.FAILED: frozenset({S.QUEUED, S.CANCELED}),
    S.INTERRUPTED: frozenset({S.QUEUED, S.FAILED, S.PAUSED, S.CANCELED}),
    S.RECOVERY_REQUIRED: frozenset({S.INTERRUPTED, S.QUEUED, S.FAILED, S.CANCELED}),
    S.AUTH_REQUIRED: frozenset({S.QUEUED, S.PAUSED, S.CANCELED}),
    S.RATE_LIMITED: frozenset({S.QUEUED, S.PAUSED, S.CANCELED}),
    S.QUOTA_EXHAUSTED: frozenset({S.QUEUED, S.PAUSED, S.CANCELED}),
    S.PAUSED: frozenset({S.QUEUED, S.CANCELED}),
    S.CANCEL_REQUESTED: frozenset({S.CANCELED, S.RECOVERY_REQUIRED}),
    S.CANCELED: frozenset({S.QUEUED}),
}

ACTIVE = frozenset({S.STARTING, S.RUNNING, S.VERIFYING, S.CANCEL_REQUESTED})   # an attempt may be alive
WAITING = frozenset({S.PERMISSION_WAIT, S.BLOCKED_NO_WORKER, S.AUTH_REQUIRED, S.RATE_LIMITED,
                     S.QUOTA_EXHAUSTED, S.PAUSED})
NEEDS_YOU = frozenset({S.PERMISSION_WAIT, S.REVIEW_REQUIRED, S.RECOVERY_REQUIRED, S.BLOCKED_NO_WORKER,
                       S.AUTH_REQUIRED, S.RATE_LIMITED, S.QUOTA_EXHAUSTED})   # nothing moves until a person decides
PAUSABLE_AFTER_FAILURE = frozenset({S.RATE_LIMITED, S.QUOTA_EXHAUSTED, S.AUTH_REQUIRED, S.INTERRUPTED,
                                    S.BLOCKED_NO_WORKER})
SUCCESS = frozenset({S.REVIEW_REQUIRED, S.ACCEPTED, S.CLOSED})

# What a person can do next in each state (drives the CLI hints and, later, the console buttons).
NEXT_ACTIONS: "dict[TaskStatus, tuple[str, ...]]" = {
    S.DRAFT: ("cancel",),
    S.READY: ("pause", "cancel"),
    S.QUEUED: ("run", "pause", "cancel"),
    S.PERMISSION_WAIT: ("approve", "reject", "cancel"),
    S.BLOCKED_NO_WORKER: ("retry", "pause", "cancel"),
    S.STARTING: ("cancel",),
    S.RUNNING: ("cancel",),
    S.VERIFYING: ("cancel",),
    S.REVIEW_REQUIRED: ("accept", "reject", "cancel"),
    S.ACCEPTED: ("close",),
    S.CLOSED: (),
    S.FAILED: ("retry", "cancel"),
    S.INTERRUPTED: ("retry", "pause", "cancel"),
    S.RECOVERY_REQUIRED: ("recover", "retry", "cancel"),
    S.AUTH_REQUIRED: ("retry", "pause", "cancel"),
    S.RATE_LIMITED: ("retry", "pause", "cancel"),
    S.QUOTA_EXHAUSTED: ("retry", "pause", "cancel"),
    S.PAUSED: ("resume", "retry", "cancel"),
    S.CANCEL_REQUESTED: (),
    S.CANCELED: ("retry",),
}


# A derived view for coordinators: one word for what the task needs now. It never replaces TaskStatus,
# never drives a transition, and is recomputed from the authoritative status plus the pending approvals.
DERIVED = ("NOT_READY", "READY_TO_RUN", "NEEDS_SCOPE", "NEEDS_PERMISSION", "RUNNING", "NEEDS_REVIEW",
           "RESULT_VERIFIED", "PAUSED", "BLOCKED", "CANCELED")


def derived_state(status: "TaskStatus | str", pending_kinds: "tuple[str, ...]" = ()) -> str:
    try:
        s = TaskStatus(status)
    except ValueError:
        return "BLOCKED"
    if s in (S.DRAFT, S.READY):
        return "NOT_READY"
    if s == S.QUEUED:
        return "READY_TO_RUN"
    if s == S.PERMISSION_WAIT:
        return "NEEDS_SCOPE" if "WRITE_PERMISSION" in pending_kinds else "NEEDS_PERMISSION"
    if s in (S.STARTING, S.RUNNING, S.VERIFYING, S.CANCEL_REQUESTED):
        return "RUNNING"
    if s == S.REVIEW_REQUIRED:
        return "NEEDS_REVIEW"
    if s in (S.ACCEPTED, S.CLOSED):
        return "RESULT_VERIFIED"
    if s == S.PAUSED:
        return "PAUSED"
    if s == S.CANCELED:
        return "CANCELED"
    return "BLOCKED"


class IllegalTransition(Exception):
    def __init__(self, src: str, dst: str) -> None:
        super().__init__(f"illegal task status transition {src} -> {dst}")
        self.src, self.dst = src, dst


def can_transition(src: "TaskStatus | str", dst: "TaskStatus | str") -> bool:
    try:
        return TaskStatus(dst) in TRANSITIONS[TaskStatus(src)]
    except (ValueError, KeyError):
        return False


def check_transition(src: "TaskStatus | str", dst: "TaskStatus | str") -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(str(getattr(src, "value", src)), str(getattr(dst, "value", dst)))


def transition_rows() -> "list[tuple[str, str]]":
    return sorted((a.value, b.value) for a, targets in TRANSITIONS.items() for b in targets)


def transitions_digest() -> str:
    return hashlib.sha256("\n".join(f"{a}>{b}" for a, b in transition_rows()).encode()).hexdigest()


def reachable_from(start: TaskStatus) -> "set[TaskStatus]":
    seen, todo = {start}, [start]
    while todo:
        for nxt in TRANSITIONS[todo.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


class AttemptState(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    ENDED = "ENDED"


class EndKind(str, Enum):
    COMPLETED = "COMPLETED"     # the provider finished cleanly and verification passed
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"  # timeout or an unexplained end
    CANCELED = "CANCELED"
    ORPHANED = "ORPHANED"        # the Kernel that owned it disappeared; found by recovery
