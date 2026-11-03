"""The Antigravity adapter's own behaviour, without a Kernel and without any program: how it reads `agy`'s stream-json, what it
classifies, what it puts in argv / standard input / environment / the private HOME, and which projects it refuses.

SYNTHETIC input: the shapes follow the official headless-mode documentation (see docs/AGY.md); real captures are tested in
test_agy_real_samples.py once a real run has been approved and made."""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from par.providers import antigravity as A
from par.providers.base import FailureClass, ProviderEvent, ProviderStatus, RunOutcome, RunRequest

SK = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


def line(obj) -> str:
    return json.dumps(obj)


def init(mode="strict", model=None, tools=("ask_permission", "run_command", "write_to_file", "...")):
    body = {"cwd": "/w", "tools": list(tools), "permission_mode": mode}
    if model:
        body["model"] = model
    return {"event": "init", "conversation_id": "c-1", "init": body}


def step(**kw):
    return {"event": "step_update", "step_update": {"conversation_id": "c-1", "step_index": 1, **kw}}


def result(status="SUCCESS", response="done\n", **kw):
    return {"event": "result", "result": {"conversation_id": "c-1", "status": status, "response": response, "duration_seconds": 1.0,
                                          "num_turns": 1, "usage": {"input_tokens": 10, "output_tokens": 4, "thinking_tokens": 2,
                                                                    "cache_read_tokens": 6, "total_tokens": 14}, **kw}}


def adapter(**kw):
    return A.AntigravityAdapter("/x/agy", verify_signature=False, environ=kw.pop("environ", {}), **kw)


def parse_all(ad, *objs):
    return [ad.parse_event(line(o)) for o in objs]


class ParseEvents(unittest.TestCase):
    def setUp(self):
        self.ad = adapter()

    def test_init_becomes_a_session_event_with_what_the_cli_says_about_itself(self):
        ev = self.ad.parse_event(line(init("strict", model="gemini-3.8-flash-medium")))
        self.assertEqual(ev.kind, "session")
        self.assertEqual(dict(ev.data), {"conversation_id": "c-1", "permission_mode": "strict", "tools": 3, "model": "gemini-3.8-flash-medium"})

    def test_a_wider_permission_mode_than_asked_is_a_warning_that_still_carries_the_model(self):
        ad = adapter()
        ad._wanted = A.STRICT
        for mode in ("request-review", "proceed-in-sandbox", "always-proceed", "something-new", None):
            ev = ad.parse_event(line({"event": "init", "init": {"permission_mode": mode, "model": "m-1"}}))
            self.assertEqual(ev.kind, "warning", mode)
            self.assertIn("wider than", ev.data["message"])
            self.assertEqual(ev.data["model"], "m-1")

    def test_a_write_task_accepts_request_review_and_strict(self):
        ad = adapter()
        ad._wanted = A.REVIEW
        self.assertEqual(ad.parse_event(line(init("request-review"))).kind, "session")
        self.assertEqual(ad.parse_event(line(init("strict"))).kind, "session")
        self.assertEqual(ad.parse_event(line(init("always-proceed"))).kind, "warning")

    def test_user_input_and_checkpoint_steps_are_plain_other_events(self):
        for stype in ("user_input", "checkpoint", "something_new"):
            ev = self.ad.parse_event(line(step(state="DONE", step_type=stype)))
            self.assertEqual((ev.kind, ev.data["type"]), ("other", stype))

    def test_partial_text_is_dropped_and_the_final_fragment_is_a_message(self):
        self.assertIsNone(self.ad.parse_event(line(step(state="ACTIVE", step_type="agent_response", text_delta="Hel"))))
        ev = self.ad.parse_event(line(step(state="DONE", step_type="agent_response", text_delta="lo\n")))
        self.assertEqual((ev.kind, ev.data["text"]), ("message", "lo\n"))
        self.assertIsNone(self.ad.parse_event(line(step(state="DONE", step_type="agent_response", text_delta="  \n"))))
        self.assertIsNone(self.ad.parse_event(line(step(state="DONE", step_type="agent_response"))))

    def test_a_command_step_keeps_the_command_but_never_its_output(self):
        ev = self.ad.parse_event(line(step(state="DONE", step_type="tool", tool_name="run_command",
                                           tool_info={"name": "run_command", "parameters": {"CommandLine": "echo hi"}, "output": f"secret {SK}\n"})))
        self.assertEqual(ev.kind, "tool")
        self.assertEqual((ev.data["tool"], ev.data["command"], ev.data["phase"]), ("run_command", "echo hi", "DONE"))
        self.assertNotIn("output", ev.data)
        self.assertNotIn(SK, json.dumps(dict(ev.data)))

    def test_a_file_tool_keeps_the_paths_it_was_given(self):
        ev = self.ad.parse_event(line(step(state="DONE", step_type="tool", tool_name="write_to_file",
                                           tool_info={"name": "write_to_file", "parameters": {"TargetFile": "/w/a.txt", "CodeContent": "x" * 5000}})))
        self.assertEqual(ev.data["paths"], ["/w/a.txt"])
        self.assertNotIn("CodeContent", json.dumps(dict(ev.data)))

    def test_a_tool_the_cli_refused_is_a_warning_naming_it(self):
        ev = self.ad.parse_event(line(step(state="DONE", step_type="tool", tool_name="run_command",
                                           tool_info={"name": "run_command", "parameters": {"CommandLine": "rm -rf x"},
                                                      "error": {"type": "PERMISSION_DENIED", "message": "needs approval"}})))
        self.assertEqual(ev.kind, "warning")
        self.assertIn("run_command", ev.data["message"])

    def test_a_tool_that_failed_for_another_reason_is_still_a_tool_event_with_the_error_type(self):
        ev = self.ad.parse_event(line(step(state="DONE", step_type="tool", tool_name="read_file",
                                           tool_info={"name": "read_file", "error": {"type": "NOT_FOUND", "message": "no such file"}})))
        self.assertEqual((ev.kind, ev.data["status"], ev.data["error"]), ("tool", "error", "NOT_FOUND"))

    def test_web_browser_and_mcp_tools_carry_a_note(self):
        for name in ("read_url_content", "search_web", "browser_click", "mcp_tool"):
            ev = self.ad.parse_event(line(step(state="DONE", step_type="tool", tool_name=name, tool_info={"name": name})))
            self.assertIn("note", ev.data, name)
        ev = self.ad.parse_event(line(step(state="DONE", step_type="tool", tool_name="grep_search", tool_info={"name": "grep_search"})))
        self.assertNotIn("note", ev.data)

    def test_a_sub_agent_is_a_warning(self):
        ev = self.ad.parse_event(line(step(state="DONE", step_type="tool", tool_name="invoke_subagent",
                                           subagent_info={"subagents": [{"type_name": "x", "role": "y"}]})))
        self.assertEqual(ev.kind, "warning")
        self.assertIn("sub-agent", ev.data["message"])

    def test_the_result_is_the_terminal_event_with_normalised_usage_and_the_answer(self):
        ev = self.ad.parse_event(line(result()))
        self.assertEqual(ev.kind, "terminal")
        self.assertEqual((ev.data["status"], ev.data["vendor_status"], ev.data["text"]), ("SUCCESS", "SUCCESS", "done\n"))
        self.assertEqual(ev.data["usage"], {"input_tokens": 10, "output_tokens": 4, "cached_input_tokens": 6, "reasoning_output_tokens": 2})
        self.assertEqual((ev.data["num_turns"], ev.data["duration_seconds"]), (1, 1.0))
        self.assertNotIn("message", ev.data)

    def test_every_vendor_status_maps_to_a_kernel_terminal_state(self):
        expect = {"SUCCESS": "SUCCESS", "ERROR": "ERROR", "INVALID": "ERROR", "RUNNING": "ERROR", "WAITING": "ERROR",
                  "CANCELED": "CANCELED", "INTERRUPTED": "CANCELED", "NEVER_HEARD_OF_IT": "ERROR", "": "ERROR"}
        for vendor, status in expect.items():
            ev = self.ad.parse_event(line(result(vendor)))
            self.assertEqual(ev.data["status"], status, vendor)
        self.assertIn("waiting for input", self.ad.parse_event(line(result("WAITING"))).data["message"])
        self.assertIn("INVALID", self.ad.parse_event(line(result("INVALID"))).data["message"])

    def test_an_error_result_carries_the_message_redacted_and_clipped(self):
        ev = self.ad.parse_event(line(result("ERROR", response="", error=f"boom {SK} " + "x" * 9000)))
        self.assertNotIn(SK, ev.data["message"])
        self.assertLessEqual(len(ev.data["message"]), A.MAX_TEXT)

    def test_bad_usage_values_are_dropped(self):
        ev = self.ad.parse_event(line({"event": "result", "result": {"status": "SUCCESS", "usage": {"input_tokens": -1, "output_tokens": True,
                                                                                                    "cache_read_tokens": "3", "thinking_tokens": 5}}}))
        self.assertEqual(ev.data["usage"], {"reasoning_output_tokens": 5})

    def test_the_answer_is_clipped_and_redacted(self):
        ev = self.ad.parse_event(line(result(response=f"key {SK}\n" + "y" * 9000)))
        self.assertNotIn(SK, ev.data["text"])
        self.assertLessEqual(len(ev.data["text"]), A.MAX_TEXT)

    def test_unknown_event_names_are_tolerated_not_trusted(self):
        ev = self.ad.parse_event(line({"event": "future_thing", "x": 1}))
        self.assertEqual((ev.kind, ev.data["type"]), ("other", "future_thing"))

    def test_garbage_is_unparsed_and_the_raw_text_is_never_kept(self):
        for text in ("not json at all " + SK, "[1, 2]", '{"no_event": 1}', '{"event": 5}', "null", '"str"'):
            ev = self.ad.parse_event(text)
            self.assertEqual(ev.kind, "unparsed", text)
            self.assertEqual(set(ev.data), {"length"})
            self.assertNotIn(SK, json.dumps(dict(ev.data)))
        self.assertIsNone(self.ad.parse_event(""))
        self.assertIsNone(self.ad.parse_event("   \n"))

    def test_malformed_payloads_do_not_raise(self):
        weird = [{"event": "init", "init": "x"}, {"event": "init", "init": {"tools": "nope", "permission_mode": 5}},
                 {"event": "step_update", "step_update": "x"}, {"event": "step_update", "step_update": {"step_type": 7, "state": 8}},
                 {"event": "step_update", "step_update": {"step_type": "tool", "tool_info": "x", "tool_name": 3}},
                 {"event": "step_update", "step_update": {"step_type": "tool", "tool_info": {"parameters": "p", "error": "e"}}},
                 {"event": "result", "result": "x"}, {"event": "result", "result": {"status": 5, "usage": "u", "response": 7}}]
        for obj in weird:
            self.assertIsInstance(self.ad.parse_event(line(obj)), ProviderEvent, obj)

    def test_init_teaches_the_adapter_the_tool_names(self):
        self.ad.parse_event(line(init(tools=("run_command", "write_to_file", "..."))))
        self.assertEqual(self.ad._tools, frozenset({"run_command", "write_to_file"}))


