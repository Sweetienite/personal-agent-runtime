import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from par.harness import check as hc
from tests.helpers import run_cli, tree_digest
from tests.world import BLOCK, SECRET_TITLE, World


@unittest.skipUnless(shutil.which("git"), "git is required")
class Discovery(World):
    def test_finds_top_level_projects_only(self):
        tops, visited = hc.discover(self.docs, self.core, 4)
        self.assertEqual([p.name for p in tops], ["proj_a", "proj_b"])
        self.assertGreater(visited, 5)

    def test_depth_limit(self):
        tops, _ = hc.discover(self.docs, self.core, 8)
        self.assertIn("too_deep", [p.name for p in tops], "found once the depth limit allows it")
        self.assertNotIn("too_deep", [p.name for p in hc.discover(self.docs, self.core, 4)[0]])

    def test_symlinks_hidden_and_dependency_folders_are_not_followed(self):
        names = {p.name for p in hc.discover(self.docs, self.core, 8)[0]}
        self.assertFalse(names & {"linked", "outside_world", "x", "y"}, names)


@unittest.skipUnless(shutil.which("git"), "git is required")
class Snapshot(World):
    def test_structure_facts(self):
        snap = self.check()
        self.assertEqual(snap["core"]["versions"], {"manifest": "1.3.0", "readme": "1.3.0", "changelog": "1.3.0"})
        self.assertTrue(snap["core"]["present"])
        self.assertIn("README.md", snap["core"]["tracked"])
        self.assertEqual(snap["sync"], {"ran": True, "exit": 0, "ok": 1})
        self.assertEqual(len(snap["projects"]), 2)
        pa = snap["projects"]["~/Documents/proj_a"]
        self.assertEqual(pa["agents"]["markers"], [1, 1])
        self.assertTrue(pa["agents"]["block_matches_core"])
        self.assertTrue(pa["claude_thin_bridge"])
        self.assertEqual(pa["task_dirs"], {"active": 1, "archive": 0})
        self.assertEqual(pa["state"]["lines"], 5)
        g = pa["git"]
        self.assertTrue(g["is_repo"])
        # untracked: the sensitive-looking file plus reports/snap/.ai/STATE.md; "runtime/" is wholly untracked
        # and git would collapse it to one line in its default mode, hiding the sensitive path inside
        self.assertEqual((g["modified"], g["untracked"], g["sensitive_paths_in_status"]), (1, 2, 1))
        self.assertFalse(g["status_truncated"])
        pb = snap["projects"]["~/Documents/proj_b"]
        self.assertEqual(pb["agents"]["markers"], [0, 0])
        self.assertFalse(pb["git"]["is_repo"])

    def test_sensitive_path_inside_a_wholly_untracked_folder_is_counted(self):
        repo = self.docs / "lonely_repo"
        (repo / "runtime" / "accounts").mkdir(parents=True)
        (repo / "runtime" / "accounts" / "y.json").write_text("{}")
        (repo / "README.md").write_text("hi")
        self.git(repo.parent, "init", "-q", "-b", "main", str(repo))
        facts = hc.git_facts(repo, self.home)
        self.assertEqual((facts["untracked"], facts["sensitive_paths_in_status"]), (2, 1))

    def test_status_listing_is_capped(self):
        repo = self.docs / "noisy_repo"
        repo.mkdir()
        self.git(repo.parent, "init", "-q", "-b", "main", str(repo))
        for i in range(5):
            (repo / f"f{i}.txt").write_text("x")
        with mock.patch.object(hc, "STATUS_LINE_CAP", 3):
            facts = hc.git_facts(repo, self.home)
        self.assertTrue(facts["status_truncated"])
        self.assertEqual(facts["untracked"], 3)

    def test_snapshot_keeps_structure_only(self):
        text = json.dumps(self.check(), ensure_ascii=False)
        for forbidden in (SECRET_TITLE, "private/path", "x.json", "accounts", "TASK-1", str(self.home)):
            self.assertNotIn(forbidden, text, forbidden)

    def test_hygiene_lines_are_scrubbed_before_they_are_stored(self):
        hy = self.check()["projects"]["~/Documents/proj_a"]["hygiene"]
        self.assertEqual(hy["exit"], 1)
        messages = [m for _, m in hy["findings"]]
        self.assertIn("STATE.md has 2 section(s) outside the Core list (ignore if the project documents the exception)", messages)
        self.assertFalse([m for m in messages if m.startswith("nested STATE: ")])
        self.assertIn("nested STATE.md files present: 1", messages)

    def test_managed_block_drift_is_detected(self):
        (self.a / "AGENTS.md").write_text(BLOCK.replace("- rule", "- edited rule") + "\n")
        pa = self.check()["projects"]["~/Documents/proj_a"]
        self.assertIs(pa["agents"]["block_matches_core"], False)
        self.assertIn(hc.Finding(hc.FAIL, "~/Documents/proj_a", "managed block differs from the Core template"),
                      hc.evaluate(self.check()))

    def test_absent_core_degrades_gracefully(self):
        snap = self.check(core=self.base / "no_core")
        self.assertFalse(snap["core"]["present"])
        self.assertEqual(snap["sync"], {"ran": False})
        self.assertIsNone(snap["projects"]["~/Documents/proj_a"]["hygiene"])
        findings = hc.evaluate(snap)
        self.assertNotIn(hc.FAIL, {f.level for f in findings}, "nothing to fail on when there is no Core to compare with")
        self.assertTrue([f for f in findings if f.subject == "core" and f.level == hc.INFO])
        self.assertEqual(len([f for f in findings if "structural check skipped" in f.message]), 2)
        self.assertTrue([f for f in findings if "sensitive-looking" in f.message],
                        "git-derived warnings do not depend on the Core and must survive")


