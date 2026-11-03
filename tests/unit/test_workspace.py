import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par.execution import gitops, workspace as ws
from par.execution.gitops import Git, GitError
from tests.helpers import tree_digest
from tests.world import git_env


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        self.home.mkdir()
        self.scratch = self.base / "scratch"
        self.scratch.mkdir(mode=0o700)
        self.git = Git(self.scratch)
        self.addCleanup(self._tmp.cleanup)

    def sh(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_env(self.home)).stdout.decode().strip()

    def write(self, root, files):
        for rel, content in files.items():
            p = Path(root) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content if isinstance(content, bytes) else content.encode())

    def make_repo(self, name="proj", files=None):
        path = self.base / name
        path.mkdir()
        self.sh(path, "init", "-q", "-b", "main")
        self.write(path, files or {"app/main.py": "print('v1')\n", "README.md": "# demo\n"})
        self.sh(path, "add", "-A")
        self.sh(path, "commit", "-q", "-m", "c1")
        return path, self.sh(path, "rev-parse", "HEAD")

    def commit_all(self, repo, message="next"):
        self.sh(repo, "add", "-A")
        self.sh(repo, "commit", "-q", "-m", message)
        return self.sh(repo, "rev-parse", "HEAD")


class GitWrapper(RepoCase):
    def test_a_git_executable_is_found_in_a_trusted_place(self):
        self.assertIn(gitops.find_git(), gitops.GIT_CANDIDATES)

    def test_a_missing_git_is_reported_clearly(self):
        with mock.patch.object(gitops, "GIT_CANDIDATES", ("/nonexistent/git",)):
            with self.assertRaises(GitError):
                Git(self.scratch)

    def test_the_environment_is_clean_and_private(self):
        with mock.patch.dict(os.environ, {"SECRET_FOO": "hunter2", "GIT_DIR": "/elsewhere", "GIT_SSH_COMMAND": "evil"}):
            env = self.git.env()
        self.assertEqual(env["HOME"], str(self.scratch))
        self.assertEqual((env["GIT_CONFIG_GLOBAL"], env["GIT_CONFIG_SYSTEM"], env["GIT_LITERAL_PATHSPECS"]), ("/dev/null", "/dev/null", "1"))
        for leaked in ("SECRET_FOO", "GIT_DIR", "GIT_SSH_COMMAND"):
            self.assertNotIn(leaked, env)

    def test_user_git_configuration_is_never_read(self):
        (self.scratch / ".gitconfig").write_text("[alias]\n\tzzz = status\n")
        repo, _ = self.make_repo()
        with self.assertRaises(GitError):
            self.git.run(repo, "zzz")

    def test_hooks_never_run(self):
        repo, commit = self.make_repo()
        clone = self.base / "clone"
        gitops.clone_at(self.git, repo, clone, commit)
        hook = clone / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch '{self.base}/hook_ran'\n")
        hook.chmod(0o755)
        (clone / "new.txt").write_text("x\n")
        self.git.run(clone, "add", "new.txt")
        self.git.run(clone, "commit", "-q", "-m", "x")
        self.assertFalse((self.base / "hook_ran").exists())

    def test_t16_adding_committing_deleting_and_pushing_in_the_workers_copy_cannot_reach_the_real_repository(self):
        """The plan's T16 (unapproved `git add -A`, delete, push): the Worker's private copy has no remote, so a push has nowhere to go,
        and everything it adds, commits or deletes stays in the copy."""
        repo, commit = self.make_repo()
        clone = self.base / "clone"
        gitops.clone_at(self.git, repo, clone, commit)

        def real_state():
            return (self.git.run(repo, "rev-parse", "HEAD"), self.git.run(repo, "for-each-ref"), self.git.run(repo, "status", "--porcelain"))
        before = real_state()
        self.assertEqual(self.git.run(clone, "remote").strip(), b"")                         # no remote at all
        (clone / "new.txt").write_text("x\n")
        self.git.run(clone, "add", "-A")
        self.git.run(clone, "commit", "-q", "-m", "the Worker's own commit")
        for victim in list(clone.iterdir()):                                                  # delete everything the Worker can see
            if victim.name != ".git":
                victim.unlink() if victim.is_file() else shutil.rmtree(victim)
        with self.assertRaises(GitError):
            self.git.run(clone, "push", "origin", "HEAD:refs/heads/elsewhere")
        self.assertEqual(real_state(), before)                                                # the real repository saw none of it

    def test_hooks_do_not_run_even_in_a_repository_the_kernel_did_not_clone(self):
        repo, _ = self.make_repo()
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch '{self.base}/hook_ran_plain'\n")
        hook.chmod(0o755)
        (repo / "new.txt").write_text("x\n")
        self.git.run(repo, "add", "new.txt")
        self.git.run(repo, "commit", "-q", "-m", "x")
        self.assertFalse((self.base / "hook_ran_plain").exists())

    def test_a_hung_git_is_stopped(self):
        slow = self.base / "slowgit"
        slow.write_text("#!/bin/sh\nsleep 5\n")
        slow.chmod(0o755)
        with self.assertRaises(GitError) as ctx:
            Git(self.scratch, exe=str(slow), timeout=0.3).run(self.base, "status")
        self.assertIn("timed out", str(ctx.exception))

    def test_failures_carry_a_redacted_reason(self):
        with self.assertRaises(GitError) as ctx:
            self.git.run(self.base, "rev-parse", "--verify", "no-such-ref")
        self.assertIsNotNone(ctx.exception.returncode)


