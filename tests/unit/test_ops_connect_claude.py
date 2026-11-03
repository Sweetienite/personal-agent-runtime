"""ops/connect_claude_desktop.py: the user-run helper that adds (or removes) the "par" entry in Claude Desktop's configuration.

Nothing here touches the real configuration: every file is a temporary one, and the running-app check is injected."""
import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

OPS = Path(__file__).resolve().parents[2] / "ops" / "connect_claude_desktop.py"
spec = importlib.util.spec_from_file_location("connect_claude_desktop", OPS)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

ENTRY = {"command": "/p/python", "args": ["-B", "-m", "par", "mcp", "serve", "--data-dir", "/d", "--coordinator", "claude_desktop"], "env": {"PYTHONPATH": "/p/src"}}
ORIGINAL = {"coworkUserFilesPath": "/x", "preferences": {"a": True, "中文": "值", "n": [1, 2]}}


def two_space(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


class ChangedText(unittest.TestCase):
    def test_adds_the_entry_and_changes_nothing_else(self):
        new = json.loads(helper.changed_text(two_space(ORIGINAL), ENTRY))
        self.assertEqual(list(new), ["coworkUserFilesPath", "preferences", "mcpServers"])
        self.assertEqual({k: v for k, v in new.items() if k != "mcpServers"}, ORIGINAL)
        self.assertEqual(new["mcpServers"], {"par": ENTRY})

    def test_keeps_the_other_servers_and_their_order(self):
        data = dict(ORIGINAL, mcpServers={"alpha": {"command": "a"}, "zeta": {"command": "z"}})
        new = json.loads(helper.changed_text(two_space(data), ENTRY))
        self.assertEqual(list(new["mcpServers"]), ["alpha", "zeta", "par"])
        self.assertEqual(new["mcpServers"]["alpha"], {"command": "a"})

    def test_a_second_run_changes_nothing_and_an_outdated_entry_is_replaced(self):
        once = helper.changed_text(two_space(ORIGINAL), ENTRY)
        self.assertIsNone(helper.changed_text(once, ENTRY))
        newer = dict(ENTRY, command="/other/python")
        replaced = json.loads(helper.changed_text(once, newer))
        self.assertEqual(replaced["mcpServers"]["par"]["command"], "/other/python")

    def test_remove_takes_only_the_par_entry_out_and_drops_an_empty_mcpservers(self):
        only = helper.changed_text(two_space(ORIGINAL), ENTRY)
        self.assertEqual(json.loads(helper.changed_text(only, None)), ORIGINAL)
        data = dict(ORIGINAL, mcpServers={"alpha": {"command": "a"}, "par": ENTRY})
        left = json.loads(helper.changed_text(two_space(data), None))
        self.assertEqual(left["mcpServers"], {"alpha": {"command": "a"}})
        self.assertIsNone(helper.changed_text(two_space(ORIGINAL), None))

    def test_a_file_with_other_formatting_is_edited_by_insertion_and_every_other_byte_stays(self):
        text = json.dumps(ORIGINAL, indent=4, ensure_ascii=False) + "\n"                          # not the 2-space layout: a rewrite would change it
        new = helper.changed_text(text, ENTRY)
        self.assertEqual(json.loads(new), dict(ORIGINAL, mcpServers={"par": ENTRY}))
        end = text.rstrip().rfind("}")
        self.assertTrue(new.startswith(text[:end].rstrip()))
        self.assertTrue(new.endswith(text[end:]))

    def test_refuses_what_it_cannot_change_safely(self):
        for text in ("{not json", "[]", '"x"'):
            with self.assertRaises(helper.Refused, msg=text):
                helper.changed_text(text, ENTRY)
        with self.assertRaises(helper.Refused):
            helper.changed_text(json.dumps({"mcpServers": []}), ENTRY)
        odd = json.dumps(dict(ORIGINAL, mcpServers={"alpha": {"command": "a"}}), indent=4)        # formatting we would have to rewrite, with servers present
        with self.assertRaises(helper.Refused):
            helper.changed_text(odd, ENTRY)


class Base(unittest.TestCase):
    """A temporary configuration file and the lines the helper says."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(os.path.realpath(self._tmp.name))
        self.path = self.dir / "claude_desktop_config.json"
        self.original_text = two_space(ORIGINAL)
        self.path.write_text(self.original_text, encoding="utf-8")
        os.chmod(self.path, 0o640)                                                                  # not the default: the new file must keep exactly this
        self.lines = []

    def tearDown(self):
        self._tmp.cleanup()

    def files(self):
        return sorted(p.name for p in self.dir.iterdir())


class Run(Base):
    def call(self, *, apply=False, remove=False, running=False):
        args = Namespace(config=str(self.path), apply=apply, remove=remove, python="/p/python", data_dir="/d")
        helper.entry_for = lambda python, data_dir: ENTRY                                          # the entry itself is tested in test_coordinators
        return helper.run(args, running=lambda: running, out=self.lines.append)

    def test_a_dry_run_writes_nothing_and_says_how_to_apply(self):
        self.assertEqual(self.call(), 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original_text)
        self.assertEqual(self.files(), ["claude_desktop_config.json"])
        self.assertIn("--apply", "\n".join(self.lines))

    def test_apply_is_refused_while_the_app_runs_and_leaves_everything_alone(self):
        self.assertEqual(self.call(apply=True, running=True), 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original_text)
        self.assertEqual(self.files(), ["claude_desktop_config.json"])
        self.assertIn("还在运行", "\n".join(self.lines))

    def test_apply_backs_up_writes_keeps_the_permissions_and_leaves_no_temporary_file(self):
        self.assertEqual(self.call(apply=True), 0)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), dict(ORIGINAL, mcpServers={"par": ENTRY}))
        backups = [n for n in self.files() if ".par-backup-" in n]
        self.assertEqual(len(backups), 1)
        self.assertEqual((self.dir / backups[0]).read_text(encoding="utf-8"), self.original_text)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE((self.dir / backups[0]).stat().st_mode), 0o600)
        self.assertFalse([n for n in self.files() if n.endswith(".par-tmp")])
        self.assertEqual(self.call(apply=True), 0)                                                  # nothing to do the second time: no second backup
        self.assertEqual(len([n for n in self.files() if ".par-backup-" in n]), 1)

    def test_a_failed_self_check_puts_the_backup_back(self):
        real = helper.write_atomically
        helper.write_atomically = lambda path, text: real(path, two_space({"broken": True}))        # what lands on disk is not what was meant
        try:
            code = self.call(apply=True)
        finally:
            helper.write_atomically = real
        self.assertEqual(code, 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original_text)

    def test_remove_needs_apply_too_and_undoes_the_edit(self):
        self.call(apply=True)
        self.assertEqual(self.call(remove=True), 0)                                                 # a dry run: the entry is still there
        self.assertIn("par", json.loads(self.path.read_text(encoding="utf-8"))["mcpServers"])
        self.assertEqual(self.call(remove=True, apply=True), 0)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), ORIGINAL)

    def test_a_missing_file_and_a_broken_file_are_refused_without_writing(self):
        broken = self.path
        broken.write_text("{oops", encoding="utf-8")
        self.assertEqual(self.call(apply=True), 1)
        self.assertEqual(broken.read_text(encoding="utf-8"), "{oops")
        broken.unlink()
        self.assertEqual(self.call(apply=True), 1)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.call(remove=True, apply=True), 0)                                     # nothing to remove from a file that is not there


class RunningCheck(unittest.TestCase):
    class Done:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out

    APP = "/Applications/Claude.app/Contents/MacOS/Claude"
    HELPER = "/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper --type=gpu-process"
    CODE = "/Users/me/Library/Application Support/Claude/claude-code/2.1.295/9d64/claude.app/Contents/MacOS/claude --print"
    OTHERS = "/sbin/launchd\n/usr/bin/python3 /tmp/x.py\n/usr/bin/vim /Users/me/Claude.app/notes.txt\n" + CODE + "\n"

    def ps(self, text, code=0):
        return lambda *a, **k: self.Done(code, text)

    def test_any_process_of_the_app_counts_as_running(self):
        self.assertTrue(helper.claude_running(self.ps(self.OTHERS + self.APP + "\n")))
        self.assertTrue(helper.claude_running(self.ps(self.OTHERS + self.HELPER + "\n")))       # only a helper left: the app is not gone yet

    def test_other_programs_do_not_count_not_even_the_claude_code_binary_or_a_mention_of_the_path(self):
        self.assertFalse(helper.claude_running(self.ps(self.OTHERS)))
        self.assertFalse(helper.claude_running(self.ps("")))

    def test_when_the_process_list_cannot_be_read_the_answer_is_running(self):
        self.assertTrue(helper.claude_running(self.ps("", code=1)))

    def test_the_command_line_wants_absolute_paths(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                helper.main(["--python", "python3"])
            with self.assertRaises(SystemExit):
                helper.main(["--data-dir", "relative/dir"])
            with self.assertRaises(SystemExit):
                helper.main(["--background"])                                                        # waiting in the background only makes sense with --apply
            with self.assertRaises(SystemExit):
                helper.main(["--apply", "--wait", "-5"])


class WaitingAndReopening(Base):
    """`--wait`: the file is read only after the app has quit (its quit-time write must not be lost); `--reopen`: only after a successful write."""

    def call(self, *, wait=60, reopen=False, quits_after=3, rewrite=None, remove=False):
        state = {"polls": 0, "opened": 0, "slept": 0}

        def running():
            state["polls"] += 1
            return state["polls"] <= quits_after

        def sleep(_seconds):
            state["slept"] += 1
            if rewrite is not None and state["polls"] == quits_after:                                # the app writes its own file while quitting
                self.path.write_text(rewrite, encoding="utf-8")

        args = Namespace(config=str(self.path), apply=True, remove=remove, python="/p/python", data_dir="/d", wait=wait, reopen=reopen)
        helper.entry_for = lambda python, data_dir: ENTRY
        code = helper.run(args, running=running, out=self.lines.append, sleep=sleep, reopen=lambda out: state.__setitem__("opened", state["opened"] + 1))
        return code, state

    def test_it_waits_until_the_app_is_gone_then_writes_and_reopens_once(self):
        code, state = self.call(reopen=True)
        self.assertEqual(code, 0)
        self.assertGreaterEqual(state["slept"], 3)
        self.assertEqual(state["opened"], 1)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), dict(ORIGINAL, mcpServers={"par": ENTRY}))

    def test_the_app_must_stay_gone_for_several_checks_in_a_row(self):
        seen = iter([True, False, True, False, False, False, False])                                # it flickers: gone, back, then really gone
        polls = []

        def running():
            polls.append(1)
            return next(seen)

        self.assertTrue(helper.wait_until_quit(running, 60, out=self.lines.append, sleep=lambda _s: None))
        self.assertEqual(len(polls), 6)                                                              # not after the first quiet check

    def test_without_reopen_the_app_is_left_closed(self):
        code, state = self.call(reopen=False)
        self.assertEqual((code, state["opened"]), (0, 0))

    def test_what_the_app_wrote_while_quitting_is_kept_because_the_file_is_read_after_the_wait(self):
        newer = dict(ORIGINAL, preferences={"changed": "at quit"})
        code, _ = self.call(rewrite=two_space(newer))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), dict(newer, mcpServers={"par": ENTRY}))

    def test_a_timeout_changes_nothing_and_does_not_reopen(self):
        code, state = self.call(wait=3, quits_after=10_000, reopen=True)
        self.assertEqual(code, 1)
        self.assertEqual(state["opened"], 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original_text)
        self.assertEqual([n for n in os.listdir(self.dir) if ".par-backup-" in n], [])

    def test_nothing_to_change_does_not_reopen_the_app(self):
        self.call()                                                                                 # connected now
        code, state = self.call(reopen=True)                                                        # nothing left to do
        self.assertEqual(code, 0)
        self.assertEqual(state["opened"], 0)


class InTheBackground(unittest.TestCase):
    class Child:
        pid = 4242

    def test_it_starts_itself_in_a_session_of_its_own_and_logs_to_the_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(os.path.realpath(tmp))
            seen = {}

            def spawn(argv, **kw):
                seen["argv"], seen["kw"] = argv, kw
                return self.Child()

            lines = []
            args = Namespace(data_dir=str(data))
            code = helper.background(["--apply", "--background", "--reopen"], args, spawn, out=lines.append)
            self.assertEqual(code, 0)
            self.assertNotIn("--background", seen["argv"])
            self.assertEqual(seen["argv"][seen["argv"].index("--wait") + 1], "1800")                # a long default wait
            self.assertIn("--reopen", seen["argv"])
            self.assertIs(seen["kw"]["start_new_session"], True)                                    # it survives the app (and its terminal) quitting
            self.assertEqual(stat.S_IMODE((data / "logs" / "connect_claude_desktop.log").stat().st_mode), 0o600)
            self.assertIn("4242", lines[0])
            self.assertIn("重新打开", lines[0])

    def test_an_explicit_wait_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = {}
            helper.background(["--apply", "--background", "--wait", "90"], Namespace(data_dir=os.path.realpath(tmp)),
                              lambda argv, **kw: seen.setdefault("argv", argv) and self.Child(), out=lambda _l: None)
            self.assertEqual(seen["argv"].count("--wait"), 1)
            self.assertEqual(seen["argv"][seen["argv"].index("--wait") + 1], "90")


if __name__ == "__main__":
    unittest.main()
