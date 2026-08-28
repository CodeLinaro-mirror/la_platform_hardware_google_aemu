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

"""Unit tests for commands.crash.analyze module."""

import argparse
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.analyze import register_analyze_parser, run_analyze


class CrashAnalyzeTest(unittest.TestCase):
    """Tests for crash analyze parser registration and execution handler."""

    def test_register_analyze_parser(self):
        """Tests ArgumentParser registration for `analyze` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_analyze_parser(subparsers)

        args = parser.parse_args(["analyze", "05d8356e2f800000", "--file-bug"])
        self.assertEqual(args.crash_cmd, "analyze")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertTrue(args.file_bug)
        self.assertEqual(args.func, run_analyze)

    @patch("commands.crash.analyze.run_file_bug")
    def test_run_analyze_file_bug(self, mock_file_bug):
        """Tests run_analyze delegating to run_file_bug when --file-bug is flag set."""
        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            file_bug=True,
            autofix=False,
        )
        run_analyze(args)
        mock_file_bug.assert_called_once_with(args)

    @patch("commands.crash.analyze.run_autofix")
    def test_run_analyze_autofix(self, mock_autofix):
        """Tests run_analyze delegating to run_autofix when --autofix flag is set."""
        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            file_bug=False,
            autofix=True,
        )
        run_analyze(args)
        mock_autofix.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
