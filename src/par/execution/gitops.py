"""Git as the Kernel uses it.

Two rules keep this safe:

* the Kernel runs git only inside directories it created itself (clones it made), never inside a
  Worker's working copy: a Worker can rewrite ``.git/config`` (fsmonitor, diff drivers, hooks) and the next
  git command would run its code. Worker output is compared as plain files instead (see ``workspace``);
* every git call starts from a clean environment - no user or system config, no hooks, no pager, no prompts,
  literal pathspecs - so results do not depend on whoever's machine this is.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

from par.core.policy import is_sensitive_path
from par.core.redact import redact_text

GIT_CANDIDATES = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_FLAGS = (
    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
    "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotepath=off", "-c", "gc.auto=0",
    "-c", "diff.renames=false", "-c", "protocol.file.allow=always", "-c", "advice.detachedHead=false",
)
BATCH = 100


class GitError(Exception):
    def __init__(self, message: str, returncode: "int | None" = None) -> None:
        super().__init__(message)
        self.returncode = returncode


def find_git() -> "str | None":
    return next((c for c in GIT_CANDIDATES if os.access(c, os.X_OK)), None)


class Git:
    def __init__(self, scratch: "str | os.PathLike[str]", *, exe: "str | None" = None, timeout: float = 120.0) -> None:
        self.exe = exe or find_git()
        if not self.exe:
            raise GitError(f"git was not found in {', '.join(GIT_CANDIDATES)}")
        self.scratch = Path(scratch)
        self.timeout = timeout

    def env(self) -> "dict[str, str]":
        return {
            "PATH": SAFE_PATH, "HOME": str(self.scratch), "LC_ALL": "C", "LANG": "C",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_LITERAL_PATHSPECS": "1",
            "GIT_PAGER": "cat", "PAGER": "cat", "GIT_EDITOR": ":", "GIT_ASKPASS": "/usr/bin/false",
            "GIT_AUTHOR_NAME": "par-kernel", "GIT_AUTHOR_EMAIL": "kernel@par.invalid",
            "GIT_COMMITTER_NAME": "par-kernel", "GIT_COMMITTER_EMAIL": "kernel@par.invalid",
        }

    def run(self, cwd: "str | os.PathLike[str]", *args: str, input_bytes: "bytes | None" = None,
            ok: "tuple[int, ...]" = (0,), env_extra: "dict[str, str] | None" = None) -> bytes:
        try:
            proc = subprocess.run([self.exe, *_FLAGS, *args], cwd=cwd, env={**self.env(), **(env_extra or {})}, input=input_bytes,
                                  capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} timed out after {self.timeout:.0f}s") from None
        except OSError as exc:
            raise GitError(f"git could not be started: {exc.strerror or exc}") from None
        if proc.returncode not in ok:
            detail = redact_text(proc.stderr.decode("utf-8", errors="replace").strip())[:500]
            raise GitError(f"git {args[0]} failed ({proc.returncode}): {detail}", proc.returncode)
        return proc.stdout

    def text(self, cwd: "str | os.PathLike[str]", *args: str) -> str:
        return self.run(cwd, *args).decode("utf-8", errors="replace").strip()


def resolve_commit(git: Git, repo: "str | os.PathLike[str]", rev: str = "HEAD") -> str:
    out = git.text(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if not COMMIT.match(out):
        raise GitError(f"could not resolve {rev!r} to a commit")
    return out


def tracked_files(git: Git, repo: "str | os.PathLike[str]") -> "list[str]":
    out = git.run(repo, "ls-files", "-z")
    return [os.fsdecode(p) for p in out.split(b"\0") if p]


def exclude_sensitive(git: Git, repo: "str | os.PathLike[str]") -> "list[str]":
    """Take tracked sensitive-looking files out of a Kernel-made working copy (and tell git to leave them be).

    A committed ``.env`` must not become readable by a Worker just because it sits in history.
    """
    hidden = [p for p in tracked_files(git, repo) if is_sensitive_path(p)]
    for i in range(0, len(hidden), BATCH):
        chunk = hidden[i:i + BATCH]
        git.run(repo, "update-index", "--skip-worktree", "--", *chunk)
        for rel in chunk:
            try:
                os.unlink(Path(repo) / rel)
            except FileNotFoundError:
                pass
    return hidden


def clone_at(git: Git, source: "str | os.PathLike[str]", dest: "str | os.PathLike[str]", commit: str) -> "list[str]":
    """A private, hook-free, remote-free copy of ``source`` at exactly ``commit``. Returns the files it left out."""
    if not COMMIT.match(commit):
        raise GitError("the base must be a full 40-character commit id")
    git.run(source, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")      # fail early and clearly
    Path(dest).mkdir(mode=0o700)                                                     # private from the first instant; git clones into an empty folder
    git.run(git.scratch, "clone", "--quiet", "--no-hardlinks", "--no-checkout", "--", str(source), str(dest))
    git.run(dest, "checkout", "--quiet", "--detach", commit)
    git.run(dest, "remote", "remove", "origin")
    git.run(dest, "config", "core.hooksPath", "/dev/null")
    return exclude_sensitive(git, dest)


def ignored_among(git: Git, repo: "str | os.PathLike[str]", paths: "Iterable[str]") -> "set[str]":
    """Which of these (untracked) paths the repository's own ignore rules would ignore."""
    wanted = [p for p in paths if not p.startswith(":")]       # a leading ":" would be read as pathspec magic
    if not wanted:
        return set()
    # check-ignore takes paths, not pathspecs, and rejects the literal-pathspec switch the other commands use
    out = git.run(repo, "check-ignore", "-z", "--stdin", input_bytes=b"\0".join(os.fsencode(p) for p in wanted) + b"\0",
                  ok=(0, 1), env_extra={"GIT_LITERAL_PATHSPECS": "0"})
    return {os.fsdecode(p) for p in out.split(b"\0") if p}


