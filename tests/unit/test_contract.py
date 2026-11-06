import json
import unittest

from par.core.contract import (
    Contract, ContractError, canonical_json, check_argv_problem, forbidden_reason, normalise_allowed_path,
    parse_contract, path_allowed,
)
from tests.world import sample_contract

R1 = dict(risk="R1", allowed_paths=[], acceptance={"criteria": ["summarise the module"], "checks": []})


def problems_of(doc_or_text):
    with unittest.TestCase().assertRaises(ContractError) as ctx:
        parse_contract(doc_or_text)
    return ctx.exception.problems


class Parsing(unittest.TestCase):
    def test_a_valid_contract_is_normalised_with_every_field_present(self):
        minimal = {"schema_version": 1, "project_id": "fixture", "title": "T", "description": "D", "risk": "R0",
                   "idempotency_key": "k"}
        c = parse_contract(minimal)
        self.assertIsInstance(c, Contract)
        self.assertEqual(c.doc["allowed_paths"], [])
        self.assertEqual(c.doc["acceptance"], {"criteria": [], "checks": []})
        self.assertEqual((c.role, c.worker, c.ad_hoc, c.dependencies), (None, None, False, ()))
        self.assertEqual(c.timeouts(), {"startup": 60.0, "idle": 300.0, "total": 1200.0})

    def test_checks_get_their_defaults(self):
        c = parse_contract(sample_contract(acceptance={"criteria": [], "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest"]}]}))
        self.assertEqual(c.checks[0], {"name": "unit", "argv": ["python3", "-m", "unittest"], "timeout": 120.0, "expect_exit": 0})

    def test_json_text_and_dict_give_the_same_digest(self):
        doc = sample_contract()
        self.assertEqual(parse_contract(json.dumps(doc)).sha256, parse_contract(doc).sha256)
        self.assertEqual(parse_contract(json.dumps(doc).encode()).sha256, parse_contract(doc).sha256)

    def test_the_digest_ignores_key_order_and_spelled_out_defaults(self):
        a = sample_contract()
        b = dict(reversed(list(sample_contract().items())))
        b["dependencies"] = []
        b["timeouts"] = {}
        self.assertEqual(parse_contract(a).sha256, parse_contract(b).sha256)

    def test_the_digest_changes_with_the_content(self):
        base = parse_contract(sample_contract()).sha256
        self.assertNotEqual(base, parse_contract(sample_contract(description="Different.")).sha256)
        self.assertNotEqual(base, parse_contract(sample_contract(allowed_paths=["app/", "lib/"])).sha256)
        self.assertNotEqual(base, parse_contract(sample_contract(timeouts={"total": 99})).sha256)

    def test_bad_json_is_reported_not_raised_raw(self):
        for text in ("{", "[]", "null", '{"a": NaN}', '{"a": 1, "a": 2}', ""):
            self.assertTrue(problems_of(text), text)

    def test_duplicate_keys_are_ambiguous_and_refused(self):
        text = json.dumps(sample_contract())[:-1] + ', "title": "Another"}'
        self.assertIn("duplicate key", problems_of(text)[0])

    def test_oversized_contracts_are_refused(self):
        self.assertIn("larger than", problems_of(json.dumps(sample_contract(description="x" * 20000)) + " " * 300000)[0])

    def test_schema_violations_are_listed_together(self):
        found = problems_of({"schema_version": 2, "project_id": "Bad Id", "title": ""})
        self.assertGreaterEqual(len(found), 4)

    def test_unknown_fields_are_refused(self):
        self.assertTrue(any("unexpected 'sneaky'" in p for p in problems_of(sample_contract(sneaky=1))))

    def test_title_must_be_one_line(self):
        self.assertTrue(problems_of(sample_contract(title="a\nb")))


