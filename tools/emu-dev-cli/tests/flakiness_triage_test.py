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

    @patch("lib.agent.verify_agentapi_available")
    @patch("commands.flakiness.triage.generate_ai_patch_for_test")
    @patch("commands.flakiness.triage.query_studio_flaky_tests")
    @patch("commands.flakiness.triage.BuganizerClient.fetch_all_component_open_bugs")
    @patch("commands.flakiness.triage.BuganizerClient.find_existing_open_bug")
    @patch("commands.flakiness.triage.BuganizerClient.create_bug")
    @patch("commands.flakiness.triage.print_result")
    def test_handle_flakiness_triage_studio_defaults_component_2289192(
        self,
        mock_print_result,
        mock_create_bug,
        mock_find,
        mock_fetch_bugs,
        mock_studio_query,
        mock_patch,
        mock_verify,
    ):
        from lib.ai_patch_generator import AIPatchResult

        mock_patch.return_value = AIPatchResult(
            test_identifier="//tools/vendor/google/cli/android-device-hub/electron:test",
            root_cause_summary="Test Summary",
            detailed_explanation="Test Explanation",
            proposed_diff="--- a/test.ts\n+++ b/test.ts\n",
        )
        mock_studio_query.return_value = [
            FlakyTestRecord(
                test_identifier="//tools/vendor/google/cli/android-device-hub/electron:test",
                module_name="electron",
                target="studio-linux",
                config_name="//tools/vendor/google/cli/android-device-hub/electron",
                total_runs=100,
                failed_runs=1,
                flake_rate_pct=1.0,
                latest_build_id="16630553",
                latest_invocation_id="ff0bab7a-ff89-4b52-b9f4-54561f5bc86e",
                latest_work_unit_id="",
                l100_failed=1,
                l100_total=100,
                l100_flake_rate_pct=1.0,
                l250_failed=1,
                l250_total=250,
                l250_flake_rate_pct=0.4,
            )
        ]
        mock_find.return_value = None
        mock_create_bug.return_value = "b/DRY_RUN_BUG_ID"

        # Omitting --min-flake-rate and --window defaults to ADH zero-tolerance (>0% over window=250)
        args = self.parser.parse_args(
            [
                "flakiness",
                "triage",
                "--target",
                "studio-linux",
                "--dry-run",
            ]
        )
        flakiness.handle_flakiness_triage(args)
        mock_studio_query.assert_called_once()
        _, kwargs = mock_studio_query.call_args
        self.assertEqual(kwargs["min_flake_rate"], 0.0)
        self.assertEqual(kwargs["min_failed_runs"], 1)
        self.assertEqual(kwargs["window"], 250)
        mock_fetch_bugs.assert_called_once_with("2289192")
        mock_create_bug.assert_called_once()
        created_payload = mock_create_bug.call_args[0][0]
        self.assertEqual(
            created_payload.assignee, "groupagent-android-emulator@google.com"
        )
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertEqual(data["component_id"], "2289192")
        self.assertEqual(data["triaged_count"], 1)
        self.assertEqual(
            data["results"][0]["assignee"],
            "groupagent-android-emulator@google.com",
        )

    def test_flakiness_help_does_not_raise_percent_format_error(self):
        with self.assertRaises(SystemExit) as ctx:
            self.parser.parse_args(["flakiness", "triage", "--help"])
        self.assertEqual(ctx.exception.code, 0)
        with self.assertRaises(SystemExit) as ctx_list:
            self.parser.parse_args(["flakiness", "list", "--help"])
        self.assertEqual(ctx_list.exception.code, 0)

    @patch("lib.agent.verify_agentapi_available")
    @patch("commands.flakiness.triage.generate_ai_patch_for_test")
    @patch("commands.flakiness.triage.query_studio_flaky_tests")
    @patch("commands.flakiness.triage.BuganizerClient.fetch_all_component_open_bugs")
    @patch("commands.flakiness.triage.BuganizerClient.find_existing_open_bug")
    @patch("commands.flakiness.triage.BuganizerClient.find_recently_fixed_bug")
    @patch("commands.flakiness.triage.BuganizerClient.create_bug")
    @patch("commands.flakiness.triage.print_result")
    def test_handle_flakiness_triage_skips_already_fixed_bug_when_l100_zero(
        self,
        mock_print_result,
        mock_create_bug,
        mock_find_fixed,
        mock_find_open,
        mock_fetch_bugs,
        mock_studio_query,
        mock_patch,
        mock_verify,
    ):
        mock_studio_query.return_value = [
            FlakyTestRecord(
                test_identifier="//tools/vendor/google/cli/android-device-hub/electron:package_test",
                module_name="electron",
                target="studio-mac-arm",
                config_name="//tools/vendor/google/cli/android-device-hub/electron",
                total_runs=250,
                failed_runs=68,
                flake_rate_pct=27.2,
                latest_build_id="16632036",
                latest_invocation_id="5c8c6d47-c090-46d2-bb11-cf50687b2085",
                latest_work_unit_id="",
                l100_failed=0,
                l100_total=100,
                l100_flake_rate_pct=0.0,
                l250_failed=68,
                l250_total=250,
                l250_flake_rate_pct=27.2,
            )
        ]
        mock_find_open.return_value = None
        mock_find_fixed.return_value = "570657414"

        args = self.parser.parse_args(
            [
                "flakiness",
                "triage",
                "--target",
                "studio-mac-arm",
                "--auto-file",
            ]
        )
        flakiness.handle_flakiness_triage(args)
        mock_patch.assert_not_called()
        mock_create_bug.assert_not_called()
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertEqual(data["results"][0]["status"], "ALREADY_FIXED_BUG")
        self.assertIn("570657414", data["results"][0]["bug_info"])


if __name__ == "__main__":
    unittest.main()
