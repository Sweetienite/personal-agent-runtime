"""Codex adapter (P3): a translator for ``codex exec --json``, the CLI bundled in ChatGPT.app.

An adapter only translates: it builds the argv, says what the child sees (environment, standard input), turns the
vendor's JSONL into events and classifies how a run ended. Processes, timeouts, cancellation escalation, the working
copy and every verification belong to the Kernel.

What this adapter insists on, and why (details and the evidence for each flag: docs/CODEX.md):

* the executable is the one the user configured by absolute path - never found on PATH (the PATH ``codex`` is broken)
  - and it must carry OpenAI's code signature;
* the user's own Codex configuration stays in force (``CODEX_HOME`` is the real one), so ``memories=false``, the rules
  and the sandbox baseline are not replaced; nothing like ``--ignore-user-config`` is ever passed;
* every Worker run still switches OFF what the user's setup would otherwise hand the model: ChatGPT apps/connectors,
  plugins, browser/desktop control, sub-agents, hooks, MCP servers (some of which run outside the sandbox) ...;
* the prompt goes through standard input, the sandbox mode is chosen by the Kernel's task (read-only unless the task
  may write), and the environment holds only ``CODEX_HOME`` and proxy/certificate settings - no API keys, so an
  environment variable can never switch the run to metered billing.

Event shapes follow the open-source ``codex-rs/exec/src/exec_events.rs``. Anything not yet seen in a real run of
the installed version is listed in docs/CODEX.md as unverified, and unknown event types are tolerated, not trusted.
"""
from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from par.core.redact import redact_text
from par.providers.base import (
    Capabilities, FailureClass, ProbeResult, ProviderEvent, ProviderStatus, RunOutcome, RunRequest, Tri,
)

BUNDLED_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
TESTED_VERSION = "0.162.0-alpha.17.2"
OPENAI_TEAM_ID = "2DC432GLL2"
MAX_PROMPT_BYTES = 400_000
MAX_TEXT = 4000
PROBE_TIMEOUT = 30.0
ISOLATION_TTL = 300.0
SIGNATURE_TTL = 600.0

# Feature flags switched off for every Worker run. A name unknown to the installed version is simply not passed
# (the version would reject it); ``probe`` reports which ones were dropped.
DISABLED_FEATURES: "dict[str, str]" = {
    "memories": "the user's safety invariant (memories off) is re-asserted instead of left to config drift",
    "apps": "ChatGPT apps/connectors would give the model reach into external accounts",
    "plugins": "plugins bring skills and MCP servers that run outside the sandbox",
    "plugin_sharing": "plugin machinery, not needed by a Worker",
    "remote_plugin": "plugin machinery, not needed by a Worker",
    "skill_mcp_dependency_install": "would install MCP dependencies on behalf of skills",
    "hooks": "the user's hook commands would run during a Worker session",
    "computer_use": "desktop control",
    "browser_use": "browser control",
    "browser_use_external": "browser control",
    "browser_use_full_cdp_access": "full DevTools-protocol access to the user's browser",
    "in_app_browser": "browser control",
    "multi_agent": "sub-agents would multiply what one run can do",
    "multi_agent_v2": "sub-agents would multiply what one run can do",
    "image_generation": "an extra cost surface",
    "shell_snapshot": "would write snapshots of the shell environment under CODEX_HOME",
    "goals": "persistent goal state",
    "fast_mode": "a higher usage rate",
    "ultrafast_mode": "a higher usage rate",
    "daemon_auto_start": "a background daemon would outlive the process group the Kernel can stop",
    "unbounded_connection_retries": "a failing connection must surface as an error, not retry forever",
    "tool_suggest": "tool and connector suggestions",
}
# The user's config may name a `notify` program that Codex starts, outside the sandbox, whenever a turn ends.
# A Worker run starts nothing the Kernel did not start: an empty list switches it off.
NO_NOTIFY = ["-c", "notify=[]"]
PASSTHROUGH = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "CODEX_CA_CERTIFICATE")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,99}\Z")
_SERVER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
PROBE_PATH = "/usr/bin:/bin"