class RealSoftDenial(unittest.TestCase):
    """What agy 1.3.5 really did when a write task's model tried to run the tests (approved write smoke, 2026-10-15)."""

    def setUp(self):
        from tests import agy_samples as S
        self.S = S
        self.ad = adapter()
        self.ad._wanted = A.REVIEW
        self.ad.parse_event(line(S.init("request-review", tools=("run_command", "view_file", "write_to_file"))))

    def test_the_result_lists_what_was_denied_and_carries_it_as_its_own_warning(self):
        ev = self.ad.parse_event(line(self.S.result("", denied=[self.S.DENIED_COMMAND])))
        self.assertEqual(ev.kind, "terminal")
        self.assertEqual((ev.data["status"], ev.data["text"]), ("SUCCESS", ""))                   # the vendor calls it a success; it produced nothing
        self.assertEqual(ev.data["denied_actions"], [{"action": "command", "display_name": "RunCommand"}])
        self.assertEqual(ev.data["warnings"], [A.soft_denial_message("command")])

    def test_a_result_without_denials_carries_neither_field(self):
        ev = self.ad.parse_event(line(self.S.result("fine\n")))
        self.assertNotIn("denied_actions", ev.data)
        self.assertNotIn("warnings", ev.data)

    def test_malformed_denied_actions_are_tolerated(self):
        for bad in ("x", 5, [None, "x", 3], {"action": "command"}):
            ev = self.ad.parse_event(line({"event": "result", "result": {"status": "SUCCESS", "denied_actions": bad}}))
            self.assertEqual(ev.kind, "terminal")
            self.assertNotIn("warnings", ev.data, bad)

    def test_the_real_stderr_notice_names_the_permission_not_a_guess(self):
        ev = self.ad.parse_stderr(self.S.DENIED_NOTICE)
        self.assertEqual((ev.kind, ev.data["message"]), ("warning", A.soft_denial_message("command")))
        self.assertNotIn("--dangerously", ev.data["message"])                                       # the vendor's own advice is never repeated, never followed

    def test_stderr_result_and_tool_step_agree_on_one_wording_so_the_note_appears_once(self):
        from par.execution import results as R
        events = [self.ad.parse_stderr(self.S.DENIED_NOTICE)] + [self.ad.parse_event(line(o)) for o in (*self.S.denied_command(), self.S.result("", denied=[self.S.DENIED_COMMAND]))]
        notes = R.worker_warnings(events)
        self.assertEqual(notes, [A.soft_denial_message("command"), "a tool call was refused: run_command (python3 -m unittest -q)"])

    def test_a_denied_command_step_has_state_error_and_becomes_a_clean_one_line_warning(self):
        active, errored = [self.ad.parse_event(line(o)) for o in self.S.denied_command("python3 -m unittest -q")]
        self.assertEqual((active.kind, active.data["phase"], active.data["command"]), ("tool", "ACTIVE", "python3 -m unittest -q"))
        self.assertEqual(errored.kind, "warning")
        self.assertNotIn("\n", errored.data["message"])
        self.assertIn("python3 -m unittest -q", errored.data["message"])

    def test_the_run_is_a_clean_provider_success_and_the_kernel_judges_it(self):
        outcome = RunOutcome(0, [self.ad.parse_event(line(o)) for o in (self.S.init("request-review"), self.S.user_step(), *self.S.denied_command(), self.S.result("", denied=[self.S.DENIED_COMMAND]))])
        self.assertIsNone(self.ad.classify_failure(outcome))                                         # no failure class: soft denial is a warning, not a failure

    def test_the_environment_note_tells_a_writing_worker_that_commands_do_not_exist_and_that_this_is_not_a_block(self):
        note = self.ad.prompt_note(allow_write=True)
        for phrase in ("Shell commands are not available", "test command", "not a block", "written summary"):
            self.assertIn(phrase, note)

    def test_the_environment_note_tells_a_read_only_worker_to_always_answer_in_writing(self):
        note = self.ad.prompt_note(allow_write=False)
        for phrase in ("read-only environment", "always finish with a written answer"):
            self.assertIn(phrase, note)
        self.assertNotEqual(note, self.ad.prompt_note(allow_write=True))


