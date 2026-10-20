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

"""Unit tests for commands.crash.file_bug module."""

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

from commands.crash.file_bug import register_file_bug_parser, run_file_bug


class CrashFileBugTest(unittest.TestCase):
    """Tests for crash file-bug parser registration and execution handler."""

    def test_register_file_bug_parser(self):
        """Tests ArgumentParser registration for `file-bug` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_file_bug_parser(subparsers)

        args = parser.parse_args(
            ["file-bug", "05d8356e2f800000", "--token", "tok_123", "--qa"]
        )
        self.assertEqual(args.crash_cmd, "file-bug")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertEqual(args.token, "tok_123")
        self.assertTrue(args.qa)
        self.assertEqual(args.func, run_file_bug)

    @patch("commands.crash.file_bug.run_crashadvisor_bazel")
    @patch("commands.crash.file_bug.acquire_auth_token")
    def test_run_file_bug_success(self, mock_acquire_token, mock_run_bazel):
        """Tests run_file_bug handler delegating to CrashAdvisor via Bazel."""
        mock_acquire_token.return_value = "token_abc"
        mock_run_bazel.return_value = MagicMock(returncode=0)

        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            token="token_abc",
            qa=True,
            verbose=False,
        )

        run_file_bug(args)
        mock_run_bazel.assert_called_once()
        call_args = mock_run_bazel.call_args[0][0]
        self.assertEqual(call_args[0], "05d8356e2f800000")
        self.assertIn("--enable-buganizer", call_args)
        self.assertIn("--auto-run", call_args)
        self.assertIn("--work-dir", call_args)
        self.assertIn("--token", call_args)
        self.assertIn("--qa", call_args)


if __name__ == "__main__":
    unittest.main()
