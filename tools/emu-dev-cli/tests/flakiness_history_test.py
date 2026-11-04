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

"""Unit tests for 'emu-dev-cli flakiness history'."""

import argparse
import unittest
from unittest.mock import patch
from commands import flakiness
from core.context import ExecutionContext
from lib.ath_api import TestHistoryEntry


class FlakinessHistoryTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)
        self.ctx = ExecutionContext.for_testing()

    @patch("commands.flakiness.history.query_test_history")
    def test_handle_flakiness_history(self, mock_history):
        mock_history.return_value = (
            {
                "test_target": "@goldfish//emulator/plugin/grpc/services:screen_recording_impl_test",
                "target": "emulator_linux_x64_tsan",
                "branch": "git_emu-main-next",
                "days": 7,
                "mode": "all",
                "total_runs": 10,
                "passed_runs": 8,
                "failed_runs": 2,
                "flake_rate_pct": 20.0,
            },
            [
                TestHistoryEntry(
                    timestamp="2026-08-05 07:00:00",
                    status="FAILED",
                    run_type="presubmit",
                    build_id="15900270",
                    invocation_id="I75500010177130681",
                    work_unit_id="WU44400250003168346",
                    target="emulator_linux_x64_tsan",
                    test_uri="http://fusion2/invocations/I75500010177130681",
                )
            ],
        )

        args = self.parser.parse_args(
            [
                "flakiness",
                "history",
                "--test",
                "@goldfish//emulator/plugin/grpc/services:screen_recording_impl_test",
                "--target",
                "emulator_linux_x64_tsan",
            ]
        )
        cmd = flakiness.FlakinessHistoryCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        data = res.data
        self.assertEqual(data["total_runs"], 10)
        self.assertEqual(data["failed_runs"], 2)
        self.assertEqual(data["flake_rate_pct"], 20.0)

    @patch("commands.flakiness.common.query_studio_test_history")
    def test_handle_flakiness_history_studio_defaults_days_20(
        self, mock_history
    ):
        mock_history.return_value = (
            {
                "test_target": "//tools/vendor/google/cli/android-device-hub/electron:test",
                "target": "studio-linux",
                "branch": "git_studio-main",
                "days": 20,
                "mode": "all",
                "total_runs": 100,
                "passed_runs": 99,
                "failed_runs": 1,
                "flake_rate_pct": 1.0,
            },
            [],
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "history",
                "--test",
                "//tools/vendor/google/cli/android-device-hub/electron:test",
                "--target",
                "studio-linux",
            ]
        )
        cmd = flakiness.FlakinessHistoryCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        mock_history.assert_called_once()
        self.assertEqual(mock_history.call_args.kwargs["days"], 20)
        data = res.data
        self.assertEqual(data["target"], "studio-linux")
        self.assertIn("20 days", data["markdown_table"])


if __name__ == "__main__":
    unittest.main()
