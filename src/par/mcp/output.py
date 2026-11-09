"""What leaves the bridge process: redaction, scrubbing, size fitting and clean child environments."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import par
from par.core.redact import redact_text
from par.mcp.catalog import ABS_PATH, ANSI, RESULT_MAX_CHARS, STRIP

CHILD_PATH = "/usr/bin:/bin"


def scrub(text: Any, data_dir: "str | os.PathLike[str] | None", limit: int) -> str:
    """Text fit for the coordinator: the data directory shown as <data>, the home directory as ~, other absolute paths as
    <path>, secrets redacted, control characters stripped (newlines kept), clipped to ``limit`` characters."""
    out = str(text)
    if data_dir:
        for root in _roots(data_dir):
            out = out.replace(root, "<data>")
    out = redact_text(out, home=_home())
    out = ABS_PATH.sub("<path>", out)
    out = ANSI.sub("", out).replace("\t", " ")
    out = STRIP.sub("", out)
    return out if len(out) <= limit else out[: max(0, limit - 3)] + "..."


def _roots(data_dir: "str | os.PathLike[str]") -> "list[str]":
    roots = {str(Path(data_dir)), os.path.realpath(os.fspath(data_dir))}
    return sorted((r for r in roots if r), key=len, reverse=True)


def _home() -> str:
    try:
        return str(Path.home())
    except Exception:                                   # no home directory: nothing to shorten
        return ""


def relative(value: Any) -> bool:
    return isinstance(value, str) and not value.startswith("/") and "\\" not in value and ".." not in value.split("/")


def _lookup(payload: Mapping[str, Any], path: "tuple[str, ...]") -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def clip_json_safe(text: str, budget: int) -> "tuple[str, bool]":
    """Cut ``text`` so its *JSON-encoded* form (escaped newlines etc cost more than one character each) fits
    ``budget``, instead of capping the raw length and risking the whole payload tripping fit's own cap later."""
    if len(json.dumps(text, ensure_ascii=False)) <= budget:
        return text, False
    cut = min(len(text), budget)
    while cut > 0 and len(json.dumps(text[:cut] + "...", ensure_ascii=False)) > budget:
        cut -= max(1, cut // 20)
    return text[:cut] + "...", True


def _fit(payload: "dict[str, Any]", shrink: "tuple[tuple[str, ...], ...]") -> "dict[str, Any]":
    """Trim the largest optional list, one item at a time, until the JSON text fits; then say it was truncated."""
    while len(json.dumps(payload, ensure_ascii=False)) > RESULT_MAX_CHARS:
        candidates = []
        for path in shrink:
            value = _lookup(payload, path)
            if isinstance(value, list) and value:
                candidates.append((len(json.dumps(value, ensure_ascii=False)), value))
        if not candidates:
            break
        max(candidates, key=lambda c: c[0])[1].pop()
        payload["truncated"] = True
        if "tasks" in payload and "count" in payload:
            payload["count"] = len(payload["tasks"])
    if len(json.dumps(payload, ensure_ascii=False)) > RESULT_MAX_CHARS:     # unreachable with the caps above; never oversize
        return {"truncated": True, "note": "the answer was too large to send; ask for less"}
    return payload


def ok(payload: "dict[str, Any]", shrink: "tuple[tuple[str, ...], ...]") -> "dict[str, Any]":
    text = json.dumps(_fit(payload, shrink), ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def tool_error(text: str) -> "dict[str, Any]":
    return {"content": [{"type": "text", "text": text}], "isError": True}


def refused(task_id: str, reason: str) -> "dict[str, Any]":
    return {"task_id": task_id, "started": False, "reason": reason}


def child_env() -> "dict[str, str]":
    """A clean environment for the attempt process: nothing the coordinator's session carried, except the home directory
    and, only when the server itself was started with the package's parent directory on PYTHONPATH, that same entry."""
    env = {"PATH": CHILD_PATH, "PYTHONDONTWRITEBYTECODE": "1"}
    home = os.environ.get("HOME")
    if home:
        env["HOME"] = home
    package_parent = os.path.dirname(os.path.dirname(os.path.realpath(par.__file__)))
    entries = [e for e in os.environ.get("PYTHONPATH", "").split(os.pathsep) if e]
    if any(os.path.realpath(e) == package_parent for e in entries):
        env["PYTHONPATH"] = package_parent
    return env
