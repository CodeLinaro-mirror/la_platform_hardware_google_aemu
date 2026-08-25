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

"""Unit tests for lib.tidy_runner."""

from pathlib import Path
import tempfile
import unittest

from lib.tidy_types import TidyDiagnostic, TidyReplacement
from lib.tidy_parser import (
    classify_diagnostic_level,
    normalize_tidy_level,
    parse_fixes_yaml,
)
from lib.tidy_runner import filter_diagnostics_by_level, apply_yaml_fixes


class TidyRunnerTest(unittest.TestCase):

    def test_normalize_tidy_level(self):
        self.assertEqual(normalize_tidy_level(None), 4)
        self.assertEqual(normalize_tidy_level("safe"), 1)
        self.assertEqual(normalize_tidy_level("local"), 2)
        self.assertEqual(normalize_tidy_level("types"), 3)
        self.assertEqual(normalize_tidy_level("public"), 4)
        self.assertEqual(normalize_tidy_level("all"), 4)
        self.assertEqual(normalize_tidy_level(1), 1)
        self.assertEqual(normalize_tidy_level("3"), 3)

    def test_classify_diagnostic_level(self):
        # Level 1: Simple modernization & bugprone
        self.assertEqual(
            classify_diagnostic_level("modernize-use-nullptr", "use nullptr"), 1
        )
        self.assertEqual(
            classify_diagnostic_level("google-readability-casting", "use static_cast"),
            1,
        )

        # Level 2: Local variable & parameter renames
        self.assertEqual(
            classify_diagnostic_level(
                "readability-identifier-naming", "invalid variable name 'x'"
            ),
            2,
        )

        # Level 3: Class / Struct / Enum names
        self.assertEqual(
            classify_diagnostic_level(
                "readability-identifier-naming", "invalid struct name 'my_struct'"
            ),
            3,
        )

        # Level 4: Function / Method / Global constants
        self.assertEqual(
            classify_diagnostic_level(
                "readability-identifier-naming", "invalid function name 'do_something'"
            ),
            4,
        )

    def test_parse_and_filter_yaml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml_text = """MainSourceFile: foo.cc
Diagnostics:
  - DiagnosticName: modernize-use-nullptr
    DiagnosticMessage:
      Message: 'use nullptr'
      FilePath: foo.cc
      FileOffset: 10
      Line: 1
      Column: 10
      Replacements:
        - FilePath: foo.cc
          Offset: 10
          Length: 4
          ReplacementText: 'nullptr'
  - DiagnosticName: readability-identifier-naming
    DiagnosticMessage:
      Message: "invalid function name 'bad_func'"
      FilePath: foo.cc
      FileOffset: 50
      Line: 3
      Column: 6
      Replacements:
        - FilePath: foo.cc
          Offset: 50
          Length: 8
          ReplacementText: 'BadFunc'
"""
            f.write(yaml_text)
            yaml_path = f.name

        try:
            diags = parse_fixes_yaml(yaml_path)
            self.assertEqual(len(diags), 2)
            self.assertEqual(diags[0].level, 1)
            self.assertEqual(diags[1].level, 4)

            # Filter level 1
            l1 = filter_diagnostics_by_level(diags, 1)
            self.assertEqual(len(l1), 1)
            self.assertEqual(l1[0].name, "modernize-use-nullptr")

            # Filter level 4
            l4 = filter_diagnostics_by_level(diags, 4)
            self.assertEqual(len(l4), 2)
        finally:
            Path(yaml_path).unlink(missing_ok=True)

    def test_apply_yaml_fixes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "test.cc"
            src.write_text("int* p = NULL; void bad_func();", encoding="utf-8")

            yaml_file = Path(tmpdir) / "fixes.yaml"
            yaml_text = f"""Diagnostics:
  - DiagnosticName: modernize-use-nullptr
    DiagnosticMessage:
      FilePath: {src}
      Message: 'use nullptr'
      Replacements:
        - FilePath: {src}
          Offset: 9
          Length: 4
          ReplacementText: 'nullptr'
"""
            yaml_file.write_text(yaml_text, encoding="utf-8")

            applied = apply_yaml_fixes(str(yaml_file), max_level=1)
            self.assertEqual(applied, 1)
            self.assertEqual(
                src.read_text(encoding="utf-8"), "int* p = nullptr; void bad_func();"
            )


if __name__ == "__main__":
    unittest.main()
