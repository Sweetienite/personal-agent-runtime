"""Test-only helper: spawn an adapter's argv, parse its events, return a RunOutcome.

The real equivalent (process groups, timeouts, escalation, recovery) is the Kernel's job in P2; this
helper only exists so the scripted provider can be exercised end to end.
"""
import contextlib
import hashlib
import io
import os
import signal
import subprocess
from pathlib import Path

from par.cli import main
from par.providers.base import RunOutcome


def run_cli(*argv):
    """Run ``par`` in-process; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse exits on usage errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def tree_digest(root):
    """Digest of every path, mode and byte below ``root`` (symlinks are not followed).

    A path listed by ``rglob`` may disappear before it is stat'ed - git's background maintenance
    creates and removes ``.git/objects/maintenance.lock`` while a test is running. Such a transient
    file is not project content, so skipping it keeps the digest a stable "was the project changed?"
    witness instead of making the whole test flaky on a busy CI runner.
    """
    h = hashlib.sha256()
    root = Path(root)
    if not root.exists() and not root.is_symlink():
        return "absent"
    for p in sorted([root, *root.rglob("*")]):
        try:
            st = os.lstat(p)
            h.update(f"{p.relative_to(root)}|{st.st_mode:o}|{st.st_size}".encode())
            if p.is_file() and not p.is_symlink():
                h.update(p.read_bytes())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
    return h.hexdigest()


def minimal_env():
    return {"PATH": "/usr/bin:/bin"}  # no HOME, no tokens: nothing from the caller's environment leaks


def run_adapter(adapter, request, timeout=15.0):
    argv = adapter.build_argv(request)
    proc = subprocess.Popen(
        argv, cwd=request.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=minimal_env(), start_new_session=True,
    )
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        adapter.request_cancel(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
    events = [ev for ev in (adapter.parse_event(line) for line in out.splitlines()) if ev is not None]
    rc = proc.returncode
    return RunOutcome(
        exit_code=rc, events=events, timed_out=timed_out,
        signal=-rc if rc is not None and rc < 0 else None, stderr_tail=err[-500:],
    )
