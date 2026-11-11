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
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)

from commands.crash.analyze import CrashAnalyzeCommand, register_analyze_parser
from core.command import CommandResult
from core.context import ExecutionContext
from core.errors import CommandValidationError


class CrashAnalyzeTest(unittest.TestCase):
    """Tests for crash analyze parser registration and Command execution."""

    def test_register_analyze_parser(self):
        """Tests ArgumentParser registration for `analyze` subcommand."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="crash_cmd")
        register_analyze_parser(subparsers)

        args = parser.parse_args(["analyze", "05d8356e2f800000", "--file-bug"])
        self.assertEqual(args.crash_cmd, "analyze")
        self.assertEqual(args.crash_id, "05d8356e2f800000")
        self.assertTrue(args.file_bug)
        self.assertIsInstance(
            getattr(args, "_command_instance"), CrashAnalyzeCommand
        )

    def test_validate_rejects_empty_crash_id(self):
        ctx = ExecutionContext.for_testing()
        cmd = CrashAnalyzeCommand()
        args = argparse.Namespace(crash_id="   ", file_bug=False, autofix=False)
        with self.assertRaises(CommandValidationError):
            cmd.validate(args, ctx)

    @patch("commands.crash.analyze.CrashFileBugCommand.execute")
    def test_execute_delegates_to_file_bug(self, mock_file_bug):
        """Tests CrashAnalyzeCommand delegating to file_bug when --file-bug is flag set."""
        mock_file_bug.return_value = CommandResult.success(
            {"bug_id": "123"}, action="crash file-bug"
        )
        ctx = ExecutionContext.for_testing()
        cmd = CrashAnalyzeCommand()
        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            file_bug=True,
            autofix=False,
        )
        res = cmd.execute(args, ctx)
        mock_file_bug.assert_called_once_with(args, ctx)
        self.assertTrue(res.ok)

    @patch("commands.crash.analyze.CrashAutofixCommand.execute")
    def test_execute_delegates_to_autofix(self, mock_autofix):
        """Tests CrashAnalyzeCommand delegating to autofix when --autofix flag is set."""
        mock_autofix.return_value = CommandResult.success(
            {"status": "dry_run"}, action="crash autofix"
        )
        ctx = ExecutionContext.for_testing()
        cmd = CrashAnalyzeCommand()
        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            file_bug=False,
            autofix=True,
        )
        res = cmd.execute(args, ctx)
        mock_autofix.assert_called_once_with(args, ctx)
        self.assertTrue(res.ok)

    @patch("commands.crash.analyze.CrashFindBugCommand.execute")
    @patch("commands.crash.analyze.CrashAdvisorRunner.run")
    def test_execute_auto_run_analysis(self, mock_runner_run, mock_find_bug):
        mock_find_bug.return_value = CommandResult.success(
            {"stable_signature": "sig_foo"}, action="crash find-bug"
        )
        ctx = ExecutionContext.for_testing()
        cmd = CrashAnalyzeCommand()
        args = argparse.Namespace(
            crash_id="05d8356e2f800000",
            file_bug=False,
            autofix=False,
            auto_run=True,
            timeout="10m",
            token="tok",
            work_dir=None,
        )
        res = cmd.execute(args, ctx)
        self.assertTrue(res.ok)
        mock_find_bug.assert_called_once_with(args, ctx)
        mock_runner_run.assert_called_once()
        self.assertEqual(res.data["action"], "crash analyze")

    @patch("commands.crash.analyze.is_path_secure_user_owned")
    @patch("commands.crash.analyze.CrashAdvisorRunner.run")
    @patch("commands.crash.analyze.acquire_auth_token")
    def test_execute_interactive_runs_investigation_script(
        self, mock_acquire_token, mock_runner_run, mock_is_secure
    ):
        mock_acquire_token.return_value = "token_xyz"
        mock_is_secure.return_value = True
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            script_path = work_dir / "investigation_cmd.sh"
            script_path.write_text("#!/bin/sh\necho 'investigating'\n", encoding="utf-8")
            ctx = ExecutionContext.for_testing()
            cmd = CrashAnalyzeCommand()
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                file_bug=False,
                autofix=False,
                auto_run=False,
                interactive=True,
                timeout="10m",
                token="tok",
                work_dir=str(work_dir),
            )
            res = cmd.execute(args, ctx)
            self.assertTrue(res.ok)
            self.assertEqual(len(ctx.process_runner.calls), 1)
            self.assertEqual(ctx.process_runner.calls[0].args, ("bash", str(script_path)))


if __name__ == "__main__":
    unittest.main()
