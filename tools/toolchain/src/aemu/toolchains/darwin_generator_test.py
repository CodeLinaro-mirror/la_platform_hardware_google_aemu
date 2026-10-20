# -*- coding: utf-8 -*-
# Copyright 2025 - The Android Open Source Project
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
from aemu.toolchains.darwin_generator import DarwinToDarwinGenerator


class TestDarwinToDarwinGenerator(unittest.TestCase):
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

        def mock_check_output(cmd, **kwargs):
            if cmd == ["xcodebuild", "-version"]:
                return "Xcode 15.0\nBuild version 15A240d"
            if cmd == ["xcode-select", "-print-path"]:
                return "/Applications/Xcode.app/Contents/Developer"
            if cmd == ["xcodebuild", "-showsdks"]:
                return "macOS SDKs:\n\tmacOS 14.0                    \t-sdk macosx14.0"
            return ""

        with patch("aemu.toolchains.toolchain_generator.CommandLineReconstructor"):
            with patch(
                "aemu.toolchains.darwin_generator.check_output",
                side_effect=mock_check_output,
            ):
                self.gen = DarwinToDarwinGenerator(
                    self.aosp, self.dest, self.prefix, self.versions
                )
                self.gen.bazel = self.mock_bazel

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_tool_methods(self):
        clang_bin = (
            self.aosp
            / "prebuilts"
            / "clang"
            / "host"
            / "linux-x86"
            / "clang-test"
            / "bin"
        )

        self.assertEqual(self.gen.nm()[0], f'"{clang_bin / "llvm-nm"}"')
        self.assertIn("clang", self.gen.cc()[0])
        self.assertIn("-arch arm64", self.gen.cc()[0])

    @patch("aemu.toolchains.darwin_generator.DarwinToDarwinGenerator.gen_script")
    @patch(
        "aemu.toolchains.darwin_generator.DarwinToDarwinGenerator.write_toolchain_config"
    )
    @patch("aemu.toolchains.darwin_generator.DarwinToDarwinGenerator.link_dirs")
    @patch(
        "aemu.toolchains.darwin_generator.DarwinToDarwinGenerator.generate_pkg_config_files"
    )
    def test_gen_toolchain(self, mock_gen_pc, mock_link, mock_write, mock_gen_script):
        self.mock_bazel.build_target.return_value = []
        self.gen.gen_toolchain([], {})

        calls = [c[0][0] for c in mock_gen_script.call_args_list]
        self.assertIn("cc", calls)
        self.assertIn("objc", calls)
        self.assertIn("ld64.lld", calls)


if __name__ == "__main__":
    unittest.main()
