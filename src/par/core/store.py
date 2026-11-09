"""SQLite execution ledger: the Kernel's record of what happened.

It stores task facts (status, attempts, events, approvals, locks, provider health), never project
knowledge. The important rules are enforced by the database itself, with triggers, so a buggy writer or
another process cannot break them:

* ``events`` is append-only; tasks and attempts are never deleted;
* an ended attempt is immutable, and an attempt's state only moves forward;
* a task's status may only change along the ``transitions`` table (mirrored from ``core.states``);
* a task row's version advances by exactly one on every update, and its identity and contract are frozen;
* a decided approval is immutable.

Durability: WAL with ``synchronous=FULL``, so a committed transaction survives a power cut. Every state
change and its event are written in the same transaction.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from par.core.clock import Clock
from par.core.redact import redact_obj
from par.core.states import TaskStatus, check_transition, transition_rows, transitions_digest

SCHEMA_VERSION = 1
DB_NAME = "runtime.sqlite3"
_STATUS_LIST = ",".join(f"'{s.value}'" for s in TaskStatus)


class LedgerError(Exception):
    pass


class VersionConflict(LedgerError):
    """Someone else changed the task since it was read."""


class IdempotencyConflict(LedgerError):
    """The same idempotency key was reused for a different contract."""


class LockBusy(LedgerError):
    def __init__(self, holder: "dict[str, Any]") -> None:
        super().__init__(f"project is busy: attempt {holder.get('task_id')}#{holder.get('attempt_n')} holds the lock")
        self.holder = holder


class NotFound(LedgerError):
    pass


# Additive, so an older build can still open this ledger: it ignores the table. Candidate rows are immutable
# audit records (a failed or verified integration is never edited); `schema` lets a later shape be told apart.
CANDIDATE_DDL = """
CREATE TABLE IF NOT EXISTS candidates (
  candidate_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  base_commit TEXT NOT NULL,
  commit_sha TEXT,
  status TEXT NOT NULL CHECK (status IN ('VERIFIED','FAILED')),
  tasks_json TEXT NOT NULL,
  requested_json TEXT NOT NULL DEFAULT '[]',
  checks_json TEXT NOT NULL,
  artifact_dir TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  schema INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  verified_at TEXT
);
CREATE INDEX IF NOT EXISTS candidates_by_project ON candidates(project_id, created_at);
"""


GRANT_DDL = """
CREATE TABLE IF NOT EXISTS coordinator_grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  coordinator TEXT NOT NULL,
  actions_json TEXT NOT NULL,
  approval_kinds_json TEXT NOT NULL DEFAULT '[]',
  project_id TEXT,
  risk_tiers_json TEXT NOT NULL DEFAULT '["R0","R1","R2"]',
  expires_at TEXT,
  revoked_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS grants_by_coordinator ON coordinator_grants(coordinator, id);
CREATE TRIGGER IF NOT EXISTS grants_no_delete BEFORE DELETE ON coordinator_grants
BEGIN SELECT RAISE(ABORT, 'coordinator grants are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS grants_identity_frozen BEFORE UPDATE ON coordinator_grants
WHEN NEW.id <> OLD.id OR NEW.coordinator <> OLD.coordinator OR NEW.actions_json <> OLD.actions_json
  OR NEW.approval_kinds_json <> OLD.approval_kinds_json OR NEW.project_id IS NOT OLD.project_id
  OR NEW.risk_tiers_json <> OLD.risk_tiers_json OR NEW.expires_at IS NOT OLD.expires_at
  OR NEW.created_at <> OLD.created_at
BEGIN SELECT RAISE(ABORT, 'a grant scope is immutable; revoke it and create a new one'); END;
CREATE TRIGGER IF NOT EXISTS grants_revoked_once BEFORE UPDATE ON coordinator_grants
WHEN OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NOT OLD.revoked_at
BEGIN SELECT RAISE(ABORT, 'a grant is revoked once and never reopened'); END;
"""


DDL = f"""
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE transitions (
  from_status TEXT NOT NULL, to_status TEXT NOT NULL, PRIMARY KEY (from_status, to_status)
);
CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);

