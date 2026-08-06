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


import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Assume these will be created
from lib.planner import RefactorPlanner, Cohort, FileTask
from lib.tidy_types import TidyDiagnostic


class PlannerTest(unittest.TestCase):

    def setUp(self):
        self.workspace_root = Path("/mock/workspace")
        self.planner = RefactorPlanner(self.workspace_root)

    def _make_diag(self, file_path, name):
        d = TidyDiagnostic(
            name=name,
            file_path=file_path,
            line=10,
            column=1,
            message="rename me",
            level=2,
        )
        return d

    @patch("lib.planner.subprocess.run")
    def test_file_atomic_merging(self, mock_run):
        # 5 diagnostics in the exact same file
        diags = [
            self._make_diag("foo.cc", "var_a"),
            self._make_diag("foo.cc", "var_b"),
            self._make_diag("foo.cc", "var_c"),
            self._make_diag("foo.cc", "var_d"),
            self._make_diag("foo.cc", "var_e"),
        ]

        # When checking rdeps, it just returns some targets
        res = MagicMock()
        res.returncode = 0
        res.stdout = "//mock:foo\n"
        mock_run.return_value = res

        cohorts = self.planner.plan(diags)

        # Should produce exactly 1 cohort containing exactly 1 FileTask
        self.assertEqual(len(cohorts), 1)
        self.assertEqual(len(cohorts[0].tasks), 1)
        self.assertEqual(cohorts[0].tasks[0].file_path, "foo.cc")
        self.assertEqual(len(cohorts[0].tasks[0].diagnostics), 5)

    @patch("lib.planner.subprocess.run")
    def test_disjoint_cohorts(self, mock_run):
        # 2 diagnostics in disjoint files
        diags = [self._make_diag("foo.cc", "var_a"), self._make_diag("bar.cc", "var_b")]

        def fake_run(cmd, *args, **kwargs):
            res = MagicMock()
            res.returncode = 0
            if "foo.cc" in cmd[-1]:
                res.stdout = "//mock:foo\n//mock:foo_test\n"
            else:
                res.stdout = "//mock:bar\n//mock:bar_test\n"
            return res

        mock_run.side_effect = fake_run

        cohorts = self.planner.plan(diags)

        # Should produce exactly 1 cohort because they don't overlap,
        # so they can run concurrently (in parallel for that cohort).
        self.assertEqual(len(cohorts), 1)
        self.assertEqual(len(cohorts[0].tasks), 2)

        # Check files are both in the same cohort
        paths = {t.file_path for t in cohorts[0].tasks}
        self.assertEqual(paths, {"foo.cc", "bar.cc"})

    @patch("lib.planner.subprocess.run")
    def test_conflicting_rdeps(self, mock_run):
        # 2 diagnostics in files that share a downstream target
        diags = [self._make_diag("foo.cc", "var_a"), self._make_diag("bar.cc", "var_b")]

        def fake_run(cmd, *args, **kwargs):
            res = MagicMock()
            res.returncode = 0
            if "foo.cc" in cmd[-1]:
                res.stdout = "//mock:foo\n//mock:shared_dep\n"
            else:
                res.stdout = "//mock:bar\n//mock:shared_dep\n"
            return res

        mock_run.side_effect = fake_run

        cohorts = self.planner.plan(diags)

        # Should produce exactly 2 sequential cohorts because they overlap
        self.assertEqual(len(cohorts), 2)

        # Each cohort should have 1 task
        self.assertEqual(len(cohorts[0].tasks), 1)
        self.assertEqual(len(cohorts[1].tasks), 1)


if __name__ == "__main__":
    unittest.main()
