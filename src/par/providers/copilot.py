"""GitHub Copilot CLI adapter: text answers only.

The CLI has no approval callback in non-interactive mode, and the vendor requires
``--allow-all-tools`` there. This adapter pairs that flag with an allowlist naming a tool
that does not exist, so the model has no tool it can call; no path or URL permission is
granted. The prompt goes through standard input, never argv. The adapter refuses write
tasks (``can_write=no``) rather than trying to turn a Q&A CLI into a code worker.

Copilot CLI 1.0.5 is the bundled engine examined here. The npm launcher can serve a newer
cached build; ``--no-auto-update`` pins the run to the bundled one and avoids downloads.
The daemon's authentication stays with the CLI: ``COPILOT_HOME`` is passed through, the
Runtime never reads or copies its contents, and ``probe`` cannot verify login offline.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from par.core.redact import redact_text
from par.providers.base import (
    Capabilities, FailureClass, ProbeResult, ProviderEvent, ProviderStatus, RunOutcome, RunRequest, Tri,
)

TESTED_VERSION = "1.0.5"
MAX_PROMPT_BYTES = 400_000
MAX_TEXT = 4000
PROBE_TIMEOUT = 30.0
PROBE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
NO_TOOLS = "par-no-tools"
PASSTHROUGH = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS")
REQUIRED_FLAGS = ("--allow-all-tools", "--available-tools", "--disable-builtin-mcps", "--no-custom-instructions",
                  "--no-ask-user", "--no-auto-update", "--no-bash-env", "--disallow-temp-dir", "--output-format",
                  "--model", "--log-level", "--log-dir", "--secret-env-vars")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,99}\Z")
_CACHE_LOCK = threading.Lock()
_HELP_CACHE: "dict[tuple[str, int, int], tuple[float, str | None]]" = {}
_HELP_TTL = 600.0


def clip(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else ""
    return redact_text(text[:limit])


def _usage(raw: Any) -> "dict[str, int]":
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                                "reasoning_output_tokens")
            if isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool) and raw[k] >= 0}


def _message_of(obj: Mapping[str, Any]) -> str:
    for key in ("message", "error", "detail"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return clip(value)
        if isinstance(value, Mapping):
            nested = _message_of(value)
            if nested:
                return nested
    return ""


def classify_text(*messages: str) -> FailureClass:
    """Map Copilot's own failure text. Unknown text stays EXECUTION_FAILED, never a guessed quota/login state."""
    text = " | ".join(m for m in messages if m).lower()
    if any(w in text for w in ("quota", "usage limit", "out of credits", "spend cap", "billing", "monthly request limit",
                               "no requests remaining", "ai credits", "payment required")):
        return FailureClass.QUOTA_EXHAUSTED
    if any(w in text for w in ("no authentication information", "not logged in", "authentication", "unauthorized",
                               "token expired", "login required", "please log in", "sign in")) or re.search(r"\b401\b", text):
        return FailureClass.AUTH_REQUIRED
    if "model" in text and any(w in text for w in ("not available", "not supported", "does not exist", "unknown", "invalid")):
        return FailureClass.MODEL_UNAVAILABLE
    if any(w in text for w in ("rate limit", "too many requests", "capacity", "try again later")) or re.search(r"\b429\b", text):
        return FailureClass.RATE_LIMITED
    if any(w in text for w in ("connection failed", "request timed out", "timed out", "temporarily unavailable",
                               "internal server error", "unexpected status 500", "unexpected status 502",
                               "unexpected status 503", "unexpected status 504")):
        return FailureClass.TRANSIENT_ERROR
    return FailureClass.EXECUTION_FAILED


