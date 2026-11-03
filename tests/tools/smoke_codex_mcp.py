#!/usr/bin/env python3
"""Does the Codex connection kit work with the REAL Codex program? It makes NO model call and uses NO quota.

    python -B tests/tools/smoke_codex_mcp.py [--executable PATH]

What it does, entirely inside throw-away folders (never ~/.codex, never the real data directory):
  1. makes a throw-away PAR data directory and a throw-away CODEX_HOME;
  2. prints the Codex kit for them (`par coordinator kit codex`) and runs its one-line command with the real program;
  3. `codex mcp list` / `codex mcp get par --json` must show an enabled stdio server with the kit's command, arguments and PYTHONPATH;
  4. does it a second time by pasting the kit's TOML snippet into a fresh CODEX_HOME/config.toml and reading it back the same way;
  5. starts the server exactly as Codex's entry says (no special working directory, only the entry's environment) and runs the MCP
     handshake: initialize, tools/list (7 tools, none that approves), then `list_tasks`; the coordinator must then show as "seen".

`codex mcp add/list/get` only read and write a configuration file. Never run by the test suite.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from par import coordinators                                  # noqa: E402
from par.cli_task import session                              # noqa: E402
from par.paths import ensure_data_dir                         # noqa: E402

BUNDLED = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
SEVEN = {"submit_task", "get_task_status", "get_task_result", "cancel_task", "review_task", "run_task", "list_tasks"}
failures = []


def check(ok, what, detail=""):
    print(("PASS  " if ok else "FAIL  ") + what + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(what)


def codex(exe, home, *argv):
    env = dict(os.environ, CODEX_HOME=str(home))
    done = subprocess.run([exe, *argv], env=env, capture_output=True, text=True, timeout=60, cwd="/")
    return done.returncode, done.stdout, done.stderr


def entry_of(exe, home):
    code, out, err = codex(exe, home, "mcp", "get", "par", "--json")
    if code != 0:
        return None, err
    return json.loads(out), err


def judge_entry(label, entry, kit_args, python, parent):
    transport = (entry or {}).get("transport", {})
    check(bool(entry) and entry.get("enabled", True) is not False, f"{label}: the server 'par' is listed and enabled")
    check(transport.get("command") == python, f"{label}: the command is this machine's Python", str(transport.get("command")))
    check(transport.get("args") == kit_args, f"{label}: the arguments are the kit's", str(transport.get("args")))
    check((transport.get("env") or {}).get("PYTHONPATH") == parent, f"{label}: PYTHONPATH is kept", str(transport.get("env")))


def handshake(entry, data_dir):
    """Start the bridge the way a coordinator application does: the entry's command and environment, nowhere in particular."""
    transport = entry["transport"]
    env = {"PATH": "/usr/bin:/bin", **(transport.get("env") or {})}
    proc = subprocess.Popen([transport["command"], *transport["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd="/", text=True)
    sent = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                              "clientInfo": {"name": "smoke", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_tasks", "arguments": {}}}]
    out, err = proc.communicate("".join(json.dumps(m) + "\n" for m in sent), timeout=60)
    replies = {}
    for line in out.splitlines():
        message = json.loads(line)
        replies[message.get("id")] = message
    return proc.returncode, replies, err


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--executable", default=BUNDLED, help="the Codex program (default: the one inside the ChatGPT app)")
    args = parser.parse_args()
    if not Path(args.executable).is_file():
        print(f"no Codex program at {args.executable}; pass --executable", file=sys.stderr)
        return 2
    code, out, _ = codex(args.executable, tempfile.gettempdir(), "--version")
    print("codex:", out.strip() or "(no version printed)")
    with tempfile.TemporaryDirectory(prefix="par-codex-mcp-") as tmp:
        tmp = Path(tmp).resolve()
        data_dir = tmp / "data"
        ensure_data_dir(data_dir, protected=())
        kit = coordinators.connection_kit("codex", python=sys.executable, data_dir=str(data_dir), package_parent=coordinators.package_parent(),
                                          codex_executable=args.executable)
        kit_args = coordinators._serve_args("codex", str(data_dir))
        parent = coordinators.package_parent()

        # 1. the one-line command
        home1 = tmp / "home-command"
        home1.mkdir()
        words = shlex.split(kit["command"])
        code, out, err = codex(words[0], home1, *words[1:])
        check(code == 0, "the one-line command is accepted by `codex mcp add`", (out + err).strip()[:300])
        code, out, err = codex(args.executable, home1, "mcp", "list")
        check(code == 0 and "par" in out, "`codex mcp list` shows par", (out + err).strip()[:300])
        entry, err = entry_of(args.executable, home1)
        judge_entry("command", entry, kit_args, sys.executable, parent)
        if entry:
            rc, replies, err = handshake(entry, data_dir)
            check(rc == 0, "the bridge exits with 0 when its input ends", err.strip()[:300])
            tools = {t["name"] for t in replies.get(2, {}).get("result", {}).get("tools", [])}
            check(tools == SEVEN, "tools/list answers exactly the seven tools", str(sorted(tools)))
            check(not any(("approve" in t or "accept" in t) for t in tools), "none of the tools approves or accepts anything")
            check("result" in replies.get(3, {}) and not replies.get(3, {}).get("result", {}).get("isError"), "list_tasks answers", str(replies.get(3))[:300])
            check(bool(err.strip() == "" or "Traceback" not in err), "the bridge printed no traceback", err.strip()[:300])

        # 2. the TOML snippet pasted by hand
        home2 = tmp / "home-snippet"
        home2.mkdir()
        (home2 / "config.toml").write_text(kit["snippet"]["text"] + "\n")
        code, out, err = codex(args.executable, home2, "mcp", "list")
        check(code == 0 and "par" in out, "`codex mcp list` reads the pasted snippet", (out + err).strip()[:300])
        entry2, err = entry_of(args.executable, home2)
        judge_entry("snippet", entry2, kit_args, sys.executable, parent)

        # 3. the bridge's heartbeat reached the (throw-away) ledger
        with session(argparse.Namespace(data_dir=str(data_dir), registry=None)) as kernel:        # after the handshake: the bridge has closed its ledger
            seen = [c for c in kernel.coordinator_status()["coordinators"] if c["id"] == "codex"][0]["last_seen"]
        check(seen is not None, "`par coordinator status` shows the Codex coordinator as seen after the handshake")
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED: " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
