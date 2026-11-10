"""``par mcp serve`` and ``par mcp config``: the Claude Desktop bridge (plan P5, see docs/MCP.md).

``serve`` is started by the coordinator, never by hand: it speaks MCP on stdin and stdout and nothing else. ``config``
prints the snippet for Claude Desktop's configuration and writes no file. Neither command decides anything for the person.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import par
from par import cli_task
from par.coordinators import known_names
from par.core.policy import PolicyViolation
from par.core.store import LedgerError
from par.mcp.server import serve
from par.mcp.tools import Tools, scrub


def add_parser(sub: Any) -> None:
    p = sub.add_parser("mcp", help="the MCP bridge: serve the Kernel to any MCP-capable coordinator (Claude Desktop, Codex, Antigravity ...), or print its config")
    actions = p.add_subparsers(dest="mcp_command", required=True, metavar="ACTION")
    c = actions.add_parser("serve", help="speak MCP on stdin and stdout (started by the coordinator, not by hand)")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.add_argument("--coordinator", default="claude_desktop", choices=sorted(known_names()),
                   help="who this bridge speaks for (default: claude_desktop); refused once another coordinator has taken over")
    c.set_defaults(func=dispatch)
    c = actions.add_parser("config", help="print the snippet for Claude Desktop's configuration (writes nothing)")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.set_defaults(func=dispatch)


def dispatch(args: Any) -> int:
    if getattr(args, "mcp_command", None) == "config":
        return _config(args)
    return _serve(args)


def _serve(args: Any) -> int:
    try:
        kernel = cli_task.open_kernel(args, allow_inspection=True)
    except (PolicyViolation, LedgerError, sqlite3.Error, OSError) as exc:
        _say_error(str(exc), _data_hint(args))
        return 2
    try:
        if kernel.inspection_diagnostic:
            _say_error(kernel.inspection_diagnostic, _data_hint(args))
        return serve(Tools(kernel, getattr(args, "coordinator", "claude_desktop")), sys.stdin.buffer, sys.stdout.buffer, sys.stderr)
    except KeyboardInterrupt:
        return 0
    finally:
        kernel.close()


def _config(args: Any) -> int:
    try:
        data = _data_dir(args)
    except PolicyViolation as exc:
        _say_error(str(exc), None)
        return 2
    package_parent = str(Path(par.__file__).resolve().parent.parent)          # where `import par` finds this code (a source checkout or site-packages)
    snippet = {"par": {"command": sys.executable, "args": ["-B", "-m", "par", "mcp", "serve", "--data-dir", str(data)],
                       "env": {"PYTHONPATH": package_parent}}}
    print(json.dumps(snippet, indent=2, ensure_ascii=False))
    print('Paste the "par" entry into the "mcpServers" object of Claude Desktop\'s configuration file, then restart Claude Desktop.')
    print("par does not write that file: this command only prints the text, and changes nothing on this Mac.")
    print("Then ask the coordinator to call list_tasks: it should see the par tools, and approvals stay with you.")
    return 0


def _data_dir(args: Any) -> Path:
    from par import cli                  # late: par.cli imports this module when it wires the command
    return cli._data_dir(args)


def _data_hint(args: Any) -> "Path | None":
    try:
        return _data_dir(args)
    except PolicyViolation:
        return None


def _say_error(message: str, data_dir: "Path | None") -> None:
    print("error: " + scrub(message, data_dir, 300), file=sys.stderr)