class ParseStderr(unittest.TestCase):
    def setUp(self):
        self.ad = adapter()
        self.ad.parse_event(line(init()))

    def test_a_denial_notice_names_the_tool_from_the_list_the_cli_reported(self):
        ev = self.ad.parse_stderr("warning: write_to_file was denied; allow it with write_file(src/) in permissions.allow")
        self.assertEqual(ev.kind, "warning")
        self.assertIn("write_to_file", ev.data["message"])

    def test_the_longest_matching_tool_name_wins(self):
        ad = adapter()
        ad.parse_event(line(init(tools=("run", "run_command"))))
        self.assertIn("run_command", ad.parse_stderr("run_command denied").data["message"])

    def test_unrelated_lines_are_ignored(self):
        for text in ("", "   ", "starting up", "warning: ignoring unsupported stream input message event \"x\"", "model gemini ready"):
            self.assertIsNone(self.ad.parse_stderr(text), text)

    def test_a_notice_without_a_known_tool_is_still_surfaced_but_without_the_line(self):
        ev = self.ad.parse_stderr(f"something needs approval {SK} for an unlisted tool")
        self.assertEqual(ev.kind, "warning")
        self.assertNotIn(SK, ev.data["message"])
        self.assertNotIn("unlisted", ev.data["message"])

    def test_before_init_the_first_identifier_is_taken(self):
        ad = adapter()
        self.assertIn("run_command", ad.parse_stderr("tool run_command was denied").data["message"])

    def test_the_raw_line_is_never_in_the_event(self):
        ev = self.ad.parse_stderr(f"run_command denied: token {SK} in the path")
        self.assertNotIn(SK, json.dumps(dict(ev.data)))
        self.assertNotIn("token", ev.data["message"])


class ClassifyText(unittest.TestCase):
    def test_the_vendors_documented_messages(self):
        C = A.classify_text
        self.assertIs(C("Error: authentication required. Run agy interactively."), FailureClass.AUTH_REQUIRED)
        self.assertIs(C("Error: failed to retrieve token: secret keyring is locked"), FailureClass.AUTH_REQUIRED)
        self.assertIs(C('invalid model selection (--model "x" --effort ""): model x is not recognized as a known model or custom model in settings'),
                      FailureClass.MODEL_UNAVAILABLE)

    def test_guessed_wordings_for_what_the_vendor_does_not_document(self):
        C = A.classify_text
        self.assertIs(C("You have exhausted your weekly limit for this model"), FailureClass.QUOTA_EXHAUSTED)
        self.assertIs(C("Quota exceeded for this plan"), FailureClass.QUOTA_EXHAUSTED)
        self.assertIs(C("429 Too Many Requests"), FailureClass.RATE_LIMITED)
        self.assertIs(C("RESOURCE_EXHAUSTED: rate limit"), FailureClass.RATE_LIMITED)
        self.assertIs(C("503 Service Unavailable: the service is temporarily unavailable"), FailureClass.TRANSIENT_ERROR)
        self.assertIs(C("unexpected EOF"), FailureClass.TRANSIENT_ERROR)
        real = 'API error (attempt 1): request failed: Post "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse": EOF'
        self.assertIs(C(real), FailureClass.TRANSIENT_ERROR)                                    # REAL (write smoke 3): a dropped connection after a good edit
        self.assertIs(C("whereof thereof"), FailureClass.EXECUTION_FAILED)                      # "eof" counts only as a word

    def test_a_plain_rate_limit_is_not_mistaken_for_an_exhausted_quota(self):
        C = A.classify_text
        self.assertIs(C("429 Too Many Requests: rate limit exceeded"), FailureClass.RATE_LIMITED)
        self.assertIs(C("Rate limit exceeded for model gemini-3.8-flash-medium"), FailureClass.RATE_LIMITED)
        self.assertIs(C("limit exceeded"), FailureClass.EXECUTION_FAILED)                           # too ambiguous to guess either way
        self.assertIs(C("weekly limit reached"), FailureClass.QUOTA_EXHAUSTED)

    def test_the_order_is_login_then_model_then_quota_then_rate(self):
        C = A.classify_text
        self.assertIs(C("429 quota exceeded"), FailureClass.QUOTA_EXHAUSTED)                   # a bare 429 only means rate when no quota wording
        self.assertIs(C("429 Too Many Requests; authentication required"), FailureClass.AUTH_REQUIRED)
        self.assertIs(C("model foo is not available, quota"), FailureClass.MODEL_UNAVAILABLE)

    def test_anything_unrecognised_is_a_plain_failure_never_a_guess(self):
        for text in ("", "segmentation fault", "the agent got confused", "exit status 3"):
            self.assertIs(A.classify_text(text), FailureClass.EXECUTION_FAILED, text)

    def test_matching_is_case_and_apostrophe_insensitive(self):
        self.assertIs(A.classify_text("AUTHENTICATION REQUIRED"), FailureClass.AUTH_REQUIRED)
        self.assertIs(A.classify_text("you’ve hit your usage limit"), FailureClass.QUOTA_EXHAUSTED)


def events_of(ad, *objs):
    return [e for e in parse_all(ad, *objs) if e is not None]


