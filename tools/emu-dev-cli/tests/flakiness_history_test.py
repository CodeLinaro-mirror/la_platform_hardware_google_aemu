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
from unittest.mock import MagicMock, patch
from commands import flakiness
from lib.ath_api import TestHistoryEntry


class FlakinessHistoryTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)

    @patch("commands.flakiness.history.query_test_history")
    @patch("commands.flakiness.history.print_result")
    def test_handle_flakiness_history(self, mock_print_result, mock_history):
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
        flakiness.handle_flakiness_history(args)
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertEqual(data["total_runs"], 10)
        self.assertEqual(data["failed_runs"], 2)
        self.assertEqual(data["flake_rate_pct"], 20.0)


if __name__ == "__main__":
    unittest.main()
