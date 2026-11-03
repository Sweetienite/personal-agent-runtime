"""Redaction of secrets and personal identifiers before anything is stored or displayed.

Pure functions, no I/O. Every log/event writer and every UI/CLI surface must pass text through
``redact_text`` / ``redact_obj`` first. Redaction is defence in depth: the primary rule is to never
read secrets in the first place.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# A key name is sensitive when it contains one of these words anywhere, or ends in "token".
# ("max_tokens", "token_count" and "tokenizer" are therefore NOT sensitive.)
_SENSITIVE_WORDS = (
    "apikey", "api_key", "api-key", "secret", "password", "passwd", "credential",
    "authorization", "cookie", "private_key", "private-key", "privatekey",
)
_PLACEHOLDER_VALUES = {"", "true", "false", "null", "none", "~", "***"}


def is_sensitive_key(name: str) -> bool:
    low = str(name).strip().strip("\"'").lower()
    return low.endswith("token") or any(w in low for w in _SENSITIVE_WORDS)


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip("\"'")
    low = v.lower()
    return (
        low in _PLACEHOLDER_VALUES
        or v.startswith("[REDACTED")
        or v.startswith("$")          # environment reference, not the secret itself
        or (v.startswith("<") and v.endswith(">"))
    )


# Every pattern below must stay LINEAR in the length of the text: the input is output a Worker produced, so a hostile or merely odd line (a
# 200 KB run of letters, a thousand "-----BEGIN" markers) must not be able to stall the Kernel. Unbounded quantifiers that restart at every position
# (an e-mail local part, an assignment key, a lookahead that scans the rest of the run) are quadratic; they are bounded or done by hand here.
# tests/unit/test_redact_perf.py feeds each of them adversarial text.
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z ]{0,40}PRIVATE KEY-----")
_PEM_OPEN_TAIL = 8192      # a block with no end marker (a truncated log line): the marker and the next 8 KB are the key
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,4096}\.[A-Za-z0-9_-]{8,4096}\.[A-Za-z0-9_-]{8,4096}")
_VENDOR_KEYS = (
    re.compile(r"\bsk-(?:ant-|proj-|or-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)(\s+)[A-Za-z0-9._~+/=-]{12,}")
# Header-style lines whose whole remainder is secret (a value can contain spaces or ';').
_HEADER_LINE = re.compile(r"(?im)^(?P<head>[ \t]*[\"']?(?:proxy-)?authorization[\"']?[ \t]*[:=][ \t]*|[ \t]*[\"']?(?:set-)?cookie[\"']?[ \t]*[:=][ \t]*)(?P<val>[^\r\n]+)")
_ASSIGN = re.compile(
    r"""(?P<key>[A-Za-z_][A-Za-z0-9_.-]{0,63})(?P<q>["']?)(?P<sep>[ \t]{0,64}[:=][ \t]{0,64})"""
    r"""(?P<val>"[^"\n]*"|'[^'\n]*'|[^\s,;&]+)"""
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,63}")
# Opaque mixed-case+digit blobs. Git SHAs (hex only) and UUIDs (no upper case) do not match. A run is found in one pass and its character
# classes are checked afterwards (a lookahead at every position would rescan the rest of the run each time).
_OPAQUE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
_UPPER, _LOWER, _DIGIT = re.compile(r"[A-Z]"), re.compile(r"[a-z]"), re.compile(r"\d")


def _opaque_sub(m: "re.Match[str]") -> str:
    run = m.group(0)
    return "[REDACTED:token]" if _UPPER.search(run) and _LOWER.search(run) and _DIGIT.search(run) else run


def _redact_pem(text: str) -> str:
    """Private-key blocks, found with one forward pass: each block is cut from its marker to its end marker (or, when there is none, to the end of
    what could still be key material)."""
    if "PRIVATE KEY-----" not in text:
        return text
    out: "list[str]" = []
    pos = 0
    while True:
        begin = _PEM_BEGIN.search(text, pos)
        if begin is None:
            break
        end = _PEM_END.search(text, begin.end())
        out.append(text[pos:begin.start()])
        out.append("[REDACTED:private-key]")
        if end is None:
            pos = min(len(text), begin.end() + _PEM_OPEN_TAIL)
            break
        pos = end.end()
    out.append(text[pos:])
    return "".join(out)


def _assign_sub(m: "re.Match[str]") -> str:
    if is_sensitive_key(m.group("key")) and not _is_placeholder(m.group("val")):
        return f'{m.group("key")}{m.group("q")}{m.group("sep")}{REDACTED}'
    return m.group(0)


def redact_text(text: str, *, home: str | None = None) -> str:
    """Return ``text`` with secrets and e-mail addresses masked. Idempotent.

    When ``home`` is given, that directory prefix is shortened to ``~``.
    """
    if not isinstance(text, str):
        return text
    out = _redact_pem(text)
    out = _JWT.sub("[REDACTED:jwt]", out)
    for pat in _VENDOR_KEYS:
        out = pat.sub("[REDACTED:api-key]", out)
    out = _BEARER.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:token]", out)
    out = _HEADER_LINE.sub(lambda m: m.group("head") + REDACTED if not _is_placeholder(m.group("val")) else m.group(0), out)
    out = _ASSIGN.sub(_assign_sub, out)
    out = _EMAIL.sub("[REDACTED:email]", out)
    out = _OPAQUE.sub(_opaque_sub, out)
    if home:
        # Shorten only at a name boundary: "/Users/me2" and "/Users/me.old" are different names,
        # while a sentence-final "/Users/me." is still the home directory.
        out = re.sub(re.escape(home) + r"(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_-])", "~", out)
    return out


def contains_secret(text: str) -> bool:
    return isinstance(text, str) and redact_text(text) != text


def redact_obj(obj: Any, *, home: str | None = None) -> Any:
    """Recursively redact a JSON-like structure. Values under sensitive keys are always masked."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if is_sensitive_key(k) and v is not None and not isinstance(v, bool) and not (
                isinstance(v, str) and _is_placeholder(v)
            ):
                out[k] = REDACTED
            else:
                out[k] = redact_obj(v, home=home)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, home=home) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj, home=home)
    return obj