class Classify(unittest.TestCase):
    def setUp(self):
        self.ad = adapter()
        self.ad._wanted = A.STRICT

    def outcome(self, objs, exit_code=0, **kw):
        return RunOutcome(exit_code, events_of(self.ad, *objs), kw.pop("timed_out", False), kw.pop("signal", None), kw.pop("stderr_tail", ""))

    def test_a_clean_run_is_a_provider_level_success(self):
        self.assertIsNone(self.ad.classify_failure(self.outcome([init(), result()])))

    def test_a_soft_denial_is_a_warning_not_a_failure(self):
        out = self.outcome([init(), step(state="DONE", step_type="tool", tool_name="run_command",
                                         tool_info={"error": {"type": "DENIED", "message": "needs approval"}}), result()])
        self.assertTrue(any(e.kind == "warning" for e in out.events))
        self.assertIsNone(self.ad.classify_failure(out))

    def test_a_wider_permission_mode_than_asked_fails_the_run_however_clean_it_looks(self):
        self.assertIs(self.ad.classify_failure(self.outcome([init("always-proceed"), result()])), FailureClass.EXECUTION_FAILED)
        self.assertIs(self.ad.classify_failure(self.outcome([init("request-review"), result()])), FailureClass.EXECUTION_FAILED)
        ad = adapter()
        ad._wanted = A.REVIEW
        self.assertIsNone(ad.classify_failure(RunOutcome(0, events_of(ad, init("request-review"), result()))))

    def test_a_success_without_an_init_event_proves_nothing_and_fails(self):
        self.assertIs(self.ad.classify_failure(self.outcome([result()])), FailureClass.EXECUTION_FAILED)

    def test_error_results_are_classified_by_their_message(self):
        cases = {"authentication required": FailureClass.AUTH_REQUIRED, "model x is not recognized as a known model": FailureClass.MODEL_UNAVAILABLE,
                 "weekly limit reached": FailureClass.QUOTA_EXHAUSTED, "429": FailureClass.RATE_LIMITED,
                 "service unavailable": FailureClass.TRANSIENT_ERROR, "weird": FailureClass.EXECUTION_FAILED}
        for message, expect in cases.items():
            out = self.outcome([init(), result("ERROR", response="", error=message)], exit_code=1)
            self.assertIs(self.ad.classify_failure(out), expect, message)

    def test_waiting_on_input_needs_a_person(self):
        self.assertIs(self.ad.classify_failure(self.outcome([init(), result("WAITING", response="")], exit_code=0)), FailureClass.PERMISSION_BLOCKED)

    def test_a_run_the_vendor_calls_canceled_or_interrupted_is_unknown_not_failed(self):
        for status in ("CANCELED", "INTERRUPTED"):
            self.assertIs(self.ad.classify_failure(self.outcome([init(), result(status, response="")], exit_code=130)), FailureClass.UNKNOWN)

    def test_success_events_with_a_failing_exit_code_do_not_pass(self):
        out = self.outcome([init(), result()], exit_code=1, stderr_tail="authentication required")
        self.assertIs(self.ad.classify_failure(out), FailureClass.AUTH_REQUIRED)
        self.assertIs(self.ad.classify_failure(self.outcome([init(), result()], exit_code=2, stderr_tail="")), FailureClass.EXECUTION_FAILED)

    def test_no_result_event_and_a_clean_exit_is_unknown_never_success(self):
        self.assertIs(self.ad.classify_failure(self.outcome([init()], exit_code=0)), FailureClass.UNKNOWN)
        self.assertIs(self.ad.classify_failure(self.outcome([], exit_code=0)), FailureClass.UNKNOWN)

    def test_no_result_and_a_failing_exit_uses_what_the_cli_said_on_stderr(self):
        for said, expect in (("Error: authentication required.", FailureClass.AUTH_REQUIRED),
                             ("Error: failed to retrieve token: secret keyring is locked", FailureClass.AUTH_REQUIRED),
                             ("boom", FailureClass.EXECUTION_FAILED)):
            self.assertIs(self.ad.classify_failure(self.outcome([], exit_code=1, stderr_tail=said)), expect, said)

    def test_a_killed_or_timed_out_process_is_unknown(self):
        self.assertIs(self.ad.classify_failure(self.outcome([init()], exit_code=None, signal=signal.SIGKILL)), FailureClass.UNKNOWN)
        self.assertIs(self.ad.classify_failure(self.outcome([init()], exit_code=None, timed_out=True)), FailureClass.UNKNOWN)

    def test_a_terminal_event_before_a_crash_exit_with_no_message_is_still_not_success(self):
        out = self.outcome([init(), result()], exit_code=None, signal=signal.SIGSEGV)
        self.assertIsNotNone(self.ad.classify_failure(out))