@unittest.skipUnless(shutil.which("git"), "git is required")
class ReadOnlyGuarantees(World):
    def test_inspected_folders_are_byte_identical_afterwards(self):
        before = tree_digest(self.docs)
        self.check()
        hc.evaluate(self.check())
        self.assertEqual(before, tree_digest(self.docs), "git internals included: no index refresh, no lock files")

    def test_only_allowlisted_commands_are_started(self):
        seen = []
        real = subprocess.run

        def spy(argv, *a, **kw):
            seen.append(list(argv))
            return real(argv, *a, **kw)

        with mock.patch("par.harness.check.subprocess.run", spy):
            self.check()
        self.assertTrue(seen)
        git = shutil.which("git")
        for argv in seen:
            if argv[0] == git:
                self.assertIn("--no-optional-locks", argv)
                self.assertIn(argv[8], {"rev-parse", "log", "status", "rev-list", "config", "ls-files"}, argv)
            else:
                self.assertEqual(argv[:3], [sys.executable, "-B", "-I"])
                self.assertIn(Path(argv[3]).name, {"harness_hygiene.py", "sync_runtime_core.py"})

    def test_allowlist_rejects_everything_else(self):
        git = shutil.which("git")
        pre = ["-c", "core.fsmonitor=false", "-c", "core.quotePath=false", "--no-optional-locks", "-C", "/x"]
        for argv in (
            [git, "push"], [git, *pre, "commit", "-m", "x"], [git, *pre, "add", "-A"], [git, *pre, "reset", "--hard"],
            [git, *pre, "config", "user.name", "x"], [git, "-C", "/x", "status"],
            ["rm", "-rf", "/"], [sys.executable, "-B", "-I", "/tmp/evil.py"], [sys.executable, "script.py"],
            [sys.executable, "-B", "-I", str(self.core / "scripts" / "init_project_harness.py")],
        ):
            self.assertFalse(hc._allowed(argv), argv)
            with self.assertRaises(ValueError):
                hc._run(argv, self.home)
        self.assertTrue(hc._allowed([git, *pre, "config", "--get", "core.hooksPath"]))
        self.assertTrue(hc._allowed([sys.executable, "-B", "-I", "/c/scripts/harness_hygiene.py", "/p", "--strict"]))


