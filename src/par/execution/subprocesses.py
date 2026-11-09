"""Running a Worker (or a check) as a child process the Kernel fully controls.

What this module guarantees, and why it exists instead of ``subprocess.run``:

* the child gets its own session and process group, so a stop signal reaches the whole tree and *only* that
  tree - never Claude Desktop, the Codex app or an Antigravity GUI;
* three independent timeouts (no output at startup, silence after output, total time) and cancellation are
  all polled from one loop that also drives the heartbeat, so a stuck child can neither hide nor outlive us;
* stopping is polite first (the adapter's own cancel request), then a hard kill of the group after a grace
  period, and any process the child left behind in its group is killed after it exits;
* output is read on separate threads, redacted before it touches disk and capped, so a chatty child cannot
  fill the disk or block on a full pipe;
* if anything in the loop raises (a database error in a callback, Ctrl-C), the group is killed before the
  exception leaves - no orphaned writer is left running.
"""
from __future__ import annotations

import errno
import os
import select
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from par.core.redact import redact_text
from par.providers.base import ProviderEvent

MAX_LINE = 1 << 20
MAX_EVENTS = 10_000
MAX_EVENT_BYTES = 32 * 1024 * 1024        # of output text that produced retained events (errors/terminal/usage always kept)
KEEP_KINDS = frozenset({"error", "terminal", "usage"})
STDERR_TAIL_CHARS = 4000
PS = next((p for p in ("/bin/ps", "/usr/bin/ps", "/opt/homebrew/bin/ps", "/usr/local/bin/ps")
           if os.path.isfile(p) and os.access(p, os.X_OK)), None)


class SpawnError(Exception):
    """The child could not be started at all."""


@dataclass(frozen=True)
class Limits:
    startup: float = 60.0           # no output at all within this long after start
    idle: float = 300.0             # silence this long after the first output
    total: float = 1200.0
    grace: float = 5.0              # polite stop -> hard kill
    max_log_bytes: int = 20 * 1024 * 1024
    poll: float = 0.05
    tick: float = 2.0               # how often on_tick (the heartbeat) runs
    cancel_check: float = 0.25      # how often should_cancel runs


@dataclass
class RunResult:
    exit_code: "int | None"
    signal: "int | None"
    timed_out: "str | None"         # startup | idle | total
    canceled: bool
    events: "list[ProviderEvent]"
    stderr_tail: str
    stdout_log: Path
    stderr_log: Path
    pid: int
    pgid: int
    duration: float
    logs_truncated: bool = False
    escalated: bool = False         # the polite stop was not enough and the group was killed
    strays_killed: bool = False     # processes left behind in the group were killed
    events_dropped: int = 0
    unparsed: int = 0