class Cloning(RepoCase):
    def test_a_clone_is_a_private_copy_at_exactly_the_commit(self):
        repo, first = self.make_repo()
        self.write(repo, {"app/main.py": "print('v2')\n", "extra.txt": "later\n"})
        second = self.commit_all(repo)
        before = tree_digest(repo)
        dest = self.base / "copy"
        excluded = gitops.clone_at(self.git, repo, dest, first)
        self.assertEqual(excluded, [])
        self.assertEqual((dest / "app" / "main.py").read_text(), "print('v1')\n")
        self.assertFalse((dest / "extra.txt").exists())
        self.assertEqual(self.sh(dest, "rev-parse", "HEAD"), first)
        self.assertEqual(self.sh(dest, "remote"), "")                                   # no way to push anywhere
        self.assertEqual(self.sh(dest, "config", "core.hooksPath"), "/dev/null")
        (dest / "app" / "main.py").write_text("changed in the copy\n")
        gitops.clone_at(self.git, repo, self.base / "copy2", second)
        self.assertEqual(tree_digest(repo), before)                                      # the project itself is untouched

    def test_only_full_existing_commit_ids_are_accepted(self):
        repo, commit = self.make_repo()
        for bad in ("HEAD", commit[:12], "--upload-pack=evil", "z" * 40, "0" * 40, "", commit.upper()):
            with self.assertRaises(GitError, msg=bad):
                gitops.clone_at(self.git, repo, self.base / "x", bad)
            self.assertFalse((self.base / "x").exists())

    def test_resolve_commit(self):
        repo, commit = self.make_repo()
        self.assertEqual(gitops.resolve_commit(self.git, repo), commit)
        with self.assertRaises(GitError):
            gitops.resolve_commit(self.git, repo, "no-such-branch")

    def test_committed_secrets_are_not_materialised(self):
        repo, commit = self.make_repo(files={"app/main.py": "x\n", ".env": "KEY=1\n", "keys/id_rsa": "private\n", "cfg/prod.pem": "pem\n",
                                           "runtime/accounts/main.json": '{"account": "x"}\n'})
        dest = self.base / "copy"
        excluded = gitops.clone_at(self.git, repo, dest, commit)
        self.assertEqual(sorted(excluded), [".env", "cfg/prod.pem", "keys/id_rsa", "runtime/accounts/main.json"])      # account JSON too (T15)
        for rel in excluded:
            self.assertFalse((dest / rel).exists(), rel)
        self.assertTrue((dest / "app" / "main.py").exists())
        self.assertEqual(self.sh(dest, "status", "--porcelain"), "")                    # and git does not see them as deleted
        self.assertEqual(gitops.diff_patch(self.git, dest, commit), b"")

    def test_ignore_rules_are_consulted_for_untracked_paths(self):
        repo, commit = self.make_repo(files={".gitignore": "build/\n*.log\n", "src/a.py": "a\n"})
        dest = self.base / "copy"
        gitops.clone_at(self.git, repo, dest, commit)
        found = gitops.ignored_among(self.git, dest, ["build/out.o", "deep/x.log", "src/new.py", "build"])
        self.assertEqual(found, {"build/out.o", "deep/x.log"})
        self.assertEqual(gitops.ignored_among(self.git, dest, []), set())

    def test_literal_pathspecs(self):
        repo, commit = self.make_repo()
        dest = self.base / "copy"
        gitops.clone_at(self.git, repo, dest, commit)
        (dest / "[ab].txt").write_text("brackets\n")
        (dest / "a.txt").write_text("a\n")
        gitops.add_intent(self.git, dest, ["[ab].txt"])
        patch = gitops.diff_patch(self.git, dest, commit)
        self.assertIn(b"[ab].txt", patch)
        self.assertNotIn(b"a.txt", patch.replace(b"[ab].txt", b""))                      # the brackets were not read as a glob

    def test_patch_stat_and_apply_round_trip(self):
        repo, commit = self.make_repo(files={"app/main.py": "one\ntwo\n", "gone.txt": "bye\n", "README.md": "# r\n"})
        work = self.base / "work"
        gitops.clone_at(self.git, repo, work, commit)
        self.write(work, {"app/main.py": "one\nTWO\nthree\n", "new/file.txt": "fresh\n", "blob.bin": bytes(range(256))})
        (work / "gone.txt").unlink()
        gitops.add_intent(self.git, work, ["new/file.txt", "blob.bin"])
        patch = gitops.diff_patch(self.git, work, commit)
        stat_ = gitops.diff_numstat(self.git, work, commit)
        self.assertEqual(stat_["files"], 4)
        self.assertEqual((stat_["additions"], stat_["deletions"]), (2 + 1, 1 + 1))        # main.py +2/-1, new file +1, gone -1; binary counts 0
        fresh = self.base / "fresh"
        gitops.clone_at(self.git, repo, fresh, commit)
        gitops.apply_patch(self.git, fresh, patch, check_only=True)
        gitops.apply_patch(self.git, fresh, patch)
        self.assertEqual(ws.tree_equal(work, fresh), [])

    def test_a_patch_that_does_not_apply_is_detected(self):
        repo, commit = self.make_repo()
        work = self.base / "work"
        gitops.clone_at(self.git, repo, work, commit)
        (work / "app" / "main.py").write_text("print('mine')\n")
        patch = gitops.diff_patch(self.git, work, commit)
        other = self.base / "other"
        gitops.clone_at(self.git, repo, other, commit)
        (other / "app" / "main.py").write_text("print('someone else')\n")
        with self.assertRaises(GitError):
            gitops.apply_patch(self.git, other, patch, check_only=True)