class Evaluate(unittest.TestCase):
    def snap(self, **over):
        s = {"core": {"path": "~/c", "present": True, "versions": {"manifest": "1", "readme": "1", "changelog": "1"},
                      "git": {"is_repo": True, "modified": 0, "staged": 0, "untracked": 0}},
             "sync": {"ran": True, "exit": 0, "ok": 1}, "projects": {}}
        s.update(over)
        return s

    def project(self, **over):
        p = {"agents": {"markers": [1, 1], "block_matches_core": True}, "git": {"is_repo": False},
             "hygiene": {"exit": 0, "findings": []}}
        p.update(over)
        return {"~/p": p}

    def find(self, snap):
        return [(f.level, f.subject, f.message) for f in hc.evaluate(snap)]

    def test_healthy_is_silent(self):
        self.assertEqual(self.find(self.snap(projects=self.project())), [])

    def test_core_rules(self):
        s = self.snap()
        s["core"]["versions"] = {"manifest": "1.3.0", "readme": "1.2.0", "changelog": "1.3.0"}
        self.assertEqual(self.find(s)[0][:2], ("FAIL", "core"))
        s = self.snap()
        s["core"]["versions"]["readme"] = None
        self.assertEqual(self.find(s)[0][:2], ("FAIL", "core"))
        s = self.snap()
        s["core"]["git"].update(modified=2, untracked=1)
        self.assertEqual(self.find(s), [("WARN", "core", "working tree has uncommitted changes (2 modified, 0 staged, 1 untracked)")])
        s = self.snap(sync={"ran": True, "exit": 1, "ok": 0})
        self.assertEqual(self.find(s)[0][:2], ("FAIL", "core"))

    def test_marker_rules(self):
        for markers, match, expect in (([0, 0], None, 0), ([2, 1], None, 1), ([1, 1], False, 1), ([1, 1], True, 0)):
            s = self.snap(projects=self.project(agents={"markers": markers, "block_matches_core": match}))
            self.assertEqual(len([x for x in self.find(s) if x[0] == "FAIL"]), expect, (markers, match))

    def test_git_rules(self):
        git = {"is_repo": True, "modified": 1, "staged": 0, "untracked": 2, "sensitive_paths_in_status": 2,
               "ahead": 3, "behind": 1}
        msgs = {m.split(" ")[0] + " " + lv for lv, _, m in self.find(self.snap(projects=self.project(git=git)))}
        found = self.find(self.snap(projects=self.project(git=git)))
        self.assertEqual({lv for lv, _, _ in found}, {"WARN", "INFO"})
        self.assertTrue(any("2 sensitive-looking path(s)" in m for _, _, m in found))
        self.assertTrue(any(m == "uncommitted: 1 modified, 2 untracked" for _, _, m in found))
        self.assertTrue(any("3 commit(s) ahead" in m for _, _, m in found))
        self.assertTrue(any(lv == "WARN" and "1 commit(s) behind" in m for lv, _, m in found))
        self.assertTrue(msgs)

    def test_hygiene_mapping(self):
        hy = {"exit": 1, "findings": [["WARN", "w"], ["REVIEW", "r"], ["INFO", "i"]]}
        found = self.find(self.snap(projects=self.project(hygiene=hy)))
        self.assertEqual(found, [("WARN", "~/p", "review: r"), ("WARN", "~/p", "w"), ("INFO", "~/p", "i")])

    def test_hygiene_unavailable(self):
        found = self.find(self.snap(projects=self.project(hygiene=None)))
        self.assertEqual(found[0][:2], ("INFO", "~/p"))

    def test_fail_sorts_first(self):
        s = self.snap(sync={"ran": True, "exit": 2, "ok": 0},
                      projects=self.project(hygiene={"exit": 1, "findings": [["WARN", "w"]]}))
        self.assertEqual([lv for lv, _, _ in self.find(s)], ["FAIL", "WARN"])


