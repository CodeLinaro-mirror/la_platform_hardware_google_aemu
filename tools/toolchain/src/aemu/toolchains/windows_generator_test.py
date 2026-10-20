# -*- coding: utf-8 -*-
# Copyright 2023 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
from aemu.toolchains.windows_generator import WindowsToWindowsGenerator


class TestWindowsToWindowsGenerator(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.aosp = Path("/aosp")
        self.dest = Path(self.test_dir) / "dest"
        self.prefix = "test-"
        self.versions = {"clang": "clang-test", "rust": "1.78.0"}

        # Mock Bazel
        self.mock_bazel = MagicMock()
        self.mock_bazel.info = {
            "output_base": "/ob",
            "output_path": "/op",
            "workspace": "/ws",
            "bazel-bin": "/bb",
        }

        with patch("aemu.toolchains.toolchain_generator.CommandLineReconstructor"):
            self.gen = WindowsToWindowsGenerator(
                self.aosp, self.dest, self.prefix, self.versions
            )
            self.gen.bazel = self.mock_bazel

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_tool_methods(self):
        # Verify that new and overridden methods return correctly quoted paths with .exe
        clang_bin = (
            self.aosp
            / "prebuilts"
            / "clang"
            / "host"
            / "linux-x86"
            / "clang-test"
            / "bin"
        )

        self.assertEqual(self.gen.nm()[0], f'"{clang_bin / "llvm-nm.exe"}"')
        self.assertEqual(self.gen.ar()[0], f'"{clang_bin / "llvm-ar.exe"}"')
        self.assertEqual(
            self.gen.llvm_objcopy()[0], f'"{clang_bin / "llvm-objcopy.exe"}"'
        )
        self.assertEqual(self.gen.clang_tidy()[0], f'"{clang_bin / "clang-tidy.exe"}"')
        self.assertEqual(self.gen.dsymutil()[0], f'"{clang_bin / "dsymutil.exe"}"')
        self.assertEqual(self.gen.lldb()[0], f'"{clang_bin / "lldb.exe"}"')

    @patch(
        "aemu.toolchains.windows_generator.WindowsToWindowsGenerator._load_visual_studio_env"
    )
    @patch("aemu.toolchains.windows_generator.WindowsToWindowsGenerator.gen_script")
    @patch(
        "aemu.toolchains.windows_generator.WindowsToWindowsGenerator.write_toolchain_config"
    )
    @patch("aemu.toolchains.windows_generator.WindowsToWindowsGenerator.link_dirs")
    @patch(
        "aemu.toolchains.windows_generator.WindowsToWindowsGenerator.generate_pkg_config_files"
    )
    def test_gen_toolchain_registers_new_tools(
        self, mock_gen_pc, mock_link, mock_write, mock_gen_script, mock_load_vs
    ):
        # Mocking bazel.build_target which is called by pkg_config and ninja
        self.mock_bazel.build_target.return_value = []

        self.gen.gen_toolchain([], {})

        # Check some of the new tools are registered
        calls = [c[0][0] for c in mock_gen_script.call_args_list]
        self.assertIn("as", calls)
        self.assertIn("size", calls)
        self.assertIn("readobj", calls)
        self.assertIn("tidy", calls)
        self.assertIn("format", calls)
        self.assertIn("lldb", calls)
        self.assertIn("lib", calls)


if __name__ == "__main__":
    unittest.main()