class BuildersAndEnvironment(unittest.TestCase):
    def request(self, **kw):
        return RunRequest("t-1", 1, kw.pop("prompt", "do it"), Path("/w"), kw.pop("model", "gemini-3.8-flash-medium"), **kw)

    def test_the_argv_is_exact_and_carries_no_prompt_and_no_permission_override(self):
        argv = adapter().build_argv(self.request(prompt="SECRET PROMPT"))
        self.assertEqual(argv, ["/x/agy", "--input-format", "stream-json", "--output-format", "stream-json", "--model", "gemini-3.8-flash-medium",
                                "--sandbox", "--print-timeout", "24h"])
        self.assertNotIn("-p", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertFalse(any("SECRET" in a for a in argv))
        for allow in (False, True):
            self.assertEqual(adapter().build_argv(self.request(allow_write=allow)), argv)                  # the mode lives in settings.json, not argv

    def test_a_bad_model_slug_is_refused_before_it_reaches_argv(self):
        with tempfile.TemporaryDirectory(prefix="par-agy-slug-") as d:
            exe = Path(d) / "agy"
            exe.write_text("#!/bin/sh\nexit 0\n")
            os.chmod(exe, 0o755)
            ad = A.AntigravityAdapter(str(exe), verify_signature=False, environ={})
            for slug in ("", "-x", "a b", "a;b", "$(x)", "a\nb", "x" * 101):
                with self.assertRaises(ValueError, msg=slug):
                    ad.build_argv(self.request(model=slug))
                self.assertIn("not a usable model slug", ad.preflight(self.request(model=slug)) or "", slug)
            for slug in ("gemini-3.8-flash-medium", "claude-opus-5-5-high", "a", "m_1.2:x/y+z"):
                ad.build_argv(self.request(model=slug))                                                    # fine slugs pass

    def test_the_prompt_is_one_stream_json_user_event_on_standard_input(self):
        prompt = 'line one\nline "two" \\ back ünï 日本語   end'
        data = adapter().stdin_data(self.request(prompt=prompt))
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(data.count(b"\n"), 1)                                                         # exactly one JSON line
        self.assertEqual(json.loads(data), {"event": "user", "message": {"content": prompt}})

    def test_the_environment_holds_only_the_updater_switch_and_proxy_and_certificate_settings(self):
        leaks = {"GEMINI_API_KEY": SK, "GOOGLE_API_KEY": SK, "AGY_ADC_AUTH": "true", "GOOGLE_GEMINI_BASE_URL": "https://evil.example",
                 "GOOGLE_CLOUD_QUOTA_PROJECT": "p", "ANTHROPIC_API_KEY": SK, "OPENAI_API_KEY": SK, "HOME": "/Users/x", "SSH_CONNECTION": "1 2 3 4"}
        proxies = {"HTTPS_PROXY": "http://proxy:3128", "no_proxy": "localhost", "SSL_CERT_FILE": "/etc/ssl/cert.pem"}
        env = adapter(environ={**leaks, **proxies}).environment(self.request())
        self.assertEqual(env, {"AGY_CLI_DISABLE_AUTO_UPDATE": "true", **proxies})
        self.assertEqual(adapter(environ=leaks).environment(self.request()), {"AGY_CLI_DISABLE_AUTO_UPDATE": "true"})

    def test_a_prompt_that_is_too_large_is_refused(self):
        ad = A.AntigravityAdapter(sys.executable, verify_signature=False, environ={})
        self.assertIn("larger than", ad.preflight(self.request(prompt="x" * (A.MAX_PROMPT_BYTES + 1))))

    def test_cancel_sends_sigint_to_a_live_process_and_nothing_to_a_dead_one(self):
        # The child restores the DEFAULT action for SIGINT itself and says so: a process started from a shell background job inherits
        # "ignore" for SIGINT (and so would this child), which would make the test hang for reasons that have nothing to do with the adapter.
        code = "import signal, sys, time; signal.signal(signal.SIGINT, signal.SIG_DFL); print('ready', flush=True); time.sleep(60)"
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            adapter().request_cancel(proc)
            self.assertEqual(proc.wait(timeout=10), -signal.SIGINT)
            adapter().request_cancel(proc)                                                             # already gone: no error
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()

    def test_capabilities_are_set_only_from_evidence(self):
        caps = adapter().capabilities()
        self.assertEqual(caps.session_persistence_control.value, "unknown")
        self.assertEqual((caps.can_headless.value, caps.supports_subscription_auth.value), ("yes", "yes"))

    def test_the_persistence_report_never_names_a_credential_file_and_says_where_state_goes(self):
        report = adapter().persistence_report()
        self.assertIn("never opened", report["sessions"])
        self.assertIn("never opens", report["credentials"])
        self.assertEqual(report["paths"], ["<attempt>/home/.gemini"])


class PrivateHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="par-agy-home-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.keychains = self.base / "real_keychains"
        self.keychains.mkdir()
        (self.keychains / "login.keychain-db").write_text("do not touch")

    def settings(self):
        return json.loads((self.home / ".gemini" / "antigravity-cli" / "settings.json").read_text())

    def test_settings_are_written_privately_with_the_preset_that_matches_the_task(self):
        A.write_home(self.home, allow_write=False, keychains_from=self.keychains)
        for d in (self.home / ".gemini", self.home / ".gemini" / "antigravity-cli"):
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        f = self.home / ".gemini" / "antigravity-cli" / "settings.json"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        ro = self.settings()
        self.assertEqual(ro["toolPermission"], "strict")
        A.write_home(self.home, allow_write=True, keychains_from=self.keychains)                        # a second call rewrites, never appends
        rw = self.settings()
        self.assertEqual(rw["toolPermission"], "request-review")
        self.assertEqual({k: v for k, v in rw.items() if k != "toolPermission"}, {k: v for k, v in ro.items() if k != "toolPermission"})

    def test_the_fixed_settings_close_every_door_the_documentation_names(self):
        A.write_home(self.home, allow_write=True, keychains_from=self.keychains)
        s = self.settings()
        self.assertIs(s["allowNonWorkspaceAccess"], False)
        self.assertIs(s["enableTerminalSandbox"], True)
        self.assertIs(s["useG1Credits"], False)                                                        # no spending of personal AI credits
        self.assertIs(s["enableTelemetry"], False)
        self.assertNotIn("modelProvider", s)                                                           # API-key (metered) mode cannot be switched on
        self.assertNotIn("permissions", s)                                                             # no allow rules unless a write task is given its own
        self.assertNotEqual(s["toolPermission"], "always-proceed")

    def test_on_macos_the_keychain_directory_is_linked_not_copied(self):
        notes = A.write_home(self.home, allow_write=False, keychains_from=self.keychains)
        if sys.platform == "darwin":
            link = self.home / "Library" / "Keychains"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), str(self.keychains))
            self.assertEqual(notes, [])
            self.assertEqual(stat.S_IMODE((self.home / "Library").stat().st_mode), 0o700)
        else:
            self.assertFalse((self.home / "Library").exists())

    @unittest.skipUnless(sys.platform == "darwin", "the keychain link is a macOS matter")
    def test_a_missing_keychain_directory_is_a_note_not_a_crash(self):
        notes = A.write_home(self.home, allow_write=False, keychains_from=self.base / "nope")
        self.assertEqual(len(notes), 1)
        self.assertIn("will not find a saved sign-in", notes[0])
        self.assertFalse((self.home / "Library").exists())

    @unittest.skipUnless(sys.platform == "darwin", "the keychain link is a macOS matter")
    def test_deleting_the_private_home_never_reaches_what_the_link_points_to(self):
        A.write_home(self.home, allow_write=False, keychains_from=self.keychains)
        shutil.rmtree(self.home)                                                                       # what the Kernel's cleanup does
        self.assertEqual((self.keychains / "login.keychain-db").read_text(), "do not touch")

    def test_a_planted_symlink_in_place_of_the_settings_file_is_not_followed(self):
        target = self.base / "victim.txt"
        target.write_text("precious")
        cfg = self.home / ".gemini" / "antigravity-cli"
        cfg.mkdir(parents=True, mode=0o700)
        os.symlink(target, cfg / "settings.json")
        with self.assertRaises(OSError):
            A.write_home(self.home, allow_write=False, keychains_from=self.keychains)
        self.assertEqual(target.read_text(), "precious")

    def test_prepare_writes_only_inside_the_given_home_and_remembers_the_wanted_mode(self):
        ad = adapter(keychains_from=self.keychains)
        req = RunRequest("t", 1, "p", self.base / "ws", "m", allow_write=True)
        before = sorted(p.name for p in self.base.iterdir())
        ad.prepare(req, self.home, self.base / "tmp")
        self.assertEqual(ad._wanted, A.REVIEW)
        self.assertEqual(self.settings()["toolPermission"], "request-review")
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), before)                           # nothing new beside the home

    def test_a_private_home_can_never_be_the_real_home_and_nothing_is_touched_when_it_is_refused(self):
        fake_real = self.base / "pretend_real_home"
        fake_real.mkdir(mode=0o750)
        before = (stat.S_IMODE(fake_real.stat().st_mode), sorted(p.name for p in fake_real.iterdir()))
        with mock.patch.object(A, "real_home", return_value=fake_real):
            with self.assertRaises(ValueError):
                A.write_home(fake_real, allow_write=False, keychains_from=self.keychains)
            with self.assertRaises(ValueError):
                A.refuse_real_home(fake_real / ".." / "pretend_real_home")                                   # however the path is spelled
            ad = adapter(keychains_from=self.keychains)
            with self.assertRaises(ValueError):
                ad.prepare(RunRequest("t", 1, "p", self.base / "ws", "m"), fake_real, self.base / "tmp")
            A.refuse_real_home(self.home)                                                                   # any other directory is fine
        self.assertEqual((stat.S_IMODE(fake_real.stat().st_mode), sorted(p.name for p in fake_real.iterdir())), before)

    def test_a_login_plan_that_would_use_the_real_home_is_refused_before_anything_is_created_or_chmod_ed(self):
        with tempfile.TemporaryDirectory(prefix="par-agy-planreal-") as d:
            data = Path(os.path.realpath(d))
            os.chmod(data, 0o750)
            with mock.patch.object(A, "real_home", return_value=data / "agy_login_home"):                   # make the plan's home equal the "real" one
                with self.assertRaises(ValueError):
                    A.login_plan("/x/agy", data, {}, keychains_from=self.keychains)
            self.assertEqual(stat.S_IMODE(data.stat().st_mode), 0o750)
            self.assertEqual(sorted(p.name for p in data.iterdir()), [])

    @unittest.skipUnless(sys.platform == "darwin", "the keychain link is a macOS matter")
    def test_cleanup_removes_only_the_link_and_what_it_pointed_to_is_untouched(self):
        ad = adapter(keychains_from=self.keychains)
        ad.prepare(RunRequest("t", 1, "p", self.base / "ws", "m"), self.home, self.base / "tmp")
        self.assertTrue((self.home / "Library" / "Keychains").is_symlink())
        ad.cleanup(RunRequest("t", 1, "p", self.base / "ws", "m"), self.home, self.base / "tmp")
        self.assertFalse(os.path.lexists(self.home / "Library"))                                        # the link and the empty folder made for it
        self.assertEqual((self.keychains / "login.keychain-db").read_text(), "do not touch")
        self.assertTrue((self.home / ".gemini" / "antigravity-cli" / "settings.json").is_file())        # the rest of the private HOME stays as evidence

    def test_cleanup_is_harmless_without_a_link_twice_and_for_a_real_directory_named_keychains(self):
        ad = adapter(keychains_from=self.keychains)
        req = RunRequest("t", 1, "p", self.base / "ws", "m")
        ad.cleanup(req, self.home, self.base / "tmp")                                                   # nothing there yet
        real = self.home / "Library" / "Keychains"
        real.mkdir(parents=True)
        (real / "mine.txt").write_text("a real folder, not ours to remove")
        ad.cleanup(req, self.home, self.base / "tmp")
        ad.cleanup(req, self.home, self.base / "tmp")
        self.assertEqual((real / "mine.txt").read_text(), "a real folder, not ours to remove")

    @unittest.skipUnless(sys.platform == "darwin", "the keychain link is a macOS matter")
    def test_cleanup_never_touches_the_real_home_even_if_it_is_handed_one(self):
        A.write_home(self.home, allow_write=False, keychains_from=self.keychains)
        with mock.patch.object(A, "real_home", return_value=self.home):                                   # pretend this directory is the real home
            A.remove_keychain_link(self.home)
        self.assertTrue((self.home / "Library" / "Keychains").is_symlink())

    @unittest.skipUnless(sys.platform == "darwin", "the keychain link is a macOS matter")
    def test_remove_keychain_link_keeps_a_library_folder_that_holds_something_else(self):
        A.write_home(self.home, allow_write=False, keychains_from=self.keychains)
        (self.home / "Library" / "Preferences").mkdir()
        A.remove_keychain_link(self.home)
        self.assertFalse(os.path.lexists(self.home / "Library" / "Keychains"))
        self.assertTrue((self.home / "Library" / "Preferences").is_dir())

    def test_the_real_home_is_what_the_password_database_says(self):
        self.assertTrue(A.real_home().is_absolute())