class CodexProblem(Exception):
    pass


def clip(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else ""
    return redact_text(text[:limit])


# --- running the CLI offline (probes) -----------------------------------------------------------------------------------
def _clean_env(codex_home: str, home: str, environ: Mapping[str, str]) -> "dict[str, str]":
    env = {"PATH": PROBE_PATH, "HOME": home, "CODEX_HOME": codex_home, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "NO_COLOR": "1"}
    env.update({k: environ[k] for k in PASSTHROUGH if k in environ})
    return env


def run_codex(exe: str, args: "list[str]", *, codex_home: str, environ: Mapping[str, str], timeout: float = PROBE_TIMEOUT) -> "tuple[int, str, str]":
    """Run a Codex subcommand that does not call the model, in a clean environment with a throwaway HOME."""
    with tempfile.TemporaryDirectory(prefix="par-codex-probe-") as home:
        try:
            proc = subprocess.run([exe, *args], env=_clean_env(codex_home, home, environ), capture_output=True, text=True,
                                  timeout=timeout, cwd=home, stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout:g}s"
        except OSError as exc:
            return 127, "", str(exc.strerror or exc)
    return proc.returncode, proc.stdout, proc.stderr


def parse_features(text: str) -> "dict[str, tuple[str, bool]]":
    """``name  stage  true|false`` lines -> {name: (stage, enabled)}."""
    found: "dict[str, tuple[str, bool]]" = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] in ("true", "false"):
            found[parts[0]] = (" ".join(parts[1:-1]), parts[-1] == "true")
    return found


def enabled_mcp_servers(json_text: str) -> "list[str]":
    try:
        servers = json.loads(json_text)
    except json.JSONDecodeError:
        raise CodexProblem("`mcp list --json` did not return JSON") from None
    if not isinstance(servers, list):
        raise CodexProblem("`mcp list --json` returned an unexpected shape")
    return [s["name"] for s in servers if isinstance(s, dict) and s.get("enabled") is True and isinstance(s.get("name"), str)]


# --- isolation: which extras to switch off for THIS install ---------------------------------------------------------------
@dataclass(frozen=True)
class Isolation:
    disable: "tuple[str, ...]"
    dropped: "tuple[str, ...]"           # known to us, unknown to the installed version
    mcp_off: "tuple[str, ...]"
    verified: bool
    problems: "tuple[str, ...]"

    def flags(self) -> "list[str]":
        out: "list[str]" = []
        for name in self.disable:
            out += ["--disable", name]
        for server in self.mcp_off:
            out += ["-c", f"mcp_servers.{server}.enabled=false"]
        return out + NO_NOTIFY


_ISOLATION_CACHE: "dict[tuple, tuple[float, Isolation]]" = {}
_SIGNATURES: "dict[tuple, tuple[float, str | None]]" = {}
_LOGINS: "dict[tuple, tuple[float, tuple[str, str]]]" = {}
_CACHE_LOCK = threading.Lock()
LOGIN_TTL = 60.0


def discover_isolation(exe: str, codex_home: str, environ: Mapping[str, str], *, ttl: float = ISOLATION_TTL) -> Isolation:
    key = (exe, codex_home)
    with _CACHE_LOCK:
        hit = _ISOLATION_CACHE.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
    result = _discover(exe, codex_home, environ)
    with _CACHE_LOCK:
        _ISOLATION_CACHE[key] = (time.monotonic(), result)
    return result


def forget_isolation(exe: str, codex_home: str) -> None:
    with _CACHE_LOCK:
        _ISOLATION_CACHE.pop((exe, codex_home), None)


def forget_caches() -> None:
    with _CACHE_LOCK:
        _ISOLATION_CACHE.clear()
        _SIGNATURES.clear()
        _LOGINS.clear()


