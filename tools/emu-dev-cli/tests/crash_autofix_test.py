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
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)

from commands.crash.autofix import CrashAutofixCommand, register_autofix_parser
from core.context import ExecutionContext
from core.errors import CommandValidationError


class CrashAutofixTest(unittest.TestCase):
    """Tests for crash autofix parser registration and Command execution."""

    def test_register_autofix_parser(self):
        """Tests ArgumentParser registration for `autofix` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_autofix_parser(subparsers)

        args = parser.parse_args(["autofix", "05d8356e2f800000", "--dry-run"])
        self.assertEqual(args.crash_cmd, "autofix")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertTrue(args.dry_run)
        self.assertIsInstance(
            getattr(args, "_command_instance"), CrashAutofixCommand
        )

    def test_validate_rejects_empty_crash_id(self):
        ctx = ExecutionContext.for_testing()
        cmd = CrashAutofixCommand()
        args = argparse.Namespace(crash_id="   ")
        with self.assertRaises(CommandValidationError):
            cmd.validate(args, ctx)

    @patch("commands.crash.autofix.CrashAdvisorRunner.run")
    @patch("commands.crash.autofix.acquire_auth_token")
    def test_run_autofix_dry_run(self, mock_acquire_token, mock_runner_run):
        """Tests autofix command execution in dry-run mode."""
        mock_acquire_token.return_value = "token_abc"

        with tempfile.TemporaryDirectory() as tmp_dir:
            rca_file = os.path.join(tmp_dir, "rca_summary.md")
            with open(rca_file, "w", encoding="utf-8") as f:
                f.write(
                    """
actionability:
  fixable: true
  target_file: android/FrameBuffer.cpp
  target_function: android::FrameBuffer::post
  remediation_summary: Null-check buffer pointer before dereferencing
```
"""
                )

            ctx = ExecutionContext.for_testing()
            cmd = CrashAutofixCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_abc",
                dry_run=True,
                force=False,
                work_dir=tmp_dir,
            )

            res = cmd.execute(args, ctx)
            self.assertEqual(res.status, "dry_run")
            self.assertEqual(res.action, "crash autofix")
            self.assertTrue(res.data["fixable"])
            self.assertEqual(res.data["target_file"], "android/FrameBuffer.cpp")

    @patch("lib.agent.AgentApiClient.start_conversation")
    @patch("commands.crash.autofix.CrashAdvisorRunner.run")
    @patch("commands.crash.autofix.acquire_auth_token")
    def test_run_autofix_dispatch(
        self, mock_acquire_token, mock_runner_run, mock_start_conv
    ):
        """Tests autofix command successfully dispatching agent conversation."""
        mock_acquire_token.return_value = "token_abc"
        mock_start_conv.return_value = MagicMock(
            returncode=0, stdout="Conversation ID: 1234"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            rca_file = os.path.join(tmp_dir, "rca_summary.md")
            with open(rca_file, "w", encoding="utf-8") as f:
                f.write(
                    """
actionability:
  fixable: true
  target_file: android/FrameBuffer.cpp
  target_function: android::FrameBuffer::post
  remediation_summary: Null-check buffer pointer before dereferencing
```
"""
                )

            ctx = ExecutionContext.for_testing()
            cmd = CrashAutofixCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_abc",
                dry_run=False,
                force=True,
                work_dir=tmp_dir,
            )

            res = cmd.execute(args, ctx)
            self.assertEqual(res.status, "dispatched")
            self.assertEqual(res.action, "crash autofix")
            self.assertIn("00000000", res.data["conversation_id"])

    @patch("commands.crash.autofix.evaluate_crash_fix_status")
    @patch("commands.crash.autofix.CrashAdvisorRunner.run")
    @patch("commands.crash.autofix.acquire_auth_token")
    def test_run_autofix_halts_when_already_fixed(
        self,
        mock_acquire_token,
        mock_runner_run,
        mock_eval_fix,
    ):
        """Tests autofix halts without dispatching when bug is already fixed and --force is not passed."""
        mock_acquire_token.return_value = "token_abc"
        mock_eval_fix.return_value = MagicMock(
            is_already_fixed=True,
            fix_status="FIXED",
            fix_reason="Buganizer b/12345678 is FIXED",
            build_id="15953806",
            version_str="34.2.1-15953806",
            older_version_warning="Crash on older build 15953806",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            rca_file = os.path.join(tmp_dir, "rca_summary.md")
            with open(rca_file, "w", encoding="utf-8") as f:
                f.write(
                    """
actionability:
  fixable: true
  target_file: android/FrameBuffer.cpp
  target_function: android::FrameBuffer::post
  remediation_summary: Null-check buffer pointer before dereferencing
```
"""
                )

            ctx = ExecutionContext.for_testing()
            cmd = CrashAutofixCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_abc",
                dry_run=False,
                force=False,
                work_dir=tmp_dir,
            )

            res = cmd.execute(args, ctx)
            self.assertEqual(res.status, "already_fixed")
            self.assertEqual(res.data["fix_reason"], "Buganizer b/12345678 is FIXED")

    @patch("commands.crash.autofix.CrashAdvisorRunner.run")
    @patch("commands.crash.autofix.acquire_auth_token")
    def test_run_autofix_generates_rca_and_halts_unfixable(
        self, mock_acquire_token, mock_runner_run
    ):
        """Tests autofix triggers CrashAdvisor when rca_summary is missing and halts on unfixable crash."""
        mock_acquire_token.return_value = "token_abc"

        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)

            def create_rca(*args, **kwargs):
                (work_dir / "rca_summary.md").write_text(
                    """
actionability:
  fixable: false
  target_file: android/FrameBuffer.cpp
  target_function: android::FrameBuffer::post
""",
                    encoding="utf-8",
                )

            mock_runner_run.side_effect = create_rca

            fake_src = work_dir / "src_repo"
            fake_src.mkdir()
            (fake_src / "android").mkdir()
            (fake_src / "android" / "FrameBuffer.cpp").write_text("// C++ code", encoding="utf-8")

            ctx = ExecutionContext.for_testing(
                source_directories={"emu-main-next": str(fake_src)}
            )
            cmd = CrashAutofixCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_abc",
                dry_run=False,
                force=False,
                work_dir=str(work_dir),
            )

            res = cmd.execute(args, ctx)
            self.assertEqual(res.status, "halted")
            self.assertEqual(res.data["fixable"], False)
            self.assertEqual(res.data["target_file"], "android/FrameBuffer.cpp")

    def test_run_autofix_halts_when_no_actionability_block(self):
        """Tests autofix halts when rca_summary has no actionability yaml block."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            (work_dir / "rca_summary.md").write_text(
                "## Root Cause\nJust raw markdown without actionability block.\n",
                encoding="utf-8",
            )
            ctx = ExecutionContext.for_testing()
            cmd = CrashAutofixCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                token="token_abc",
                dry_run=False,
                force=False,
                work_dir=str(work_dir),
            )
            res = cmd.execute(args, ctx)
            self.assertEqual(res.status, "halted")
            self.assertEqual(res.data["reason"], "no_actionability_block")


if __name__ == "__main__":
    unittest.main()
