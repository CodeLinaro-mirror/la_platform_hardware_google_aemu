import unittest
import os
import sys

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.launch import resolve_emulator_executable, prepare_environment
from install.installer import detect_default_install_path, copy_src_to_release_lib


class EmuDevCliTest(unittest.TestCase):

    def test_detect_default_install_path(self):
        install_path = detect_default_install_path()
        self.assertTrue(install_path.endswith("emu-dev-cli") or install_path.endswith("emu-dev-cli.exe"))

    def test_copy_src_to_release_lib(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            copy_src_to_release_lib(SRC_DIR, tmp_dir)
            self.assertTrue(os.path.exists(os.path.join(tmp_dir, "__main__.py")))
            self.assertTrue(os.path.exists(os.path.join(tmp_dir, "commands", "launch.py")))
            # Verify copying when src and dst are the same directory does not raise SameFileError
            copy_src_to_release_lib(tmp_dir, tmp_dir)

    def test_prepare_environment(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            lib64_dir = os.path.join(tmp_dir, "lib64")
            os.makedirs(lib64_dir, exist_ok=True)
            env = prepare_environment(tmp_dir)
            if sys.platform.startswith("linux"):
                self.assertIn("LD_LIBRARY_PATH", env)
                self.assertIn(lib64_dir, env["LD_LIBRARY_PATH"])


    def test_find_bazel_cmd(self):
        from commands.update_cmd import find_bazel_cmd
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            bazel_dir = os.path.join(tmp_dir, "prebuilts", "bazel", "linux-x86_64")
            os.makedirs(bazel_dir, exist_ok=True)
            mock_bazel = os.path.join(bazel_dir, "bazel")
            with open(mock_bazel, "w") as f:
                f.write("#!/bin/sh\necho 1\n")
            os.chmod(mock_bazel, 0o755)
            resolved = find_bazel_cmd(tmp_dir)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(mock_bazel))


if __name__ == "__main__":
    unittest.main()
