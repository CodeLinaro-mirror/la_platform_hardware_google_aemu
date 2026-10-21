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

    @patch("commands.flakiness.list_cmd.query_studio_flaky_tests")
    @patch("commands.flakiness.list_cmd.print_result")
    def test_handle_flakiness_list_studio_target(
        self, mock_print_result, mock_studio_query
    ):
        mock_studio_query.return_value = [
            FlakyTestRecord(
                test_identifier="//tools/vendor/google/cli/android-device-hub/electron:test",
                module_name="electron",
                target="studio-linux",
                config_name="//tools/vendor/google/cli/android-device-hub/electron",
                total_runs=250,
                failed_runs=1,
                flake_rate_pct=0.4,
                latest_build_id="16630553",
                latest_invocation_id="ff0bab7a-ff89-4b52-b9f4-54561f5bc86e",
                latest_work_unit_id="",
                l100_failed=1,
                l100_total=100,
                l100_flake_rate_pct=1.0,
                l250_failed=1,
                l250_total=250,
                l250_flake_rate_pct=0.4,
                latest_failed_build_id="16630553",
                latest_failed_invocation_id="ff0bab7a-ff89-4b52-b9f4-54561f5bc86e",
            )
        ]
        # Omitting --min-flake-rate and --window defaults to ADH zero-tolerance (>0% over window=250)
        args = self.parser.parse_args(
            [
                "flakiness",
                "list",
                "--target",
                "studio-linux",
            ]
        )
        flakiness.handle_flakiness_list(args)
        mock_studio_query.assert_called_once()
        _, kwargs = mock_studio_query.call_args
        self.assertEqual(kwargs["min_flake_rate"], 0.0)
        self.assertEqual(kwargs["min_failed_runs"], 1)
        self.assertEqual(kwargs["window"], 250)

        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertEqual(data["source"], "f1_test_actions")
        self.assertEqual(data["records_count"], 1)
        self.assertEqual(
            data["records"][0]["test_identifier"],
            "//tools/vendor/google/cli/android-device-hub/electron:test",
        )
        self.assertEqual(data["days"], 20)
        self.assertEqual(data["window"], 250)
        self.assertIn(">0%", data["summary"])

    @patch("commands.flakiness.list_cmd.query_studio_flaky_tests")
    @patch("commands.flakiness.list_cmd.print_result")
    def test_handle_flakiness_list_studio_target_explicit_zero_threshold(
        self, mock_print_result, mock_studio_query
    ):
        mock_studio_query.return_value = []
        args = self.parser.parse_args(
            [
                "flakiness",
                "list",
                "--target",
                "studio-all",
                "--min-flake-rate",
                "0",
            ]
        )
        flakiness.handle_flakiness_list(args)
        mock_studio_query.assert_called_once()
        _, kwargs = mock_studio_query.call_args
        self.assertEqual(kwargs["min_flake_rate"], 0.0)
        self.assertEqual(kwargs["min_failed_runs"], 0)
        self.assertEqual(kwargs["window"], 250)


if __name__ == "__main__":
    unittest.main()
