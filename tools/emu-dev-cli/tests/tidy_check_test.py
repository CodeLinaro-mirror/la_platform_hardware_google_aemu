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

"""Unit tests for emu-dev-cli tidy check."""

import argparse
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from commands.tidy.check_cmd import handle_tidy_check, _resolve_report_targets
from lib.tidy_types import TidyDiagnostic


class TidyCheckTest(unittest.TestCase):

    def test_resolve_report_targets(self):
        self.assertEqual(
            _resolve_report_targets(MagicMock(), "@goldfish//emulator/libs/async:tidy"),
            ["@goldfish//emulator/libs/async:tidy_report"],
        )
        self.assertEqual(
            _resolve_report_targets(
                MagicMock(), "@goldfish//emulator/libs/async:async"
            ),
            ["@goldfish//emulator/libs/async:async_report"],
        )
        self.assertEqual(
            _resolve_report_targets(
                MagicMock(), "@goldfish//emulator/libs/async:tidy_report"
            ),
            ["@goldfish//emulator/libs/async:tidy_report"],
        )

    @patch("commands.tidy.check_cmd.BazelRunner")
    @patch("commands.tidy.check_cmd.parse_fixes_yaml")
    @patch("commands.tidy.check_cmd.Path.exists", return_value=True)
    def test_handle_tidy_check_json_with_string_level(
        self, mock_exists, mock_parse, mock_bazel
    ):
        mock_parse.return_value = [
            TidyDiagnostic(
                name="modernize-use-nullptr",
                file_path="foo.cc",
                line=10,
                column=5,
                message="use nullptr",
                level=1,
            )
        ]
        args = argparse.Namespace(
            target="@goldfish//emulator/libs/async:tidy",
            target_flag=None,
            level="safe",
            json=True,
            diff=False,
            file=None,
        )

        captured_stdout = StringIO()
        with patch("sys.stdout", captured_stdout):
            handle_tidy_check(args)

        output = captured_stdout.getvalue()
        self.assertIn("modernize-use-nullptr", output)
        self.assertIn('"total": 1', output)

    @patch("commands.tidy.check_cmd.BazelRunner")
    @patch("commands.tidy.check_cmd.parse_fixes_yaml")
    @patch("commands.tidy.check_cmd.Path.exists", return_value=True)
    def test_handle_tidy_check_default_level_is_level4(
        self, mock_exists, mock_parse, mock_bazel
    ):
        mock_parse.return_value = [
            TidyDiagnostic(
                name="readability-identifier-naming",
                file_path="foo.h",
                line=20,
                column=5,
                message="invalid case style for function 'fooBar'",
                level=4,
            )
        ]
        # Without specifying level (None), it should default to level 4
        args = argparse.Namespace(
            target="@goldfish//emulator/libs/async:tidy",
            target_flag=None,
            level=None,
            json=True,
            diff=False,
            file=None,
        )

        captured_stdout = StringIO()
        with patch("sys.stdout", captured_stdout):
            handle_tidy_check(args)

        output = captured_stdout.getvalue()
        self.assertIn("readability-identifier-naming", output)
        self.assertIn('"total": 1', output)


if __name__ == "__main__":
    unittest.main()