CREATE TABLE tasks (
  task_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  contract_sha256 TEXT NOT NULL,
  contract_json TEXT NOT NULL,
  title TEXT NOT NULL,
  role TEXT,
  requested_worker TEXT,
  risk_tier TEXT NOT NULL CHECK (risk_tier IN ('R0','R1','R2')),
  status TEXT NOT NULL CHECK (status IN ({_STATUS_LIST})),
  version INTEGER NOT NULL CHECK (version >= 1),
  submitted_by TEXT NOT NULL CHECK (submitted_by IN ('user','coordinator')),
  ad_hoc INTEGER NOT NULL DEFAULT 0 CHECK (ad_hoc IN (0,1)),
  base_json TEXT NOT NULL,
  profile_json TEXT NOT NULL,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  retry_budget INTEGER NOT NULL DEFAULT 1,
  switch_budget INTEGER NOT NULL DEFAULT 1,
  pinned_worker TEXT,
  not_before TEXT,
  last_failure TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, idempotency_key)
);

CREATE TABLE attempts (
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  n INTEGER NOT NULL CHECK (n >= 1),
  worker_id TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  model_requested TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('STARTING','RUNNING','VERIFYING','ENDED')),
  end_kind TEXT CHECK (end_kind IN ('COMPLETED','FAILED','INTERRUPTED','CANCELED','ORPHANED')),
  failure_class TEXT,
  stage TEXT,
  pid INTEGER, pgid INTEGER, proc_identity TEXT,
  kernel_id TEXT NOT NULL,
  heartbeat_at TEXT,
  workspace_path TEXT, artifact_dir TEXT, prompt_sha256 TEXT,
  exit_code INTEGER, signal INTEGER, timeout_kind TEXT,
  config_json TEXT NOT NULL,
  result_json TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  PRIMARY KEY (task_id, n),
  CHECK ((state = 'ENDED') = (end_kind IS NOT NULL))
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  task_id TEXT,
  attempt_n INTEGER,
  type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  reason TEXT,
  data_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX events_by_task ON events(task_id, id);

CREATE TABLE approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  kind TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','REJECTED')),
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT,
  note TEXT,
  payload_json TEXT NOT NULL DEFAULT '{{}}'
);

CREATE TABLE project_locks (
  project_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  attempt_n INTEGER NOT NULL,
  kernel_id TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL
);

CREATE TABLE provider_health (
  provider_id TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  detail TEXT,
  since TEXT NOT NULL,
  until TEXT,
  PRIMARY KEY (provider_id, model)
);

CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER tasks_no_delete BEFORE DELETE ON tasks
BEGIN SELECT RAISE(ABORT, 'tasks are never deleted'); END;
CREATE TRIGGER attempts_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempts are never deleted'); END;
CREATE TRIGGER attempts_immutable_when_ended BEFORE UPDATE ON attempts WHEN OLD.state = 'ENDED'
BEGIN SELECT RAISE(ABORT, 'an ended attempt is immutable'); END;
CREATE TRIGGER attempts_state_forward BEFORE UPDATE OF state ON attempts
WHEN NEW.state <> OLD.state AND NOT (
     (OLD.state = 'STARTING' AND NEW.state IN ('RUNNING','ENDED'))
  OR (OLD.state = 'RUNNING'  AND NEW.state IN ('VERIFYING','ENDED'))
  OR (OLD.state = 'VERIFYING' AND NEW.state = 'ENDED'))
BEGIN SELECT RAISE(ABORT, 'illegal attempt state change'); END;
CREATE TRIGGER tasks_transition_guard BEFORE UPDATE OF status ON tasks
WHEN NEW.status <> OLD.status AND NOT EXISTS
     (SELECT 1 FROM transitions WHERE from_status = OLD.status AND to_status = NEW.status)
BEGIN SELECT RAISE(ABORT, 'illegal task status transition'); END;
CREATE TRIGGER tasks_version_step BEFORE UPDATE ON tasks WHEN NEW.version <> OLD.version + 1
BEGIN SELECT RAISE(ABORT, 'task version must advance by exactly one'); END;
CREATE TRIGGER tasks_identity_frozen BEFORE UPDATE ON tasks
WHEN NEW.task_id <> OLD.task_id OR NEW.project_id <> OLD.project_id
  OR NEW.idempotency_key <> OLD.idempotency_key OR NEW.contract_sha256 <> OLD.contract_sha256
  OR NEW.contract_json <> OLD.contract_json OR NEW.created_at <> OLD.created_at