def login_state(exe: str, codex_home: str, environ: Mapping[str, str], *, ttl: float = LOGIN_TTL) -> "tuple[str, str]":
    """(state, first line of what ``login status`` said): state is 'chatgpt', 'api_key', 'none' or 'unknown'."""
    key = (exe, codex_home)
    with _CACHE_LOCK:
        hit = _LOGINS.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
    rc, out, err = run_codex(exe, ["login", "status"], codex_home=codex_home, environ=environ)
    said = (out + err).strip()
    text = said.lower()
    if "not logged in" in text:
        state = "none"
    elif rc == 0 and "logged in using chatgpt" in text:
        state = "chatgpt"
    elif "api key" in text:
        state = "api_key"
    else:
        state = "unknown"
    result = (state, redact_text(said.splitlines()[0] if said else "")[:120])
    with _CACHE_LOCK:
        _LOGINS[key] = (time.monotonic(), result)
    return result


def _discover(exe: str, codex_home: str, environ: Mapping[str, str]) -> Isolation:
    problems: "list[str]" = []
    rc, out, err = run_codex(exe, ["features", "list"], codex_home=codex_home, environ=environ)
    if rc != 0:
        return Isolation((), (), (), False, (f"`features list` failed ({rc}): {redact_text(err.strip())[:120]}",))
    known = parse_features(out)
    disable = tuple(n for n in DISABLED_FEATURES if n in known)
    dropped = tuple(n for n in DISABLED_FEATURES if n not in known)
    if "memories" not in disable:
        problems.append("this Codex has no `memories` feature flag to re-assert")
    base_flags: "list[str]" = []
    for name in disable:
        base_flags += ["--disable", name]
    base_flags += NO_NOTIFY
    rc, out, err = run_codex(exe, [*base_flags, "mcp", "list", "--json"], codex_home=codex_home, environ=environ)
    if rc != 0:
        return Isolation(disable, dropped, (), False, tuple(problems) + (f"`mcp list` failed ({rc}): {redact_text(err.strip())[:120]}",))
    try:
        still = enabled_mcp_servers(out)
    except CodexProblem as exc:
        return Isolation(disable, dropped, (), False, tuple(problems) + (str(exc),))
    bad = [n for n in still if not _SERVER.match(n)]
    if bad:
        return Isolation(disable, dropped, (), False, tuple(problems) + (f"MCP server names cannot be switched off safely: {bad}",))
    mcp_off = tuple(still)
    # the proof: with everything applied, nothing may remain enabled and `memories` must read false
    flags = Isolation(disable, dropped, mcp_off, True, ()).flags()
    rc, out, err = run_codex(exe, [*flags, "mcp", "list", "--json"], codex_home=codex_home, environ=environ)
    try:
        left = enabled_mcp_servers(out) if rc == 0 else ["<unknown>"]
    except CodexProblem:
        left = ["<unknown>"]
    if left:
        problems.append(f"MCP servers still enabled after switching them off: {left}")
    rc, out, err = run_codex(exe, [*flags, "features", "list"], codex_home=codex_home, environ=environ)
    after = parse_features(out) if rc == 0 else {}
    if after.get("memories", ("", True))[1]:
        problems.append("the memories feature is still ON after being disabled")
    return Isolation(disable, dropped, mcp_off, not problems, tuple(problems))


# --- the vendor's messages ---------------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").lower()


_QUOTA = ("you've hit your usage limit", "you hit your spend cap", "out of credits", "quota exceeded", "usage limit",
          "to use codex with your chatgpt plan")
_AUTH = ("unauthorized", "refresh token", "sign in again", "log out and sign in", "not logged in", "please log in",
         "login required", "invalid_api_key", "token expired", "access token could not be refreshed", "auth error")
_RATE = ("rate limit exceeded", "too many requests", "exceeded retry limit, last status: 429", "status: 429",
         "unexpected status 429", "is at capacity", "capacity unavailable")
