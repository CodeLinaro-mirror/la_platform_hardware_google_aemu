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

"""Unit tests for lib.level1_engine."""

from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lib.level1_engine import Level1RefactoringEngine, _build_level1_fixup_prompt
from lib.tidy_types import TidyDiagnostic


class Level1EngineTest(unittest.TestCase):

    def test_build_level1_fixup_prompt(self):
        prompt = _build_level1_fixup_prompt("my_target", "error: undeclared identifier")
        self.assertIn("my_target", prompt)
        self.assertIn("error: undeclared identifier", prompt)

    @patch("lib.tidy_engine._find_yaml_file")
    @patch("lib.tidy_runner.parse_fixes_yaml")
    def test_run_level1_remediation_clean_target(self, mock_parse, mock_find):
        mock_find.return_value = Path("/mock/fixes.yaml")
        mock_parse.return_value = []
        mock_runner = MagicMock()
        mock_oracle = MagicMock()
        mock_dispatcher = MagicMock()
        mock_tx = MagicMock()
        mock_tx.is_tree_clean.return_value = True

        engine = Level1RefactoringEngine(
            mock_runner, mock_oracle, mock_dispatcher, "/mock/source"
        )
        res = engine.run_level1_remediation(
            target="@goldfish//emulator/libs/async:tidy",
            report_target="@goldfish//emulator/libs/async:tidy_report",
            tx=mock_tx,
        )
        self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
