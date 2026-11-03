#!/usr/bin/env python3
"""Scripted stand-in for a vendor CLI. Standard library only, so it runs under ``python -I``.

    fake_cli.py '<scenario-json>' [--read-only]

Runs in the current directory (the task workspace) and never touches the network, the environment or
anything outside that directory. With --read-only every write is refused with a permission error,
like a real CLI started without write permission. Each step is one of:

    {"op": "emit",   "event": {...}}        one JSON event line on stdout
    {"op": "raw",    "text": "..."}         one raw (possibly non-JSON) line on stdout
    {"op": "stderr", "text": "..."}         one line on stderr
    {"op": "write",  "path": "a/b.txt", "content": "..."}   file inside the workspace only
    {"op": "sleep",  "seconds": 0.5}
    {"op": "exit",   "code": 0}             stop with this exit code (default 0 at the end)
    {"op": "delete", "path": "a/b.txt"}     remove a file inside the workspace
    {"op": "symlink", "path": "a/l", "target": "x"}   create a symlink inside the workspace
    {"op": "chmod",  "path": "a/run.sh", "mode": 493}
    {"op": "ignore_sigterm"}                SIGTERM is ignored from here on (only SIGKILL stops it)
    {"op": "stray", "seconds": 30, "pid_file": "stray.pid", "detach": false}
                                            leave a child behind; detach=true puts it in its own session
    {"op": "flood", "lines": 1000, "width": 80}   many raw output lines
    {"op": "write_env", "path": "env.json"}  record the environment NAMES plus HOME/TMPDIR/PATH/cwd
    {"op": "die",    "signal": 9}           kill this process with a signal

"on_sigterm": "cancel" makes SIGTERM emit a CANCELED terminal event and exit 130.
"""
import json
import os
import signal
import sys
import time


def _emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _target(rel):
    """Resolve ``rel`` inside the workspace or raise ValueError. Symlink escapes are resolved first."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or rel.startswith("~"):
        raise ValueError("path must be relative to the workspace")
    root = os.path.realpath(os.getcwd())
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("path escapes the workspace")
    return target


def main(argv):
    scenario = json.loads(argv[1])
    read_only = "--read-only" in argv[2:]
    if scenario.get("on_sigterm") == "cancel":
        def _on_term(_sig, _frame):
            _emit({"type": "terminal", "status": "CANCELED"})
            os._exit(130)
        signal.signal(signal.SIGTERM, _on_term)
    for step in scenario["steps"]:
        op = step["op"]
        if op == "emit":
            _emit(step["event"])
        elif op == "raw":
            sys.stdout.write(step["text"] + "\n")
            sys.stdout.flush()
        elif op == "stderr":
            sys.stderr.write(step["text"] + "\n")
            sys.stderr.flush()
        elif op == "write":
            if read_only:
                _emit({"type": "error", "code": "permission_denied", "message": "write refused: read-only mode"})
                _emit({"type": "terminal", "status": "ERROR"})
                return 1
            try:
                target = _target(step["path"])
            except ValueError as exc:
                _emit({"type": "error", "code": "permission_denied", "message": str(exc)})
                _emit({"type": "terminal", "status": "ERROR"})
                return 1
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(step["content"])
        elif op in ("delete", "symlink", "chmod"):
            if read_only:
                _emit({"type": "error", "code": "permission_denied", "message": "write refused: read-only mode"})
                _emit({"type": "terminal", "status": "ERROR"})
                return 1
            if op == "symlink":                      # the link itself must sit inside the workspace; its target may be anything
                link = os.path.join(os.path.realpath(os.path.dirname(_target(step["path"]))), os.path.basename(step["path"]))
                os.makedirs(os.path.dirname(link), exist_ok=True)
                os.symlink(step["target"], link)
            elif op == "delete":
                os.unlink(_target(step["path"]))
            else:
                os.chmod(_target(step["path"]), int(step["mode"]))
        elif op == "ignore_sigterm":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        elif op == "stray":
            import subprocess
            code = "import time; time.sleep(%r)" % float(step["seconds"])
            child = subprocess.Popen([sys.executable, "-I", "-c", code], start_new_session=bool(step.get("detach")))
            if step.get("pid_file"):
                with open(_target(step["pid_file"]), "w", encoding="utf-8") as f:
                    f.write(str(child.pid))
        elif op == "flood":
            line = "x" * int(step.get("width", 80))
            for _ in range(int(step["lines"])):
                sys.stdout.write(line + "\n")
            sys.stdout.flush()
        elif op == "write_env":
            doc = {"names": sorted(os.environ), "HOME": os.environ.get("HOME"), "TMPDIR": os.environ.get("TMPDIR"),
                   "PATH": os.environ.get("PATH"), "cwd": os.getcwd()}
            with open(_target(step["path"]), "w", encoding="utf-8") as f:
                json.dump(doc, f)
        elif op == "die":
            os.kill(os.getpid(), int(step["signal"]))
            time.sleep(5)
        elif op == "sleep":
            time.sleep(float(step["seconds"]))
        elif op == "exit":
            return int(step["code"])
        else:
            raise SystemExit(f"unknown op: {op!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
