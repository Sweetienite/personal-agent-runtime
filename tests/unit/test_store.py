import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from par import schema
from par.core.clock import FakeClock
from par.core.states import TRANSITIONS, IllegalTransition, TaskStatus as T, transition_rows
from par.core.store import (
    SCHEMA_VERSION, IdempotencyConflict, LedgerError, LockBusy, NotFound, Store, VersionConflict,
)
from tests import SRC
from tests.world import sample_contract


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(os.path.realpath(self._tmp.name))
        self.clock = FakeClock()
        self.store = Store(self.dir, self.clock)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.store.close)

    def raw(self):
        self.store.conn                                   # make sure the ledger exists and is migrated
        conn = sqlite3.connect(self.store.path, isolation_level=None, timeout=0.2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(conn.close)
        return conn

    def make_task(self, project="fixture", key="k1", *, risk="R2"):
        with self.store.tx():
            task_id = self.store.next_task_id(project)
            self.store.insert_task(
                task_id=task_id, project_id=project, idempotency_key=key, contract_sha256="a" * 64,
                contract=sample_contract(project_id=project, idempotency_key=key), title="Fix add()", role="code",
                requested_worker=None, risk_tier=risk, submitted_by="user", ad_hoc=False,
                base={"vcs": "git", "commit": "abc123"}, profile={"mode": "safe_auto"}, retry_budget=1)
        return task_id

    def walk(self, task_id, *statuses):
        for status in statuses:
            with self.store.tx():
                self.store.set_status(task_id, status, reason=f"to {status}")


class Opening(StoreCase):
    def test_database_file_is_private(self):
        self.store.conn
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)
        with self.store.tx():
            self.store.add_event("PING")
        for suffix in ("-wal", "-shm"):
            side = Path(str(self.store.path) + suffix)
            if side.exists():
                self.assertEqual(os.stat(side).st_mode & 0o777, 0o600, side.name)

    def test_durability_settings(self):
        conn = self.store.conn
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)        # FULL
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertTrue(self.store.integrity_ok())

    def test_reopening_is_harmless_and_keeps_data(self):
        task_id = self.make_task()
        created = self.store.conn.execute("SELECT value FROM schema_meta WHERE key='created_at'").fetchone()[0]
        self.store.close()
        again = Store(self.dir, self.clock)
        self.addCleanup(again.close)
        self.assertEqual(again.get_task(task_id)["status"], "DRAFT")
        self.assertEqual(again.conn.execute("SELECT value FROM schema_meta WHERE key='created_at'").fetchone()[0], created)
        self.assertEqual(again.conn.execute("SELECT COUNT(*) FROM events WHERE type='TRANSITIONS_RESYNCED'").fetchone()[0], 0)

    def test_a_newer_ledger_is_refused(self):
        self.store.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        self.store.close()
        with self.assertRaises(LedgerError):
            Store(self.dir).conn

    def test_transition_table_mirrors_the_code(self):
        rows = sorted(tuple(r) for r in self.store.conn.execute("SELECT from_status, to_status FROM transitions"))
        self.assertEqual(rows, transition_rows())

    def test_a_tampered_transition_table_is_repaired_on_open_and_recorded(self):
        self.raw().execute("INSERT INTO transitions VALUES ('RUNNING', 'ACCEPTED')")
        self.store.close()
        again = Store(self.dir, self.clock)
        self.addCleanup(again.close)
        rows = sorted(tuple(r) for r in again.conn.execute("SELECT from_status, to_status FROM transitions"))
        self.assertEqual(rows, transition_rows())
        self.assertEqual(again.events()[-1]["type"], "TRANSITIONS_RESYNCED")

    def test_the_repair_closes_the_door_the_tamper_opened(self):
        self.raw().execute("INSERT INTO transitions VALUES ('DRAFT', 'ACCEPTED')")
        self.store.close()
        self.store = Store(self.dir, self.clock)
        task_id = self.make_task()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw().execute("UPDATE tasks SET status='ACCEPTED', version=version+1 WHERE task_id=?", (task_id,))


