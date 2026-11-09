"""The bridge as a real child process over pipes: stdout carries JSON lines only, errors stay short, and `par mcp config`
prints a snippet and writes nothing.

The command is reached through a two-line shim that does what `par.cli.main` does, so these tests hold whether or not
the command is already registered in par.cli. Everything lives under a temporary HOME.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from par.paths import ensure_data_dir
from tests import SRC
from tests.helpers import tree_digest

SHIM = r"""
import argparse, sys
from par import cli, cli_mcp
parser = cli.build_parser()
sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
if "mcp" not in sub.choices:
    cli_mcp.add_parser(sub)
args = parser.parse_args(sys.argv[1:])
raise SystemExit(args.func(args))
"""


def run_bridge(home, *argv, stdin=b"", timeout=60):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-B", "-c", SHIM, *argv], input=stdin, capture_output=True, env=env,
                          cwd=str(home), timeout=timeout)
    return done.returncode, done.stdout, done.stderr


class BridgeProcess(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(os.path.realpath(self._tmp.name)) / "home"
        self.home.mkdir()
        self.data = self.home / "data"
        ensure_data_dir(self.data, protected=())

    def tearDown(self):
        self._tmp.cleanup()

    def test_stdout_carries_only_json_lines_and_end_of_input_ends_the_session_with_zero(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_tasks", "arguments": {}}},
        ]
        stdin = b"".join(json.dumps(r).encode() + b"\n" for r in requests) + b"{garbage\n"
        code, out, err = run_bridge(self.home, "mcp", "serve", "--data-dir", str(self.data), stdin=stdin)
        self.assertEqual(code, 0)
        self.assertNotIn(b"Traceback", err)
        lines = out.split(b"\n")
        self.assertEqual(lines[-1], b"", "the last line ends with a newline and nothing follows it")
        replies = [json.loads(line) for line in lines[:-1]]
        self.assertEqual([r.get("id") for r in replies], [1, 2, 3, None])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(replies[1]["result"]["tools"]), 21)
        self.assertEqual(replies[2]["result"]["isError"], False)
        self.assertEqual(json.loads(replies[2]["result"]["content"][0]["text"]), {"tasks": [], "count": 0, "truncated": False})
        self.assertEqual(replies[3]["error"]["code"], -32700)

    def test_a_relative_data_dir_is_refused_with_a_short_message(self):
        code, out, err = run_bridge(self.home, "mcp", "serve", "--data-dir", "relative/dir")
        self.assertEqual(code, 2)
        self.assertEqual(out, b"")
        self.assertIn(b"absolute path", err)
        self.assertNotIn(b"Traceback", err)

    def test_an_uninitialised_data_dir_is_refused_without_printing_its_absolute_path(self):
        missing = self.home / "not-initialised"
        code, out, err = run_bridge(self.home, "mcp", "serve", "--data-dir", str(missing))
        self.assertEqual(code, 2)
        self.assertEqual(out, b"")
        self.assertIn(b"<data>", err)
        self.assertNotIn(str(missing).encode(), err)
        self.assertNotIn(b"Traceback", err)

    def test_config_prints_the_snippet_and_three_lines_and_writes_nothing(self):
        before = tree_digest(self.home)
        code, out, err = run_bridge(self.home, "mcp", "config", "--data-dir", str(self.data))
        self.assertEqual(code, 0)
        self.assertEqual(err, b"")
        text = out.decode("utf-8")
        snippet, end = json.JSONDecoder().raw_decode(text)
        self.assertEqual(set(snippet), {"par"})
        self.assertEqual(snippet["par"]["command"], sys.executable)
        self.assertEqual(snippet["par"]["args"], ["-B", "-m", "par", "mcp", "serve", "--data-dir", str(self.data)])
        self.assertEqual(set(snippet["par"]["env"]), {"PYTHONPATH"})                              # Claude Desktop starts it from nowhere in particular
        self.assertTrue((Path(snippet["par"]["env"]["PYTHONPATH"]) / "par" / "__init__.py").is_file())
        instructions = [line for line in text[end:].splitlines() if line.strip()]
        self.assertEqual(len(instructions), 3)
        self.assertIn("mcpServers", instructions[0])
        self.assertEqual(tree_digest(self.home), before, "par mcp config never touches a file")

    def test_config_refuses_a_relative_data_dir(self):
        code, out, err = run_bridge(self.home, "mcp", "config", "--data-dir", "relative")
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"absolute path", err)


if __name__ == "__main__":
    unittest.main()