class Scanning(RepoCase):
    def test_scan_records_files_modes_symlinks_and_skips_the_top_level_git(self):
        root = self.base / "tree"
        self.write(root, {"a.txt": "a", "sub/b.sh": "#!/bin/sh\n", ".git/config": "x", "empty_dir_file": ""})
        (root / "sub" / "b.sh").chmod(0o755)
        (root / "emptydir").mkdir()
        os.symlink("a.txt", root / "link")
        got = ws.scan_tree(root)
        self.assertEqual(sorted(got), ["a.txt", "empty_dir_file", "link", "sub/b.sh"])
        self.assertEqual(got["sub/b.sh"], ws.Entry("file", 10, True))
        self.assertEqual(got["a.txt"].exec, False)
        self.assertEqual(got["link"], ws.Entry("symlink", link="a.txt"))

    def test_symlinked_directories_are_not_followed(self):
        outside = self.base / "outside"
        self.write(outside, {"secret.txt": "s", "deep/more.txt": "m"})
        root = self.base / "tree"
        root.mkdir()
        os.symlink(outside, root / "portal")
        got = ws.scan_tree(root)
        self.assertEqual(list(got), ["portal"])
        self.assertEqual(got["portal"].kind, "symlink")

    def test_nested_git_folders_and_special_files_are_recorded_not_entered(self):
        root = self.base / "tree"
        self.write(root, {"vendor/lib/.git/HEAD": "ref", "vendor/lib/x.py": "x"})
        os.mkfifo(root / "pipe")
        got = ws.scan_tree(root)
        self.assertEqual(got["vendor/lib/.git"].kind, "dir")
        self.assertNotIn("vendor/lib/.git/HEAD", got)
        self.assertEqual(got["pipe"].kind, "other")

    def test_a_tree_that_is_too_large_is_refused(self):
        root = self.base / "tree"
        self.write(root, {f"f{i}": "x" for i in range(30)})
        with self.assertRaises(ws.TreeTooLarge):
            ws.scan_tree(root, max_files=10)


