import dataclasses
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par.config import ProjectEntry
from par.context import harness as H
from par.core.policy import PolicyViolation
from tests.helpers import tree_digest
from tests.world import GOOD_STATE, make_harness_project, run_git

SK = "sk-" + "A1b2C3d4" * 4  # assembled at run time: no literal secret in this file
RULES = "<!-- AI_HARNESS_CORE_BEGIN -->\nrule\n<!-- AI_HARNESS_CORE_END -->\n\n## Project rules\n- a\n"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Fx(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.home / "proj"

    def tearDown(self):
        self._tmp.cleanup()

    def project(self, vcs="auto", write_enabled=False, **kw):
        make_harness_project(self.root, **kw)
        return ProjectEntry("proj", self.root, vcs, write_enabled)

    def adapter(self, entry, **kw):
        return H.HarnessContextAdapter(entry, home=self.home, **kw)

    def git_init(self):
        run_git(self.root.parent, self.home, "init", "-q", "-b", "main", str(self.root))
        run_git(self.root, self.home, "add", "-A")
        run_git(self.root, self.home, "commit", "-q", "-m", "init")

    def kinds(self, manifest):
        return {(e.kind, e.path): e for e in manifest.entries}


class ViewFacts(Fx):
    def test_basic_facts(self):
        a = self.adapter(self.project(agents=RULES))
        v = a.view()
        self.assertTrue(v.exists)
        self.assertEqual((v.rules.path, v.rules.bytes, v.rules.sha256), ("AGENTS.md", len(RULES.encode()), sha(RULES)))
        self.assertEqual((v.managed_markers, v.bridge), ((1, 1), "thin"))
        s = v.state
        self.assertEqual((s.path, s.bytes, s.lines, s.sha256, s.oversized), (".ai/STATE.md", len(GOOD_STATE.encode()), len(GOOD_STATE.splitlines()), sha(GOOD_STATE), False))
        self.assertEqual(v.task_dirs, {"active": 1, "archive": 0})
        self.assertEqual((v.contract_state, v.git), ("one", {"is_repo": False}))

    def test_contract_detection(self):
        for names, expect in (((), "none"), (("A.md",), "one"), (("A.md", "B.md"), "multiple")):
            if self.root.exists():
                shutil.rmtree(self.root)
            self.project(contracts=names)
            active = self.root / ".ai" / "tasks" / "active"
            (active / ".gitkeep").write_text("")      # dotfiles and non-markdown files are not contracts
            (active / "notes.txt").write_text("x")
            self.assertEqual(self.adapter(ProjectEntry("proj", self.root)).view().contract_state, expect, names)

    def test_a_missing_folder_is_a_view_not_a_crash(self):
        a = self.adapter(ProjectEntry("proj", self.base / "gone"))
        v = a.view()
        self.assertFalse(v.exists)
        m = a.manifest(v)
        self.assertIn("AGENTS.md is missing", m.warnings)
        self.assertEqual({b.code for b in a.write_readiness(v).blockers}, {"WRITE_DISABLED", "NO_PROJECT"})

    def test_missing_state_and_rules(self):
        entry = self.project()
        (self.root / ".ai" / "STATE.md").unlink()
        (self.root / "AGENTS.md").unlink()
        a = self.adapter(entry)
        m = a.manifest()
        self.assertTrue({"AGENTS.md is missing", ".ai/STATE.md is missing"} <= set(m.warnings))
        self.assertIn("NO_STATE", [b.code for b in a.write_readiness().blockers])


class SourcePointers(Fx):
    def test_statuses_and_boundaries(self):
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "spec.md").write_text("spec")
        outside = self.base / "outside_dir"
        outside.mkdir()
        (outside / "inside.txt").write_text("x")
        os.symlink(outside, self.root / "link")
        state = (
            "# S\n\n## Sources of Truth\n\n"
            "- Spec: `docs/spec.md`\n- Spec anchor: `docs/spec.md#section-2`\n- Folder: `docs/`\n"
            "- Gone: `docs/missing.md`\n- Abs: `/etc/hosts`\n- Up: `../outside.txt`\n"
            "- Web: `https://example.org/x`\n- Link: `link/inside.txt`\n"
            "- Account: `runtime/accounts/acct_1.json`\n- Env: `.env`\n"
            "\n## Next\n\n- After: `docs/after.md`\n"
        )
        v = self.adapter(self.project(state=state)).view()
        got = [(sp.topic, sp.status) for sp in v.sources]
        self.assertEqual(got, [
            ("Spec", "ok"), ("Spec anchor", "ok"), ("Folder", "directory"), ("Gone", "missing"), ("Abs", "outside_project"),
            ("Up", "outside_project"), ("Web", "not_a_path"), ("Link", "outside_project"),
        ])
        self.assertEqual(v.sensitive_sources, 2, "sensitive-looking pointers are counted and never listed")
        self.assertEqual(v.sources[0].bytes, 4)
        self.assertNotIn("docs/after.md", [sp.path for sp in v.sources], "the section ends at the next heading")
        self.assertNotIn("acct_1", repr(v))

    def test_pointer_cap(self):
        state = "# S\n\n## Sources of Truth\n\n" + "".join(f"- T{i}: `docs/f{i}.md`\n" for i in range(60))
        v = self.adapter(self.project(state=state)).view()
        self.assertEqual((len(v.sources), v.sources_cut), (50, 10))

    def test_secret_in_a_topic_is_redacted(self):
        state = f"# S\n\n## Sources of Truth\n\n- Key {SK}: `docs/a.md`\n"
        v = self.adapter(self.project(state=state)).view()
        self.assertNotIn(SK, repr(v))
        self.assertIn("[REDACTED:api-key]", v.sources[0].topic)

    def test_state_without_a_sources_section(self):
        v = self.adapter(self.project(state="# S\n\n## Baseline\n\n- x\n")).view()
        self.assertEqual((v.sources, v.sources_cut, v.sensitive_sources), ((), 0, 0))


