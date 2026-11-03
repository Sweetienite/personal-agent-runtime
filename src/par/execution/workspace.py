"""Per-attempt working copies, and judging what a Worker did to its copy - without trusting its ``.git``.

Layout of an attempt (all below the private data directory, mode 0700)::

    workspace/   the Worker's copy (R0: an empty folder)
    ref/         a pristine copy the Kernel keeps for itself; the Worker never sees it
    verify/      ref + the Worker's *allowed* changes; patch is cut here and the checks run here

The Worker's result is found by comparing ``workspace/`` with ``ref/`` as plain files (symlinks are never
followed). That comparison is the only thing the Kernel believes about what the Worker changed.
"""
from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from par.core.contract import forbidden_reason, path_allowed
from par.core.policy import is_sensitive_path
from par.execution import gitops

MAX_FILES = 200_000
MAX_CHANGED_FILES = 500
MAX_FILE_BYTES = 20 * 1024 * 1024
_CHUNK = 1 << 20
NOISE_NAMES = frozenset({".DS_Store"})
MAX_IMPORT_BYTES = 1024 * 1024 * 1024
# Folders that desktop assistants create next to the files they open (their own settings, indexes, history). When a person brings a folder back from
# one of them, NEW entries under these names are not the Worker's work and are ignored (listed as noise); a change to a tracked file is still judged.
APP_NOISE_DIRS = frozenset({".trae", ".trae-cn", ".workbuddy", ".codebuddy", ".codebuddycn", ".vscode", ".idea", ".cursor", ".windsurf", ".cline"})


class TreeTooLarge(Exception):
    pass


class TooManyChanges(Exception):
    pass


class EscapeAttempt(Exception):
    """A path would land outside the directory it must stay inside."""


class ImportRefused(Exception):
    """A folder a person brought back cannot be taken in (the reason is shown to them)."""


@dataclass(frozen=True)
class Entry:
    kind: str                       # file | symlink | dir | other
    size: int = 0
    exec: bool = False
    link: "str | None" = None


@dataclass(frozen=True)
class Change:
    path: str
    status: str                     # A added | M modified | D deleted | T type changed
    old: "Entry | None" = None
    new: "Entry | None" = None


@dataclass(frozen=True)
class Violation:
    path: str
    status: str
    reason: str
    protected: bool = False         # a protected or sensitive-looking path (counts towards boundary.sensitive)


@dataclass
class Classification:
    allowed: "list[Change]" = field(default_factory=list)
    violations: "list[Violation]" = field(default_factory=list)
    noise: "list[str]" = field(default_factory=list)

    @property
    def protected_count(self) -> int:
        return sum(1 for v in self.violations if v.protected)