class WriteRules(unittest.TestCase):
    """A file-changing task gets `write_file(<path>)` allow-rules for the paths its contract allows, and nothing else. Measured on 1.3.5 (write
    smoke 2026-10-15): in headless mode even an edit inside the working copy is soft-denied without a rule."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="par-agy-rules-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.ws = self.base / "ws"
        self.ws.mkdir()
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.keychains = self.base / "real_keychains"
        self.keychains.mkdir()

    def settings(self, home=None):
        return json.loads(((home or self.home) / ".gemini" / "antigravity-cli" / "settings.json").read_text())

    def test_each_allowed_path_becomes_a_relative_and_an_absolute_write_rule_and_nothing_else(self):
        rules = A.write_rules(["app/", "docs/notes.md"], self.ws)
        self.assertEqual(rules, ["write_file(app/)", f"write_file({self.ws}/app/)", "write_file(docs/notes.md)", f"write_file({self.ws}/docs/notes.md)"])
        self.assertEqual(A.write_rules(["app/", "app/"], self.ws), rules[:2])                           # no duplicates
        self.assertEqual(A.write_rules([], self.ws), [])

    def test_the_absolute_form_names_the_real_path_of_the_working_copy_however_it_was_spelled(self):
        link = self.base / "link"
        os.symlink(self.ws, link)
        self.assertEqual(A.write_rules(["app/"], link), A.write_rules(["app/"], self.ws))

    def test_a_working_copy_path_that_would_be_ambiguous_inside_a_rule_gets_the_relative_rules_only(self):
        for name in ("we(ird)", "star*", "br[ack]et", "brace{s}", "back\\slash"):
            odd = self.base / name
            odd.mkdir()
            self.assertEqual(A.write_rules(["app/"], odd), ["write_file(app/)"], name)
        spaced = self.base / "Application Support"                                                      # macOS data directory: a space is fine
        spaced.mkdir()
        self.assertEqual(A.write_rules(["app/"], spaced), ["write_file(app/)", f"write_file({spaced}/app/)"])

    def test_the_write_task_settings_carry_exactly_those_rules_and_the_others_carry_none(self):
        rules = A.write_rules(["app/"], self.ws)
        A.write_home(self.home, allow_write=True, keychains_from=self.keychains, rules=rules)
        self.assertEqual(self.settings()["permissions"], {"allow": rules})
        self.assertEqual(self.settings()["toolPermission"], "request-review")
        self.assertNotIn("permissions", A.settings_for(True))                                           # a write task without rules: none invented
        self.assertNotIn("permissions", A.settings_for(False))
        with self.assertRaises(ValueError):
            A.settings_for(False, rules)                                                                # a read-only task can never get one
        other = self.base / "home2"
        other.mkdir(mode=0o700)
        A.write_home(other, allow_write=False, keychains_from=self.keychains)
        self.assertNotIn("permissions", self.settings(other))

    def test_only_plain_write_rules_are_ever_written(self):
        for bad in ("command(git)", "command(regex:npm run (build|test))", "command(*)", "write_file(*)", "write_file(/)", "write_file(//)",
                    "write_file(regex:.*)", "write_file(../x)", "write_file(app/../../x)", "write_file(~/x)", "write_file()", "write_file(a)\nwrite_file(b)",
                    "write_file(a) command(b)", "write_file(/x/*/app/)", "write_file(/x/a(b)/app/)", "write_file(/x/[ab]/app/)", "write_file(/x/{a,b}/app/)",
                    "write_file(/x/a?/app/)", "write_file(/x/a\\\\b/app/)", "write_file(/x/../app/)", "read_file(app/)", "unsandboxed(git push)", "mcp(x/y)", "write_file(app/", "WRITE_FILE(app/)", "", None, 5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                A.checked_rules([bad])
            with self.assertRaises(ValueError, msg=repr(bad)):
                A.settings_for(True, [bad])
        self.assertEqual(A.checked_rules(["write_file(app/)", "write_file(/x/Application Support/app/)"]),
                         ["write_file(app/)", "write_file(/x/Application Support/app/)"])

    def test_the_rules_never_include_a_command_rule_whatever_the_paths_are(self):
        for paths in (["app/"], ["command(git)/"], ["a/b/c.py", "d/"]):
            try:
                rules = A.write_rules(paths, self.ws)
                A.checked_rules(rules)
            except ValueError:
                continue
            self.assertTrue(all(r.startswith("write_file(") for r in rules), paths)

    def test_prepare_derives_the_rules_from_the_request_and_only_for_a_write_task(self):
        ad = adapter(keychains_from=self.keychains)
        ad.prepare(RunRequest("t", 1, "p", self.ws, "m", allow_write=True, allowed_paths=("app/", "docs/a.md")), self.home, self.base / "tmp")
        self.assertEqual(self.settings()["permissions"], {"allow": A.write_rules(["app/", "docs/a.md"], self.ws)})
        other = self.base / "home2"
        other.mkdir(mode=0o700)
        ad.prepare(RunRequest("t", 1, "p", self.ws, "m", allow_write=False, allowed_paths=("app/",)), other, self.base / "tmp")
        self.assertNotIn("permissions", self.settings(other))                                           # a read-only request never gets rules

    def test_a_prepare_with_a_rule_it_cannot_accept_writes_nothing(self):
        ad = adapter(keychains_from=self.keychains)
        with mock.patch.object(A, "write_rules", return_value=["command(git)"]):
            with self.assertRaises(ValueError):
                ad.prepare(RunRequest("t", 1, "p", self.ws, "m", allow_write=True, allowed_paths=("app/",)), self.home, self.base / "tmp")
        self.assertFalse((self.home / ".gemini" / "antigravity-cli" / "settings.json").exists())

    def test_preflight_refuses_a_file_changing_task_that_cannot_be_given_its_rules(self):
        ad = A.AntigravityAdapter(sys.executable, verify_signature=False, environ={})
        for paths in ((), ("a b/",), ("\u00e9/",), ("a(b)/",), ("x*",), ("../x",), ("/etc/",), ("./",), ("",), ("app/", "bad path/")):
            reason = ad.preflight(RunRequest("t", 1, "p", self.ws, "gemini-3.8-flash-medium", allow_write=True, allowed_paths=paths))
            self.assertTrue(reason and "allowed" in reason and "agy" in reason, (paths, reason))


class WriteStrategy(unittest.TestCase):
    """`rules` (default) or `accept-edits`: two ways to let a file-changing task edit files in headless mode (see antigravity.WRITE_STRATEGIES)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="par-agy-strategy-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.ws = self.base / "ws"
        self.ws.mkdir()
        self.keychains = self.base / "kc"
        self.keychains.mkdir()

    def make(self, **kw):
        return A.AntigravityAdapter("/x/agy", verify_signature=False, environ=kw.pop("environ", {}), keychains_from=self.keychains, **kw)

    def request(self, allow_write):
        return RunRequest("t", 1, "p", self.ws, "gemini-3.8-flash-medium", allow_write=allow_write, allowed_paths=("app/",) if allow_write else ())

    def test_the_default_is_rules_and_the_argv_has_no_mode_flag(self):
        ad = self.make()
        self.assertEqual(ad.write_strategy, "rules")
        self.assertNotIn("--mode", ad.build_argv(self.request(True)))

    def test_accept_edits_adds_the_mode_flag_for_a_file_changing_task_only(self):
        ad = self.make(write_strategy="accept-edits")
        argv = ad.build_argv(self.request(True))
        self.assertEqual(argv[-2:], ["--mode", "accept-edits"])
        self.assertNotIn("--mode", ad.build_argv(self.request(False)))
        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_accept_edits_writes_no_allow_rules_and_rules_writes_no_mode_flag(self):
        home = self.base / "home"
        home.mkdir(mode=0o700)
        self.make(write_strategy="accept-edits").prepare(self.request(True), home, self.base / "tmp")
        settings = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text())
        self.assertEqual(settings["toolPermission"], "request-review")
        self.assertNotIn("permissions", settings)

    def test_the_strategy_can_come_from_the_environment_and_nonsense_is_ignored(self):
        self.assertEqual(self.make(environ={"PAR_AGY_WRITE_STRATEGY": "accept-edits"}).write_strategy, "accept-edits")
        for junk in ("", "RULES", "accept_edits", "always-proceed", "rules; rm -rf /", " rules"):
            self.assertEqual(self.make(environ={"PAR_AGY_WRITE_STRATEGY": junk}).write_strategy, "rules", junk)
        self.assertEqual(self.make(write_strategy="bogus").write_strategy, "rules")
        self.assertEqual(self.make(environ={"PAR_AGY_WRITE_STRATEGY": "accept-edits"}, write_strategy="rules").write_strategy, "rules")

    def test_a_cli_that_reports_accept_edits_is_as_wide_as_request_review_and_no_wider(self):
        self.assertFalse(A.wider_than("accept-edits", A.REVIEW))
        self.assertTrue(A.wider_than("accept-edits", A.STRICT))                                    # a read-only task must still never run with it


