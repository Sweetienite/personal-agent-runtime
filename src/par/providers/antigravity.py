"""Antigravity adapter (P4): a translator for ``agy``, Google's Antigravity CLI.

Like the Codex adapter it only translates: it builds the argv, says what the child sees (environment, standard input, the files in
its private HOME), turns the vendor's stream-json into events and classifies how a run ended. Processes, timeouts, cancellation
escalation, the working copy and every verification belong to the Kernel.

What this adapter insists on, and why (details and the evidence for each choice: docs/AGY.md):

* the executable is the one the user configured by absolute path - never found on PATH - and it must carry Google's code signature;
* ``agy`` never sees the user's own ``~/.gemini`` (IDE state, legacy Gemini CLI credentials, global rules, global MCP/hook/sidecar
  configuration). The Worker's HOME is the attempt's private one; the adapter writes the ONLY configuration ``agy`` reads there
  (``settings.json``: permission preset, sandbox, no telemetry, no AI-credit spending) and links the user's login keychain into it, so
  the one-time Google sign-in is found without handing the CLI anything else;
* a read-only task runs with the ``strict`` preset (every non-read tool is soft-denied), a writing task with ``request-review`` plus
  ``write_file(<path>)`` allow-rules for exactly the paths its contract allows. Measured on 1.3.5: headless mode cannot ask, and without
  a rule even a file edit inside the private working copy is soft-denied; shell commands stay soft-denied (the Runtime never writes a
  ``command(...)`` rule, a wildcard or a pattern rule). The ``init`` event reports the permission mode the CLI really runs with;
  anything wider than what was asked for fails the attempt;
* a project that brings its own hooks, MCP servers or plugins (``.agents/hooks.json`` ...) is refused: those run shell commands;
* the prompt goes through standard input as one stream-json ``user`` event, and the environment holds only proxy/certificate settings and
  the switch that disables the CLI's self-updater - no API keys, so nothing can move the run to metered billing.

Soft denials are the one failure mode specific to this CLI: a tool that needs an approval nobody can give is skipped, the run goes on
and exits 0 with a notice on stderr. They become ``warning`` events (shown on the result), never a silent success.

Event shapes follow the official headless-mode documentation. Anything not yet seen in a real run of the installed version is listed in
docs/AGY.md as unverified, and unknown event types are tolerated, not trusted.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from par.core.redact import redact_text
from par.providers.base import (
    Capabilities, FailureClass, ProbeResult, ProviderEvent, ProviderStatus, RunOutcome, RunRequest, Tri,
)

TESTED_VERSION = "1.3.5"
GOOGLE_TEAM_ID = "EQHXZ8M8AV"
MAX_PROMPT_BYTES = 400_000
MAX_TEXT = 4000
PROBE_TIMEOUT = 30.0
ACCOUNT_TIMEOUT = 60.0
SIGNATURE_TTL = 600.0
LOGIN_TTL = 60.0
LOGIN_HOME = "agy_login_home"      # under the Runtime data directory: where the one-time interactive sign-in runs
LOGIN_CWD = ("scratch", "agy_login_cwd")
PRINT_TIMEOUT = "24h"             # the CLI's own wait limit (default 5 minutes) must never fire before the Kernel's timeouts do
PROBE_PATH = "/usr/bin:/bin"
PASSTHROUGH = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
               "SSL_CERT_FILE", "SSL_CERT_DIR")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,99}\Z")

# The permission presets of the CLI, narrowest first (docs: settings, `toolPermission`).
STRICT, REVIEW = "strict", "request-review"
PERMISSION_RANK = {STRICT: 0, REVIEW: 1, "accept-edits": 1, "proceed-in-sandbox": 2, "always-proceed": 3}   # accept-edits = request-review that also approves file edits

# How a file-changing task is allowed to edit files in headless mode (1.3.5 soft-denies them under request-review): "rules" writes write_file allow-rules for the
# contract's paths (default); "accept-edits" asks the CLI for its own `--mode accept-edits` (the binary describes it as "auto-approve file edits, prompt for
# commands"). The second exists to be tried if the first is not matched by the real CLI; PAR_AGY_WRITE_STRATEGY selects it (the real smoke test has a flag).
WRITE_STRATEGIES = ("rules", "accept-edits")

# The configuration the Worker's `agy` reads - written by the Runtime, never taken from the user's own files. Every key is a documented
# `settings.json` key (docs: CLI reference); `toolPermission` is added per task.
FIXED_SETTINGS: "dict[str, Any]" = {
    "allowNonWorkspaceAccess": False,       # file tools stay inside the working copy
    "enableTerminalSandbox": True,          # a command that is ever allowed runs inside the OS sandbox
    "useG1Credits": False,                  # never spend personal AI credits when the plan quota is used up
    "enableTelemetry": False,
    "notifications": False,
    "showTips": False,
    "showFeedbackSurvey": False,
    "altScreenMode": "never",
}

# The only permission rules the Runtime ever writes: ``write_file(<path>)`` for a file-changing task, for the paths its contract allows
# (the contract already refuses globs, "..", absolute paths and sensitive names; this is the narrower alphabet a rule can carry without the
# CLI's own rule parser reading it differently).
_RULE_PATH = re.compile(r"[A-Za-z0-9_./-]+\Z")
_RULE_BAD = re.compile(r"[()*?\[\]{}\\\x00-\x1f\x7f]")                     # characters that would make a rule target ambiguous
_WRITE_RULE = "write_file("

# Project files that make ``agy`` run something the Kernel did not start (hooks are shell commands, MCP servers are processes) are
# refused; unknown entries in ``.agents`` are refused too (this adapter has not been taught what they do). Rules and skills are text.
_AGENTS_DIRS = (".agents", ".agent")
_TEXT_ONLY = {".agents": {"rules", "skills", "rules.json", "AGENTS.md", "GEMINI.md"}, ".agent": {"rules", "skills"}}

_CACHE_LOCK = threading.Lock()
_SIGNATURES: "dict[tuple[str, int, int, int], tuple[float, str | None]]" = {}
_LOGINS: "dict[str, tuple[float, str, str, tuple[str, ...]]]" = {}
_MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def clip(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else ""
    return redact_text(text[:limit])


def forget_caches() -> None:
    with _CACHE_LOCK:
        _SIGNATURES.clear()
        _LOGINS.clear()


def real_home() -> Path:
    """The user's actual home directory (what the password database says, not what an environment variable claims)."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


