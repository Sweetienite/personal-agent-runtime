"""Opt-in checks against the REAL `agy`. NO model is called and nothing is written outside throw-away folders.

    PAR_REAL_AGY=1 PAR_AGY_EXE=/absolute/path/to/agy python -B -m unittest tests.integration.test_real_agy -v

Skipped unless PAR_REAL_AGY=1 (the default suite never touches the real CLI). The first class runs only subcommands that answer from
local files (`--version`, `--help`) and a second class (PAR_REAL_AGY_ACCOUNT=1) asks the account itself (`-p /usage`, `models`): no
model is called and no quota is used, but it does reach Google. A real model call is a separate, explicitly approved step:
tests/tools/smoke_agy.py.

Every class also guards the user's own ~/.gemini: its names, sizes and modification times must be identical before and after
(never contents: it holds credentials of other tools).
"""
import os
import tempfile
import unittest
from pathlib import Path

from par.providers import antigravity as A
from par.providers.base import ProviderStatus, RunRequest

REAL = os.environ.get("PAR_REAL_AGY") == "1"
ACCOUNT = os.environ.get("PAR_REAL_AGY_ACCOUNT") == "1"
EXE = os.environ.get("PAR_AGY_EXE", "")
GEMINI = Path.home() / ".gemini"


def snapshot(root: Path) -> "dict[str, tuple[int, int]]":
    """{relative path: (size, mtime_ns)} below ``root``: names and numbers only. Links are listed, never followed."""
    found: "dict[str, tuple[int, int]]" = {}
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            found[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns)
    return found


class Guard(unittest.TestCase):
    """A real run must leave the user's ~/.gemini exactly as it was."""

    def setUp(self):
        A.forget_caches()
        self.before = snapshot(GEMINI)
        self.addCleanup(self.assert_gemini_unchanged)
        self.addCleanup(A.forget_caches)

    def assert_gemini_unchanged(self):
        after = snapshot(GEMINI)
        added = sorted(set(after) - set(self.before))
        removed = sorted(set(self.before) - set(after))
        changed = sorted(k for k in set(after) & set(self.before) if after[k] != self.before[k])
        self.assertEqual((added, removed), ([], []), "~/.gemini gained or lost entries")
        # something else (the Antigravity app, which is open) may legitimately touch its own files while we run; report, never hide:
        if changed:
            print(f"\n[recorded] {len(changed)} entr(y/ies) under ~/.gemini changed during this test (first: {changed[:3]}) - "
                  "check that none of them is agy's doing before relying on this guard")


@unittest.skipUnless(REAL and EXE, "set PAR_REAL_AGY=1 and PAR_AGY_EXE=/absolute/path/to/agy to check the real agy (offline)")
class RealAgyOffline(Guard):
    def setUp(self):
        super().setUp()
        self.adapter = A.AntigravityAdapter(EXE, environ={"PATH": "/usr/bin:/bin"})

    def test_the_binary_is_trusted_by_the_adapters_own_rules(self):
        self.assertIsNone(self.adapter.executable_problem())
        self.assertIsNone(self.adapter.signature_problem())

    def test_the_version_is_the_one_this_adapter_was_checked_against(self):
        rc, out, err = self.adapter.run_cli(["--version"])
        self.assertEqual(rc, 0, err)
        if A.TESTED_VERSION not in out:
            self.fail(f"agy says {out.strip()!r}, not the version this adapter was checked against ({A.TESTED_VERSION}): re-check docs/AGY.md §12")

    def test_every_flag_the_adapter_passes_exists_in_this_version(self):
        rc, out, err = self.adapter.run_cli(["--help"])
        help_text = out + err
        self.assertTrue(help_text.strip(), "agy --help printed nothing")
        argv = self.adapter.build_argv(RunRequest("t", 1, "x", Path("/w"), "m"))
        for flag in [a for a in argv if a.startswith("--")]:
            self.assertIn(flag, help_text, flag)
        self.assertIn("stream-json", help_text)

    def test_without_the_keychain_link_there_is_no_sign_in_and_models_says_so_at_once(self):
        """The private HOME has no keychain link here (keychains_from points nowhere), so the CLI cannot find a sign-in. Measured on 1.3.5:
        `models` fails at once (exit 1, "Please sign in ..."), whereas `-p /usage` prints a Google login URL and WAITS (docs/AGY.md §12)."""
        with tempfile.TemporaryDirectory(prefix="par-agy-nokc-") as d:
            ad = A.AntigravityAdapter(EXE, environ={"PATH": "/usr/bin:/bin"}, keychains_from=Path(d) / "nowhere")
            rc, out, err = ad.run_cli(["models"], timeout=30)
            state, said = ad.login_state(ttl=0)
        print(f"\n[recorded] signed-out `models`: exit {rc}; stderr: {err.strip()[:200]!r}; login_state: {state!r}")
        self.assertEqual((rc, state), (1, "none"))
        self.assertIn("sign in", err.lower())

    def test_signed_out_usage_waits_for_a_browser_login_which_is_why_it_is_never_used_to_check_the_sign_in(self):
        with tempfile.TemporaryDirectory(prefix="par-agy-nokc-") as d:
            ad = A.AntigravityAdapter(EXE, environ={"PATH": "/usr/bin:/bin"}, keychains_from=Path(d) / "nowhere")
            rc, out, err = ad.run_cli(["-p", "/usage"], timeout=12)
        print(f"\n[recorded] signed-out `-p /usage` after 12 s: exit {rc}; stderr starts: {err.strip()[:120]!r}")
        self.assertEqual(rc, 124, "agy no longer waits for a browser login when signed out: the sign-in check could use it again")

    def test_the_settings_the_adapter_writes_are_accepted_by_this_version(self):
        """A settings file with an unknown key or a bad value would make the CLI refuse to start or ignore it: run --version through
        the adapter's own private-HOME layout and look for complaints."""
        rc, out, err = self.adapter.run_cli(["--version"])
        self.assertEqual(rc, 0, err)
        for word in ("unknown", "invalid", "unrecognized", "ignoring"):
            self.assertNotIn(word, err.lower(), err)


@unittest.skipUnless(REAL and ACCOUNT and EXE, "set PAR_REAL_AGY=1, PAR_REAL_AGY_ACCOUNT=1 and PAR_AGY_EXE=... (asks the account; no model call)")
class RealAgyAccount(Guard):
    def setUp(self):
        super().setUp()
        self.adapter = A.AntigravityAdapter(EXE)

    def test_the_probe_sees_a_ready_install(self):
        r = self.adapter.probe()
        self.assertEqual(r.status, ProviderStatus.AVAILABLE, r.detail)
        self.assertEqual(r.facts["login"], "signed in")
        self.assertTrue(r.facts["models_in_catalog"], r.facts)
        self.assertTrue(r.facts["usage"], "the quota report could not be read")
        if not r.facts["tested_version"]:
            self.fail(f"agy is {r.version}, not the version this adapter was checked against ({A.TESTED_VERSION})")

    def test_the_usage_report_is_recorded_for_the_notes(self):
        rc, out, err = self.adapter.run_cli(["-p", "/usage"], timeout=90)
        print(f"\n[recorded] `-p /usage` exit {rc}; {len(out.splitlines())} line(s) on stdout; first lines: {out.strip().splitlines()[:6]!r}; stderr: {err.strip()[:200]!r}")
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
