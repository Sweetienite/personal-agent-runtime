"""Path policy: where the Runtime may write, and which paths are never read, logged or committed.

Pure path logic. Nothing here opens, reads or lists a file: the checks work on names and on
``os.path.realpath`` resolution only.

Two independent guards:

* ``is_sensitive_path``  - deny-by-name for credential/PII locations (applies anywhere).
* ``WriteGuard``         - a write is allowed only inside explicitly allowed roots, never inside a
                           protected location (existing Harness, vendor tool configs, shell rc files).
"""
from __future__ import annotations

import fnmatch
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterable

from par.core.redact import redact_text


class PolicyViolation(Exception):
    """A requested path or action is outside what the Runtime is allowed to do."""


def _canon(text: str) -> str:
    # macOS file systems are case-insensitive and may store names in NFD: compare on NFC + casefold.
    return unicodedata.normalize("NFC", text).casefold()


# --- sensitive names -----------------------------------------------------------------------------
_SENSITIVE_FILE_GLOBS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keychain", "*.keychain-db", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    "auth.json", "oauth*.json", "*oauth*token*", "google_accounts.json",
    "application_default_credentials.json", "credentials", "credentials.*", ".credentials*",
    ".netrc", ".npmrc", ".pypirc", "*.token", "token.json", "secrets.*", "*.secret", "*.secrets",
)
_NOT_SENSITIVE_NAMES = {".env.example", ".env.sample", ".env.template"}
_SENSITIVE_DIR_NAMES = {
    ".ssh", ".gnupg", ".aws", ".kube", ".deploy", "keychains", "secrets", "_by_email", "_pending_codes",
}
_SENSITIVE_SEQUENCES = (("runtime", "accounts"),)

_GLOBS = tuple(_canon(g) for g in _SENSITIVE_FILE_GLOBS)
_EXAMPLES = {_canon(n) for n in _NOT_SENSITIVE_NAMES}
_DIRS = {_canon(n) for n in _SENSITIVE_DIR_NAMES}
_SEQS = tuple(tuple(_canon(p) for p in seq) for seq in _SENSITIVE_SEQUENCES)


def is_sensitive_path(path: "str | os.PathLike[str]") -> bool:
    """True when the path *name* looks like a credential / PII location. Never touches the file system."""
    parts = [_canon(p) for p in PurePath(os.fspath(path)).parts if p not in ("/", "")]
    if not parts:
        return False
    name = parts[-1]
    if name in _EXAMPLES:
        return False
    if any(fnmatch.fnmatchcase(name, g) for g in _GLOBS):
        return True
    if any(p in _DIRS for p in parts):
        return True
    return any(tuple(parts[i:i + len(s)]) == s for s in _SEQS for i in range(len(parts) - len(s) + 1))


def count_sensitive(paths: Iterable[str]) -> int:
    """How many of ``paths`` are sensitive. Use this instead of printing such paths."""
    return sum(1 for p in paths if is_sensitive_path(p))


# --- protected locations ---------------------------------------------------------------------------
_PROTECTED_HOME_RELATIVE = (
    # vendor tool state and credentials: owned by each tool, never by the Runtime
    ".claude", ".claude.json", ".codex", ".gemini", ".workbuddy", ".codebuddy", ".copilot", ".dsh",
    ".config/kilo", ".local/share/kilo", ".config/github-copilot", ".config/gh",
    ".ssh", ".gnupg", ".aws", ".kube", ".docker",
    # shell and git user configuration
    ".zshrc", ".zprofile", ".zshenv", ".zlogin", ".bashrc", ".bash_profile", ".profile", ".gitconfig",
    # places where a planted file would execute or persist
    ".local/bin", "Library/LaunchAgents", "Library/Keychains", ".Trash",
    # the existing Harness: Core, its audit history and backups (read-only for the Runtime)
    "Documents/AI_Harness_Core", "Documents/AI_Harness_History", "ai-harness-backups",
)


def protected_roots(home: "str | os.PathLike[str] | None" = None,
                    extra: Iterable["str | os.PathLike[str]"] = ()) -> tuple[Path, ...]:
    """Locations the Runtime must never write to. ``extra`` adds e.g. a relocated Core or project roots."""
    h = Path(os.path.realpath(home if home is not None else Path.home()))
    roots = [h / rel for rel in _PROTECTED_HOME_RELATIVE]
    roots += [Path(os.path.realpath(os.path.expanduser(os.fspath(e)))) for e in extra]
    return tuple(roots)


def inside_protected(path: "str | os.PathLike[str]", roots: Iterable[Path]) -> "Path | None":
    """The protected root that contains ``path`` (compared case-insensitively on NFC), else None."""
    p = Path(os.path.realpath(os.path.expanduser(os.fspath(path))))
    for root in roots:
        if _is_within_canon(p, Path(os.path.realpath(root))):
            return root
    return None


def _is_within(child: Path, root: Path) -> bool:
    return child == root or root in child.parents


def _is_within_canon(child: Path, root: Path) -> bool:
    c, r = _canon(str(child)), _canon(str(root)).rstrip("/")
    return c == r or c.startswith(r + "/")


def _short(p: Path) -> str:
    return redact_text(str(p), home=str(Path.home()))


@dataclass(frozen=True)
class WriteGuard:
    """Decides whether a path may be written. ``check`` returns the resolved path or raises."""

    allowed_roots: tuple[Path, ...]
    protected: tuple[Path, ...] = ()

    @staticmethod
    def resolve(path: "str | os.PathLike[str]") -> Path:
        return Path(os.path.realpath(os.path.expanduser(os.fspath(path))))

    def check(self, path: "str | os.PathLike[str]") -> Path:
        p = self.resolve(path)
        roots = [self.resolve(r) for r in self.allowed_roots]
        if not any(_is_within(p, r) for r in roots):
            raise PolicyViolation(f"path is outside the allowed roots: {_short(p)}")
        for prot in self.protected:
            if _is_within_canon(p, self.resolve(prot)):
                raise PolicyViolation(f"path is inside a protected location: {_short(self.resolve(prot))}")
        if is_sensitive_path(p):
            raise PolicyViolation("path matches a sensitive-path rule")  # the name itself is not echoed
        return p
