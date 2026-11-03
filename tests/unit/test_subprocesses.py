import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from par.execution import subprocesses as sp
from par.providers.base import ProviderEvent, RunRequest
from par.providers.fake import ScriptedProvider, ops, scenarios

TOOK_TOO_LONG = 12.0


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.ws = self.base / "ws"
        self.ws.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.started = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for pgid in {s[1] for s in self.started}:
            sp._signal_group(pgid, signal.SIGKILL)
        for pid_file in self.ws.glob("*.pid"):
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        self._tmp.cleanup()

    def run_scenario(self, scenario, *, write=True, limits=None, cancel_after=None, on_tick=None, parse_line=None,
                     env=None, **limit_kw):
        provider = ScriptedProvider(scenario)
        req = RunRequest("t-0001", 1, "prompt", self.ws, "m", allow_write=write)
        limits = limits or sp.Limits(**{"startup": 5, "idle": 5, "total": 20, "grace": 1.0, "tick": 0.2, "cancel_check": 0.05, **limit_kw})
        t0 = time.monotonic()
        stamp = {}

        def should_cancel():
            return cancel_after is not None and time.monotonic() - t0 >= cancel_after

        def on_started(pid, pgid, identity):
            self.started.append((pid, pgid, identity))

        result = sp.run_process(
            provider.build_argv(req), cwd=self.ws, env=env or {"PATH": "/usr/bin:/bin", "HOME": str(self.home)},
            limits=limits, parse_line=parse_line or provider.parse_event, stdout_log=self.base / "out.log",
            stderr_log=self.base / "err.log", request_cancel=provider.request_cancel, on_started=on_started,
            on_tick=on_tick, should_cancel=should_cancel)
        self.assertLess(time.monotonic() - t0, TOOK_TOO_LONG)
        return result

    def wait_dead(self, pid, seconds=3.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not sp.pid_alive(pid):
                return True
            time.sleep(0.05)
        return not sp.pid_alive(pid)


class StandardInput(RunnerCase):
    """A prompt travels on standard input: complete, whatever its size, without ever blocking the runner."""

    def run_py(self, code, stdin_data=None):
        limits = sp.Limits(startup=10, idle=10, total=30, grace=1.0, tick=0.2, cancel_check=0.05)
        return sp.run_process([sys.executable, "-B", "-c", code], cwd=self.ws, env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)},
                              limits=limits, parse_line=lambda line: None, stdout_log=self.base / "out.log", stderr_log=self.base / "err.log",
                              request_cancel=lambda proc: proc.terminate(), on_started=lambda *a: self.started.append(a), stdin_data=stdin_data)

    READ_ALL = "import sys, hashlib, pathlib\ndata = sys.stdin.buffer.read()\npathlib.Path('got.txt').write_text(f'{len(data)} {hashlib.sha256(data).hexdigest()}')\n"

    def test_the_child_receives_exactly_the_bytes_that_were_given(self):
        import hashlib
        for data in (b"hello", "修复 add() \u2014 caf\u00e9".encode(), bytes(range(256)) * 3, b"x" * (3 * 1024 * 1024)):
            r = self.run_py(self.READ_ALL, data)
            self.assertEqual(r.exit_code, 0)
            self.assertEqual((self.ws / "got.txt").read_text(), f"{len(data)} {hashlib.sha256(data).hexdigest()}")

    def test_without_input_the_child_sees_an_empty_stream(self):
        r = self.run_py("import sys, pathlib\npathlib.Path('got.txt').write_text(repr(sys.stdin.read()))\n")
        self.assertEqual((r.exit_code, (self.ws / "got.txt").read_text()), (0, "''"))

    def test_a_child_that_never_reads_its_input_neither_hangs_nor_breaks_the_runner(self):
        r = self.run_py("print('not interested')\n", b"x" * (4 * 1024 * 1024))
        self.assertEqual(r.exit_code, 0)

    def test_a_child_that_quits_while_input_is_still_being_sent_is_survived(self):
        r = self.run_py("import sys\nsys.stdin.buffer.read(10)\nsys.exit(7)\n", b"y" * (8 * 1024 * 1024))
        self.assertEqual(r.exit_code, 7)

    def test_the_input_is_not_visible_in_the_process_arguments(self):
        r = self.run_py("import os, sys, pathlib\npathlib.Path('argv.txt').write_text(' '.join(sys.argv))\nsys.stdin.read()\n", b"SECRET-PROMPT-TEXT")
        self.assertEqual(r.exit_code, 0)
        self.assertNotIn("SECRET-PROMPT-TEXT", (self.ws / "argv.txt").read_text())