# --- the Worker's private HOME ---------------------------------------------------------------------------------------------------
def refuse_real_home(home: Path) -> None:
    """A private HOME (a Worker's, a probe's, the sign-in's) is written to and chmod-ed: it must never BE the user's real home directory.
    Found the hard way in P4: a mutation check that pointed the sign-in plan at real_home() changed the real home's permissions and wrote
    into the real ~/.gemini. Every function that creates a private HOME calls this first, before it touches anything."""
    if os.path.realpath(home) == os.path.realpath(real_home()):
        raise ValueError("a private HOME must never be the user's real home directory")


def rules_problem(allowed_paths: "Sequence[str]") -> "str | None":
    """Why a file-changing task cannot be given its write rules, or None."""
    if not allowed_paths:
        return "a file-changing task needs allowed_paths: without them agy cannot be given permission to change any file"
    for entry in allowed_paths:
        if (not isinstance(entry, str) or not _RULE_PATH.match(entry) or entry.startswith("/") or ".." in entry.split("/")
                or entry.strip("/") in ("", ".")):
            return (f"the allowed path {entry!r} cannot be written as a permission rule for agy (only letters, digits and . _ - / are supported): "
                    "rename it, or give the task to another Worker")
    return None


def write_rules(allowed_paths: "Sequence[str]", workspace: "Path | str") -> "list[str]":
    """``write_file(<path>)`` for each path the contract allows - relative to the working copy (the form the vendor's headless documentation
    shows) and again absolute inside it (the form its permission notice and its own automation skill use; the real CLI names the absolute
    path of the file it was denied). Nothing outside the working copy, no wildcard, never a command."""
    base = os.path.realpath(workspace).rstrip("/")                  # the path agy sees as its working directory
    absolute = base.startswith("/") and not _RULE_BAD.search(base)
    rules: "list[str]" = []
    for entry in allowed_paths:
        for rule in (f"{_WRITE_RULE}{entry})", f"{_WRITE_RULE}{base}/{entry})" if absolute else None):
            if rule is not None and rule not in rules:
                rules.append(rule)
    return rules


def checked_rules(rules: "Sequence[str]") -> "list[str]":
    """Every rule the Runtime writes passes through here: ``write_file(<path>)`` only - no command rule, no wildcard, no pattern rule, nothing
    that is the root or leaves the working copy. (A relative target is plain path characters only, which also rules out the ``regex:`` form
    and ``~``; an absolute one starts with a slash and carries none of the ambiguous characters.)"""
    checked: "list[str]" = []
    for rule in rules:
        target = rule[len(_WRITE_RULE):-1] if isinstance(rule, str) and rule.startswith(_WRITE_RULE) and rule.endswith(")") else None
        if (target is None or not target.strip("/") or _RULE_BAD.search(target) or ".." in target.split("/")
                or (not target.startswith("/") and not _RULE_PATH.match(target))):
            raise ValueError(f"refusing a permission rule that is not a plain write_file(<path>): {str(rule)[:80]!r}")
        checked.append(rule)
    return checked


def settings_for(allow_write: bool, rules: "Sequence[str]" = ()) -> "dict[str, Any]":
    settings: "dict[str, Any]" = {**FIXED_SETTINGS, "toolPermission": REVIEW if allow_write else STRICT}
    if rules:
        if not allow_write:
            raise ValueError("a read-only task never gets permission rules")
        settings["permissions"] = {"allow": checked_rules(rules)}
    return settings


def expected_mode(allow_write: bool) -> str:
    return REVIEW if allow_write else STRICT