class Manifest(Fx):
    def test_healthy_project(self):
        a = self.adapter(self.project(agents=RULES))
        m = a.manifest()
        e = self.kinds(m)
        self.assertEqual(e[("rules", "AGENTS.md")].mode, "inline")
        self.assertEqual(e[("state", ".ai/STATE.md")].mode, "inline")
        self.assertEqual(e[("contract", ".ai/tasks/active/TASK-1.md")].mode, "inline")
        self.assertEqual(e[("bridge", "CLAUDE.md")].mode, "skip")
        self.assertEqual(m.warnings, ())
        self.assertEqual(m.state_revision, sha(GOOD_STATE))
        self.assertEqual(m.parent_contract_ref["path"], ".ai/tasks/active/TASK-1.md")

    def test_oversized_state_is_a_pointer_with_the_reason_and_a_warning(self):
        big = "# S\n\n## Baseline\n\n" + ("x" * 199 + "\n") * 100
        a = self.adapter(self.project(state=big))
        v = a.view()
        m = a.manifest(v)
        s = self.kinds(m)[("state", ".ai/STATE.md")]
        self.assertTrue(v.state.oversized)
        self.assertEqual((s.mode, s.bytes, s.sha256), ("pointer", len(big.encode()), sha(big)))
        self.assertIn("never inlined", s.reason)
        self.assertTrue([w for w in m.warnings if "oversized" in w])
        self.assertIn("STATE_OVERSIZED", [b.code for b in a.write_readiness(v).blockers])
        self.assertNotIn("xxxxxxxxxx", a.render(m), "the oversized STATE never reaches a Worker's context")

    def test_per_entry_limit_demotes_to_a_pointer_and_never_truncates(self):
        a = self.adapter(self.project(), inline_max_bytes=10)
        m = a.manifest()
        rules = self.kinds(m)[("rules", "AGENTS.md")]
        self.assertEqual(rules.mode, "pointer")
        self.assertIn("exceeds the 10-byte inline limit", rules.reason)
        self.assertEqual({e.kind for e, _ in a.read_inline(m)}, {"state"}, "nothing is partially included")

    def test_total_budget_demotes_from_the_end(self):
        entry = self.project(agents=RULES)
        a = self.adapter(entry, total_inline_max_bytes=len(RULES.encode()) + 1)
        m = a.manifest()
        modes = {e.kind: e.mode for e in m.entries if e.kind in {"rules", "state", "contract"}}
        self.assertEqual(modes, {"rules": "inline", "state": "pointer", "contract": "pointer"})
        self.assertIn("total inline budget", self.kinds(m)[("contract", ".ai/tasks/active/TASK-1.md")].reason)

    def test_claude_md_variants(self):
        a = self.adapter(self.project(claude="custom instructions\n"))
        m = a.manifest()
        self.assertEqual(self.kinds(m)[("bridge", "CLAUDE.md")].mode, "pointer")
        self.assertIn("CLAUDE.md is not the thin @AGENTS.md bridge", m.warnings)
        shutil.rmtree(self.root)
        m = self.adapter(self.project(claude=None)).manifest()
        self.assertNotIn(("bridge", "CLAUDE.md"), self.kinds(m))

    def test_contract_rules(self):
        a = self.adapter(self.project(contracts=("A.md", "B.md")))
        m = a.manifest()
        self.assertTrue([w for w in m.warnings if "2 active contracts" in w])
        self.assertIsNone(m.parent_contract_ref, "with several contracts none is trusted")
        self.assertEqual([e.mode for e in m.entries if e.kind == "contract"], ["pointer", "pointer"])
        shutil.rmtree(self.root)
        a = self.adapter(self.project(contracts=()))
        self.assertIsNone(a.manifest().parent_contract_ref)
        self.assertIn("NO_ACTIVE_CONTRACT", [b.code for b in a.write_readiness().blockers])

    def test_digest_is_stable_and_follows_the_content(self):
        a = self.adapter(self.project())
        m1, m2 = a.manifest(), a.manifest()
        self.assertEqual(m1.digest, m2.digest, "generated_at is not part of the digest")
        (self.root / ".ai" / "STATE.md").write_text(GOOD_STATE + "- more\n")
        m3 = a.manifest()
        self.assertNotEqual(m1.digest, m3.digest)
        self.assertNotEqual(m1.state_revision, m3.state_revision)

    def test_sources_become_pointers_and_bad_ones_are_skipped(self):
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "spec.md").write_text("spec")
        state = "# S\n\n## Sources of Truth\n\n- Spec: `docs/spec.md`\n- Gone: `docs/none.md`\n- Env: `.env`\n"
        m = self.adapter(self.project(state=state)).manifest()
        src = {e.path: e for e in m.entries if e.kind == "source"}
        self.assertEqual((src["docs/spec.md"].mode, src["docs/spec.md"].bytes, src["docs/spec.md"].topic), ("pointer", 4, "Spec"))
        self.assertEqual(src["docs/none.md"].mode, "skip")
        self.assertNotIn(".env", src)
        self.assertEqual(m.skipped_sensitive, 1)


