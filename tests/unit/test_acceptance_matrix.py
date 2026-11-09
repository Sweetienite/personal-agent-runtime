"""The 59-case acceptance ledger must stay complete, parseable and honest about its totals."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "acceptance" / "P0-P3-results.md"
EXPECTED = tuple([f"P0-{n:02d}" for n in range(1, 12)] + [f"P1-{n:02d}" for n in range(1, 15)]
                 + [f"P2-{n:02d}" for n in range(1, 20)] + [f"P3-{n:02d}" for n in range(1, 16)])
STATUSES = {"PASS", "FAIL", "NOT_RUN", "BLOCKED"}
CASE = re.compile(r"P[0-3]-\d{2}")


def rows():
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and CASE.fullmatch(cells[0]):
            out.append((cells[0], cells[1:]))
    return out


class Ledger(unittest.TestCase):
    def test_the_ledger_covers_exactly_the_59_matrix_cases_once_each(self):
        found = [case for case, _ in rows()]
        self.assertEqual(found, list(EXPECTED))
        self.assertEqual(len(set(found)), 59)

    def test_every_row_has_an_allowed_status_and_the_summary_matches(self):
        counts = {"PASS": 0, "FAIL": 0, "NOT_RUN": 0, "BLOCKED": 0}
        for case, cells in rows():
            with self.subTest(case=case):
                status = [cell for cell in cells if cell in STATUSES]
                self.assertEqual(len(status), 1, cells)
                counts[status[0]] += 1
        text = LEDGER.read_text(encoding="utf-8")
        self.assertIn("| **合计** | **59** | **56** | **1** | **2** |", text)
        self.assertEqual(counts, {"PASS": 56, "FAIL": 0, "NOT_RUN": 1, "BLOCKED": 2})

    def test_the_two_unmet_gates_are_named_not_hidden(self):
        text = LEDGER.read_text(encoding="utf-8")
        self.assertIn("CLOSED_BY_SCOPE_DECISION", text)
        self.assertIn("P3-07", text)
        self.assertIn("P0-08", text)
        self.assertIn("没有 tag", text)


if __name__ == "__main__":
    unittest.main()
