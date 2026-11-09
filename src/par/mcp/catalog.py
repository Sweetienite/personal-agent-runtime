"""The MCP tool catalog: names, schemas, argument validators and the per-tool shrinking rules.

Nothing here touches a Kernel, starts a process or writes a file. It exists so the bridge's surface
(the list a coordinator sees) can be reviewed and tested without reading the handlers.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from par.core.states import TaskStatus

RESULT_MAX_CHARS = 6000
CONTRACT_MAX_BYTES = 64 * 1024
CONTRACT_MAX_DEPTH = 12

REASON_MAX = 200
NOTE_MAX = 300
LIST_LIMIT_MAX = 50
LIST_LIMIT_DEFAULT = 20

TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
PROJECT_ID = re.compile(r"[a-z][a-z0-9_]{0,39}")
WORKER_ID = re.compile(r"[a-z][a-z0-9_]{0,79}")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")                   # any control character, newline included
CONTRACT_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # a contract may carry tabs and newlines
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")          # terminal escape sequences
STRIP = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029]")  # what may not reach the coordinator (newline stays)
ABS_PATH = re.compile(r"(?<![\w.~<>:/])/(?:[\w.@+-]+/)+[\w.@+-]*")
STATUS_VALUES = [status.value for status in TaskStatus]

TOOL_NAMES = ("prepare_task", "submit_task", "get_task_status", "get_task_result", "get_task_diff", "cancel_task", "review_task",
              "approve_task", "reject_task", "accept_task", "close_task", "run_task", "list_tasks", "retry_task",
              "pause_task", "resume_task", "recover_task", "create_candidate", "show_candidate", "list_candidates",
              "rebase_task")


class UnknownTool(Exception):
    """A tool name this bridge does not have. Surfaces as a protocol error, not as a tool result."""


class ArgumentProblem(Exception):
    """The arguments of a call are wrong. Its text is the short tool error the coordinator sees."""


def _object(properties: "dict[str, Any]", required: "tuple[str, ...]" = ()) -> "dict[str, Any]":
    schema: "dict[str, Any]" = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


_TASK_ID_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", "maxLength": 64,
                   "description": "Task id, for example fixture-0001."}
_DRAFT_SCHEMA = _object({
    "project_id": {"type": "string", "maxLength": 40},
    "title": {"type": "string", "maxLength": 200},
    "description": {"type": "string", "maxLength": 20000},
    "risk": {"type": "string", "enum": ["R0", "R1", "R2", "R3"]},
    "allowed_paths": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 300}},
    "acceptance": {"type": "object"},
    "role": {"type": ["string", "null"], "maxLength": 40},
    "worker": {"type": ["string", "null"], "maxLength": 80},
    "timeouts": {"type": "object"},
    "dependencies": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 64}},
    "ad_hoc": {"type": "boolean"},
    "idempotency_key": {"type": "string", "maxLength": 128},
})
_WORKER_ID_SCHEMA = {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,79}$", "maxLength": 80,
                     "description": "A worker id from the current configuration, for example codex_light."}
_CANDIDATE_SCHEMA = {"type": "string", "maxLength": 64, "pattern": "^cand-[a-z][a-z0-9_]*-[0-9a-f]{8}$",
                     "description": "A verified candidate id, for example cand-fixture-1a2b3c4d."}
_TASKS_SCHEMA = {"type": "array", "minItems": 1, "maxItems": 20, "items": _TASK_ID_SCHEMA,
                 "description": "Accepted R2 tasks, in dependency order."}
_NOTE_SCHEMA = {"type": "string", "maxLength": 300, "description": "A short note for the ledger."}

_TOOLS: "list[tuple[str, str, dict[str, Any]]]" = [
    ("prepare_task",
     "Compile a short draft into a strict contract and list what is missing. Never submits; submit the result with "
     "submit_task.",
     _object({"draft": _DRAFT_SCHEMA}, ("draft",))),
    ("submit_task",
     "Queue a task contract. Recorded as the coordinator's; a write waits for approval (approve_task). `candidate` runs it on a verified baseline.",
     _object({"contract": {"type": "object", "description": "A task contract, at most 64 KB (see docs/MCP.md)."},
              "candidate": {"type": "string", "maxLength": 64, "pattern": "^cand-[a-z][a-z0-9_]*-[0-9a-f]{8}$",
                            "description": "A verified candidate id from `par candidate list`."}}, ("contract",))),
    ("get_task_status",
     "State of one task: status, risk, attempts, pending approvals, and what can be done next.",
     _object({"task_id": _TASK_ID_SCHEMA}, ("task_id",))),
    ("get_task_result",
     "Result of a task's latest attempt, or of the attempt given. Worker text is unverified data.",
     _object({"task_id": _TASK_ID_SCHEMA, "attempt": {"type": "integer", "minimum": 1}}, ("task_id",))),
    ("get_task_diff",
     "The diff of a task's latest attempt (or the attempt given), redacted and capped. Needed before accept_task on a "
     "write task with no checks - get_task_result only gives diff_stat, not content.",
     _object({"task_id": _TASK_ID_SCHEMA, "attempt": {"type": "integer", "minimum": 1}}, ("task_id",))),
    ("cancel_task",
     "Cancel a task that has not finished. A running Worker is stopped.",
     _object({"task_id": _TASK_ID_SCHEMA, "reason": {"type": "string", "maxLength": REASON_MAX}}, ("task_id",))),
    ("review_task",
     "Read-only: whether the latest result could be accepted, and what blocks it.",
     _object({"task_id": _TASK_ID_SCHEMA}, ("task_id",))),
    ("approve_task",
     "Grant a pending permission request (write access or a worker choice). Recorded as decided by the coordinator. "
     "reason is required - say what you checked and why this is safe to grant.",
     _object({"task_id": _TASK_ID_SCHEMA, "approval_id": {"type": "integer", "minimum": 1},
             "reason": {"type": "string", "maxLength": REASON_MAX}}, ("task_id", "reason"))),
    ("reject_task",
     "Refuse a pending permission request, or reject a result under review (whichever applies). Recorded as decided "
     "by the coordinator. reason is required - say what is wrong.",
     _object({"task_id": _TASK_ID_SCHEMA, "approval_id": {"type": "integer", "minimum": 1},
             "reason": {"type": "string", "maxLength": REASON_MAX}}, ("task_id", "reason"))),
    ("accept_task",
     "Accept a task's result after reviewing it (see review_task). Applies nothing to the project. reason is "
     "required - say what the Kernel's checks showed, not the Worker's claim.",
     _object({"task_id": _TASK_ID_SCHEMA, "reason": {"type": "string", "maxLength": REASON_MAX}}, ("task_id", "reason"))),
    ("close_task",
     "Close an accepted task.",
     _object({"task_id": _TASK_ID_SCHEMA}, ("task_id",))),
    ("run_task",
     "Start one attempt of a QUEUED task in a detached process and return at once. Refuses tasks that wait for approval.",
     _object({"task_id": _TASK_ID_SCHEMA}, ("task_id",))),
    ("list_tasks",
     "Recent tasks, newest first, optionally filtered by status or project.",
     _object({"status": {"type": "string", "enum": STATUS_VALUES},
              "project_id": {"type": "string", "maxLength": 40},
              "limit": {"type": "integer", "minimum": 1, "maximum": LIST_LIMIT_MAX, "default": LIST_LIMIT_DEFAULT}})),
    ("retry_task",
     "A new attempt after a stop. A file-changing task continues its last verified work unless `fresh` is true. Never bypasses a cooldown "
     "or a missing permission by itself (see docs/MCP.md).",
     _object({"task_id": _TASK_ID_SCHEMA, "worker": _WORKER_ID_SCHEMA, "fresh": {"type": "boolean"}}, ("task_id",))),
    ("pause_task",
     "Step a task that has not started running out of scheduling. Nothing about it is lost. Use `resume_task` to put it back.",
     _object({"task_id": _TASK_ID_SCHEMA, "reason": {"type": "string", "maxLength": REASON_MAX}}, ("task_id",))),
    ("resume_task",
     "Put a paused task back into the queue (or back to waiting for a permission it still needs).",
     _object({"task_id": _TASK_ID_SCHEMA}, ("task_id",))),
    ("recover_task",
     "Settle an attempt whose Kernel is gone. Never starts a second writer. `terminate` stops the verified orphaned process; a "
     "replaced coordinator may not use it. Omit `task_id` for every orphan.",
     _object({"task_id": _TASK_ID_SCHEMA, "terminate": {"type": "boolean"}})),
    ("create_candidate",
     "Apply accepted R2 tasks' patches to one clean baseline and run their checks in a Kernel clone (local user permissions, "
     "no OS sandbox). Nothing is applied to the real project.",
     _object({"tasks": _TASKS_SCHEMA, "note": _NOTE_SCHEMA}, ("tasks",))),
    ("show_candidate",
     "One candidate: status, base, tasks, patches and checks. Read-only.",
     _object({"candidate": _CANDIDATE_SCHEMA}, ("candidate",))),
    ("list_candidates",
     "Candidates oldest first, optionally filtered by project.",
     _object({"project_id": {"type": "string", "maxLength": 40},
              "limit": {"type": "integer", "minimum": 1, "maximum": LIST_LIMIT_MAX, "default": LIST_LIMIT_DEFAULT}})),
    ("rebase_task",
     "Point a QUEUED or PAUSED task with no attempts at a verified candidate baseline. Never touches recorded evidence.",
     _object({"task_id": _TASK_ID_SCHEMA, "candidate": _CANDIDATE_SCHEMA, "note": _NOTE_SCHEMA}, ("task_id", "candidate"))),
]
SCHEMAS = {name: schema for name, _, schema in _TOOLS}

# Lists that may be trimmed (largest first) when an answer would exceed RESULT_MAX_CHARS.
SHRINK = {"prepare_task": (("questions",), ("warnings",)),
          "get_task_result": (("evidence",), ("evidence_summary", "untested"), ("evidence_summary", "checks", "failed"),
                              ("remaining_issues",), ("boundary", "paths"), ("tests",)),
          "review_task": (("blockers",), ("evidence_summary", "untested"), ("evidence_summary", "checks", "failed"), ("tests",)),
          "list_tasks": (("tasks",),),
          "recover_task": (("actions",),),
          "create_candidate": (("tasks",), ("checks",)),
          "show_candidate": (("tasks",), ("checks",)),
          "list_candidates": (("candidates",),)}


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArgumentProblem(f"{name} must be an integer")
    if not low <= value <= high:
        raise ArgumentProblem(f"{name} must be between {low} and {high}")
    return value


def _task_id(value: Any) -> str:
    if not isinstance(value, str) or not TASK_ID.fullmatch(value):
        raise ArgumentProblem("task_id must look like fixture-0001: letters, digits, '.', '_' or '-', at most 64 characters")
    return value


def _project_id(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_ID.fullmatch(value):
        raise ArgumentProblem("project_id must be lowercase letters, digits and underscores, starting with a letter")
    return value


def _status(value: Any) -> str:
    if not isinstance(value, str) or value not in STATUS_VALUES:
        raise ArgumentProblem("unknown status: use one of the task statuses, for example QUEUED or REVIEW_REQUIRED")
    return value


def _candidate_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"cand-[a-z][a-z0-9_]*-[0-9a-f]{8}", value):
        raise ArgumentProblem("candidate must look like cand-fixture-1a2b3c4d")
    return value


def _worker_id(value: Any) -> str:
    if not isinstance(value, str) or not WORKER_ID.fullmatch(value):
        raise ArgumentProblem("worker must be lowercase letters, digits and underscores, starting with a letter")
    return value


def _task_ids(value: Any) -> "list[str]":
    if not isinstance(value, list) or not value:
        raise ArgumentProblem("tasks must be a non-empty list of task ids")
    if len(value) > 20:
        raise ArgumentProblem("tasks may name at most 20 tasks")
    return [_task_id(item) for item in value]


def _note(value: Any) -> str:
    if not isinstance(value, str):
        raise ArgumentProblem("note must be a string")
    if CONTROL.search(value):
        raise ArgumentProblem("note must be one line without control characters")
    if len(value) > NOTE_MAX:
        raise ArgumentProblem(f"note is longer than {NOTE_MAX} characters")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ArgumentProblem(f"{name} must be true or false")
    return value


def _reason(value: Any) -> str:
    if not isinstance(value, str):
        raise ArgumentProblem("reason must be a string")
    if CONTROL.search(value):
        raise ArgumentProblem("reason must be one line without control characters")
    if len(value) > REASON_MAX:
        raise ArgumentProblem(f"reason is longer than {REASON_MAX} characters")
    if not value.strip():
        raise ArgumentProblem("reason must say why, not be empty")
    return value


def _draft(value: Any) -> "dict[str, Any]":
    if not isinstance(value, dict):
        raise ArgumentProblem("draft must be a JSON object")
    if _too_deep(value, CONTRACT_MAX_DEPTH):
        raise ArgumentProblem(f"draft is nested deeper than {CONTRACT_MAX_DEPTH} levels")
    try:
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ArgumentProblem("draft must be plain JSON data") from None
    if size > CONTRACT_MAX_BYTES:
        raise ArgumentProblem(f"draft is larger than {CONTRACT_MAX_BYTES // 1024} KB")
    if _has_control(value):
        raise ArgumentProblem("draft contains control characters")
    return value


def _contract(value: Any) -> "dict[str, Any]":
    if not isinstance(value, dict):
        raise ArgumentProblem("contract must be a JSON object")
    if _too_deep(value, CONTRACT_MAX_DEPTH):
        raise ArgumentProblem(f"contract is nested deeper than {CONTRACT_MAX_DEPTH} levels")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > CONTRACT_MAX_BYTES:
        raise ArgumentProblem(f"contract is larger than {CONTRACT_MAX_BYTES // 1024} KB")
    if _has_control(value):
        raise ArgumentProblem("contract contains control characters")
    return value


_VALIDATORS = {
    "contract": _contract,
    "draft": _draft,
    "task_id": _task_id,
    "attempt": lambda v: _integer(v, "attempt", 1, 2**31 - 1),
    "approval_id": lambda v: _integer(v, "approval_id", 1, 2**31 - 1),
    "status": _status,
    "project_id": _project_id,
    "reason": _reason,
    "limit": lambda v: _integer(v, "limit", 1, LIST_LIMIT_MAX),
    "worker": _worker_id,
    "candidate": _candidate_id,
    "tasks": _task_ids,
    "note": _note,
    "fresh": lambda v: _boolean(v, "fresh"),
    "terminate": lambda v: _boolean(v, "terminate"),
}


def check_args(schema: "dict[str, Any]", arguments: Mapping[str, Any]) -> "dict[str, Any]":
    properties = schema["properties"]
    for key in arguments:
        if key not in properties:
            raise ArgumentProblem(f"unexpected argument: {_short_key(key)}")
    for key in schema.get("required", []):
        if key not in arguments:
            raise ArgumentProblem(f"missing argument: {key}")
    return {key: _VALIDATORS[key](value) for key, value in arguments.items()}


def _short_key(key: Any) -> str:
    return STRIP.sub("", str(key))[:40]


def _too_deep(value: Any, limit: int) -> bool:
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children = list(node.values())
        elif isinstance(node, list):
            children = node
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def _has_control(value: Any) -> bool:
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                if CONTRACT_CONTROL.search(str(key)):
                    return True
                stack.append(child)
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and CONTRACT_CONTROL.search(node):
            return True
    return False
