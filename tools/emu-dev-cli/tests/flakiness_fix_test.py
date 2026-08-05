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

"""Unit tests for 'emu-dev-cli flakiness fix'."""

import argparse
import unittest
from unittest.mock import MagicMock, patch
from commands import flakiness


class FlakinessFixTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)

    @patch("lib.buganizer.BuganizerClient.fetch_all_component_open_bugs")
    @patch("commands.flakiness.fix.print_result")
    def test_handle_flakiness_fix_resolved_from_bug(
        self, mock_print_result, mock_bugs
    ):
        mock_bugs.return_value = [
            "Issue 123456789: [Flaky Test] @goldfish//emulator/libs/async:loop_handoff_test failing on emulator_linux_x64_tsan (20.0% flake rate)"
        ]
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "123456789",
                "--iterations",
                "50",
                "--dry-run",
            ]
        )
        flakiness.handle_flakiness_fix(args)
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["bug_id"], "123456789")
        self.assertEqual(
            data["test_target"],
            "@goldfish//emulator/libs/async:loop_handoff_test",
        )

    @patch("lib.buganizer.BuganizerClient.fetch_all_component_open_bugs")
    @patch("commands.flakiness.fix.print_result")
    def test_handle_flakiness_fix_unresolved_target_error(
        self, mock_print_result, mock_bugs
    ):
        mock_bugs.return_value = ["Issue 999999: Some other unrelated bug"]
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "999999",
                "--dry-run",
            ]
        )
        flakiness.handle_flakiness_fix(args)
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertEqual(data["error"], "UNRESOLVED_TEST_TARGET")
        self.assertTrue(mock_print_result.call_args[1].get("is_error", False))

    @patch("commands.flakiness.fix.print_result")
    def test_handle_flakiness_fix_dry_run_explicit_test(
        self, mock_print_result
    ):
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "123456789",
                "--test",
                "//emulator/videobridge:in_process_media_provider_test",
                "--iterations",
                "50",
                "--dry-run",
            ]
        )
        flakiness.handle_flakiness_fix(args)
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["bug_id"], "123456789")
        self.assertEqual(
            data["test_target"],
            "//emulator/videobridge:in_process_media_provider_test",
        )

    @patch("subprocess.run")
    @patch("lib.buganizer.BuganizerClient.fetch_all_component_open_bugs")
    @patch("commands.flakiness.fix.print_result")
    def test_handle_flakiness_fix_success_upload(
        self, mock_print_result, mock_bugs, mock_run
    ):
        mock_bugs.return_value = [
            "123456789: [Flaky Test] @goldfish//emulator/libs/async:loop_handoff_test"
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "PASSED 50/50 iterations"
        mock_run.return_value = mock_proc

        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "b/123456789",
                "--iterations",
                "50",
                "--upload",
            ]
        )
        flakiness.handle_flakiness_fix(args)
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertTrue(data["test_passed"])
        self.assertEqual(data["bug_id"], "123456789")
        self.assertEqual(
            data["test_target"],
            "@goldfish//emulator/libs/async:loop_handoff_test",
        )
        self.assertTrue(data["upload"])

    @patch("subprocess.run")
    @patch("lib.buganizer.BuganizerClient.fetch_all_component_open_bugs")
    @patch("commands.flakiness.fix.print_result")
    def test_handle_flakiness_fix_failure(
        self, mock_print_result, mock_bugs, mock_run
    ):
        mock_bugs.return_value = [
            "123456789: [Flaky Test] @goldfish//emulator/libs/async:loop_handoff_test"
        ]
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "FAILED on iteration 12"
        mock_run.return_value = mock_proc

        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "b/123456789",
                "--iterations",
                "50",
                "--upload",
            ]
        )
        flakiness.handle_flakiness_fix(args)
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertFalse(data["test_passed"])
        self.assertTrue(mock_print_result.call_args[1].get("is_error", False))


if __name__ == "__main__":
    unittest.main()