class CopilotAdapter:
    provider_id = "copilot"
    kind = "copilot"
    CAPABILITIES = Capabilities(
        can_headless=Tri.YES, structured_output=Tri.YES, can_select_model=Tri.YES, can_write=Tri.NO,
        can_restrict_tools=Tri.YES, has_sandbox=Tri.UNKNOWN, session_persistence_control=Tri.NO,
        supports_subscription_auth=Tri.YES)
    workspace_with_git = False

    def __init__(self, executable: str, *, provider_id: str = "copilot", copilot_home: "str | None" = None,
                 environ: "Mapping[str, str] | None" = None, models: "tuple[str, ...]" = ()) -> None:
        self.executable = executable
        self.provider_id = provider_id
        self.environ = dict(os.environ if environ is None else environ)
        self.copilot_home = copilot_home or self.environ.get("COPILOT_HOME") or str(Path.home() / ".copilot")
        self.models = tuple(models)
        self._log_dir: "Path | None" = None

    @classmethod
    def from_entry(cls, entry: Any, *, environ: "Mapping[str, str] | None" = None,
                   models: "tuple[str, ...]" = ()) -> "CopilotAdapter":
        return cls(entry.executable or "", provider_id=entry.id, environ=environ, models=models)

    # --- trust in the launcher ---------------------------------------------------------------------------------
    def _real_files(self) -> "list[Path]":
        path = Path(self.executable)
        files = [path]
        try:
            resolved = path.resolve()
        except OSError:
            return files
        if resolved != path:
            files.append(resolved)
        return files

    def executable_problem(self) -> "str | None":
        if not self.executable or not Path(self.executable).is_absolute():
            return "the Copilot executable must be configured as an absolute path (PATH lookups are not used)"
        for path in self._real_files():
            if not path.is_file():
                return f"no executable at {path}"
            if not os.access(path, os.X_OK):
                return f"{path} is not executable"
            try:
                st = path.stat()
            except OSError as exc:
                return f"cannot inspect {path}: {exc.strerror or exc}"
            if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return f"{path} is writable by other users"
            if st.st_uid not in (0, os.getuid()):
                return f"{path} belongs to another user"
        return None

    def _clean_env(self, home: str) -> "dict[str, str]":
        env = {"PATH": PROBE_PATH, "HOME": home, "COPILOT_HOME": self.copilot_home, "LANG": "en_US.UTF-8",
               "LC_ALL": "en_US.UTF-8", "NO_COLOR": "1", "CI": "1"}
        env.update({k: self.environ[k] for k in PASSTHROUGH if k in self.environ})
        return env

    def _run_offline(self, args: "list[str]", *, timeout: float = PROBE_TIMEOUT) -> "tuple[int, str, str]":
        with tempfile.TemporaryDirectory(prefix="par-copilot-probe-") as home:
            try:
                proc = subprocess.run([self.executable, "--no-auto-update", *args], env=self._clean_env(home),
                                      capture_output=True, text=True, timeout=timeout, cwd=home,
                                      stdin=subprocess.DEVNULL, check=False)
            except subprocess.TimeoutExpired:
                return 124, "", f"timed out after {timeout:g}s"
            except OSError as exc:
                return 127, "", str(exc.strerror or exc)
            return proc.returncode, proc.stdout, proc.stderr

    def _help(self) -> "str | None":
        try:
            st = self._real_files()[-1].stat()
        except OSError:
            return None
        key = (str(self._real_files()[-1]), st.st_mtime_ns, st.st_size)
        with _CACHE_LOCK:
            hit = _HELP_CACHE.get(key)
            if hit and time.monotonic() - hit[0] < _HELP_TTL:
                return hit[1]
        rc, out, err = self._run_offline(["--help"])
        text = out if rc == 0 and out.strip() else None
        with _CACHE_LOCK:
            _HELP_CACHE[key] = (time.monotonic(), text)
        return text

    def _help_problem(self) -> "str | None":
        text = self._help()
        if text is None:
            return "`copilot --no-auto-update --help` did not return the bundled CLI's help"
        missing = [flag for flag in REQUIRED_FLAGS if flag not in text]
        if missing:
            return "this Copilot CLI build lacks required safety flags: " + ", ".join(missing)
        return None

    # --- the adapter protocol ----------------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return self.CAPABILITIES

    def preflight(self, request: RunRequest) -> "Optional[str]":
        problem = self.executable_problem()
        if problem:
            return problem
        if not Path(self.copilot_home).is_dir():
            return (f"the Copilot home {self.copilot_home} does not exist: run "
                    f"`HOME={self.copilot_home} COPILOT_HOME={self.copilot_home} copilot --no-auto-update login` once first")
        if not _MODEL.match(request.model or ""):
            return f"{request.model!r} is not a usable model slug; check `copilot --no-auto-update --help`"
        if request.allow_write:
            return "the Copilot adapter is text-answer only; it cannot produce a verified file change"
        if len(request.prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            return f"the prompt is larger than {MAX_PROMPT_BYTES} bytes"
        return self._help_problem()

    def build_argv(self, request: RunRequest) -> "list[str]":
        if not _MODEL.match(request.model or ""):
            raise ValueError(f"{request.model!r} is not a usable model slug")
        argv = [self.executable, "--no-auto-update", "--output-format", "json", "--model", request.model,
                "--allow-all-tools", f"--available-tools={NO_TOOLS}", "--disable-builtin-mcps",
                "--no-custom-instructions", "--no-ask-user", "--no-bash-env", "--disallow-temp-dir",
                "--log-level", "none", "--secret-env-vars=GITHUB_TOKEN,GH_TOKEN,COPILOT_GITHUB_TOKEN"]
        if self._log_dir is not None:
            argv += ["--log-dir", str(self._log_dir)]
        return argv

    def environment(self, request: RunRequest) -> "dict[str, str]":
        # The CLI's credential lookup is scoped by HOME; a per-attempt HOME cannot see the login.
        # Both are pinned to the same stable Copilot directory, which the person logs into once.
        env = {"HOME": self.copilot_home, "COPILOT_HOME": self.copilot_home, "NO_COLOR": "1", "CI": "1"}
        env.update({k: self.environ[k] for k in PASSTHROUGH if k in self.environ})
        return env

    def stdin_data(self, request: RunRequest) -> "Optional[bytes]":
        return request.prompt.encode("utf-8")

    def prepare(self, request: RunRequest, home: Path, tmp: Path) -> None:
        logs = Path(tmp) / "copilot-logs"
        logs.mkdir(mode=0o700, exist_ok=True)
        os.chmod(logs, 0o700)
        self._log_dir = logs

    def cleanup(self, request: RunRequest, home: Path, tmp: Path) -> None:
        log_dir = Path(tmp) / "copilot-logs"
        if log_dir.is_dir() and not log_dir.is_symlink() and log_dir.parent == Path(tmp):
            shutil.rmtree(log_dir, ignore_errors=True)
        self._log_dir = None

    def parse_event(self, line: str) -> "Optional[ProviderEvent]":
        text = line.strip()
        if not text:
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return ProviderEvent("unparsed", {"length": len(text)})
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            return ProviderEvent("unparsed", {"length": len(text)})
        kind = obj["type"]
        if kind == "assistant.message":
            content = obj.get("content")
            if not isinstance(content, str):
                data = obj.get("data")
                content = data.get("content") if isinstance(data, Mapping) else None
            return ProviderEvent("message", {"text": clip(content)})
        if kind == "assistant.message_delta":
            delta = obj.get("deltaContent")
            if not isinstance(delta, str):
                data = obj.get("data")
                delta = data.get("deltaContent") if isinstance(data, Mapping) else None
            return ProviderEvent("other", {"type": kind, "chars": len(delta) if isinstance(delta, str) else 0})
        if kind == "assistant.message_start":
            return ProviderEvent("other", {"type": kind})
        if kind == "result":
            exit_code = obj.get("exitCode", obj.get("exit_code"))
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                exit_code = None
            data: "dict[str, Any]" = {"status": "SUCCESS" if exit_code in (0, None) else "ERROR", "exit_code": exit_code}
            if isinstance(obj.get("sessionId"), str):
                data["session_id"] = clip(obj.get("sessionId"), 80)
            usage = _usage(obj.get("usage"))
            if usage:
                data["usage"] = usage
            message = _message_of(obj)
            if message:
                data["message"] = message
            return ProviderEvent("terminal", data)
        low = kind.lower()
        if "error" in low:
            return ProviderEvent("error", {"message": _message_of(obj) or clip(kind, 80)})
        if low.startswith("tool"):
            name = obj.get("name") or obj.get("tool") or obj.get("toolName")
            status = obj.get("status") or obj.get("phase")
            return ProviderEvent("tool", {"tool": clip(name, 60), "phase": clip(status, 40)})
        if low.startswith("session"):
            return ProviderEvent("session", {"type": clip(kind, 60)})
        return ProviderEvent("other", {"type": clip(kind, 60)})

    def classify_failure(self, outcome: RunOutcome) -> "Optional[FailureClass]":
        if outcome.timed_out:
            return FailureClass.UNKNOWN
        terminal = outcome.terminal()
        errors = [str(e.data.get("message", "")) for e in outcome.events if e.kind == "error"]
        stderr = outcome.stderr_tail or ""
        failed_exit = (outcome.exit_code not in (0, None)) or outcome.signal is not None
        if terminal is not None and terminal.data.get("status") == "ERROR":
            return classify_text(str(terminal.data.get("message", "")), *errors[-3:], stderr)
        if terminal is not None and terminal.data.get("status") == "SUCCESS":
            return classify_text(*errors[-3:], stderr) if failed_exit else None
        if not failed_exit:
            return FailureClass.UNKNOWN
        return classify_text(*errors[-3:], stderr)

    def request_cancel(self, process: "subprocess.Popen[Any]") -> None:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)

    def persistence_report(self) -> "dict[str, Any]":
        home = self.copilot_home
        return {
            "sessions": f"the CLI runs with HOME=COPILOT_HOME={home}; prompt-mode session state may be written there",
            "logs": "CLI logs are directed to the attempt's private log directory and removed when the attempt ends; "
                    "the Kernel keeps its own stdout/stderr copies",
            "memory": "off: --enable-memory is never passed",
            "paths": [home],
            "credentials": f"{home} and the system credential store belong to the CLI; the Runtime never opens or copies them",
            "telemetry": "this adapter does not disable server-side telemetry; the bundled build has no client-telemetry flag",
            "downloads": "--no-auto-update pins the bundled engine and prevents CLI downloads",
        }

    def probe(self) -> ProbeResult:
        exe = self.executable
        problem = self.executable_problem()
        if problem:
            return ProbeResult(self.provider_id, ProviderStatus.UNAVAILABLE, problem, executable=exe or None)
        facts: "dict[str, Any]" = {"copilot_home": self.copilot_home,
                                   "vendor_signature": "not available (npm-installed Node script)"}
        if not Path(self.copilot_home).is_dir():
            return ProbeResult(self.provider_id, ProviderStatus.AUTH_REQUIRED,
                               f"{self.copilot_home} does not exist: run `copilot --no-auto-update login` once",
                               executable=exe, facts=facts)
        problem = self._help_problem()
        facts["required_flags"] = "verified" if problem is None else problem
        if problem:
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, problem, executable=exe, facts=facts)
        rc, out, err = self._run_offline(["--version"])
        match = re.search(r"\b(\d+(?:\.\d+)+)\b", out) if rc == 0 else None
        version = match.group(1) if match else None
        if version is None:
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY,
                               f"`--version` failed ({rc}): {redact_text(err.strip())[:120]}", executable=exe, facts=facts)
        facts["tested_version"] = version == TESTED_VERSION
        notes = [] if version == TESTED_VERSION else [f"untested version (tested: {TESTED_VERSION})"]
        help_text = self._help() or ""
        start = help_text.find("--model")
        end = help_text.find("\n  --", start + len("--model")) if start >= 0 else -1
        block = help_text[start:end if end > 0 else start + 2000] if start >= 0 else ""
        catalog = re.findall(r'"([A-Za-z0-9][A-Za-z0-9._:/+-]{0,99})"', block)
        if catalog:
            facts["models_in_help"] = catalog
            missing = [m for m in self.models if m not in catalog]
            if missing:
                notes.append(f"configured model(s) not in this build's help: {', '.join(missing)}")
        facts["login"] = "not verified offline; the first run reports AUTH_REQUIRED until the CLI is logged in"
        detail = "ready for text-answer tasks" + (f" ({'; '.join(notes)})" if notes else "")
        return ProbeResult(self.provider_id, ProviderStatus.AVAILABLE, detail, version=version, executable=exe, facts=facts)