class FileHygiene(StoreCase):
    def test_the_database_file_is_never_opened_outside_sqlite(self):
        """Closing any raw descriptor of the database drops the POSIX locks of every other connection in the process,
        after which a closing connection elsewhere deletes the WAL under them. (A real crash test found this once.)"""
        import par.core.store as store_module
        source = Path(store_module.__file__).read_text()
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        self.assertNotIn("os.open(", code)
        self.assertNotIn("os.close(", code)
        self.assertNotIn("open(self.path", code)


class Transactions(StoreCase):
    def test_writes_outside_a_transaction_are_refused(self):
        task_id = self.make_task()
        attempts = (
            lambda: self.store.add_event("X"),
            lambda: self.store.next_task_id("p"),
            lambda: self.store.set_status(task_id, T.READY, reason="r"),
            lambda: self.store.update_task(task_id, "NOTE", reason="r", columns={"title": "x"}),
            lambda: self.store.insert_attempt(task_id, 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={}),
            lambda: self.store.update_attempt(task_id, 1, stage="x"),
            lambda: self.store.heartbeat(task_id, 1, "fixture"),
            lambda: self.store.acquire_lock("fixture", task_id, 1, "k"),
            lambda: self.store.release_lock("fixture", task_id),
            lambda: self.store.add_approval(task_id, "WRITE_PERMISSION"),
            lambda: self.store.decide_approval(1, "APPROVED", "me"),
            lambda: self.store.set_health("p", "", "RATE_LIMITED", until=None),
            lambda: self.store.clear_health("p"),
        )
        for i, attempt in enumerate(attempts):
            with self.assertRaises(LedgerError, msg=f"writer #{i}"):
                attempt()

    def test_a_transaction_commits_everything_together(self):
        task_id = self.make_task()
        with self.store.tx():
            self.store.set_status(task_id, T.READY, reason="ready")
            self.store.add_event("NOTE", task_id=task_id)
        types = [e["type"] for e in self.store.events(task_id)]
        self.assertEqual(self.store.get_task(task_id)["status"], "READY")
        self.assertIn("NOTE", types)

    def test_a_failed_transaction_leaves_nothing_behind(self):
        task_id = self.make_task()
        before = len(self.store.events())
        with self.assertRaises(RuntimeError):
            with self.store.tx():
                self.store.set_status(task_id, T.READY, reason="ready")
                self.store.add_event("NOTE", task_id=task_id)
                raise RuntimeError("boom")
        self.assertEqual(self.store.get_task(task_id)["status"], "DRAFT")
        self.assertEqual(len(self.store.events()), before)
        with self.store.tx():                       # and the store is usable again, not stuck in a transaction
            self.store.add_event("AFTER")

    def test_an_inner_failure_undoes_only_the_inner_block(self):
        with self.store.tx():
            self.store.add_event("OUTER_1")
            try:
                with self.store.tx():
                    self.store.add_event("INNER")
                    raise RuntimeError("inner failed")
            except RuntimeError:
                pass
            self.store.add_event("OUTER_2")
        self.assertEqual([e["type"] for e in self.store.events()], ["OUTER_1", "OUTER_2"])

    def test_a_task_id_is_not_consumed_by_a_rolled_back_submit(self):
        with self.assertRaises(RuntimeError):
            with self.store.tx():
                self.assertEqual(self.store.next_task_id("fixture"), "fixture-0001")
                raise RuntimeError("submit failed")
        self.assertEqual(self.make_task(), "fixture-0001")

    def test_only_one_writer_at_a_time(self):
        other = self.raw()
        with self.store.tx():
            self.store.add_event("HOLD")
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("BEGIN IMMEDIATE")
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")