BEGIN SELECT RAISE(ABORT, 'task identity and contract are frozen'); END;
CREATE TRIGGER approvals_immutable_when_decided BEFORE UPDATE ON approvals WHEN OLD.status <> 'PENDING'
BEGIN SELECT RAISE(ABORT, 'a decided approval is immutable'); END;
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are never deleted'); END;
""" + CANDIDATE_DDL


def _dump(obj: Any, *, redact: bool = False) -> str:
    return json.dumps(redact_obj(obj) if redact else obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def row_dict(row: "sqlite3.Row | None") -> "dict[str, Any] | None":
    return None if row is None else {k: row[k] for k in row.keys()}


class Store:
    def __init__(self, data_dir: "str | os.PathLike[str]", clock: "Clock | None" = None, filename: str = DB_NAME) -> None:
        self.path = Path(data_dir) / filename
        self.clock = clock or Clock()
        self._conn: "sqlite3.Connection | None" = None
        self._depth = 0

    # --- connection and migrations ---------------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # Never open and close the database file outside SQLite: on POSIX, closing any descriptor of a file drops the
            # advisory locks every other connection in this process holds on it, and a closing connection elsewhere would then
            # believe it is the last one and delete the WAL under us. (SQLite creates the file; chmod is path-based, no descriptor.)
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=10.0, check_same_thread=False)   # handed between threads, never shared
            os.chmod(self.path, 0o600)                       # before any data is written; -wal and -shm copy this mode
            conn.row_factory = sqlite3.Row
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise LedgerError(f"this filesystem cannot run the ledger in WAL mode (got {mode!r})")
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=10000")
                self._migrate(conn)
            except BaseException:
                conn.close()
                raise
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._depth = 0

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise LedgerError(f"the ledger is schema {version}, newer than this build understands ({SCHEMA_VERSION})")
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]   # another process may have migrated meanwhile
            if version == 0:
                for statement in _split_statements(DDL):
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_meta(key, value) VALUES ('created_at', ?)", (self.clock.iso(),))
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            for statement in _split_statements(CANDIDATE_DDL):       # additive: a v1 ledger gains the table, no version bump
                conn.execute(statement)
            for statement in _split_statements(GRANT_DDL):            # additive: older builds ignore the table
                conn.execute(statement)
            candidate_columns = {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}
            if "requested_json" not in candidate_columns:             # an intermediate build of this table had no requested set
                conn.execute("ALTER TABLE candidates ADD COLUMN requested_json TEXT NOT NULL DEFAULT '[]'")
            conn.execute("CREATE INDEX IF NOT EXISTS events_by_type ON events(type, id)")     # additive: lets rare event types (coordinator switches) be found without a scan
            # The transitions table must equal the code's table, whatever the stored digest says: the triggers
            # trust this table, so an edited copy is repaired (and the repair is on the record).
            wanted = transition_rows()
            found = sorted((r[0], r[1]) for r in conn.execute("SELECT from_status, to_status FROM transitions"))
            if found != wanted:
                conn.execute("DELETE FROM transitions")
                conn.executemany("INSERT INTO transitions(from_status, to_status) VALUES (?, ?)", wanted)
                conn.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('transitions_sha256', ?)",
                             (transitions_digest(),))
                if found:
                    conn.execute("INSERT INTO events(ts, type, reason, data_json) VALUES (?, 'TRANSITIONS_RESYNCED', ?, ?)",
                                 (self.clock.iso(), "the stored transition table differed from the code's table",
                                  _dump({"had": len(found), "now": len(wanted)})))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction: everything inside commits together or not at all.

        A nested ``tx()`` is a SAVEPOINT, so an inner block that fails undoes only its own work.
        """
        conn = self.conn
        if self._depth:
            name = f"sp{self._depth}"
            conn.execute(f"SAVEPOINT {name}")
            self._depth += 1
            try:
                yield conn
            except BaseException:
                conn.execute(f"ROLLBACK TO {name}")
                conn.execute(f"RELEASE {name}")
                raise
            else:
                conn.execute(f"RELEASE {name}")
            finally:
                self._depth -= 1
            return
        conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            self._depth = 0

    def _need_tx(self) -> None:
        """Every ledger write must sit inside ``tx()``, so a state change and its event can never be split."""
        if not self._depth:
            raise LedgerError("ledger writes must run inside Store.tx()")

    def integrity_ok(self) -> bool:
        return self.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # --- events ---------------------------------------------------------------------------------------
    def add_event(self, type_: str, *, task_id: "str | None" = None, attempt_n: "int | None" = None,
                  from_status: "str | None" = None, to_status: "str | None" = None, reason: "str | None" = None,
                  data: "dict[str, Any] | None" = None) -> int:
        self._need_tx()
        cur = self.conn.execute(
            "INSERT INTO events(ts, task_id, attempt_n, type, from_status, to_status, reason, data_json) VALUES (?,?,?,?,?,?,?,?)",
            (self.clock.iso(), task_id, attempt_n, type_, from_status, to_status,
             None if reason is None else str(redact_obj(reason))[:500], _dump(data or {}, redact=True)))
        return int(cur.lastrowid)

    def events(self, task_id: "str | None" = None, after_id: int = 0, limit: "int | None" = None) -> "list[dict[str, Any]]":
        sql, args = "SELECT * FROM events WHERE id > ?", [after_id]
        if task_id is not None:
            sql += " AND task_id = ?"
            args.append(task_id)
        sql += " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        out = []
        for r in self.conn.execute(sql, args):
            d = row_dict(r)
            d["data"] = json.loads(d.pop("data_json"))
            d["attempt"] = d.pop("attempt_n")
            out.append(d)
        return out

    def recent_events_of(self, type_: str, limit: int = 50) -> "list[dict[str, Any]]":
        """The newest events of one type, newest first (the ledger is only scanned by its type, never in full)."""
        out = []
        for r in self.conn.execute("SELECT * FROM events WHERE type = ? ORDER BY id DESC LIMIT ?", (type_, int(limit))):
            d = row_dict(r)
            d["data"] = json.loads(d.pop("data_json"))
            d["attempt"] = d.pop("attempt_n")
            out.append(d)
        return out

    # --- tasks ------------------------------------------------------------------------------------------
    def next_task_id(self, project_id: str) -> str:
        self._need_tx()
        name = f"task:{project_id}"
        self.conn.execute("INSERT INTO counters(name, value) VALUES (?, 0) ON CONFLICT(name) DO NOTHING", (name,))
        self.conn.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (name,))
        n = self.conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0]
        return f"{project_id}-{n:04d}"

    def insert_task(self, *, task_id: str, project_id: str, idempotency_key: str, contract_sha256: str,
                    contract: dict, title: str, role: "str | None", requested_worker: "str | None", risk_tier: str,
                    submitted_by: str, ad_hoc: bool, base: dict, profile: dict, retry_budget: int = 1,
                    switch_budget: int = 1) -> None:
        self._need_tx()
        now = self.clock.iso()
        try:
            self.conn.execute(
                "INSERT INTO tasks(task_id, project_id, idempotency_key, contract_sha256, contract_json, title, role, "
                "requested_worker, risk_tier, status, version, submitted_by, ad_hoc, base_json, profile_json, "
                "retry_budget, switch_budget, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'DRAFT',1,?,?,?,?,?,?,?,?)",
                (task_id, project_id, idempotency_key, contract_sha256, _dump(contract), title, role, requested_worker,
                 risk_tier, submitted_by, int(ad_hoc), _dump(base), _dump(profile), retry_budget, switch_budget, now, now))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) and "idempotency_key" in str(exc):
                raise IdempotencyConflict("a task with this idempotency key already exists") from exc
            raise

    def get_task(self, task_id: str) -> "dict[str, Any] | None":
        d = row_dict(self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone())
        return _decode_task(d)

    def find_task(self, project_id: str, idempotency_key: str) -> "dict[str, Any] | None":
        d = row_dict(self.conn.execute("SELECT * FROM tasks WHERE project_id = ? AND idempotency_key = ?",
                                       (project_id, idempotency_key)).fetchone())
        return _decode_task(d)

    def list_tasks(self, status: "str | None" = None, project_id: "str | None" = None) -> "list[dict[str, Any]]":
        sql, args = "SELECT * FROM tasks WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if project_id:
            sql += " AND project_id = ?"
            args.append(project_id)
        return [_decode_task(row_dict(r)) for r in self.conn.execute(sql + " ORDER BY created_at, task_id", args)]

    # --- candidates (T11: read-only integration record) -------------------------------------------------
    def insert_candidate(self, *, candidate_id: str, project_id: str, base_commit: str, commit_sha: "str | None",
                         status: str, tasks: "list[dict[str, Any]]", checks: "list[dict[str, Any]]",
                         artifact_dir: str, requested: "list[str]", reason: str = "") -> None:
        self._need_tx()
        now = self.clock.iso()
        self.conn.execute(
            "INSERT INTO candidates(candidate_id, project_id, base_commit, commit_sha, status, tasks_json, requested_json, "
            "checks_json, artifact_dir, reason, schema, created_at, verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (candidate_id, project_id, base_commit, commit_sha, status, _dump(tasks), _dump(requested), _dump(checks),
             artifact_dir, reason[:500], now, now if status == "VERIFIED" else None))

    def get_candidate(self, candidate_id: str) -> "dict[str, Any] | None":
        return _decode_candidate(row_dict(self.conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()))

    def list_candidates(self, project_id: "str | None" = None) -> "list[dict[str, Any]]":
        sql, args = "SELECT * FROM candidates WHERE 1=1", []
        if project_id:
            sql += " AND project_id = ?"
            args.append(project_id)
        return [_decode_candidate(row_dict(r)) for r in self.conn.execute(sql + " ORDER BY created_at, candidate_id", args)]

    def set_status(self, task_id: str, to_status: "TaskStatus | str", *, reason: str, attempt_n: "int | None" = None,
                   expected_version: "int | None" = None, columns: "dict[str, Any] | None" = None,
                   data: "dict[str, Any] | None" = None) -> int:
        """Move a task along an allowed transition and record the event, in the caller's transaction."""
        self._need_tx()
        to_status = TaskStatus(to_status).value
        row = self.conn.execute("SELECT status, version FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFound(f"no task {task_id!r}")
        if expected_version is not None and row["version"] != expected_version:
            raise VersionConflict(f"{task_id} is at version {row['version']}, expected {expected_version}")
        check_transition(row["status"], to_status)
        new_version = self._update(task_id, row["version"], {"status": to_status, **(columns or {})})
        self.add_event("STATUS_CHANGED", task_id=task_id, attempt_n=attempt_n, from_status=row["status"],
                       to_status=to_status, reason=reason, data={"version": new_version, **(data or {})})
        return new_version

    def update_task(self, task_id: str, event_type: str, *, reason: str, columns: "dict[str, Any]",
                    expected_version: "int | None" = None, attempt_n: "int | None" = None,
                    data: "dict[str, Any] | None" = None) -> int:
        """Change non-status columns (still bumps the version) and record why."""
        self._need_tx()
        row = self.conn.execute("SELECT version FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise NotFound(f"no task {task_id!r}")
        if expected_version is not None and row["version"] != expected_version:
            raise VersionConflict(f"{task_id} is at version {row['version']}, expected {expected_version}")
        new_version = self._update(task_id, row["version"], columns)
        self.add_event(event_type, task_id=task_id, attempt_n=attempt_n, reason=reason, data={"version": new_version, **(data or {})})
        return new_version

    def _update(self, task_id: str, version: int, columns: "dict[str, Any]") -> int:
        cols = dict(columns)
        for key in ("base_json", "profile_json"):
            if key in cols and not isinstance(cols[key], str):
                cols[key] = _dump(cols[key])
        cols.update(version=version + 1, updated_at=self.clock.iso())
        assignments = ", ".join(f"{k} = ?" for k in cols)
        cur = self.conn.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ? AND version = ?",
                                [*cols.values(), task_id, version])
        if cur.rowcount != 1:
            raise VersionConflict(f"{task_id} changed while it was being updated")
        return version + 1

    # --- attempts ---------------------------------------------------------------------------------------
    def insert_attempt(self, task_id: str, n: int, *, worker_id: str, provider_id: str, model: str,
                       kernel_id: str, config: dict) -> None:
        self._need_tx()
        now = self.clock.iso()
        self.conn.execute(
            "INSERT INTO attempts(task_id, n, worker_id, provider_id, model_requested, state, kernel_id, heartbeat_at, "
            "config_json, started_at) VALUES (?,?,?,?,?,'STARTING',?,?,?,?)",
            (task_id, n, worker_id, provider_id, model, kernel_id, now, _dump(config), now))

    def update_attempt(self, task_id: str, n: int, **cols: Any) -> None:
        self._need_tx()
        assignments = ", ".join(f"{k} = ?" for k in cols)
        self.conn.execute(f"UPDATE attempts SET {assignments} WHERE task_id = ? AND n = ?", [*cols.values(), task_id, n])

    def end_attempt(self, task_id: str, n: int, *, end_kind: str, failure_class: "str | None" = None,
                    stage: "str | None" = None, exit_code: "int | None" = None, signal: "int | None" = None,
                    timeout_kind: "str | None" = None, result: "dict | None" = None) -> None:
        self._need_tx()
        self.update_attempt(task_id, n, state="ENDED", end_kind=end_kind, failure_class=failure_class, stage=stage,
                            exit_code=exit_code, signal=signal, timeout_kind=timeout_kind,
                            result_json=None if result is None else _dump(result), ended_at=self.clock.iso())

    def get_attempt(self, task_id: str, n: int) -> "dict[str, Any] | None":
        return _decode_attempt(row_dict(self.conn.execute("SELECT * FROM attempts WHERE task_id = ? AND n = ?", (task_id, n)).fetchone()))

    def attempts(self, task_id: str) -> "list[dict[str, Any]]":
        return [_decode_attempt(row_dict(r)) for r in self.conn.execute("SELECT * FROM attempts WHERE task_id = ? ORDER BY n", (task_id,))]

    def unfinished_attempts(self) -> "list[dict[str, Any]]":
        return [_decode_attempt(row_dict(r)) for r in self.conn.execute("SELECT * FROM attempts WHERE state <> 'ENDED' ORDER BY started_at")]

    def heartbeat(self, task_id: str, n: int, project_id: str) -> None:
        self._need_tx()
        now = self.clock.iso()
        self.conn.execute("UPDATE attempts SET heartbeat_at = ? WHERE task_id = ? AND n = ? AND state <> 'ENDED'", (now, task_id, n))
        self.conn.execute("UPDATE project_locks SET heartbeat_at = ? WHERE project_id = ? AND task_id = ? AND attempt_n = ?",
                          (now, project_id, task_id, n))

    # --- project locks ----------------------------------------------------------------------------------
    def acquire_lock(self, project_id: str, task_id: str, n: int, kernel_id: str) -> None:
        self._need_tx()
        now = self.clock.iso()
        try:
            self.conn.execute("INSERT INTO project_locks(project_id, task_id, attempt_n, kernel_id, acquired_at, heartbeat_at) "
                              "VALUES (?,?,?,?,?,?)", (project_id, task_id, n, kernel_id, now, now))
        except sqlite3.IntegrityError:
            raise LockBusy(self.get_lock(project_id) or {}) from None

    def release_lock(self, project_id: str, task_id: str) -> None:
        self._need_tx()
        self.conn.execute("DELETE FROM project_locks WHERE project_id = ? AND task_id = ?", (project_id, task_id))

    def get_lock(self, project_id: str) -> "dict[str, Any] | None":
        return row_dict(self.conn.execute("SELECT * FROM project_locks WHERE project_id = ?", (project_id,)).fetchone())

    def locks(self) -> "list[dict[str, Any]]":
        return [row_dict(r) for r in self.conn.execute("SELECT * FROM project_locks ORDER BY project_id")]

    # --- approvals --------------------------------------------------------------------------------------
    def add_approval(self, task_id: str, kind: str, payload: "dict | None" = None) -> int:
        self._need_tx()
        cur = self.conn.execute("INSERT INTO approvals(task_id, kind, status, requested_at, payload_json) VALUES (?,?,'PENDING',?,?)",
                                (task_id, kind, self.clock.iso(), _dump(payload or {}, redact=True)))
        self.add_event("APPROVAL_REQUESTED", task_id=task_id, reason=kind, data={"approval_id": cur.lastrowid, "kind": kind})
        return int(cur.lastrowid)

    def approvals(self, task_id: str, status: "str | None" = None) -> "list[dict[str, Any]]":
        sql, args = "SELECT * FROM approvals WHERE task_id = ?", [task_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        out = []
        for r in self.conn.execute(sql + " ORDER BY id", args):
            d = row_dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out

    def decide_approval(self, approval_id: int, decision: str, decided_by: str, note: str = "") -> None:
        self._need_tx()
        self.conn.execute("UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, note = ? WHERE id = ?",
                          (decision, self.clock.iso(), decided_by, str(redact_obj(note))[:500], approval_id))

    def add_grant(self, coordinator: str, actions: "list[str]", *, approval_kinds: "list[str]",
                  project_id: "str | None", risk_tiers: "list[str]", expires_at: "str | None", note: str = "") -> int:
        self._need_tx()
        cur = self.conn.execute(
            "INSERT INTO coordinator_grants(coordinator, actions_json, approval_kinds_json, project_id, risk_tiers_json, "
            "expires_at, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (coordinator, _dump(actions), _dump(approval_kinds), project_id, _dump(risk_tiers), expires_at,
             str(redact_obj(note))[:500], self.clock.iso()))
        return int(cur.lastrowid)

    def grants(self, coordinator: "str | None" = None) -> "list[dict[str, Any]]":
        sql, args = "SELECT * FROM coordinator_grants", []
        if coordinator is not None:
            sql += " WHERE coordinator = ?"
            args.append(coordinator)
        out = []
        for r in self.conn.execute(sql + " ORDER BY id", args):
            d = row_dict(r)
            d["actions"] = json.loads(d.pop("actions_json"))
            d["approval_kinds"] = json.loads(d.pop("approval_kinds_json"))
            d["risk_tiers"] = json.loads(d.pop("risk_tiers_json"))
            out.append(d)
        return out

    def revoke_grant(self, grant_id: int, when: str) -> None:
        self._need_tx()
        cur = self.conn.execute("UPDATE coordinator_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                                (when, grant_id))
        if cur.rowcount != 1:
            raise NotFound(f"no active coordinator grant #{grant_id}")

    # --- provider health ----------------------------------------------------------------------------------
    def set_health(self, provider_id: str, model: str, status: str, *, until: "str | None", detail: str = "") -> None:
        self._need_tx()
        self.conn.execute("INSERT OR REPLACE INTO provider_health(provider_id, model, status, detail, since, until) VALUES (?,?,?,?,?,?)",
                          (provider_id, model, status, str(redact_obj(detail))[:300], self.clock.iso(), until))

    def get_health(self, provider_id: str, model: str = "") -> "dict[str, Any] | None":
        return row_dict(self.conn.execute("SELECT * FROM provider_health WHERE provider_id = ? AND model = ?", (provider_id, model)).fetchone())

    def health_rows(self) -> "list[dict[str, Any]]":
        return [row_dict(r) for r in self.conn.execute("SELECT * FROM provider_health ORDER BY provider_id, model")]

    def clear_health(self, provider_id: str, model: "str | None" = None) -> int:
        self._need_tx()
        if model is None:
            cur = self.conn.execute("DELETE FROM provider_health WHERE provider_id = ?", (provider_id,))
        else:
            cur = self.conn.execute("DELETE FROM provider_health WHERE provider_id = ? AND model = ?", (provider_id, model))
        return cur.rowcount


def _split_statements(script: str) -> "list[str]":
    """Split the DDL into single statements (trigger bodies stay whole) without ``executescript``'s implicit COMMIT."""
    statements, buf = [], ""
    for line in script.splitlines():
        if not line.strip() and not buf:
            continue
        buf += line + "\n"
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise LedgerError("the ledger DDL ends in the middle of a statement")
    return statements


def _decode_task(d: "dict[str, Any] | None") -> "dict[str, Any] | None":
    if d is None:
        return None
    for key in ("contract_json", "base_json", "profile_json"):
        d[key[:-5]] = json.loads(d.pop(key))
    return d


def _decode_candidate(d: "dict[str, Any] | None") -> "dict[str, Any] | None":
    if d is None:
        return None
    d["tasks"] = json.loads(d.pop("tasks_json"))
    d["requested"] = json.loads(d.pop("requested_json", "[]"))
    d["checks"] = json.loads(d.pop("checks_json"))
    return d


def _decode_attempt(d: "dict[str, Any] | None") -> "dict[str, Any] | None":
    if d is None:
        return None
    d["config"] = json.loads(d.pop("config_json"))
    raw = d.pop("result_json")
    d["result"] = None if raw is None else json.loads(raw)
    return d