class RiskRules(unittest.TestCase):
    def test_r3_is_never_run(self):
        found = problems_of(sample_contract(risk="R3"))
        self.assertTrue(any("R3" in p for p in found))

    def test_r2_needs_allowed_paths(self):
        self.assertTrue(any("needs allowed_paths" in p for p in problems_of(sample_contract(allowed_paths=[]))))

    def test_read_only_tiers_cannot_have_paths_or_checks(self):
        for risk in ("R0", "R1"):
            self.assertTrue(any("allowed_paths must be empty" in p for p in problems_of(sample_contract(risk=risk))))
            only_checks = sample_contract(risk=risk, allowed_paths=[])
            self.assertTrue(any("cannot have acceptance.checks" in p for p in problems_of(only_checks)))
            parse_contract(sample_contract(risk=risk, allowed_paths=[], acceptance={"criteria": ["x"], "checks": []}))

    def test_ad_hoc_is_a_valid_contract_shape_at_parse_time(self):
        parse_contract(sample_contract(ad_hoc=True))
        parse_contract(sample_contract(ad_hoc=True, **R1))

    def test_an_r2_task_without_checks_is_allowed_but_flagged(self):
        c = parse_contract(sample_contract(acceptance={"criteria": ["reads well"], "checks": []}))
        self.assertTrue(any("no mechanical check" in w for w in c.warnings))
        self.assertEqual(parse_contract(sample_contract()).warnings, ())

    def test_timeouts_must_be_consistent(self):
        self.assertTrue(problems_of(sample_contract(timeouts={"total": 10, "idle": 60})))
        self.assertTrue(problems_of(sample_contract(timeouts={"total": 0})))
        parse_contract(sample_contract(timeouts={"startup": 5, "idle": 20, "total": 60}))

    def test_credentials_in_a_contract_are_refused_without_echoing_them(self):
        secret = "sk-ant-abcdefghijklmnopqrstuvwxyz012345"
        for field in ({"description": f"use key {secret}"}, {"title": f"fix {secret}"},
                      {"acceptance": {"criteria": [f"token {secret}"], "checks": []}}):
            found = problems_of(sample_contract(**field))
            self.assertTrue(any("credential" in p for p in found))
            self.assertNotIn(secret, " ".join(found))

    def test_duplicate_check_names_are_refused(self):
        chk = {"name": "unit", "argv": ["python3", "-m", "unittest"]}
        self.assertTrue(any("duplicate name" in p for p in problems_of(sample_contract(acceptance={"criteria": [], "checks": [chk, chk]}))))


class AllowedPaths(unittest.TestCase):
    def test_clean_forms(self):
        self.assertEqual(normalise_allowed_path("app/"), "app/")
        self.assertEqual(normalise_allowed_path("./app//sub/"), "app/sub/")
        self.assertEqual(normalise_allowed_path("app/x.py"), "app/x.py")
        self.assertEqual(normalise_allowed_path("app/../lib/y.py"), "lib/y.py")

    def test_rejected_forms(self):
        for bad in ("/etc/passwd", "~/x", "..", "../x", "a/../../x", ".", "./", "app/*.py", "app/[ab]", "{a,b}/", "a\\b", "a\nb",
                    ".git/config", "x/.git/hooks/pre-commit", ".ai/STATE.md", "AGENTS.md", "docs/CLAUDE.md", ".env",
                    "config/id_rsa", "secrets/prod.pem"):
            with self.assertRaises(ValueError, msg=bad):
                normalise_allowed_path(bad)

    def test_contract_level_errors_name_the_entry(self):
        found = problems_of(sample_contract(allowed_paths=["app/", "../outside", "app/"]))
        self.assertTrue(any("'../outside'" in p for p in found))
        self.assertTrue(any("duplicate" in p for p in found))

    def test_matching_is_prefix_for_directories_and_exact_for_files(self):
        allowed = ("app/", "docs/readme.md")
        self.assertTrue(path_allowed("app/x.py", allowed))
        self.assertTrue(path_allowed("app/sub/y.py", allowed))
        self.assertTrue(path_allowed("docs/readme.md", allowed))
        self.assertFalse(path_allowed("application/x.py", allowed))       # "app/" is not a string prefix match on "app"
        self.assertFalse(path_allowed("docs/readme.md.bak", allowed))
        self.assertFalse(path_allowed("docs/other.md", allowed))
        self.assertFalse(path_allowed("app", allowed))

    def test_forbidden_paths_regardless_of_allow_lists(self):
        for bad in (".git/config", "sub/.git/x", ".ai/tasks/active/t.md", "AGENTS.md", "pkg/CLAUDE.md", ".env", "k/id_rsa"):
            self.assertIsNotNone(forbidden_reason(bad), bad)
        for fine in ("app/main.py", "README.md", "docs/guide.md", ".github/workflows/ci.yml", "src/ai/model.py"):
            self.assertIsNone(forbidden_reason(fine), fine)