class ProjectProblems(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="par-agy-proj-")
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name)

    def touch(self, rel, text="x"):
        p = self.ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_a_plain_project_is_fine(self):
        self.touch("a.py")
        self.assertIsNone(A.project_problem(self.ws))

    def test_rules_skills_and_agent_instruction_files_are_text_and_accepted(self):
        for rel in (".agents/rules/style.md", ".agents/skills/s/SKILL.md", ".agents/rules.json", ".agents/AGENTS.md", ".agents/GEMINI.md",
                    ".agent/rules/old.md", ".agent/skills/x/SKILL.md", "AGENTS.md", "GEMINI.md"):
            self.touch(rel)
        self.assertIsNone(A.project_problem(self.ws))

    def test_hooks_mcp_servers_plugins_and_anything_unknown_are_refused(self):
        for rel in (".agents/hooks.json", ".agents/mcp_config.json", ".agents/plugins/p/plugin.json", ".agents/settings.json", ".agent/hooks.json"):
            with self.subTest(rel):
                shutil.rmtree(self.ws)
                self.ws.mkdir()
                self.touch(rel)
                problem = A.project_problem(self.ws)
                self.assertIsNotNone(problem)
                self.assertIn(rel.split("/")[0] + "/" + rel.split("/")[1], problem)

    def test_an_agents_entry_that_is_not_a_plain_directory_is_refused(self):
        (self.ws / ".agents").write_text("a file, not a directory")
        self.assertIn("not a plain directory", A.project_problem(self.ws))
        (self.ws / ".agents").unlink()
        elsewhere = self.ws.parent / (self.ws.name + "-elsewhere")
        elsewhere.mkdir()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        os.symlink(elsewhere, self.ws / ".agents")
        self.assertIn("not a plain directory", A.project_problem(self.ws))

    def test_preflight_refuses_such_a_project_before_anything_else_is_asked_of_the_cli(self):
        self.touch(".agents/hooks.json", "{}")
        ad = A.AntigravityAdapter(sys.executable, verify_signature=False, environ={})
        reason = ad.preflight(RunRequest("t", 1, "p", self.ws, "m-1"))
        self.assertIn("hooks.json", reason)


class TrustInTheBinary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="par-agy-exe-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))

    def exe(self, name="agy", mode=0o755, body="#!/bin/sh\nexit 0\n"):
        p = self.base / name
        p.write_text(body)
        os.chmod(p, mode)
        return p

    def test_the_executable_must_be_an_absolute_path_to_a_private_executable_file(self):
        self.assertIn("absolute path", A.AntigravityAdapter("agy").executable_problem())
        self.assertIn("absolute path", A.AntigravityAdapter("").executable_problem())
        self.assertIn("no executable at", A.AntigravityAdapter(str(self.base / "nope")).executable_problem())
        self.assertIn("not executable", A.AntigravityAdapter(str(self.exe(mode=0o644))).executable_problem())
        self.assertIn("writable by other users", A.AntigravityAdapter(str(self.exe("w", mode=0o777))).executable_problem())
        self.assertIsNone(A.AntigravityAdapter(str(self.exe("ok"))).executable_problem())

    def test_a_missing_agy_is_unavailable_and_the_probe_says_why_it_is_never_replaced(self):
        """The plan's T04: agy is not installed -> UNAVAILABLE with the reason; the person decides about installing; nothing falls back to gemini."""
        for exe in (str(self.base / "nope"), "agy", ""):
            result = A.AntigravityAdapter(exe).probe()
            self.assertEqual(result.status, ProviderStatus.UNAVAILABLE, exe)
            self.assertTrue(result.detail, exe)
        self.assertIn("no executable at", A.AntigravityAdapter(str(self.base / "nope")).probe().detail)
        self.assertNotIn("gemini", A.AntigravityAdapter(str(self.base / "nope")).probe().detail.lower())

    @unittest.skipUnless(sys.platform == "darwin", "code signatures are checked on macOS")
    def test_a_script_is_refused_because_it_carries_no_signature(self):
        problem = A.AntigravityAdapter(str(self.exe())).signature_problem()
        self.assertIn("is a script", problem)

    @unittest.skipUnless(sys.platform == "darwin", "code signatures are checked on macOS")
    def test_a_file_that_is_not_a_mach_o_binary_is_refused(self):
        problem = A.AntigravityAdapter(str(self.exe("blob", body="just some bytes that are not code"))).signature_problem()
        self.assertIn("not a Mach-O", problem)

    @unittest.skipUnless(sys.platform == "darwin", "code signatures are checked on macOS")
    def test_a_binary_signed_by_someone_else_is_refused(self):
        python = Path(os.path.realpath(sys.executable))
        problem = A.AntigravityAdapter(str(python))._check_signature(python)
        self.assertIsNotNone(problem)                                                                   # not Google's team (or not verifiable)
        self.assertNotIn(A.GOOGLE_TEAM_ID, problem or "")

    @unittest.skipUnless(sys.platform == "darwin", "code signatures are checked on macOS")
    def test_real_binaries_google_is_accepted_and_every_other_team_is_named_and_refused(self):
        google = Path("/Applications/Antigravity.app/Contents/MacOS/Antigravity")            # signed by Google LLC (EQHXZ8M8AV)
        openai = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")   # signed by OpenAI
        apple = Path("/usr/bin/ssh")                                                         # a platform binary: no team at all
        if not google.exists():
            self.skipTest("the Antigravity app is not installed here")
        self.assertIsNone(A.AntigravityAdapter._check_signature(google))
        if openai.exists():
            self.assertEqual(A.AntigravityAdapter._check_signature(openai), "the executable is not signed by Google (team: 2DC432GLL2)")
        self.assertEqual(A.AntigravityAdapter._check_signature(apple), "the executable is not signed by Google (team: not set)")

    def test_the_signature_check_can_only_be_skipped_by_construction(self):
        self.assertIsNone(A.AntigravityAdapter(str(self.exe()), verify_signature=False).signature_problem())


