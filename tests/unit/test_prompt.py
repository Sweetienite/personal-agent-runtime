import unittest

from par.core.contract import parse_contract
from par.core.prompt import build_prompt
from tests.world import sample_contract


def prompt(**over):
    c = parse_contract(sample_contract(**over))
    return build_prompt(c, task_id="fixture-0001", attempt=2, context_text="# Context for project fixture\n", workspace_has_files=True)


class Prompt(unittest.TestCase):
    def test_a_write_task_states_task_boundary_and_checks(self):
        text = prompt()
        for needle in ("# Task fixture-0001 (attempt 2): Fix add()", "Risk tier: R2", "Make add() return the sum.", "- tests pass",
                       "unit: python3 -m unittest -q", "only under: app/", "Your own statement about tests is not used",
                       "# Context for project fixture"):
            self.assertIn(needle, text)

    def test_read_only_tasks_say_so(self):
        c = parse_contract(sample_contract(risk="R1", allowed_paths=[], acceptance={"criteria": ["x"], "checks": []}))
        self.assertIn("read-only: do not create, change or delete", build_prompt(c, task_id="f-0001", attempt=1, context_text="c", workspace_has_files=True))
        self.assertIn("You have no project files", build_prompt(c, task_id="f-0001", attempt=1, context_text="c", workspace_has_files=False))

    def test_protected_places_are_always_named(self):
        for risk_doc in (sample_contract(), sample_contract(risk="R0", allowed_paths=[], acceptance={"criteria": [], "checks": []})):
            c = parse_contract(risk_doc)
            text = build_prompt(c, task_id="f-0001", attempt=1, context_text="c", workspace_has_files=True)
            self.assertIn("Never touch .git, the .ai/ folder, AGENTS.md, CLAUDE.md", text)

    def test_it_is_deterministic_and_carries_no_history(self):
        self.assertEqual(prompt(), prompt())
        self.assertNotIn("previous attempt", prompt().lower())

    def test_reporting_requires_a_file_by_file_summary_and_an_explicit_nothing_changed_case(self):
        text = prompt()
        self.assertIn("Name every file you changed and what changed in it.", text)
        self.assertIn("If you changed nothing, say so explicitly and why", text)
        self.assertIn("not treated as proof", text)


if __name__ == "__main__":
    unittest.main()
