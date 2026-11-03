"""JSON-RPC 2.0 framing for MCP over stdio: one message per line, bounded, never a batch.

The coordinator is a model-driven client, so every line it sends is untrusted input. A line is read with a size bound
before anything parses it, a line that does not parse is an error reply rather than a crash, and every reply is one
ASCII line: escaping keeps characters such as U+2028 out of the stream, because some line-based readers split on them.
"""
from __future__ import annotations

import json
from typing import Any, BinaryIO

MAX_LINE_BYTES = 256 * 1024                           # a request line above this is refused without being parsed
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

EOF = "eof"
LINE = "line"
OVERSIZE = "oversize"


def read_frame(stream: BinaryIO, limit: int = MAX_LINE_BYTES) -> "tuple[str, bytes]":
    """The next request line without its newline, as (LINE, body); (EOF, b"") at end of input; (OVERSIZE, b"") for a
    line over the limit, whose remainder is discarded without being kept. Never holds more than ``limit + 2`` bytes."""
    raw = stream.readline(limit + 2)                  # the line, its newline, and one byte that shows an overflow
    if not raw:
        return EOF, b""
    if raw.endswith(b"\n"):
        body = raw[:-1]
        return (OVERSIZE, b"") if len(body) > limit else (LINE, body)
    if len(raw) < limit + 2:
        return LINE, raw                              # the last line, with no newline before the end of input
    while True:
        chunk = stream.readline(65536)
        if not chunk or chunk.endswith(b"\n"):
            return OVERSIZE, b""


def decode(body: bytes) -> Any:
    """Parse one line strictly: UTF-8 JSON with no duplicate keys and no NaN or Infinity. Raises ValueError, and
    RecursionError for absurd nesting; the caller turns both into a parse error."""
    return json.loads(body.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_refuse_constant)


def _unique_keys(pairs: "list[tuple[str, Any]]") -> "dict[str, Any]":
    out: "dict[str, Any]" = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def encode(message: "dict[str, Any]") -> bytes:
    """One reply as one newline-terminated ASCII line."""
    return json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"


def result(req_id: Any, value: Any) -> "dict[str, Any]":
    return {"jsonrpc": "2.0", "id": req_id, "result": value}


def error(req_id: Any, code: int, message: str) -> "dict[str, Any]":
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
