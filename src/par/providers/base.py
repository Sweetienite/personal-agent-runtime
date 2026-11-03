"""Provider abstraction shared by the real CLI adapters (P3/P4) and the scripted test provider.

An adapter is a *translator*: it builds an argv, parses the CLI's event lines and classifies a finished
run. Launching the process, timeouts, process groups, cancellation escalation and verification belong
to the Kernel (P2+). "Unknown" is a first-class answer: capabilities and health are never guessed.

Contract that every adapter must keep:

* ``build_argv`` returns an argument *list*; nothing is ever built for a shell.
* ``environment`` returns the *only* extra variables the child process receives. The Kernel starts every
  child from a minimal clean environment (fixed PATH, a private HOME and TMPDIR), so nothing leaks in from
  the user's shell; an adapter that needs its own login state names exactly what it needs.
* ``stdin_data`` is what the child reads on standard input (None: nothing, the pipe is /dev/null). A prompt goes
  here rather than on the command line, so it is neither visible in the process list nor limited by argv size.
* ``preflight`` returns a reason to refuse starting at all (executable missing, isolation could not be verified,
  prompt too large ...), or None. Nothing is spawned when it returns a reason.
* ``prompt_note`` (optional method, ``prompt_note(allow_write=...)``): text the Kernel appends to the prompt, under an "Environment" heading,
  before the prompt is saved and hashed - what this CLI's environment will not let the Worker do, told up front (a CLI that silently
  denies the project's test command can otherwise end the run with no output). It is part of ``prompt.txt``, so the audit trail
  shows exactly what the model was told.
* ``prepare`` (optional method, ``prepare(request, home, tmp)``): called once per attempt after ``preflight`` accepted and before the
  child starts, with the attempt's private HOME and TMPDIR (made by the Kernel: new, empty, mode 0700). It may write the
  configuration the vendor CLI should find there (a Runtime-written settings file, a link to a directory the CLI needs). It never
  writes outside ``home``/``tmp`` and never copies credentials. An exception ends the attempt before anything is started.
  ``request.allowed_paths`` is what the task's contract allows the Worker to change (empty for a read-only task); an adapter whose CLI
  needs a permission per path derives it from there - never from anything the Worker or the project says.
* ``cleanup`` (optional method, ``cleanup(request, home, tmp)``): called once per attempt as soon as the child has ended (or could not
  be started), also when ``prepare`` failed half way. It undoes what ``prepare`` set up that must not outlive the run - for example a
  link to a directory outside the Runtime (anything an evidence folder points at can be followed by a careless tool). It never
  raises into the Kernel (exceptions are swallowed) and never deletes what a link points to. A Kernel that dies mid-run does not
  call it.
* ``parse_stderr`` (optional method): called for every line the child writes to standard error; may return an event (typically a
  ``warning``, e.g. a notice that a tool was soft-denied) that is kept with the others. It must not keep the raw line.
* ``workspace_with_git`` (optional attribute, default True): False asks the Kernel for a Worker copy without the top-level
  ``.git``. The Kernel never uses the Worker's repository, and a CLI that remembers writable git workspaces in the user's own
  configuration (Codex does) is then not triggered to write there.
* ``parse_event`` never keeps raw output lines (they may contain secrets); it keeps structured fields.
* ``classify_failure`` returns ``None`` only for a provider-level clean success. That is *not*
  acceptance: the Kernel still verifies diff, tests and approvals itself. Exit code 0 alone is never
  success, and anything the model says about its own tests is an unverified statement.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


class Tri(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ProviderStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PERMISSION_BLOCKED = "PERMISSION_BLOCKED"
    UNHEALTHY = "UNHEALTHY"
    UNAVAILABLE = "UNAVAILABLE"  # not installed / not found
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class FailureClass(str, Enum):
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    PERMISSION_BLOCKED = "PERMISSION_BLOCKED"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    UNKNOWN = "UNKNOWN"


class TerminalState(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    CANCELED = "CANCELED"


@dataclass(frozen=True)
class Capabilities:
    """What a CLI can do. Every field defaults to UNKNOWN and is only set from evidence."""

    can_headless: Tri = Tri.UNKNOWN
    structured_output: Tri = Tri.UNKNOWN
    can_select_model: Tri = Tri.UNKNOWN
    can_write: Tri = Tri.UNKNOWN
    can_restrict_tools: Tri = Tri.UNKNOWN
    has_sandbox: Tri = Tri.UNKNOWN
    session_persistence_control: Tri = Tri.UNKNOWN
    supports_subscription_auth: Tri = Tri.UNKNOWN


@dataclass(frozen=True)
class ProbeResult:
    provider_id: str
    status: ProviderStatus
    detail: str
    version: "str | None" = None
    executable: "str | None" = None
    facts: "Mapping[str, Any]" = field(default_factory=dict)   # structured findings (flags, models, ...); never secrets


@dataclass(frozen=True)
class RunRequest:
    task_id: str
    attempt: int
    prompt: str
    workspace: Path
    model: str
    allow_write: bool = False
    allowed_paths: "tuple[str, ...]" = ()       # the contract's workspace-relative paths a file-changing task may touch ("app/" = a directory prefix)


@dataclass(frozen=True)
class ProviderEvent:
    kind: str  # message | tool | error | warning | usage | terminal | session | other | unparsed
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class RunOutcome:
    """The facts about a finished process that classification may use."""

    exit_code: "int | None"
    events: "list[ProviderEvent]" = field(default_factory=list)
    timed_out: bool = False
    signal: "int | None" = None
    stderr_tail: str = ""

    def terminal(self) -> "Optional[ProviderEvent]":
        for ev in reversed(self.events):
            if ev.kind == "terminal":
                return ev
        return None


@runtime_checkable
class ProviderAdapter(Protocol):
    provider_id: str
    kind: str

    def capabilities(self) -> Capabilities: ...

    def probe(self) -> ProbeResult: ...

    def build_argv(self, request: RunRequest) -> "list[str]": ...

    def environment(self, request: RunRequest) -> "Mapping[str, str]": ...

    def stdin_data(self, request: RunRequest) -> "Optional[bytes]": ...

    def preflight(self, request: RunRequest) -> "Optional[str]": ...

    def parse_event(self, line: str) -> "Optional[ProviderEvent]": ...

    def classify_failure(self, outcome: RunOutcome) -> "Optional[FailureClass]": ...

    def request_cancel(self, process: "subprocess.Popen[Any]") -> None: ...

    def persistence_report(self) -> "dict[str, Any]": ...
