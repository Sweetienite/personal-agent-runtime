"""A fake `agy` program for tests (the launcher script in tests/fake_agy.py runs ``main``).

Offline calls (``--version``, ``-p /usage``, ``models``) are answered from ``$FAKE_AGY_ROOT/fake.json``, with defaults shaped like
the real CLI's output. A worker run (``--input-format stream-json --output-format stream-json``) reads its prompt from standard
input and plays a scenario. Every call is appended to ``$FAKE_AGY_ROOT/calls.jsonl``, so a test can see exactly what the adapter
asked for: argv, working directory, environment, the prompt, the private HOME's settings and the Keychains link.

This program imports nothing from the Runtime: a test runs it as a separate program.
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

CONV = "conv-fake-1"
DEFAULT_VERSION = "1.3.5"
DEFAULT_MODELS = ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low", "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"]
DEFAULT_MODE = "request-review"                     # the permission mode when settings.json is missing or unreadable
EFFORTS = ("low", "medium", "high")
VALUE_FLAGS = ("--input-format", "--output-format", "--model", "--print-timeout", "--mode")
TOOLS = ["ask_permission", "run_command", "write_to_file", "view_file", "list_dir"]
USAGE = {"input_tokens": 1200, "output_tokens": 90, "thinking_tokens": 30, "cache_read_tokens": 800, "total_tokens": 1290}
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0}
USAGE_REPORT = ["Gemini Models\tWeekly Limit Remaining\t99%\t2026-10-20T08:19:37Z", "Gemini Models\tFive Hour Limit Remaining\t100%\t2026-10-14T21:12:50Z",
                "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-10-21T16:12:50Z", "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-10-14T21:12:50Z"]
LOGIN_FAILURES = {                                  # login state: (stderr of `models`, exit code) - the wording of "none" is the real 1.3.5's
    "none": ("Error: Please sign in to view available models. Launch the CLI without arguments to sign in.", 1),
    "locked": ("Error: failed to retrieve token: secret keyring is locked", 1),
    "weird": ("something unexpected broke", 3),
}
OAUTH_NOTICE = ("Authentication required. Please visit the URL to log in:\n  https://accounts.google.com/o/oauth2/auth?client_id=fake\n\n"
                "Waiting for authentication (timeout 60s)...\nOr, paste the authorization code here and press Enter:")
UNKNOWN_MODEL = ('invalid model selection (--model "{model}" --effort ""): model {model} is not recognized as a known model '
                 'or custom model in settings\nAvailable models:\nGemini 3.8 Flash (Medium)')
LOG_KEYS = ("kind", "argv", "cwd", "env", "stdin", "home_settings", "keychains_link")
WRITE_NOTICE = ('jetski: no output produced \u2014 a tool required the "write_file" permission that headless mode cannot prompt for, so it was auto-denied. '
                'Add an allow-rule under permissions.allow in settings.json (e.g. write_file(<target>)). Alternatively, re-run with '
                '--dangerously-skip-permissions to auto-approve all tools.')       # REAL wording (1.3.5, write smoke 2026-10-15)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.environ.get("FAKE_AGY_ROOT")
    if not root:
        print("fake agy: FAKE_AGY_ROOT is not set", file=sys.stderr)
        sys.exit(64)
    home = os.environ.get("HOME") or None
    record = {"kind": classify(argv), "argv": argv, "cwd": os.getcwd(),
              "env": {k: v for k, v in os.environ.items() if k != "FAKE_AGY_ROOT"}, "stdin": None,
              "home_settings": home_settings(home), "keychains_link": keychains_link(home)}
    try:
        code = dispatch(Path(root), argv, record)
    finally:
        if not record.get("logged"):
            log_call(Path(root), record)
    sys.stdout.flush()
    sys.exit(code)


def classify(argv):
    if "--version" in argv:
        return "version"
    if any(a == "-p" and b == "/usage" for a, b in zip(argv, argv[1:])):
        return "usage"
    if argv[:1] == ["models"]:
        return "models"
    if flag_value(argv, "--input-format") == "stream-json" and flag_value(argv, "--output-format") == "stream-json":
        return "run"
    return "other"


def flag_value(argv, flag):
    """The word after `flag`, or None when the flag is absent or has no value."""
    if flag not in argv or argv.index(flag) + 1 >= len(argv):
        return None
    return argv[argv.index(flag) + 1]


def dispatch(root, argv, record):
    cfg = load(root)
    kind = record["kind"]
    if kind == "version":
        print(f"agy {cfg.get('version', DEFAULT_VERSION)}")
        return 0
    if kind == "usage":
        return usage(cfg)
    if kind == "models":
        return models(cfg)
    if kind == "run":
        return run(root, cfg, argv, record)
    print("fake agy: unsupported call", file=sys.stderr)
    return 64


def usage(cfg):
    """Signed in: the quota table. Signed out the REAL CLI does not fail: it prints a login URL and waits (60 s) for a pasted code."""
    state = cfg.get("login", "ok")
    if state == "ok":
        print("\n".join(USAGE_REPORT))
        return 0
    if state not in LOGIN_FAILURES:
        print(f"fake agy: unknown login state {state!r}", file=sys.stderr)
        return 64
    print(OAUTH_NOTICE, file=sys.stderr, flush=True)
    time.sleep(float(cfg.get("usage_wait", 60)))
    return 1


def models(cfg):
    """Signed in: one `slug<TAB>display name` line per model; signed out it fails at once (the real CLI's behaviour)."""
    state = cfg.get("login", "ok")
    print("Fetching available models...", file=sys.stderr, flush=True)
    if state != "ok":
        if state not in LOGIN_FAILURES:
            print(f"fake agy: unknown login state {state!r}", file=sys.stderr)
            return 64
        text, code = LOGIN_FAILURES[state]
        print(text, file=sys.stderr)
        return code
    code = cfg.get("models_exit", 0)
    if isinstance(code, int) and code:
        print("models failed", file=sys.stderr)
        return code
    for slug in cfg.get("models", DEFAULT_MODELS):
        print(f"{slug}\t{display_name(slug)}")
    return 0


def display_name(slug):
    """gemini-3.8-flash-medium -> Gemini 3.8 Flash (Medium)."""
    words = slug.split("-")
    effort = None
    if len(words) > 1 and words[-1] in EFFORTS:
        effort = words.pop().capitalize()
    name = " ".join(word.capitalize() for word in words)
    return f"{name} ({effort})" if effort else name


def run(root, cfg, argv, record):
    opts, problem = parse_run(argv)
    if problem:                                     # refused before the input is read: logged, nothing played
        log_call(root, record)
        print(problem, file=sys.stderr, flush=True)
        return 2
    prompt, problem = read_prompt(sys.stdin.buffer.read())
    scenario = pick(cfg, prior_runs(root))
    install_sigint(scenario.get("sigint", "graceful"))  # before the call is logged: a test may signal as soon as it sees it
    record["stdin"] = prompt
    log_call(root, record)
    if problem:
        emit(result_event("ERROR", "", error=problem))
        return 1
    model = opts["--model"]
    if (cfg.get("run") or {}).get("model_check", True) and model is not None and model not in cfg.get("models", DEFAULT_MODELS):
        emit(result_event("ERROR", "", error=UNKNOWN_MODEL.format(model=model)))
        return 1
    time.sleep(scenario.get("sleep_before", 0))
    real = tool_permission(record["home_settings"])
    reported = scenario.get("permission_mode", real)  # what the init event says
    change_files(scenario, scenario.get("act_as", real), record["home_settings"], accept_edits=opts["--mode"] == "accept-edits")  # what the writes obey
    for item in scenario.get("events", default_events()):
        if "$sleep" in item:
            time.sleep(item["$sleep"])
        elif "$raw" in item:
            print(item["$raw"], flush=True)
        elif "$stderr" in item:
            print(item["$stderr"], file=sys.stderr, flush=True)
        elif "$exit" in item:
            return item["$exit"]
        elif "$init" in item:
            if item["$init"]:
                emit(init_event(reported, model))
        else:
            emit(item)
    if scenario.get("hang"):
        time.sleep(600)
    return scenario.get("exit", 0)


def parse_run(argv):
    """The worker's flags: (options, None), or (None, the refusal to print)."""
    opts = {"--sandbox": False, **{flag: None for flag in VALUE_FLAGS}}
    args = iter(argv)
    for arg in args:
        if arg in VALUE_FLAGS:
            value = next(args, None)
            if value is None:
                return None, f"Error: flag needs an argument: {arg}"
            opts[arg] = value
        elif arg == "--sandbox":
            opts[arg] = True
        elif arg.startswith("-"):
            return None, f"Error: unknown flag: {arg}"
    return opts, None


def read_prompt(data):
    """The prompt the stdin lines carry (text blocks joined by newlines), or the reason the input is refused."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "stdin is not valid UTF-8"
    pieces = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            return None, f"stdin line {number} is not valid JSON"
        if not isinstance(message, dict) or "event" not in message:
            return None, f"stdin line {number} has no event"
        event = message["event"]
        if event != "user":
            print(f'warning: ignoring unsupported stream input message event "{event}"', file=sys.stderr, flush=True)
            continue
        body = message.get("message")
        content = body.get("content") if isinstance(body, dict) else None
        if isinstance(content, str):
            pieces.append(content)
        elif isinstance(content, list):
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)):
                    return None, f"stdin line {number} has a content block that is not text"
                pieces.append(block["text"])
        else:
            return None, f"stdin line {number} has no message content"
    return "\n".join(pieces), None


def pick(cfg, prior):
    """This run's scenario: cfg["run"], or the step a sequence gives the n-th run (the last step repeats)."""
    scenario = cfg.get("run")
    if not scenario:
        return {}
    if "sequence" in scenario:
        steps = scenario["sequence"]
        return steps[min(prior, len(steps) - 1)] if steps else {}
    return scenario


def prior_runs(root):
    return sum(1 for call in read_calls(root) if call.get("kind") == "run")


def change_files(scenario, mode, settings, accept_edits=False):
    """The model's file changes, as the real CLI treats them in headless mode (measured on 1.3.5): under strict every change is soft-denied;
    under request-review a change is soft-denied too unless a permissions.allow rule `write_file(<path>)` covers it; only a wide preset
    (always-proceed) lets everything through. A denial is one notice on stderr, and the run goes on."""
    writes = scenario.get("writes", {})
    deletes = scenario.get("deletes", [])
    denied = False
    for rel, text in writes.items():
        if not may_write(rel, mode, settings, accept_edits):
            denied = True
            continue
        path = inside(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for rel in deletes:
        if not may_write(rel, mode, settings, accept_edits):
            denied = True
            continue
        inside(rel).unlink()
    if denied:
        print(WRITE_NOTICE, file=sys.stderr, flush=True)


def may_write(rel, mode, settings, accept_edits=False):
    """Whether the permission engine lets a write to `rel` (relative to the working directory) through."""
    if mode == "always-proceed":
        return True
    if mode != "request-review":
        return False
    if accept_edits:                                          # --mode accept-edits: file edits are approved without a rule
        return True
    allow = ((settings or {}).get("permissions") or {}).get("allow") if isinstance(settings, dict) else None
    full = os.path.join(os.getcwd(), rel)
    for rule in allow if isinstance(allow, list) else []:
        if not (isinstance(rule, str) and rule.startswith("write_file(") and rule.endswith(")")):
            continue                                                         # only write rules are understood here
        target = rule[len("write_file("):-1]
        covered = target if target.startswith("/") else os.path.join(os.getcwd(), target)
        if full == covered or (target.endswith("/") and full.startswith(covered)):
            return True
    return False


def inside(rel):
    """`rel` under the working directory. A path that would leave it is refused: the fake touches nothing else."""
    base = os.getcwd()
    path = os.path.normpath(os.path.join(base, rel))
    if os.path.isabs(rel) or not path.startswith(base + os.sep):
        raise SystemExit(f"fake agy: refusing to touch {rel!r}: it is outside the working directory")
    return Path(path)


def install_sigint(mode):
    if mode == "graceful":
        signal.signal(signal.SIGINT, interrupted)
    elif mode == "ignore":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    elif mode == "die":
        signal.signal(signal.SIGINT, lambda *_: os._exit(130))
    else:
        raise SystemExit(f"fake agy: unknown sigint mode {mode!r}")


def interrupted(*_):
    """SIGINT: one INTERRUPTED result line, written straight to the descriptor (safe inside a handler), then exit 130."""
    os.write(1, (json.dumps(result_event("INTERRUPTED", "")) + "\n").encode("utf-8"))
    os._exit(130)


def tool_permission(settings):
    """The permission mode the CLI runs with: settings.json's toolPermission, or the default when it cannot be read."""
    value = settings.get("toolPermission") if isinstance(settings, dict) else None
    return value if isinstance(value, str) else DEFAULT_MODE


def default_events():
    """A scenario that a test does not give: init, the user's step, an answer in two parts, a checkpoint, a SUCCESS result."""
    return [
        {"$init": True},
        step(0, "DONE", "user_input"),
        step(1, "ACTIVE", "agent_response", text_delta="All "),
        step(1, "DONE", "agent_response", text_delta="done.\n", duration_seconds=0.5, usage=USAGE),
        step(2, "DONE", "checkpoint"),
        result_event("SUCCESS", "All done.\n", duration=0.5, turns=1, usage=USAGE),
    ]


def init_event(mode, model):
    return {"event": "init", "conversation_id": CONV,
            "init": {"cwd": os.getcwd(), "tools": list(TOOLS), "permission_mode": mode, "model": model}}


def step(index, state, step_type, **extra):
    body = {"conversation_id": CONV, "step_index": index, "state": state, "step_type": step_type}
    body.update(extra)
    return {"event": "step_update", "step_update": body}


def result_event(status, response, *, error=None, duration=0, turns=0, usage=ZERO_USAGE):
    body = {"conversation_id": CONV, "status": status, "response": response}
    if error is not None:
        body["error"] = error
    body.update(duration_seconds=duration, num_turns=turns, usage=usage)
    return {"event": "result", "result": body}


def emit(event):
    print(json.dumps(event), flush=True)


def load(root):
    path = root / "fake.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def read_calls(root):
    log = root / "calls.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def log_call(root, record):
    """Appends the call once. A run is logged as soon as its standard input has been read, before its scenario is played."""
    with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({k: record[k] for k in LOG_KEYS}) + "\n")
    record["logged"] = True


def home_settings(home):
    """$HOME/.gemini/antigravity-cli/settings.json, parsed; None when it is missing or invalid."""
    if not home:
        return None
    try:
        with open(os.path.join(home, ".gemini", "antigravity-cli", "settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def keychains_link(home):
    """Where $HOME/Library/Keychains points. Only read with readlink: the target itself is never opened."""
    if not home:
        return None
    path = os.path.join(home, "Library", "Keychains")
    return os.readlink(path) if os.path.islink(path) else None