_TRANSIENT = ("stream disconnected before completion", "connection failed", "request timed out", "experiencing high demand",
              "internal server error", "agent loop died", "error while reading the server response", "temporarily unavailable",
              "unexpected status 500", "unexpected status 502", "unexpected status 503", "unexpected status 504")
_NOT_TRANSIENT = ("content_filter", "incomplete response returned")
_MODEL_WORDS = ("does not exist", "not supported", "not found", "unknown model", "do not have access", "is not available")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_RESET = re.compile(r"try again at (?P<when>[^.]*?(?:am|pm))\b", re.I)


def parse_reset_time(message: str, now: "datetime | None" = None) -> "str | None":
    """The reset time inside an official usage-limit message ("... try again at 7:30 PM." or "... at Oct 15th, 2026 3:05 AM."),
    as an ISO UTC timestamp, or None. Codex formats it in the machine's local time zone; the same machine parses it here."""
    m = _RESET.search(message.replace("’", "'"))
    if not m:
        return None
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", m.group("when").strip(), flags=re.I)
    local = (now or datetime.now()).astimezone()
    try:
        if re.match(r"^\d{1,2}:\d{2}\s*[ap]m$", text, re.I):
            t = datetime.strptime(text.upper().replace(" ", ""), "%I:%M%p")
            when = local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if when <= local:                                    # a reset is always ahead: "3:05 AM" at 11 PM means tomorrow
                when += timedelta(days=1)
        else:
            parts = re.match(r"^([A-Za-z]{3})[a-z]*\s+(\d{1,2}),\s*(\d{4})\s+(\d{1,2}):(\d{2})\s*([ap]m)$", text, re.I)
            if not parts or parts.group(1).lower() not in _MONTHS:
                return None
            hour = int(parts.group(4)) % 12 + (12 if parts.group(6).lower() == "pm" else 0)
            when = local.replace(year=int(parts.group(3)), month=_MONTHS[parts.group(1).lower()], day=int(parts.group(2)),
                                 hour=hour, minute=int(parts.group(5)), second=0, microsecond=0)
    except ValueError:
        return None
    return when.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def classify_text(*messages: str) -> "FailureClass":
    """Map the vendor's own failure text to a class. Order matters: an exhausted quota beats a login problem beats a
    rate limit. A bare '429' is a rate limit - only the vendor's usage-limit wording means the quota is gone."""
    text = _norm(" | ".join(m for m in messages if m))
    if any(w in text for w in _QUOTA):
        return FailureClass.QUOTA_EXHAUSTED
    if any(w in text for w in _AUTH) or re.search(r"\b401\b", text):
        return FailureClass.AUTH_REQUIRED
    if "model" in text and any(w in text for w in _MODEL_WORDS):
        return FailureClass.MODEL_UNAVAILABLE
    if any(w in text for w in _RATE) or re.search(r"\b429\b", text):
        return FailureClass.RATE_LIMITED
    if any(w in text for w in _TRANSIENT) and not any(w in text for w in _NOT_TRANSIENT):
        return FailureClass.TRANSIENT_ERROR
    return FailureClass.EXECUTION_FAILED


# --- the adapter ---------------------------------------------------------------------------------------------------------------
def _usage(raw: Any) -> "dict[str, int]":
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")
            if isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool) and raw[k] >= 0}