def add_intent(git: Git, repo: "str | os.PathLike[str]", paths: "list[str]") -> None:
    for i in range(0, len(paths), BATCH):
        git.run(repo, "add", "-N", "--", *paths[i:i + BATCH])


def diff_patch(git: Git, repo: "str | os.PathLike[str]", base: str) -> bytes:
    """Everything that differs from ``base`` (new files must already be intent-to-add), as an applyable patch."""
    return git.run(repo, "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames", base, "--")


def diff_numstat(git: Git, repo: "str | os.PathLike[str]", base: str) -> "dict[str, int]":
    out = git.run(repo, "diff", "--numstat", "-z", "--no-ext-diff", "--no-textconv", "--no-renames", base, "--")
    files = additions = deletions = 0
    for record in out.split(b"\0"):
        if not record:
            continue
        add, _, rest = record.partition(b"\t")
        dele, _, _path = rest.partition(b"\t")
        files += 1
        additions += int(add) if add.isdigit() else 0
        deletions += int(dele) if dele.isdigit() else 0
    return {"files": files, "additions": additions, "deletions": deletions}


def apply_patch(git: Git, repo: "str | os.PathLike[str]", patch: bytes, *, check_only: bool = False,
                ceiling: "str | os.PathLike[str] | None" = None) -> None:
    """Apply ``patch`` to the files in ``repo``. ``ceiling``: a folder git must not climb into when looking for a repository - for a plain copy
    without its own ``.git`` it keeps ``git apply`` from treating some enclosing repository as the thing being patched."""
    args = ["apply", "--binary", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    extra = {"GIT_CEILING_DIRECTORIES": os.fspath(ceiling)} if ceiling is not None else None
    git.run(repo, *args, "-", input_bytes=patch, env_extra=extra)


def patch_paths(git: Git, patch: bytes) -> "list[str]":
    """The paths a patch would touch (read from the patch itself, nothing is applied or written)."""
    out = git.run(git.scratch, "apply", "--numstat", "-z", "-", input_bytes=patch)
    paths = []
    for record in out.split(b"\0"):
        if record:
            paths.append(os.fsdecode(record.split(b"\t", 2)[2]))
    return paths