# --- process facts ---------------------------------------------------------------------------------------------------
def process_identity(pid: int) -> "str | None":
    """The start time of a process as ``ps`` reports it. With the pid it identifies a process even after pid reuse."""
    if PS is None:
        return None
    try:
        out = subprocess.run([PS, "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
                             env={"LC_ALL": "C", "PATH": "/bin:/usr/bin"}, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def terminate_group(pgid: int, grace: float = 5.0, poll: float = 0.05) -> bool:
    """SIGTERM the group, wait up to ``grace``, then SIGKILL. True when nothing is left."""
    if not _signal_group(pgid, signal.SIGTERM):
        return not group_alive(pgid)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not group_alive(pgid):
            return True
        time.sleep(poll)
    _signal_group(pgid, signal.SIGKILL)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not group_alive(pgid):
            return True
        time.sleep(poll)
    return not group_alive(pgid)


# --- the runner ---------------------------------------------------------------------------------------------------------
class _Pump(threading.Thread):
    """Reads one pipe without ever blocking for good: redacts, caps and logs each line, then hands it to a handler.

    It polls the raw descriptor with a short timeout, so the runner can always make it stop - even when some
    process outside the child's group still holds the other end of the pipe open.
    """

    def __init__(self, stream, log_path: Path, limit: int, handler: "Callable[[str], None]", shared: "_Shared") -> None:
        super().__init__(daemon=True)
        self.stream, self.handler, self.shared, self.limit = stream, handler, shared, limit
        self.fd = stream.fileno()
        os.set_blocking(self.fd, False)
        self.log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        self.written = 0
        self.truncated = False
        self.stop = threading.Event()
        self._buf = b""

    def run(self) -> None:
        try:
            while True:
                ready, _, _ = select.select([self.fd], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(self.fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    self._feed(chunk)
                elif self.stop.is_set():
                    break
            if self._buf:
                self._line(self._buf)
                self._buf = b""
        finally:
            os.close(self.log_fd)

    def _feed(self, chunk: bytes) -> None:
        self._buf += chunk
        while True:
            nl = self._buf.find(b"\n", 0, MAX_LINE + 1)     # a line is never longer than MAX_LINE
            if nl < 0:
                if len(self._buf) >= MAX_LINE:          # no end in sight: cut it here
                    line, self._buf = self._buf[:MAX_LINE], self._buf[MAX_LINE:]
                    self._line(line)
                    continue
                return
            line, self._buf = self._buf[:nl], self._buf[nl + 1:]
            self._line(line)

    def _line(self, raw: bytes) -> None:
        self.shared.touch()
        text = redact_text(raw.decode("utf-8", errors="replace").rstrip("\r"))
        self._log(text)
        try:
            self.handler(text)
        except Exception:      # a parser bug must not stop the pipe being drained
            pass

    def _log(self, text: str) -> None:
        if self.truncated:
            return
        data = (text + "\n").encode("utf-8", errors="replace")
        if self.written + len(data) > self.limit:
            os.write(self.log_fd, f"[log truncated at {self.limit} bytes]\n".encode())
            self.truncated = True
            return
        os.write(self.log_fd, data)
        self.written += len(data)


@dataclass
class _Shared:
    lock: threading.Lock = field(default_factory=threading.Lock)
    first_output: "float | None" = None
    last_output: float = 0.0

    def touch(self) -> None:
        now = time.monotonic()
        with self.lock:
            if self.first_output is None:
                self.first_output = now
            self.last_output = now

    def snapshot(self) -> "tuple[float | None, float]":
        with self.lock:
            return self.first_output, self.last_output


def run_process(
    argv: "list[str]", *, cwd: "str | os.PathLike[str]", env: "dict[str, str]", limits: Limits,
    parse_line: "Callable[[str], Optional[ProviderEvent]]", stdout_log: Path, stderr_log: Path,
    request_cancel: "Callable[[subprocess.Popen], None]",
    on_started: "Optional[Callable[[int, int, Optional[str]], None]]" = None,
    on_tick: "Optional[Callable[[], None]]" = None,
    should_cancel: "Optional[Callable[[], bool]]" = None,
    stdin_data: "Optional[bytes]" = None,
    parse_stderr: "Optional[Callable[[str], Optional[ProviderEvent]]]" = None,
) -> RunResult:
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL if stdin_data is None else subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
    except (OSError, ValueError) as exc:
        raise SpawnError(f"could not start {os.path.basename(argv[0])}: {exc.strerror if isinstance(exc, OSError) and exc.strerror else exc}") from None
    started = time.monotonic()
    pid = proc.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = pid

    shared = _Shared(last_output=started)
    events: "list[ProviderEvent]" = []
    counters = {"dropped": 0, "unparsed": 0, "bytes": 0}
    tail: "deque[str]" = deque()
    tail_chars = [0]

    def keep(ev: ProviderEvent, text: str) -> None:
        if ev.kind not in KEEP_KINDS and (len(events) >= MAX_EVENTS or counters["bytes"] + len(text) > MAX_EVENT_BYTES):
            counters["dropped"] += 1
            return
        counters["bytes"] += len(text)
        events.append(ev)

    def on_stdout(text: str) -> None:
        try:
            ev = parse_line(text)
        except Exception:
            ev = ProviderEvent("unparsed", {"error": "the adapter's parser raised"})
        if ev is None:
            return
        if ev.kind == "unparsed":
            counters["unparsed"] += 1
        keep(ev, text)

    def on_stderr(text: str) -> None:
        tail.append(text)
        tail_chars[0] += len(text) + 1
        while tail_chars[0] > STDERR_TAIL_CHARS and len(tail) > 1:
            tail_chars[0] -= len(tail.popleft()) + 1
        if parse_stderr is not None:                      # an adapter may turn notices on stderr into events (e.g. a soft denial)
            try:
                ev = parse_stderr(text)
            except Exception:
                ev = None
            if ev is not None:
                keep(ev, text)

    pumps = [_Pump(proc.stdout, stdout_log, limits.max_log_bytes, on_stdout, shared),
             _Pump(proc.stderr, stderr_log, limits.max_log_bytes, on_stderr, shared)]
    feeder: "threading.Thread | None" = None
    if stdin_data is not None:
        def feed() -> None:                       # a thread, so a child that does not read yet cannot stall the loop
            try:
                proc.stdin.write(stdin_data)
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass                              # the child exited or closed its input early; its exit tells the story
        feeder = threading.Thread(target=feed, daemon=True)
    stop_reason: "str | None" = None
    escalated = strays = False
    try:
        for pump in pumps:
            pump.start()
        if feeder is not None:
            feeder.start()
        if on_started is not None:
            on_started(pid, pgid, process_identity(pid))
        last_tick = last_cancel_check = started
        deadline: "float | None" = None
        while proc.poll() is None:
            now = time.monotonic()
            if stop_reason is None:
                first, last = shared.snapshot()
                if now - started > limits.total:
                    stop_reason = "total"
                elif first is None and now - started > limits.startup:
                    stop_reason = "startup"
                elif first is not None and now - last > limits.idle:
                    stop_reason = "idle"
                elif should_cancel is not None and now - last_cancel_check >= limits.cancel_check:
                    last_cancel_check = now
                    if should_cancel():
                        stop_reason = "cancel"
                if stop_reason is not None:
                    request_cancel(proc)
                    deadline = time.monotonic() + limits.grace
            elif deadline is not None and now > deadline and not escalated:
                _signal_group(pgid, signal.SIGKILL)
                escalated = True
            if on_tick is not None and now - last_tick >= limits.tick:
                last_tick = now
                on_tick()
            try:
                proc.wait(timeout=limits.poll)
            except subprocess.TimeoutExpired:
                pass
        # the leader is gone: whatever it left in its group is ours to clear
        if group_alive(pgid):
            strays = _signal_group(pgid, signal.SIGKILL)
    except BaseException:
        _signal_group(pgid, signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise
    finally:
        drain_until = time.monotonic() + 2.0           # the pipes end as soon as every writer is gone
        for pump in pumps:
            pump.join(timeout=max(0.0, drain_until - time.monotonic()))
        for pump in pumps:
            pump.stop.set()
            pump.join(timeout=1.0)
        for pump in pumps:
            if not pump.is_alive():                    # never close a pipe a thread might still be reading
                try:
                    pump.stream.close()
                except Exception:
                    pass
        if feeder is not None:
            feeder.join(timeout=1.0)
            if not feeder.is_alive():
                try:
                    proc.stdin.close()
                except Exception:
                    pass

    rc = proc.returncode
    return RunResult(
        exit_code=rc if rc is not None and rc >= 0 else None,
        signal=-rc if rc is not None and rc < 0 else None,
        timed_out=stop_reason if stop_reason in ("startup", "idle", "total") else None,
        canceled=stop_reason == "cancel", events=events, stderr_tail="\n".join(tail),
        stdout_log=stdout_log, stderr_log=stderr_log, pid=pid, pgid=pgid, duration=time.monotonic() - started,
        logs_truncated=any(p.truncated for p in pumps), escalated=escalated, strays_killed=strays,
        events_dropped=counters["dropped"], unparsed=counters["unparsed"],
    )