class CodexAdapter:
    provider_id = "codex"
    kind = "codex"
    CAPABILITIES = Capabilities(
        can_headless=Tri.YES, structured_output=Tri.YES, can_select_model=Tri.YES, can_write=Tri.YES,
        can_restrict_tools=Tri.NO,            # no per-tool allow-list; the sandbox limits model-run shell commands only
        has_sandbox=Tri.YES, session_persistence_control=Tri.YES, supports_subscription_auth=Tri.YES)
    # Measured (docs/CODEX.md §10): a writable (`-s workspace-write`) run inside a git workspace makes Codex append a
    # `[projects."<workspace>"] trust_level = "trusted"` entry to the USER'S ~/.codex/config.toml at startup - every attempt, and
    # `-c projects.….trust_level=…` does not prevent it. The same files without `.git` do not trigger it. The Kernel never uses
    # the Worker's repository, so Codex gets a plain copy.
    workspace_with_git = False

    def __init__(self, executable: str, *, provider_id: str = "codex", codex_home: "str | None" = None,
                 environ: "Mapping[str, str] | None" = None, models: "tuple[str, ...]" = (), verify_signature: bool = True) -> None:
        self.executable = executable
        self.verify_signature = verify_signature            # only tests turn this off (a fake program has no signature)
        self.provider_id = provider_id
        self.environ = dict(os.environ if environ is None else environ)
        self.codex_home = codex_home or self.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        self.models = tuple(models)

    @classmethod
    def from_entry(cls, entry: Any, *, environ: "Mapping[str, str] | None" = None, models: "tuple[str, ...]" = ()) -> "CodexAdapter":
        return cls(entry.executable or "", provider_id=entry.id, environ=environ, models=models)

    # --- trust in the binary -----------------------------------------------------------------------------------------------------
    def executable_problem(self) -> "str | None":
        p = Path(self.executable)
        if not self.executable or not p.is_absolute():
            return "the Codex executable must be configured as an absolute path (the one on PATH is never used)"
        if not p.is_file():
            return f"no executable at {self.executable}"
        st = p.stat()
        if not os.access(self.executable, os.X_OK):
            return f"{self.executable} is not executable"
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return f"{self.executable} is writable by other users"
        if st.st_uid not in (0, os.getuid()):
            return f"{self.executable} belongs to another user"
        return None

    def signature_problem(self) -> "str | None":
        """On macOS the executable must be the Mach-O binary inside OpenAI's signed bundle, and that bundle must still verify.

        A wrapper script (the ChatGPT app's ``bin/codex``) is refused: a script carries no signature of its own."""
        if sys.platform != "darwin" or not self.verify_signature:
            return None
        p = Path(self.executable).resolve()
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size, st.st_ino)
        with _CACHE_LOCK:
            hit = _SIGNATURES.get(key)
            if hit and time.monotonic() - hit[0] < SIGNATURE_TTL:
                return hit[1]
        problem = self._check_signature(p)
        with _CACHE_LOCK:
            _SIGNATURES[key] = (time.monotonic(), problem)
        return problem

    @staticmethod
    def _check_signature(p: Path) -> "str | None":
        try:
            with open(p, "rb") as f:
                head = f.read(4)
        except OSError as exc:
            return f"cannot read the executable: {exc.strerror or exc}"
        if head[:2] == b"#!":
            return f"{p} is a script, which carries no signature: point `executable` at the signed binary inside CodexCLI.app (Contents/MacOS/codex)"
        bundle = next((parent for parent in p.parents if parent.suffix == ".app"), None)
        if bundle is None:
            return f"{p} is not inside a signed .app bundle"
        try:
            verify = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(bundle)], capture_output=True, text=True, timeout=120)
            info = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=2", str(bundle)], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"could not check the code signature: {exc}"
        team = re.search(r"^TeamIdentifier=(.+)$", info.stderr, re.M)
        if verify.returncode != 0:
            return "the code signature does not verify: " + redact_text(verify.stderr.strip())[:120]
        if not team or team.group(1).strip() != OPENAI_TEAM_ID:
            return f"the executable is not signed by OpenAI (team: {team.group(1).strip() if team else 'none'})"
        return None

    def isolation(self) -> Isolation:
        return discover_isolation(self.executable, self.codex_home, self.environ)

    # --- the adapter protocol ----------------------------------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return self.CAPABILITIES

    def preflight(self, request: RunRequest) -> "Optional[str]":
        problem = self.executable_problem()
        if problem:
            return problem
        if not Path(self.codex_home).is_dir():
            return f"the Codex home {self.codex_home} does not exist: sign in with the ChatGPT app or `codex login` first"
        if not _MODEL.match(request.model or ""):
            return f"{request.model!r} is not a usable model slug"
        if len(request.prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            return f"the prompt is larger than {MAX_PROMPT_BYTES} bytes"
        problem = self.signature_problem()
        if problem:
            return problem
        state, said = login_state(self.executable, self.codex_home, self.environ)
        if state == "api_key":
            return "Codex is logged in with an API key (metered billing); the Runtime only uses the ChatGPT subscription login"
        if state == "none":
            return "Codex is not logged in: sign in with the ChatGPT app or `codex login` (the Runtime never handles credentials)"
        if state != "chatgpt":
            return f"could not confirm a ChatGPT-subscription login (`codex login status` said: {said or 'nothing'})"
        iso = self.isolation()
        if iso.verified and self._still_enabled(iso):
            forget_isolation(self.executable, self.codex_home)          # the setup changed since the proof was made: look again
            iso = self.isolation()
        if not iso.verified:
            return "could not verify that Codex's extra tools are switched off: " + "; ".join(iso.problems)
        return None

    def _still_enabled(self, iso: Isolation) -> "list[str]":
        """One cheap offline call per attempt: with this run's flags applied, which MCP servers would still start?
        (The full discovery is cached for minutes; a server added to the user's config meanwhile must not slip in.)"""
        rc, out, _ = run_codex(self.executable, [*iso.flags(), "mcp", "list", "--json"], codex_home=self.codex_home, environ=self.environ)
        if rc != 0:
            return ["<could not ask>"]
        try:
            return enabled_mcp_servers(out)
        except CodexProblem:
            return ["<unreadable>"]

    def build_argv(self, request: RunRequest) -> "list[str]":
        if not _MODEL.match(request.model or ""):
            raise ValueError(f"{request.model!r} is not a usable model slug")
        iso = self.isolation()
        return [self.executable, "exec", "--json", "--color", "never", "--ephemeral", "--skip-git-repo-check",
                "-s", "workspace-write" if request.allow_write else "read-only",
                "-m", request.model, "-C", str(request.workspace), *iso.flags(), "-"]

    def environment(self, request: RunRequest) -> "dict[str, str]":
        env = {"CODEX_HOME": self.codex_home}
        env.update({k: self.environ[k] for k in PASSTHROUGH if k in self.environ})
        return env

    def stdin_data(self, request: RunRequest) -> "Optional[bytes]":
        return request.prompt.encode("utf-8")

    def parse_event(self, line: str) -> "Optional[ProviderEvent]":
        text = line.strip()
        if not text:
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return ProviderEvent("unparsed", {"length": len(text)})            # the raw text is not kept
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            return ProviderEvent("unparsed", {"length": len(text)})
        kind = obj["type"]
        if kind == "thread.started":
            return ProviderEvent("session", {"thread_id": clip(obj.get("thread_id"), 80)})
        if kind == "turn.started":
            return ProviderEvent("other", {"type": kind})
        if kind in ("item.started", "item.updated", "item.completed"):
            return self._item(kind.split(".")[1], obj.get("item"))
        if kind == "turn.completed":
            return ProviderEvent("terminal", {"status": "SUCCESS", "usage": _usage(obj.get("usage"))})
        if kind == "turn.failed":
            error = obj.get("error")
            message = clip(error.get("message") if isinstance(error, dict) else None)
            data: "dict[str, Any]" = {"status": "ERROR", "message": message}
            reset = parse_reset_time(message)
            if reset:
                data["reset_at"] = reset
            return ProviderEvent("terminal", data)
        if kind == "error":
            message = clip(obj.get("message"))
            data = {"message": message}
            reset = parse_reset_time(message)
            if reset:
                data["reset_at"] = reset
            return ProviderEvent("error", data)
        return ProviderEvent("other", {"type": clip(kind, 60)})

    def _item(self, phase: str, item: Any) -> "ProviderEvent":
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            return ProviderEvent("unparsed", {"length": 0})
        kind, status = item["type"], item.get("status") if isinstance(item.get("status"), str) else None
        if kind == "agent_message":
            return ProviderEvent("message", {"text": clip(item.get("text")), "phase": phase})
        if kind == "reasoning":
            return ProviderEvent("other", {"type": "reasoning", "chars": len(item.get("text") or "")})
        if kind == "command_execution":
            if status == "declined":
                # exec mode cannot grant an approval, so the CLI refused it: the run may be incomplete, and a person should see that
                return ProviderEvent("warning", {"message": f"a command was declined (it needed an approval that exec mode cannot give): {clip(item.get('command'), 80)}"})
            return ProviderEvent("tool", {"tool": "command", "phase": phase, "status": status,
                                          "exit_code": item.get("exit_code") if isinstance(item.get("exit_code"), int) else None,
                                          "command": clip(item.get("command"), 200)})
        if kind == "file_change":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            paths = [clip(c.get("path"), 200) for c in changes[:20] if isinstance(c, dict)]
            if status == "declined":
                return ProviderEvent("warning", {"message": f"a file change was declined (it needed an approval that exec mode cannot give): {', '.join(paths[:3])}"})
            return ProviderEvent("tool", {"tool": "file_change", "phase": phase, "status": status, "paths": paths})
        if kind == "error":
            return ProviderEvent("warning", {"message": clip(item.get("message"))})
        if kind in ("mcp_tool_call", "collab_tool_call", "web_search"):
            what = {"mcp_tool_call": f"an MCP tool was called ({clip(item.get('server'), 60)}/{clip(item.get('tool'), 60)}) although MCP is switched off",
                    "collab_tool_call": "a sub-agent tool was used although multi-agent is switched off",
                    "web_search": "the Worker used web search"}[kind]
            return ProviderEvent("tool", {"tool": kind, "phase": phase, "status": status, "note": what})
        return ProviderEvent("tool", {"tool": clip(kind, 60), "phase": phase, "status": status})

    def classify_failure(self, outcome: RunOutcome) -> "Optional[FailureClass]":
        """None = a clean provider-level success (still not acceptance). Exit code 0 alone is never success: a clean
        success needs the CLI's own ``turn.completed``. Vendor failures are recognised by version-pinned message families
        (docs/CODEX.md); anything unrecognised is EXECUTION_FAILED, never a guess at quota or login problems."""
        if outcome.timed_out:
            return FailureClass.UNKNOWN
        events = outcome.events
        terminal = next((e for e in reversed(events) if e.kind == "terminal"), None)
        errors = [str(e.data.get("message", "")) for e in events if e.kind == "error"]
        stderr = outcome.stderr_tail or ""
        failed_exit = (outcome.exit_code not in (0, None)) or outcome.signal is not None
        if terminal is not None and terminal.data.get("status") == "ERROR":
            return classify_text(str(terminal.data.get("message", "")), *errors[-3:])
        if terminal is not None and terminal.data.get("status") == "SUCCESS":
            # a declined command is a warning on the result, not a failure: exec mode cannot grant approvals, so a retry
            # could not change it, and the Kernel verifies the outcome itself
            return classify_text(*errors[-3:], stderr) if failed_exit else None
        # no terminal event at all
        if not failed_exit or (outcome.signal is not None and outcome.exit_code in (None, 0)):
            return FailureClass.UNKNOWN                      # the stream simply ended (exit 0), or the process was killed: unknown, not failed
        return classify_text(*errors[-3:], stderr)

    def request_cancel(self, process: "subprocess.Popen[Any]") -> None:
        """Codex handles only Ctrl-C (SIGINT) gracefully: it interrupts the turn and exits. SIGTERM would just kill it."""
        if process.poll() is None:
            process.send_signal(signal.SIGINT)

    def persistence_report(self) -> "dict[str, Any]":
        home = self.codex_home
        return {
            "sessions": f"not written for Worker runs (--ephemeral); interactive use keeps them under {home}/sessions",
            "logs": f"the CLI still writes its own diagnostics under {home} (log/, logs_2.sqlite) and may append to history.jsonl",
            "memory": "off: re-asserted for every run with --disable memories (the user's config also says false)",
            "paths": [home],
            "credentials": f"{home}/auth.json belongs to the CLI; the Runtime never opens it and never copies it",
        }

    def probe(self) -> ProbeResult:
        """No model call: the executable, its signature, its version, the login state, the isolation proof, the model catalog."""
        exe = self.executable
        problem = self.executable_problem()
        if problem:
            return ProbeResult(self.provider_id, ProviderStatus.UNAVAILABLE, problem, executable=exe or None)
        facts: "dict[str, Any]" = {"codex_home": self.codex_home}
        if not Path(self.codex_home).is_dir():                       # before anything runs: a CLI pointed at nothing may create it
            return ProbeResult(self.provider_id, ProviderStatus.AUTH_REQUIRED, f"{self.codex_home} does not exist: sign in with the ChatGPT app or `codex login` first", executable=exe, facts=facts)
        problem = self.signature_problem()
        facts["signature"] = "valid, OpenAI" if problem is None else problem
        if problem:
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, problem, executable=exe, facts=facts)
        rc, out, err = run_codex(exe, ["--version"], codex_home=self.codex_home, environ=self.environ)
        version = out.strip().split()[-1] if rc == 0 and out.strip() else None
        if version is None:
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, f"`--version` failed ({rc}): {redact_text(err.strip())[:120]}", executable=exe, facts=facts)
        facts["tested_version"] = version == TESTED_VERSION
        notes = [] if version == TESTED_VERSION else [f"untested version (tested: {TESTED_VERSION})"]
        state, said = login_state(exe, self.codex_home, self.environ, ttl=0.0)
        facts["login"] = {"chatgpt": "ChatGPT", "api_key": "API key", "none": "not logged in"}.get(state, "unknown")
        if state == "api_key":
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, "Codex is logged in with an API key (metered billing); the Runtime only uses the ChatGPT subscription login", version=version, executable=exe, facts=facts)
        if state == "none":
            return ProbeResult(self.provider_id, ProviderStatus.AUTH_REQUIRED, "not logged in: sign in with the ChatGPT app or `codex login` (the Runtime never handles credentials)", version=version, executable=exe, facts=facts)
        if state != "chatgpt":
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, f"could not confirm a ChatGPT-subscription login (`codex login status` said: {said or 'nothing'})", version=version, executable=exe, facts=facts)
        iso = self.isolation()
        facts["isolation"] = {"verified": iso.verified, "features_disabled": len(iso.disable), "features_dropped": list(iso.dropped),
                              "mcp_servers_switched_off": list(iso.mcp_off), "problems": list(iso.problems)}
        if not iso.verified:
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, "isolation could not be verified: " + "; ".join(iso.problems), version=version, executable=exe, facts=facts)
        rc, out, err = run_codex(exe, ["debug", "models"], codex_home=self.codex_home, environ=self.environ)
        catalog: "list[str]" = []
        if rc == 0:
            try:
                catalog = [m["slug"] for m in json.loads(out).get("models", []) if isinstance(m, dict) and isinstance(m.get("slug"), str)]
            except (json.JSONDecodeError, AttributeError):
                notes.append("the model catalog could not be read")
        facts["models_in_catalog"] = catalog
        missing = [m for m in self.models if catalog and m not in catalog]
        facts["configured_models_missing"] = missing
        if missing:
            notes.append(f"configured model(s) not in this install's catalog: {', '.join(missing)}")
        detail = "ready: signed by OpenAI, logged in with ChatGPT, extras switched off" + (f" ({'; '.join(notes)})" if notes else "")
        return ProbeResult(self.provider_id, ProviderStatus.AVAILABLE, detail, version=version, executable=exe, facts=facts)