class StderrNotices(RunnerCase):
    """An adapter may turn notices on standard error (a soft-denied tool, say) into events; the raw line is never kept."""

    def run_py(self, code, parse_stderr):
        limits = sp.Limits(startup=10, idle=10, total=30, grace=1.0, tick=0.2, cancel_check=0.05)
        return sp.run_process([sys.executable, "-B", "-c", code], cwd=self.ws, env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)},
                              limits=limits, parse_line=lambda line: None, stdout_log=self.base / "out.log", stderr_log=self.base / "err.log",
                              request_cancel=lambda proc: proc.terminate(), on_started=lambda *a: self.started.append(a), parse_stderr=parse_stderr)

    SAY = "import sys\nfor line in ['noise', 'NOTICE: tool run_command was denied', 'more noise', 'NOTICE: second']:\n    print(line, file=sys.stderr, flush=True)\n"

    @staticmethod
    def parser(line):
        return ProviderEvent("warning", {"message": line[8:]}) if line.startswith("NOTICE: ") else None

    def test_notices_on_stderr_become_events_and_everything_else_is_ignored(self):
        r = self.run_py(self.SAY, self.parser)
        self.assertEqual([(e.kind, e.data["message"]) for e in r.events], [("warning", "tool run_command was denied"), ("warning", "second")])
        self.assertIn("noise", r.stderr_tail)                                   # the tail for classification is unchanged

    def test_without_a_hook_nothing_changes(self):
        self.assertEqual(self.run_py(self.SAY, None).events, [])

    def test_a_hook_that_raises_is_survived(self):
        calls = []

        def boom(line):
            calls.append(line)
            raise RuntimeError("adapter bug")
        r = self.run_py(self.SAY, boom)
        self.assertEqual((r.exit_code, r.events, len(calls)), (0, [], 4))

    def test_a_flood_of_notices_is_bounded(self):
        code = "import sys\nfor i in range(300):\n    print('NOTICE: n%d' % i, file=sys.stderr, flush=True)\n"
        with mock.patch.object(sp, "MAX_EVENTS", 50):
            r = self.run_py(code, self.parser)
        self.assertEqual(r.exit_code, 0)
        self.assertLessEqual(len(r.events), 50)