class LoginPlan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="par-agy-login-")
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(os.path.realpath(self._tmp.name))
        os.chmod(self.data, 0o700)
        self.keychains = self.data.parent / (self.data.name + "-kc")
        self.keychains.mkdir()
        self.addCleanup(shutil.rmtree, self.keychains, True)

    def test_the_plan_runs_the_cli_itself_in_a_runtime_owned_home_and_an_empty_directory(self):
        env = {"TERM": "xterm-256color", "COLORTERM": "truecolor", "GEMINI_API_KEY": SK, "HTTPS_PROXY": "http://p:1", "HOME": "/Users/real", "PATH": "/evil"}
        plan = A.login_plan("/x/agy", self.data, env, keychains_from=self.keychains)
        self.assertEqual(plan["argv"], ["/x/agy"])
        self.assertEqual(plan["home"], str(self.data / "agy_login_home"))
        self.assertEqual(plan["cwd"], str(self.data / "scratch" / "agy_login_cwd"))
        self.assertEqual(plan["env"], {"PATH": "/usr/bin:/bin", "HOME": plan["home"], "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                                       "AGY_CLI_DISABLE_AUTO_UPDATE": "true", "TERM": "xterm-256color", "COLORTERM": "truecolor",
                                       "HTTPS_PROXY": "http://p:1"})
        self.assertEqual(os.listdir(plan["cwd"]), [])
        for d in (plan["home"], plan["cwd"], str(self.data / "scratch")):
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        settings = json.loads((Path(plan["home"]) / ".gemini" / "antigravity-cli" / "settings.json").read_text())
        self.assertEqual(settings["toolPermission"], "strict")                                         # a sign-in session gets no more rights than a read-only Worker

    def test_without_create_nothing_is_written(self):
        plan = A.login_plan("/x/agy", self.data, {}, create=False)
        self.assertEqual(os.listdir(self.data), [])
        self.assertEqual(plan["notes"], [])
        self.assertEqual(plan["home"], str(self.data / "agy_login_home"))

    def test_running_it_twice_changes_nothing(self):
        A.login_plan("/x/agy", self.data, {}, keychains_from=self.keychains)
        first = sorted(str(p.relative_to(self.data)) for p in self.data.rglob("*"))
        A.login_plan("/x/agy", self.data, {}, keychains_from=self.keychains)
        self.assertEqual(sorted(str(p.relative_to(self.data)) for p in self.data.rglob("*")), first)


class ParseModels(unittest.TestCase):
    def test_the_real_output_shape_slug_tab_display_name(self):
        text = ("gemini-3.8-flash-high\tGemini 3.8 Flash (High)\nclaude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
                "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n")
        self.assertEqual(A.parse_models(text), ("gemini-3.8-flash-high", "claude-sonnet-4-6", "gpt-oss-120b-medium"))

    def test_progress_lines_blank_lines_duplicates_and_junk_are_ignored(self):
        text = "Fetching available models...\n\n  \nalpha-1\tA\nalpha-1\tA again\nnot a slug!\n-bad\tx\n"
        self.assertEqual(A.parse_models(text), ("alpha-1",))
        self.assertEqual(A.parse_models(""), ())


class SignInCheck(unittest.TestCase):
    """The sign-in state comes from `agy models`: it fails at once when signed out. `-p /usage` WAITS for a browser login (measured on
    1.3.5) and must never be what an attempt waits for."""

    def setUp(self):
        from tests.fake_agy import FakeAgy
        self._tmp = tempfile.TemporaryDirectory(prefix="par-agy-signin-")
        self.addCleanup(self._tmp.cleanup)
        self.fake = FakeAgy(Path(self._tmp.name) / "fake")
        self.ad = self.fake.adapter()
        self.addCleanup(A.forget_caches)

    def test_signed_in_gives_ok_and_the_catalog(self):
        self.assertEqual(self.ad.login_state()[0], "ok")
        self.assertIn("claude-sonnet-4-6", self.ad.catalog)
        self.assertEqual([c["kind"] for c in self.fake.calls()], ["models"])

    def test_each_failure_has_its_own_state_and_none_of_them_waits(self):
        for login, state in (("none", "none"), ("locked", "locked"), ("weird", "unknown")):
            A.forget_caches()
            self.fake.configure(login=login)
            t0 = time.monotonic()
            self.assertEqual(self.ad.login_state(ttl=0)[0], state, login)
            self.assertLess(time.monotonic() - t0, 20, login)
            self.assertEqual(self.ad.catalog, (), login)
        self.assertNotIn("usage", [c["kind"] for c in self.fake.calls()])

    def test_the_answer_is_cached_for_a_minute_and_ttl_zero_asks_again(self):
        self.ad.login_state()
        self.ad.login_state()
        self.assertEqual(len(self.fake.calls()), 1)
        self.ad.login_state(ttl=0)
        self.assertEqual(len(self.fake.calls()), 2)

    def test_a_cached_answer_still_fills_the_catalog_of_a_fresh_adapter(self):
        self.ad.login_state()
        fresh = self.fake.adapter()
        fresh.login_state()
        self.assertEqual(fresh.catalog, self.ad.catalog)
        self.assertEqual(len(self.fake.calls()), 1)

    def test_an_empty_model_list_is_not_a_sign_in(self):
        self.fake.configure(models=[])
        self.assertEqual(self.ad.login_state()[0], "unknown")

    def test_the_usage_report_is_four_tab_separated_columns(self):
        rows = self.ad.usage_report()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], {"group": "Gemini Models", "window": "Weekly Limit Remaining", "remaining": "99%", "resets_at": "2026-10-20T08:19:37Z"})

    def test_a_signed_out_usage_report_that_waits_is_given_up_on_and_comes_back_empty(self):
        self.fake.configure(login="none", usage_wait=30)
        with mock.patch.object(A, "ACCOUNT_TIMEOUT", 1.5):
            self.assertEqual(self.ad.usage_report(), [])


class ForgetCaches(unittest.TestCase):
    def test_forget_caches_empties_both_caches(self):
        with A._CACHE_LOCK:
            A._LOGINS["x"] = (0.0, "ok", "", ())
            A._SIGNATURES[("p", 1, 2, 3)] = (0.0, None)
        A.forget_caches()
        self.assertEqual((A._LOGINS, A._SIGNATURES), ({}, {}))


if __name__ == "__main__":
    unittest.main()
