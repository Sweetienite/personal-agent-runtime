"""The fake ``copilot`` program tests/fake_copilot.py launches.

Offline calls answer from ``$COPILOT_HOME/fake.json``. A prompt is read from standard input and
answered with the JSONL shape the real CLI uses: ``assistant.message`` then ``result``. Every call
is appended to ``$COPILOT_HOME/calls.jsonl`` so a test can inspect argv, cwd, environment and stdin.
"""
import json
import os
import sys
import time
from pathlib import Path

HELP = """GitHub Copilot CLI - fake

  --allow-all-tools
  --available-tools[=tools...]
  --disable-builtin-mcps
  --no-custom-instructions
  --no-ask-user
  --no-auto-update
  --no-bash-env
  --disallow-temp-dir
  --output-format <format>
  --model <model> (choices: "claude-haiku-4.5", "gpt-5-mini")
  --log-level <level>
  --log-dir <directory>
  --secret-env-vars[=vars...]
"""
DEFAULT_EVENTS = [
    {"type": "assistant.message_start"},
    {"type": "assistant.message", "content": "PAR-COPILOT-OK"},
    {"type": "result", "exitCode": 0, "sessionId": "session-fake-1",
     "usage": {"input_tokens": 12, "output_tokens": 3}},
]


def load(home):
    path = home / "fake.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def log(home, record):
    with open(home / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    home = Path(os.environ["COPILOT_HOME"])
    cfg = load(home)
    record = {"argv": argv, "cwd": os.getcwd(), "env": dict(os.environ), "stdin": None, "kind": "prompt"}
    if "--version" in argv:
        record["kind"] = "version"
        log(home, record)
        print(f"GitHub Copilot CLI {cfg.get('version', '1.0.5')}.")
        return 0
    if "--help" in argv:
        record["kind"] = "help"
        log(home, record)
        print(cfg.get("help", HELP))
        return cfg.get("help_exit", 0)
    if "-p" in argv or "--prompt" in argv:
        log(home, record)
        print("prompt must be on stdin", file=sys.stderr)
        return 64
    record["stdin"] = sys.stdin.read()
    log(home, record)
    if cfg.get("hang"):
        time.sleep(cfg["hang"])
    for rel, text in cfg.get("writes", {}).items():
        target = Path(record["cwd"]) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for item in cfg.get("events", DEFAULT_EVENTS):
        if isinstance(item, dict) and "$stderr" in item:
            print(item["$stderr"], file=sys.stderr, flush=True)
        elif isinstance(item, dict) and "$sleep" in item:
            time.sleep(item["$sleep"])
        elif isinstance(item, dict) and "$exit" in item:
            return item["$exit"]
        else:
            print(json.dumps(item), flush=True)
    if cfg.get("stderr"):
        print(cfg["stderr"], file=sys.stderr, flush=True)
    return cfg.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main())