class Tasks(StoreCase):
    def test_ids_are_sequential_per_project(self):
        self.assertEqual(self.make_task("fixture", "a"), "fixture-0001")
        self.assertEqual(self.make_task("fixture", "b"), "fixture-0002")
        self.assertEqual(self.make_task("other", "a"), "other-0001")

    def test_a_task_round_trips(self):
        task_id = self.make_task()
        task = self.store.get_task(task_id)
        self.assertEqual((task["status"], task["version"], task["attempt_count"], task["cancel_requested"]), ("DRAFT", 1, 0, 0))
        self.assertEqual(task["contract"]["title"], "Fix add()")
        self.assertEqual(task["base"], {"vcs": "git", "commit": "abc123"})
        self.assertEqual(task["profile"], {"mode": "safe_auto"})
        self.assertEqual(task["risk_tier"], "R2")
        self.assertIsNone(self.store.get_task("fixture-9999"))

    def test_an_idempotency_key_is_unique_within_a_project(self):
        self.make_task("fixture", "same")
        with self.assertRaises(IdempotencyConflict):
            self.make_task("fixture", "same")
        self.make_task("other", "same")                  # a different project may reuse it
        self.assertEqual(self.store.find_task("fixture", "same")["task_id"], "fixture-0001")
        self.assertIsNone(self.store.find_task("fixture", "nope"))

    def test_listing_filters(self):
        a, b = self.make_task("fixture", "a"), self.make_task("other", "b")
        self.walk(a, T.READY)
        self.assertEqual([t["task_id"] for t in self.store.list_tasks()], [a, b])
        self.assertEqual([t["task_id"] for t in self.store.list_tasks(status="READY")], [a])
        self.assertEqual([t["task_id"] for t in self.store.list_tasks(project_id="other")], [b])

    def test_r3_cannot_be_stored(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.make_task(risk="R3")


class StatusChanges(StoreCase):
    def test_a_legal_chain_bumps_the_version_and_records_each_step(self):
        task_id = self.make_task()
        self.clock.advance(5)
        self.walk(task_id, T.READY, T.QUEUED)
        task = self.store.get_task(task_id)
        self.assertEqual((task["status"], task["version"]), ("QUEUED", 3))
        self.assertGreater(task["updated_at"], task["created_at"])
        steps = [(e["from_status"], e["to_status"]) for e in self.store.events(task_id) if e["type"] == "STATUS_CHANGED"]
        self.assertEqual(steps, [("DRAFT", "READY"), ("READY", "QUEUED")])

    def test_an_illegal_jump_is_refused_without_a_trace(self):
        task_id = self.make_task()
        before = len(self.store.events())
        with self.assertRaises(IllegalTransition):
            with self.store.tx():
                self.store.set_status(task_id, T.RUNNING, reason="skip ahead")
        task = self.store.get_task(task_id)
        self.assertEqual((task["status"], task["version"]), ("DRAFT", 1))
        self.assertEqual(len(self.store.events()), before)

    def test_a_stale_version_is_a_conflict(self):
        task_id = self.make_task()
        self.walk(task_id, T.READY)
        with self.assertRaises(VersionConflict):
            with self.store.tx():
                self.store.set_status(task_id, T.QUEUED, reason="late", expected_version=1)
        with self.store.tx():
            self.store.set_status(task_id, T.QUEUED, reason="fresh", expected_version=2)

    def test_an_unknown_task_is_not_found(self):
        with self.assertRaises(NotFound):
            with self.store.tx():
                self.store.set_status("nope-0001", T.READY, reason="x")
        with self.assertRaises(NotFound):
            with self.store.tx():
                self.store.update_task("nope-0001", "NOTE", reason="x", columns={"title": "t"})

    def test_extra_columns_change_together_with_the_status(self):
        task_id = self.make_task()
        self.walk(task_id, T.READY, T.QUEUED)
        with self.store.tx():
            self.store.set_status(task_id, T.CANCELED, reason="stop", columns={"cancel_requested": 1, "last_failure": "user"})
        task = self.store.get_task(task_id)
        self.assertEqual((task["status"], task["cancel_requested"], task["last_failure"]), ("CANCELED", 1, "user"))

    def test_update_task_changes_non_status_fields_and_logs_why(self):
        task_id = self.make_task()
        with self.store.tx():
            version = self.store.update_task(task_id, "BASE_REFRESHED", reason="new base", columns={"base_json": {"vcs": "git", "commit": "def"}})
        task = self.store.get_task(task_id)
        self.assertEqual((version, task["version"], task["base"]["commit"], task["status"]), (2, 2, "def", "DRAFT"))
        self.assertEqual(self.store.events(task_id)[-1]["type"], "BASE_REFRESHED")

    def test_events_follow_the_event_schema(self):
        task_id = self.make_task()
        self.walk(task_id, T.READY)
        events = self.store.events(task_id)
        self.assertTrue(events)
        for event in events:
            self.assertEqual(schema.check(event, "event"), [], event)

    def test_events_are_redacted_and_bounded(self):
        with self.store.tx():
            self.store.add_event("NOTE", reason="used key sk-ant-abcdefghijklmnopqrstuvwxyz0123456789", data={"api_key": "hunter2", "ok": 1})
            self.store.add_event("LONG", reason="x" * 2000)
        first, second = self.store.events()
        self.assertNotIn("sk-ant", first["reason"])
        self.assertEqual(first["data"], {"api_key": "[REDACTED]", "ok": 1})
        self.assertEqual(len(second["reason"]), 500)

    def test_event_paging(self):
        with self.store.tx():
            for i in range(5):
                self.store.add_event("TICK", data={"i": i})
        events = self.store.events()
        self.assertEqual([e["id"] for e in events], [1, 2, 3, 4, 5])
        self.assertEqual([e["id"] for e in self.store.events(after_id=3)], [4, 5])
        self.assertEqual([e["id"] for e in self.store.events(limit=2)], [1, 2])


class DatabaseGuards(StoreCase):
    """The database defends its own rules, even against raw SQL from a buggy or foreign writer."""

    def test_events_are_append_only(self):
        with self.store.tx():
            self.store.add_event("FACT", reason="original")
        raw = self.raw()
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute("UPDATE events SET reason='rewritten' WHERE id=1")
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute("DELETE FROM events WHERE id=1")
        self.assertEqual(self.store.events()[0]["reason"], "original")

    def test_tasks_attempts_and_approvals_are_never_deleted(self):
        task_id = self.make_task()
        with self.store.tx():
            self.store.insert_attempt(task_id, 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={})
            self.store.add_approval(task_id, "WRITE_PERMISSION")
        raw = self.raw()
        for sql in ("DELETE FROM tasks", "DELETE FROM attempts", "DELETE FROM approvals"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                raw.execute(sql)

    def test_every_pair_of_statuses_obeys_the_table(self):
        raw = self.raw()
        allowed = refused = 0
        for i, src in enumerate(T):
            for j, dst in enumerate(T):
                task_id = f"grid-{i:02d}{j:02d}"
                raw.execute(
                    "INSERT INTO tasks(task_id, project_id, idempotency_key, contract_sha256, contract_json, title, risk_tier, "
                    "status, version, submitted_by, base_json, profile_json, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, "grid", task_id, "a" * 64, "{}", "t", "R1", src.value, 1, "user", "{}", "{}", "t", "t"))
                legal = src == dst or dst in TRANSITIONS[src]
                try:
                    raw.execute("UPDATE tasks SET status=?, version=version+1 WHERE task_id=?", (dst.value, task_id))
                    done = True
                except sqlite3.IntegrityError:
                    done = False
                self.assertEqual(done, legal, f"{src.value} -> {dst.value}")
                allowed += done
                refused += not done
        self.assertEqual(allowed + refused, len(T) ** 2)
        self.assertEqual(refused, len(T) ** 2 - len(T) - len(transition_rows()))

    def test_the_version_must_advance_by_exactly_one(self):
        task_id = self.make_task()
        raw = self.raw()
        for sql in ("UPDATE tasks SET title='x' WHERE task_id=?",                       # no bump
                    "UPDATE tasks SET title='x', version=version+2 WHERE task_id=?",    # jump
                    "UPDATE tasks SET title='x', version=version-1 WHERE task_id=?"):   # back
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                raw.execute(sql, (task_id,))
        raw.execute("UPDATE tasks SET title='ok', version=version+1 WHERE task_id=?", (task_id,))

    def test_identity_and_contract_are_frozen(self):
        task_id = self.make_task()
        raw = self.raw()
        for column, value in (("project_id", "evil"), ("idempotency_key", "other"), ("contract_sha256", "b" * 64),
                              ("contract_json", "{}"), ("task_id", "fixture-9999"), ("created_at", "yesterday")):
            with self.assertRaises(sqlite3.IntegrityError, msg=column):
                raw.execute(f"UPDATE tasks SET {column}=?, version=version+1 WHERE task_id=?", (value, task_id))

    def test_unknown_statuses_cannot_be_stored(self):
        task_id = self.make_task()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw().execute("UPDATE tasks SET status='DONE', version=version+1 WHERE task_id=?", (task_id,))

    def test_an_attempt_only_moves_forward_and_ended_is_final(self):
        task_id = self.make_task()
        with self.store.tx():
            self.store.insert_attempt(task_id, 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={})
        raw = self.raw()
        for state in ("VERIFYING",):                     # skipping RUNNING is allowed only via RUNNING or ENDED
            with self.assertRaises(sqlite3.IntegrityError, msg=state):
                raw.execute("UPDATE attempts SET state=? WHERE task_id=? AND n=1", (state, task_id))
        raw.execute("UPDATE attempts SET state='RUNNING' WHERE task_id=? AND n=1", (task_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute("UPDATE attempts SET state='STARTING' WHERE task_id=? AND n=1", (task_id,))
        raw.execute("UPDATE attempts SET state='VERIFYING' WHERE task_id=? AND n=1", (task_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute("UPDATE attempts SET state='RUNNING' WHERE task_id=? AND n=1", (task_id,))
        with self.store.tx():
            self.store.end_attempt(task_id, 1, end_kind="COMPLETED")
        for sql in ("UPDATE attempts SET stage='late' WHERE task_id=? AND n=1",
                    "UPDATE attempts SET state='RUNNING', end_kind=NULL WHERE task_id=? AND n=1",
                    "UPDATE attempts SET end_kind='FAILED' WHERE task_id=? AND n=1"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                raw.execute(sql, (task_id,))

    def test_ended_and_end_kind_go_together(self):
        task_id = self.make_task()
        with self.store.tx():
            self.store.insert_attempt(task_id, 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={})
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw().execute("UPDATE attempts SET state='ENDED' WHERE task_id=? AND n=1", (task_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw().execute("UPDATE attempts SET end_kind='FAILED' WHERE task_id=? AND n=1", (task_id,))

    def test_a_decided_approval_cannot_be_changed(self):
        task_id = self.make_task()
        with self.store.tx():
            approval_id = self.store.add_approval(task_id, "WRITE_PERMISSION", {"why": "needs write"})
            self.store.decide_approval(approval_id, "APPROVED", "me", "ok")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw().execute("UPDATE approvals SET status='REJECTED' WHERE id=?", (approval_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.tx():
                self.store.decide_approval(approval_id, "REJECTED", "me")
        self.assertEqual(self.store.approvals(task_id)[0]["status"], "APPROVED")

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.tx():
                self.store.insert_attempt("ghost-0001", 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={})
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.tx():
                self.store.add_approval("ghost-0001", "WRITE_PERMISSION")


class AttemptsLocksApprovalsHealth(StoreCase):
    def test_attempts_lifecycle(self):
        task_id = self.make_task()
        with self.store.tx():
            self.store.insert_attempt(task_id, 1, worker_id="w_code", provider_id="codex", model="m", kernel_id="k1", config={"timeouts": {"total": 9}})
        attempt = self.store.get_attempt(task_id, 1)
        self.assertEqual((attempt["state"], attempt["end_kind"], attempt["config"]["timeouts"]["total"]), ("STARTING", None, 9))
        self.assertEqual([a["n"] for a in self.store.unfinished_attempts()], [1])
        with self.store.tx():
            self.store.update_attempt(task_id, 1, state="RUNNING", pid=4242, pgid=4242, proc_identity="Tue Oct 14 03:00:00 2026")
            self.store.end_attempt(task_id, 1, end_kind="FAILED", failure_class="RATE_LIMITED", exit_code=1, result={"x": 1})
        attempt = self.store.get_attempt(task_id, 1)
        self.assertEqual((attempt["state"], attempt["end_kind"], attempt["failure_class"], attempt["result"]), ("ENDED", "FAILED", "RATE_LIMITED", {"x": 1}))
        self.assertIsNotNone(attempt["ended_at"])
        self.assertEqual(self.store.unfinished_attempts(), [])
        self.assertEqual([a["n"] for a in self.store.attempts(task_id)], [1])

    def test_one_attempt_number_per_task(self):
        task_id = self.make_task()
        with self.store.tx():
            self.store.insert_attempt(task_id, 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={})
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.tx():
                self.store.insert_attempt(task_id, 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={})

    def test_heartbeat_touches_the_open_attempt_and_its_lock_only(self):
        task_id = self.make_task()
        with self.store.tx():
            self.store.insert_attempt(task_id, 1, worker_id="w", provider_id="p", model="m", kernel_id="k", config={})
            self.store.acquire_lock("fixture", task_id, 1, "k")
        first = self.store.get_attempt(task_id, 1)["heartbeat_at"]
        self.clock.advance(3)
        with self.store.tx():
            self.store.heartbeat(task_id, 1, "fixture")
        self.assertGreater(self.store.get_attempt(task_id, 1)["heartbeat_at"], first)
        self.assertGreater(self.store.get_lock("fixture")["heartbeat_at"], first)
        with self.store.tx():
            self.store.end_attempt(task_id, 1, end_kind="FAILED")
            self.store.heartbeat(task_id, 1, "fixture")      # an ended attempt is untouched, and that is not an error
        self.assertEqual(self.store.get_attempt(task_id, 1)["state"], "ENDED")

    def test_one_lock_per_project(self):
        a, b = self.make_task("fixture", "a"), self.make_task("fixture", "b")
        with self.store.tx():
            self.store.acquire_lock("fixture", a, 1, "k1")
        with self.assertRaises(LockBusy) as ctx:
            with self.store.tx():
                self.store.acquire_lock("fixture", b, 1, "k2")
        self.assertEqual(ctx.exception.holder["task_id"], a)
        with self.store.tx():
            self.store.release_lock("fixture", b)             # not the holder: nothing happens
        self.assertEqual(self.store.get_lock("fixture")["task_id"], a)
        with self.store.tx():
            self.store.release_lock("fixture", a)
            self.store.acquire_lock("fixture", b, 1, "k2")
        self.assertEqual([lock["task_id"] for lock in self.store.locks()], [b])

    def test_a_lock_failure_in_a_transaction_undoes_the_rest_of_it(self):
        a, b = self.make_task("fixture", "a"), self.make_task("fixture", "b")
        with self.store.tx():
            self.store.acquire_lock("fixture", a, 1, "k1")
        with self.assertRaises(LockBusy):
            with self.store.tx():
                self.store.set_status(b, T.READY, reason="first step")
                self.store.acquire_lock("fixture", b, 1, "k2")
        self.assertEqual(self.store.get_task(b)["status"], "DRAFT")

    def test_approvals(self):
        task_id = self.make_task()
        with self.store.tx():
            first = self.store.add_approval(task_id, "WRITE_PERMISSION", {"paths": ["app/"]})
            second = self.store.add_approval(task_id, "BASE_DRIFT")
        self.assertEqual([a["status"] for a in self.store.approvals(task_id)], ["PENDING", "PENDING"])
        with self.store.tx():
            self.store.decide_approval(first, "APPROVED", "user", "looks right")
        self.assertEqual([a["id"] for a in self.store.approvals(task_id, "PENDING")], [second])
        decided = self.store.approvals(task_id, "APPROVED")[0]
        self.assertEqual((decided["decided_by"], decided["note"], decided["payload"]), ("user", "looks right", {"paths": ["app/"]}))
        self.assertEqual(sum(e["type"] == "APPROVAL_REQUESTED" for e in self.store.events(task_id)), 2)

    def test_provider_health(self):
        until = "2026-10-14T04:00:00.000Z"
        with self.store.tx():
            self.store.set_health("codex", "m1", "RATE_LIMITED", until=until, detail="429 slow down")
            self.store.set_health("codex", "", "AUTH_REQUIRED", until=None)
        self.assertEqual(self.store.get_health("codex", "m1")["until"], until)
        self.assertEqual(self.store.get_health("codex")["status"], "AUTH_REQUIRED")
        self.assertEqual(len(self.store.health_rows()), 2)
        with self.store.tx():
            self.assertEqual(self.store.clear_health("codex", "m1"), 1)
        self.assertIsNone(self.store.get_health("codex", "m1"))
        with self.store.tx():
            self.assertEqual(self.store.clear_health("codex"), 1)
        self.assertEqual(self.store.health_rows(), [])


CHILD_TICKER = """
import sys
from par.core.store import Store
store = Store(sys.argv[1])
n = 0
while True:
    with store.tx():
        store.add_event("TICK", data={"n": n})
    n += 1
    print(n, flush=True)
"""

CHILD_UNCOMMITTED = """
import sys, time
from par.core.store import Store
store = Store(sys.argv[1])
with store.tx():
    store.add_event("GHOST")
    store.add_event("GHOST")
    print("inside", flush=True)
    time.sleep(120)
"""


class Durability(StoreCase):
    """Process-kill stand-ins for a power cut: SIGKILL gives the writer no chance to clean up.

    (A real power cut also loses the OS cache; that case rests on SQLite's documented WAL + synchronous=FULL
    guarantee, which the pragma test above pins down.)
    """

    def spawn(self, code):
        env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.Popen([sys.executable, "-B", "-c", code, str(self.dir)], stdout=subprocess.PIPE, text=True, env=env)

    def test_committed_events_survive_a_sigkill(self):
        self.store.close()
        proc = self.spawn(CHILD_TICKER)
        seen = 0
        deadline = time.monotonic() + 30
        while seen < 25 and time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            seen = int(line)
        proc.kill()
        proc.wait()
        proc.stdout.close()
        self.assertGreaterEqual(seen, 25, "the writer child did not make progress")
        again = Store(self.dir)
        self.addCleanup(again.close)
        ids = [e["id"] for e in again.events()]
        self.assertGreaterEqual(len(ids), seen)                      # nothing acknowledged was lost
        self.assertEqual(ids, list(range(1, len(ids) + 1)))          # and nothing is torn or out of order
        self.assertTrue(again.integrity_ok())

    def test_an_uncommitted_transaction_vanishes_and_does_not_block_anyone(self):
        self.store.close()
        proc = self.spawn(CHILD_UNCOMMITTED)
        self.assertEqual(proc.stdout.readline().strip(), "inside")
        proc.kill()
        proc.wait()
        proc.stdout.close()
        again = Store(self.dir)
        self.addCleanup(again.close)
        self.assertEqual(again.events(), [])
        self.assertTrue(again.integrity_ok())
        with again.tx():                                              # the dead writer's lock is gone
            again.add_event("AFTER")
        self.assertEqual([e["type"] for e in again.events()], ["AFTER"])


if __name__ == "__main__":
    unittest.main()
