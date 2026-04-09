import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from sim import matlab_runtime


class PrependUniquePathTests(unittest.TestCase):
    def test_adds_new_path_to_front(self):
        path_list = ["/existing"]
        matlab_runtime._prepend_unique_path(path_list, "/new")
        self.assertEqual(path_list[0], "/new")

    def test_no_duplicate_when_already_present(self):
        path_list = ["/already"]
        matlab_runtime._prepend_unique_path(path_list, "/already")
        self.assertEqual(len(path_list), 1)

    def test_normalizes_before_compare(self):
        path_list = [os.path.normpath("/some/path")]
        matlab_runtime._prepend_unique_path(path_list, "/some/path/")
        self.assertEqual(len(path_list), 1)


class PrependUniqueEnvPathTests(unittest.TestCase):
    def test_sets_when_empty(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_VAR_XYZ", None)
            matlab_runtime._prepend_unique_env_path("TEST_VAR_XYZ", "/foo")
            self.assertEqual(os.environ["TEST_VAR_XYZ"], "/foo")

    def test_prepends_to_existing(self):
        with patch.dict(os.environ, {"TEST_VAR_XYZ": "/bar"}, clear=False):
            matlab_runtime._prepend_unique_env_path("TEST_VAR_XYZ", "/foo")
            parts = os.environ["TEST_VAR_XYZ"].split(os.pathsep)
            self.assertEqual(parts[0], "/foo")
            self.assertIn("/bar", parts)

    def test_no_duplicate_when_already_present(self):
        with patch.dict(
            os.environ, {"TEST_VAR_XYZ": "/foo" + os.pathsep + "/bar"}, clear=False
        ):
            matlab_runtime._prepend_unique_env_path("TEST_VAR_XYZ", "/foo")
            self.assertEqual(
                os.environ["TEST_VAR_XYZ"], "/foo" + os.pathsep + "/bar"
            )


class RuntimeLayoutTests(unittest.TestCase):
    def test_win32_returns_win64(self):
        with patch.object(sys, "platform", "win32"):
            arch, var = matlab_runtime._runtime_layout()
            self.assertEqual(arch, "win64")
            self.assertEqual(var, "PATH")

    def test_linux_returns_glnxa64(self):
        with patch.object(sys, "platform", "linux"):
            arch, var = matlab_runtime._runtime_layout()
            self.assertEqual(arch, "glnxa64")
            self.assertEqual(var, "LD_LIBRARY_PATH")

    def test_unsupported_platform_raises(self):
        with patch.object(sys, "platform", "plan9"):
            with self.assertRaises(ImportError):
                matlab_runtime._runtime_layout()


class PrepareMatlabRootTests(unittest.TestCase):
    def test_empty_root_is_noop(self):
        # Should not raise
        matlab_runtime.prepare_matlab_root("")
        matlab_runtime.prepare_matlab_root("   ")

    def test_missing_directory_raises(self):
        with self.assertRaises(ImportError) as ctx:
            matlab_runtime.prepare_matlab_root("/definitely/nonexistent/matlab/root")
        self.assertIn("MATLAB_ROOT", str(ctx.exception))


class PurgeStaleMatlabModulesTests(unittest.TestCase):
    def test_removes_modules_without_file(self):
        fake = types.ModuleType("matlab.fake_stale")
        # no __file__ attribute on module deliberately
        with patch.dict(sys.modules, {"matlab.fake_stale": fake}, clear=False):
            matlab_runtime.purge_stale_matlab_modules("/tmp/matlab_root")
            self.assertNotIn("matlab.fake_stale", sys.modules)

    def test_empty_root_is_noop(self):
        matlab_runtime.purge_stale_matlab_modules("")

    def test_keeps_modules_under_expected_dist_dir(self):
        expected_dist = os.path.abspath(
            os.path.join("/tmp/matlab_root", "extern", "engines", "python", "dist")
        )
        fake_path = os.path.join(expected_dist, "matlab", "__init__.py")
        fake = types.ModuleType("matlab")
        fake.__file__ = fake_path
        with patch.dict(sys.modules, {"matlab": fake}, clear=False):
            matlab_runtime.purge_stale_matlab_modules("/tmp/matlab_root")
            # Should still be present (path matches expected prefix)
            self.assertIn("matlab", sys.modules)
            self.assertIs(sys.modules["matlab"], fake)
            # Cleanup
            sys.modules.pop("matlab", None)


if __name__ == "__main__":
    unittest.main()
