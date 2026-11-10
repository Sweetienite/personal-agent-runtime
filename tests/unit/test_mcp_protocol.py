"""MCP framing over stdio: handshake, version negotiation, JSON-RPC errors, bounded lines and silence for notifications.

Everything here runs in-process on byte buffers. The child-process tests (stdout really carries only JSON lines) are in
test_mcp_process.py.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path

import par
from par.mcp import protocol as P
from par.mcp import server as S
from par.mcp.tools import Tools


class FakeKernel:
    """Only what the protocol layer touches: the data directory (for scrubbing) and one read-only listing."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)

    def list_tasks(self, status=None, project_id=None):
        return []


class Framing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tools = Tools(FakeKernel(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def exchange(self, *lines, tools=None):
        """Feed raw lines to the server; return (exit code, replies, stderr text)."""
        stdin = io.BytesIO(b"".join(lines))
        stdout, stderr = io.BytesIO(), io.StringIO()
        code = S.serve(tools or self.tools, stdin, stdout, stderr)
        raw = [line for line in stdout.getvalue().split(b"\n") if line]
        for line in raw:
            self.assertTrue(line.isascii(), "every reply line is plain ASCII JSON")
        return code, [json.loads(line) for line in raw], stderr.getvalue()

    @staticmethod
    def request(method, params=None, req_id=1):
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        return (json.dumps(msg) + "\n").encode("utf-8")

    # ---- handshake -------------------------------------------------------------------------------------
    def test_initialize_replies_with_capabilities_server_info_and_the_warning(self):
        code, replies, _ = self.exchange(self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}))
        self.assertEqual(code, 0)
        result = replies[0]["result"]
        self.assertEqual(replies[0]["jsonrpc"], "2.0")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"], {"name": "par-bridge", "version": par.__version__})
        self.assertEqual(result["instructions"], S.INSTRUCTIONS)
        self.assertIn("unverified", result["instructions"])
        self.assertIn("coordinator", result["instructions"])
        self.assertLessEqual(result["instructions"].count(". "), 2, "two or three sentences")

    def test_initialize_uses_read_only_guidance_in_inspection_mode(self):
        self.tools.kernel.inspection_only = True
        _, replies, _ = self.exchange(self.request("initialize", {}))
        instructions = replies[0]["result"]["instructions"]
        self.assertEqual(instructions, S.INSPECTION_INSTRUCTIONS)
        self.assertIn("inspection-only", instructions)
        self.assertIn("par doctor", instructions)
        self.assertIn("repair", instructions)
        self.assertIn("restart", instructions)
        self.assertIn("unverified worker text", instructions)
        for action in ("approve", "reject", "accept", "close"):
            self.assertNotIn(action, instructions)

    def test_the_client_version_is_echoed_when_supported_otherwise_the_latest_is_offered(self):
        cases = {"2024-11-05": "2024-11-05", "2025-03-26": "2025-03-26", "2025-06-18": "2025-06-18",
                 "1999-01-01": "2025-06-18", "": "2025-06-18", "latest": "2025-06-18"}
        for asked, offered in cases.items():
            with self.subTest(asked=asked):
                _, replies, _ = self.exchange(self.request("initialize", {"protocolVersion": asked}))
                self.assertEqual(replies[0]["result"]["protocolVersion"], offered)

    def test_initialized_notification_gets_no_reply(self):
        note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n"
        code, replies, _ = self.exchange(note)
        self.assertEqual((code, replies), (0, []))

    def test_ping_answers_with_an_empty_result(self):
        _, replies, _ = self.exchange(self.request("ping"))
        self.assertEqual(replies, [{"jsonrpc": "2.0", "id": 1, "result": {}}])

    # ---- errors ------------------------------------------------------------------------------------------
    def test_an_unknown_method_with_an_id_is_method_not_found(self):
        _, replies, _ = self.exchange(self.request("resources/list", {}, req_id="abc"))
        self.assertEqual(replies[0]["id"], "abc")
        self.assertEqual(replies[0]["error"]["code"], P.METHOD_NOT_FOUND)

    def test_malformed_json_is_a_parse_error_and_the_server_keeps_going(self):
        _, replies, _ = self.exchange(b"{not json}\n", self.request("ping", req_id=2))
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[0]["error"]["code"], P.PARSE_ERROR)
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_a_blank_line_and_non_utf8_bytes_are_parse_errors(self):
        for junk in (b"\n", b"\xff\xfe\xfd\n"):
            with self.subTest(junk=junk):
                _, replies, _ = self.exchange(junk)
                self.assertEqual(replies[0]["error"]["code"], P.PARSE_ERROR)

    def test_a_batch_array_is_an_invalid_request(self):
        batch = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).encode() + b"\n"
        _, replies, _ = self.exchange(batch)
        self.assertEqual((replies[0]["id"], replies[0]["error"]["code"]), (None, P.INVALID_REQUEST))
        self.assertIn("batch", replies[0]["error"]["message"])                                  # refused as a batch, not just as "not an object"

    def test_a_request_that_is_not_an_object_is_an_invalid_request(self):
        _, replies, _ = self.exchange(b"42\n", b'"ping"\n', b"null\n")
        self.assertEqual([r["error"]["code"] for r in replies], [P.INVALID_REQUEST] * 3)

    def test_duplicate_keys_and_non_standard_numbers_are_refused_not_guessed(self):
        duplicated = b'{"jsonrpc":"2.0","id":1,"method":"ping","method":"tools/list"}\n'
        nan = b'{"jsonrpc":"2.0","id":2,"method":"ping","params":{"x":NaN}}\n'
        _, replies, _ = self.exchange(duplicated, nan)
        self.assertEqual([r["error"]["code"] for r in replies], [P.PARSE_ERROR, P.PARSE_ERROR])

    def test_extreme_nesting_is_a_parse_error_and_does_not_crash_the_server(self):
        deep = b'{"jsonrpc":"2.0","id":3,"method":"ping","params":' + b"[" * 20000 + b"]" * 20000 + b"}\n"
        code, replies, _ = self.exchange(deep, self.request("ping", req_id=4))
        self.assertEqual(code, 0)
        self.assertEqual(replies[0]["error"]["code"], P.PARSE_ERROR)
        self.assertEqual(replies[1]["result"], {})

    def test_invalid_params_are_minus_32602(self):
        cases = [
            self.request("initialize", []),                                    # not an object
            self.request("tools/call", {"name": 5}),                            # name is not a string
            self.request("tools/call", {"name": "no_such_tool"}),               # unknown tool name
            self.request("tools/call", {"name": "list_tasks", "arguments": "x"}),  # arguments not an object
        ]
        _, replies, _ = self.exchange(*cases)
        self.assertEqual([r["error"]["code"] for r in replies], [P.INVALID_PARAMS] * 4)

    def test_a_failing_tool_is_a_tool_result_with_is_error_not_a_protocol_error(self):
        _, replies, _ = self.exchange(self.request("tools/call", {"name": "get_task_status", "arguments": {}}))
        self.assertNotIn("error", replies[0])
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("missing argument", replies[0]["result"]["content"][0]["text"])

    def test_an_internal_failure_is_a_generic_error_and_the_server_keeps_going(self):
        class Broken(Tools):
            def list_tools(self):
                raise RuntimeError("secret detail that must not reach the coordinator")

        out = Broken(FakeKernel(self._tmp.name))
        code, replies, err = self.exchange(self.request("tools/list", req_id=5), self.request("ping", req_id=6), tools=out)
        self.assertEqual(code, 0)
        self.assertEqual(replies[0]["error"], {"code": P.INTERNAL_ERROR, "message": "internal error"})
        self.assertEqual(replies[1]["result"], {})
        self.assertNotIn("secret detail", json.dumps(replies))
        self.assertIn("RuntimeError", err)
        self.assertNotIn("Traceback", err)

    # ---- framing and size -----------------------------------------------------------------------------
    def test_a_request_line_over_256_kilobytes_is_refused_without_being_parsed(self):
        padding = b"a" * (P.MAX_LINE_BYTES + 10)
        oversize = b'{"jsonrpc":"2.0","id":7,"method":"ping","params":{"x":"' + padding + b'"}}\n'
        code, replies, _ = self.exchange(oversize, self.request("ping", req_id=8))
        self.assertEqual(code, 0)
        self.assertEqual((replies[0]["id"], replies[0]["error"]["code"]), (None, P.INVALID_REQUEST))
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 8, "result": {}}, "the rest of the stream is still served")

    def test_a_line_of_exactly_the_limit_is_accepted(self):
        prefix = b'{"jsonrpc":"2.0","id":9,"method":"ping","params":{"x":"'
        suffix = b'"}}'
        line = prefix + b"a" * (P.MAX_LINE_BYTES - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(line), P.MAX_LINE_BYTES)
        _, replies, _ = self.exchange(line + b"\n")
        self.assertEqual(replies, [{"jsonrpc": "2.0", "id": 9, "result": {}}])

    def test_a_line_one_byte_over_the_limit_is_refused(self):
        prefix = b'{"jsonrpc":"2.0","id":9,"method":"ping","params":{"x":"'
        suffix = b'"}}'
        line = prefix + b"a" * (P.MAX_LINE_BYTES + 1 - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(line), P.MAX_LINE_BYTES + 1)
        _, replies, _ = self.exchange(line + b"\n", self.request("ping", req_id=10))
        self.assertEqual((replies[0]["id"], replies[0]["error"]["code"]), (None, P.INVALID_REQUEST))
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 10, "result": {}})

    def test_the_reader_is_bounded_before_anything_is_parsed(self):
        class Spy:
            """A stream that records how much each read asked for."""

            def __init__(self, data):
                self.inner = io.BytesIO(data)
                self.asked = []

            def readline(self, size=-1):
                self.asked.append(size)
                return self.inner.readline(size)

        spy = Spy(b"x" * (P.MAX_LINE_BYTES * 3) + b"\n")
        status, _ = P.read_frame(spy)
        self.assertEqual(status, P.OVERSIZE)
        self.assertTrue(spy.asked, "the reader read something")
        self.assertTrue(all(0 < size <= P.MAX_LINE_BYTES + 2 for size in spy.asked), spy.asked)

    def test_an_endless_oversize_line_without_a_newline_ends_cleanly(self):
        code, replies, _ = self.exchange(b"x" * (P.MAX_LINE_BYTES * 2))
        self.assertEqual(code, 0)
        self.assertEqual(replies[0]["error"]["code"], P.INVALID_REQUEST)

    def test_a_final_request_without_a_newline_is_still_answered(self):
        _, replies, _ = self.exchange(b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
        self.assertEqual(replies, [{"jsonrpc": "2.0", "id": 1, "result": {}}])

    def test_end_of_input_exits_zero_without_output(self):
        code, replies, _ = self.exchange()
        self.assertEqual((code, replies), (0, []))

    # ---- notifications never get an answer ---------------------------------------------------------------
    def test_notifications_never_get_a_reply_even_when_unknown(self):
        unknown = json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}).encode() + b"\n"
        no_method = json.dumps({"jsonrpc": "2.0", "params": {}}).encode() + b"\n"
        code, replies, _ = self.exchange(unknown, no_method)
        self.assertEqual((code, replies), (0, []))

    def test_every_reply_is_one_json_object_with_the_jsonrpc_version(self):
        _, replies, _ = self.exchange(self.request("ping", req_id=1), self.request("nope", req_id=2))
        for reply in replies:
            self.assertEqual(reply["jsonrpc"], "2.0")
            self.assertEqual(sum(("result" in reply, "error" in reply)), 1)


class Versions(unittest.TestCase):
    def test_the_supported_versions_are_the_three_documented_ones(self):
        self.assertEqual(P.SUPPORTED_PROTOCOLS, ("2024-11-05", "2025-03-26", "2025-06-18"))
        self.assertEqual(P.LATEST_PROTOCOL, "2025-06-18")


if __name__ == "__main__":
    unittest.main()
