"""Task contracts: strict parsing, the rules a JSON schema cannot express, and a canonical digest.

A contract is what a person (or the coordinator, on their behalf) asks the Kernel to do. Three things are
decided here, before anything is stored or run:

* what may be touched - ``allowed_paths`` are workspace-relative prefixes ("app/") or exact files; there are
  no globs, and Harness-owned or sensitive paths can never be listed;
* what counts as proof - ``acceptance.checks`` are commands the *Kernel* runs (argv lists, no shell, an
  allowlisted executable with a few flags it understands), never statements the Worker makes about itself;
* what is refused outright - R3 work, secrets inside the contract, and anything ambiguous (duplicate keys).
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from par import schema
from par.core.policy import is_sensitive_path
from par.core.redact import contains_secret

MAX_CONTRACT_BYTES = 256 * 1024
DEFAULT_TIMEOUTS = {"startup": 60.0, "idle": 300.0, "total": 1200.0}   # plan 6.5: 60 s / 5 min / 20 min
DEFAULT_CHECK_TIMEOUT = 120.0
PROTECTED_NAMES = ("AGENTS.md", "CLAUDE.md")
_GLOB = re.compile(r"[*?\[\]{}]")


class ContractError(Exception):
    def __init__(self, problems: "Iterable[str]") -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- paths -----------------------------------------------------------------------------------------------
def forbidden_reason(path: str) -> "str | None":
    """Why a workspace-relative path can never be changed by a Worker (None = it may be, if allowed)."""
    parts = path.split("/")
    if ".git" in parts:
        return "inside a .git directory"
    if ".ai" in parts:
        return "the .ai/ folder belongs to the Harness Maintainer"
    if parts[-1] in PROTECTED_NAMES:
        return f"{parts[-1]} is a rule file, read-only for Workers"
    if is_sensitive_path(path):
        return "a sensitive-looking path"
    return None


def normalise_allowed_path(raw: str) -> str:
    """A clean workspace-relative path; a trailing slash keeps its meaning (directory prefix). Raises ValueError."""
    if "\x00" in raw or "\\" in raw or "\n" in raw or "\r" in raw:
        raise ValueError("contains a control or backslash character")
    if raw.startswith(("/", "~")):
        raise ValueError("must be relative to the project root")
    if _GLOB.search(raw):
        raise ValueError("globs are not supported: list a directory with a trailing slash, or a file")
    is_dir = raw.endswith("/")
    norm = posixpath.normpath(raw)
    if norm in (".", "") or norm == ".." or norm.startswith("../"):
        raise ValueError("must name something inside the project, not the project root or its parent")
    reason = forbidden_reason(norm)
    if reason:
        raise ValueError(f"can never be allowed: {reason}")
    return norm + "/" if is_dir else norm


def path_allowed(path: str, allowed: Iterable[str]) -> bool:
    for entry in allowed:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


# --- acceptance checks -----------------------------------------------------------------------------------
PY_MODULES = frozenset({"unittest", "pytest", "compileall", "py_compile", "doctest", "json.tool", "mypy", "ruff",
                        "flake8", "pyflakes", "pylint", "black", "isort", "coverage"})
PY_FLAGS = frozenset({"-B", "-I", "-E", "-s", "-S", "-u", "-O", "-OO", "-q"})
NPM_SUBCOMMANDS = frozenset({"test", "t", "run", "run-script"})
GO_SUBCOMMANDS = frozenset({"test", "vet", "build"})
CARGO_SUBCOMMANDS = frozenset({"test", "check", "clippy", "build", "fmt"})
SWIFT_SUBCOMMANDS = frozenset({"test", "build"})
_MAKE_TARGET = re.compile(r"[A-Za-z0-9_.-]+\Z")
_MAKE_FLAG = re.compile(r"-(?:k|s|n|j[0-9]{0,2})\Z")
_SCRIPT = re.compile(r"[A-Za-z0-9_./-]+\Z")
CHECK_EXECUTABLES = frozenset({"python3", "python", "pytest", "ruff", "mypy", "tsc", "node", "npm", "make", "go",
                               "cargo", "swift"})


def _bad_path_arg(arg: str) -> bool:
    for piece in (arg, arg.split("=", 1)[-1]):
        if piece.startswith(("/", "~")) or ".." in piece.split("/"):
            return True
    return False


def check_argv_problem(argv: "list[str]") -> "str | None":
    """Why this command may not be a check, or None. The Kernel runs it without a shell in a scratch clone."""
    exe, args = argv[0], argv[1:]
    if exe not in CHECK_EXECUTABLES:
        return f"{exe!r} is not an allowed check executable (allowed: {', '.join(sorted(CHECK_EXECUTABLES))})"
    if any("\x00" in a for a in argv):
        return "contains a NUL byte"
    for a in args:
        if _bad_path_arg(a):
            return f"argument {a!r} points outside the workspace (absolute, ~ or ..)"
    if exe in ("python", "python3"):
        i = 0
        while i < len(args) and args[i] in PY_FLAGS:
            i += 1
        if i >= len(args):
            return "python needs -m MODULE or a script path"
        if args[i] == "-m":
            if i + 1 >= len(args) or args[i + 1] not in PY_MODULES:
                return f"python -m is limited to: {', '.join(sorted(PY_MODULES))}"
        elif args[i].startswith("-") or not (args[i].endswith(".py") and _SCRIPT.match(args[i])):
            return "python may run -m MODULE or a relative .py script (inline code with -c is not allowed)"
    elif exe == "node":
        if not args:
            return "node needs --test or a relative script"
        if args[0] != "--test" and not (args[0].endswith((".js", ".mjs", ".cjs")) and _SCRIPT.match(args[0])):
            return "node may run --test or a relative .js/.mjs/.cjs script (no -e/-p/--require)"
    elif exe == "npm":
        if not args or args[0] not in NPM_SUBCOMMANDS:
            return f"npm is limited to: {', '.join(sorted(NPM_SUBCOMMANDS))}"
    elif exe == "go":
        if not args or args[0] not in GO_SUBCOMMANDS:
            return f"go is limited to: {', '.join(sorted(GO_SUBCOMMANDS))}"
    elif exe == "cargo":
        if not args or args[0] not in CARGO_SUBCOMMANDS:
            return f"cargo is limited to: {', '.join(sorted(CARGO_SUBCOMMANDS))}"
    elif exe == "swift":
        if not args or args[0] not in SWIFT_SUBCOMMANDS:
            return f"swift is limited to: {', '.join(sorted(SWIFT_SUBCOMMANDS))}"
    elif exe == "make":
        for a in args:
            if not (_MAKE_FLAG.match(a) or _MAKE_TARGET.match(a)):
                return f"make accepts targets and -k/-s/-n/-jN only (found {a!r})"
    return None


# --- the contract ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Contract:
    doc: "dict[str, Any]"       # normalised: every optional field present, paths cleaned
    sha256: str
    warnings: "tuple[str, ...]" = ()

    @property
    def project_id(self) -> str:
        return self.doc["project_id"]

    @property
    def title(self) -> str:
        return self.doc["title"]

    @property
    def description(self) -> str:
        return self.doc["description"]

    @property
    def role(self) -> "str | None":
        return self.doc["role"]

    @property
    def worker(self) -> "str | None":
        return self.doc["worker"]

    @property
    def risk(self) -> str:
        return self.doc["risk"]

    @property
    def writes(self) -> bool:
        return self.doc["risk"] == "R2"

    @property
    def allowed_paths(self) -> "tuple[str, ...]":
        return tuple(self.doc["allowed_paths"])

    @property
    def criteria(self) -> "tuple[str, ...]":
        return tuple(self.doc["acceptance"]["criteria"])

    @property
    def checks(self) -> "tuple[dict[str, Any], ...]":
        return tuple(self.doc["acceptance"]["checks"])

    @property
    def dependencies(self) -> "tuple[str, ...]":
        return tuple(self.doc["dependencies"])

    @property
    def ad_hoc(self) -> bool:
        return self.doc["ad_hoc"]

    @property
    def idempotency_key(self) -> str:
        return self.doc["idempotency_key"]

    def timeouts(self, defaults: "Mapping[str, float] | None" = None) -> "dict[str, float]":
        merged = dict(defaults or DEFAULT_TIMEOUTS)
        merged.update({k: float(v) for k, v in self.doc["timeouts"].items()})
        return merged


def _control_chars(text: str, *, allow_newlines: bool = False) -> bool:
    """Control characters can rewrite a terminal (escape sequences) or hide text: keep them out of everything a person reads."""
    allowed = "\n\t" if allow_newlines else ""
    return any((ord(ch) < 32 and ch not in allowed) or 0x7F <= ord(ch) <= 0x9F for ch in text)


def _strict_loads(text: str) -> Any:
    def pairs(items: "list[tuple[str, Any]]") -> "dict[str, Any]":
        out: "dict[str, Any]" = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate key {key!r}")
            out[key] = value
        return out

    def no_constants(name: str) -> Any:
        raise ValueError(f"{name} is not valid JSON")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=no_constants)


def parse_contract(source: "str | bytes | Mapping[str, Any]") -> Contract:
    """Validate and normalise a contract. Raises ``ContractError`` listing every problem found."""
    if isinstance(source, (str, bytes)):
        raw = source.decode("utf-8", errors="strict") if isinstance(source, bytes) else source
        if len(raw.encode("utf-8")) > MAX_CONTRACT_BYTES:
            raise ContractError([f"the contract is larger than {MAX_CONTRACT_BYTES} bytes"])
        try:
            doc = _strict_loads(raw)
        except (ValueError, RecursionError) as exc:
            raise ContractError([f"not valid JSON: {exc}"]) from None
    else:
        doc = json.loads(canonical_json(dict(source)))   # a deep, JSON-clean copy
    if not isinstance(doc, dict):
        raise ContractError(["a contract must be a JSON object"])
    problems = schema.check(doc, "task")
    if problems:
        raise ContractError(problems)

    risk = doc["risk"]
    warnings: "list[str]" = []
    if risk == "R3":
        problems.append("risk R3 (destructive, credential, deployment, remote-write or paid work) is never run by "
                        "V1; it needs a single explicit human authorisation outside the Runtime")
    if _control_chars(doc["title"]) or _control_chars(doc["description"], allow_newlines=True):
        problems.append("title must be one line; no control characters anywhere (only newlines and tabs in the description)")
    elif any(_control_chars(text) for text in [*doc.get("acceptance", {}).get("criteria", []), *doc.get("allowed_paths", []),
                                               *(a for c in doc.get("acceptance", {}).get("checks", []) for a in [c["name"], *c["argv"]])]):
        problems.append("criteria, paths and check commands must not contain control characters")

    allowed = []
    for entry in doc.get("allowed_paths", []):
        try:
            allowed.append(normalise_allowed_path(entry))
        except ValueError as exc:
            problems.append(f"allowed_paths: {entry!r} {exc}")
    if len(set(allowed)) != len(allowed):
        problems.append("allowed_paths: duplicate entries")
    if risk == "R2" and not allowed:
        problems.append("an R2 (file-changing) task needs allowed_paths")
    if risk in ("R0", "R1") and allowed:
        problems.append(f"a {risk} task cannot change files, so allowed_paths must be empty")

    acceptance = doc.get("acceptance") or {}
    checks_in = acceptance.get("checks", [])
    checks: "list[dict[str, Any]]" = []
    names: "set[str]" = set()
    for i, chk in enumerate(checks_in):
        why = check_argv_problem(chk["argv"])
        if why:
            problems.append(f"acceptance.checks[{i}] ({chk['name']}): {why}")
        if chk["name"] in names:
            problems.append(f"acceptance.checks[{i}]: duplicate name {chk['name']!r}")
        names.add(chk["name"])
        checks.append({"name": chk["name"], "argv": list(chk["argv"]),
                       "timeout": float(chk.get("timeout", DEFAULT_CHECK_TIMEOUT)),
                       "expect_exit": int(chk.get("expect_exit", 0))})
    if risk in ("R0", "R1") and checks:
        problems.append(f"a {risk} task changes nothing, so it cannot have acceptance.checks")
    if risk == "R2" and not checks:
        warnings.append("no mechanical check: acceptance will rest on your own review of the diff")

    timeouts = doc.get("timeouts", {})
    total = float(timeouts.get("total", DEFAULT_TIMEOUTS["total"]))
    for key in ("startup", "idle"):
        if key in timeouts and float(timeouts[key]) > total:
            problems.append(f"timeouts.{key} cannot exceed timeouts.total")

    secret_fields = [doc["title"], doc["description"], *acceptance.get("criteria", []),
                     *(" ".join(c["argv"]) for c in checks_in), *doc.get("allowed_paths", [])]
    if any(contains_secret(text) for text in secret_fields):
        problems.append("the contract contains something that looks like a credential; "
                        "keep secrets out of tasks (the Worker uses its own login)")
    if problems:
        raise ContractError(problems)

    normal = {
        "schema_version": 1, "project_id": doc["project_id"], "title": doc["title"], "description": doc["description"],
        "role": doc.get("role"), "worker": doc.get("worker"), "risk": risk, "allowed_paths": allowed,
        "acceptance": {"criteria": list(acceptance.get("criteria", [])), "checks": checks},
        "dependencies": list(doc.get("dependencies", [])), "ad_hoc": bool(doc.get("ad_hoc", False)),
        "timeouts": {k: float(v) for k, v in sorted(timeouts.items())},
        "idempotency_key": doc["idempotency_key"],
    }
    return Contract(normal, sha256_text(canonical_json(normal)), tuple(warnings))
