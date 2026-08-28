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

"""Unit tests for 'emu-dev-cli flakiness list'."""

import argparse
import unittest
from unittest.mock import MagicMock, patch
from commands import flakiness
from lib.ath_api import FlakyTestRecord


class FlakinessListTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)

    @patch("commands.flakiness.list_cmd.query_ath_flaky_tests")
    @patch("commands.flakiness.list_cmd.print_result")
    def test_handle_flakiness_list(self, mock_print_result, mock_query):
        mock_query.return_value = []
        args = self.parser.parse_args(
            ["flakiness", "list", "--target", "emulator_linux_x64_asan"]
        )
        flakiness.handle_flakiness_list(args)
        mock_print_result.assert_called_once()
        call_args, _ = mock_print_result.call_args
        self.assertEqual(call_args[0]["target"], "emulator_linux_x64_asan")

    @patch("commands.flakiness.list_cmd.query_ath_flaky_tests")
    @patch("commands.flakiness.list_cmd.print_result")
    def test_handle_flakiness_list_live_records(
        self, mock_print_result, mock_query
    ):
        mock_query.return_value = [
            FlakyTestRecord(
                test_identifier="//emulator/videobridge:in_process_media_provider_test",
                module_name="videobridge",
                target="emulator_linux_x64_tsan",
                config_name="devtools/emulator",
                total_runs=100,
                failed_runs=25,
                flake_rate_pct=25.0,
                latest_build_id="15900270",
                latest_invocation_id="I75500010177130681",
                latest_work_unit_id="WU44400250003168346",
            )
        ]
        args = self.parser.parse_args(
            [
                "flakiness",
                "list",
                "--target",
                "emulator_linux_x64_tsan",
                "--min-flake-rate",
                "10.0",
            ]
        )
        flakiness.handle_flakiness_list(args)
        mock_query.assert_called_once()
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertEqual(data["records_count"], 1)
        self.assertEqual(
            data["records"][0]["test_identifier"],
            "//emulator/videobridge:in_process_media_provider_test",
        )


if __name__ == "__main__":
    unittest.main()