class NormalRuns(RunnerCase):
    def test_a_successful_run(self):
        r = self.run_scenario(scenarios.success({"a.txt": "hello\n"}))
        self.assertEqual((r.exit_code, r.signal, r.timed_out, r.canceled), (0, None, None, False))
        self.assertEqual(r.events[-1].kind, "terminal")
        self.assertEqual(r.events[-1].data["status"], "SUCCESS")
        self.assertEqual((self.ws / "a.txt").read_text(), "hello\n")
        self.assertFalse(r.escalated or r.strays_killed or r.logs_truncated)

    def test_the_child_gets_its_own_session_and_the_start_is_reported_before_it_ends(self):
        r = self.run_scenario(scenarios.success())
        (pid, pgid, identity), = self.started
        self.assertEqual((pid, pgid), (r.pid, r.pgid))
        self.assertEqual(pgid, pid)                                      # a new process group led by the child
        self.assertNotEqual(pgid, os.getpgid(0))
        self.assertTrue(identity)                                        # the start time, usable after pid reuse
        self.assertFalse(sp.group_alive(pgid))

    def test_logs_are_private_files(self):
        r = self.run_scenario(scenarios.success())
        for log in (r.stdout_log, r.stderr_log):
            self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)
        self.assertIn('"status":"SUCCESS"', r.stdout_log.read_text().replace(" ", ""))

    def test_non_zero_exit_and_stderr_are_reported(self):
        r = self.run_scenario(scenarios.crash(139))
        self.assertEqual((r.exit_code, r.signal), (139, None))
        self.assertIn("segmentation fault", r.stderr_tail)

    def test_death_by_signal_is_a_signal_not_an_exit_code(self):
        r = self.run_scenario(scenarios.custom("die", ops.message("about to die"), ops.die(9)))
        self.assertEqual((r.exit_code, r.signal), (None, 9))

    def test_the_working_directory_and_environment_are_exactly_what_was_given(self):
        with mock.patch.dict(os.environ, {"PAR_TEST_SECRET": "hunter2", "ANTHROPIC_API_KEY": "sk-ant-abcdefghijklmnop0123"}):
            r = self.run_scenario(scenarios.custom("env", ops.write_env(), *ops.success_tail()),
                                  env={"PATH": "/usr/bin:/bin", "HOME": str(self.home), "TMPDIR": str(self.home)})
        doc = json.loads((self.ws / "env.json").read_text())
        self.assertEqual(r.exit_code, 0)
        self.assertLessEqual({"HOME", "PATH", "TMPDIR"}, set(doc["names"]))
        # only what we passed, plus the two variables the interpreter itself adds on macOS - never the parent's secrets
        self.assertLessEqual(set(doc["names"]), {"HOME", "PATH", "TMPDIR", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"})
        self.assertNotIn("PAR_TEST_SECRET", doc["names"])
        self.assertNotIn("ANTHROPIC_API_KEY", doc["names"])
        self.assertEqual(doc["HOME"], str(self.home))
        self.assertEqual(os.path.realpath(doc["cwd"]), str(self.ws))

    def test_a_read_only_request_is_honoured_by_the_child(self):
        r = self.run_scenario(scenarios.success({"a.txt": "x"}), write=False)
        self.assertFalse((self.ws / "a.txt").exists())
        self.assertEqual(r.exit_code, 1)

    def test_garbage_lines_are_counted_and_do_not_stop_the_run(self):
        r = self.run_scenario(scenarios.garbage())
        self.assertGreaterEqual(r.unparsed, 3)
        self.assertEqual(r.events[-1].data["status"], "SUCCESS")

    def test_a_parser_that_raises_is_survived(self):
        def bad_parser(line):
            raise RuntimeError("parser bug")
        r = self.run_scenario(scenarios.success(), parse_line=bad_parser)
        self.assertEqual(r.exit_code, 0)
        self.assertGreater(r.unparsed, 0)

    def test_an_event_flood_keeps_the_events_that_decide_the_outcome(self):
        flood = [ops.message(str(i)) for i in range(200)]
        with mock.patch.object(sp, "MAX_EVENTS", 50):
            r = self.run_scenario(scenarios.custom("flood", *flood, ops.emit(type="usage", output_tokens=1), *ops.success_tail()))
        self.assertGreater(r.events_dropped, 100)
        self.assertEqual([e.kind for e in r.events if e.kind != "message"], ["usage", "terminal"])

    def test_retained_events_are_bounded_in_bytes_too(self):
        fat = [ops.message("x" * 2000) for _ in range(100)]
        with mock.patch.object(sp, "MAX_EVENT_BYTES", 20_000):
            r = self.run_scenario(scenarios.custom("fat", *fat, ops.emit(type="usage", output_tokens=1), *ops.success_tail()))
        kept = [e for e in r.events if e.kind == "message"]
        self.assertLess(len(kept), 15)
        self.assertGreater(r.events_dropped, 80)
        self.assertEqual([e.kind for e in r.events if e.kind != "message"], ["usage", "terminal"])
        self.assertEqual(r.events[-1].data["status"], "SUCCESS")                           # what decides the outcome is never dropped

    def test_heartbeat_and_cancel_polls_run_on_their_own_schedules(self):
        ticks, polls = [], []
        provider_calls = {"n": 0}

        def should_cancel():
            provider_calls["n"] += 1
            return False
        scenario = scenarios.custom("steady", ops.message("a"), ops.sleep(1.0), *ops.success_tail())
        provider = ScriptedProvider(scenario)
        req = RunRequest("t-0001", 1, "p", self.ws, "m", allow_write=True)
        t0 = time.monotonic()
        sp.run_process(provider.build_argv(req), cwd=self.ws, env={"PATH": "/usr/bin:/bin"},
                       limits=sp.Limits(startup=5, idle=5, total=20, tick=0.2, cancel_check=0.25), parse_line=provider.parse_event,
                       stdout_log=self.base / "o", stderr_log=self.base / "e", request_cancel=provider.request_cancel,
                       on_tick=lambda: ticks.append(time.monotonic()), should_cancel=should_cancel)
        elapsed = time.monotonic() - t0                                   # (a slow machine stretches this, so the bounds scale with it)
        self.assertGreaterEqual(len(ticks), max(1, int(elapsed / 0.2) - 4))
        self.assertGreaterEqual(provider_calls["n"], 1)
        self.assertLessEqual(provider_calls["n"], elapsed / 0.25 + 3)     # ~every 0.25 s, not every 0.05 s poll


class Stopping(RunnerCase):
    def test_silence_at_startup_is_a_startup_timeout(self):
        r = self.run_scenario(scenarios.custom("mute", ops.sleep(30)), startup=0.4, idle=30)
        self.assertEqual(r.timed_out, "startup")
        self.assertEqual(r.signal, signal.SIGTERM)
        self.assertFalse(r.escalated)

    def test_silence_after_output_is_an_idle_timeout_not_proof_of_death(self):
        r = self.run_scenario(scenarios.timeout(30), startup=5, idle=0.4)
        self.assertEqual(r.timed_out, "idle")
        self.assertTrue(any(e.kind == "message" for e in r.events))

    def test_the_total_limit_applies_even_while_output_keeps_coming(self):
        steps = []
        for i in range(100):
            steps += [ops.message(str(i)), ops.sleep(0.1)]
        r = self.run_scenario(scenarios.custom("busy", *steps), startup=5, idle=5, total=0.7)
        self.assertEqual(r.timed_out, "total")
        self.assertLess(r.duration, 10)

    def test_cancel_asks_politely_first(self):
        r = self.run_scenario(scenarios.cancellable(30), cancel_after=0.3)
        self.assertTrue(r.canceled)
        self.assertEqual(r.exit_code, 130)                               # the child answered the polite request itself
        self.assertEqual(r.events[-1].data["status"], "CANCELED")
        self.assertFalse(r.escalated)

    def test_a_child_that_ignores_the_polite_request_is_killed_after_the_grace_period(self):
        r = self.run_scenario(scenarios.custom("stubborn", ops.ignore_sigterm(), ops.message("working"), ops.sleep(30)),
                              cancel_after=0.3, grace=0.5)
        self.assertTrue(r.canceled and r.escalated)
        self.assertEqual(r.signal, signal.SIGKILL)
        self.assertLess(r.duration, 10)

    def test_processes_left_behind_in_the_group_are_cleared(self):
        r = self.run_scenario(scenarios.custom("leaves a stray", ops.stray(30), *ops.success_tail()))
        self.assertEqual(r.exit_code, 0)
        self.assertTrue(r.strays_killed)
        self.assertTrue(self.wait_dead(int((self.ws / "stray.pid").read_text())))

    def test_a_stray_that_holds_the_pipe_open_cannot_hang_the_runner(self):
        r = self.run_scenario(scenarios.custom("holds the pipe", ops.stray(30, detach=True), *ops.success_tail()))
        self.assertEqual(r.exit_code, 0)                                 # it left the group, so it is not ours to kill...
        self.assertFalse(r.strays_killed)
        self.assertLess(r.duration, 12)                                  # ...but the runner still came back (not after 30 s)
        self.assertEqual(r.events[-1].data["status"], "SUCCESS")

    def test_a_failing_callback_kills_the_child_before_the_error_escapes(self):
        def boom():
            raise RuntimeError("database went away")
        with self.assertRaises(RuntimeError):
            self.run_scenario(scenarios.custom("long", ops.message("hi"), ops.sleep(30)), on_tick=boom)
        (_, pgid, _), = self.started
        self.assertFalse(sp.group_alive(pgid))

    def test_ctrl_c_in_the_runner_kills_the_child_too(self):
        def interrupt():
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_scenario(scenarios.custom("long", ops.message("hi"), ops.sleep(30)), on_tick=interrupt)
        (_, pgid, _), = self.started
        self.assertFalse(sp.group_alive(pgid))

    def test_a_child_that_cannot_be_started(self):
        for argv in (["/nonexistent/binary"], [str(self.base)], ["/etc/hosts"]):
            with self.assertRaises(sp.SpawnError, msg=argv):
                sp.run_process(argv, cwd=self.ws, env={}, limits=sp.Limits(), parse_line=lambda l: None,
                               stdout_log=self.base / "o", stderr_log=self.base / "e", request_cancel=lambda p: None)


class Logging(RunnerCase):
    def test_secrets_never_reach_the_logs_or_the_stderr_tail(self):
        key = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
        bearer = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
        r = self.run_scenario(scenarios.custom("leaky", ops.raw(f"using key {key}"), ops.stderr(bearer), ops.stderr("password=hunter2"),
                                               *ops.success_tail()))
        for text in (r.stdout_log.read_text(), r.stderr_log.read_text(), r.stderr_tail):
            for secret in (key, "abcdefghijklmnopqrstuvwxyz0123456789", "hunter2"):
                self.assertNotIn(secret, text)
        self.assertIn("[REDACTED", r.stdout_log.read_text())

    def test_log_size_is_capped_but_the_pipe_is_still_drained(self):
        r = self.run_scenario(scenarios.custom("chatty", ops.flood(5000, 100), *ops.success_tail()), max_log_bytes=10_000)
        self.assertEqual(r.exit_code, 0)
        self.assertTrue(r.logs_truncated)
        size = os.stat(r.stdout_log).st_size
        self.assertLessEqual(size, 10_000 + 64)
        self.assertIn("[log truncated", r.stdout_log.read_text())
        self.assertEqual(r.events[-1].data["status"], "SUCCESS")         # events keep flowing after the log stops

    def test_stderr_tail_is_bounded(self):
        r = self.run_scenario(scenarios.custom("noisy", *[ops.stderr("e" * 100) for _ in range(200)], *ops.success_tail()))
        self.assertLessEqual(len(r.stderr_tail), sp.STDERR_TAIL_CHARS + 120)
        self.assertTrue(r.stderr_tail.endswith("e" * 100))

    def test_a_huge_line_without_a_newline_is_cut_not_buffered_forever(self):
        with mock.patch.object(sp, "MAX_LINE", 1000):
            r = self.run_scenario(scenarios.custom("long line", ops.raw("y" * 5000), *ops.success_tail()))
        self.assertEqual(r.exit_code, 0)
        self.assertGreaterEqual(r.unparsed, 5)


class ProcessFacts(unittest.TestCase):
    def test_identity_exists_for_a_live_process_only(self):
        self.assertTrue(sp.process_identity(os.getpid()))
        self.assertIsNone(sp.process_identity(2 ** 22 - 3))

    def test_liveness_checks(self):
        self.assertTrue(sp.pid_alive(os.getpid()))
        self.assertFalse(sp.pid_alive(2 ** 22 - 3))
        self.assertFalse(sp.group_alive(2 ** 22 - 3))

    def spawn(self, script):
        """An orphan like the ones recovery meets: its parent (a short-lived helper) is gone, so init reaps it."""
        code = ("import subprocess, sys; p = subprocess.Popen(['/bin/sh', '-c', sys.argv[1]], start_new_session=True, "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid, flush=True)")
        pgid = int(subprocess.run([sys.executable, "-I", "-c", code, script], capture_output=True, text=True, check=True).stdout)
        self.addCleanup(lambda: sp._signal_group(pgid, signal.SIGKILL))
        return pgid

    def test_terminate_group_is_polite_when_that_is_enough(self):
        pgid = self.spawn("sleep 30")
        self.assertTrue(sp.group_alive(pgid))
        t0 = time.monotonic()
        self.assertTrue(sp.terminate_group(pgid, grace=10))
        self.assertLess(time.monotonic() - t0, 8)                        # well inside the grace period: it was polite
        self.assertFalse(sp.group_alive(pgid))

    def test_terminate_group_escalates_when_it_has_to(self):
        pgid = self.spawn("trap '' TERM; while true; do sleep 1; done")
        time.sleep(0.3)
        t0 = time.monotonic()
        self.assertTrue(sp.terminate_group(pgid, grace=0.4))
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)              # it really waited out the grace period first
        self.assertFalse(sp.group_alive(pgid))

    def test_terminate_group_on_nothing_is_fine(self):
        self.assertTrue(sp.terminate_group(2 ** 22 - 3, grace=0.1))


if __name__ == "__main__":
    unittest.main()
