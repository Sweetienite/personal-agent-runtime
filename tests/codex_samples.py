"""Event streams shaped like `codex exec --json` output.

SYNTHETIC: the shapes follow the open-source codex-rs/exec/src/exec_events.rs and the error messages follow
codex-rs/core/src/error.rs for the version recorded in docs/CODEX.md. They are not captured from a real run; real captures
(redacted) are added next to them once a real call has been approved and made, and the parser is checked against both.
"""
from datetime import datetime, timedelta

USAGE = {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 90, "reasoning_output_tokens": 30}


def started():
    return [{"type": "thread.started", "thread_id": "0199-fake-thread"}, {"type": "turn.started"}]


def message(text):
    return {"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": text}}


def command(cmd="python3 -m unittest -q", exit_code=0, status="completed"):
    return {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": cmd,
                                              "aggregated_output": "OK", "exit_code": exit_code, "status": status}}


def file_change(*paths):
    return {"type": "item.completed", "item": {"id": "item_2", "type": "file_change", "status": "completed",
                                              "changes": [{"path": p, "kind": "update"} for p in paths]}}


def completed(usage=None):
    return {"type": "turn.completed", "usage": USAGE if usage is None else usage}


def ok_run(text="All done.", *middle):
    return started() + list(middle) + [message(text), completed()]


def failed_run(text):
    """The CLI reports a fatal error, then the failed turn."""
    return started() + [{"type": "error", "message": text}, {"type": "turn.failed", "error": {"message": text}}]


def reset_in(hours):
    """The wording of an official usage-limit message with a reset time `hours` from now (local time, as the CLI prints it)."""
    when = (datetime.now() + timedelta(hours=hours)).astimezone().strftime("%-I:%M %p")
    return ("You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
            f"https://chatgpt.com/codex/settings/usage to purchase more credits or try again at {when}.")


RATE_LIMIT = "exceeded retry limit, last status: 429 Too Many Requests"
AUTH = "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header"
MODEL = "The model `gpt-nope` does not exist or you do not have access to it."
TRANSIENT = "stream disconnected before completion: error sending request for url"
