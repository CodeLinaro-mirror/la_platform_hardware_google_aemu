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

"""Unit tests for lib.target_resolver."""

import unittest
from unittest.mock import MagicMock

from lib.target_resolver import resolve_dependent_targets, resolve_report_targets


class TargetResolverTest(unittest.TestCase):

    def test_resolve_report_targets_single(self):
        mock_runner = MagicMock()
        self.assertEqual(
            resolve_report_targets(mock_runner, "@goldfish//emulator/libs/async:tidy"),
            ["@goldfish//emulator/libs/async:tidy_report"],
        )
        self.assertEqual(
            resolve_report_targets(mock_runner, "@goldfish//emulator/libs/async:async"),
            ["@goldfish//emulator/libs/async:async_report"],
        )
        self.assertEqual(
            resolve_report_targets(
                mock_runner, "@goldfish//emulator/libs/async:tidy_report"
            ),
            ["@goldfish//emulator/libs/async:tidy_report"],
        )

    def test_resolve_dependent_targets_level1(self):
        mock_runner = MagicMock()
        targets = resolve_dependent_targets(
            mock_runner, "@goldfish//emulator/libs/async:tidy", level=1
        )
        self.assertEqual(targets, ["@goldfish//emulator/libs/async:all"])

    def test_resolve_dependent_targets_level3_rdeps(self):
        mock_runner = MagicMock()
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "@goldfish//emulator/libs/async:async\n@goldfish//emulator/libs/sockets:sockets\n"
        mock_runner.cquery.return_value = mock_res

        targets = resolve_dependent_targets(
            mock_runner, "@goldfish//emulator/libs/async:async", level=3
        )
        self.assertIn("@goldfish//emulator/libs/async:all", targets)
        self.assertIn("@goldfish//emulator/libs/sockets:sockets", targets)


if __name__ == "__main__":
    unittest.main()
