import unittest
from datetime import timezone

from par.core import clock
from par.core.states import (
    ACTIVE, NEXT_ACTIONS, TRANSITIONS, IllegalTransition, TaskStatus as T, can_transition, check_transition,
    reachable_from, transition_rows, transitions_digest,
)


class TransitionTable(unittest.TestCase):
    def test_every_status_has_a_row_and_a_next_action_entry(self):
        self.assertEqual(set(TRANSITIONS), set(T))
        self.assertEqual(set(NEXT_ACTIONS), set(T))

    def test_everything_is_reachable_from_draft(self):
        self.assertEqual(reachable_from(T.DRAFT), set(T))

    def test_nothing_goes_back_to_draft(self):
        self.assertFalse([s for s, targets in TRANSITIONS.items() if T.DRAFT in targets])

    def test_closed_is_the_only_dead_end(self):
        self.assertEqual([s for s, targets in TRANSITIONS.items() if not targets], [T.CLOSED])

    def test_the_jumps_the_plan_forbids(self):
        for src, dst in (
            (T.RUNNING, T.ACCEPTED),       # bypasses verification and review
            (T.STARTING, T.ACCEPTED),
            (T.VERIFYING, T.ACCEPTED),
            (T.ACCEPTED, T.RUNNING),       # no in-place re-run
            (T.ACCEPTED, T.QUEUED),
            (T.CANCELED, T.RUNNING),       # a canceled task needs a NEW attempt via QUEUED
            (T.CANCELED, T.STARTING),
            (T.CLOSED, T.QUEUED),
            (T.FAILED, T.ACCEPTED),
            (T.DRAFT, T.RUNNING),
            (T.QUEUED, T.RUNNING),         # RUNNING is only entered from STARTING
            (T.REVIEW_REQUIRED, T.RUNNING),
        ):
            self.assertFalse(can_transition(src, dst), f"{src.value} -> {dst.value}")
            with self.assertRaises(IllegalTransition):
                check_transition(src, dst)

    def test_the_only_way_to_accepted_is_review(self):
        self.assertEqual([s for s, targets in TRANSITIONS.items() if T.ACCEPTED in targets], [T.REVIEW_REQUIRED])
        self.assertEqual([s for s, targets in TRANSITIONS.items() if T.REVIEW_REQUIRED in targets], [T.VERIFYING])

    def test_a_canceled_task_can_only_retry_through_the_queue(self):
        self.assertEqual(TRANSITIONS[T.CANCELED], frozenset({T.QUEUED}))
        self.assertTrue(can_transition(T.QUEUED, T.STARTING))

    def test_only_active_states_can_reach_recovery_required(self):
        sources = {s for s, targets in TRANSITIONS.items() if T.RECOVERY_REQUIRED in targets}
        self.assertEqual(sources, set(ACTIVE))

    def test_strings_are_accepted_and_garbage_is_not(self):
        self.assertTrue(can_transition("QUEUED", "STARTING"))
        self.assertFalse(can_transition("QUEUED", "NOT_A_STATUS"))
        self.assertFalse(can_transition("NOT_A_STATUS", "QUEUED"))

    def test_rows_and_digest_are_deterministic(self):
        rows = transition_rows()
        self.assertEqual(rows, sorted(rows))
        self.assertEqual(len(rows), sum(len(v) for v in TRANSITIONS.values()))
        self.assertEqual(transitions_digest(), transitions_digest())
        self.assertEqual(len(transitions_digest()), 64)

    def test_every_next_action_is_a_known_verb(self):
        verbs = {"run", "pause", "resume", "cancel", "approve", "reject", "accept", "close", "retry", "recover"}
        self.assertTrue({a for acts in NEXT_ACTIONS.values() for a in acts} <= verbs)


class Clocks(unittest.TestCase):
    def test_iso_is_utc_millisecond_and_sortable(self):
        c = clock.FakeClock()
        a = c.iso()
        c.advance(0.5)
        b = c.iso()
        self.assertTrue(a.endswith("Z") and "." in a)
        self.assertLess(a, b)
        self.assertEqual(clock.parse_iso(a).tzinfo, timezone.utc)
        self.assertEqual(clock.iso(clock.parse_iso(a)), a)

    def test_real_clock_is_timezone_aware(self):
        self.assertIsNotNone(clock.Clock().now().tzinfo)


if __name__ == "__main__":
    unittest.main()
