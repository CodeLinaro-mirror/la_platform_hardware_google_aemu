# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for emu-dev-cli tidy add."""

import argparse
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


from commands.tidy.add_cmd import handle_tidy_add


class TidyAddTest(unittest.TestCase):

    def test_handle_tidy_add_new_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            build_file = Path(tmpdir) / "BUILD.bazel"
            build_file.write_text(
                '# Copyright 2026 The Android Open Source Project\n# License header\n\ncc_library(name = "my_lib", srcs = ["my_lib.cc"])\n',
                encoding="utf-8",
            )

            args = argparse.Namespace(build_file=str(build_file), targets=["my_lib"])
            handle_tidy_add(args)

            result = build_file.read_text(encoding="utf-8")
            lines = result.splitlines()
            self.assertTrue(lines[0].startswith("# Copyright"))
            self.assertTrue(lines[1].startswith("# License"))
            self.assertIn(
                'load("@goldfish_build//rules:clang_tidy.bzl", "clang_tidy_test")',
                result,
            )
            self.assertIn("clang_tidy_test(", result)
            self.assertIn('":my_lib",', result)

    def test_handle_tidy_add_update_existing_rule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            build_file = Path(tmpdir) / "BUILD.bazel"
            build_file.write_text(
                """# Copyright 2026
load("@goldfish_build//rules:clang_tidy.bzl", "clang_tidy_test")

clang_tidy_test(
    name = "tidy",
    targets = [
        ":existing_target",
    ],
    tidy_config_file = "//:clang_tidy_config",
)
""",
                encoding="utf-8",
            )

            args = argparse.Namespace(
                build_file=str(build_file), targets=["existing_target", "new_target"]
            )
            handle_tidy_add(args)

            result = build_file.read_text(encoding="utf-8")
            self.assertIn('":existing_target",', result)
            self.assertIn('":new_target",', result)
            # Ensure no duplication of existing_target

    def test_handle_tidy_add_directory_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            build_file = Path(tmpdir) / "BUILD.bazel"
            build_file.write_text(
                'cc_library(name = "dir_lib", srcs = ["dir_lib.cc"])\n',
                encoding="utf-8",
            )

            # Pass the parent directory rather than BUILD.bazel explicitly
            args = argparse.Namespace(path=tmpdir, build_file=None, targets=["dir_lib"])
            handle_tidy_add(args)

            result = build_file.read_text(encoding="utf-8")
            self.assertIn("clang_tidy_test(", result)
            self.assertIn('":dir_lib",', result)

    def test_handle_tidy_add_nonexistent_path_actionable_error(self):
        from io import StringIO

        args = argparse.Namespace(
            path="nonexistent/fake/package", build_file=None, targets=["fake"]
        )
        captured_stderr = StringIO()
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", captured_stderr):
                handle_tidy_add(args)

        error_output = captured_stderr.getvalue()
        self.assertIn("Error: Target BUILD.bazel file does not exist", error_output)
        self.assertIn("Actionable suggestions", error_output)
        self.assertIn("emu-dev-cli tidy add", error_output)

    def test_handle_tidy_add_auto_discovers_all_cc_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            build_file = Path(tmpdir) / "BUILD.bazel"
            build_file.write_text(
                """cc_library(
    name = "lib_one",
    srcs = ["one.cc"],
)

cc_binary(
    name = "bin_tool",
    srcs = ["tool.cc"],
)

cc_test(
    name = "lib_one_test",
    srcs = ["test.cc"],
)
""",
                encoding="utf-8",
            )

            # Targets list is empty / None
            args = argparse.Namespace(path=tmpdir, build_file=None, targets=None)
            handle_tidy_add(args)

            result = build_file.read_text(encoding="utf-8")
            self.assertIn('":lib_one",', result)
            self.assertIn('":bin_tool",', result)
            self.assertIn('":lib_one_test",', result)


if __name__ == "__main__":
    unittest.main()