class ReadingWhatWasPromised(Fx):
    def test_inline_text_is_exactly_the_file(self):
        a = self.adapter(self.project(agents=RULES))
        texts = {e.path: t for e, t in a.read_inline(a.manifest())}
        self.assertEqual(texts["AGENTS.md"], RULES)
        self.assertEqual(texts[".ai/STATE.md"], GOOD_STATE)

    def test_a_changed_file_is_detected_even_at_the_same_size(self):
        a = self.adapter(self.project(agents=RULES))
        m = a.manifest()
        (self.root / "AGENTS.md").write_text(RULES.replace("rule", "ruLe"))   # same length, different bytes
        with self.assertRaises(H.ContextChanged) as ctx:
            a.read_inline(m)
        self.assertEqual(str(ctx.exception), "AGENTS.md")

    def test_a_grown_file_is_detected(self):
        a = self.adapter(self.project())
        m = a.manifest()
        (self.root / ".ai" / "STATE.md").write_text(GOOD_STATE + "x")
        with self.assertRaises(H.ContextChanged):
            a.read_inline(m)

    def test_crafted_entries_cannot_reach_outside_or_sensitive_files(self):
        a = self.adapter(self.project())
        m = a.manifest()
        (self.base / "outside.txt").write_text("secret-ish")
        os.symlink(self.base / "outside.txt", self.root / "link.txt")
        (self.root / ".env").write_text("K=V")
        for path in ("../outside.txt", ".env", "link.txt"):
            forged = dataclasses.replace(m, entries=(H.ManifestEntry("rules", path, "inline", 1, "0" * 64),))
            with self.assertRaises(PolicyViolation, msg=path):
                a.read_inline(forged)

    def test_render_contains_inline_text_pointers_and_no_pointer_content(self):
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "spec.md").write_text("POINTER-ONLY-CONTENT")
        state = "# S\n\n## Sources of Truth\n\n- Spec: `docs/spec.md`\n- Env: `.env`\n"
        a = self.adapter(self.project(agents=RULES, state=state))
        out = a.render(a.manifest())
        self.assertIn("# Context for project proj (manifest ", out)
        self.assertIn("## rules: AGENTS.md", out)
        self.assertIn(RULES.strip(), out)
        self.assertIn("- source: docs/spec.md (20 bytes)", out)
        self.assertNotIn("POINTER-ONLY-CONTENT", out)
        self.assertIn("1 sensitive-looking pointer(s) were left out", out)
        pointers = out.split("## Pointers", 1)[1]
        self.assertNotIn(".env", pointers, "the pointer listing the adapter builds never names a sensitive path")


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitBase(Fx):
    def test_base_facts_and_dirty_digest(self):
        entry = self.project(vcs="git")
        self.git_init()
        a = self.adapter(entry)
        m = a.manifest()
        head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual((m.base["vcs"], m.base["head"], m.base["branch"], m.base_git_commit), ("git", head, "main", head))
        self.assertEqual(m.base["dirty"], {"modified": 0, "staged": 0, "untracked": 0})
        clean = m.base["dirty_digest"]
        (self.root / "AGENTS.md").write_text("changed\n")
        dirty = a.manifest()
        self.assertEqual(dirty.base["dirty"]["modified"], 1)
        self.assertNotEqual(dirty.base["dirty_digest"], clean)
        self.assertEqual(dirty.base["head"], head)
        (self.root / "AGENTS.md").write_text("# own rules\n")
        self.assertEqual(a.manifest().base["dirty_digest"], clean, "the digest returns when the tree does")

    def test_new_untracked_file_changes_the_digest_without_leaking_its_name(self):
        entry = self.project(vcs="git")
        self.git_init()
        a = self.adapter(entry)
        before = a.manifest().base["dirty_digest"]
        (self.root / "scratch_notes.txt").write_text("x")
        m = a.manifest()
        self.assertNotEqual(m.base["dirty_digest"], before)
        self.assertNotIn("scratch_notes", repr(m.to_dict()))

    def test_declared_vcs_none_ignores_a_repository_and_says_so(self):
        entry = self.project(vcs="none")
        self.git_init()
        a = self.adapter(entry)
        v = a.view()
        self.assertEqual(v.git, {"is_repo": False})
        self.assertTrue([n for n in v.notes if "declared 'none'" in n])
        self.assertEqual(a.manifest().base["vcs"], "none")

    def test_declared_git_without_a_repository(self):
        a = self.adapter(self.project(vcs="git"))
        codes = {b.code for b in a.write_readiness().blockers}
        self.assertTrue({"VCS_MISMATCH", "NO_VCS"} <= codes)
        self.assertTrue([n for n in a.view().notes if "no repository" in n])