def _write_private_file(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def write_home(home: Path, *, allow_write: bool, keychains_from: "Path | None" = None, rules: "Sequence[str]" = ()) -> "list[str]":
    """Fill a private HOME with what ``agy`` should find there. Returns notes about anything that could not be set up.

    * ``.gemini/antigravity-cli/settings.json`` - the only configuration the Worker's CLI reads (mode 0600): the fixed settings, the
      permission preset and, for a file-changing task, its ``write_file`` rules;
    * on macOS ``Library/Keychains`` -> the user's keychain directory, because the keyring lookup is HOME-relative: without the link the
      CLI cannot find the Google sign-in the user made once. The link is not a copy and the Runtime never opens what it points to.
    Nothing is ever written outside ``home``."""
    refuse_real_home(home)
    notes: "list[str]" = []
    config = home / ".gemini" / "antigravity-cli"
    config.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(home / ".gemini", 0o700)
    os.chmod(config, 0o700)
    _write_private_file(config / "settings.json", json.dumps(settings_for(allow_write, rules), indent=2, sort_keys=True) + "\n")
    if sys.platform == "darwin":
        source = keychains_from if keychains_from is not None else real_home() / "Library" / "Keychains"
        if source.is_dir():
            library = home / "Library"
            library.mkdir(mode=0o700, exist_ok=True)
            link = library / "Keychains"
            if not os.path.lexists(link):
                os.symlink(source, link)
        else:
            notes.append(f"no keychain directory at {source}: the CLI will not find a saved sign-in")
    return notes


def remove_keychain_link(home: Path) -> None:
    """Take the keychain link out of a private HOME - the LINK only, never what it points to (a real directory named Keychains is left alone).
    The link is needed while agy runs; afterwards nothing in an evidence folder should point at the user's keychains: a careless tool that
    follows links (grep -R, find -L, a file browser, a future console) would wander out of the Runtime's data directory."""
    if os.path.realpath(home) == os.path.realpath(real_home()):
        return                                      # never "clean up" the user's real home
    link = Path(home) / "Library" / "Keychains"
    try:
        if link.is_symlink():
            os.unlink(link)
        os.rmdir(link.parent)                       # the empty Library folder we made for it (fails harmlessly when it holds anything)
    except OSError:
        pass


def _private_dir(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    os.chmod(path, 0o700)


def login_plan(executable: str, data_dir: Path, environ: Mapping[str, str], *, create: bool = True,
               keychains_from: "Path | None" = None) -> "dict[str, Any]":
    """What ``par provider login antigravity`` starts: the CLI itself, interactively, in a Runtime-owned HOME laid out like a Worker's
    (the Runtime's settings.json and the keychain link) and in an empty directory. The sign-in is the vendor's own browser flow, so the
    Runtime never sees a credential - and the CLI never sees the user's real ``~/.gemini`` (no first-launch import of legacy profiles).
    With ``create=False`` nothing is written."""
    home = Path(data_dir) / LOGIN_HOME
    cwd = Path(data_dir).joinpath(*LOGIN_CWD)
    refuse_real_home(home)                                    # before anything is created or chmod-ed
    notes: "list[str]" = []
    if create:
        for d in (cwd.parent, cwd, home):
            _private_dir(d)
        notes = write_home(home, allow_write=False, keychains_from=keychains_from)
    env = {"PATH": PROBE_PATH, "HOME": str(home), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}
    env.update({k: environ[k] for k in ("TERM", "COLORTERM") if k in environ})                  # the interactive screen needs them
    env.update({k: environ[k] for k in PASSTHROUGH if k in environ})
    return {"argv": [executable], "env": env, "cwd": str(cwd), "home": str(home), "notes": notes}


def project_problem(workspace: Path) -> "str | None":
    """A reason to refuse the working copy: it carries configuration that would make ``agy`` start things on its own."""
    for name in _AGENTS_DIRS:
        base = workspace / name
        if not os.path.lexists(base):
            continue
        if base.is_symlink() or not base.is_dir():
            return f"{name} in the project is not a plain directory"
        try:
            entries = sorted(p.name for p in base.iterdir())
        except OSError as exc:
            return f"cannot read {name} in the project: {exc.strerror or exc}"
        unknown = [e for e in entries if e not in _TEXT_ONLY[name]]
        if unknown:
            return (f"the project carries {name}/{unknown[0]}: hooks, MCP servers and plugins would run commands the Runtime did not start "
                    f"(only {', '.join(sorted(_TEXT_ONLY[name]))} are accepted); remove it from the Worker's copy or use another Worker")
    return None


# --- running the CLI offline (probes) --------------------------------------------------------------------------------------------
def _env(home: str, environ: Mapping[str, str]) -> "dict[str, str]":
    env = {"PATH": PROBE_PATH, "HOME": home, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "NO_COLOR": "1",
           "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}
    env.update({k: environ[k] for k in PASSTHROUGH if k in environ})
    return env


# --- classification --------------------------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").lower()


_AUTH = ("authentication required", "not authenticated", "unauthenticated", "not logged in", "please log in", "please sign in",
         "sign in again", "login required", "failed to retrieve token", "keyring is locked", "secret keyring", "token expired",
         "invalid_grant", "refresh token", "credentials", "user interaction is not allowed", "errsecinteractionnotallowed")
_QUOTA = ("quota", "out of credits", "usage limit", "limit reached", "five hour limit", "5 hour limit", "weekly limit", "no remaining requests")
_RATE = ("rate limit", "rate-limit", "ratelimit", "too many requests", "resource_exhausted", "resource has been exhausted", "status: 429", "http 429", "code 429")
# REAL (write smoke #3, 2026-10-15): after a successful edit the vendor ended the run with status ERROR and
#   API error (attempt 1): request failed: Post "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse": EOF
# - a dropped connection, not a failure of the work. It is transient (the run is INTERRUPTED, the work done so far is kept for a relay).
_TRANSIENT = ("temporarily unavailable", "service unavailable", "internal error", "deadline exceeded", "connection reset",
              "connection refused", "connection closed", "timed out", "timeout", "unexpected eof", "stream error", "overloaded", "status: 500",
              "status: 502", "status: 503", "status: 504", "request failed", "broken pipe", "tls handshake", "no such host",
              "network is unreachable", "unexpected end of")
_EOF_WORD = re.compile(r"\beof\b")
_LOCKED = ("keyring is locked", "keychain is locked", "user interaction is not allowed", "errsecinteractionnotallowed")
_MODEL_WORDS = ("not recognized", "invalid model", "unknown model", "model not found", "not available", "do not have access",
                "no access to", "not supported")


def classify_text(*messages: str) -> "FailureClass":
    """Map the vendor's own failure text to a class. Order matters: a login problem beats a model problem beats an exhausted quota beats a
    rate limit (a bare '429' is only a rate limit when no quota wording is there). The wording of quota/rate-limit messages is not
    documented by the vendor: until a real one has been seen, anything unrecognised stays EXECUTION_FAILED - never a guess."""
    text = _norm(" | ".join(m for m in messages if m))
    if any(w in text for w in _AUTH) or re.search(r"\b401\b", text):
        return FailureClass.AUTH_REQUIRED
    if "model" in text and any(w in text for w in _MODEL_WORDS):
        return FailureClass.MODEL_UNAVAILABLE
    if any(w in text for w in _QUOTA):
        return FailureClass.QUOTA_EXHAUSTED
    if any(w in text for w in _RATE) or re.search(r"\b429\b", text):
        return FailureClass.RATE_LIMITED
    if any(w in text for w in _TRANSIENT) or _EOF_WORD.search(text):
        return FailureClass.TRANSIENT_ERROR
    return FailureClass.EXECUTION_FAILED


_USAGE_MAP = {"input_tokens": "input_tokens", "output_tokens": "output_tokens",
              "cache_read_tokens": "cached_input_tokens", "thinking_tokens": "reasoning_output_tokens"}


def _usage(raw: Any) -> "dict[str, int]":
    if not isinstance(raw, dict):
        return {}
    return {ours: raw[theirs] for theirs, ours in _USAGE_MAP.items()
            if isinstance(raw.get(theirs), int) and not isinstance(raw.get(theirs), bool) and raw[theirs] >= 0}


def soft_denial_message(action: str) -> str:
    """One wording for one fact, so the same denial reported by stderr, by the tool step and by the result line collapses to one note."""
    return f'a tool was soft-denied: the "{action}" permission (headless mode cannot ask for approval)'


_NOTICE_ACTION = re.compile(r'required the "([^"]{1,40})" permission')            # real 1.3.5: ... a tool required the "command" permission that headless mode cannot prompt for ...
_DENIAL = re.compile(r"soft[- ]?den|\bdenied\b|not allowed|(?:requires?|needs?|awaiting) (?:approval|permission|confirmation)|permission", re.I)
_IDENT = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_WEB_TOOL = re.compile(r"browser|web|url|http|mcp", re.I)
_PERMISSION_ERROR = ("permission", "denied", "not allowed", "approval", "forbidden")
_FILE_KEYS = re.compile(r"file|path|dir", re.I)


def parse_models(text: str) -> "tuple[str, ...]":
    """``agy models`` prints one ``<slug><TAB><display name>`` line per model (a progress line goes to stderr)."""
    slugs = []
    for line in text.splitlines():
        token = line.split(None, 1)[0] if line.strip() else ""
        if _MODEL.match(token) and "-" in token and token not in slugs:
            slugs.append(token)
    return tuple(slugs)


def wider_than(observed: "str | None", wanted: str) -> bool:
    """True if the permission mode the CLI reports lets it do more than the one asked for. An unknown or missing mode counts as wider."""
    rank = PERMISSION_RANK.get(observed or "")
    return rank is None or rank > PERMISSION_RANK[wanted]


# --- the adapter -----------------------------------------------------------------------------------------------------------------
class AntigravityAdapter:
    provider_id = "antigravity"
    kind = "antigravity"
    workspace_with_git = True            # not known to matter for agy; the plain default until a real run says otherwise
    CAPABILITIES = Capabilities(
        can_headless=Tri.YES, structured_output=Tri.YES, can_select_model=Tri.YES, can_write=Tri.YES,
        can_restrict_tools=Tri.YES,           # permission presets and rules; the Runtime only uses the presets
        has_sandbox=Tri.YES,                  # for commands; the file tools are not inside it
        session_persistence_control=Tri.UNKNOWN, supports_subscription_auth=Tri.YES)

    def __init__(self, executable: str, *, provider_id: str = "antigravity", environ: "Mapping[str, str] | None" = None,
                 models: "tuple[str, ...]" = (), verify_signature: bool = True, keychains_from: "Path | None" = None,
                 write_strategy: "str | None" = None) -> None:
        self.executable = executable
        self.verify_signature = verify_signature            # only tests turn this off (a fake program has no signature)
        self.provider_id = provider_id
        self.environ = dict(os.environ if environ is None else environ)
        wanted = write_strategy if write_strategy is not None else self.environ.get("PAR_AGY_WRITE_STRATEGY", "rules")
        self.write_strategy = wanted if wanted in WRITE_STRATEGIES else "rules"        # anything else is ignored, never guessed at
        self.models = tuple(models)
        self.keychains_from = keychains_from                # tests point this at a scratch directory
        self._wanted = STRICT                               # set by preflight; one adapter instance serves one attempt
        self._tools: "frozenset[str]" = frozenset()
        self.catalog: "tuple[str, ...]" = ()                # the account's model slugs, as of the last sign-in check

    @classmethod
    def from_entry(cls, entry: Any, *, environ: "Mapping[str, str] | None" = None, models: "tuple[str, ...]" = ()) -> "AntigravityAdapter":
        return cls(entry.executable or "", provider_id=entry.id, environ=environ, models=models)

    # --- trust in the binary -------------------------------------------------------------------------------------------------------
    def executable_problem(self) -> "str | None":
        p = Path(self.executable)
        if not self.executable or not p.is_absolute():
            return "the agy executable must be configured as an absolute path (the one on PATH is never used)"
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
        """On macOS the executable must be a Mach-O binary whose code signature verifies and names Google's team."""
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
            return f"{p} is a script, which carries no signature: point `executable` at the signed agy binary"
        if head not in _MACHO_MAGIC:
            return f"{p} is not a Mach-O executable"
        try:
            verify = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(p)], capture_output=True, text=True, timeout=120)
            info = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=2", str(p)], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"could not check the code signature: {exc}"
        team = re.search(r"^TeamIdentifier=(.+)$", info.stderr, re.M)
        if verify.returncode != 0:
            return "the code signature does not verify: " + redact_text(verify.stderr.strip())[:120]
        if not team or team.group(1).strip() != GOOGLE_TEAM_ID:
            return f"the executable is not signed by Google (team: {team.group(1).strip() if team else 'none'})"
        return None

    # --- running subcommands that do not call a model --------------------------------------------------------------------------
    def run_cli(self, args: "list[str]", *, timeout: float = PROBE_TIMEOUT) -> "tuple[int, str, str]":
        """A clean environment, a throw-away HOME prepared like a Worker's, no terminal on standard input (so a missing sign-in is an
        error instead of a browser window), and the self-updater switched off."""
        with tempfile.TemporaryDirectory(prefix="par-agy-probe-") as base:
            home, cwd = Path(base) / "home", Path(base) / "cwd"
            home.mkdir(mode=0o700)
            cwd.mkdir(mode=0o700)
            write_home(home, allow_write=False, keychains_from=self.keychains_from)
            try:
                proc = subprocess.run([self.executable, *args], env=_env(str(home), self.environ), capture_output=True, text=True,
                                      timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL, check=False)
            except subprocess.TimeoutExpired:
                return 124, "", f"timed out after {timeout:g}s"
            except OSError as exc:
                return 127, "", str(exc.strerror or exc)
        return proc.returncode, proc.stdout, proc.stderr

    def login_state(self, *, ttl: float = LOGIN_TTL) -> "tuple[str, str]":
        """('ok' | 'none' | 'locked' | 'unknown', what the CLI said). Asked with the CLI's own ``models`` command: it needs the account but calls
        no model and spends no quota, takes seconds, and - measured on 1.3.5 - when nobody is signed in it fails at once ("Please sign in to
        view available models", exit 1). ``-p /usage`` must NOT be used for this: signed out, it prints a Google login URL and WAITS 60 s for
        a pasted code. A successful answer is also the account's model catalog (``self.catalog``)."""
        key = self.executable
        with _CACHE_LOCK:
            hit = _LOGINS.get(key)
            if hit and ttl > 0 and time.monotonic() - hit[0] < ttl:
                self.catalog = hit[3]
                return hit[1], hit[2]
        rc, out, err = self.run_cli(["models"], timeout=ACCOUNT_TIMEOUT)         # it asks the account over the network
        said = clip((err.strip() or out.strip()).splitlines()[-1] if (err.strip() or out.strip()) else "", 160)
        catalog = parse_models(out) if rc == 0 else ()
        if rc == 0 and catalog:
            state = "ok"
        elif any(w in _norm(f"{err} {out}") for w in _LOCKED):
            state = "locked"
        elif classify_text(err, out) is FailureClass.AUTH_REQUIRED:
            state = "none"
        else:
            state = "unknown"
        self.catalog = catalog
        with _CACHE_LOCK:
            _LOGINS[key] = (time.monotonic(), state, said, catalog)
        return state, said

    def usage_report(self) -> "list[dict[str, str]]":
        """The account's quota windows from ``-p /usage`` (tab-separated: group, window, remaining, reset time). Only asked once ``models``
        has shown that somebody is signed in (signed out, this command waits for a browser login). Empty when it cannot be read."""
        rc, out, _ = self.run_cli(["-p", "/usage"], timeout=ACCOUNT_TIMEOUT)
        rows = []
        if rc == 0:
            for line in out.splitlines():
                parts = [clip(x.strip(), 80) for x in line.split("\t")]
                if len(parts) == 4:
                    rows.append({"group": parts[0], "window": parts[1], "remaining": parts[2], "resets_at": parts[3]})
        return rows

    # --- the adapter protocol ------------------------------------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return self.CAPABILITIES

    def preflight(self, request: RunRequest) -> "Optional[str]":
        problem = self.executable_problem()
        if problem:
            return problem
        if not _MODEL.match(request.model or ""):
            return f"{request.model!r} is not a usable model slug"
        if len(request.prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            return f"the prompt is larger than {MAX_PROMPT_BYTES} bytes"
        problem = project_problem(Path(request.workspace))
        if problem:
            return problem
        if request.allow_write:
            problem = rules_problem(request.allowed_paths)
            if problem:
                return problem
        problem = self.signature_problem()
        if problem:
            return problem
        state, said = self.login_state()
        if state == "none":
            return "agy is not signed in: sign in once with `par provider login antigravity` (the Runtime never handles credentials)"
        if state == "locked":
            return "the macOS keychain is locked or cannot be read, so agy cannot reach its sign-in: unlock the keychain (log in to the Mac, or open Keychain Access) and try again"
        if state != "ok":
            return f"could not confirm that agy is signed in (`agy models` said: {said or 'nothing'})"
        self._wanted = expected_mode(request.allow_write)
        return None

    def prompt_note(self, *, allow_write: bool) -> str:
        """What the headless environment will not let the Worker do, told up front. Measured on 1.3.5 (real write smoke): a model asked to make
        the project's tests pass tried to RUN them, was denied, took that for "blocked" (the Kernel's prompt says to stop when blocked) and ended
        the run with no output at all - vendor status SUCCESS, exit 0, nothing changed."""
        if allow_write:
            return ("Shell commands are not available in this environment: a command you try to run (including the project's test command) is denied "
                    "automatically, nobody can approve it, and a denied action can end your run with no output at all. Read files with your file tools and "
                    "make your changes directly with your file-editing tools; do not try to run tests, scripts or any other command. The checks listed above "
                    "are run for you after you finish. This is not a block: go ahead and make the change, and finish with a written summary.")
        return ("This is a read-only environment: you can read and search files. Shell commands, file edits, web and browser access and every other action "
                "are denied automatically, nobody can approve them, and a denied action can end your run with no output at all. Answer from what you can "
                "read, and always finish with a written answer.")

    def prepare(self, request: RunRequest, home: Path, tmp: Path) -> None:
        self._wanted = expected_mode(request.allow_write)
        rules = write_rules(request.allowed_paths, request.workspace) if request.allow_write and self.write_strategy == "rules" else []
        write_home(Path(home), allow_write=request.allow_write, keychains_from=self.keychains_from, rules=rules)

    def cleanup(self, request: RunRequest, home: Path, tmp: Path) -> None:
        remove_keychain_link(Path(home))

    def build_argv(self, request: RunRequest) -> "list[str]":
        if not _MODEL.match(request.model or ""):
            raise ValueError(f"{request.model!r} is not a usable model slug")
        argv = [self.executable, "--input-format", "stream-json", "--output-format", "stream-json", "--model", request.model,
                "--sandbox", "--print-timeout", PRINT_TIMEOUT]
        if request.allow_write and self.write_strategy == "accept-edits":
            argv += ["--mode", "accept-edits"]
        return argv

    def environment(self, request: RunRequest) -> "dict[str, str]":
        env = {"AGY_CLI_DISABLE_AUTO_UPDATE": "true"}
        env.update({k: self.environ[k] for k in PASSTHROUGH if k in self.environ})
        return env

    def stdin_data(self, request: RunRequest) -> "Optional[bytes]":
        """One prompt as a stream-json ``user`` event; the Kernel closes the pipe afterwards, which ends the session cleanly."""
        return (json.dumps({"event": "user", "message": {"content": request.prompt}}, ensure_ascii=False) + "\n").encode("utf-8")

    # --- events --------------------------------------------------------------------------------------------------------------------
    def parse_event(self, line: str) -> "Optional[ProviderEvent]":
        text = line.strip()
        if not text:
            return None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return ProviderEvent("unparsed", {"length": len(text)})            # the raw text is not kept
        if not isinstance(obj, dict) or not isinstance(obj.get("event"), str):
            return ProviderEvent("unparsed", {"length": len(text)})
        kind = obj["event"]
        if kind == "init":
            return self._init(obj)
        if kind == "step_update":
            return self._step(obj.get("step_update"))
        if kind == "result":
            return self._result(obj.get("result"))
        return ProviderEvent("other", {"type": clip(kind, 60)})

    def _init(self, obj: "dict[str, Any]") -> ProviderEvent:
        init = obj.get("init") if isinstance(obj.get("init"), dict) else {}
        tools = [clip(t, 60) for t in (init.get("tools") if isinstance(init.get("tools"), list) else [])[:300] if isinstance(t, str)]
        self._tools = frozenset(t for t in tools if t and t != "...")
        mode = clip(init.get("permission_mode"), 40) or None
        data: "dict[str, Any]" = {"conversation_id": clip(obj.get("conversation_id"), 80), "permission_mode": mode, "tools": len(self._tools)}
        if isinstance(init.get("model"), str) and init["model"]:
            data["model"] = clip(init["model"], 100)
        if wider_than(mode, self._wanted):
            data["message"] = (f"agy reports the permission mode {mode or 'none'!r}, wider than the {self._wanted!r} the Runtime asked for: "
                               "the run cannot be trusted")
            return ProviderEvent("warning", data)
        return ProviderEvent("session", data)

    def _step(self, step: Any) -> "Optional[ProviderEvent]":
        if not isinstance(step, dict):
            return ProviderEvent("unparsed", {"length": 0})
        stype = step.get("step_type") if isinstance(step.get("step_type"), str) else ""
        state = step.get("state") if isinstance(step.get("state"), str) else None
        if isinstance(step.get("subagent_info"), dict):
            return ProviderEvent("warning", {"message": "a sub-agent was started (the main agent chooses what tools and permissions it gets)"})
        if stype == "agent_response":
            if state != "DONE":
                return None                                                   # partial text fragments: the result event carries the answer
            delta = step.get("text_delta")
            return ProviderEvent("message", {"text": clip(delta), "phase": "done"}) if isinstance(delta, str) and delta.strip() else None
        if stype == "tool" or isinstance(step.get("tool_info"), dict) or isinstance(step.get("tool_name"), str):
            return self._tool(step, state)
        return ProviderEvent("other", {"type": clip(stype, 60) or "step", "phase": state})

    def _tool(self, step: "dict[str, Any]", state: "str | None") -> ProviderEvent:
        info = step.get("tool_info") if isinstance(step.get("tool_info"), dict) else {}
        name = clip(step.get("tool_name") if isinstance(step.get("tool_name"), str) else info.get("name"), 60) or "tool"
        error = info.get("error") if isinstance(info.get("error"), dict) else None
        params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
        if error is not None:
            said = f"{clip(error.get('type'), 60)} {clip(error.get('message'), 300)}".strip()
            if any(w in said.lower() for w in _PERMISSION_ERROR):
                what = params.get("CommandLine") if isinstance(params.get("CommandLine"), str) else None
                return ProviderEvent("warning", {"message": f"a tool call was refused: {name}" + (f" ({' '.join(clip(what, 100).split())})" if what else "")})
        data: "dict[str, Any]" = {"tool": name, "phase": state, "status": "error" if error is not None else None}
        if error is not None:
            data["error"] = clip(error.get("type"), 60) or clip(error.get("message"), 120)
        command = params.get("CommandLine")
        if isinstance(command, str):
            data["command"] = clip(command, 200)
        paths = [clip(v, 200) for k, v in params.items() if isinstance(k, str) and _FILE_KEYS.search(k) and isinstance(v, str)][:5]
        if paths:
            data["paths"] = paths
        if _WEB_TOOL.search(name):
            data["note"] = "the Worker used web, browser or MCP access"
        return ProviderEvent("tool", data)

    def _result(self, res: Any) -> ProviderEvent:
        if not isinstance(res, dict):
            return ProviderEvent("unparsed", {"length": 0})
        vendor = clip(res.get("status"), 20).upper()
        status = {"SUCCESS": "SUCCESS", "CANCELED": "CANCELED", "INTERRUPTED": "CANCELED"}.get(vendor, "ERROR")
        data: "dict[str, Any]" = {"status": status, "vendor_status": vendor, "text": clip(res.get("response")),
                                  "usage": _usage(res.get("usage")), "conversation_id": clip(res.get("conversation_id"), 80)}
        message = clip(res.get("error"))
        if not message and vendor == "WAITING":
            message = "the run ended while waiting for input that nobody can give in headless mode"
        elif not message and vendor in ("INVALID", "RUNNING"):
            message = f"the run ended as {vendor}"
        if message:
            data["message"] = message
        denied = [d for d in (res.get("denied_actions") if isinstance(res.get("denied_actions"), list) else [])[:20] if isinstance(d, dict)]
        if denied:                                                                    # real 1.3.5: the result itself lists what headless mode auto-denied
            data["denied_actions"] = [{"action": clip(d.get("action"), 40), "display_name": clip(d.get("display_name"), 60)} for d in denied]
            data["warnings"] = [soft_denial_message(clip(d.get("action"), 40) or clip(d.get("display_name"), 60) or "unknown") for d in denied]
        for key in ("num_turns", "duration_seconds"):
            if isinstance(res.get(key), (int, float)) and not isinstance(res.get(key), bool):
                data[key] = res[key]
        return ProviderEvent("terminal", data)

    def parse_stderr(self, line: str) -> "Optional[ProviderEvent]":
        """A soft denial is announced only on stderr (documented: a notice naming the tool and how to allow it). Only the tool's name is
        kept - from the list the CLI itself reported in ``init`` - never the line."""
        text = line.strip()[:2000]
        if not text or not _DENIAL.search(text):
            return None
        real = _NOTICE_ACTION.search(text)
        if real:
            return ProviderEvent("warning", {"message": soft_denial_message(clip(real.group(1), 40))})
        tool = next((t for t in sorted(self._tools, key=len, reverse=True) if t in text), None)
        if tool is None and not self._tools:                   # before `init` the tool list is not known yet: take the first identifier
            m = _IDENT.search(text)
            tool = m.group(0) if m else None
        if tool:
            return ProviderEvent("warning", {"message": f"a tool was soft-denied: {clip(tool, 60)} (it needed an approval that headless mode cannot give)"})
        return ProviderEvent("warning", {"message": "agy printed a permission notice on stderr (a tool may have been soft-denied)"})

    # --- classification ------------------------------------------------------------------------------------------------------------
    def classify_failure(self, outcome: RunOutcome) -> "Optional[FailureClass]":
        """None = a clean provider-level success (still not acceptance). Exit code 0 alone is never success: a clean success needs
        the CLI's own ``result`` event with status SUCCESS, and a permission mode no wider than the one asked for. A soft denial is a
        warning on the result, not a failure: nobody can grant the approval, so a retry could not change it, and the Kernel verifies
        the outcome itself."""
        if outcome.timed_out:
            return FailureClass.UNKNOWN
        events = outcome.events
        terminal = next((e for e in reversed(events) if e.kind == "terminal"), None)
        stderr = outcome.stderr_tail or ""
        failed_exit = (outcome.exit_code not in (0, None)) or outcome.signal is not None
        mode_event = next((e for e in events if "permission_mode" in e.data), None)
        if mode_event is not None and wider_than(mode_event.data.get("permission_mode"), self._wanted):
            return FailureClass.EXECUTION_FAILED
        if terminal is not None and terminal.data.get("status") == "SUCCESS" and mode_event is None:
            return FailureClass.EXECUTION_FAILED               # no `init` event: nothing proves which permission mode the run had
        if terminal is not None:
            vendor = terminal.data.get("vendor_status")
            if terminal.data.get("status") == "ERROR":
                if vendor == "WAITING":
                    return FailureClass.PERMISSION_BLOCKED
                return classify_text(str(terminal.data.get("message", "")), stderr)
            if terminal.data.get("status") == "CANCELED":
                return FailureClass.UNKNOWN
            return classify_text(stderr) if failed_exit else None
        if not failed_exit or (outcome.signal is not None and outcome.exit_code in (None, 0)):
            return FailureClass.UNKNOWN                      # the stream simply ended (exit 0), or the process was killed: unknown, not failed
        return classify_text(stderr)

    def request_cancel(self, process: "subprocess.Popen[Any]") -> None:
        """SIGINT is what the documentation names for an interrupted run (status INTERRUPTED); the Kernel escalates if it is ignored."""
        if process.poll() is None:
            process.send_signal(signal.SIGINT)

    def persistence_report(self) -> "dict[str, Any]":
        return {
            "sessions": "kept under the attempt's private HOME (.gemini/antigravity-cli and .gemini/antigravity), which the Kernel owns; "
                        "the user's own ~/.gemini is never opened",
            "logs": "the CLI's own diagnostics (cli.log) land in the same private HOME",
            "memory": "not used: every run starts a new conversation (no --continue / --conversation), headless runs are stateless by default",
            "paths": ["<attempt>/home/.gemini"],
            "credentials": "the sign-in lives in the macOS keychain, owned by the CLI; the private HOME only holds a symlink to the keychain "
                           "directory; the Runtime never opens, reads or copies a credential",
            "telemetry": "requested off through settings.json (enableTelemetry=false); NOT verified: the CLI drops that key when it rewrites the file "
                         "(its default may already be false, or 1.3.5 may not know the key) and its log still mentions an analytics startup event",
        }

    def probe(self) -> ProbeResult:
        """No model call: the executable, its signature, its version, the sign-in, the model list."""
        exe = self.executable
        problem = self.executable_problem()
        if problem:
            return ProbeResult(self.provider_id, ProviderStatus.UNAVAILABLE, problem, executable=exe or None)
        facts: "dict[str, Any]" = {}
        problem = self.signature_problem()
        facts["signature"] = "valid, Google" if problem is None else problem
        if problem:
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, problem, executable=exe, facts=facts)
        rc, out, err = self.run_cli(["--version"])
        m = re.search(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", out) if rc == 0 else None
        version = m.group(0) if m else None
        if version is None:
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, f"`--version` failed ({rc}): {redact_text(err.strip())[:120]}", executable=exe, facts=facts)
        facts["tested_version"] = version == TESTED_VERSION
        notes = [] if version == TESTED_VERSION else [f"untested version (tested: {TESTED_VERSION})"]
        state, said = self.login_state(ttl=0.0)
        facts["login"] = {"ok": "signed in", "none": "not signed in", "locked": "keychain locked"}.get(state, "unknown")
        if state == "none":
            return ProbeResult(self.provider_id, ProviderStatus.AUTH_REQUIRED, "not signed in: sign in once with `par provider login antigravity` (the Runtime never handles credentials)", version=version, executable=exe, facts=facts)
        if state == "locked":
            return ProbeResult(self.provider_id, ProviderStatus.AUTH_REQUIRED, "the macOS keychain is locked or cannot be read, so agy cannot reach its sign-in: unlock the keychain and try again", version=version, executable=exe, facts=facts)
        if state != "ok":
            return ProbeResult(self.provider_id, ProviderStatus.UNHEALTHY, f"could not confirm that agy is signed in (`agy models` said: {said or 'nothing'})", version=version, executable=exe, facts=facts)
        catalog = list(self.catalog)
        facts["models_in_catalog"] = catalog
        usage = self.usage_report()
        facts["usage"] = usage
        if not usage:
            notes.append("the quota report (`-p /usage`) could not be read")
        missing = [mdl for mdl in self.models if catalog and mdl not in catalog]
        facts["configured_models_missing"] = missing
        if missing:
            notes.append(f"configured model(s) not offered to this account: {', '.join(missing)}")
        detail = "ready: signed by Google, signed in, private settings in force" + (f" ({'; '.join(notes)})" if notes else "")
        return ProbeResult(self.provider_id, ProviderStatus.AVAILABLE, detail, version=version, executable=exe, facts=facts)
