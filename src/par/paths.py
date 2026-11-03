"""Runtime data directory: location, inspection and controlled creation.

Default: ``~/Library/Application Support/PersonalAgentRuntime`` (mode 0700). From P2 on it holds the
execution ledger and per-task evidence; it never holds project truth. A directory that already exists
with the wrong owner/mode/type is reported, never "fixed": it may not be ours.
"""
from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from par.core.policy import PolicyViolation, WriteGuard, protected_roots

APP_DIR_NAME = "PersonalAgentRuntime"
ENV_DATA_DIR = "PAR_DATA_DIR"
DIR_MODE = 0o700
SUBDIRS = ("config",)  # tasks/, workspaces/, cache/, exports/ arrive with the phase that needs them


def default_data_dir(home: "str | os.PathLike[str] | None" = None,
                     env: "Mapping[str, str] | None" = None) -> Path:
    env = os.environ if env is None else env
    override = env.get(ENV_DATA_DIR)
    if override:
        p = Path(os.path.expanduser(override))
        if not p.is_absolute():
            raise PolicyViolation(f"{ENV_DATA_DIR} must be an absolute path")
        return p
    base = Path(home) if home is not None else Path.home()
    return base / "Library" / "Application Support" / APP_DIR_NAME


@dataclass(frozen=True)
class DataDirStatus:
    path: Path
    exists: bool
    mode: "int | None" = None
    free_bytes: "int | None" = None
    missing_subdirs: "tuple[str, ...]" = ()
    problems: "tuple[str, ...]" = ()

    @property
    def usable(self) -> bool:
        return self.exists and not self.problems


def _free_bytes(path: Path) -> "int | None":
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def inspect_data_dir(path: "str | os.PathLike[str]") -> DataDirStatus:
    """Read-only. Never follows a symlink at the final component."""
    p = Path(path)
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return DataDirStatus(path=p, exists=False, free_bytes=_free_bytes(p))
    problems = []
    if stat.S_ISLNK(st.st_mode):
        problems.append("is a symlink")
    elif not stat.S_ISDIR(st.st_mode):
        problems.append("is not a directory")
    mode = stat.S_IMODE(st.st_mode)
    if mode != DIR_MODE:
        problems.append(f"mode is {mode:04o}, expected {DIR_MODE:04o}")
    if st.st_uid != os.getuid():
        problems.append("is not owned by the current user")
    missing = ()
    if not problems:
        missing = tuple(s for s in SUBDIRS if not (p / s).is_dir())
    return DataDirStatus(path=p, exists=True, mode=mode, free_bytes=_free_bytes(p),
                         missing_subdirs=missing, problems=tuple(problems))


def _mkdir_private(p: Path) -> None:
    try:
        os.mkdir(p, DIR_MODE)
    except FileExistsError:
        pass
    os.chmod(p, DIR_MODE)  # umask-proof; only ever applied to a directory we just created


def ensure_data_dir(path: "str | os.PathLike[str]",
                    protected: "Iterable[Path] | None" = None) -> DataDirStatus:
    """Create the data directory (0700) and its subdirectories, or raise ``PolicyViolation``."""
    p = Path(os.path.expanduser(os.fspath(path)))
    guard = WriteGuard(allowed_roots=(p,), protected=tuple(protected_roots() if protected is None else protected))
    guard.check(p)
    if not p.parent.is_dir():
        raise PolicyViolation("parent directory of the data directory does not exist")
    status = inspect_data_dir(p)
    if status.exists:
        if status.problems:
            raise PolicyViolation("data directory exists but is not safe to use: " + "; ".join(status.problems))
    else:
        _mkdir_private(p)
    for sub in SUBDIRS:
        sub_path = p / sub
        if not sub_path.is_dir():
            _mkdir_private(sub_path)
    return inspect_data_dir(p)
