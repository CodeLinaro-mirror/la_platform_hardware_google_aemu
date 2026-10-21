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
from aemu.toolchains.linux_arm_generator import LinuxToLinuxAarch64Generator


class TestLinuxToLinuxAarch64Generator(unittest.TestCase):
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
            self.gen = LinuxToLinuxAarch64Generator(
                self.aosp, self.dest, self.prefix, self.versions
            )
            self.gen.bazel = self.mock_bazel

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch(
        "aemu.toolchains.linux_arm_generator.LinuxToLinuxAarch64Generator._fetch_toolchain",
        return_value=Path("/sysroot"),
    )
    def test_tool_methods(self, mock_fetch):
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
        self.assertIn("--target=aarch64-none-linux-gnu", self.gen.cc()[0])

    @patch(
        "aemu.toolchains.linux_arm_generator.LinuxToLinuxAarch64Generator.gen_script"
    )
    @patch(
        "aemu.toolchains.linux_arm_generator.LinuxToLinuxAarch64Generator.write_toolchain_config"
    )
    @patch("aemu.toolchains.linux_arm_generator.LinuxToLinuxAarch64Generator.link_dirs")
    @patch(
        "aemu.toolchains.linux_arm_generator.LinuxToLinuxAarch64Generator.generate_pkg_config_files"
    )
    def test_gen_toolchain(self, mock_gen_pc, mock_link, mock_write, mock_gen_script):
        self.mock_bazel.build_target.return_value = []
        self.gen.gen_toolchain([], {})

        calls = [c[0][0] for c in mock_gen_script.call_args_list]
        self.assertIn("cc", calls)
        self.assertIn("ld.lld", calls)

    def test_hermetic_sysroot_and_cross_config(self):
        hermetic_sysroot = Path(self.test_dir) / "arm_sysroot"
        self.gen.sysroot = hermetic_sysroot
        self.gen.wrapper_path = lambda p: p

        self.assertEqual(self.gen._fetch_toolchain(), hermetic_sysroot)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.gen.toolchain_map = {"cc": "cc", "cxx": "c++"}
        self.gen.write_toolchain_config()
        ini_content = (self.dest / "aosp-cl.ini").read_text(encoding="utf-8")
        self.assertIn("skip_sanity_check = true", ini_content)
        self.assertIn("[host_machine]", ini_content)
        self.assertIn("cpu_family = 'aarch64'", ini_content)


if __name__ == "__main__":
    unittest.main()
