"""Event streams shaped like `agy` stream-json output (the lines the Antigravity adapter reads).

SYNTHETIC, except where a comment says REAL: the shapes follow the official headless-mode documentation, and the REAL ones are what
agy 1.3.5 printed in the approved write smoke (the soft-denied command). The quota and rate-limit wordings are guesses, because the
vendor does not document them. Real captures (redacted) are in tests/data/agy_real/.
"""

CONV = "conv-fake-1"
TOOLS = ["ask_permission", "run_command", "write_to_file", "view_file", "list_dir"]
USAGE = {"input_tokens": 1200, "output_tokens": 90, "thinking_tokens": 30, "cache_read_tokens": 800, "total_tokens": 1290}
USAGE_NORMALIZED = {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 90, "reasoning_output_tokens": 30}
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0}

AUTH = "Error: authentication required. Run agy interactively to sign in."
KEYRING = "Error: failed to retrieve token: secret keyring is locked"
UNKNOWN_MODEL = ('invalid model selection (--model "{model}" --effort ""): model {model} is not recognized as a known model '
                 'or custom model in settings\nAvailable models:\nGemini 3.8 Flash (Medium)')     # use .format(model=...)
RATE = "429 Too Many Requests: rate limit exceeded"
QUOTA = "You have exhausted your weekly limit for this model; it resets in 3 days"
TRANSIENT = "503 Service Unavailable: the service is temporarily unavailable"


# REAL (1.3.5): what headless mode prints on stderr when a tool needs a permission nobody can give ...
DENIED_NOTICE = ('jetski: no output produced \u2014 a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied. '
                 'Add an allow-rule under permissions.allow in settings.json (e.g. command(<target>)). Alternatively, re-run with '
                 '--dangerously-skip-permissions to auto-approve all tools.')
# ... and what the final `result` event lists under `denied_actions`
DENIED_COMMAND = {"action": "command", "display_name": "RunCommand"}


def init(mode="strict", model="gemini-3.8-flash-medium", tools=None):
    return {"event": "init", "conversation_id": CONV,
            "init": {"cwd": "/work/project", "tools": list(TOOLS if tools is None else tools),
                     "permission_mode": mode, "model": model}}


def _step(index, state, step_type, **extra):
    return {"event": "step_update", "step_update": {"conversation_id": CONV, "step_index": index, "state": state,
                                                    "step_type": step_type, **extra}}


def user_step():
    return _step(0, "DONE", "user_input")


def delta(text, state="ACTIVE"):
    extra = {"duration_seconds": 0.5, "usage": USAGE} if state == "DONE" else {}
    return _step(1, state, "agent_response", text_delta=text, **extra)


def tool(name="run_command", state="DONE", params=None, output="", error=None):
    info = {"name": name, "parameters": {} if params is None else params, "output": output}
    if error is not None:
        info["error"] = error
    return _step(1, state, "tool", tool_name=name, tool_info=info)


def denied_command(command="python3 -m unittest -q", index=2):
    """REAL shape (1.3.5): the two tool steps of a command that headless mode auto-denied - the second one has state ERROR."""
    info = {"name": "run_command", "parameters": {"CommandLine": command}}
    error = {"type": "TOOL_ERROR", "message": (f'permission check failed for command "{command}": user denied permission to run command:\n{command}\n'
                                              "Do not attempt to circumvent this denial by rephrasing the command, using alternative tools/scripts "
                                              "(e.g. python, sh, curl), or accessing the same target resource. Proceed without performing this action.")}
    return [_step(index, "ACTIVE", "tool", tool_name="run_command", tool_info=dict(info)),
            _step(index, "ERROR", "tool", tool_name="run_command", duration_seconds=0.25, tool_info={**info, "error": error})]


def checkpoint():
    return _step(2, "DONE", "checkpoint")


def result(response="All done.\n", status="SUCCESS", error=None, usage=None, denied=None):
    ok = status == "SUCCESS"
    if usage is None:
        usage = USAGE if ok else ZERO_USAGE
    body = {"conversation_id": CONV, "status": status, "response": response}
    if error is not None:
        body["error"] = error
    body.update(duration_seconds=0.5 if ok else 0, num_turns=1 if ok else 0, usage=usage)
    if denied:                                                       # REAL (1.3.5): the result itself lists what headless mode auto-denied
        body["denied_actions"] = list(denied)
    return {"event": "result", "result": body}


def ok_run(text="All done.\n", *middle, mode="strict"):
    """init, the user's step, the given steps (for example tool()), the answer, a checkpoint and a SUCCESS result."""
    steps = [user_step(), *middle, delta(text, "DONE"), checkpoint()]
    for index, event in enumerate(steps):
        if "step_update" in event:
            event["step_update"]["step_index"] = index       # every step of the run gets its own index, in order
    return [init(mode=mode), *steps, result(text)]


def failed_run(error, status="ERROR", mode="strict"):
    """init, then a failed result that carries the error."""
    return [init(mode=mode), result(response="", status=status, error=error)]