class Comparing(RepoCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.base / "a", self.base / "b"
        for root in (self.a, self.b):
            self.write(root, {"same.txt": "same", "edit.txt": "aaaa", "mode.sh": "run", "gone.txt": "x", "kind": "file"})
        os.symlink("t1", self.a / "link")
        os.symlink("t1", self.b / "link")

    def diff(self):
        return {c.path: c.status for c in ws.diff_trees(self.a, ws.scan_tree(self.a), self.b, ws.scan_tree(self.b))}

    def test_identical_trees_have_no_changes(self):
        self.assertEqual(self.diff(), {})

    def test_every_kind_of_change(self):
        (self.b / "edit.txt").write_text("bbbb")                  # same size, different bytes
        (self.b / "mode.sh").chmod(0o755)
        (self.b / "gone.txt").unlink()
        (self.b / "added.txt").write_text("new")
        (self.b / "kind").unlink()
        os.symlink("elsewhere", self.b / "kind")
        (self.b / "link").unlink()
        os.symlink("t2", self.b / "link")
        self.assertEqual(self.diff(), {"added.txt": "A", "edit.txt": "M", "gone.txt": "D", "kind": "T", "link": "M", "mode.sh": "M"})

    def test_size_differences_are_modifications(self):
        (self.b / "edit.txt").write_text("much longer now")
        self.assertEqual(self.diff(), {"edit.txt": "M"})

    def test_equal_size_equal_bytes_is_not_a_change_even_with_a_newer_timestamp(self):
        (self.b / "same.txt").write_text("same")
        os.utime(self.b / "same.txt", (2_000_000_000, 2_000_000_000))
        self.assertEqual(self.diff(), {})


def change(path, status="A", kind="file", size=1, exec_=False):
    new = None if status == "D" else ws.Entry(kind, size, exec_, link="x" if kind == "symlink" else None)
    old = None if status == "A" else ws.Entry("file", 1)
    return ws.Change(path, status, old, new)


class Judging(unittest.TestCase):
    def judge(self, changes, allowed=("app/",), writes=True, ignored=(), **kw):
        return ws.classify(changes, allowed_paths=allowed, writes=writes, ignored=ignored, **kw)

    def reasons(self, result):
        return {v.path: v.reason for v in result.violations}

    def test_changes_inside_allowed_paths_pass(self):
        r = self.judge([change("app/a.py"), change("app/sub/b.py", "M"), change("app/old.py", "D")])
        self.assertEqual([c.path for c in r.allowed], ["app/a.py", "app/sub/b.py", "app/old.py"])
        self.assertEqual((r.violations, r.noise), ([], []))

    def test_changes_outside_are_violations_and_say_so(self):
        r = self.judge([change("app/a.py"), change("lib/b.py"), change("application/c.py"), change("app/old.py", "D"), change("docs/x.md", "D")])
        self.assertEqual(self.reasons(r), {"lib/b.py": "outside the allowed paths", "application/c.py": "outside the allowed paths", "docs/x.md": "outside the allowed paths"})

    def test_a_read_only_task_may_change_nothing(self):
        r = self.judge([change("app/a.py"), change("README.md", "M"), change("x.txt", "D")], allowed=(), writes=False)
        self.assertEqual(len(r.violations), 3)
        self.assertTrue(all(v.reason == "a read-only task changed a file" for v in r.violations))

    def test_protected_paths_are_violations_even_inside_an_allowed_folder(self):
        paths = ["app/.env", "app/AGENTS.md", "app/id_rsa", "app/vendor/.git/config", ".ai/STATE.md", "CLAUDE.md"]
        r = self.judge([change(p) for p in paths] + [change("app/ok.py")], allowed=("app/", ".ai/", "CLAUDE.md"))
        self.assertEqual([c.path for c in r.allowed], ["app/ok.py"])
        self.assertEqual(r.protected_count, len(paths))
        self.assertTrue(all(v.protected for v in r.violations))

    def test_protected_beats_noise(self):
        r = self.judge([change("app/.env"), change("app/__pycache__/x.pyc"), change("app/.DS_Store")], ignored={"app/.env"})
        self.assertEqual([v.path for v in r.violations], ["app/.env"])
        self.assertEqual(sorted(r.noise), ["app/.DS_Store", "app/__pycache__/x.pyc"])

    def test_noise_is_only_new_files_and_follows_ignore_rules(self):
        r = self.judge([change("build/out.o"), change("lib/stray.txt"), change("app/__pycache__/m.pyc", "A")], ignored={"build/out.o"}, writes=False, allowed=())
        self.assertEqual(sorted(r.noise), ["app/__pycache__/m.pyc", "build/out.o"])
        self.assertEqual([v.path for v in r.violations], ["lib/stray.txt"])
        modified = self.judge([change("app/__pycache__/m.pyc", "M"), change("build/out.o", "D")], ignored={"build/out.o"}, allowed=())
        self.assertEqual(len(modified.violations), 2)                      # a tracked file is never "noise"

    def test_symlinks_special_files_and_nested_repos_are_never_accepted(self):
        r = self.judge([change("app/link", kind="symlink"), change("app/pipe", kind="other"), change("app/inner/.git", kind="dir"),
                        change("app/fine.py")])
        self.assertEqual([c.path for c in r.allowed], ["app/fine.py"])
        self.assertIn("symlink", self.reasons(r)["app/link"])
        self.assertIn("special file", self.reasons(r)["app/pipe"])
        self.assertTrue(r.violations[2].protected)

    def test_oversized_files_are_violations(self):
        r = self.judge([change("app/big.bin", size=100)], max_file_bytes=50)
        self.assertIn("larger than", self.reasons(r)["app/big.bin"])

    def test_unsafe_names_are_violations_with_a_printable_path(self):
        r = self.judge([change("app/new\nline.py"), change("app/ctrl\x07.py"), change("app/bell\udc80.py")])
        self.assertEqual(len(r.violations), 3)
        self.assertTrue(all(v.reason == "unsafe file name" for v in r.violations))
        self.assertTrue(all(v.path.isprintable() or "\\" in v.path for v in r.violations))

    def test_too_many_changes(self):
        with self.assertRaises(ws.TooManyChanges):
            self.judge([change(f"app/f{i}.py") for i in range(6)], max_changed=5)


class EndToEnd(RepoCase):
    """prepare -> a Worker edits its copy -> compare -> verification tree -> patch -> the patch reproduces the tree."""

    def setUp(self):
        super().setUp()
        self.repo, self.commit = self.make_repo(files={
            "app/main.py": "print('v1')\n", "app/old.py": "old\n", "app/tool.sh": "#!/bin/sh\n", "README.md": "# demo\n",
            ".gitignore": "build/\n", ".env": "KEY=1\n", "tracked_link": "target",
        })
        self.attempt = self.base / "attempt"

    def prepare(self, risk="R2"):
        return ws.prepare(self.git, source=self.repo, base_commit=self.commit, attempt_dir=self.attempt, risk=risk)

    def test_r0_gets_an_empty_folder_and_no_project_files(self):
        p = ws.prepare(self.git, source=None, base_commit=None, attempt_dir=self.attempt, risk="R0")
        self.assertEqual(list(p.workspace.iterdir()), [])
        self.assertIsNone(p.ref)
        self.assertEqual(stat.S_IMODE(os.stat(p.workspace).st_mode), 0o700)

    def test_r1_and_r2_get_a_private_clone_plus_a_pristine_reference(self):
        p = self.prepare("R1")
        self.assertEqual(ws.tree_equal(p.ref, p.workspace), [])
        self.assertEqual(p.excluded, (".env",))
        self.assertFalse((p.workspace / ".env").exists())
        self.assertEqual(stat.S_IMODE(os.stat(p.workspace).st_mode), 0o700)
        self.assertNotEqual(p.workspace, p.ref)

    def test_a_copy_without_git_has_every_file_but_no_repository(self):
        p = ws.prepare(self.git, source=self.repo, base_commit=self.commit, attempt_dir=self.attempt, risk="R2", with_git=False)
        self.assertFalse((p.workspace / ".git").exists())
        self.assertTrue((p.ref / ".git").is_dir())                                         # the Kernel's own reference keeps its repository
        self.assertEqual(ws.tree_equal(p.ref, p.workspace), [])                            # ... and the two still compare equal
        self.assertEqual(sorted(ws.scan_tree(p.ref)), sorted(ws.scan_tree(p.workspace)))
        self.assertTrue((p.workspace / "app" / "main.py").is_file())
        self.assertTrue(os.access(p.workspace / "app" / "tool.sh", os.X_OK) == os.access(p.ref / "app" / "tool.sh", os.X_OK))
        self.assertEqual(stat.S_IMODE(os.stat(p.workspace).st_mode), 0o700)

    def test_only_the_top_level_git_is_left_out(self):
        self.write(self.repo, {"vendor/.gitignore": "x\n"})
        self.git.run(self.repo, "add", "-A")
        self.git.run(self.repo, "commit", "-q", "-m", "more")
        commit = gitops.resolve_commit(self.git, self.repo)
        p = ws.prepare(self.git, source=self.repo, base_commit=commit, attempt_dir=self.attempt, risk="R1", with_git=False)
        self.assertTrue((p.workspace / "vendor" / ".gitignore").is_file())                # a file merely NAMED like git metadata stays
        self.assertTrue((p.workspace / ".gitignore").is_file())

    def test_work_in_a_copy_without_git_is_judged_exactly_like_work_in_a_normal_copy(self):
        for with_git in (True, False):
            with self.subTest(with_git=with_git):
                attempt = self.base / f"attempt_{with_git}"
                p = ws.prepare(self.git, source=self.repo, base_commit=self.commit, attempt_dir=attempt, risk="R2", with_git=with_git)
                (p.workspace / "app" / "main.py").write_text("print('v2')\n")
                self.write(p.workspace, {"app/new.py": "x = 1\n", "README.md": "outside\n"})
                changes = ws.diff_trees(p.ref, ws.scan_tree(p.ref), p.workspace, ws.scan_tree(p.workspace))
                verdict = ws.classify(changes, allowed_paths=("app/",), writes=True, ignored=set())
                self.assertEqual(sorted(c.path for c in verdict.allowed), ["app/main.py", "app/new.py"])
                self.assertEqual([v.path for v in verdict.violations], ["README.md"])

    def test_a_hard_kill_in_the_middle_of_a_copy_never_leaves_a_folder_open_to_others(self):
        """The mode must be private from the first instant, not fixed afterwards (a crash test once caught a 0755 workspace)."""
        for what in ("workspace", "verify"):
            with self.subTest(folder=what):
                attempt = self.base / f"attempt_{what}"
                if what == "workspace":
                    with mock.patch("shutil.copyfile", side_effect=KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            ws.prepare(self.git, source=self.repo, base_commit=self.commit, attempt_dir=attempt, risk="R2")
                else:
                    p = ws.prepare(self.git, source=self.repo, base_commit=self.commit, attempt_dir=attempt, risk="R1")
                    with mock.patch("shutil.copyfile", side_effect=KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            ws.build_verify_tree(p.ref, attempt / "verify", p.workspace, [])
                self.assertEqual(stat.S_IMODE(os.stat(attempt / what).st_mode) & 0o077, 0)

    def test_the_pristine_copy_and_its_parents_are_private_too(self):
        p = self.prepare("R2")
        for folder in (self.attempt, p.ref, p.workspace):
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode) & 0o077, 0, str(folder))
        ws.build_verify_tree(p.ref, self.attempt / "verify", p.workspace, [])
        self.assertEqual(stat.S_IMODE(os.stat(self.attempt / "verify").st_mode) & 0o077, 0)

    def test_a_project_without_a_git_base_is_refused(self):
        with self.assertRaises(GitError):
            ws.prepare(self.git, source=None, base_commit=None, attempt_dir=self.attempt, risk="R1")

    def test_the_whole_chain_with_a_faithful_patch(self):
        p = self.prepare()
        # the "Worker" edits its copy: modify, add (incl. an executable and a binary), delete, retype, plus noise and a sneaky file
        (p.workspace / "app" / "main.py").write_text("print('v2')\n")
        (p.workspace / "app" / "old.py").unlink()
        self.write(p.workspace, {"app/new/feature.py": "def f(): pass\n", "app/data.bin": bytes(range(256)) * 4, "build/out.o": "obj",
                                 "app/__pycache__/main.cpython-312.pyc": "pyc", "README.md": "# changed outside the allowed paths\n"})
        (p.workspace / "app" / "run.sh").write_text("#!/bin/sh\necho hi\n")
        (p.workspace / "app" / "run.sh").chmod(0o755)
        (p.workspace / "app" / "tool.sh").chmod(0o755)
        (p.workspace / "tracked_link").unlink()
        os.symlink("elsewhere", p.workspace / "tracked_link")                              # tracked file replaced by a symlink: refused
        marker = self.base / "poisoned_config_ran"
        (p.workspace / ".git" / "config").write_text(f"[core]\n\tfsmonitor = touch {marker}\n")   # the Worker poisons its own git config
        ref_scan, work_scan = ws.scan_tree(p.ref), ws.scan_tree(p.workspace)
        changes = ws.diff_trees(p.ref, ref_scan, p.workspace, work_scan)
        added = [c.path for c in changes if c.status == "A"]
        ignored = gitops.ignored_among(self.git, p.ref, added)
        verdict = ws.classify(changes, allowed_paths=("app/",), writes=True, ignored=ignored)
        self.assertEqual(sorted(c.path for c in verdict.allowed),
                         ["app/data.bin", "app/main.py", "app/new/feature.py", "app/old.py", "app/run.sh", "app/tool.sh"])
        self.assertEqual({v.path for v in verdict.violations}, {"README.md", "tracked_link"})
        self.assertEqual(sorted(verdict.noise), ["app/__pycache__/main.cpython-312.pyc", "build/out.o"])
        verify = self.attempt / "verify"
        ws.build_verify_tree(p.ref, verify, p.workspace, verdict.allowed)
        self.assertEqual(sorted(ws.tree_equal(p.ref, verify)), sorted(c.path for c in verdict.allowed))
        self.assertTrue(os.access(verify / "app" / "run.sh", os.X_OK))
        self.assertFalse((verify / "app" / "old.py").exists())
        self.assertEqual((verify / "README.md").read_text(), "# demo\n")                    # the violation did not get in
        gitops.add_intent(self.git, verify, [c.path for c in verdict.allowed if c.status == "A"])
        patch = gitops.diff_patch(self.git, verify, self.commit)
        self.assertTrue(patch.startswith(b"diff --git"))
        self.assertNotIn(b"README", patch)
        self.assertNotIn(b"fsmonitor", patch)
        # the proof that matters: applying the patch to a pristine copy gives exactly the verified tree
        gitops.apply_patch(self.git, p.ref, patch)
        self.assertEqual(ws.tree_equal(p.ref, verify), [])
        # and nothing the Kernel ran executed the Worker's poisoned .git/config
        self.assertFalse(marker.exists())

    def test_a_path_that_would_leave_the_verification_tree_is_stopped(self):
        p = self.prepare()
        outside = self.base / "outside"
        outside.mkdir()
        os.symlink(outside, p.ref / "app" / "portal")
        bad = ws.Change("app/portal/escape.txt", "A", None, ws.Entry("file", 1))
        (p.workspace / "app" / "portal").mkdir(parents=True, exist_ok=True)
        (p.workspace / "app" / "portal" / "escape.txt").write_text("x")
        with self.assertRaises(ws.EscapeAttempt):
            ws.build_verify_tree(p.ref, self.attempt / "verify", p.workspace, [bad])
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_workspace_file_swapped_for_a_symlink_is_not_followed(self):
        p = self.prepare()
        secret = self.base / "secret.txt"
        secret.write_text("TOP SECRET\n")
        (p.workspace / "app" / "main.py").unlink()
        os.symlink(secret, p.workspace / "app" / "main.py")
        fake = ws.Change("app/main.py", "M", ws.Entry("file", 1), ws.Entry("file", 1))     # as if the scan had seen a plain file
        with self.assertRaises(OSError):
            ws.build_verify_tree(p.ref, self.attempt / "verify", p.workspace, [fake])


if __name__ == "__main__":
    unittest.main()
