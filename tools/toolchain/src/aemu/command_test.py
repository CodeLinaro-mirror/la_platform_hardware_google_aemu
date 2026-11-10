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
import argparse
import sys
import unittest
import os
from pathlib import Path
from unittest.mock import patch
import shlex
from aemu.command import CommandLineReconstructor


class TestCommandLineReconstructor(unittest.TestCase):
    @patch("sys.argv", ["amc.py", "setup", "--config", "config.jsonc"])
    @patch.dict(os.environ, {}, clear=True)
    def test_get_command_parts_python(self):
        """Test get_command_parts for Python invocation."""
        reconstructor = CommandLineReconstructor()
        expected = [sys.executable, "amc.py", "setup", "--config", "config.jsonc"]
        self.assertEqual(reconstructor.get_command_parts(), expected)

    @patch("sys.argv", ["amc.py", "setup", "--update"])
    @patch.dict(os.environ, {}, clear=True)
    def test_get_command_parts_python_filter_update(self):
        """Test get_command_parts filters out --update flag for Python invocation."""
        reconstructor = CommandLineReconstructor()
        expected = [sys.executable, "amc.py", "setup"]
        self.assertEqual(reconstructor.get_command_parts(), expected)

    @patch("sys.argv", ["amc.py", "setup", "--config", "config.jsonc"])
    @patch.dict(os.environ, {"BAZEL_SELF_LABEL": "//tools/toolchain:amc"})
    def test_get_command_parts_bazel(self):
        """Test get_command_parts for Bazel invocation."""
        reconstructor = CommandLineReconstructor()
        expected = [
            "bazel",
            "run",
            "//tools/toolchain:amc",
            "--",
            "setup",
            "--config",
            "config.jsonc",
        ]
        self.assertEqual(reconstructor.get_command_parts(), expected)

    @patch("sys.argv", ["amc.py", "setup", "--update"])
    @patch.dict(os.environ, {"BAZEL_SELF_LABEL": "//tools/toolchain:amc"})
    def test_get_command_parts_bazel_filter_update(self):
        """Test get_command_parts filters out --update flag for Bazel invocation."""
        reconstructor = CommandLineReconstructor()
        expected = ["bazel", "run", "//tools/toolchain:amc", "--", "setup"]
        self.assertEqual(reconstructor.get_command_parts(), expected)

    @patch("sys.argv", ["amc.py", "arg with spaces"])
    @patch.dict(os.environ, {}, clear=True)
    def test_get_command_string_python(self):
        """Test get_command_string for Python invocation with spaces."""
        reconstructor = CommandLineReconstructor()
        expected = f"{shlex.quote(sys.executable)} {shlex.quote('amc.py')} {shlex.quote('arg with spaces')}"
        self.assertEqual(reconstructor.get_command_string(), expected)

    @patch("sys.argv", ["C:\\Program Files\\Python\\python.exe", "C:\\Users\\Doctor Doom\\Documents\\my script.py", "arg1"])
    @patch.dict(os.environ, {}, clear=True)
    def test_get_command_parts_python_with_spaces_in_path(self):
        """Test get_command_parts for Python invocation with spaces in script path."""
        reconstructor = CommandLineReconstructor()
        # When sys.argv[0] is a path with spaces, sys.executable is not used.
        # The script itself is the first part of the command.
        expected = [sys.executable, "C:\\Program Files\\Python\\python.exe", "C:\\Users\\Doctor Doom\\Documents\\my script.py", "arg1"]
        self.assertEqual(reconstructor.get_command_parts(), expected)

    @patch("sys.argv", ["C:\\Program Files\\Python\\python.exe", "C:\\Users\\Doctor Doom\\Documents\\my script.py", "arg1"])
    @patch.dict(os.environ, {}, clear=True)
    def test_get_command_string_python_with_spaces_in_path(self):
        """Test get_command_string for Python invocation with spaces in script path."""
        reconstructor = CommandLineReconstructor()
        python_exe_path = 'C:\\Program Files\\Python\\python.exe'
        script_path = 'C:\\Users\\Doctor Doom\\Documents\\my script.py'
        arg1 = 'arg1'
        expected = f"{shlex.quote(sys.executable)} {shlex.quote(python_exe_path)} {shlex.quote(script_path)} {shlex.quote(arg1)}"
        self.assertEqual(reconstructor.get_command_string(), expected)

    @patch("os.getcwd", return_value="/current/working/dir")
    @patch.dict(os.environ, {}, clear=True)
    def test_get_cwd_no_bazel_env(self, mock_getcwd):
        """Test get_cwd when BAZEL_WORKSPACE_DIRECTORY is not set."""
        reconstructor = CommandLineReconstructor()
        self.assertEqual(reconstructor.get_cwd(), "/current/working/dir")
        mock_getcwd.assert_called_once()

    @patch("os.getcwd", return_value="/current/working/dir")
    @patch.dict(os.environ, {"BUILD_WORKSPACE_DIRECTORY": "/bazel/workspace with spaces"})
    def test_get_cwd_with_bazel_env_and_spaces(self, mock_getcwd):
        """Test get_cwd when BAZEL_WORKSPACE_DIRECTORY is set with spaces."""
        reconstructor = CommandLineReconstructor()
        self.assertEqual(reconstructor.get_cwd(), "/bazel/workspace with spaces")
        mock_getcwd.assert_not_called()

    @patch("os.getcwd", return_value="/current/working/dir")
    @patch.dict(os.environ, {"BUILD_WORKSPACE_DIRECTORY": "/bazel/workspace"})
    def test_get_cwd_with_bazel_env(self, mock_getcwd):
        """Test get_cwd when BAZEL_WORKSPACE_DIRECTORY is set."""
        reconstructor = CommandLineReconstructor()
        self.assertEqual(reconstructor.get_cwd(), "/bazel/workspace")
        mock_getcwd.assert_not_called()

    @patch("os.getcwd", return_value="/ephemeral/amc.runfiles/_main")
    @patch.dict(
        os.environ,
        {
            "BUILD_WORKING_DIRECTORY": "/bazel/workspace/subdir",
            "BUILD_WORKSPACE_DIRECTORY": "/bazel/workspace",
        },
        clear=True,
    )
    def test_get_cwd_prefers_build_working_directory(self, mock_getcwd):
        """Test get_cwd prefers BUILD_WORKING_DIRECTORY over BUILD_WORKSPACE_DIRECTORY."""
        reconstructor = CommandLineReconstructor()
        self.assertEqual(reconstructor.get_cwd(), "/bazel/workspace/subdir")
        mock_getcwd.assert_not_called()

    @patch("os.getcwd", return_value="/ephemeral/amc.runfiles/_main")
    @patch.dict(
        os.environ,
        {"BUILD_WORKING_DIRECTORY": "/bazel/workspace/测试_🚀_é"},
        clear=True,
    )
    def test_resolve_path_relative_and_unicode(self, mock_getcwd):
        """Test resolve_path and resolve_cli_paths resolve relative paths against BUILD_WORKING_DIRECTORY."""
        reconstructor = CommandLineReconstructor()
        self.assertEqual(
            reconstructor.resolve_path("out/qemu-bld"),
            Path("/bazel/workspace/测试_🚀_é/out/qemu-bld"),
        )
        self.assertEqual(
            reconstructor.resolve_path("/abs/path/qemu-bld"),
            Path("/abs/path/qemu-bld"),
        )
        args = argparse.Namespace(
            out="out/qemu-bld",
            release="dist/trusty.zip",
            config="/abs/qemu-trusty-build-config.jsonc",
            aosp=None,
        )
        reconstructor.resolve_cli_paths(args)
        self.assertEqual(args.out, Path("/bazel/workspace/测试_🚀_é/out/qemu-bld"))
        self.assertEqual(args.release, "/bazel/workspace/测试_🚀_é/dist/trusty.zip")
        self.assertEqual(args.config, "/abs/qemu-trusty-build-config.jsonc")
        self.assertIsNone(args.aosp)
        mock_getcwd.assert_not_called()

    @patch("os.getcwd", side_effect=FileNotFoundError)
    @patch.dict(os.environ, {}, clear=True)
    def test_get_cwd_file_not_found_fallback(self, mock_getcwd):
        """Test get_cwd falls back to provided fallback on FileNotFoundError."""
        reconstructor = CommandLineReconstructor()
        fallback_path = Path("/fallback/path")
        self.assertEqual(
            reconstructor.get_cwd(fallback=fallback_path), str(fallback_path)
        )
        mock_getcwd.assert_called_once()


if __name__ == "__main__":
    unittest.main()
