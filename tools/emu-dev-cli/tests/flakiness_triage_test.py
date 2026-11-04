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
from unittest.mock import patch
from clients.buganizer_service import BuganizerIssue
from commands import flakiness
from core.context import ExecutionContext
from core.errors import ExecutionError
from lib.ath_api import FlakyTestRecord


class FlakinessTriageTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)
        self.ctx = ExecutionContext.for_testing()

    @patch("commands.flakiness.triage.verify_agent_environment")
    @patch("commands.flakiness.triage.generate_ai_patch_for_test")
    @patch("commands.flakiness.triage.query_ath_flaky_tests")
    def test_handle_flakiness_triage_dry_run(
        self, mock_query, mock_patch, mock_verify
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
        cmd = flakiness.FlakinessTriageCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        data = res.data
        self.assertEqual(data["triaged_count"], 1)
        self.assertEqual(data["results"][0]["status"], "NEW_FLAKY_TEST")

    @patch("commands.flakiness.triage.verify_agent_environment")
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
        cmd = flakiness.FlakinessTriageCommand()
        with self.assertRaises(ExecutionError) as ctx_err:
            cmd.execute(args, self.ctx)
        self.assertIn("Authentication/Authorization failed", str(ctx_err.exception))

    @patch("commands.flakiness.triage.verify_agent_environment")
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
        cmd = flakiness.FlakinessTriageCommand()
        with self.assertRaises(ExecutionError) as ctx_err:
            cmd.execute(args, self.ctx)
        self.assertIn("No execution or flakiness records found in ATH", str(ctx_err.exception))

    @patch("commands.flakiness.triage.verify_agent_environment")
    @patch("commands.flakiness.triage.generate_ai_patch_for_test")
    @patch("commands.flakiness.triage.query_studio_flaky_tests")
    def test_handle_flakiness_triage_studio_defaults_component_2289192(
        self,
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

        args = self.parser.parse_args(
            [
                "flakiness",
                "triage",
                "--target",
                "studio-linux",
                "--dry-run",
            ]
        )
        cmd = flakiness.FlakinessTriageCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        mock_studio_query.assert_called_once()
        _, kwargs = mock_studio_query.call_args
        self.assertEqual(kwargs["min_flake_rate"], 0.0)
        self.assertEqual(kwargs["min_failed_runs"], 1)
        self.assertEqual(kwargs["window"], 250)

        data = res.data
        self.assertEqual(data["component_id"], "2289192")
        self.assertEqual(data["triaged_count"], 1)
        self.assertEqual(
            data["results"][0]["assignee"],
            "groupagent-android-emulator@google.com",
        )

    def test_flakiness_help_does_not_raise_percent_format_error(self):
        with self.assertRaises(SystemExit) as ctx_triage:
            self.parser.parse_args(["flakiness", "triage", "--help"])
        self.assertEqual(ctx_triage.exception.code, 0)
        with self.assertRaises(SystemExit) as ctx_list:
            self.parser.parse_args(["flakiness", "list", "--help"])
        self.assertEqual(ctx_list.exception.code, 0)

    @patch("commands.flakiness.triage.verify_agent_environment")
    @patch("commands.flakiness.triage.generate_ai_patch_for_test")
    @patch("commands.flakiness.triage.query_studio_flaky_tests")
    def test_handle_flakiness_triage_skips_already_fixed_bug_when_l100_zero(
        self,
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
        self.ctx.fake_buganizer_service.seed_issue(
            BuganizerIssue(
                issue_id="570657414",
                title="[Flaky Test] //tools/vendor/google/cli/android-device-hub/electron:package_test",
                component_id=2289192,
                status="FIXED",
            )
        )

        args = self.parser.parse_args(
            [
                "flakiness",
                "triage",
                "--target",
                "studio-mac-arm",
                "--auto-file",
            ]
        )
        cmd = flakiness.FlakinessTriageCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        mock_patch.assert_not_called()
        data = res.data
        self.assertEqual(data["results"][0]["status"], "ALREADY_FIXED_BUG")
        self.assertIn("570657414", data["results"][0]["bug_info"])


if __name__ == "__main__":
    unittest.main()