@unittest.skipUnless(shutil.which("git"), "git is required")
class WriteReadiness(Fx):
    def perfect(self, **kw):
        entry = self.project(vcs="git", **kw)
        self.git_init()
        return entry

    def test_a_perfect_project_is_still_blocked_by_the_registry_in_p1(self):
        a = self.adapter(self.perfect())
        w = a.write_readiness()
        self.assertEqual([b.code for b in w.blockers], ["WRITE_DISABLED"])
        self.assertFalse(w.allowed)

    def test_the_gate_opens_only_when_every_condition_holds(self):
        entry = dataclasses.replace(self.perfect(), write_enabled=True)   # built directly: the loader never allows this in P1
        w = self.adapter(entry).write_readiness()
        self.assertEqual((w.allowed, w.blockers), (True, ()))

    def test_each_condition_blocks_on_its_own(self):
        entry = dataclasses.replace(self.perfect(), write_enabled=True)
        a = self.adapter(entry)
        (self.root / "runtime" / "accounts").mkdir(parents=True)
        (self.root / "runtime" / "accounts" / "x.json").write_text("{}")
        codes = {b.code for b in a.write_readiness().blockers}
        self.assertEqual(codes, {"SENSITIVE_VISIBLE", "DIRTY_BASE"})
        (self.root / "runtime" / "accounts" / "x.json").unlink()
        (self.root / "runtime" / "accounts").rmdir()
        (self.root / "runtime").rmdir()
        run_git(self.root, self.home, "status")
        self.assertEqual(a.write_readiness().blockers, ())
        (self.root / ".ai" / "STATE.md").write_text("# S\n\n## Baseline\n\n" + ("x" * 199 + "\n") * 100)
        self.assertEqual({b.code for b in a.write_readiness().blockers}, {"STATE_OVERSIZED", "DIRTY_BASE"})


@unittest.skipUnless(shutil.which("git"), "git is required")
class ReadOnly(Fx):
    def test_nothing_is_written_not_even_inside_git(self):
        entry = self.project(vcs="git")
        self.git_init()
        (self.root / "wip.txt").write_text("x")
        a = self.adapter(entry)
        before = tree_digest(self.root)
        v = a.view()
        m = a.manifest(v)
        a.render(m)
        a.write_readiness(v)
        self.assertEqual(before, tree_digest(self.root))

    def test_only_allowlisted_commands_are_started(self):
        entry = self.project(vcs="git")
        self.git_init()
        seen, real = [], subprocess.run

        def spy(argv, *a, **kw):
            seen.append(list(argv))
            return real(argv, *a, **kw)

        with mock.patch("par.harness.check.subprocess.run", spy):
            self.adapter(entry).manifest()
        git = shutil.which("git")
        self.assertTrue(seen)
        for argv in seen:
            self.assertEqual(argv[0], git)
            self.assertIn("--no-optional-locks", argv)
            self.assertIn(argv[8], {"rev-parse", "log", "status", "rev-list", "config", "ls-files"})


if __name__ == "__main__":
    unittest.main()