class Compare(unittest.TestCase):
    def test_diff_kinds_and_volatile_keys(self):
        a = {"taken_at": "1", "discovery": {"dirs_visited": 5}, "projects": {"~/p": {"state": {"bytes": 10, "sha256": "aa"}}, "~/q": {"x": 1}}}
        b = {"taken_at": "2", "discovery": {"dirs_visited": 99}, "projects": {"~/p": {"state": {"bytes": 11, "sha256": "aa"}}, "~/r": {"x": 1}}}
        self.assertEqual(hc.compare(a, b), [
            "CHANGED projects.~/p.state.bytes: 10 -> 11",
            "REMOVED projects.~/q.x (was 1)",
            "ADDED   projects.~/r.x = 1",
        ])
        self.assertEqual(hc.compare(a, a), [])


@unittest.skipUnless(shutil.which("git"), "git is required")
class Baseline(World):
    def setUp(self):
        super().setUp()
        self.init_data()

    def test_save_load_and_rotation(self):
        snap = self.check()
        path = hc.save_baseline(self.data, snap)
        self.assertEqual(path, self.data / "harness" / "baseline.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
        loaded, err = hc.load_baseline(self.data)
        self.assertIsNone(err)
        self.assertEqual(loaded["schema_version"], hc.SCHEMA_VERSION)
        self.assertEqual(hc.compare(loaded, snap), [])
        hc.save_baseline(self.data, snap)
        self.assertTrue((path.parent / "baseline.prev.json").is_file())
        self.assertFalse((path.parent / "baseline.json.tmp").exists())

    def test_no_baseline_yet(self):
        self.assertEqual(hc.load_baseline(self.data), (None, None))

    def test_unreadable_or_foreign_baseline_is_reported_not_fatal(self):
        folder = self.data / "harness"
        folder.mkdir()
        (folder / "baseline.json").write_text("{ not json")
        self.assertEqual(hc.load_baseline(self.data), (None, "baseline file is unreadable"))
        (folder / "baseline.json").write_text('{"schema_version": 99}')
        self.assertEqual(hc.load_baseline(self.data)[1], "baseline has an unsupported schema_version")

    def test_a_change_is_reported_against_the_baseline(self):
        hc.save_baseline(self.data, self.check())
        (self.b / ".ai" / "STATE.md").write_text("# Current State\n\n## Baseline\n\n- much longer now\n")
        baseline, _ = hc.load_baseline(self.data)
        diffs = hc.compare(baseline, self.check())
        self.assertTrue(any(".state.sha256" in d and "proj_b" in d for d in diffs), diffs)
        self.assertFalse(any("proj_a" in d for d in diffs), diffs)

    def test_symlinked_baseline_folder_is_refused(self):
        outside = self.base / "outside_dir"
        outside.mkdir()
        os.symlink(outside, self.data / "harness")
        from par.core.policy import PolicyViolation
        with self.assertRaises(PolicyViolation):
            hc.save_baseline(self.data, self.check())
        self.assertEqual(list(outside.iterdir()), [])


@unittest.skipUnless(shutil.which("git"), "git is required")
class Cli(World):
    def args(self, *extra):
        return ["harness", "check", "--data-dir", str(self.data), "--core", str(self.core), "--root", str(self.docs), *extra]

    def setUp(self):
        super().setUp()
        self.init_data()
        self.patch = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def test_json_report_and_exit_code_zero_when_no_fail(self):
        code, out, _ = run_cli(*self.args("--json"))
        doc = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(set(doc), {"snapshot", "findings", "differences", "baseline"})
        self.assertIsNone(doc["differences"])
        self.assertIn("none yet", doc["baseline"])
        self.assertTrue(any(f["level"] == "WARN" for f in doc["findings"]))

    def test_a_fail_finding_gives_exit_1(self):
        (self.core / ".sync_exit").write_text("1")
        code, out, _ = run_cli(*self.args())
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] core: sync_runtime_core --check exited 1", out)

    def test_baseline_cycle(self):
        code, out, _ = run_cli(*self.args("--save-baseline"))
        self.assertEqual(code, 0)
        self.assertIn("Baseline saved", out)
        code, out, _ = run_cli(*self.args())
        self.assertEqual(code, 0)
        self.assertIn("0 difference(s)", out)
        (self.a / ".ai" / "STATE.md").write_text("# Current State\n\n- different\n")
        code, out, _ = run_cli(*self.args())
        self.assertEqual(code, 1, "a difference from the baseline needs attention")
        self.assertIn("CHANGED projects.~/Documents/proj_a.state.sha256", out)
        code, _, _ = run_cli(*self.args("--no-baseline"))
        self.assertEqual(code, 0)
        code, _, _ = run_cli(*self.args("--save-baseline"))
        self.assertEqual(code, 0, "accepting the new state as the baseline resets the alarm")
        self.assertEqual(run_cli(*self.args())[0], 0)

    def test_a_vanished_harness_is_an_alarm_not_silence(self):
        """If a background job loses read access to Documents, the check sees no Core and no projects.
        With a baseline that is a large difference (exit 1), never a quiet 'healthy'."""
        self.assertEqual(run_cli(*self.args("--save-baseline"))[0], 0)
        empty = self.base / "unreadable_stand_in"
        empty.mkdir()
        code, out, _ = run_cli("harness", "check", "--data-dir", str(self.data), "--core", str(empty / "core"), "--root", str(empty))
        self.assertEqual(code, 1)
        self.assertIn("NOT FOUND", out)
        self.assertIn("REMOVED projects.~/Documents/proj_a", out)

    def test_save_baseline_needs_an_initialised_data_dir(self):
        shutil.rmtree(self.data)
        code, _, err = run_cli(*self.args("--save-baseline"))
        self.assertEqual(code, 2)
        self.assertIn("par init", err)

    def test_usage_errors(self):
        self.assertEqual(run_cli(*self.args("--max-depth", "0"))[0], 2)
        self.assertEqual(run_cli("harness", "check", "--data-dir", str(self.data), "--core", "relative/core")[0], 2)
        self.assertEqual(run_cli("harness")[0], 2)

    def test_bad_projects_toml_is_a_config_error(self):
        (self.data / "config" / "projects.toml").write_text("schema_version = 1\n[harness]\nmax_depth = 99\n")
        code, _, err = run_cli("harness", "check", "--data-dir", str(self.data))
        self.assertEqual(code, 2)
        self.assertIn("max_depth", err)

    def test_projects_toml_supplies_the_settings(self):
        (self.data / "config" / "projects.toml").write_text(
            f'schema_version = 1\n[harness]\ncore_path = "{self.core}"\nsearch_root = "{self.docs}"\nmax_depth = 3\n')
        code, out, _ = run_cli("harness", "check", "--data-dir", str(self.data), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["snapshot"]["discovery"]["max_depth"], 3)

    def test_output_never_shows_the_home_path_or_project_text(self):
        for extra in ((), ("--json",)):
            _, out, _ = run_cli(*self.args(*extra))
            self.assertNotIn(str(self.home), out)
            self.assertNotIn(SECRET_TITLE, out)
            self.assertNotIn("x.json", out)

    def test_cli_run_leaves_the_inspected_folders_untouched(self):
        before = tree_digest(self.docs)
        run_cli(*self.args())
        run_cli(*self.args("--save-baseline"))
        self.assertEqual(before, tree_digest(self.docs))


if __name__ == "__main__":
    unittest.main()
