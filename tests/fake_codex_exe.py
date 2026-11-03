"""A fake `codex` program for tests (the launcher script in tests/fake_codex.py runs ``main``).

Offline subcommands answer from ``$CODEX_HOME/fake.json`` (with defaults shaped like the real CLI's output);
``exec`` plays a scenario. Every call is appended to ``$CODEX_HOME/calls.jsonl``, so a test can see exactly what the
adapter asked for: argv, working directory, environment and the prompt read from standard input.
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

FEATURES = {                         # name: (stage, default). Everything the adapter switches off starts ON.
    "memories": ("experimental", True), "apps": ("stable", True), "plugins": ("stable", True), "plugin_sharing": ("stable", True),
    "remote_plugin": ("stable", True), "skill_mcp_dependency_install": ("stable", True), "hooks": ("stable", True),
    "computer_use": ("stable", True), "browser_use": ("stable", True), "browser_use_external": ("stable", True),
    "browser_use_full_cdp_access": ("stable", True), "in_app_browser": ("stable", True), "multi_agent": ("stable", True),
    "multi_agent_v2": ("stable", False),
    "image_generation": ("stable", True), "shell_snapshot": ("stable", True), "goals": ("stable", True), "fast_mode": ("stable", True),
    "ultrafast_mode": ("under development", False), "daemon_auto_start": ("stable", True),
    "unbounded_connection_retries": ("under development", True), "tool_suggest": ("stable", True),
    "shell_tool": ("stable", True), "unified_exec": ("stable", True), "web_search_request": ("stable", True),
}
MCP = [
    {"name": "node_repl", "enabled": True, "source": "config"},
    {"name": "computer-use", "enabled": False, "source": "config"},
    {"name": "browser_plugin", "enabled": True, "source": "plugin"},
]
DEFAULT_EXEC = {"events": [
    {"type": "thread.started", "thread_id": "thread-fake-1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "All done."}},
    {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 90, "reasoning_output_tokens": 30}},
]}


def split_flags(argv):
    """Pull ``--disable X`` and ``-c k=v`` out of the arguments, wherever they are."""
    disabled, overrides, rest = [], [], []
    it = iter(argv)
    for arg in it:
        if arg == "--disable":
            disabled.append(next(it, ""))
        elif arg in ("-c", "--config"):
            overrides.append(next(it, ""))
        else:
            rest.append(arg)
    return disabled, overrides, rest


def load(home):
    path = home / "fake.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    home = Path(os.environ["CODEX_HOME"])
    cfg = load(home)
    disabled, overrides, rest = split_flags(argv)
    record = {"argv": argv, "cwd": os.getcwd(), "env": dict(os.environ), "stdin": None}
    code = 0
    try:
        code = dispatch(home, cfg, disabled, overrides, rest, record)
    finally:
        if not record.get("logged"):
            log_call(home, record)
    sys.stdout.flush()
    sys.exit(code)


def log_call(home, record):
    """An `exec` call is logged as soon as it has read its prompt, so a test can see it while it is still running."""
    record["logged"] = True
    with open(home / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in record.items() if k != "logged"}) + "\n")


def dispatch(home, cfg, disabled, overrides, rest, record):
    if rest[:1] == ["--version"]:
        print(f"codex-cli {cfg.get('version', '0.162.0-alpha.17.2')}")
        return 0
    if rest[:2] == ["login", "status"]:
        text, code = cfg.get("login", ["Logged in using ChatGPT", 0])
        print(text)
        return code
    if rest[:2] == ["features", "list"]:
        return features(cfg, disabled)
    if rest[:2] == ["mcp", "list"]:
        return mcp(cfg, disabled, overrides)
    if rest[:2] == ["debug", "models"]:
        print(json.dumps({"models": [{"slug": m} for m in cfg.get("models", ["gpt-fake-1", "gpt-fake-2"])]}))
        return 0
    if rest[:1] == ["exec"]:
        return run_exec(home, cfg, rest[1:], record)
    print("fake codex: unsupported call", file=sys.stderr)
    return 64


def features(cfg, disabled):
    if cfg.get("features_exit"):
        print("features list failed", file=sys.stderr)
        return cfg["features_exit"]
    drop = set(cfg.get("unknown_features", []))
    for name, (stage, default) in FEATURES.items():
        if name in drop:
            continue
        value = default and not (name in disabled and not (name == "memories" and cfg.get("memories_sticky")))
        print(f"{name:<40} {stage:<18} {'true' if value else 'false'}")
    return 0


def mcp(cfg, disabled, overrides):
    if cfg.get("mcp_raw") is not None:
        print(cfg["mcp_raw"])
        return 0
    if cfg.get("mcp_exit"):
        print("mcp list failed", file=sys.stderr)
        return cfg["mcp_exit"]
    off = set()
    if not cfg.get("ignore_overrides"):
        for item in overrides:
            if item.startswith("mcp_servers.") and item.endswith(".enabled=false"):
                off.add(item[len("mcp_servers."):-len(".enabled=false")])
    out = []
    for server in cfg.get("mcp", MCP):
        if server["source"] == "plugin" and "plugins" in disabled:
            continue
        out.append({"name": server["name"], "enabled": server["enabled"] and server["name"] not in off, "transport": {"type": "stdio"}})
    print(json.dumps(out, indent=2))
    return 0


def run_exec(home, cfg, args, record):
    record["stdin"] = sys.stdin.read() if "-" in args else None
    scenario = cfg.get("exec", DEFAULT_EXEC)
    if "sequence" in scenario:
        log = (home / "calls.jsonl")
        seen = sum(1 for line in (log.read_text().splitlines() if log.exists() else []) if split_flags(json.loads(line)["argv"])[2][:1] == ["exec"])
        steps = scenario["sequence"]
        scenario = steps[min(seen, len(steps) - 1)]
    mode = scenario.get("sigint", "graceful")
    if mode == "graceful":
        signal.signal(signal.SIGINT, lambda *_: os._exit(130))
    elif mode == "ignore":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    log_call(home, record)                                  # logged last: a test that sees it may signal us at once
    workdir = Path(args[args.index("-C") + 1]) if "-C" in args else Path.cwd()
    time.sleep(scenario.get("sleep_before", 0))
    for rel, text in scenario.get("writes", {}).items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for rel in scenario.get("deletes", []):
        (workdir / rel).unlink()
    for item in scenario.get("events", []):
        if "$sleep" in item:
            time.sleep(item["$sleep"])
        elif "$raw" in item:
            print(item["$raw"], flush=True)
        elif "$stderr" in item:
            print(item["$stderr"], file=sys.stderr, flush=True)
        elif "$exit" in item:
            return item["$exit"]
        else:
            print(json.dumps(item), flush=True)
    if scenario.get("hang"):
        time.sleep(600)
    return scenario.get("exit", 0)
