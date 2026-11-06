"""Scripted test provider: a fake CLI whose behaviour is data.

It exercises the same path as a real adapter (build_argv -> spawn -> parse_event -> classify_failure)
without a model, a network or a subscription. It is deliberately NOT registrable from config files
(``config.py`` has no "fake" kind): tests construct it directly, so a real profile can never route
work to it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from par import __version__
from par.providers.base import (
    Capabilities, FailureClass, ProbeResult, ProviderEvent, ProviderStatus, RunOutcome, RunRequest, Tri,
)

FAKE_CLI = Path(__file__).with_name("fake_cli.py")


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: "tuple[dict[str, Any], ...]"
    on_sigterm: "str | None" = None

    def to_json(self) -> str:
        doc: "dict[str, Any]" = {"name": self.name, "steps": list(self.steps)}
        if self.on_sigterm:
            doc["on_sigterm"] = self.on_sigterm
        return json.dumps(doc, ensure_ascii=False, sort_keys=True)


def _writes(files: "Optional[Mapping[str, str]]") -> "list[dict[str, Any]]":
    return [{"op": "write", "path": p, "content": c} for p, c in (files or {}).items()]


def _emit(**event: Any) -> "dict[str, Any]":
    return {"op": "emit", "event": event}


def _terminal(status: str) -> "dict[str, Any]":
    return _emit(type="terminal", status=status)


def _failure(code: "str | None", exit_code: int, **extra: Any) -> "list[dict[str, Any]]":
    event: "dict[str, Any]" = {"type": "error", **extra}
    if code:
        event["code"] = code
    return [{"op": "emit", "event": event}, _terminal("ERROR"), {"op": "exit", "code": exit_code}]


class scenarios:  # noqa: N801 - a namespace of named behaviours
    """Deterministic behaviours. Each one finishes instantly unless it says otherwise."""

    @staticmethod
    def success(files: "Optional[Mapping[str, str]]" = None, message: str = "work finished",
                claim_tests_pass: bool = False) -> Scenario:
        text = message + ("; tests: PASS (self-reported)" if claim_tests_pass else "")
        return Scenario("success", tuple(
            [_emit(type="message", text="starting")] + _writes(files)
            + [_emit(type="message", text=text), _emit(type="usage", output_tokens=42), _terminal("SUCCESS")]))

    @staticmethod
    def quota_exhausted(files: "Optional[Mapping[str, str]]" = None) -> Scenario:
        return Scenario("quota_exhausted", tuple(
            [_emit(type="message", text="starting")] + _writes(files)
            + _failure("quota_exhausted", 1, http_status=429, message="quota exhausted")))

    @staticmethod
    def rate_limited(retry_after: int = 60, files: "Optional[Mapping[str, str]]" = None) -> Scenario:
        return Scenario("rate_limited", tuple(
            _writes(files) + _failure("rate_limited", 1, http_status=429, retry_after=retry_after)))

    @staticmethod
    def bare_429() -> Scenario:
        """A 429 with no structured cause: a rate limit, never proof that the quota is gone."""
        return Scenario("bare_429", tuple(_failure(None, 1, http_status=429, message="Too Many Requests")))

    @staticmethod
    def auth_required() -> Scenario:
        return Scenario("auth_required", tuple(_failure("auth_required", 1, message="login required")))

    @staticmethod
    def model_unavailable() -> Scenario:
        return Scenario("model_unavailable", tuple(_failure("model_unavailable", 1, message="unknown model")))

    @staticmethod
    def permission_denied(soft: bool = False, files: "Optional[Mapping[str, str]]" = None) -> Scenario:
        """``soft=True`` exits 0 although the run failed: exit code 0 is never success by itself."""
        return Scenario("permission_denied_soft" if soft else "permission_denied", tuple(
            _writes(files) + _failure("permission_denied", 0 if soft else 1, message="tool call denied")))

    @staticmethod
    def timeout(sleep: float = 30.0, files: "Optional[Mapping[str, str]]" = None) -> Scenario:
        """Writes ``files`` and then hangs without a terminal event."""
        return Scenario("timeout", tuple(
            [_emit(type="message", text="starting")] + _writes(files) + [{"op": "sleep", "seconds": sleep}]))

    @staticmethod
    def partial_write_then_quota(count: int = 3) -> Scenario:
        files = {f"part_{i}.txt": f"chunk {i}\n" for i in range(1, count + 1)}
        return Scenario("partial_write_then_quota", scenarios.quota_exhausted(files).steps)

    @staticmethod
    def crash(code: int = 139, files: "Optional[Mapping[str, str]]" = None) -> Scenario:
        return Scenario("crash", tuple(
            _writes(files) + [{"op": "stderr", "text": "segmentation fault (scripted)"}, {"op": "exit", "code": code}]))

    @staticmethod
    def truncated(files: "Optional[Mapping[str, str]]" = None) -> Scenario:
        """Exits 0 after some output but never reports a terminal state."""
        return Scenario("truncated", tuple(
            _writes(files) + [_emit(type="message", text="about to finish...")]))

    @staticmethod
    def garbage() -> Scenario:
        return Scenario("garbage", (
            {"op": "raw", "text": "not json at all"}, {"op": "raw", "text": "{broken"},
            {"op": "raw", "text": '{"type": "mystery"}'},
            _emit(type="message", text="still working"), _terminal("SUCCESS")))

    @staticmethod
    def escape_attempt(path: str = "../outside.txt") -> Scenario:
        return Scenario("escape_attempt", tuple(_writes({path: "should never exist\n"})))

    @staticmethod
    def custom(name: str, *steps: "dict[str, Any]", on_sigterm: "str | None" = None) -> Scenario:
        """Any sequence of ``ops`` steps (flatten ``ops.success_tail()`` with ``*``)."""
        return Scenario(name, tuple(steps), on_sigterm)

    @staticmethod
    def cancellable(sleep: float = 30.0) -> Scenario:
        """Hangs, but answers SIGTERM with a CANCELED terminal event and exit 130."""
        return Scenario("cancellable", (_emit(type="message", text="working"), {"op": "sleep", "seconds": sleep}),
                        on_sigterm="cancel")


class ops:  # noqa: N801 - step constructors for custom scenarios
    emit = staticmethod(_emit)
    terminal = staticmethod(_terminal)

    @staticmethod
    def message(text: str) -> "dict[str, Any]":
        return _emit(type="message", text=text)

    @staticmethod
    def write(path: str, content: str = "") -> "dict[str, Any]":
        return {"op": "write", "path": path, "content": content}

    @staticmethod
    def delete(path: str) -> "dict[str, Any]":
        return {"op": "delete", "path": path}

    @staticmethod
    def symlink(path: str, target: str) -> "dict[str, Any]":
        return {"op": "symlink", "path": path, "target": target}

    @staticmethod
    def chmod(path: str, mode: int) -> "dict[str, Any]":
        return {"op": "chmod", "path": path, "mode": mode}

    @staticmethod
    def sleep(seconds: float) -> "dict[str, Any]":
        return {"op": "sleep", "seconds": seconds}

    @staticmethod
    def exit(code: int) -> "dict[str, Any]":
        return {"op": "exit", "code": code}

    @staticmethod
    def raw(text: str) -> "dict[str, Any]":
        return {"op": "raw", "text": text}

    @staticmethod
    def stderr(text: str) -> "dict[str, Any]":
        return {"op": "stderr", "text": text}

    @staticmethod
    def ignore_sigterm() -> "dict[str, Any]":
        return {"op": "ignore_sigterm"}

    @staticmethod
    def stray(seconds: float = 30.0, pid_file: str = "stray.pid", detach: bool = False) -> "dict[str, Any]":
        return {"op": "stray", "seconds": seconds, "pid_file": pid_file, "detach": detach}

    @staticmethod
    def flood(lines: int, width: int = 80) -> "dict[str, Any]":
        return {"op": "flood", "lines": lines, "width": width}

    @staticmethod
    def write_env(path: str = "env.json") -> "dict[str, Any]":
        return {"op": "write_env", "path": path}

    @staticmethod
    def die(sig: int = 9) -> "dict[str, Any]":
        return {"op": "die", "signal": sig}

    @staticmethod
    def success_tail(text: str = "work finished") -> "list[dict[str, Any]]":
        return [_emit(type="message", text=text), _terminal("SUCCESS")]


_KNOWN_KINDS = {"message", "tool", "error", "usage", "terminal"}
_CODE_TO_FAILURE = {
    "quota_exhausted": FailureClass.QUOTA_EXHAUSTED,
    "rate_limited": FailureClass.RATE_LIMITED,
    "auth_required": FailureClass.AUTH_REQUIRED,
    "model_unavailable": FailureClass.MODEL_UNAVAILABLE,
    "permission_denied": FailureClass.PERMISSION_BLOCKED,
    "transient": FailureClass.TRANSIENT_ERROR,
}


# Static facts of this build, not of a run: the registry can read them without creating an adapter
# (creating one would consume a scripted scenario in tests).
SCRIPTED_CAPABILITIES = Capabilities(
    can_headless=Tri.YES, structured_output=Tri.YES, can_select_model=Tri.NO, can_write=Tri.YES,
    can_restrict_tools=Tri.NO, has_sandbox=Tri.NO, session_persistence_control=Tri.NO,
    supports_subscription_auth=Tri.NO,
)


class ScriptedProvider:
    kind = "fake"
    CAPABILITIES = SCRIPTED_CAPABILITIES

    def __init__(self, scenario: Scenario, provider_id: str = "fake") -> None:
        self.scenario = scenario
        self.provider_id = provider_id

    def capabilities(self) -> Capabilities:
        return self.CAPABILITIES

    def probe(self) -> ProbeResult:
        return ProbeResult(self.provider_id, ProviderStatus.AVAILABLE,
                           "scripted provider: no external process, model or network", version=__version__)

    def build_argv(self, request: RunRequest) -> "list[str]":
        argv = [sys.executable, "-I", "-B", str(FAKE_CLI), self.scenario.to_json()]
        if not request.allow_write:
            argv.append("--read-only")
        return argv

    def environment(self, request: RunRequest) -> "dict[str, str]":
        return {}

    def stdin_data(self, request: RunRequest) -> "Optional[bytes]":
        return None

    def preflight(self, request: RunRequest) -> "Optional[str]":
        return None

    def parse_event(self, line: str) -> "Optional[ProviderEvent]":
        text = line.strip()
        if not text:
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return ProviderEvent("unparsed", {"length": len(text)})  # the raw text is not kept
        if not isinstance(obj, dict) or obj.get("type") not in _KNOWN_KINDS:
            return ProviderEvent("unparsed", {"length": len(text)})
        return ProviderEvent(obj["type"], {k: v for k, v in obj.items() if k != "type"})

    def classify_failure(self, outcome: RunOutcome) -> "Optional[FailureClass]":
        """None = provider-level clean success (still not acceptance). Otherwise the failure class."""
        if outcome.timed_out:
            return FailureClass.UNKNOWN
        errors = [e for e in outcome.events if e.kind == "error"]
        for e in errors:  # a structured cause always beats a status code
            cause = _CODE_TO_FAILURE.get(e.data.get("code"))
            if cause is not None:
                return cause
        if any(e.data.get("http_status") == 429 for e in errors):
            return FailureClass.RATE_LIMITED  # a bare 429 is a rate limit, never an exhausted quota
        term = outcome.terminal()
        if term is None:
            return FailureClass.EXECUTION_FAILED if outcome.exit_code not in (0, None) else FailureClass.UNKNOWN
        status = term.data.get("status")
        if status == "SUCCESS":
            return None if outcome.exit_code == 0 and not errors else FailureClass.EXECUTION_FAILED
        if status == "CANCELED":
            return FailureClass.UNKNOWN  # the Kernel knows whether it asked for the cancellation
        return FailureClass.EXECUTION_FAILED

    def request_cancel(self, process: "subprocess.Popen[Any]") -> None:
        if process.poll() is None:
            process.terminate()

    def persistence_report(self) -> "dict[str, Any]":
        return {"sessions": "none", "logs": "none", "memory": "none", "paths": []}


FakeProvider = ScriptedProvider  # the name used in the construction plan


class ScenarioSequence:
    """Hands out one scenario per attempt, in order; when they run out the last one repeats.

    ``sequence.factory("fake_a")`` is what a test registers as the adapter factory for a provider id.
    """

    def __init__(self, *scenarios: Scenario) -> None:
        if not scenarios:
            raise ValueError("a sequence needs at least one scenario")
        self._scenarios = list(scenarios)
        self.calls = 0

    def reset(self, *scenarios: Scenario) -> None:
        """Start over with new scenarios (and a call count of zero)."""
        if not scenarios:
            raise ValueError("a sequence needs at least one scenario")
        self._scenarios = list(scenarios)
        self.calls = 0

    def next(self) -> Scenario:
        scenario = self._scenarios[min(self.calls, len(self._scenarios) - 1)]
        self.calls += 1
        return scenario

    def factory(self, provider_id: str = "fake") -> "Callable[[], ScriptedProvider]":
        sequence = self

        class _Factory:
            declared_capabilities = SCRIPTED_CAPABILITIES

            def __call__(self) -> ScriptedProvider:
                return ScriptedProvider(sequence.next(), provider_id)

        return _Factory()
