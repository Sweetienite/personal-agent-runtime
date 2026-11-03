import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par.core.policy import PolicyViolation
from par.paths import APP_DIR_NAME, ENV_DATA_DIR, default_data_dir, ensure_data_dir, inspect_data_dir


def mode_of(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


class LocationTests(unittest.TestCase):
    def test_default_location_is_application_support(self):
        p = default_data_dir(home="/Users/someone", env={})
        self.assertEqual(p, Path("/Users/someone/Library/Application Support") / APP_DIR_NAME)

    def test_env_override_must_be_absolute(self):
        self.assertEqual(default_data_dir(env={ENV_DATA_DIR: "/tmp/x"}), Path("/tmp/x"))
        with self.assertRaises(PolicyViolation):
            default_data_dir(env={ENV_DATA_DIR: "relative/dir"})


class EnsureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.data = self.base / APP_DIR_NAME

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_dir_is_reported_not_created_by_inspect(self):
        st = inspect_data_dir(self.data)
        self.assertFalse(st.exists)
        self.assertFalse(st.usable)
        self.assertGreater(st.free_bytes, 0)
        self.assertFalse(self.data.exists(), "inspect must be read-only")

    def test_creates_private_directory_tree(self):
        st = ensure_data_dir(self.data, protected=())
        self.assertTrue(st.usable)
        self.assertEqual(mode_of(self.data), 0o700)
        self.assertEqual(mode_of(self.data / "config"), 0o700)
        self.assertEqual(st.missing_subdirs, ())

    def test_umask_cannot_loosen_the_mode(self):
        old = os.umask(0)
        try:
            ensure_data_dir(self.data, protected=())
        finally:
            os.umask(old)
        self.assertEqual(mode_of(self.data), 0o700)
        self.assertEqual(mode_of(self.data / "config"), 0o700)

    def test_idempotent_and_repairs_missing_subdir(self):
        ensure_data_dir(self.data, protected=())
        (self.data / "config").rmdir()
        self.assertEqual(inspect_data_dir(self.data).missing_subdirs, ("config",))
        st = ensure_data_dir(self.data, protected=())
        self.assertTrue(st.usable)
        self.assertTrue((self.data / "config").is_dir())

    def test_existing_dir_with_wrong_mode_is_refused_and_left_alone(self):
        self.data.mkdir(mode=0o755)
        os.chmod(self.data, 0o755)
        with self.assertRaises(PolicyViolation) as ctx:
            ensure_data_dir(self.data, protected=())
        self.assertIn("mode is 0755", str(ctx.exception))
        self.assertEqual(mode_of(self.data), 0o755, "an existing directory must never be chmod-ed")
        self.assertFalse((self.data / "config").exists())

    def test_symlink_is_refused(self):
        real = self.base / "real"
        real.mkdir(mode=0o700)
        os.symlink(real, self.data)
        with self.assertRaises(PolicyViolation) as ctx:
            ensure_data_dir(self.data, protected=())
        self.assertIn("symlink", str(ctx.exception))

    def test_regular_file_is_refused(self):
        self.data.write_text("x")
        with self.assertRaises(PolicyViolation):
            ensure_data_dir(self.data, protected=())

    def test_missing_parent_is_refused(self):
        with self.assertRaises(PolicyViolation) as ctx:
            ensure_data_dir(self.base / "no" / "such" / APP_DIR_NAME, protected=())
        self.assertIn("parent", str(ctx.exception))
        self.assertFalse((self.base / "no").exists(), "no intermediate directories are created")

    def test_protected_location_is_refused(self):
        with self.assertRaises(PolicyViolation):
            ensure_data_dir(self.base / ".codex" / "par", protected=(self.base / ".codex",))

    def test_sensitive_name_is_refused(self):
        with self.assertRaises(PolicyViolation):
            ensure_data_dir(self.base / "secrets", protected=())

    def test_foreign_owner_is_reported(self):
        ensure_data_dir(self.data, protected=())
        with mock.patch("par.paths.os.getuid", return_value=os.getuid() + 1):
            st = inspect_data_dir(self.data)
        self.assertFalse(st.usable)
        self.assertIn("is not owned by the current user", st.problems)


if __name__ == "__main__":
    unittest.main()
