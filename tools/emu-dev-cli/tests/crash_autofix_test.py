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

"""Unit tests for commands.crash.autofix module."""

import argparse
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.autofix import register_autofix_parser, run_autofix


class CrashAutofixTest(unittest.TestCase):
    """Tests for crash autofix parser registration and execution handler."""

    def test_register_autofix_parser(self):
        """Tests ArgumentParser registration for `autofix` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_autofix_parser(subparsers)

        args = parser.parse_args(["autofix", "05d8356e2f800000", "--dry-run"])
        self.assertEqual(args.crash_cmd, "autofix")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.func, run_autofix)

    @patch("subprocess.run")
    @patch("commands.crash.autofix.get_crashadvisor_sandbox_dir")
    @patch("commands.crash.autofix.run_crashadvisor_bazel")
    @patch("commands.crash.autofix.acquire_auth_token")
    def test_run_autofix_dry_run(
        self, mock_acquire_token, mock_run_bazel, mock_get_sandbox, mock_subprocess_run
    ):
        """Tests run_autofix handler in dry-run mode."""
        mock_acquire_token.return_value = "token_abc"
        mock_run_bazel.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_get_sandbox.return_value = tmp_dir
            rca_file = os.path.join(tmp_dir, "rca_summary.md")
            with open(rca_file, "w", encoding="utf-8") as f:
                f.write("""
actionability:
  fixable: true
  target_file: android/FrameBuffer.cpp
  target_function: android::FrameBuffer::post
  remediation_summary: Null-check buffer pointer before dereferencing
```
""")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_abc",
                dry_run=True,
            )

            with self.assertRaises(SystemExit) as cm:
                run_autofix(args)
            self.assertEqual(cm.exception.code, 0)
            mock_subprocess_run.assert_not_called()

    @patch("lib.agent.AgentApiClient.start_conversation")
    @patch("commands.crash.autofix.get_crashadvisor_sandbox_dir")
    @patch("commands.crash.autofix.run_crashadvisor_bazel")
    @patch("commands.crash.autofix.acquire_auth_token")
    def test_run_autofix_dispatch(
        self, mock_acquire_token, mock_run_bazel, mock_get_sandbox, mock_start_conv
    ):
        """Tests run_autofix handler successfully dispatching agent conversation."""
        mock_acquire_token.return_value = "token_abc"
        mock_run_bazel.return_value = MagicMock(returncode=0)
        mock_start_conv.return_value = MagicMock(
            returncode=0, stdout="Conversation ID: 1234"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_get_sandbox.return_value = tmp_dir
            rca_file = os.path.join(tmp_dir, "rca_summary.md")
            with open(rca_file, "w", encoding="utf-8") as f:
                f.write("""
actionability:
  fixable: true
  target_file: android/FrameBuffer.cpp
  target_function: android::FrameBuffer::post
  remediation_summary: Null-check buffer pointer before dereferencing
```
""")

            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_abc",
                dry_run=False,
            )

            run_autofix(args)
            mock_start_conv.assert_called_once()


if __name__ == "__main__":
    unittest.main()
