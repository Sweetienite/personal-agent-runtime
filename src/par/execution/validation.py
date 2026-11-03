"""Running a task's acceptance checks - the only source of "tests passed" the Kernel believes.

A check is an argv list the contract names; it runs without a shell, from a trusted executable path, in the
verification tree, in a clean environment (fixed PATH, private HOME and TMPDIR, no inherited variables), in
its own process group with a hard time limit. A Worker's own claim that its tests pass is never consulted.

Residual risk, stated plainly: a check executes code from the Worker's patch (that is what a test is). In V1
there is no operating-system sandbox around it, so this relies on the verification tree being a disposable
copy, the environment holding no secrets, and the P8 rule that real projects need an explicit write gate.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from par.execution.subprocesses import Limits, SpawnError, run_process

TRUSTED_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")
CHILD_PATH = ":".join(TRUSTED_DIRS)
PYTHONS = frozenset({"python", "python3"})


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str                       # PASS | FAIL | TIMEOUT | ERROR
    exit_code: "int | None"
    duration_s: float
    log: "str | None"                 # path relative to the attempt directory
    expect_exit: int = 0
    detail: str = ""

    def to_test(self) -> "dict[str, object]":
        return {"name": self.name, "status": self.status, "exit_code": self.exit_code,
                "duration_s": round(self.duration_s, 3), "log": self.log}


def resolve_executable(name: str) -> "str | None":
    """Where a check's program really is. Never the user's PATH: python means the interpreter running the Kernel."""
    if name in PYTHONS:
        return sys.executable
    for directory in TRUSTED_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def check_env(home: Path, tmp: Path) -> "dict[str, str]":
    return {"PATH": CHILD_PATH, "HOME": str(home), "TMPDIR": str(tmp), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "CI": "1", "NO_COLOR": "1"}


def _combine(out_log: Path, err_log: Path, target: Path) -> None:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        for label, path in (("stdout", out_log), ("stderr", err_log)):
            f.write(f"--- {label} ---\n".encode())
            try:
                f.write(path.read_bytes())
            except OSError:
                pass
            try:
                path.unlink()
            except OSError:
                pass


def run_checks(checks: "Iterable[dict]", *, cwd: Path, attempt_dir: Path, home: Path, tmp: Path,
               on_tick: "Optional[Callable[[], None]]" = None,
               should_cancel: "Optional[Callable[[], bool]]" = None, grace: float = 2.0,
               on_started: "Optional[Callable[[int, int, Optional[str]], None]]" = None) -> "list[CheckResult]":
    """Run each check in order and report all of them (a failing check does not hide the ones after it)."""
    results: "list[CheckResult]" = []
    for spec in checks:
        name, argv = spec["name"], list(spec["argv"])
        log_path = attempt_dir / f"check_{name}.log"
        rel = log_path.name
        exe = resolve_executable(argv[0])
        if exe is None:
            log_path.write_text(f"--- stdout ---\n--- stderr ---\n{argv[0]!r} was not found in the trusted locations {TRUSTED_DIRS}\n")
            os.chmod(log_path, 0o600)
            results.append(CheckResult(name, "ERROR", None, 0.0, rel, int(spec.get("expect_exit", 0)), "executable not found"))
            continue
        limit = float(spec.get("timeout", 120.0))
        out_log, err_log = attempt_dir / f"check_{name}.out", attempt_dir / f"check_{name}.err"
        started = time.monotonic()
        try:
            run = run_process([exe, *argv[1:]], cwd=cwd, env=check_env(home, tmp),
                              limits=Limits(startup=limit, idle=limit, total=limit, grace=grace),
                              parse_line=lambda line: None, stdout_log=out_log, stderr_log=err_log,
                              request_cancel=lambda proc: proc.terminate(), on_tick=on_tick, should_cancel=should_cancel,
                              on_started=on_started)
        except SpawnError as exc:
            log_path.write_text(f"--- stdout ---\n--- stderr ---\n{exc}\n")
            os.chmod(log_path, 0o600)
            results.append(CheckResult(name, "ERROR", None, time.monotonic() - started, rel, int(spec.get("expect_exit", 0)), str(exc)))
            continue
        _combine(out_log, err_log, log_path)
        expect = int(spec.get("expect_exit", 0))
        if run.canceled:
            status, detail = "ERROR", "canceled while running"
        elif run.timed_out:
            status, detail = "TIMEOUT", f"no result within {limit:g}s ({run.timed_out})"
        elif run.exit_code == expect:
            status, detail = "PASS", ""
        else:
            status = "FAIL"
            detail = f"exit code {run.exit_code}" if run.exit_code is not None else f"killed by signal {run.signal}"
        results.append(CheckResult(name, status, run.exit_code, run.duration, rel, expect, detail))
        if run.canceled:
            break
    return results
