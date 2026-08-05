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

"""Unit tests for lib.bazel module."""

import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.bazel import (
    BazelRunner,
    BuildExitCode,
    QueryExitCode,
    find_bazel_cmd,
    format_error_msg,
    try_convert_exit_code,
)


class BazelRunnerTest(unittest.TestCase):
    """Tests for BazelRunner and Bazel binary discovery."""

    def test_find_bazel_cmd_prebuilt(self):
        """Tests finding prebuilt Bazel binary in workspace."""
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
            bazel_dir = Path(tmp_dir) / "prebuilts" / "bazel" / p_dir
            bazel_dir.mkdir(parents=True, exist_ok=True)
            mock_bazel = bazel_dir / ("bazel.exe" if system == "windows" else "bazel")
            mock_bazel.write_text("#!/bin/sh\necho 1\n", encoding="utf-8")
            if system != "windows":
                mock_bazel.chmod(0o755)

            resolved = find_bazel_cmd(tmp_dir)
            self.assertEqual(Path(resolved).resolve(), mock_bazel.resolve())

    @patch("subprocess.run")
    def test_bazel_runner_run_target(self, mock_run):
        """Tests executing a Bazel target."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = BazelRunner(bazel_binary="/bin/bazel")
        runner.run("@goldfish//advisor", args=["12345"], cwd="/work/repo")

        mock_run.assert_called_once_with(
            ["/bin/bazel", "run", "@goldfish//advisor", "--", "12345"],
            cwd="/work/repo",
            check=True,
        )

    @patch("subprocess.run")
    def test_bazel_runner_build_targets(self, mock_run):
        """Tests building Bazel targets."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = BazelRunner(bazel_binary="/bin/bazel")
        runner.build(["//target1", "//target2"], cwd="/work/repo", check=False)

        mock_run.assert_called_once_with(
            ["/bin/bazel", "build", "//target1", "//target2"],
            cwd="/work/repo",
            check=False,
            capture_output=False,
            text=True,
        )

    @patch("subprocess.run")
    def test_bazel_runner_cquery(self, mock_run):
        """Tests running a Bazel cquery."""
        mock_run.return_value = MagicMock(returncode=0, stdout="//target:out.bin\n")
        runner = BazelRunner(bazel_binary="/bin/bazel", source_dir="/work/repo")
        res = runner.cquery("//target:all", invocation_flags=["--output=files"])

        mock_run.assert_called_once_with(
            ["/bin/bazel", "cquery", "--output=files", "//target:all"],
            cwd="/work/repo",
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.stdout.strip(), "//target:out.bin")

    @patch("subprocess.run")
    def test_bazel_runner_info(self, mock_run):
        """Tests querying bazel info."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="execution_root: /work/repo/execroot\noutput_base: /work/repo/outbase\n",
        )
        runner = BazelRunner(bazel_binary="/bin/bazel", source_dir="/work/repo")
        info_dict = runner.get_info()

        self.assertEqual(info_dict["execution_root"], "/work/repo/execroot")
        self.assertEqual(info_dict["output_base"], "/work/repo/outbase")
        self.assertEqual(runner.get_info("execution_root"), "/work/repo/execroot")

    @patch.object(BazelRunner, "get_info")
    @patch.object(BazelRunner, "cquery")
    def test_bazel_runner_query_artifacts(self, mock_cquery, mock_info):
        """Tests resolving target artifact output paths via cquery and execution_root."""
        mock_info.side_effect = lambda key=None, cwd=None: (
            "/work/repo/execroot"
            if key == "execution_root"
            else {"execution_root": "/work/repo/execroot"}
        )
        mock_cquery.return_value = MagicMock(
            returncode=0,
            stdout="bazel-out/k8-fastbuild/bin/target1\nbazel-out/k8-fastbuild/bin/target2\n",
        )

        runner = BazelRunner(bazel_binary="/bin/bazel", source_dir="/work/repo")
        artifacts = runner.query_artifacts(["//target1", "//target2"])

        self.assertEqual(len(artifacts), 2)
        self.assertEqual(
            artifacts[0],
            Path("/work/repo/execroot/bazel-out/k8-fastbuild/bin/target1"),
        )
        self.assertEqual(
            artifacts[1],
            Path("/work/repo/execroot/bazel-out/k8-fastbuild/bin/target2"),
        )

    def test_exit_codes_and_error_formatting(self):
        """Tests exit code conversion and diagnostic message formatting."""
        self.assertEqual(
            try_convert_exit_code(1, BuildExitCode), BuildExitCode.BUILD_FAILED
        )
        self.assertEqual(
            try_convert_exit_code(3, BuildExitCode), BuildExitCode.TESTS_FAILED
        )
        self.assertEqual(
            try_convert_exit_code(7, QueryExitCode), QueryExitCode.QUERY_COMMAND_FAILED
        )
        self.assertEqual(try_convert_exit_code(999, BuildExitCode), 999)

        msg = format_error_msg(
            ["bazel", "build", "//..."],
            BuildExitCode.BUILD_FAILED,
            stderr="Error details",
        )
        self.assertIn("BUILD_FAILED", msg)
        self.assertIn("Error details", msg)


if __name__ == "__main__":
    unittest.main()
