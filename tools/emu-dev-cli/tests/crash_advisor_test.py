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

"""Unit tests for commands.crash.advisor module."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.advisor import (
    ensure_crashadvisor_imports,
    run_crashadvisor_bazel,
)


class CrashAdvisorTest(unittest.TestCase):
    """Tests for CrashAdvisor imports setup and execution."""

    def test_ensure_crashadvisor_imports(self):
        """Tests setting up sys.path and importing CrashAdvisor modules."""
        modules = ensure_crashadvisor_imports()
        self.assertIn("advisor", modules)
        self.assertIn("symbols", modules)
        self.assertIn("buganizer", modules)

    @patch("subprocess.run")
    @patch("lib.workspace.WorkspacePathResolver.find_tool_binary")
    def test_run_crashadvisor_bazel_executable_found(self, mock_find_bin, mock_run):
        """Tests executing CrashAdvisor via precompiled binary executable."""
        mock_find_bin.return_value = Path("/path/to/advisor")
        mock_run.return_value = MagicMock(returncode=0)

        res = run_crashadvisor_bazel(["12345", "--auto-run"])
        mock_run.assert_called_once_with(
            ["/path/to/advisor", "12345", "--auto-run"], check=True
        )

    @patch("subprocess.run")
    @patch("lib.workspace.WorkspacePathResolver.find_tool_binary")
    @patch("lib.workspace.WorkspacePathResolver.find_directory")
    @patch("lib.bazel.find_bazel_cmd")
    def test_run_crashadvisor_bazel_fallback(
        self, mock_find_bazel, mock_find_dir, mock_find_bin, mock_run
    ):
        """Tests fallback execution via Bazel when executable binary is absent."""
        mock_find_bin.return_value = None
        mock_find_dir.return_value = Path("/work/emu-main-next/hardware/google/aemu")
        mock_find_bazel.return_value = "bazel"
        mock_run.return_value = MagicMock(returncode=0)

        res = run_crashadvisor_bazel(["12345"])
        mock_run.assert_called_once_with(
            [
                "bazel",
                "run",
                "@goldfish//emulator/crashreport/tool/advisor",
                "--",
                "12345",
            ],
            cwd="/work/emu-main-next",
            check=True,
        )

    @patch("subprocess.run")
    @patch("lib.workspace.WorkspacePathResolver.find_tool_binary")
    def test_run_crashadvisor_bazel_error_handling(self, mock_find_bin, mock_run):
        """Tests handling of CalledProcessError when check=False."""
        mock_find_bin.return_value = Path("/path/to/advisor")
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["/path/to/advisor"]
        )

        res = run_crashadvisor_bazel(["12345"], check=False)
        self.assertEqual(res.returncode, 1)

        with self.assertRaises(subprocess.CalledProcessError):
            run_crashadvisor_bazel(["12345"], check=True)


if __name__ == "__main__":
    unittest.main()