# --- scanning and comparing -----------------------------------------------------------------------------------
def scan_tree(root: "str | os.PathLike[str]", *, max_files: int = MAX_FILES) -> "dict[str, Entry]":
    """Every non-directory entry below ``root`` keyed by its relative path. Never follows a symlink.

    The top-level ``.git`` is skipped (it is repository metadata, not work); a ``.git`` anywhere deeper is recorded as an
    entry so it can be flagged. Empty directories are invisible, like in git.
    """
    root = Path(root)
    out: "dict[str, Entry]" = {}
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        with os.scandir(root / rel_dir if rel_dir else root) as it:
            for de in it:
                rel = f"{rel_dir}/{de.name}" if rel_dir else de.name
                if rel == ".git":
                    continue
                st = de.stat(follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    out[rel] = Entry("symlink", link=os.readlink(de.path))
                elif stat.S_ISDIR(st.st_mode):
                    if de.name == ".git":
                        out[rel] = Entry("dir")
                    else:
                        stack.append(rel)
                elif stat.S_ISREG(st.st_mode):
                    out[rel] = Entry("file", st.st_size, bool(st.st_mode & 0o111))
                else:
                    out[rel] = Entry("other")
                if len(out) > max_files:
                    raise TreeTooLarge(f"more than {max_files} files")
    return out


def _same_bytes(a: Path, b: Path) -> bool:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(_CHUNK), fb.read(_CHUNK)
            if ca != cb:
                return False
            if not ca:
                return True


def diff_trees(ref_root: "str | os.PathLike[str]", ref: "dict[str, Entry]",
               work_root: "str | os.PathLike[str]", work: "dict[str, Entry]") -> "list[Change]":
    ref_root, work_root = Path(ref_root), Path(work_root)
    changes: "list[Change]" = []
    for path in sorted(set(ref) | set(work)):
        a, b = ref.get(path), work.get(path)
        if a is None:
            changes.append(Change(path, "A", None, b))
        elif b is None:
            changes.append(Change(path, "D", a, None))
        elif a.kind != b.kind:
            changes.append(Change(path, "T", a, b))
        elif a.kind == "symlink":
            if a.link != b.link:
                changes.append(Change(path, "M", a, b))
        elif a.kind == "file":
            if a.size != b.size or a.exec != b.exec or not _same_bytes(ref_root / path, work_root / path):
                changes.append(Change(path, "M", a, b))
    return changes


# --- judging -------------------------------------------------------------------------------------------------------
def _unsafe_name(path: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 or 0xD800 <= ord(ch) <= 0xDFFF for ch in path)


def is_noise(path: str, noise_dirs: "Iterable[str]" = ()) -> bool:
    parts = path.split("/")
    return "__pycache__" in parts or parts[-1].endswith(".pyc") or parts[-1] in NOISE_NAMES or parts[0] in set(noise_dirs)


def classify(changes: "Iterable[Change]", *, allowed_paths: "Iterable[str]", writes: bool,
             ignored: "Iterable[str]" = (), max_file_bytes: int = MAX_FILE_BYTES,
             max_changed: int = MAX_CHANGED_FILES, noise_dirs: "Iterable[str]" = ()) -> Classification:
    allowed_paths, ignored = tuple(allowed_paths), set(ignored)
    result = Classification()
    for c in changes:
        reason = forbidden_reason(c.path)
        if _unsafe_name(c.path):
            result.violations.append(Violation(c.path.encode("unicode_escape").decode("ascii"), c.status, "unsafe file name"))
        elif reason:
            result.violations.append(Violation(c.path, c.status, reason, protected=True))
        elif c.new is not None and c.new.kind != "file":
            result.violations.append(Violation(c.path, c.status, {"symlink": "a symlink", "dir": "a nested repository folder"}.get(c.new.kind, "a special file")
                                               + " is never accepted"))
        elif c.status == "A" and (is_noise(c.path, noise_dirs) or c.path in ignored):
            result.noise.append(c.path)
        elif not writes:
            result.violations.append(Violation(c.path, c.status, "a read-only task changed a file"))
        elif not path_allowed(c.path, allowed_paths):
            result.violations.append(Violation(c.path, c.status, "outside the allowed paths"))
        elif c.new is not None and c.new.size > max_file_bytes:
            result.violations.append(Violation(c.path, c.status, f"larger than {max_file_bytes // (1024 * 1024)} MiB"))
        else:
            result.allowed.append(c)
    if len(result.allowed) + len(result.violations) > max_changed:
        raise TooManyChanges(f"more than {max_changed} changed files")
    return result


# --- building the verification tree ------------------------------------------------------------------------------
def _inside(root: Path, target: Path) -> None:
    real_root = os.path.realpath(root)
    real = os.path.realpath(target)
    if real != real_root and not real.startswith(real_root + os.sep):
        raise EscapeAttempt(str(target))


def _copy_regular(src: Path, dst: Path, executable: bool) -> None:
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise EscapeAttempt(f"{src} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fin, open(dst, "wb") as fout:
            shutil.copyfileobj(fin, fout, _CHUNK)
    finally:
        os.close(fd)
    os.chmod(dst, 0o755 if executable else 0o644)


def build_verify_tree(ref_root: "str | os.PathLike[str]", verify_root: "str | os.PathLike[str]",
                      work_root: "str | os.PathLike[str]", allowed: "Iterable[Change]") -> None:
    """``verify`` = a copy of ``ref`` with the allowed changes applied (deletions first, so a path can change kind)."""
    ref_root, verify_root, work_root = Path(ref_root), Path(verify_root), Path(work_root)
    verify_root.mkdir(mode=0o700)                                    # private from the first instant (copytree would use the umask)
    shutil.copytree(ref_root, verify_root, symlinks=True, dirs_exist_ok=True)
    os.chmod(verify_root, 0o700)
    changes = sorted(allowed, key=lambda c: (c.status != "D" and c.status != "T", c.path))
    for c in changes:
        target = verify_root / c.path
        _inside(verify_root, target.parent if target.parent != verify_root else verify_root)
        if c.status in ("D", "T") or target.is_symlink():
            if os.path.lexists(target):
                os.unlink(target)
        if c.status == "D":
            continue
        assert c.new is not None and c.new.kind == "file"
        target.parent.mkdir(parents=True, exist_ok=True)
        _inside(verify_root, target.parent)
        _copy_regular(work_root / c.path, target, c.new.exec)


def tree_equal(a: "str | os.PathLike[str]", b: "str | os.PathLike[str]") -> "list[str]":
    """Paths that differ between two trees (empty = identical). Used to prove a patch reproduces the verified tree."""
    ea, eb = scan_tree(a), scan_tree(b)
    return [c.path for c in diff_trees(a, ea, b, eb)]


# --- creating the attempt's directories ------------------------------------------------------------------------
@dataclass(frozen=True)
class Prepared:
    workspace: Path
    ref: "Path | None"
    excluded: "tuple[str, ...]" = ()


def stage_import(src: "str | os.PathLike[str]", dst: "str | os.PathLike[str]", *, max_bytes: int = MAX_IMPORT_BYTES) -> None:
    """A private, stable snapshot of a folder a person brought back (the app that made it may still be open and writing to it).

    Nothing is followed: a link is copied as a link and judged later (it is never accepted). Refused before anything is copied: a folder that is not a real
    directory, one with special files (a pipe would block the copy), one with sensitive-looking files (their contents are not read) or one that is too big."""
    src, dst = Path(src), Path(dst)
    if src.is_symlink() or not src.is_dir():
        raise ImportRefused("the result folder must be a real directory (not a link)")
    entries = scan_tree(src)
    special = sorted(p for p, e in entries.items() if e.kind == "other")
    if special:
        raise ImportRefused("the folder contains special files (pipes, sockets or devices), which are never accepted: " + ", ".join(special[:5]))
    sensitive = sorted(p for p in entries if is_sensitive_path(p))
    if sensitive:
        raise ImportRefused("the folder contains sensitive-looking files (their contents were not read); take them out first: " + ", ".join(sensitive[:5]))
    if sum(e.size for e in entries.values() if e.kind == "file") > max_bytes:
        raise ImportRefused(f"the folder is larger than {max_bytes // (1024 * 1024)} MiB")
    dst.mkdir(mode=0o700)
    top = os.fspath(src)
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True, ignore=lambda directory, names: {".git"} if directory == top else set())
    os.chmod(dst, 0o700)


def apply_seed(git: "gitops.Git", workspace: Path, patch: bytes) -> None:
    """Put a previous attempt's verified changes (a patch the Kernel cut itself) into this attempt's copy, before the Worker starts.

    Checked first, applied second; ``git apply`` is told not to climb out of the attempt folder, so a copy without its own ``.git`` is never
    mistaken for part of some enclosing repository."""
    ceiling = Path(workspace).parent
    gitops.apply_patch(git, workspace, patch, check_only=True, ceiling=ceiling)
    gitops.apply_patch(git, workspace, patch, ceiling=ceiling)


def prepare(git: "gitops.Git", *, source: "Path | None", base_commit: "str | None", attempt_dir: Path, risk: str,
            with_git: bool = True) -> Prepared:
    """R0 gets an empty folder (no project files at all). R1/R2 get a private clone at the recorded base commit.

    ``with_git=False`` gives the Worker a plain copy WITHOUT the top-level ``.git``. The Kernel never uses the Worker's
    repository anyway (it compares plain files against its own ``ref/``), and some CLIs treat a writable git workspace as
    something to remember in the user's own configuration (Codex adds a trust entry to ~/.codex/config.toml)."""
    attempt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)   # (the Kernel has already made it, privately)
    workspace = attempt_dir / "workspace"
    if risk == "R0":
        workspace.mkdir(mode=0o700)
        return Prepared(workspace, None)
    if source is None or base_commit is None:
        raise gitops.GitError("this project has no git base to work from")
    ref = attempt_dir / "ref"
    excluded = gitops.clone_at(git, source, ref, base_commit)
    workspace.mkdir(mode=0o700)                                      # a crash mid-copy must never leave it open to others
    top = os.fspath(ref)
    skip = None if with_git else (lambda directory, names: {".git"} if directory == top else set())
    shutil.copytree(ref, workspace, symlinks=True, dirs_exist_ok=True, ignore=skip)
    os.chmod(workspace, 0o700)
    return Prepared(workspace, ref, tuple(excluded))