class CheckCommands(unittest.TestCase):
    def ok(self, *argv):
        self.assertIsNone(check_argv_problem(list(argv)), argv)

    def bad(self, *argv):
        self.assertIsNotNone(check_argv_problem(list(argv)), argv)

    def test_ordinary_test_runs_are_allowed(self):
        self.ok("python3", "-m", "unittest", "-q")
        self.ok("python3", "-B", "-m", "unittest", "discover", "-s", "tests")
        self.ok("python", "-m", "pytest", "-x", "tests/test_a.py")
        self.ok("python3", "tests/run_all.py")
        self.ok("pytest", "-q")
        self.ok("npm", "test")
        self.ok("npm", "run", "build")
        self.ok("node", "--test")
        self.ok("node", "scripts/check.js")
        self.ok("make", "test")
        self.ok("make", "-k", "-j4", "lint", "test")
        self.ok("go", "test", "./...")
        self.ok("cargo", "test", "--quiet")
        self.ok("swift", "test")
        self.ok("ruff", "check", "src")
        self.ok("tsc", "--noEmit")

    def test_executables_outside_the_allowlist_are_refused(self):
        for exe in ("bash", "sh", "zsh", "curl", "rm", "git", "ssh", "osascript", "open", "sudo", "env", "/bin/ls", "./run.sh"):
            self.bad(exe, "-c", "echo hi")

    def test_inline_code_is_refused(self):
        self.bad("python3", "-c", "print(1)")
        self.bad("python3", "-B", "-c", "print(1)")
        self.bad("python3")
        self.bad("python3", "-m", "pip", "install", "x")
        self.bad("python3", "-m", "http.server")
        self.bad("python3", "script.sh")
        self.bad("node", "-e", "1")
        self.bad("node", "--require", "x.js", "a.js")
        self.bad("node")

    def test_install_and_publish_commands_are_refused(self):
        for argv in (("npm", "install"), ("npm", "i", "left-pad"), ("npm", "publish"), ("npm", "exec", "x"), ("npm",),
                     ("go", "get", "x"), ("go", "run", "main.go"), ("cargo", "install", "x"), ("cargo", "publish"),
                     ("swift", "package", "update"), ("go",)):
            self.bad(*argv)

    def test_make_cannot_pick_another_makefile_or_directory(self):
        self.bad("make", "-f", "/tmp/evil.mk")
        self.bad("make", "-C", "..")
        self.bad("make", "--file=x")
        self.bad("make", "a;b")

    def test_paths_must_stay_inside_the_workspace(self):
        self.bad("python3", "-m", "unittest", "/tmp/x")
        self.bad("python3", "-m", "unittest", "../other/tests")
        self.bad("pytest", "--rootdir=/")
        self.bad("pytest", "~/tests")
        self.bad("python3", "/Users/me/evil.py")

    def test_a_bad_check_is_reported_at_parse_time(self):
        found = problems_of(sample_contract(acceptance={"criteria": [], "checks": [{"name": "x", "argv": ["bash", "-c", "rm -rf ~"]}]}))
        self.assertTrue(any("acceptance.checks[0] (x)" in p and "not an allowed" in p for p in found))


class ControlCharacters(unittest.TestCase):
    def test_terminal_control_characters_are_refused_everywhere_a_person_will_read_them(self):
        esc = "\x1b[2J\x1b]0;pwned\x07"
        for field in ({"title": f"Fix {esc}"}, {"description": f"do it {esc}"}, {"title": "two\nlines"}, {"description": "bell\x07"},
                      {"acceptance": {"criteria": [f"ok {esc}"], "checks": []}},
                      {"acceptance": {"criteria": [], "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", f"x{esc}"]}]}},
                      {"description": "carriage\rreturn"}, {"description": "c1 control \x9b31m"}):
            self.assertTrue(problems_of(sample_contract(**field)), field)

    def test_newlines_and_tabs_are_fine_in_the_description(self):
        parse_contract(sample_contract(description="Line one.\n\tIndented line two.\n"))


class Canonical(unittest.TestCase):
    def test_canonical_json_is_stable(self):
        self.assertEqual(canonical_json({"b": 1, "a": [1, 2]}), '{"a":[1,2],"b":1}')
        self.assertEqual(canonical_json({"é": "é"}), '{"é":"é"}')


if __name__ == "__main__":
    unittest.main()
