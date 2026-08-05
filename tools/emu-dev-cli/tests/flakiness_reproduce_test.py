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

"""Unit tests for 'emu-dev-cli flakiness reproduce'."""

import argparse
import subprocess
import unittest
from unittest.mock import MagicMock, patch
from commands import flakiness


class FlakinessReproduceTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)

    @patch("subprocess.run")
    @patch("commands.flakiness.reproduce.print_result")
    def test_handle_flakiness_reproduce_passed(
        self, mock_print_result, mock_run
    ):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "PASSED in 0.5s"
        mock_run.return_value = mock_proc

        args = self.parser.parse_args(
            [
                "flakiness",
                "reproduce",
                "--test",
                "//emulator/videobridge:in_process_media_provider_test",
                "--target",
                "emulator_linux_x64",
                "--iterations",
                "10",
            ]
        )
        flakiness.handle_flakiness_reproduce(args)
        mock_run.assert_called_once()
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "PASSED (No Flake Observed)")

    @patch("subprocess.run")
    @patch("commands.flakiness.reproduce.print_result")
    def test_handle_flakiness_reproduce_failed(
        self, mock_print_result, mock_run
    ):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "FAILED (TSAN race detected)"
        mock_run.return_value = mock_proc

        args = self.parser.parse_args(
            [
                "flakiness",
                "reproduce",
                "--test",
                "//emulator/videobridge:in_process_media_provider_test",
                "--target",
                "emulator_linux_x64_tsan",
                "--iterations",
                "20",
            ]
        )
        flakiness.handle_flakiness_reproduce(args)
        mock_run.assert_called_once()
        mock_print_result.assert_called_once()
        data = mock_print_result.call_args[0][0]
        self.assertFalse(data["success"])
        self.assertEqual(data["status"], "FAILED (Flake Reproduced)")


if __name__ == "__main__":
    unittest.main()
