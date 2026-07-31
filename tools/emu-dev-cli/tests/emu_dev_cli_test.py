import unittest
import os
import sys

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.launch import resolve_emulator_executable, prepare_environment
from install.installer import detect_default_install_path, copy_src_to_release_lib


class EmuDevCliTest(unittest.TestCase):

    def test_detect_default_install_path(self):
        install_path = detect_default_install_path()
        self.assertTrue(
            install_path.endswith("emu-dev-cli")
            or install_path.endswith("emu-dev-cli.exe")
        )

    def test_copy_src_to_release_lib(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            copy_src_to_release_lib(SRC_DIR, tmp_dir)
            self.assertTrue(os.path.exists(os.path.join(tmp_dir, "__main__.py")))
            self.assertTrue(
                os.path.exists(os.path.join(tmp_dir, "commands", "launch.py"))
            )
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
        import platform
        system = platform.system().lower()
        machine = platform.machine().lower()
        p_dir = "linux-x86_64"
        if system == "darwin":
            p_dir = (
                "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x86_64"
            )
        elif system == "windows":
            p_dir = "windows-x86_64"

        with tempfile.TemporaryDirectory() as tmp_dir:
            bazel_dir = os.path.join(tmp_dir, "prebuilts", "bazel", p_dir)
            os.makedirs(bazel_dir, exist_ok=True)
            mock_bazel = os.path.join(bazel_dir, "bazel")
            with open(mock_bazel, "w") as f:
                f.write("#!/bin/sh\necho 1\n")
            os.chmod(mock_bazel, 0o755)
            resolved = find_bazel_cmd(tmp_dir)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(mock_bazel))

    def test_parse_crash_id(self):
        from commands.crash import parse_crash_id

        self.assertEqual(parse_crash_id("05d8356e2f800000"), "05d8356e2f800000")
        self.assertEqual(
            parse_crash_id("https://crash.corp.google.com/05d8356e2f800000"),
            "05d8356e2f800000",
        )
        self.assertEqual(
            parse_crash_id("go/crash/05d8356e2f800000"), "05d8356e2f800000"
        )

    def test_extract_top_fault_frame(self):
        from commands.crash import extract_top_fault_frame

        sample_dump = """
Crashing Thread:
#0 0x00007f123456 in abort () from /lib64/libc.so.6
#1 0x00007f123457 in android::FrameBuffer::post() at FrameBuffer.cpp:142
#2 0x00007f123458 in RenderThread::main() at RenderThread.cpp:88
"""
        func, file_info = extract_top_fault_frame(sample_dump)
        self.assertEqual(func, "android::FrameBuffer::post")
        self.assertEqual(file_info, "FrameBuffer.cpp:142")

    def test_resolve_crashadvisor_path(self):
        from lib.workspace import WorkspacePathResolver

        advisor_path = WorkspacePathResolver("emu-main-next").find_directory(
            "hardware/generic/goldfish/emulator/crashreport/tool/advisor"
        )
        self.assertIsNotNone(advisor_path)
        self.assertTrue(os.path.exists(advisor_path))
        self.assertTrue(os.path.exists(os.path.join(advisor_path, "advisor.py")))

    def test_crash_parser_registration(self):
        import argparse
        from commands import crash

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        crash.register_parser(subparsers)

        # Verify find-bug parsing
        args = parser.parse_args(["crash", "find-bug", "123456"])
        self.assertEqual(args.subcommand, "crash")
        self.assertEqual(args.crash_cmd, "find-bug")
        self.assertEqual(args.crash_id, "123456")

        # Verify autofix parsing with dry-run
        args = parser.parse_args(["crash", "autofix", "654321", "--dry-run"])
        self.assertEqual(args.crash_cmd, "autofix")
        self.assertTrue(args.dry_run)

        # Verify crash analyze subcommand parsing
        args = parser.parse_args(["crash", "analyze", "05d8356e2f800000"])
        self.assertEqual(args.subcommand, "crash")
        self.assertEqual(args.crash_cmd, "analyze")
        self.assertEqual(args.crash_id, "05d8356e2f800000")

    def test_get_crashadvisor_sandbox_dir(self):
        from commands.crash import get_crashadvisor_sandbox_dir

        sandbox = get_crashadvisor_sandbox_dir("05d8356e2f800000")
        self.assertIn("crashadvisor_05d8356e2f800000_", sandbox)

    def test_acquire_auth_token(self):
        from commands.crash import acquire_auth_token

        # Test explicit user token with Bearer prefix stripping
        self.assertEqual(acquire_auth_token("Bearer my_test_token\n"), "my_test_token")
        self.assertEqual(acquire_auth_token("my_test_token"), "my_test_token")

    def test_ensure_crashadvisor_imports(self):
        from commands.crash import ensure_crashadvisor_imports

        modules = ensure_crashadvisor_imports()
        self.assertIn("advisor", modules)
        self.assertIn("symbols", modules)
        self.assertIn("buganizer", modules)

    def test_is_path_secure_user_owned(self):
        import tempfile
        from commands.crash import is_path_secure_user_owned

        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1. Secure directory (mode 0o700)
            os.chmod(tmp_dir, 0o700)
            self.assertTrue(is_path_secure_user_owned(tmp_dir, is_dir=True))
            self.assertFalse(
                is_path_secure_user_owned(tmp_dir, is_dir=False)
            )  # Mismatch: expected file

            # 2. Insecure directory (mode 0o777)
            os.chmod(tmp_dir, 0o777)
            self.assertFalse(is_path_secure_user_owned(tmp_dir, is_dir=True))

            # Restore dir permissions for cleanup
            os.chmod(tmp_dir, 0o700)

            # 3. Secure file (mode 0o700)
            test_file = os.path.join(tmp_dir, "test.sh")
            with open(test_file, "w") as f:
                f.write("#!/bin/sh\necho test\n")
            os.chmod(test_file, 0o700)
            self.assertTrue(is_path_secure_user_owned(test_file, is_dir=False))
            self.assertFalse(
                is_path_secure_user_owned(test_file, is_dir=True)
            )  # Mismatch: expected dir

            # 4. Insecure file (group & other writable 0o777)
            os.chmod(test_file, 0o777)
            self.assertFalse(is_path_secure_user_owned(test_file, is_dir=False))

            # 5. Symlink check
            symlink_path = os.path.join(tmp_dir, "test_symlink.sh")
            os.chmod(test_file, 0o700)
            os.symlink(test_file, symlink_path)
            self.assertFalse(is_path_secure_user_owned(symlink_path, is_dir=False))

            # 6. Non-existent path
            self.assertFalse(
                is_path_secure_user_owned(
                    os.path.join(tmp_dir, "nonexistent.sh"), is_dir=False
                )
            )



if __name__ == "__main__":
    unittest.main()

