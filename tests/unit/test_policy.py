import os
import tempfile
import unittest
from pathlib import Path

from par.core.policy import (
    PolicyViolation, WriteGuard, count_sensitive, is_sensitive_path, protected_roots,
)


class SensitivePathTests(unittest.TestCase):
    def test_sensitive_names(self):
        for p in (
            ".env", "app/.env.local", "x/id_rsa", "x/id_ed25519.pub", "certs/server.pem", "a/b.key",
            "gem/oauth_creds.json", "google_accounts.json", "home/.codex/auth.json", ".NETRC",
            "runtime/accounts/_by_email/", "proj/runtime/accounts/acct_1.json", "p/.deploy/prod.yaml",
            "p/.ssh/config", "x/_pending_codes/abc.json", "p/secrets/a.txt", "jetski-standalone-oauth-token",
            "建模/runtime/accounts/x.json",
        ):
            self.assertTrue(is_sensitive_path(p), p)

    def test_ordinary_names_are_not_sensitive(self):
        for p in (
            "src/main.py", ".env.example", "docs/accounts_overview.md", "runtime/artifacts/x.json",
            "README.md", "tokenizer.py", "keys.md", "accounts/readme.md", "毕业论文/AGENTS.md",
        ):
            self.assertFalse(is_sensitive_path(p), p)

    def test_case_and_unicode_normalisation(self):
        self.assertTrue(is_sensitive_path("A/RUNTIME/Accounts/x"))
        nfd = "café/.ssh/key"   # NFD spelling in an unrelated component
        self.assertTrue(is_sensitive_path(nfd))

    def test_empty_and_root(self):
        self.assertFalse(is_sensitive_path(""))
        self.assertFalse(is_sensitive_path("/"))

    def test_windows_drives_unc_and_backslashes_are_not_a_bypass(self):
        # On POSIX a backslash is a legal file-name character; PurePath alone would treat the whole
        # string as one name and miss the credential-shaped components inside it.
        for p in (r"C:\Users\me\.ssh\id_rsa", r"\\server\share\.env", r"D:\work\.aws\credentials",
                  r"proj\runtime\accounts\acct_1.json", "D:/work/.env.local", r"x\id_ed25519"):
            self.assertTrue(is_sensitive_path(p), p)

    def test_separator_normalisation_does_not_create_false_positives(self):
        for p in ("src/main.py", ".env.example", "docs/accounts_overview.md", "runtime/artifacts/x.json",
                  "app/ÅGENTS.md", "keys.md"):
            self.assertFalse(is_sensitive_path(p), p)

    def test_never_touches_the_filesystem(self):
        # A path that does not exist (and cannot be created) is classified purely by name.
        self.assertTrue(is_sensitive_path("/definitely/not/here/.env"))

    def test_count_sensitive(self):
        self.assertEqual(count_sensitive(["a.py", ".env", "runtime/accounts/x", "b.md"]), 2)


class ProtectedRootsTests(unittest.TestCase):
    def test_contains_tool_configs_shell_files_and_harness(self):
        home = Path(os.path.realpath(tempfile.gettempdir())) / "fakehome"
        roots = {str(r) for r in protected_roots(home)}
        for rel in (".codex", ".claude", ".gemini", ".zshrc", ".local/bin", "Documents/AI_Harness_Core", "ai-harness-backups"):
            self.assertIn(str(home / rel), roots, rel)

    def test_extra_roots_are_added(self):
        with tempfile.TemporaryDirectory() as t:
            roots = protected_roots(t, extra=[Path(t) / "Runge"])
            self.assertIn(Path(os.path.realpath(t)) / "Runge", roots)


class WriteGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.root = self.base / "data"
        self.root.mkdir()
        self.guard = WriteGuard(allowed_roots=(self.root,))

    def tearDown(self):
        self._tmp.cleanup()

    def test_inside_allowed_root(self):
        self.assertEqual(self.guard.check(self.root / "a" / "b.txt"), self.root / "a" / "b.txt")
        self.assertEqual(self.guard.check(self.root), self.root)

    def test_outside_allowed_root(self):
        with self.assertRaises(PolicyViolation):
            self.guard.check(self.base / "other" / "x")

    def test_dotdot_escape(self):
        with self.assertRaises(PolicyViolation):
            self.guard.check(self.root / ".." / "escape.txt")

    def test_sibling_with_same_prefix_is_not_inside(self):
        (self.base / "data2").mkdir()
        with self.assertRaises(PolicyViolation):
            self.guard.check(self.base / "data2" / "x")

    def test_symlink_escape_is_resolved(self):
        outside = self.base / "outside"
        outside.mkdir()
        os.symlink(outside, self.root / "link")
        with self.assertRaises(PolicyViolation):
            self.guard.check(self.root / "link" / "x.txt")

    def test_protected_location_wins_over_allowed_root(self):
        guard = WriteGuard(allowed_roots=(self.base,), protected=(self.base / ".codex",))
        guard.check(self.base / "fine.txt")
        with self.assertRaises(PolicyViolation) as ctx:
            guard.check(self.base / ".codex" / "config.toml")
        self.assertIn("protected", str(ctx.exception))

    def test_protected_check_is_case_insensitive(self):
        guard = WriteGuard(allowed_roots=(self.base,), protected=(self.base / "Harness_Core",))
        with self.assertRaises(PolicyViolation):
            guard.check(self.base / "harness_core" / "scripts" / "x.py")

    def test_allowed_root_inside_protected_location_is_refused(self):
        guard = WriteGuard(allowed_roots=(self.root,), protected=(self.base,))
        with self.assertRaises(PolicyViolation):
            guard.check(self.root / "x")

    def test_sensitive_name_inside_allowed_root_is_refused_without_echoing_it(self):
        with self.assertRaises(PolicyViolation) as ctx:
            self.guard.check(self.root / "runtime" / "accounts" / "acct_secret_name.json")
        self.assertNotIn("acct_secret_name", str(ctx.exception))
        with self.assertRaises(PolicyViolation):
            self.guard.check(self.root / ".env")

    def test_tilde_is_expanded(self):
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(self.base)
        try:
            self.assertEqual(WriteGuard(allowed_roots=(self.base,)).check("~/data/x"), self.root / "x")
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old

    def test_error_message_shortens_home(self):
        try:
            self.guard.check(Path.home() / "Documents" / "elsewhere")
        except PolicyViolation as exc:
            self.assertNotIn(str(Path.home()), str(exc))
            self.assertIn("~", str(exc))
        else:
            self.fail("expected PolicyViolation")


if __name__ == "__main__":
    unittest.main()
