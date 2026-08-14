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

"""Unit tests for 'emu-dev-cli flakiness triage'."""

import argparse
import unittest
from unittest.mock import MagicMock, patch
from commands import flakiness
from lib.ath_api import FlakyTestRecord


class FlakinessTriageTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)

    @patch("lib.agent.verify_agentapi_available")
    @patch("commands.flakiness.triage.generate_ai_patch_for_test")
    @patch("commands.flakiness.triage.query_ath_flaky_tests")
    @patch("commands.flakiness.triage.BuganizerClient.find_existing_open_bug")
    @patch("commands.flakiness.triage.BuganizerClient.create_bug")
    @patch("commands.flakiness.triage.print_result")
    def test_handle_flakiness_triage_dry_run(
        self, mock_print_result, mock_create_bug, mock_find, mock_query, mock_patch, mock_verify
    ):
        from lib.ai_patch_generator import AIPatchResult
        mock_patch.return_value = AIPatchResult(
            test_identifier="@goldfish//emulator/libs/async:loop_handoff_test",
            root_cause_summary="Test Summary",
            detailed_explanation="Test Explanation",
            proposed_diff="--- a/test.cpp\n+++ b/test.cpp\n",
        )
        mock_query.return_value = [
            FlakyTestRecord(
                test_identifier="@goldfish//emulator/libs/async:loop_handoff_test",
                module_name="async",
                target="emulator_linux_x64_tsan",
                config_name="devtools/emulator",
                total_runs=50,
                failed_runs=10,
                flake_rate_pct=20.0,
                latest_build_id="15900270",
                latest_invocation_id="I75500010177130681",
                latest_work_unit_id="WU44400250003168346",
            )
        ]
        mock_find.return_value = None
        mock_create_bug.return_value = "b/DRY_RUN_BUG_ID"

        args = self.parser.parse_args(
            [
                "flakiness",
                "triage",
                "--test",
                "@goldfish//emulator/libs/async:loop_handoff_test",
                "--target",
                "emulator_linux_x64_tsan",
                "--dry-run",
            ]
        )
        flakiness.handle_flakiness_triage(args)
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertEqual(data["triaged_count"], 1)
        self.assertEqual(data["results"][0]["status"], "NEW_FLAKY_TEST")

    @patch("lib.agent.verify_agentapi_available")
    @patch("commands.flakiness.triage.query_ath_flaky_tests")
    def test_handle_flakiness_triage_ath_auth_error_raises_fatal(
        self, mock_query, mock_verify
    ):
        mock_query.side_effect = RuntimeError(
            "Authentication/Authorization failed for Android Test Hub API (HTTP 403: Forbidden)."
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "triage",
                "--test",
                "@goldfish//emulator/libs/process:process_unittests",
            ]
        )
        with self.assertRaises(RuntimeError) as ctx:
            flakiness.handle_flakiness_triage(args)
        self.assertIn("Authentication/Authorization failed", str(ctx.exception))

    @patch("lib.agent.verify_agentapi_available")
    @patch("commands.flakiness.triage.query_ath_flaky_tests")
    def test_handle_flakiness_triage_no_ath_records_raises_fatal(
        self, mock_query, mock_verify
    ):
        mock_query.return_value = []
        args = self.parser.parse_args(
            [
                "flakiness",
                "triage",
                "--test",
                "@goldfish//emulator/libs/process:process_unittests",
            ]
        )
        with self.assertRaises(RuntimeError) as ctx:
            flakiness.handle_flakiness_triage(args)
        self.assertIn("No execution or flakiness records found in ATH", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
