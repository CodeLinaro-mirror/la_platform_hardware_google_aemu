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

"""Unit tests for 'emu-dev-cli flakiness fetch-logs'."""

import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from commands import flakiness


class FlakinessFetchLogsTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)

    @patch("lib.ath_api.get_android_build_token")
    @patch("commands.flakiness.fetch_logs.print_result")
    def test_handle_flakiness_fetch_logs(self, mock_print_result, mock_token):
        mock_token.return_value = "mock_oauth2_token"
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self.parser.parse_args(
                [
                    "flakiness",
                    "fetch-logs",
                    "--invocation-id",
                    "I75500010177130681",
                    "--artifact-type",
                    "HOST_LOG",
                    "--out-dir",
                    tmpdir,
                ]
            )
            flakiness.handle_flakiness_fetch_logs(args)
            mock_print_result.assert_called_once()
            call_data = mock_print_result.call_args[0][0]
            self.assertEqual(call_data["total_files"], 2)

            stdout_log = Path(tmpdir) / "host_stdout.log"
            stderr_log = Path(tmpdir) / "host_stderr.log"
            self.assertTrue(stdout_log.exists())
            self.assertTrue(stderr_log.exists())
            self.assertTrue(stdout_log.stat().st_size > 0)
            self.assertTrue(stderr_log.stat().st_size > 0)

            # Verify source metadata
            downloaded = call_data["downloaded_files"]
            self.assertIn("source", downloaded[0])
            self.assertIn("is_synthetic", downloaded[0])


if __name__ == "__main__":
    unittest.main()
